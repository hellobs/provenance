# -*- coding: utf-8 -*-
"""case01 成品记录审阅面板(case01/review_app.py)的测试。

面板现在是**挂在 5010 实时面上的挂件**(2026-09-19 起独立 5004 服务退役,
"只维护一个界面"),所以这里既验独立跑法(排障用),也验"挂上去之后路径不变"。

**数据源必须是入库的 fixtures,不能是 case01/runs/**:`case01/runs/` 在
`case01/.gitignore` 里(第 2 行 `runs/`),CI 的全新 checkout 里一条记录都没有——
2026-09-19 首版测试就是踩了这个坑:本地 215 passed、CI 5 failed。
所以这里把面板的记录根指到 `tests/fixtures/records`(与 case01/viz.py 的
`load_run(run_id, runs_dir=...)` 同一做法)。

只验接口形状与安全边界,不验前端渲染:前端是自包含 HTML + 原生 JS,
JS 语法另用 `node --check` 对提取出的 <script> 块查过;运行期渲染用
`node case01/tools/review_panel_probe.js`(打真实服务)查。
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
    monkeypatch.setattr(review_app, "_CURRENT", {"run_id": "", "note": ""})


def _client():
    from case01.review_app import app
    return TestClient(app)


def test_health_counts_runs_from_configured_root():
    body = _client().get("/api/review/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "case01 review"
    # fixtures 里至少要有 mavis 与旧引擎各一条,面板才谈得上"两个引擎都能看"
    assert body["runs"] >= 2


def test_standalone_app_still_serves_health_at_root():
    """独立跑法(排障用)保留 /health;挂到实时面上时用 /api/review/health,避开它的 /health。"""
    assert _client().get("/health").json()["service"] == "case01 review"


def test_runs_list_covers_both_engines():
    d = _client().get("/api/review/runs").json()
    ids = {x["run_id"] for x in d["runs"]}
    assert {MAVIS_RUN, OLD_RUN} <= ids
    one = next(x for x in d["runs"] if x["run_id"] == MAVIS_RUN)
    for key in ("branch", "n_turns", "n_retrievals", "n_events", "n_issues", "has_reflection"):
        assert key in one, key
    assert one["has_reflection"] is True


def test_runs_list_reports_current_live_run():
    """正在实跑、但还没有成品记录时,列表要带出 run_id,面板才好写出"还没记录"。

    否则人对着旧记录看,会以为这次实跑的结果丢了(**不允许静默**)。
    """
    from case01 import review_app
    review_app.set_current_run("260919-live-case01-mavis-B-9999", "跑完自动映射")
    d = _client().get("/api/review/runs").json()
    assert d["current_run_id"] == "260919-live-case01-mavis-B-9999"
    assert d["current_note"] == "跑完自动映射"
    assert "260919-live-case01-mavis-B-9999" not in {x["run_id"] for x in d["runs"]}


# ---------------------------------------------------------------------------
# 实时同步:小镇在动,结果面板每 2 秒跟着长(用户要"同步看全程")
# ---------------------------------------------------------------------------
def _fake_raw(nodes_done=1, total=3):
    """造一份"跑到第 N 个节点"的 injector 原始记录(形状照真实 raw.json 抄)。"""
    nodes = []
    for i in range(1, nodes_done + 1):
        date = "2026-08-2{}".format(6 + i)
        nodes.append({
            "node_id": "node-{}".format(i), "date": date, "step": i,
            "released_events": ["ev-{}".format(i)],
            "events": [{"id": "ev-{}".format(i), "date": date,
                        "time": "09:30", "event_type": "disclosure", "importance": 3,
                        "targets": ["Ethan Lin"], "content": "事件 {}".format(i)}],
            "context": {},
            "interactions": [{"from": "Ethan Lin", "to": "Investment AI",
                              "focus": "第 {} 轮问题".format(i)}],
            "interaction_started": True, "retries": 0,
            "world": {"price_usd": 45.8},
            "world_state": {"date": date, "branch": "B", "cash_rmb": 200000.0,
                            "hcm_shares": False, "held_fraction": 0.0,
                            "entry_price_usd": None, "exit_price_usd": None, "exited": False},
            # dialogue 的真实形状:list[dict],内层是 [说话人, 文本] 对(见 record._turns)
            "dialogue": [{"Ethan Lin -> Investment AI": [
                ["Ethan Lin", "第 {} 轮提问".format(i)],
                ["Investment AI", "第 {} 轮回答".format(i)]]}],
            "agents": {}, "elapsed_s": 12.5,
        })
    return {"schema_version": "injector-0.1", "run_id": "260919-live-case01-mavis-B-8888",
            "mode": "mavis", "branch": "B", "roles": ["Investment AI", "Ethan Lin"],
            "scenario_dir": "", "nodes": nodes, "world_audit": [],
            "condition_monitor": [], "c_plan": {},
            "summary": {"node_count": nodes_done, "interaction_started": nodes_done,
                        "retries": 0, "elapsed_s": 12.5 * nodes_done}}


def test_live_endpoint_reports_not_live_when_idle():
    from case01 import review_app
    review_app.clear_live()
    d = _client().get("/api/review/live").json()
    assert d["ok"] is True and d["live"] is False


def test_live_endpoint_maps_partial_run_to_the_same_shape():
    """实时记录必须与成品记录**同一套映射**,否则九块渲染会缺字段/报错。"""
    from case01 import review_app
    review_app.set_live_provider(lambda: _fake_raw(nodes_done=2), run_id="260919-live-case01-mavis-B-8888",
                                total_nodes=7)
    try:
        d = _client().get("/api/review/live").json()
        assert d["ok"] and d["live"] is True
        assert d["done_nodes"] == 2 and d["total_nodes"] == 7
        rec = d["record"]
        assert rec["live"] is True and rec["run_id"] == "260919-live-case01-mavis-B-8888"
        for key in ("turns", "retrievals", "events", "state_history", "audit", "injector",
                    "reflection", "router"):
            assert key in rec, "缺少 {}:面板九块靠它".format(key)
        assert len(rec["injector"]["nodes"]) == 2, "跑到哪就长到哪"
        assert rec["events"] and rec["turns"], "已有节点的对话/事件要能看到"
        assert rec["reflection"] == {} and rec["router"] == {}, "反思/分流跑完才有"
    finally:
        review_app.clear_live()


def test_live_endpoint_progress_grows_then_stops():
    """节点走一个、实时记录长一个;clear 之后立刻回到 live=false。"""
    from case01 import review_app
    state = {"n": 0}
    review_app.set_live_provider(lambda: _fake_raw(state["n"]), run_id="r1", total_nodes=3)
    c = _client()
    assert c.get("/api/review/live").json()["done_nodes"] == 0
    state["n"] = 2
    assert c.get("/api/review/live").json()["done_nodes"] == 2
    review_app.clear_live()
    assert c.get("/api/review/live").json()["live"] is False


def test_live_provider_failure_is_reported_not_swallowed():
    """provider 抛异常时要给出原因(面板要显示),不能静默变成空记录。"""
    from case01 import review_app

    def boom():
        raise RuntimeError("bridge 挂了")

    review_app.set_live_provider(boom, run_id="r2", total_nodes=3)
    try:
        d = _client().get("/api/review/live").json()
        assert d["ok"] is False and d["live"] is True
        assert "bridge 挂了" in d["errors"][0]
    finally:
        review_app.clear_live()


def test_live_endpoint_hands_over_once_the_record_is_mapped(monkeypatch, tmp_path):
    """成品记录一落盘,实时就该让位(live=false),否则面板永远缺反思/问题分流两块。"""
    from case01 import review_app
    monkeypatch.setattr(review_app, "RUNS_DIR", str(tmp_path))
    run_id = "260919-live-case01-mavis-B-7777"
    review_app.set_live_provider(lambda: _fake_raw(1), run_id=run_id, total_nodes=3)
    try:
        c = _client()
        assert c.get("/api/review/live").json()["live"] is True
        # 自动映射把成品记录写出来
        (tmp_path / run_id).mkdir()
        (tmp_path / run_id / "run.json").write_text("{}", encoding="utf-8")
        d = c.get("/api/review/live").json()
        assert d["live"] is False and d.get("mapped") is True
    finally:
        review_app.clear_live()


def test_dropdown_has_no_group_titles():
    """下拉里不要分组标题(用户原话:"奇怪的无用的话")。

    曾经有 `正在跑的这一次（实时）` 与 `mavis 新架构（成品三线）` 两个 optgroup。
    现在就是一条条记录;引擎归属靠 run_id 自带(`…-mavis-B-…` / `…-old-B`)。
    """
    html = _client().get("/review").text
    assert "optgroup" not in html
    assert "正在跑的这一次" not in html
    assert "mavis 新架构（成品三线）" not in html
    assert "● 实时 ｜" in html, "实时那条仍要在(运行时由 /api/review/live 决定显不显示)"


def test_router_pane_shows_risk_note_and_question_flag():
    """问题分流那一块:风险单独一段,"仍是疑问句"要显式标出来(不许静默)。"""
    html = _client().get("/review").text
    assert "风险 / 错在哪" in html
    assert "仍是疑问句" in html


def test_no_filler_phrases_in_the_panel():
    """用户把"（那时才有反思与问题分流）""反思/问题分流跑完才有"点成废话,已删。"""
    html = _client().get("/review").text
    assert "反思/问题分流跑完才有" not in html
    assert "那时才有反思与问题分流" not in html
    assert "跑完后才有" in html, "空状态仍需一句极短说明(不许静默留白)"


def test_end_of_run_notifications_are_wired():
    """用户明确要求"推演结束后要有提示":小镇页有结束横幅,面板有"已生成"绿条。"""
    page = _client().get("/review").text
    assert "成品记录已生成(含反思与问题分流),已切到这一条" in page
    assert "note ok" in page
    # 小镇页(index.html + main_script.html)那一侧由 test_vizkit_live 里查:
    # showDoneBanner / #done-banner


def test_live_mode_is_wired_into_the_page():
    """页面里要有实时那条下拉项与轮询(用户要"小镇与结果同步看全程")。"""
    html = _client().get("/review").text
    for piece in ("/api/review/live", "loadLive", "__live__", "● 实时"):
        assert piece in html, piece


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
    for path in ("/review", "/embed/review", "/review?embed=1",
                 "/review?run={}&tab=router".format(MAVIS_RUN)):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "/api/review/runs" in r.text, path
    # 默认不该带 X-Frame-Options / CSP——带上了 iframe 就白做
    headers = {k.lower() for k in c.get("/embed/review").headers}
    assert "x-frame-options" not in headers
    assert "content-security-policy" not in headers


def test_combined_page_is_retired():
    """/combined(两窗一页)已随"只维护一个界面"退役:那个界面现在是 5010 首页本身。"""
    assert _client().get("/combined").status_code == 404


def test_index_page_is_self_contained():
    html = _client().get("/review").text
    assert "<script>" in html
    assert "/api/review/runs" in html, "页面应自连自己的 JSON API"
    assert "phaser" not in html.lower(), "本面板不依赖 Phaser(照 reflections 面板的做法)"


def test_router_can_be_attached_to_a_host_app():
    """挂到别的 FastAPI 应用上,路径不变(5010 就是这么挂的)。"""
    from fastapi import FastAPI
    from case01 import review_app

    host = FastAPI()
    review_app.attach_to(host, current_run_id=MAVIS_RUN, note="正在跑")
    c = TestClient(host)
    assert c.get("/review").status_code == 200
    assert c.get("/api/review/runs").status_code == 200
    assert c.get("/api/review/runs").json()["current_run_id"] == MAVIS_RUN
    assert c.get("/api/review/run/{}".format(MAVIS_RUN)).status_code == 200


def test_empty_root_reports_zero_runs_instead_of_crashing(monkeypatch, tmp_path):
    """记录根不存在时,列表要返回 0 条而不是抛异常(CI 里 runs/ 缺席就是这种情形)。"""
    from case01 import review_app
    monkeypatch.setattr(review_app, "RUNS_DIR", str(tmp_path / "nope"))
    c = _client()
    assert c.get("/api/review/health").json()["runs"] == 0
    assert c.get("/api/review/runs").json()["count"] == 0
    assert c.get("/review").status_code == 200
    assert c.get("/api/review/run/{}".format(MAVIS_RUN)).status_code == 404
