# -*- coding: utf-8 -*-
"""case01 成品记录审阅面板(case01/review_app.py, 只读服务 5004)的测试。

**数据源必须是入库的 fixtures,不能是 case01/runs/**:`case01/runs/` 在
`case01/.gitignore` 里(第 2 行 `runs/`),CI 的全新 checkout 里一条记录都没有——
2026-09-19 首版测试就是踩了这个坑:本地 215 passed、CI 5 failed。
所以这里把面板的记录根指到 `tests/fixtures/records`(与 case01/viz.py 的
`load_run(run_id, runs_dir=...)` 同一做法)。

只验接口形状与安全边界,不验前端渲染:前端是自包含 HTML + 原生 JS,
JS 语法另用 `node --check` 对提取出的 <script> 块查过。
"""
import os

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "records")
MAVIS_RUN = "260917-demo-case01-mavis-C"   # 含六段(含 injector)
OLD_RUN = "260905-demo-case01-old-C"       # 旧引擎:无 injector 段


@pytest.fixture(autouse=True)
def _use_fixtures(monkeypatch):
    from case01 import review_app
    monkeypatch.setattr(review_app, "RUNS_DIR", FIXTURES)


def _client():
    from case01.review_app import app
    return TestClient(app)


def test_health_counts_runs_from_configured_root():
    body = _client().get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "case01 review"
    # fixtures 里至少要有 mavis 与旧引擎各一条,面板才谈得上"两个引擎都能看"
    assert body["runs"] >= 2


def test_runs_list_covers_both_engines():
    d = _client().get("/api/review/runs").json()
    ids = {x["run_id"] for x in d["runs"]}
    assert {MAVIS_RUN, OLD_RUN} <= ids
    one = next(x for x in d["runs"] if x["run_id"] == MAVIS_RUN)
    for key in ("branch", "n_turns", "n_retrievals", "n_events", "n_issues", "has_reflection"):
        assert key in one, key
    assert one["has_reflection"] is True


def test_run_detail_has_the_six_sections():
    d = _client().get("/api/review/run/{}".format(MAVIS_RUN)).json()
    for key in ("turns", "retrievals", "events", "reflection", "router", "injector"):
        assert key in d, "缺少 {}(面板的九块靠它渲染)".format(key)
    assert d["reflection"]["text"], "反思文本不应为空"
    assert d["router"]["issues"], "应有分流问题"
    assert d["injector"]["nodes"], "mavis 记录应有注入器节点"


def test_router_issues_risk_is_lowercase_per_contract():
    """issues[].risk 必须是小写(平台契约);raw 里是首字母大写,面板靠 issues 出徽标。"""
    d = _client().get("/api/review/run/{}".format(MAVIS_RUN)).json()
    risks = [i["risk"] for i in d["router"]["issues"]]
    assert risks, "应有分流问题"
    assert all(r == r.lower() for r in risks), risks
    assert set(risks) <= {"low", "medium", "high"}, risks


def test_unknown_run_is_404_and_traversal_rejected():
    c = _client()
    assert c.get("/api/review/run/does-not-exist").status_code == 404
    # run_id 走白名单校验:任何穿越尝试都必须 404,不能读到记录根之外的文件
    for bad in ("..%2F..%2Fetc%2Fpasswd", "....//....//etc", "..", "."):
        assert c.get("/api/review/run/{}".format(bad)).status_code == 404, bad


def test_old_engine_run_has_no_injector_section():
    """旧引擎记录没有 injector 段——面板据此显示"无注入器记录",不能因此报错。"""
    d = _client().get("/api/review/run/{}".format(OLD_RUN)).json()
    assert "injector" not in d
    assert d["turns"] and d["router"]["issues"] and d["reflection"]["text"]


def test_runs_list_marks_engine():
    """列表摘要要标出引擎归属:mavis(成品)/ legacy(旧引擎对照)。

    面板靠它把默认值落在成品三线上,并给旧记录标"无 injector 段";
    只凭 run_id 猜(比如看名字里有没有 -mavis)不够稳。
    """
    by = {x["run_id"]: x for x in _client().get("/api/review/runs").json()["runs"]}
    assert by[MAVIS_RUN]["engine"] == "mavis"
    assert by[OLD_RUN]["engine"] == "legacy"


def test_both_retrieval_shapes_are_reachable():
    """两种检索形态都要能在 API 里取到(面板据此分别渲染)。

    mavis(成品): date / mode / query / injected[]
    旧引擎对照:  current_date / query / hits[] / source_stats
    2026-09-19 首版面板只认前一套,旧记录的命中明细(hits/source_stats)被整个丢掉。
    """
    mavis = _client().get("/api/review/run/{}".format(MAVIS_RUN)).json()["retrievals"][0]
    assert "injected" in mavis and "date" in mavis and "mode" in mavis
    assert "hits" not in mavis

    legacy = _client().get("/api/review/run/{}".format(OLD_RUN)).json()["retrievals"][0]
    assert "hits" in legacy and legacy["hits"], "旧记录必须有 hits 明细"
    assert "current_date" in legacy and "source_stats" in legacy
    assert "injected" not in legacy
    # 命中元素要有可渲染的字段(面板直接读这几个)
    for key in ("time", "source", "type", "title", "score"):
        assert key in legacy["hits"][0], key


def test_legacy_run_lacks_injector_and_summary():
    """旧引擎记录没有 injector/summary/compat——面板据此分段渲染,不能渲染成 0/空。"""
    d = _client().get("/api/review/run/{}".format(OLD_RUN)).json()
    for absent in ("injector", "summary", "compat"):
        assert absent not in d, absent


def test_brief_carries_derived_branch_summary():
    """列表摘要必须带 branch_summary——它**不在 run.json 里**,是服务端推导的一句话。

    2026-09-19 面板首版直接读 data.get("branch_summary") 拿到 None,下拉标签里就成了
    "A 线 · — ｜ <旧 id>";用户反馈"我看不懂"。现在改用 5002 契约同源的
    full_context._branch_summary,两边口径一致。
    """
    from case01 import full_context as fc

    by = {x["run_id"]: x for x in _client().get("/api/review/runs").json()["runs"]}
    for rid in (MAVIS_RUN, OLD_RUN):
        assert by[rid]["branch_summary"], "{} 的 branch_summary 不能为空".format(rid)
    # 与契约同源(同一条分支应得到同一句话)
    assert by[MAVIS_RUN]["branch_summary"] == fc._branch_summary(by[MAVIS_RUN]["branch"], {})
    # 记录本身没有这个字段 —— 证明它是推导的,不是抄的
    raw = _client().get("/api/review/run/{}".format(MAVIS_RUN)).json()
    assert "branch_summary" not in raw


def test_embed_surface_is_served_and_iframe_allowed():
    """嵌入面:仝牧平台用 iframe 引它。同一页,靠前端识别 /embed/ 路径切压缩版式。"""
    c = _client()
    for path in ("/embed/review", "/?embed=1", "/?run={}&tab=router".format(MAVIS_RUN)):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "/api/review/runs" in r.text, path
    # 默认不该带 X-Frame-Options / CSP——带上了 iframe 就白做
    headers = {k.lower() for k in c.get("/embed/review").headers}
    assert "x-frame-options" not in headers
    assert "content-security-policy" not in headers


def test_combined_page_hosts_both_windows():
    """两窗一页:实时小镇 iframe + 结果 iframe,整页本身也可再被 iframe。"""
    c = _client()
    body = c.get("/combined").text
    assert "/embed/review" in body, "结果窗应 iframe 本服务的嵌入面"
    assert "http://127.0.0.1:5010/embed/scene" in body, "默认小镇源 = case01 实时面"
    # ?live= 可换成别的小镇(case00 的 5001 也是 Phaser 小镇)
    r2 = c.get("/combined", params={"live": "http://127.0.0.1:5001/embed/scene"})
    assert "http://127.0.0.1:5001/embed/scene" in r2.text
    # 非 http(s) 一律回落默认:不把任意串塞进 iframe
    r3 = c.get("/combined", params={"live": "javascript:alert(1)"})
    assert "javascript:" not in r3.text
    assert "http://127.0.0.1:5010/embed/scene" in r3.text


def test_index_page_is_self_contained():
    html = _client().get("/").text
    assert "<script>" in html
    assert "/api/review/runs" in html, "页面应自连自己的 JSON API"
    assert "phaser" not in html.lower(), "本面板不依赖 Phaser(照 reflections 面板的做法)"


def test_empty_root_reports_zero_runs_instead_of_crashing(monkeypatch, tmp_path):
    """记录根不存在时,列表要返回 0 条而不是抛异常(CI 里 runs/ 缺席就是这种情形)。"""
    from case01 import review_app
    monkeypatch.setattr(review_app, "RUNS_DIR", str(tmp_path / "nope"))
    c = _client()
    assert c.get("/health").json()["runs"] == 0
    assert c.get("/api/review/runs").json()["count"] == 0
    assert c.get("/").status_code == 200
    assert c.get("/api/review/run/{}".format(MAVIS_RUN)).status_code == 404
