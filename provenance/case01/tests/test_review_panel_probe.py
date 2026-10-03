# -*- coding: utf-8 -*-
"""专家面深链页签的行为守卫(2026-09-25 第十五轮体检)。

为什么要有:文档《给平台侧_嵌入与数据接入》§五 承诺"tab 非法 → 页面上黄条说明并回到默认页签",
而实测 `/embed/review?tab=injector` 会把**主区域**渲染成注入器面板(导航里却没有这个页签)——
那句 SAFE 守卫写在读 `WANT_TAB` 之前,是死代码。

这条测试**不连服务**:用 TestClient 渲染页面 → 写临时 HTML → 交给
`case01/tools/review_panel_probe.js --page-file ... --only-deeplink`
(那个探针用最小 DOM stub 在 node 里真跑页面 JS,是仓库里既有的运行期自查工具)。
"""
import io
import os
import shutil
import subprocess
import sys

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(os.path.dirname(_HERE))          # provenance/provenance(包目录)
_PROBE = os.path.join(_PKG, "case01", "tools", "review_panel_probe.js")


def _render(path):
    from case01.review_app import app
    return TestClient(app).get(path).text


def test_probe_dom_stub_covers_host_protocol():
    """探针自己的 DOM stub 必须带上宿主协议要用的 API。

    2026-09-25:探针在 `window.addEventListener` 上初始化就抛 —— 宿主协议落地后 stub 没跟上,
    这个唯一的运行期自查工具**静默坏了**(它不在 CI 里,没人发现)。这里钉住它至少能初始化。
    """
    src = io.open(_PROBE, encoding="utf-8").read()
    for api in ("addEventListener", "postMessage", "parent"):
        assert api in src, "探针 stub 缺 {} —— 宿主协议会让它在初始化阶段抛".format(api)


@pytest.mark.skipif(shutil.which("node") is None, reason="没有 node,跳过页面 JS 行为检查")
def test_expert_face_deeplink_tab_behaviour(tmp_path):
    html = _render("/embed/review")
    page = tmp_path / "embed_review.html"
    page.write_text(html, encoding="utf-8")
    proc = subprocess.run(["node", _PROBE, "--page-file", str(page), "--only-deeplink"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "合计通过" in out, out
    assert "专家面 ?tab=injector" in out, out


@pytest.mark.skipif(shutil.which("node") is None, reason="没有 node,跳过页面 JS 行为检查")
def test_overview_renders_every_branch_source_label(tmp_path):
    """概览的"分支来源"一行:五种取值 + 未知值都要渲染成可读标签。

    两个静态源码测试查不出来的运行期回归(2026-10-03 实测):
    ① judge-failed/rules/unknown 以前落到 "—",和"压根没这个字段"无法区分;
    ② 标签是写死的可信 HTML,外层误包 esc() 会把 <span> 转义成页面上可见的
    "&lt;span&gt;…" 文本。探针用 DOM stub 真渲染 PANES.overview 才能抓住。
    """
    html = _render("/review")
    page = tmp_path / "review.html"
    page.write_text(html, encoding="utf-8")
    proc = subprocess.run(["node", _PROBE, "--page-file", str(page), "--only-deeplink"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "概览 · 分支来源标签" in out, out
    for piece in ("source=preset → 实验设计预设",
                  "source=judge → 由 AI 回答判定",
                  "source=judge-failed → 判定失败",
                  "source=rules → 规则判定",
                  "source=unknown → 来源未记录",
                  "source=weird-x → 未记录(原值可见)"):
        assert piece in out, out
