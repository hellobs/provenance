# -*- coding: utf-8 -*-
"""体检:当前各场景的碰撞(collision)到底有没有、对不对。

问的是"现在场景是否有 collision",所以按**实际在用的那几份 maze.json**逐个数,
而不是看代码里有没有 collision 这个词:
- 总瓦片 / 有碰撞 / 可走;
- maze.json 里出现但**没有 collision 字段**的瓦片(引擎按"无碰撞"处理,是隐性放行);
- 可走区连通分量(多于 1 个 = 有走不到的死区);
- 每个角色出生点是否落在可走格(落在墙里 = 一开局就卡住/穿墙)。

maze.json 的 tiles 是**列表**(不是字典),这里两种形态都兼容。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MAZES = [
    ("case00 场景目录", os.path.join(ROOT, "case00", "scenario", "maze.json")),
    ("case01 注入器场景", os.path.join(ROOT, "case01", "injector", "scenario", "maze.json")),
    ("前端静态(装好的 scene)", os.path.join(
        ROOT, "frontend", "static", "assets", "village", "maze.json")),
]
SCENARIOS = [
    ("case00 场景", os.path.join(ROOT, "case00", "scenario")),
    ("case01 注入器场景", os.path.join(ROOT, "case01", "injector", "scenario")),
]


def load(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def tile_items(m):
    """统一成 [(coord_tuple, tile_dict)]。"""
    t = m.get("tiles")
    out = []
    if isinstance(t, list):
        for x in t:
            c = x.get("coord")
            if isinstance(c, list) and len(c) >= 2:
                out.append(((int(c[0]), int(c[1])), x))
    elif isinstance(t, dict):
        for k, x in t.items():
            if "," in k:
                a, b = k.split(",")[:2]
                out.append(((int(a), int(b)), x))
            elif isinstance(x.get("coord"), list):
                out.append(((int(x["coord"][0]), int(x["coord"][1])), x))
    return out


def walkables(items):
    return {c for c, x in items if not x.get("collision")}


def comps(walk):
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


def coords_of(scen_dir):
    out = {}
    adir = os.path.join(scen_dir, "agents")
    if not os.path.isdir(adir):
        return out
    for name in sorted(os.listdir(adir)):
        p = os.path.join(adir, name, "agent.json")
        if os.path.isfile(p):
            try:
                c = load(p).get("coord")
                if isinstance(c, list) and len(c) >= 2:
                    out[name] = (int(c[0]), int(c[1]))
            except Exception as e:  # noqa: BLE001
                out[name] = "读失败: {}".format(e)
    return out


print("说明:collision=true 表示**不可走**;缺该字段的瓦片引擎按可走处理。\n")
for label, p in MAZES:
    if not os.path.isfile(p):
        print("== {}: 文件不存在".format(label))
        continue
    m = load(p)
    items = tile_items(m)
    n = len(items)
    blocked = sum(1 for _c, x in items if x.get("collision"))
    missing = sum(1 for _c, x in items if "collision" not in x)
    walk = walkables(items)
    size = m.get("size") or m.get("map") or {}
    print("== {}  ({})".format(label, os.path.relpath(p, ROOT)))
    print("   瓦片 {} · 不可走 {} · 可走 {} · 未标 collision {}"
          .format(n, blocked, len(walk), missing))
    if isinstance(size, dict):
        print("   地图 {}x{} · 瓦片尺寸 {}".format(
            size.get("width") or size.get("cols"), size.get("height") or size.get("rows"),
            m.get("tile_size")))
    if n == 0:
        print("   ⚠ 没有瓦片 —— 这份 maze 没有碰撞数据")
    else:
        sizes = comps(walk)
        print("   可走区连通分量 {} 个: {}".format(
            len(sizes), sizes[:6] + (["…"] if len(sizes) > 6 else [])))
        if len(sizes) > 1:
            print("   ⚠ 有 {} 个分离区域(最小 {} 格):存在走不到的死区"
                  .format(len(sizes), sizes[-1]))
        if missing == n:
            print("   ⚠ **整份 maze 一个 collision 字段都没有** → 相当于没有碰撞,角色会穿墙")
        elif missing:
            print("   · {} 格未标 collision(按可走处理,多为非瓦片装饰)".format(missing))

for label, d in SCENARIOS:
    cs = coords_of(d)
    mp = os.path.join(d, "maze.json")
    if not cs or not os.path.isfile(mp):
        continue
    tiles = dict(tile_items(load(mp)))
    print("== {} 的角色出生点".format(label))
    for name, c in sorted(cs.items()):
        if isinstance(c, str):
            print("   {:<14} {}".format(name, c))
            continue
        t = tiles.get(c)
        if t is None:
            print("   {:<14} {} → ⚠ 该格不在 maze 里".format(name, c))
        elif t.get("collision"):
            print("   {:<14} {} → ⚠ 出生点在**墙**里".format(name, c))
        else:
            print("   {:<14} {} → 可走 ✓".format(name, c))
