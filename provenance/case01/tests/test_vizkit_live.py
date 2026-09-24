# -*- coding: utf-8 -*-
"""实时可视化插件测试(经 mavis-vizkit 接真实前端):WS 握手、实时投递、事件缓冲、页面可渲染。"""
import os

import pytest

from mavis_vizkit import create
from mavis_vizkit.plugins.live import LiveVisualizer

SCENARIO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "injector", "scenario")
FRONTEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend")
ROLES = ["Investment AI", "Ethan Lin"]
ALIAS = {"Investment AI": "AI Advisor", "Ethan Lin": "Mr. Zhou"}


def _live(port=5099, extra_panels=None, on_restart=None, restart_choices=None):
    return create("live", port=port, roles=ROLES, alias=ALIAS, scenario_dir=SCENARIO,
                  static_root=os.path.join(FRONTEND, "static"),
                  template_dir=os.path.join(FRONTEND, "templates"),
                  ping_interval=30.0, extra_panels=extra_panels,
                  on_restart=on_restart, restart_choices=restart_choices)


def test_registered_and_config():
    live = _live()
    assert isinstance(live, LiveVisualizer)
    assert live.url().startswith("http://127.0.0.1:")
    assert set(live.init_pos) == set(ROLES), "初始坐标应来自场景配置"
    for coord in live.init_pos.values():
        assert coord, "坐标不能为空"


def test_events_buffered_before_server_starts():
    live = _live()
    live.on_event({"type": "agent", "name": "Ethan Lin", "coord": [9, 7], "time": "t"})
    live.on_event({"type": "chat_line", "speaker": "Ethan Lin", "text": "hi", "time": "t"})
    pending = live.pending()
    assert [m["type"] for m in pending] == ["agent", "chat_line"]
    assert pending[0]["texture"] == "Mr. Zhou"          # 贴图别名生效
    assert live.drain_pending() and not live.pending()


def test_websocket_handshake_and_live_delivery():
    from fastapi.testclient import TestClient

    live = _live(port=5098)
    client = TestClient(live.app)
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "init"
        assert first["agents"] == ROLES
        assert first["textures"]["Investment AI"] == "AI Advisor"

        live.on_event({"type": "agent", "name": "Ethan Lin", "coord": [9, 7],
                       "action": "asking", "time": "2026-08-27"})
        msg = ws.receive_json()
        assert msg["type"] == "agent"
        assert msg["name"] == "Ethan Lin" and msg["coord"] == [9, 7]

        live.on_event({"type": "chat_line", "speaker": "Investment AI", "text": "explain"})
        chat = ws.receive_json()
        assert chat == {"type": "chat_line", "speaker": "Investment AI", "text": "explain"}


def test_snapshot_is_sent_to_late_joiner():
    from fastapi.testclient import TestClient

    live = _live(port=5097)
    live.on_event({"type": "snapshot", "agents": {"Ethan Lin": {"coord": [9, 7]}},
                   "time": "2026-08-27"})
    client = TestClient(live.app)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "init"
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and "Ethan Lin" in snap["agents"]


def _recv_until(ws, kind, limit=10):
    """收消息直到出现指定类型(容忍追赶快照/积压事件夹在中间)。"""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == kind:
            return msg
    raise AssertionError("在 {} 条消息内没有收到 {}".format(limit, kind))


def test_index_page_renders_with_alias_assets():
    from fastapi.testclient import TestClient

    live = _live(port=5096)
    client = TestClient(live.app)
    page = client.get("/")
    assert page.status_code == 200
    body = page.text
    assert "Investment AI" in body and "Ethan Lin" in body
    assert client.get("/static/assets/village/agents/Investment AI/portrait.png").status_code == 200
    assert client.get("/static/assets/village/agents/Ethan Lin/portrait.png").status_code == 200
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["roles"] == ROLES


def test_page_shows_visible_run_status():
    """页面必须有可见的运行状态指示(**不允许静默**)。

    起因:推演跑完后服务继续保持(--hold),新开页面的人只看到不动的小镇、
    没有任何说明,于是反馈"可视化里的人根本没反应"。
    """
    from fastapi.testclient import TestClient

    live = _live(port=5093)
    body = TestClient(live.app).get("/").text
    assert 'id="sim-status"' in body and 'id="sim-status-text"' in body
    assert "setSimStatus" in body
    for state in ("connecting", "running", "finished", "error"):
        assert state in body, "状态机缺少 {} 分支".format(state)


def test_page_has_loud_end_of_run_banner():
    """用户明确要求:"要保证推演结束后有提示"。

    状态块那行小字不够——必须有一条居中的大横幅,并且把"成品记录还在生成"说清楚。
    """
    from fastapi.testclient import TestClient

    body = TestClient(_live(port=5092).app).get("/").text
    assert 'id="done-banner"' in body
    assert "showDoneBanner" in body
    for piece in ("推演结束", "成品记录正在生成", "推演被中断", "推演出错"):
        assert piece in body, "结束提示缺少 {} 文案".format(piece)
    # 结束横幅要盖在剧情事件横幅之上(结束是最该被看见的)
    assert "z-index: 320" in body


def test_restart_button_only_when_caller_registers_it():
    """推演结束后的小按钮"重开一局"(用户要求):注册了 on_restart 才有,没注册就不出现。

    同时验:页面上那个下拉来自通用的 restart_choices,选中的值会随 POST body
    交给回调(用户问"为什么跑的全是 B" —— 现在能在页面上选 A/B/C)。
    """
    from fastapi.testclient import TestClient

    plain = TestClient(_live(port=5083).app).get("/").text
    assert 'id="restart-btn"' not in plain, "没注册回调时不该有按钮"
    assert TestClient(_live(port=5082).app).post("/control/restart").status_code == 404

    seen = []

    def on_restart(payload):
        seen.append(payload)
        return {"ok": True, "detail": "已受理"}

    live = _live(port=5081, on_restart=on_restart,
                 restart_choices=[{"id": "branch", "label": "分支",
                                   "options": ["A", "B", "C"], "value": "B"}])
    c = TestClient(live.app)
    page = c.get("/").text
    assert 'id="restart-btn"' in page and "restartRun" in page
    assert 'id="restart-box"' in page and 'id="restart-choice-branch"' in page
    assert ">分支A</option>" in page and ">分支C</option>" in page
    # 按钮的显示由服务端 restart_ready 决定(结果出完才给),不看"跑完"
    assert "watchRestartReady" in page
    assert 'state === "finished"' not in page.split("watchRestartReady")[0][-400:], \
        "按钮不该在 setSimStatus 里直接显示"
    r = c.post("/control/restart", json={"branch": "A"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["detail"] == "已受理"
    assert seen == [{"branch": "A"}], "页面选的分支要原样交给回调"
    # 不带 body 也要能打(老页面/curl);先 begin_run 解除上一条的 pending
    live.begin_run()
    assert c.post("/control/restart").json()["ok"] is True
    assert seen[-1] == {}
    assert c.get("/health").json()["can_restart"] is True


def test_restart_rejects_form_posts():
    """表单/纯文本 POST 必须被拒:那是跨站可发的简单请求(不需要预检)。

    2026-09-24 体检实测:用 `Content-Type: text/plain` 打 /control/restart 竟然**受理了** ——
    也就是说浏览器里一个 <form> 就能替我们把一局重开掉。这里钉住 415,并且证明回调**没被调用**。
    """
    from fastapi.testclient import TestClient

    seen = []

    def on_restart(payload):
        seen.append(payload)
        return {"ok": True, "detail": "已受理"}

    live = _live(port=5080, on_restart=on_restart)
    c = TestClient(live.app)
    for ctype, body in (("text/plain", "branch=A"),
                        ("application/x-www-form-urlencoded", "branch=A"),
                        ("multipart/form-data; boundary=x", "branch=A")):
        r = c.post("/control/restart", content=body, headers={"Content-Type": ctype})
        assert r.status_code == 415, (ctype, r.status_code)
        assert "application/json" in r.json()["errors"][0]
    assert seen == [], "被拒的请求不许触发重开回调"
    # 正常 JSON 仍然通(别把自家页面一起挡了)
    assert c.post("/control/restart", json={"branch": "A"}).json()["ok"] is True
    assert seen == [{"branch": "A"}]


def test_restart_button_waits_until_results_are_ready():
    """用户要求:结果出完了才给"再来一次"的按钮。

    页面不能按"跑完(done)"就显示按钮,只能按服务端的 restart_ready
    (由调用方在成品记录落盘之后置位)。
    """
    from fastapi.testclient import TestClient

    live = _live(port=5078, on_restart=lambda payload: {"ok": True})
    c = TestClient(live.app)
    page = c.get("/").text
    assert "watchRestartReady" in page, "要有盯 restart_ready 的轮询"
    assert "/health" in page

    # 跑完但结果还没出:restart_ready=False,按钮不该显示
    live.finish("run_finished")
    h = c.get("/health").json()
    assert h["finished"] is True and h["restart_ready"] is False
    # 结果出完(调用方置位)
    live.set_restart_ready(True)
    assert c.get("/health").json()["restart_ready"] is True
    # 新一局开跑:再次收起
    live.begin_run()
    h2 = c.get("/health").json()
    assert h2["restart_ready"] is False and h2["finished"] is False


def test_second_restart_request_is_refused_until_the_next_run_starts():
    """用户要求:重开过一次之后,按钮要禁用 —— 服务端也要挡住重复请求。"""
    from fastapi.testclient import TestClient

    calls = []
    live = _live(port=5077, on_restart=lambda payload: calls.append(payload) or {"ok": True})
    c = TestClient(live.app)
    assert c.get("/health").json()["restart_pending"] is False
    assert c.post("/control/restart", json={"branch": "A"}).json()["ok"] is True
    assert c.get("/health").json()["restart_pending"] is True
    # 再来一次:被拒(页面此时也不该有按钮,因为 restart_ready 已被清)
    r2 = c.post("/control/restart", json={"branch": "B"}).json()
    assert r2["ok"] is False and "已经重开过一次" in r2["error"]
    assert len(calls) == 1, "第二次请求不该再交给回调"
    # 新一局开跑 → 解除
    live.begin_run()
    assert c.get("/health").json()["restart_pending"] is False
    assert c.post("/control/restart", json={"branch": "C"}).json()["ok"] is True
    assert len(calls) == 2


def test_restart_callback_failure_is_reported_to_the_page():
    """重开失败不能让页面以为成功了(不许静默)。"""
    from fastapi.testclient import TestClient

    def boom(payload):
        raise RuntimeError("桥接挂了")

    live = _live(port=5080, on_restart=boom)
    r = TestClient(live.app).post("/control/restart")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "桥接挂了" in body["error"]


def test_begin_run_clears_finished_state_for_the_next_round():
    """重开一局时插件必须被"重置":否则第二局从第一秒起就告诉页面"已结束",
    或者把上一局积压的 done 补发给刚刷新连上来的页面(实测:重开后又突然说推演结束)。"""
    from fastapi.testclient import TestClient

    live = _live(port=5079)
    c = TestClient(live.app)
    # 造上一局的残留:一条没发出去的 done + 追赶快照
    live.on_event({"type": "agent", "name": "Ethan Lin", "coord": [9, 7], "time": "t1"})
    live.finish("run_finished")
    assert c.get("/health").json()["finished"] is True
    assert live.pending(), "上一局的 done 还在积压里"

    live.begin_run()
    h = c.get("/health").json()
    assert h["finished"] is False and h["finish_reason"] == ""
    assert live.pending() == [], "上一局积压的事件必须清掉"
    with c.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "init"
        # 清干净了:既没有 done,也没有上一局的追赶快照
        live.on_event({"type": "time", "time": "2026-08-27-09:30"})
        assert ws.receive_json()["type"] == "time"


def test_bridge_close_can_keep_the_shared_visualizer():
    """跨局共享的插件不能被 bridge.close() 关掉(实测:重开后 5010 直接不监听)。"""
    from case01.injector.bridge import MavisBridge

    class _Viz:
        name = "live"

        def __init__(self):
            self.closed = False

        def on_event(self, event):
            pass

        def close(self):
            self.closed = True

    b = MavisBridge(nodes=[], roles=ROLES, scenario_dir=SCENARIO, dry_run=True)
    viz = _Viz()
    from mavis_vizkit import Fanout
    b._fanout = Fanout([viz])
    b.close(keep_visualizers=True)
    assert viz.closed is False, "重开时不能关掉共享插件"
    b.close()
    assert viz.closed is True, "进程退出时才真的关"


def test_clock_shows_the_date_not_only_hh_mm():
    """时钟必须带日期。

    case01 每个节点都是"某天 09:30",旧实现只截 HH:MM → 时钟永远显示 09:30,
    用户反馈"9:30 这个时间为什么一直没变"。现在渲染成 "MM-DD HH:MM"。
    """
    from fastapi.testclient import TestClient

    body = TestClient(_live(port=5091).app).get("/").text
    assert "function setCurrentTime" in body
    assert "m[2] + \"-\" + m[3]" in body, "时钟标签要拼出 MM-DD"
    assert "window.__sim_time_raw" in body, "原始时间串要留着(便于核对)"


def test_finish_broadcasts_done_to_connected_client():
    """跑完要主动广播 done:不让页面停在"人不动、也没人解释"的状态。"""
    from fastapi.testclient import TestClient

    live = _live(port=5092)
    client = TestClient(live.app)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "init"
        live.finish("run_finished")
        msg = _recv_until(ws, "done")
        assert msg["reason"] == "run_finished"
    assert live.finished is True


def test_late_joiner_after_finish_gets_synthesized_snapshot_and_done():
    """跑完之后才打开页面的人:先拿到合成快照(角色归位),再被告知已结束。"""
    from fastapi.testclient import TestClient

    live = _live(port=5091)
    live.on_event({"type": "agent", "name": "Ethan Lin", "coord": [9, 7],
                   "action": "asking", "location": "office", "time": "2026-09-15"})
    live.finish("run_finished")
    client = TestClient(live.app)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "init"
        snap = _recv_until(ws, "snapshot")
        assert snap.get("synthesized") is True
        assert snap["agents"]["Ethan Lin"]["coord"] == [9, 7]
        assert snap["time"] == "2026-09-15"
        assert _recv_until(ws, "done")["type"] == "done"


def test_finish_is_idempotent():
    live = _live(port=5090)
    live.finish("run_finished")
    live.finish("run_finished")
    assert [m["type"] for m in live.pending()] == ["done"]


def test_late_joiner_gets_current_state_not_history():
    """中途连进来的人看到的是"此刻",不是从头回放(否则角色会倒退)。

    本插件是**实时**可视化:快照已经代表当前状态,再把积压的历史 agent 事件
    放一遍,角色会先跳到最新位置、又被拽回旧位置。
    """
    from fastapi.testclient import TestClient

    live = _live(port=5089)
    live.on_event({"type": "agent", "name": "Ethan Lin", "coord": [1, 1], "time": "T1"})
    live.on_event({"type": "agent", "name": "Ethan Lin", "coord": [9, 7], "time": "T2"})
    client = TestClient(live.app)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "init"
        snap = _recv_until(ws, "snapshot")
        assert snap["agents"]["Ethan Lin"]["coord"] == [9, 7]
    assert live.pending() == [], "有快照时历史事件应被丢弃(否则角色倒退)"


def test_duplicate_init_in_backlog_is_dropped():
    """积压里那条 init 不再重复发:握手已经发过一次。"""
    from fastapi.testclient import TestClient

    live = _live(port=5088)
    live.on_event({"type": "init", "agents": ROLES})
    live.on_event({"type": "chat_line", "speaker": "Ethan Lin", "text": "hi"})
    client = TestClient(live.app)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "init"          # 握手那条
        msg = ws.receive_json()
        assert msg["type"] == "chat_line", "积压照发,但不应再出现第二条 init"


def test_extra_panels_render_as_cards_only_when_caller_provides_them():
    """调用方挂了额外面板才出卡片(与 Chat Log 同款);没挂的 face(比如 case00)一行都不多。

    这是"单一界面"的通用口子:本包不认识那些面板是什么,只把给定 URL 放进卡片的 iframe。
    2026-09-19 用户要求结果记录像 Chat Log 那样**嵌在页面里**,不要整页接管。
    """
    from fastapi.testclient import TestClient

    plain = TestClient(_live(port=5087).app).get("/").text
    assert 'class="extra-panel"' not in plain, "没给 extra_panels 时不该出现卡片"

    live = _live(port=5086, extra_panels=[{"id": "review", "label": "结果记录",
                                           "url": "/review?embed=1"}])
    c = TestClient(live.app)
    page = c.get("/").text
    assert 'id="panel-review"' in page and "结果记录" in page
    assert 'data-src="/review?embed=1"' in page, "卡片里 iframe 要指向调用方给的地址"
    # 同款卡片的三个部件:可点头部、折叠标记、单独打开
    for piece in ('id="panel-head-review"', 'id="panel-toggle-review"',
                  'id="panel-body-review"', "ep-open"):
        assert piece in page, piece
    # 不是整页接管:不该再有那套"切走小镇"的页签机制
    assert "face-tabs" not in page and "showFace" not in page
    # 嵌入面(case00/平台只要场景)不带卡片,免得把别人的界面塞进 iframe
    assert 'class="extra-panel"' not in c.get("/embed/scene").text


def test_health_reports_finished_state():
    """/health 报 finished/finish_reason:运维靠它分清"在推演/已跑完/仅审阅"。"""
    from fastapi.testclient import TestClient

    live = _live(port=5085)
    h = TestClient(live.app).get("/health").json()
    assert h["finished"] is False and h["finish_reason"] == ""
    live.finish("review_only")
    h2 = TestClient(live.app).get("/health").json()
    assert h2["finished"] is True and h2["finish_reason"] == "review_only"


def test_single_interface_service_hosts_town_and_review_panel():
    """**只维护一个界面**:5010 的页面里同时有小镇与结果面板(九块)。

    2026-09-19 用户拍板:小镇(过程)与结果(九块)合并到一个界面,独立的 5004 面板服务
    退役;结果面板要**像 Chat Log 那样嵌在右栏卡片里**(不是整页接管的页签)。
    做法 = 实时面 include case01 的结果面板路由 + 给它一张卡片(extra_panels)。
    """
    from fastapi.testclient import TestClient

    from case01.vizkit.live_run import build_service

    live = build_service(host="127.0.0.1", port=5084, roles=ROLES,
                         run_id="260919-live-case01-mavis-B-0001")
    c = TestClient(live.app)
    page = c.get("/").text
    assert 'id="panel-review"' in page, "结果记录要作为卡片嵌在页面里"
    assert 'data-src="/review?embed=1"' in page
    assert 'id="chat-panel"' in page and 'id="right-col"' in page, "与 Chat Log 同栏"
    # 同一个服务上确实挂着结果面板(页面 + 数据)
    assert c.get("/review").status_code == 200
    runs = c.get("/api/review/runs")
    assert runs.status_code == 200
    # 正在实跑的那条还没成品记录时,面板要能说清楚
    assert runs.json()["current_run_id"] == "260919-live-case01-mavis-B-0001"
    live.close()


def test_canvas_face_ships_no_experiment_vocabulary():
    """平台侧"只嵌一个画布"时,画布页的**源码**里不该出现实验相关的词。

    2026-09-24 第九轮体检实测:画布面发出去的 HTML 里有 HTML 注释
    "…那一块里带着 POST /control/restart 的按钮与分支下拉,不该出现在别人的页面上",
    main_script 里还有 JS 注释"把 restart_choices 里选中的值(现在只有分支)一起交给服务端"。
    DOM 藏起来不等于口径藏起来 —— 平台侧专家打开 devtools 就能读到"这实验有分支"。
    注释改成 Jinja 注释(只在模板里)之后,这里钉住它不再回来。
    """
    from fastapi.testclient import TestClient

    live = _live(port=5086)
    html = TestClient(live.app).get("/embed/canvas",
                                    headers={"Accept-Encoding": "identity"}).text
    for w in ("分支", "branch", "注入器", "injector", "实验", "预设", "判定方式"):
        assert w not in html, "画布面源码里出现实验词: {}".format(w)
    # 注:case00 专有的那几块 JS(治理/时间轴/重开)仍会随整块模板发出 ——
    # 那是模板共用的结构问题,按 chrome=0 摘 script 需要浏览器复核,
    # 已记在《0924 下一步方向》第九轮 P2。