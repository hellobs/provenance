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


def test_embed_scene_routes_serve_bare_scene(frontend, scenario_dir):
    """嵌入面:外部平台靠它 iframe 只取场景。

    5001(provenance 的实时服务)早就有 /embed/scene;本插件此前只有 /,
    模板里的 embed 变量被写死成 ""——于是 case01 这条实时线没法只嵌场景。
    """
    from fastapi.testclient import TestClient

    live = _live(frontend, scenario_dir, port=5095)
    client = TestClient(live.app)
    for path in ("/embed/scene", "/embed", "/?embed=scene"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert ROLE_A in r.text and ROLE_B in r.text, path


def test_cors_default_is_loopback_only(frontend, scenario_dir, monkeypatch):
    """安全体检 2026-09-23:本插件的默认跨源白名单**只给本机来源**。

    为什么这条测试必须在本包里:实测踩过 —— 只改了平台侧 `live/routes.py`,
    而 case01 这条实时面根本不 import 它,跑起来照样回 `ACAO=*`。
    两处默认值同一口径(`live/netguard.py::resolve_embed_origins`),改一处要改两处。
    """
    from fastapi.testclient import TestClient

    monkeypatch.delenv("EMBED_ALLOW_ORIGINS", raising=False)
    live = _live(frontend, scenario_dir, port=5094)
    client = TestClient(live.app)
    evil = client.get("/", headers={"Origin": "https://evil.example"})
    assert evil.headers.get("access-control-allow-origin") is None, \
        "默认白名单下外站不该拿到 ACAO"
    ok = client.get("/", headers={"Origin": "http://127.0.0.1:5010"})
    assert ok.headers.get("access-control-allow-origin") == "http://127.0.0.1:5010"


def test_cors_allowlist_can_be_widened_explicitly(frontend, scenario_dir, monkeypatch):
    """给了域名就按给的来(平台侧跨源取数靠它),不是把它一起禁掉。"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("EMBED_ALLOW_ORIGINS", "https://gov.example")
    live = _live(frontend, scenario_dir, port=5093)
    client = TestClient(live.app)
    r = client.get("/", headers={"Origin": "https://gov.example"})
    assert r.headers.get("access-control-allow-origin") == "https://gov.example"


def test_canvas_face_is_the_bare_canvas(frontend, scenario_dir):
    """`/embed/canvas`(平台侧"只嵌一个画布"用的最小面)= embed=scene + chrome=0。

    为什么单独立一条路径:对接文档里要能写一个不会被 query 拼错的地址;
    而 chrome=0 让模板**根本不渲染**顶栏、时钟、状态浮层与"重开一局"卡片
    (那一块带着 POST /control/restart 的按钮与分支下拉)。
    """
    from fastapi.testclient import TestClient

    live = _live(frontend, scenario_dir, port=5092)
    client = TestClient(live.app)
    canvas = client.get("/embed/canvas")
    assert canvas.status_code == 200
    assert 'data-embed="scene"' in canvas.text
    assert 'data-chrome="0"' in canvas.text
    for path in ("/embed/scene?chrome=0", "/?embed=scene&chrome=0"):
        r = client.get(path)
        assert r.status_code == 200 and 'data-chrome="0"' in r.text, path
    # 默认仍是带 chrome 的嵌入面(别把原有行为一起改了)
    assert 'data-chrome="1"' in client.get("/embed/scene").text


def test_health_reports_the_run_id(frontend, scenario_dir):
    """`/health` 要报这一局叫什么:平台侧只嵌画布时,靠它把画布与记录对上。"""
    from fastapi.testclient import TestClient

    live = create("live", port=5091, roles=[ROLE_A, ROLE_B], alias=ALIAS,
                  scenario_dir=scenario_dir, static_root=frontend["static"],
                  template_dir=frontend["template"], ping_interval=30.0,
                  run_id="260924-live-case01-mavis-B-1200")
    h = TestClient(live.app).get("/health").json()
    assert h["run_id"] == "260924-live-case01-mavis-B-1200"
    # 没给 run_id 时也要有这个字段(空串),别让对接方去猜字段是否存在
    live2 = _live(frontend, scenario_dir, port=5090)
    assert TestClient(live2.app).get("/health").json()["run_id"] == ""