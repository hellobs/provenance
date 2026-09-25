# -*- coding: utf-8 -*-
"""注入边界守卫(2026-09-21 体检新增,回答"引擎会不会注入 agent"这件事)。

边界(设计如此,这里把它变成可红的约束):
- **允许**:引擎/桥把"世界"给角色 —— 节点事件写成 mavis 的 story、请求一次交互、每步注入
  **步级上下文**(`external_state` → `agent.set_step_context`)。这三者是引擎的正当职责,
  信息控制本身就是 case01 的实验条件。
- **不允许**:写 agent 的**记忆 / 倾向 / 治理约束**,或绕过钩子直接改 agent 内部状态 ——
  那会同时破掉 IVD 不变量 2(记忆与倾向归内核)与"决策必须由 agent 产生"。

所以本文件既断言"该接的钩子接上了",也断言"不该碰的东西一处都没有"。
"""
import io
import os
import re

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "provenance")
# 阶段 3 抽包:注入器实现移至独立包,边界检查跟代码走
INJECTOR = os.path.join(_REPO, "packages", "mavis-case01-injector", "src",
                        "mavis_case01_injector")

pytestmark = pytest.mark.skipif(not os.path.isdir(INJECTOR), reason="case01 注入器不在")


def _py_files(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "tests")]
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _code_lines(root):
    """产出 (相对路径, 行号, 原文),跳过注释行 —— 注释里提到这些词是允许的(解释边界)。"""
    for p in _py_files(root):
        rel = os.path.relpath(p, _PKG).replace("\\", "/")
        for i, line in enumerate(io.open(p, encoding="utf-8", errors="replace").read().split("\n"), 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            yield rel, i, s


FORBIDDEN = [
    # 记忆 / 倾向 / 治理:agent 内部状态,引擎一律不许写
    (re.compile(r"attach_governance|set_constraints|governance\s*\.\s*(update|save)"),
     "写治理约束"),
    (re.compile(r"\.memory\s*\.\s*(add|append|insert|extend)|memory_stream\s*\.\s*(add|append)"),
     "写记忆"),
    (re.compile(r"initial_tendency\s*\[|value_tendency\s*\[|\.tendency\s*="),
     "改倾向"),
    (re.compile(r"\.status\s*\[[^\]]+\]\s*="), "改 agent 状态字典"),
    (re.compile(r"\.set_step_context\("), "自己调 set_step_context(只能由 mavis 在钩子里调)"),
]


def test_injector_never_writes_agent_internals():
    bad = []
    for rel, ln, s in _code_lines(INJECTOR):
        for pat, what in FORBIDDEN:
            if pat.search(s):
                bad.append((rel, ln, what, s[:90]))
    assert not bad, "注入器碰了 agent 内部状态(越界): {}".format(bad)


def test_sanctioned_injection_points_are_wired():
    """该接的钩子必须接上 —— 否则 case01 的"信息控制"其实没生效。"""
    bridge = io.open(os.path.join(INJECTOR, "bridge.py"), encoding="utf-8").read()
    assert "external_state=self.external_state" in bridge, "步级上下文钩子没接"
    assert "interaction_request=self.interaction_request" in bridge, "交互请求钩子没接"
    assert "def external_state(" in bridge and "def interaction_request(" in bridge
    # 世界事件:当前节点的事件要写成 mavis 的 story(而不是塞进角色记忆)
    assert re.search(r"\.story\s*=", bridge), "没把节点事件写成 story"


def test_story_events_come_from_released_nodes_only():
    """写进 story 的必须是"本节点已释放"的事件(而不是提前把未来事实塞进去)。"""
    bridge = io.open(os.path.join(INJECTOR, "bridge.py"), encoding="utf-8").read()
    m = re.search(r"self\.simulator\.story\s*=\s*\[(.*?)\]", bridge, re.S)
    assert m, "找不到 story 赋值"
    body = m.group(1)
    assert "node.events" in body, "story 不是从当前节点的事件来的: {}".format(body[:120])


def test_mavis_side_still_owns_the_injection_call():
    """钩子由 mavis 每步调用(set_step_context 只能在那里被调)—— 边界在两边都成立。"""
    sim = os.path.join(os.path.dirname(_REPO), "mavis", "mavisframework", "runtime", "simulator.py")
    if not os.path.isfile(sim):
        pytest.skip("mavis 仓不在预期位置")
    txt = io.open(sim, encoding="utf-8").read()
    assert "set_step_context" in txt and "_apply_injection_hooks" in txt
    assert "if self.external_state is not None" in txt, "钩子应当是「显式提供才调用」"
