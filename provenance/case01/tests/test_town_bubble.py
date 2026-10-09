# -*- coding: utf-8 -*-
"""小镇页头顶气泡「聊天记录 + 打字机」的行为守卫(2026-10-09)。

为什么要有:需求是"主角头上顶着那个要直接是聊天记录,要有打字机的效果"(2026-10-09),
而气泡原来显示的是"角色名: 行动文本"(纯文本、一次性全显、无打字机)。
这段逻辑活在 `frontend/templates/main_script.html` 的一大坨内联 JS 里,
`tests/frontend_smoke.js` **只查语法** —— 逻辑写错(不吐字/不截断/行动文本抢镜)
照样 green。所以用 `case01/tools/town_bubble_probe.js` 在最小桩下**真跑**一遍,断言行为。

本测试**不连服务**:用 TestClient 渲染首页 → 写临时 HTML → 交给 node 探针。
"""
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(os.path.dirname(_HERE))          # provenance/provenance(包目录)
_PROBE = os.path.join(_PKG, "case01", "tools", "town_bubble_probe.js")


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
    """渲染小镇首页到字符串。

    小镇页由**实时面**(`live.routes`)渲染 —— review_app 是另一个 app(结果审阅面板)。
    首页依赖运行期状态(group `sim_state` 的 start_time、game、compressor),
    所以这里照 `tests/test_live_api.py::client` 的做法把它们 monkeypatch 成最小假对象;
    否则 Jinja 里的 `start_datetime` 为空,模板渲染直接抛(实测 ValueError)。
    """
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
def test_bubble_shows_chat_with_typewriter(index_html, tmp_path):
    page = tmp_path / "index.html"
    page.write_text(index_html, encoding="utf-8")
    proc = subprocess.run(["node", _PROBE, "--page-file", str(page)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "8 passed, 0 failed" in out, out


def test_page_script_defines_bubble_helpers(index_html):
    """内联脚本必须真的定义了这三个函数(防改名/漏拷到模板)。

    探针自带"找不到 setBubble 就退出 1"的兜底,但那条只在 node 可用时跑;
    这条纯文本断言在无 node 环境下也能守住"函数还在"。
    """
    for fn in ("function setBubble", "function tickBubbles", "function setActionBubble"):
        assert fn in index_html, "页面脚本缺 {}".format(fn)


def test_bubble_text_has_word_wrap(index_html):
    """头顶气泡的文本对象必须设 wordWrap —— 否则长句横向铺成一行糊在头上。

    2026-10-09 用户反馈"你的聊天气泡要支持换行啊":气泡原文本对象只设了字体/底色,
    没有 wordWrap,长句不折行。这条钉住气泡**自己那段**的换行配置。

    注意:`add_text`(另一个函数)本来就有 wordWrap —— 所以不能只断言"页面里有
    wordWrap",那样 add_text 一处在就永远绿(假绿)。这里钉**气泡专用的限宽值 200**,
    它只出现在气泡那段配置里。
    """
    assert "wordWrap: { width: 200" in index_html, \
        "气泡文本缺专属换行配置(wordWrap width:200)——长句不会换行"
    # 气泡那段还得开中文友好的 useAdvancedWrap
    bubble_block = index_html.split("pronunciatios[persona_name] = this.add.text", 1)
    assert len(bubble_block) == 2, "找不到气泡文本对象的创建处(结构变了?)"
    assert "wordWrap" in bubble_block[1][:400], "气泡创建块里没有 wordWrap"
