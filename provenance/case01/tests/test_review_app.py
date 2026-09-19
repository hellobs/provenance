# -*- coding: utf-8 -*-
"""case01 成品记录审阅面板(case01/review_app.py, 只读服务 5004)的测试。

只验接口形状与安全边界,不验前端渲染:前端是自包含 HTML + 原生 JS,
JS 语法在本地用 `node --check` 对提取出的 <script> 块单独查过。
"""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


def _client():
    from case01.review_app import app
    return TestClient(app)


def test_health():
    r = _client().get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "case01 review"
    assert body["runs"] >= 6


def test_runs_list_covers_both_engines():
    d = _client().get("/api/review/runs").json()
    ids = {x["run_id"] for x in d["runs"]}
    # mavis 三条成品 + 旧引擎三条对照,都要能被面板选到
    assert {"demo-A-mavis", "demo-B-mavis", "demo-C-mavis"} <= ids
    assert {"demo-1", "demo-2", "demo-3"} <= ids
    # 列表摘要要带上各段条数(面板左栏计数用)
    one = next(x for x in d["runs"] if x["run_id"] == "demo-A-mavis")
    for key in ("branch", "n_turns", "n_retrievals", "n_events", "n_issues", "has_reflection"):
        assert key in one, key
    assert one["has_reflection"] is True


def test_run_detail_has_the_six_sections():
    d = _client().get("/api/review/run/demo-A-mavis").json()
    for key in ("turns", "retrievals", "events", "reflection", "router", "injector"):
        assert key in d, "缺少 {}(面板的九块靠它渲染)".format(key)
    assert d["reflection"]["text"], "反思文本不应为空"
    assert d["router"]["issues"], "应有分流问题"


def test_router_issues_risk_is_lowercase_per_contract():
    """issues[].risk 必须是小写(平台契约);raw 里是首字母大写,面板靠 issues 出徽标。"""
    d = _client().get("/api/review/run/demo-A-mavis").json()
    risks = [i["risk"] for i in d["router"]["issues"]]
    assert risks, "应有分流问题"
    assert all(r == r.lower() for r in risks), risks
    assert set(risks) <= {"low", "medium", "high"}, risks


def test_unknown_run_is_404_and_traversal_rejected():
    c = _client()
    assert c.get("/api/review/run/does-not-exist").status_code == 404
    # run_id 走白名单校验:任何穿越尝试都必须 404,不能读到 runs/ 之外的文件
    for bad in ("..%2F..%2Fetc%2Fpasswd", "....//....//etc", "..", "."):
        assert c.get("/api/review/run/{}".format(bad)).status_code == 404, bad


def test_old_engine_run_has_no_injector_section():
    """旧引擎记录没有 injector 段——面板据此显示"无注入器记录",不能因此报错。"""
    d = _client().get("/api/review/run/demo-1").json()
    assert "injector" not in d
    assert d["turns"] and d["router"]["issues"] and d["reflection"]["text"]


def test_index_page_is_self_contained():
    html = _client().get("/").text
    assert "<script>" in html
    assert "/api/review/runs" in html, "页面应自连自己的 JSON API"
    assert "phaser" not in html.lower(), "本面板不依赖 Phaser(照 reflections 面板的做法)"
