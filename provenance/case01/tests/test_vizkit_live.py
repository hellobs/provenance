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