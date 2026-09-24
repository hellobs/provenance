# -*- coding: utf-8 -*-
"""暴露面守卫的测试(2026-09-23 安全体检)。

守的是三件事:①非本机绑定必须显式声明,否则拒绝启动;
②跨源白名单默认只给本机来源(原来默认 `*`,而服务没有鉴权);
③默认配置下,外站 Origin 拿不到 ACAO。
"""
import io
import os
import re
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


# --------------------------------------------------------------- 清单与代码必须一致
# 为什么要这两条:2026-09-24 CI 红过一次 —— vizkit 插件里注册了 `/control/restart`,
# 暴露面清单没登记,而当时的测试只断言"清单里有那几个字面量",于是清单和代码
# 各说各话,谁也发现不了。现在**从代码里扫**写端点,双向对齐。

# 所有可能注册路由的面(含 vizkit 插件 —— 漏掉它就等于漏掉 /control/restart)
_SURFACES = (
    "provenance/live/routes.py",
    "provenance/live/history.py",
    "provenance/case01/review_app.py",
    "provenance/live_fastapi.py",
    "provenance/case01/vizkit/live_run.py",
    "packages/mavis-vizkit/src/mavis_vizkit/plugins/live.py",
    "packages/mavis-vizkit/src/mavis_vizkit/plugins/report.py",
    "packages/mavis-vizkit/src/mavis_vizkit/plugins/town.py",
    "packages/mavis-vizkit/src/mavis_vizkit/plugins/console.py",
)
_WRITE_DECORATOR = re.compile(
    r"@(?:app|router)\.(?:post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']")


def _registered_write_paths():
    """从代码里扫出真实注册的写端点 → {path: 文件}。"""
    found = {}
    for rel in _SURFACES:
        p = os.path.join(_REPO, rel)
        if not os.path.isfile(p):
            continue
        text = io.open(p, encoding="utf-8", errors="replace").read()
        for m in _WRITE_DECORATOR.finditer(text):
            found.setdefault(m.group(1), rel)
    return found


def test_scanner_actually_sees_the_plugin():
    """先证明扫描面本身没漏:漏了 vizkit 插件,下面的守卫就是假的。"""
    reg = _registered_write_paths()
    assert "/control/restart" in reg, "扫描面里没有 vizkit 插件(它才是最容易漏的一处)"
    assert reg["/control/restart"].endswith("plugins/live.py")


def test_every_registered_write_endpoint_is_listed():
    """代码里注册的写端点,必须在 `netguard.WRITE_ENDPOINTS` 里有名字。

    加一个新的写端点而忘了登记 → 这条红(而不是等到"有人开端口时才发现清单不全")。
    """
    listed = "\n".join(NG.WRITE_ENDPOINTS)
    missing = {p: rel for p, rel in _registered_write_paths().items() if p not in listed}
    assert not missing, (
        "这些写端点没进 netguard.WRITE_ENDPOINTS(暴露面清单):{}"
        " —— 它们是**无鉴权**的状态变更口子,开放端口前必须点名。".format(missing))


def test_listed_5010_endpoints_actually_exist():
    """反向:清单里写的 5010 端点必须真在代码里(防陈旧条目/拼错)。"""
    reg = _registered_write_paths()
    for line in NG.WRITE_ENDPOINTS:
        if not line.startswith("5010"):
            continue
        m = re.search(r"POST\s+(/\S+)", line)
        assert m, "清单条目格式变了,守卫读不出端点:{}".format(line)
        assert m.group(1) in reg, "清单写了代码里没有的端点:{}".format(line)


_MAVIS_CONFIG_TOOL = os.path.join(_REPO, "..", "mavis", "config_tool", "app.py")


def test_config_tool_write_count_matches_the_list():
    """清单里 8060 那行写的"等 N 个写端点"必须与代码一致。

    为什么要它:8060 是**无鉴权**的写面(改场景/删角色/执行运行),清单上那个数字若漂了,
    读清单的人会低估暴露面。数字来自真实 decorator 计数,不靠人记。
    """
    if not os.path.isfile(_MAVIS_CONFIG_TOOL):
        pytest.skip("mavis 仓不在预期位置(与 test_mavis_purity 同口径)")
    src = io.open(_MAVIS_CONFIG_TOOL, encoding="utf-8", errors="replace").read()
    actual = len(re.findall(r"@app\.(?:post|put|patch|delete)\(", src))
    m = re.search(r"等\s*(\d+)\s*个写端点", "\n".join(NG.WRITE_ENDPOINTS))
    assert m, "清单里没有写端点数量(格式变了,守卫读不出来)"
    claimed = int(m.group(1))
    assert claimed == actual, "8060 代码里 {} 个写端点,清单写 {} 个".format(actual, claimed)
