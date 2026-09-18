# -*- coding: utf-8 -*-
"""实时可视化插件测试:WS 握手、实时投递、事件缓冲、页面可渲染。"""
import pytest

from mavis_vizkit import create
from mavis_vizkit.plugins.live import LiveVisualizer

from conftest import ALIAS, ROLE_A, ROLE_B


def _live(frontend, scenario_dir, port=5099):
    return create("live", port=port, roles=[ROLE_A, ROLE_B], alias=ALIAS,
                  scenario_dir=scenario_dir, static_root=frontend["static"],
                  template_dir=frontend["template"], ping_interval=30.0)


def test_registered_and_config(frontend, scenario_dir):
    live = _live(frontend, scenario_dir)
    assert isinstance(live, LiveVisualizer)
    assert live.url().startswith("http://127.0.0.1:")
    assert set(live.init_pos) == {ROLE_A, ROLE_B}, "初始坐标应来自场景配置"
    for coord in live.init_pos.values():
        assert coord, "坐标不能为空"


def test_events_buffered_before_server_starts(frontend, scenario_dir):
    """服务未起时事件进缓冲,不丢语义(无头运行/单测可用)。"""
    live = _live(frontend, scenario_dir)
    live.on_event({"type": "agent", "name": ROLE_A, "coord": [9, 7], "time": "t"})
    live.on_event({"type": "chat_line", "speaker": ROLE_A, "text": "hi", "time": "t"})
    pending = live.pending()
    assert [m["type"] for m in pending] == ["agent", "chat_line"]
    assert pending[0]["texture"] == ALIAS[ROLE_A]          # 贴图别名生效
    assert live.drain_pending() and not live.pending()


def test_websocket_handshake_and_live_delivery(frontend, scenario_dir):
    from fastapi.testclient import TestClient

    live = _live(frontend, scenario_dir, port=5098)
    client = TestClient(live.app)
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "init"
        assert first["agents"] == [ROLE_A, ROLE_B]
        assert first["textures"][ROLE_A] == ALIAS[ROLE_A]

        live.on_event({"type": "agent", "name": ROLE_A, "coord": [9, 7],
                       "action": "asking", "time": "2026-08-27"})
        msg = ws.receive_json()
        assert msg["type"] == "agent"
        assert msg["name"] == ROLE_A and msg["coord"] == [9, 7]

        live.on_event({"type": "chat_line", "speaker": ROLE_A, "text": "explain"})
        chat = ws.receive_json()
        assert chat == {"type": "chat_line", "speaker": ROLE_A, "text": "explain"}


def test_snapshot_is_sent_to_late_joiner(frontend, scenario_dir):
    from fastapi.testclient import TestClient

    live = _live(frontend, scenario_dir, port=5097)
    live.on_event({"type": "snapshot", "agents": {ROLE_A: {"coord": [9, 7]}},
                   "time": "2026-08-27"})
    client = TestClient(live.app)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "init"
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and ROLE_A in snap["agents"]


def test_index_page_renders_with_alias_assets(frontend, scenario_dir):
    from fastapi.testclient import TestClient

    live = _live(frontend, scenario_dir, port=5096)
    client = TestClient(live.app)
    page = client.get("/")
    assert page.status_code == 200
    body = page.text
    assert ROLE_A in body and ROLE_B in body
    # 角色贴图别名可直接取到(前端按 /static/assets/village/agents/<角色>/portrait.png)
    for role in (ROLE_A, ROLE_B):
        assert client.get("/static/assets/village/agents/{}/portrait.png"
                          .format(role)).status_code == 200
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["roles"] == [ROLE_A, ROLE_B]