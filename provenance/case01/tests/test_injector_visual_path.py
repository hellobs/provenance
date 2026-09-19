# -*- coding: utf-8 -*-
"""可视路径测试:pin 之后 mavis 侧 path 为空,前端只能沿直线走 → 斜穿格子。

2026-09-19 用户实测反馈"有的AI走斜线,而不是横着竖着走的"。
修法是 bridge 自己用迷宫补一条**正交可视路径**(只影响画面,不改 mavis 语义:
交互仍按"同址 + 空路径"判定)。
"""
import os

from case01.injector.bridge import MavisBridge

SCENARIO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "injector", "scenario")
ROLES = ("Investment AI", "Ethan Lin")


class _FakeMaze:
    """正交路径的假迷宫:先横后竖,和真 BFS 一样不斜穿。"""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def find_path(self, src, dst):
        self.calls.append((list(src), list(dst)))
        if self.fail:
            return []
        src, dst = list(src), list(dst)
        path = [src]
        cur = list(src)
        while cur[0] != dst[0]:
            cur[0] += 1 if dst[0] > cur[0] else -1
            path.append(list(cur))
        while cur[1] != dst[1]:
            cur[1] += 1 if dst[1] > cur[1] else -1
            path.append(list(cur))
        return path


class _FakeAgent:
    def __init__(self, maze, role_type="user"):
        self.maze = maze
        self.role_type = role_type


class _FakeGame:
    def __init__(self, agents):
        self.agents = agents
        self.logger = _Logger()


class _Logger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *a, **kw):
        self.warnings.append(str(msg))

    def info(self, *a, **kw):
        pass


def _bridge(agents, emitted):
    b = MavisBridge(nodes=[], roles=ROLES, scenario_dir=SCENARIO, dry_run=True)
    b.game = _FakeGame(agents)
    b._fanout = _Fanout(emitted)
    return b


class _Fanout:
    def __init__(self, sink):
        self.sink = sink

    def emit(self, event):
        self.sink.append(event)


def test_pinned_move_gets_an_orthogonal_visual_path():
    maze = _FakeMaze()
    agents = {"Investment AI": _FakeAgent(maze), "Ethan Lin": _FakeAgent(maze)}
    out = []
    b = _bridge(agents, out)
    # 起点 = 场景初始坐标(与前端摆的位置同源):Ethan Lin 在 [9,7]
    assert b._last_visual_coord["Ethan Lin"] == [9, 7], "初始坐标要从场景里播种"
    # mavis 侧 pin 之后 path 为空(交互要求"同址且静止")
    b._emit_agent("Ethan Lin", {"coord": [10, 6], "path": [], "action": "walk"}, "t")
    ev = out[-1]
    assert ev["path"], "没补可视路径 → 前端会斜着滑过去"
    pts = [list(p) for p in ev["path"]]
    assert pts[0] == [9, 7] and pts[-1] == [10, 6]
    assert all(abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1]) == 1
               for i in range(len(pts) - 1)), "每步必须是上下左右,不能斜穿"
    assert maze.calls == [([9, 7], [10, 6])]


def test_mavis_own_path_is_used_as_is():
    """mavis 自带路径(自然行走)照原样用,不重复算。"""
    maze = _FakeMaze()
    agents = {"Investment AI": _FakeAgent(maze), "Ethan Lin": _FakeAgent(maze)}
    out = []
    b = _bridge(agents, out)
    own = [[9, 7], [9, 6]]
    b._emit_agent("Ethan Lin", {"coord": [9, 6], "path": own}, "t")
    assert out[-1]["path"] == own
    assert not maze.calls, "有 mavis 路径时不该再问迷宫"


def test_unreachable_falls_back_but_logs():
    """寻不到路时回退直线,但必须留一行警告(不许静默)。"""
    maze = _FakeMaze(fail=True)
    agents = {"Investment AI": _FakeAgent(maze), "Ethan Lin": _FakeAgent(maze)}
    out = []
    b = _bridge(agents, out)
    b._emit_agent("Ethan Lin", {"coord": [3, 3], "path": []}, "t")
    assert out[-1]["path"] == [], "没有路径就只能是空(前端回退直线)"
    assert b.game.logger.warnings, "回退时必须留痕"


def test_same_coord_needs_no_path():
    maze = _FakeMaze()
    agents = {"Investment AI": _FakeAgent(maze), "Ethan Lin": _FakeAgent(maze)}
    out = []
    b = _bridge(agents, out)
    b._emit_agent("Ethan Lin", {"coord": [9, 7], "path": []}, "t")   # 原地不动
    assert out[-1]["path"] == []
    assert not maze.calls


def test_real_maze_path_is_orthogonal():
    """用真实场景的迷宫再验一次(不依赖假迷宫):两个角色初始格是斜相邻的。"""
    import pytest
    try:
        from mavisframework.config.loader import load_config
        from mavisframework.core.timer import Timer
        from mavisframework.runtime.game import Game
    except Exception:      # pragma: no cover - 引擎缺席时跳过
        pytest.skip("mavisframework 不可用")
    cfg = load_config(start_time="20260827-09:30", stride=0, agents=list(ROLES),
                      config_path=os.path.join(SCENARIO, "config.json"), assets_root="")
    game = Game("visual-path-test", SCENARIO, cfg, {}, timer=Timer("20260827-09:30"),
                governance=None, consequence_fn=None)
    p = game.get_agent("Ethan Lin").maze.find_path([9, 7], [10, 6])
    assert p, "真实迷宫里这两个格子必须可达"
    pts = [list(c) for c in p]
    assert all(abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1]) == 1
               for i in range(len(pts) - 1)), pts
