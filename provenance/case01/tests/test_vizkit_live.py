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


def _live(port=5099, extra_panels=None):
    return create("live", port=port, roles=ROLES, alias=ALIAS, scenario_dir=SCENARIO,
                  static_root=os.path.join(FRONTEND, "static"),
                  template_dir=os.path.join(FRONTEND, "templates"),
                  ping_interval=30.0, extra_panels=extra_panels)


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