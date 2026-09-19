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


def _live(port=5099):
    return create("live", port=port, roles=ROLES, alias=ALIAS, scenario_dir=SCENARIO,
                  static_root=os.path.join(FRONTEND, "static"),
                  template_dir=os.path.join(FRONTEND, "templates"),
                  ping_interval=30.0)


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