# -*- coding: utf-8 -*-
"""小镇页移动/避让的行为守卫(2026-10-09)。

为什么要有:用户反馈"有走路但是被前面的人挡住,从而一直在一个地方走"。
根因是 `frontend/templates/main_script.html` 的两处几何逻辑,而
`tests/frontend_smoke.js` **只查语法** —— 逻辑写错(走不到/原地踏步)照样 green。
所以用 `case01/tools/town_move_probe.js` 在最小桩里**真跑** moveAgent + update,
断言行为:该走的人走到目标格、停下的人不再原地踏步、气泡挂在角色正上方。

本测试**不连服务**:用 TestClient 渲染首页 → 写临时 HTML → 交给 node 探针。
渲染桩与 `test_town_bubble.py` 同一套(小镇页由实时面 `live.routes` 渲染)。
"""
import os
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(os.path.dirname(_HERE))          # provenance/provenance(包目录)
_PROBE = os.path.join(_PKG, "case01", "tools", "town_move_probe.js")


class _FakeAgent:
    """最小 agent 桩:首页只读它的倾向/约束。"""

    def __init__(self, name):
        self.name = name
        self.role_type = "user"
        self.initial_tendency = {}
        self.status = {"value_tendency": {}, "tendency_window_n": 0,
                       "tendency_window": []}

    def get_tendency(self):
        return {}

    def get_constraints(self):
        return {}


def _fake_game():
    return SimpleNamespace(
        agents={"AI Advisor": _FakeAgent("AI Advisor")},
        consequence=SimpleNamespace(
            health=lambda: {"total_calls": 0, "degraded_calls": 0,
                            "degrade_rate": 0.0, "last_error": ""}),
        _timer=SimpleNamespace(get_date=lambda *a: "20250213-10:00"))


@pytest.fixture
def index_html(tmp_path, monkeypatch):
    """渲染小镇首页到字符串(桩同 test_town_bubble.py,否则模板渲染直接抛)。"""
    from live import routes as lf
    from live import state

    tmp_ckpt = tmp_path / "results" / "checkpoints"
    tmp_ckpt.mkdir(parents=True)
    monkeypatch.setattr(state, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(state, "server", SimpleNamespace(game=_fake_game()))
    monkeypatch.setattr(state, "sim_state", {
        "name": "test-sim", "status": "running",
        "start_time": "20250213-09:30", "stride": 2})
    monkeypatch.setattr(state, "compressor", SimpleNamespace(
        checkpoints_folder=str(tmp_ckpt), started=False))
    return TestClient(lf.app).get("/").text


@pytest.mark.skipif(shutil.which("node") is None, reason="没有 node,跳过页面 JS 行为检查")
def test_walkers_arrive_and_stop(index_html, tmp_path):
    page = tmp_path / "index.html"
    page.write_text(index_html, encoding="utf-8")
    proc = subprocess.run(["node", _PROBE, "--page-file", str(page)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    # 只看"零失败 + 至少跑了这么多条";不写死精确条数,免得每加一条断言都要改这里。
    m = re.search(r"(\d+) passed, 0 failed", out)
    assert m, out
    assert int(m.group(1)) >= 16, out


def test_page_script_keeps_move_helpers(index_html):
    """内联脚本必须真的带着这几个东西(防改名/漏拷到模板)。

    探针自带"找不到 moveAgent 就退出 1"的兜底,但那条只在 node 可用时跑;
    这条纯文本断言在无 node 环境下也能守住。
    """
    for token in ("function moveAgent", "function layoutBubbles", "function _bubbleHitsBox",
                  "Math.abs(dx) <= step_px && Math.abs(dy) <= step_px"):
        assert token in index_html, "页面脚本缺 {}".format(token)
