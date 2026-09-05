# -*- coding: utf-8 -*-
"""serve.py:只读数据服务 API 测试(TestClient + 临时 runs 目录)。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from case01 import serve


def _write_run(root, run_id, **over):
    d = root / run_id
    d.mkdir()
    rec = {
        "run_id": run_id, "start_date": "2026-08-27", "end_date": "2026-09-15",
        "branch": "A",
        "branch_action": {"timeline": "A", "judge": "forced", "c_plan": None},
        "turns": [
            {"speaker": "ethan", "date": "2026-08-27",
             "text": "HCM 消息可信吗?"},
            {"speaker": "investment_ai", "date": "2026-08-27",
             "text": "我认为值得买入。"},
        ],
        "retrievals": [], "events": [], "state_history": [],
        "final_feedback": {"date": "2026-09-15", "ethan": "亏了。", "ai": "收到。"},
        "reflection": {"text": "反思全文", "material": "m"},
        "router": {"issues": [
            {"id": "issue-1", "summary": "过度采信", "field": "信息甄别",
             "risk": "High", "routing_reason": "源头单一"}]},
        "audit": [{"t": "2026-08-27", "action": "set_branch", "branch": "A"}],
    }
    rec.update(over)
    (d / "run.json").write_text(json.dumps(rec, ensure_ascii=False),
                                encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    _write_run(tmp_path, "run-01")
    _write_run(tmp_path, "run-02", branch="C",
               branch_action={"timeline": "A", "judge": "llm",
                              "c_plan": {"action": "wait"}},
               reflection={}, router={})
    monkeypatch.setattr(serve, "RUNS_ROOT", str(tmp_path))
    return TestClient(serve.app)


class TestListAndDetail:
    def test_index(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["service"].startswith("GTC Case 01")

    def test_list_runs(self, client):
        r = client.get("/api/runs")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 2
        ids = [x["run_id"] for x in body["runs"]]
        assert ids == ["run-02", "run-01"]  # 倒序

    def test_detail_contains_governance_payload(self, client):
        r = client.get("/api/runs/run-01")
        assert r.status_code == 200
        m = r.json()
        assert m["branch_summary"].startswith("建议买入")
        assert m["reflection"]["generated"] is True
        assert m["reflection"]["text"] == "反思全文"
        assert len(m["router"]["issues"]) == 1
        assert m["router"]["issues"][0]["risk"] == "High"
        assert m["audit"][0]["action"] == "set_branch"

    def test_detail_missing_reflection(self, client):
        r = client.get("/api/runs/run-02")
        m = r.json()
        assert m["reflection"]["generated"] is False
        assert m["router"]["issues"] == []
        assert m["branch_summary"].startswith("条件化方案")

    def test_404_unknown(self, client):
        assert client.get("/api/runs/nope").status_code == 404

    def test_404_path_traversal(self, client):
        assert client.get("/api/runs/..%2F..").status_code == 404
        assert client.get("/api/runs/..%2F..%2Fetc").status_code == 404


class TestFullContext:
    def test_full_context_natural_language(self, client):
        r = client.get("/api/runs/run-01/full-context")
        assert r.status_code == 200
        body = r.json()
        assert body["run_id"] == "run-01"
        assert body["format"] == "text/plain; charset=utf-8"
        txt = body["full_context"]
        # 自然语言段落存在,无实验元字段
        assert "一、案例设定" in txt
        assert "Branch" not in txt
        assert '"branch_action"' not in txt

    def test_full_context_404(self, client):
        assert client.get("/api/runs/nope/full-context").status_code == 404


class TestOpenApi:
    def test_openapi_has_three_run_endpoints(self, client):
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        assert "/api/runs" in paths
        assert "/api/runs/{run_id}" in paths
        assert "/api/runs/{run_id}/full-context" in paths
