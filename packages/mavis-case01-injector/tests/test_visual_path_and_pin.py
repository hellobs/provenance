# -*- coding: utf-8 -*-
"""可视路径"绕开别人"与"不把两人钉到同一格"的行为守卫(2026-10-10)。

为什么要有:两条都是用户当面指出、而代码里确实缺的:

1. `_visual_path` 原来是**不带 blocked 的裸 BFS**,于是后端算给前端的路径会一头
   撞进站着的人身上 —— 用户原话"如果走不到对应的位置因为有人挡住,BFS 这里换条路
   不就好了吗"。引擎自己的 `Agent.find_path` 早就把"别人站的格子"当临时障碍,
   这里要跟上同一口径(并保留"全被堵死就退回裸 BFS"的兜底)。
2. `_pin_for_interaction` 默认把两个角色一起 move 到 `roles[0]` 所在的格子,
   于是场景配置里的初始距离在第一个交互节点就被抹掉 —— 用户原话"最一开始他们两
   还是靠这么近"。查实 mavis 的对话前置条件(`Agent._chat_with_locked`)没有任何一条
   与"是否同一格"有关,所以同址从来不是必要条件。

本测试不连服务、不跑 LLM:用真 maze + 最小桩,只测这两段几何/编排逻辑。
"""
import io
import json
import os
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
SCENARIO = os.path.join(_REPO_ROOT, "provenance", "case01", "injector", "scenario")


def _load_maze():
    """真场景迷宫(27x24)。找不到就跳过 —— 不拿假地图冒充真障碍。"""
    path = os.path.join(SCENARIO, "maze.json")
    if not os.path.exists(path):
        pytest.skip("场景 maze.json 不存在:%s" % path)
    from mavisframework.scene.maze import Maze

    with io.open(path, encoding="utf-8") as f:
        return Maze(json.load(f))


def _bridge(maze, roles, last_visual):
    """只装配"这两个方法要用到"的最小 bridge 实例(不跑 Game/LLM)。"""
    from mavis_case01_injector.bridge import MavisBridge

    b = MavisBridge(nodes=[], roles=roles, scenario_dir=SCENARIO, dry_run=True)
    agent = types.SimpleNamespace(maze=maze)
    b.game = types.SimpleNamespace(
        agents={r: agent for r in roles},
        get_agent=lambda r, _a={r: agent for r in roles}: _a.get(r),
        logger=types.SimpleNamespace(warning=lambda *a: None))
    b._last_visual_coord = {k: list(v) for k, v in last_visual.items()}
    return b


def _straight_corridor(maze, need=6):
    """找一条同一行上、从 x 到 y 全部可站、且横向不短的走廊(测试的落脚点)。

    返回 (y, xs);找不到返回 None —— 不拿假地图冒充真障碍。
    """
    for y in range(maze.maze_height):
        run = [x for x in range(maze.maze_width) if not maze.tile_at((x, y)).collision]
        if len(run) < need:
            continue
        xs = [x for x in run if 1 < x < maze.maze_width - 2]
        if len(xs) < need:
            continue
        if all(not maze.tile_at((x, y)).collision for x in range(xs[0], xs[-1] + 1)):
            return y, xs
    return None


def test_visual_path_avoids_the_other_agent():
    """两人同在一行、中间隔几格:走向对方时必须绕开,不能笔直顶上去。"""
    maze = _load_maze()
    found = _straight_corridor(maze)
    if not found:
        pytest.skip("地图里找不到一条足够长的直线走廊")
    y, xs = found
    src, blocker, dst = xs[0], xs[2], xs[5]

    b = _bridge(maze, ["A", "B"], {"A": [src, y], "B": [blocker, y]})
    path = b._visual_path("A", [dst, y])
    assert path, "应当算出一条路"
    # ⚠ 必须转成 tuple 再比:`path` 里每格是 list,`[4,4] in [[2,4],...]` 恒为 False,
    # 写成 list 比较等于**断言没写**、测试永远绿(2026-10-10 实踩)。
    cells = [tuple(c) for c in path]
    assert (blocker, y) not in cells, \
        "路径不该经过对方站的格子 %s,实得 %s" % ([blocker, y], path)
    assert cells[-1] == (dst, y), "终点应当仍是目标格"


def test_visual_path_falls_back_when_fully_blocked():
    """整条走廊全被对方占住 ⇒ 绕不开;此时退回裸 BFS,不能"卡住不动"。"""
    maze = _load_maze()
    found = _straight_corridor(maze)
    if not found:
        pytest.skip("地图里找不到一条足够长的直线走廊")
    y, xs = found
    src, dst = xs[0], xs[-1]
    # 把整条走廊(除首尾)都算成"别人站着的格子" ⇒ 带 blocked 的 BFS 必然算不出路
    others = {"B%d" % x: [x, y] for x in xs[1:-1]}
    others["B"] = [src, y - 1] if not maze.tile_at((src, y - 1)).collision else [src, y + 1]
    b = _bridge(maze, ["A"] + list(others), dict({"A": [src, y]}, **others))
    path = b._visual_path("A", [dst, y])
    assert path and [tuple(c) for c in path][-1] == (dst, y), \
        "被堵死时应退回裸 BFS 直达目标,实得 %s" % (path,)


def test_visual_path_gives_up_when_really_unreachable():
    """目标格在墙上 ⇒ BFS 无解 ⇒ 返回 None 让角色**停下**,且不更新"当前所在格"。

    2026-10-10 用户原话:"要是实在过不去,BFS 都给出无解了那就应该要停下!"
    此前这条兜底是反的:返回 None 后前端退化成"沿直线滑到目标格"(穿墙),
    而且起点已经被提前记成目标格,后面每一步都从错的地方起算。
    """
    maze = _load_maze()
    found = _straight_corridor(maze)
    if not found:
        pytest.skip("地图里找不到一条足够长的直线走廊")
    y, xs = found
    src = xs[0]
    wall = None
    for x in range(maze.maze_width):
        if maze.tile_at((x, y)).collision:
            wall = x
            break
    if wall is None:
        pytest.skip("这一行全是可站格,没有墙可作不可达目标")

    b = _bridge(maze, ["A"], {"A": [src, y]})
    before = dict(b._last_visual_coord)
    assert b._visual_path("A", [wall, y]) is None, "目标是墙时应当报告无解"
    assert b._last_visual_coord == before, \
        "无解时不该把'当前所在格'记成走不到的目标,实得 %s" % (b._last_visual_coord,)


def test_emit_agent_holds_position_when_no_route():
    """_emit_agent 在无解时把 coord 换成"停住的那一格",前端因此一步都不会走。"""
    maze = _load_maze()
    found = _straight_corridor(maze)
    if not found:
        pytest.skip("地图里找不到一条足够长的直线走廊")
    y, xs = found
    src = xs[0]
    wall = next((x for x in range(maze.maze_width) if maze.tile_at((x, y)).collision), None)
    if wall is None:
        pytest.skip("这一行全是可站格,没有墙可作不可达目标")

    sent = []
    b = _bridge(maze, ["A"], {"A": [src, y]})
    b._plugin_mode = True
    b._fanout = types.SimpleNamespace(emit=lambda ev: sent.append(ev))
    b._emit_agent("A", {"coord": [wall, y], "action": "", "location": "", "currently": ""},
                  "2026-08-27-09:30")
    assert sent, "应当仍发出一条 agent 事件"
    ev = sent[0]
    assert list(ev["coord"]) == [src, y], \
        "无解时事件里的 coord 应是停住的那一格,实得 %s" % (ev["coord"],)
    assert not (ev.get("path") or []), "无解时不该给出路径,实得 %s" % (ev.get("path"),)


def test_pin_keeps_roles_where_they_are():
    """默认(未显式给 meeting_coord)时,强制交互**不得**把两人挪到同一格。"""
    maze = _load_maze()
    roles = ["Investment AI", "Ethan Lin"]
    own = {"Investment AI": [10, 6], "Ethan Lin": [10, 9]}
    b = _bridge(maze, roles, own)
    b.config = {"agents": {r: {"coord": list(own[r]), "path": []} for r in roles}}
    moved = []
    for r in roles:
        agent = b.game.agents[r]
        agent.coord = tuple(own[r])
        agent.path = [1]
        agent.schedule = types.SimpleNamespace(daily_schedule=[1])
        agent.move = lambda c, p, _r=r: moved.append((_r, list(c)))
    b._pin_for_interaction(types.SimpleNamespace())
    assert moved == [], "默认不该再把角色 move 到同一个格子,实得 move 记录 %s" % (moved,)
    assert b.config["agents"]["Investment AI"]["coord"] == [10, 6]
    assert b.config["agents"]["Ethan Lin"]["coord"] == [10, 9], \
        "两人的初始距离不该被交互节点抹掉"
    for r in roles:
        assert b.game.agents[r].path == [], "%s 的运行时路径应被清空(对话前置条件)" % r
