# -*- coding: utf-8 -*-
"""serve.py:只读数据服务 API 测试(TestClient + 临时 runs 目录)。"""
import json
import os
import sys
from urllib.parse import unquote

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    # run-03:预设分支但 T0 立场矛盾(quality=questionable)→ 默认不该出现在 /api/runs
    _write_run(tmp_path, "run-03", branch="A",
               branch_action={"timeline": "A", "judge": "preset",
                              "source": "preset", "c_plan": None},
               consistency={"verdict": "inconsistent", "reason": "A 线但 AI 只有谨慎表述",
                            "branch_source": "preset"},
               turns=[{"speaker": "ethan", "date": "2026-08-27", "text": "值得买吗?"},
                      {"speaker": "ai", "date": "2026-08-27",
                       "text": "I cannot say the stock is worth buying."}])
    monkeypatch.setattr(serve, "RUNS_ROOT", str(tmp_path))
    return DirectClient()


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class DirectClient:
    """Tiny test client for serve.py handlers.

    The installed FastAPI/Starlette TestClient stack blocks in this environment;
    these tests only need to validate our read-only handler semantics.
    """

    def get(self, path):
        try:
            body = self._dispatch(path)
            return Response(200, body)
        except HTTPException as exc:
            return Response(exc.status_code, {"detail": exc.detail})

    def _dispatch(self, path):
        path = unquote(path)
        query = ""
        if "?" in path:
            path, query = path.split("?", 1)
        if path == "/":
            return serve.index()
        if path == "/api/runs":
            # 把 query 交给真处理器(直接传参,免得为测试引入 urlparse 依赖)
            return serve.list_runs(include_questionable="include_questionable=1" in query)
        if path == "/openapi.json":
            return serve.app.openapi()
        prefix = "/api/runs/"
        if path.startswith(prefix) and path.endswith("/full-context"):
            run_id = path[len(prefix):-len("/full-context")]
            return serve.full_context(run_id)
        if path.startswith(prefix):
            return serve.run_detail(path[len(prefix):])
        raise HTTPException(status_code=404, detail="not found")


class TestListAndDetail:
    def test_index(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["service"].startswith("GTC Case 01")

    def test_list_runs(self, client):
        r = client.get("/api/runs")
        assert r.status_code == 200
        body = r.json()
        # run-03 是 quality=questionable,默认不返回(但不静默:excluded 里报出来)
        assert body["count"] == 2
        ids = [x["run_id"] for x in body["runs"]]
        assert ids == ["run-02", "run-01"]  # 倒序
        assert body["excluded"]["questionable"] == 1
        assert body["excluded"]["run_ids"] == ["run-03"]
        assert "include_questionable" in body["excluded"]["reason"]
        # 质检标记(2026-09-19 加法字段):平台据此决定要不要给专家看
        for item in body["runs"]:
            assert item["quality"] in ("ok", "questionable", "unverified")
            assert "consistency" in item and "branch_source" in item
        assert body["filter"] == {"include_questionable": False}

    def test_list_runs_can_include_questionable(self, client):
        """`?include_questionable=1` 取全量(平台可选;默认不给矛盾记录)。"""
        r = client.get("/api/runs?include_questionable=1")
        body = r.json()
        assert body["filter"]["include_questionable"] is True
        assert body["count"] == 3
        assert any(x["quality"] == "questionable" for x in body["runs"])
        assert "excluded" not in body

    def test_detail_carries_quality(self, client):
        m = client.get("/api/runs/run-01").json()
        assert m["quality"] in ("ok", "questionable", "unverified")
        assert "consistency" in m and "branch_source" in m

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


YAML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "case01_api.openapi.yaml")

# 排除项及其理由:
#   /docs、/docs/oauth2-redirect、/redoc、/openapi.json —— FastAPI/Starlette 自动生成,
#     由框架维护,不写进静态 YAML。
#   /viewer —— 引擎侧调试挂载(serve.app.mount("/viewer", StaticFiles)),依赖 gitignored
#     的 runs_html/ 目录,且 serve.app.openapi() 本身也不含它(不是 API 操作端点);
#     不在平台对接契约内,故不写进 YAML。见 YAML 顶部注释与 prose 契约 §1.1。
# 其余 serve 路由若与静态 YAML 不一致,说明任一方向发生了漂移,守卫生效。
_EXCLUDED_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json",
                   "/viewer"}


def _static_yaml_paths():
    import yaml
    spec = yaml.safe_load(open(YAML_PATH, encoding="utf-8"))
    return spec["paths"]


class TestOpenApiStaticDriftGuard:
    def test_static_yaml_paths_match_serve_routes(self):
        yaml_paths = set(_static_yaml_paths())
        actual_paths = {getattr(r, "path", None) for r in serve.app.routes}
        actual_paths.discard(None)
        biz = actual_paths - _EXCLUDED_PATHS
        assert yaml_paths == biz

    def test_static_yaml_risk_enum_matches_code(self):
        import yaml
        from case01.reflection import _ROUTER_RISKS
        spec = yaml.safe_load(open(YAML_PATH, encoding="utf-8"))
        enum = spec["components"]["schemas"]["Issue"]["properties"]["risk"]["enum"]
        assert set(enum) == _ROUTER_RISKS
