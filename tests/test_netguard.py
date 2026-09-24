# -*- coding: utf-8 -*-
"""暴露面守卫的测试(2026-09-23 安全体检)。

守的是三件事:①非本机绑定必须显式声明,否则拒绝启动;
②跨源白名单默认只给本机来源(原来默认 `*`,而服务没有鉴权);
③默认配置下,外站 Origin 拿不到 ACAO。
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

from live import netguard as NG  # noqa: E402


def test_loopback_is_recognised():
    for host in ("127.0.0.1", "localhost", "::1", "", " 127.0.0.1 "):
        assert NG.is_loopback(host), host
    # 用 192.0.2.7(TEST-NET-1,保留给文档用),不把本机内网地址写进仓库
    for host in ("0.0.0.0", "192.0.2.7", "::", "example.com"):
        assert not NG.is_loopback(host), host


def test_remote_bind_is_refused_without_explicit_optin(monkeypatch):
    """默认拒绝:一句 warning 会被忽略,而端口已经开了。"""
    monkeypatch.delenv(NG.REMOTE_ENV, raising=False)
    with pytest.raises(SystemExit) as exc:
        NG.require_explicit_remote("0.0.0.0", where="5010 实时面")
    msg = str(exc.value)
    assert "没有任何鉴权" in msg and NG.REMOTE_ENV in msg, msg


def test_remote_bind_allowed_after_optin(monkeypatch):
    monkeypatch.setenv(NG.REMOTE_ENV, "1")
    NG.require_explicit_remote("0.0.0.0")          # 不抛就算过
    monkeypatch.delenv(NG.REMOTE_ENV, raising=False)
    NG.require_explicit_remote("127.0.0.1")        # 回环永远放行


def test_exposure_lines_name_the_write_endpoints():
    """开放端口前把"暴露了什么"列出来:5010 的**四个**写端点必须在清单里。

    第四个(`/control/restart`)此前漏登记:它不在 `live/routes.py` 里,而在
    `packages/mavis-vizkit` 的 live 插件里 —— 只扫 routes.py 会漏掉。
    """
    text = "\n".join(NG.exposure_lines("0.0.0.0", 5010))
    for ep in ("/api/goals", "/api/undo-intervention", "/api/reflections/mark",
               "/control/restart"):
        assert ep in text, ep
    assert "8060" in text, "配置工具的写端点也必须点名"


def test_embed_origins_default_is_loopback_only():
    """默认白名单:只给本机来源;显式给了就按给的来。"""
    assert NG.resolve_embed_origins("") == ["http://127.0.0.1:5010",
                                            "http://localhost:5010"]
    assert "*" not in NG.resolve_embed_origins("")
    assert NG.resolve_embed_origins("https://gov.example, https://a.example") == [
        "https://gov.example", "https://a.example"]


def test_foreign_origin_gets_no_acao_by_default(monkeypatch):
    """行为级:没设 EMBED_ALLOW_ORIGINS 时,外站 Origin 不许拿到 ACAO。"""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from live.routes import app

    c = TestClient(app)
    r = c.get("/api/runs", headers={"Origin": "https://evil.example"})
    acao = r.headers.get("access-control-allow-origin")
    assert acao is None, "默认白名单下不该给外站 ACAO,实际:{}".format(acao)
    r2 = c.get("/api/runs", headers={"Origin": "http://127.0.0.1:5010"})
    assert r2.headers.get("access-control-allow-origin") == "http://127.0.0.1:5010"
