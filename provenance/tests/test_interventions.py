# -*- coding: utf-8 -*-
"""干预策略注册表测试(2026-09-27 随 InterventionStrategy 落地新增)。

覆盖:
- 注册表契约(register 重复/非法 id 拒绝、known/get/all_ids/describe);
- goals 端点(此前**零测试**):校验路径、落盘路径(governance.json + 审计)、
  权重清洗(NaN/数字开头/Σ≠1)、未知角色拒绝;
- undo 端点(此前**零测试**):回滚、撤销审计、重复撤销拒绝、未找到、沙盒 403;
- 统一分发口 /api/intervention/{id}:未知策略 404 带清单、JSON-only 守卫;
- corrective_feedback(扩展性证明):经统一分发口调用,记忆注入 + 审计,
  且 internalization_metrics 对该记录优雅跳过。

只读原则:所有写落在 tmp 目录(ctx 注入),不碰真实 governance.json / results/。
"""
import datetime
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from live import interventions as ivm
from live import reflections as refl
from live import state

# 模块级导入:live.routes 的 StaticFiles 挂载要求真实 frontend/static,
# 必须在 monkeypatch BASE_DIR 之前完成导入(之后走模块缓存,不再触发检查)。
from live.routes import app as _app  # noqa: E402


# ---------------------------------------------------------------------------
# 测试脚手架
# ---------------------------------------------------------------------------

class _FakeTimer:
    def get_date(self, fmt=""):
        d = datetime.datetime(2025, 2, 13, 9, 30)
        return d.strftime(fmt) if fmt else d


class _FakeGame:
    def __init__(self, agents=None, governance=None):
        self.agents = agents or {}
        self.governance = governance
        self._timer = _FakeTimer()


class _FakeServer:
    def __init__(self, game):
        self.game = game


class _FakeAgent:
    """带记忆注入面的最小 agent(供 corrective_feedback 测试)。"""

    def __init__(self):
        self.injected = []

    def inject_story_event(self, ev):
        self.injected.append(ev)


@pytest.fixture()
def sandbox_env(tmp_path, monkeypatch):
    """隔离环境:tmp BASE_DIR + case00 声明为 experiment-eval(允许 undo)。"""
    os.makedirs(os.path.join(str(tmp_path), "cases", "case00_village"), exist_ok=True)
    (tmp_path / "cases" / "case00_village" / "scenario.yaml").write_text(
        "meta:\n  case_id: case00_village\n  engine: experiment-eval\n",
        encoding="utf-8")
    monkeypatch.setattr(state, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(state, "server", None)
    yield tmp_path


@pytest.fixture()
def client():
    return TestClient(_app)


def _make_fake_server(tmp_path, agent_names=("AI Advisor",), with_agent=False):
    agents = {}
    for n in agent_names:
        agents[n] = _FakeAgent() if with_agent else object()
    gov = _governance(tmp_path)
    return _FakeServer(_FakeGame(agents=agents, governance=gov))


def _governance(tmp_path):
    from mavisframework.runtime.governance import Governance
    return Governance(os.path.join(str(tmp_path), "governance.json"))


# ---------------------------------------------------------------------------
# 注册表契约
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_builtin_registered(self):
        assert ivm.all_ids() == ["corrective_feedback", "goals", "mark", "undo"]
        for sid in ivm.all_ids():
            assert ivm.known(sid)
            assert ivm.get(sid).strategy_id == sid
            d = ivm.describe(sid)
            assert d["strategy"] == sid and d["name"] and d["desc"]

    def test_duplicate_rejected(self):
        with pytest.raises(ValueError, match="已注册"):
            ivm.register("goals", ivm.WeightAdjustStrategy, {})

    def test_bad_id_rejected(self):
        with pytest.raises(ValueError, match="非法"):
            ivm.register("../evil", ivm.WeightAdjustStrategy, {})
        with pytest.raises(ValueError, match="非法"):
            ivm.register("", ivm.WeightAdjustStrategy, {})

    def test_custom_strategy_extends(self):
        """扩展性证明:第三方策略 = 写类 + register,统一分发口即可用。"""

        class _Echo(ivm.InterventionStrategy):
            strategy_id = "test-echo-x7"
            name = "回声(测试)"

            def apply(self, ctx, payload):
                return {"ok": True, "echo": payload}

        ivm.register("test-echo-x7", _Echo, {"name": "回声(测试)"})
        try:
            assert ivm.known("test-echo-x7")
            c = TestClient(_app)
            r = c.post("/api/intervention/test-echo-x7", json={"k": "v"})
            assert r.status_code == 200 and r.json()["echo"] == {"k": "v"}
        finally:
            ivm.STRATEGIES.pop("test-echo-x7", None)
            ivm._INTERVENTIONS.pop("test-echo-x7", None)


# ---------------------------------------------------------------------------
# goals 端点(首批测试)
# ---------------------------------------------------------------------------

class TestGoalsEndpoint:
    def test_missing_name(self, client):
        assert client.post("/api/goals", json={}).json() == {
            "ok": False, "errors": ["缺少角色名"]}

    def test_goals_not_dict(self, client):
        r = client.post("/api/goals", json={"name": "A", "goals": "x"}).json()
        assert r["errors"] == ["约束应为非空 dict(目标:权重)"]

    def test_unknown_role_rejected_when_server_present(self, client, sandbox_env):
        state.server = _FakeServer(_FakeGame(agents={"AI Advisor": _FakeAgent()}))
        try:
            r = client.post("/api/goals",
                            json={"name": "查无此人", "goals": {"A": 1.0}}).json()
            assert "没有这个角色" in r["errors"][0]
        finally:
            state.server = None

    def test_nan_rejected(self, client):
        # NaN 以裸字面量直发(httpx 的 json= 禁 NaN,而服务端 json.loads 会解析它
        # —— 这正是历史上"NaN 骗过 Σ=1 校验"的攻击面)
        r = client.post("/api/goals",
                        content='{"name": "A", "goals": {"A": NaN}}',
                        headers={"Content-Type": "application/json"}).json()
        assert "权重必须是有限数" in r["errors"][0]

    def test_sum_not_one_rejected(self, client):
        r = client.post("/api/goals",
                        json={"name": "A", "goals": {"A": 0.5, "B": 0.2}}).json()
        assert "约束权重总和应为 1" in r["errors"][0]

    def test_valid_goals_persist_and_audit(self, client, sandbox_env):
        r = client.post("/api/goals",
                        json={"name": "Mr. Zhou", "goals": {"A": 1.0},
                              "note": "测试干预"}).json()
        assert r["ok"] is True and r["constraints"] == {"A": 1.0}
        # governance.json 落盘
        gov = json.load(open(os.path.join(str(sandbox_env), "governance.json"),
                             encoding="utf-8"))
        assert gov["roles"]["Mr. Zhou"] == {"A": 1.0}
        # 审计落盘,带模态标记
        audit = json.load(open(os.path.join(
            str(sandbox_env), "results", "checkpoints", "interventions.json"),
            encoding="utf-8"))
        assert audit[-1]["agent"] == "Mr. Zhou"
        assert audit[-1]["operator"] == "expert"
        assert audit[-1]["intervention"] == "goals"
        assert audit[-1]["note"] == "测试干预"

    def test_digit_leading_goal_dropped(self, client):
        # {"1": 1.0} 数字开头 = 误输入 → 清洗后为空 → 拒绝
        r = client.post("/api/goals",
                        json={"name": "A", "goals": {"1": 1.0}}).json()
        assert "清洗后无有效目标" in r["errors"][0]

    def test_live_gov_synced_when_server_present(self, client, sandbox_env):
        """内存同步:agent._governance 指向 game.governance 同一对象,不更新则内化失效。"""
        gov = _governance(sandbox_env)
        state.server = _FakeServer(_FakeGame(agents={"AI Advisor": _FakeAgent()},
                                             governance=gov))
        try:
            r = client.post("/api/goals",
                            json={"name": "AI Advisor", "goals": {"A": 1.0}}).json()
            assert r["ok"] is True
            assert gov.data["roles"]["AI Advisor"] == {"A": 1.0}, "运行中实例未同步"
        finally:
            state.server = None


# ---------------------------------------------------------------------------
# undo 端点(首批测试)
# ---------------------------------------------------------------------------

def _seed_intervention(tmp_path, agent="Mr. Zhou"):
    """造一条已存在的权重干预审计,返回 (sim_time, time)。"""
    path = os.path.join(str(tmp_path), "results", "checkpoints",
                        "interventions.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rec = {"time": "2026-09-27 10:00:00", "sim_time": "20250213-10:00",
           "simulation": "", "agent": agent,
           "old_constraints": {"A": 0.3, "B": 0.7},
           "new_constraints": {"A": 0.6, "B": 0.4},
           "operator": "expert", "note": "", "intervention": "goals"}
    json.dump([rec], open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return rec["sim_time"], rec["time"]


class TestUndoEndpoint:
    def test_missing_fields(self, client, sandbox_env):
        # experiment-eval 场景下才会走到字段校验(sandbox 场景在守卫处就 403)
        r = client.post("/api/undo-intervention", json={}).json()
        assert r["errors"] == ["缺少 agent/sim_time/time"]

    def test_rollback_and_audit(self, client, sandbox_env):
        sim_time, rec_time = _seed_intervention(sandbox_env)
        # 回滚前先落一个"当前约束"(回滚校验要求当前非空)
        client.post("/api/goals",
                    json={"name": "Mr. Zhou", "goals": {"A": 0.6, "B": 0.4}})
        r = client.post("/api/undo-intervention",
                        json={"agent": "Mr. Zhou", "sim_time": sim_time,
                              "time": rec_time}).json()
        assert r["ok"] is True, r
        assert r["rollback_to"] == {"A": 0.3, "B": 0.7}
        gov = json.load(open(os.path.join(str(sandbox_env), "governance.json"),
                             encoding="utf-8"))
        assert abs(sum(gov["roles"]["Mr. Zhou"].values()) - 1.0) < 1e-6
        audit = json.load(open(os.path.join(
            str(sandbox_env), "results", "checkpoints", "interventions.json"),
            encoding="utf-8"))
        assert audit[0].get("revoked") is True
        assert audit[-1]["operator"] == "undo"
        assert audit[-1]["intervention"] == "undo"

    def test_double_undo_rejected(self, client, sandbox_env):
        sim_time, rec_time = _seed_intervention(sandbox_env)
        client.post("/api/goals", json={"name": "Mr. Zhou", "goals": {"A": 1.0}})
        ok = client.post("/api/undo-intervention",
                         json={"agent": "Mr. Zhou", "sim_time": sim_time,
                               "time": rec_time}).json()
        assert ok["ok"] is True
        again = client.post("/api/undo-intervention",
                            json={"agent": "Mr. Zhou", "sim_time": sim_time,
                                  "time": rec_time}).json()
        assert "重复撤销" in again["errors"][0]

    def test_no_match_reported(self, client, sandbox_env):
        _seed_intervention(sandbox_env, agent="someone-else")
        r = client.post("/api/undo-intervention",
                        json={"agent": "X", "sim_time": "t", "time": "t"}).json()
        assert "未找到匹配" in r["errors"][0]

    def test_sandbox_blocked_403(self, client, sandbox_env, monkeypatch):
        # 场景声明为 sandbox-value → undo 一律 403
        monkeypatch.setattr(ivm, "_case00_engine_id",
                            lambda base_dir: "sandbox-value")
        r = client.post("/api/undo-intervention",
                        json={"agent": "X", "sim_time": "t", "time": "t"})
        assert r.status_code == 403
        assert "不允许倒带" in r.json()["errors"][0]


# ---------------------------------------------------------------------------
# 统一分发口 + corrective_feedback(扩展性证明)
# ---------------------------------------------------------------------------

class TestGenericDispatch:
    def test_unknown_strategy_404_with_listing(self, client):
        r = client.post("/api/intervention/nope", json={})
        assert r.status_code == 404
        assert "已注册" in r.json()["errors"][0]

    def test_json_only_guard_applies(self, client):
        r = client.post("/api/intervention/goals", data={"x": "1"},
                        headers={"Content-Type": "text/plain"})
        assert r.status_code == 415

    def test_listing_endpoint(self, client):
        r = client.get("/api/interventions").json()
        assert r["ok"] is True
        assert {s["strategy"] for s in r["strategies"]} == set(ivm.all_ids())

    def test_corrective_feedback_via_generic_endpoint(self, client, sandbox_env):
        """扩展性证明:第 4 策略无专用路由,仅凭注册即可经统一分发口使用。"""
        agent = _FakeAgent()
        state.server = _FakeServer(_FakeGame(agents={"AI Advisor": agent}))
        try:
            r = client.post("/api/intervention/corrective_feedback",
                            json={"agent": "AI Advisor",
                                  "correction": "提示回撤风险时要给具体数字"}).json()
            assert r["ok"] is True and r["injected"] is True
            assert "提示回撤风险" in agent.injected[0]["content"]
            assert agent.injected[0]["event_type"] == "专家纠正"
            # 审计入链(带模态标记)
            audit = json.load(open(os.path.join(
                str(sandbox_env), "results", "checkpoints", "interventions.json"),
                encoding="utf-8"))
            assert audit[-1]["intervention"] == "corrective_feedback"
            assert audit[-1]["correction"] == "提示回撤风险时要给具体数字"
        finally:
            state.server = None

    def test_corrective_feedback_requires_running_sim(self, client):
        r = client.post("/api/intervention/corrective_feedback",
                        json={"agent": "X", "correction": "y"}).json()
        assert "没有运行中的模拟" in r["errors"][0]

    def test_correction_record_skipped_by_internalization_metrics(self,
                                                                  client,
                                                                  sandbox_env):
        """模态标记的兼容性:纠正记录无 old/new constraints,
        internalization_metrics / behavior_follow 必须优雅跳过而非崩溃。"""
        agent = _FakeAgent()
        state.server = _FakeServer(_FakeGame(agents={"AI Advisor": agent}))
        try:
            client.post("/api/intervention/corrective_feedback",
                        json={"agent": "AI Advisor", "correction": "纠正" * 10})
        finally:
            state.server = None
        from case01.tools.internalization_metrics import analyse_simulation
        iv = json.load(open(os.path.join(
            str(sandbox_env), "results", "checkpoints", "interventions.json"),
            encoding="utf-8"))
        result = analyse_simulation(
            os.path.join(str(sandbox_env), "results", "checkpoints", "nonexistent"),
            iv)
        assert result["summary"]["n_interventions_analysed"] == 0
        assert result["summary"]["n_skipped"] >= 1
