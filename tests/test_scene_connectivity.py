# -*- coding: utf-8 -*-
"""场景连通性守卫(2026-09-21 深度体检新增;2026-10-03 静态场景已对齐)。

碰撞本身在(每份场景 maze 有 244 不可走格),但**光有碰撞不够**:碰撞把地图切成几块时,
落在小碎块里的角色就走不到别人那儿 —— 表现是"这个角色一直原地打转",而日志里什么错都没有。
本文件把四件事钉死:

1. 两个在跑的场景(case00 / case01)的 maze:可走区**只有连通的一块**,且每个角色出生点都可走;
2. 装好的静态场景(`frontend/static/assets/village/maze.json`)必须与在跑场景**字节一致**
   (2026-10-03 前它是旧版 492 格/350 碰撞/9 碎块,与权威 244 墙分叉;已同步并钉死);
3. 静态场景仍**不允许运行时模块引用它**(只允许存档/体检工具引用)—— 它是样板存档,
   实时链路只读 tilemap.json 与各 scenario/maze.json,多一条加载路径就多一份分叉风险;
4. 每份 maze 的碰撞字段必须存在且为布尔(缺字段=引擎按可走处理,是隐性放行)。
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


def test_static_scene_is_connected_and_matches_active_scene():
    """静态样板必须与在跑场景**字节一致**且自身连通(2026-10-03 起钉死)。

    历史:它曾是旧版(492 格/350 碰撞/9 碎块),与 tilemap 权威 244 墙分叉,
    没人运行时读它所以一直没爆;deep_check 的连通性体检把它照了出来。
    同步后用**字节一致**钉死 —— 语义比对会放过"多一格少一格"的缓慢漂移。
    """
    if not os.path.isfile(STATIC_SCENE):
        pytest.skip("静态场景不存在")
    static_bytes = io.open(STATIC_SCENE, "rb").read()
    ref_bytes = io.open(SCENES["case00"][0], "rb").read()
    assert static_bytes == ref_bytes, (
        "frontend/static/assets/village/maze.json 与 case00/scenario/maze.json 分叉了; "
        "样板必须与在跑场景保持一致(2026-10-03 前曾分叉成 492 格/9 碎块)。"
        "同步后若改地图,三处(case00/case01/static)必须一起换")
    # 双保险:就算哪天三份被一起换成新地图,新地图本身也必须连通。
    _maze, tiles = _tiles(STATIC_SCENE)
    walk = {c for c, x in tiles.items() if not x.get("collision")}
    sizes = _components(walk)
    assert len(sizes) == 1, "静态样板可走区裂成 {} 块 {}".format(len(sizes), sizes[:6])


def test_static_scene_is_not_wired_into_runtime():
    """静态样板**只允许**存档/体检工具引用,运行时不得加载它。

    2026-10-03 后它已与在跑场景一致、不再是碎块,这条守卫的意义随之变化:
    不是"碎了不能接",而是**实时链路只能有一条地图加载路径**(tilemap.json 渲染 +
    各 scenario/maze.json 寻路)。谁再 `fetch`/读取 static 副本,就多出一条会与权威
    分叉的路径 —— 那种分叉的典型表现仍是"角色原地打转、日志无异常",所以继续拦住。
    """
    if not os.path.isfile(STATIC_SCENE):
        pytest.skip("静态场景不存在")

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
        "这些运行时文件引用了静态样板场景: {} —— "
        "实时链路只允许读 tilemap.json 与各 scenario/maze.json,"
        "多一条加载路径就会再次分叉".format(offenders))
