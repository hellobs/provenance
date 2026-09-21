# -*- coding: utf-8 -*-
"""场景连通性守卫(2026-09-21 深度体检新增)。

碰撞本身在(每份场景 maze 有 244 不可走格),但**光有碰撞不够**:碰撞把地图切成几块时,
落在小碎块里的角色就走不到别人那儿 —— 表现是"这个角色一直原地打转",而日志里什么错都没有。
本文件把三件事钉死:

1. 两个在跑的场景(case00 / case01)的 maze:可走区**只有连通的一块**,且每个角色出生点都可走;
2. 装好的静态场景(`frontend/static/assets/village/maze.json`)目前是碎的(9 块),所以
   **不允许运行时模块引用它**(只允许存档/体检工具引用)—— 谁把它接进实时链路就红,
   因为那会让三个角色落进小碎块;
3. 每份 maze 的碰撞字段必须存在且为布尔(缺字段=引擎按可走处理,是隐性放行)。
"""
import io
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "provenance")
for p in (_PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

SCENES = {
    "case00": (os.path.join(_PKG, "case00", "scenario", "maze.json"),
               os.path.join(_PKG, "case00", "scenario", "agents")),
    "case01": (os.path.join(_PKG, "case01", "injector", "scenario", "maze.json"),
               os.path.join(_PKG, "case01", "injector", "scenario", "agents")),
}
STATIC_SCENE = os.path.join(_PKG, "frontend", "static", "assets", "village", "maze.json")


def _tiles(path):
    m = json.load(io.open(path, encoding="utf-8"))
    out = {}
    for x in m.get("tiles") or []:
        c = x.get("coord")
        if isinstance(c, list) and len(c) >= 2:
            out[(int(c[0]), int(c[1]))] = x
    return m, out


def _components(walk):
    seen, sizes = set(), []
    for s in walk:
        if s in seen:
            continue
        stack, n = [s], 0
        seen.add(s)
        while stack:
            x, y = stack.pop()
            n += 1
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb in walk and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        sizes.append(n)
    return sorted(sizes, reverse=True)


def _spawn_coords(agents_dir):
    out = {}
    for name in sorted(os.listdir(agents_dir)):
        p = os.path.join(agents_dir, name, "agent.json")
        if os.path.isfile(p):
            c = json.load(io.open(p, encoding="utf-8")).get("coord")
            if isinstance(c, list) and len(c) >= 2:
                out[name] = (int(c[0]), int(c[1]))
    return out


@pytest.mark.parametrize("case", sorted(SCENES))
def test_active_scene_is_fully_connected(case):
    """在跑的场景:可走区必须是一块(否则角色走不到彼此)。"""
    maze, tiles = _tiles(SCENES[case][0])
    walk = {c for c, x in tiles.items() if not x.get("collision")}
    sizes = _components(walk)
    assert sizes, "{}: 没有任何可走格".format(case)
    assert len(sizes) == 1, \
        "{}: 可走区裂成 {} 块 {} —— 落在小块的角会走不到别人".format(case, len(sizes), sizes[:6])


@pytest.mark.parametrize("case", sorted(SCENES))
def test_every_spawn_is_walkable(case):
    _maze, tiles = _tiles(SCENES[case][0])
    walk = {c for c, x in tiles.items() if not x.get("collision")}
    bad = {n: c for n, c in _spawn_coords(SCENES[case][1]).items() if c not in walk}
    assert not bad, "{}: 出生点不在可走格(开局就卡住/穿墙): {}".format(case, bad)


@pytest.mark.parametrize("case", sorted(SCENES))
def test_collision_semantics_are_unambiguous(case):
    """碰撞语义必须无歧义。

    实测约定:**不可走**的格子显式写 `"collision": true`;**可走**格省略该字段
    (case00/case01 各 379 个省略)。引擎按"缺字段=可走"处理 —— 这是允许的,
    但不允许含糊值,也不允许"用省略表达不可走"。
    """
    _maze, tiles = _tiles(SCENES[case][0])
    bad_types = {c: x.get("collision") for c, x in tiles.items()
                 if "collision" in x and not isinstance(x.get("collision"), bool)}
    assert not bad_types, "{}: collision 不是布尔(含糊值): {}".format(
        case, list(bad_types.items())[:3])

    blocked = {c for c, x in tiles.items() if x.get("collision") is True}
    implicit = {c for c, x in tiles.items() if "collision" not in x}
    walk = {c for c, x in tiles.items() if not x.get("collision")}
    assert blocked, "{}: 一个不可走格都没有 —— 碰撞没进场景?".format(case)
    assert walk == implicit | {c for c, x in tiles.items() if x.get("collision") is False}, \
        "{}: 可走格集合与'缺字段/显式 false'对不上,存在既没写又不可走的黑洞".format(case)
    assert len(walk) + len(blocked) == len(tiles), \
        "{}: 可走+不可走 != 总瓦片".format(case)


def test_static_scene_is_not_wired_into_runtime():
    """装好的静态场景目前是碎的(9 块),因此**只允许**存档/体检工具引用它。

    这不是"容忍脏数据",而是把"它不在实时链路上"变成可检查的事实:
    哪天有人把 `assets/village/maze.json` 接进运行时,三个角色的出生点会落在小碎块里,
    表现是"一直原地打转、日志无异常"—— 那种 bug 最难查,所以在这里拦住。
    """
    if not os.path.isfile(STATIC_SCENE):
        pytest.skip("静态场景不存在")
    _maze, tiles = _tiles(STATIC_SCENE)
    walk = {c for c, x in tiles.items() if not x.get("collision")}
    sizes = _components(walk)

    allow = ("hc_data_integrity.py", "check_installed_scene.py", "check_collision.py",
             "install_scene.py", "test_scene_connectivity.py")
    offenders = []
    for dirpath, dirs, files in os.walk(_PKG):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache",
                                                "runs", "results", "runs_html", "node_modules")]
        for fn in files:
            if not fn.endswith((".py", ".js", ".html")):
                continue
            if fn in allow:
                continue
            p = os.path.join(dirpath, fn)
            try:
                txt = io.open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if "assets/village/maze.json" in txt or "assets\\\\village\\\\maze.json" in txt:
                offenders.append(os.path.relpath(p, _PKG).replace("\\", "/"))
    assert not offenders, (
        "这些运行时文件引用了碎片化的静态场景({} 块 {}): {} —— "
        "接进链路前先把连通性修好".format(len(sizes), sizes[:5], offenders))
