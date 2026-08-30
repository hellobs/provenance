# -*- coding: utf-8 -*-
"""live_fastapi 后端 API 测试(不依赖真实模拟/LLM/embedding)

覆盖核心端点:
- GET /api/goals      : 约束/倾向/干预(跨模拟隔离)/角色类型/embedding 健康度
- POST /api/goals     : 专家干预 → 写 governance.json + interventions.json 审计
- GET /api/explain    : 倾向成因解释三层(构成分解/窗口明细/干预因果链)
- GET /api/export-chart : 倾向曲线 PNG 导出(缺数据时返回错误)

通过注入假 server.game(内存 Agent)与临时文件隔离,不启动真实模拟。
"""
import json
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# 确保可 import live_fastapi(其内部用相对包导入)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_fastapi as lf


# ---------------------------------------------------------------------------
# 假 Agent(最小,满足 API 读取的字段)
# ---------------------------------------------------------------------------
class _FakeAgent:
    def __init__(self, name, tendency, initial, obs=15, window=None, constraints=None):
        self.name = name
        self._tendency = dict(tendency)
        self.initial_tendency = dict(initial)
        self._tendency_obs = obs
        self.role_type = "user"
        self.status = {
            "value_tendency": dict(tendency),
            "tendency_window_n": len(window or []),
            "tendency_window": [dict(w) for w in (window or [])],
        }
        self._constraints = dict(constraints or {})

    def get_tendency(self):
        return dict(self._tendency)

    def get_constraints(self):
        return dict(self._constraints)


def _fake_game(agents=None):
    """构造假 game:agents 字典 + consequence 健康度 stub"""
    agents = agents or {
        "AI Advisor": _FakeAgent(
            "AI Advisor",
            {"Risk Control": 0.36, "Serve Users": 0.38, "Compliance Rigor": 0.26},
            {"Serve Users": 0.4, "Compliance Rigor": 0.25, "Risk Control": 0.2, "Data Rigor": 0.15},
            obs=23, window=[
                {"action": "run stress test", "alignment": {"Risk Control": 0.6},
                 "feedback": {"Risk Control": 0.14}},
            ],
            constraints={"Serve Users": 0.48, "Compliance Rigor": 0.18, "Risk Control": 0.34},
        ),
        "Daniel Shen": _FakeAgent(
            "Daniel Shen",
            {"Steady Returns": 0.39, "Risk Control": 0.25},
            {"Steady Returns": 0.35, "Risk Control": 0.25},
            obs=3,
        ),
    }
    return SimpleNamespace(agents=agents, consequence=SimpleNamespace(
        health=lambda: {"total_calls": 10, "degraded_calls": 0,
                        "degrade_rate": 0.0, "last_error": ""}
    ), _timer=SimpleNamespace(get_date=lambda *a: "20250213-10:00"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """隔离的 TestClient:假 server + 临时文件目录"""
    # 隔离文件:governance.json / results/checkpoints(与 BASE_DIR 相对路径一致)
    tmp_ckpt = tmp_path / "results" / "checkpoints"
    tmp_ckpt.mkdir(parents=True)
    gov_path = tmp_path / "governance.json"
    gov_path.write_text(json.dumps({
        "roles": {
            "AI Advisor": {"Serve Users": 0.48, "Compliance Rigor": 0.18, "Risk Control": 0.34},
            "Daniel Shen": {"Steady Returns": 0.35, "Risk Control": 0.25},
        }
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(lf, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(lf, "server", SimpleNamespace(game=_fake_game()))
    monkeypatch.setattr(lf, "sim_state", {"name": "test-sim", "status": "running",
                                          "start_time": "20250213-09:30", "stride": 2})
    monkeypatch.setattr(lf, "compressor", SimpleNamespace(
        checkpoints_folder=str(tmp_ckpt), started=False))
    return TestClient(lf.app)


# ---------------------------------------------------------------------------
# GET /api/goals
# ---------------------------------------------------------------------------
class TestGetGoals:
    def test_returns_goals_and_tendency(self, client):
        r = client.get("/api/goals")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "AI Advisor" in body["goals"]
        assert "AI Advisor" in body["tendency"]
        assert body["simulation"] == "test-sim"

    def test_interventions_scoped_to_current_sim(self, client, tmp_path):
        # 写一条属于本模拟的干预 + 一条其他模拟的干预
        iv_path = tmp_path / "results" / "checkpoints" / "interventions.json"
        iv_path.write_text(json.dumps([
            {"time": "2026-08-30 10:00:00", "sim_time": "20250213-10:00",
             "simulation": "test-sim", "agent": "AI Advisor",
             "old_constraints": {"Risk Control": 0.2},
             "new_constraints": {"Risk Control": 0.5}},
            {"time": "2026-08-29 10:00:00", "sim_time": "20250213-09:00",
             "simulation": "other-sim", "agent": "AI Advisor",
             "old_constraints": {}, "new_constraints": {}},
        ], ensure_ascii=False), encoding="utf-8")
        r = client.get("/api/goals")
        ivs = r.json()["interventions"]
        assert len(ivs) == 1
        assert ivs[0]["simulation"] == "test-sim"

    def test_embedding_health_present(self, client):
        r = client.get("/api/goals")
        h = r.json()["embedding_health"]
        assert h["degrade_rate"] == 0.0


# ---------------------------------------------------------------------------
# POST /api/goals(专家干预)
# ---------------------------------------------------------------------------
class TestPostGoals:
    def test_update_constraints_writes_audit(self, client, tmp_path):
        r = client.post("/api/goals", json={
            "name": "AI Advisor",
            "goals": {"Serve Users": 0.3, "Risk Control": 0.5, "Compliance Rigor": 0.2},
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # governance.json 已更新
        gov = json.loads((tmp_path / "governance.json").read_text(encoding="utf-8"))
        assert gov["roles"]["AI Advisor"]["Risk Control"] == 0.5
        # interventions.json 有审计且带 simulation
        iv = json.loads((tmp_path / "results" / "checkpoints" / "interventions.json").read_text(encoding="utf-8"))
        assert iv[-1]["simulation"] == "test-sim"
        assert iv[-1]["new_constraints"]["Risk Control"] == 0.5

    def test_rejects_unnormalized_weights(self, client):
        r = client.post("/api/goals", json={
            "name": "AI Advisor",
            "goals": {"Serve Users": 0.3, "Risk Control": 0.3},  # sum=0.6
        })
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert any("总和" in e for e in r.json().get("errors", []))

    def test_rejects_zero_and_numeric_goals(self, client):
        r = client.post("/api/goals", json={
            "name": "AI Advisor",
            "goals": {"Serve Users": 0.5, "1": 0.2, "Zero": 0.0},
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False  # 清洗后只剩 0.5,sum≠1


# ---------------------------------------------------------------------------
# GET /api/explain
# ---------------------------------------------------------------------------
class TestExplain:
    def test_returns_three_layers(self, client):
        r = client.get("/api/explain", params={"agent": "AI Advisor"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "decomposition" in body      # ① 构成分解
        assert "window_details" in body     # ② 窗口明细
        assert "intervention_chain" in body  # ③ 干预因果链
        assert "Risk Control" in body["decomposition"]
        assert body["decomposition"]["Risk Control"]["alpha"] == pytest.approx(0.1)  # obs=23 → α=0.1

    def test_missing_agent_returns_error(self, client):
        r = client.get("/api/explain", params={"agent": "Nobody"})
        assert r.status_code == 200
        assert r.json()["ok"] is False


# ---------------------------------------------------------------------------
# GET /api/export-chart
# ---------------------------------------------------------------------------
class TestExportChart:
    def test_no_data_returns_error(self, client):
        r = client.get("/api/export-chart", params={"agent": "AI Advisor"})
        # 无 checkpoint 数据 → 错误响应(而非崩溃)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False or "errors" in body
