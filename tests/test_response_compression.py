# -*- coding: utf-8 -*-
"""响应压缩(2026-09-24 体检补):两个 5010 面都必须压大响应。

为什么单独立一条守卫:压缩是**跨两个实现**的口径 —— case00 面加在 `live/routes.py`,
case01 面加在 `packages/mavis-vizkit` 插件的 `_build_app()` 里(那个包不认识
provenance 的模块,不能共用一份代码)。任何一边漏掉,平台侧拿到的都是几百 KB
起步的响应,而两边各自的测试都不会报错 —— 所以两边各有一条行为测试。
本文件守 case00 面;case01 面在 `packages/mavis-vizkit/tests/test_vizkit_live.py`。

实测(2026-09-24,带 `Accept-Encoding: gzip`):
  954 223 B 的 case00 痕迹详情 → 179 713 B(18.8%)
  32 444 B 的记录列表        → 1 684 B(5.2%)
"""
import os
import sys

import pytest

_this_dir = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_this_dir)
_PKG = os.path.join(_REPO, "provenance")
for _p in (_PKG, _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 仓库里自带的静态文件(1 MB 前端库),不依赖 records 数据是否在场 → CI 干净克隆也能跑
_BIG_STATIC = "/static/vendor/phaser.min.js"


@pytest.fixture
def case00_app():
    from live.routes import app
    return app


def test_case00_face_compresses_big_responses(case00_app):
    from fastapi.testclient import TestClient

    c = TestClient(case00_app)
    big = c.get(_BIG_STATIC, headers={"Accept-Encoding": "gzip"})
    assert big.status_code == 200
    assert len(big.content) > 100 * 1024, "这个文件本来就有 1 MB;变小说明静态目录换了"
    assert big.headers.get("content-encoding") == "gzip"


def test_case00_face_does_not_compress_small_responses(case00_app):
    """/health 只有几百字节:给它加 20 字节头 + 一次压缩是净亏。"""
    from fastapi.testclient import TestClient

    c = TestClient(case00_app)
    small = c.get("/health", headers={"Accept-Encoding": "gzip"})
    assert small.status_code == 200
    assert len(small.content) < 1024
    assert small.headers.get("content-encoding") is None


def test_case00_face_keeps_working_for_clients_without_gzip(case00_app):
    """不能解压的客户端(老脚本、curl 忘了 --compressed)必须仍拿到原样响应。"""
    from fastapi.testclient import TestClient

    c = TestClient(case00_app)
    plain = c.get(_BIG_STATIC, headers={"Accept-Encoding": "identity"})
    assert plain.status_code == 200
    assert plain.headers.get("content-encoding") is None
    assert len(plain.content) > 100 * 1024


def test_websocket_is_not_affected(case00_app):
    """压缩只走 HTTP:实时推演的 WS 帧不能被套上 gzip(否则前端解不了)。"""
    from fastapi.testclient import TestClient

    c = TestClient(case00_app)
    with c.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert "type" in first
