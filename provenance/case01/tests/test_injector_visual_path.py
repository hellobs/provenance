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


def test_mavis_own_path_is_used_as_is():
    """mavis 自带路径(自然行走)照原样用,不重复算。"""
    maze = _FakeMaze()
    agents = {"Investment AI": _FakeAgent(maze), "Ethan Lin": _FakeAgent(maze)}
    out = []
    b = _bridge(agents, out)
    start = list(b._last_visual_coord["Ethan Lin"])
    own = [start, [start[0], max(0, start[1] - 1)]]
    b._emit_agent("Ethan Lin", {"coord": own[-1], "path": own}, "t")
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
    same = list(b._last_visual_coord["Ethan Lin"])          # 原地不动
    b._emit_agent("Ethan Lin", {"coord": same, "path": []}, "t")
    assert out[-1]["path"] == []
    assert not maze.calls


def test_step_snapshot_reports_the_picture_not_the_engine():
    """走不过去时,发给画面的快照必须是"人现在画在哪",不是引擎那个到不了的格子。

    2026-10-10 用户报"Investment AI 在推演结束时突然瞬移":`_emit_agent` 会把人留在
    原地(见 `test_unreachable_falls_back_but_logs`),而同一步的 `snapshot` 取自
    记录侧 `_agent_trace`(引擎的目标格)⇒ 前端 applySnapshot 直接把人钉过去,
    既截断走位又推翻"停在原地"。记录本身必须照旧记引擎位置。
    """
    maze = _FakeMaze(fail=True)
    agents = {"Investment AI": _FakeAgent(maze), "Ethan Lin": _FakeAgent(maze)}
    out = []
    b = _bridge(agents, out)
    held = list(b._last_visual_coord["Ethan Lin"])
    b._viz_on_agent("Ethan Lin", {"coord": [3, 3], "path": []}, 1, "t")
    b._viz_on_step({"step": 1, "time": "t"})

    snap = [e for e in out if e.get("type") == "snapshot"][-1]
    assert snap["agents"]["Ethan Lin"]["coord"] == held, "快照不许把人钉到走不到的格子"
    assert b._agent_trace[1]["Ethan Lin"]["coord"] == [3, 3], "记录侧要如实记引擎的目标格"


def test_real_maze_path_is_orthogonal(monkeypatch, tmp_path):
    """用真实场景的迷宫再验一次(不依赖假迷宫):两个角色初始格必须互相可达。

    ⚠ `MAVIS_CHECKPOINTS_ROOT` **必须指向 tmp**(第十轮 N9-7/第十一轮定因):
    `mavisframework/runtime/game.py:39-41` 在构造Game 时会
    `os.makedirs(os.path.join(checkpoints_root, name, "storage"))`,而
    `checkpoints_root` 默认是**相对当前工作目录**的 `results/checkpoints`。
    本测试不设它⇒ 在**生产路径** `provenance/results/checkpoints/` 留下一个空目录
    (实测目录名 `visual-path-test/storage`,0 个文件)。

    后果不只是"多了个空目录":`test_metric_semantics.py` 的 F 组判据是
    `os.path.isdir(CK_DIR)`,那个目录一旦存在,CI 上原本该打印的
    "results/checkpoints/ 未入库(gitignore)" 就变成
    "checkpoints 目录在但无 value_tendency 样本" —— **测试自己把自己的依据说明
    改成了错的**,而且那3 条正是红线 1 引用的性质断言。同文件另两处
    (`test_viz_plugin_wiring.py:69`、`test_injector_mavis_build.py:44`)早就设了,
    这处是漏的。
    """
    import pytest
    try:
        from mavisframework.config.loader import load_config
        from mavisframework.core.timer import Timer
        from mavisframework.runtime.game import Game
    except Exception:      # pragma: no cover - 引擎缺席时跳过
        pytest.skip("mavisframework 不可用")
    monkeypatch.setenv("MAVIS_CHECKPOINTS_ROOT", str(tmp_path / "checkpoints"))
    cfg = load_config(start_time="20260827-09:30", stride=0, agents=list(ROLES),
                      config_path=os.path.join(SCENARIO, "config.json"), assets_root="")
    game = Game("visual-path-test", SCENARIO, cfg, {}, timer=Timer("20260827-09:30"),
                governance=None, consequence_fn=None)
    # 坐标从场景读(改场景/改碰撞后自动跟着变);两者必须可达,否则开局就有人走不过去
    a = list(game.get_agent(ROLES[0]).coord)
    b = list(game.get_agent(ROLES[1]).coord)
    p = game.get_agent(ROLES[1]).maze.find_path(a, b)
    assert p, "真实迷宫里这两个格子必须可达(不可达说明场景里有人被墙围死)"
    pts = [list(c) for c in p]
    assert all(abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1]) == 1
               for i in range(len(pts) - 1)), pts
