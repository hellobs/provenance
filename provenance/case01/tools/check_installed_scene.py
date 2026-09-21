# -*- coding: utf-8 -*-
"""补充体检:那份"装好的 scene"(frontend/static/assets/village/maze.json)裂成 9 块,
到底谁在用、角色出生点各落在哪一块 —— 落进小块 = 那个角色走不到别人。

同时回答"现在跑的服务用的是哪份 maze":按代码里的引用逐条列出来。
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def items(m):
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
    return out


def comp_map(walk):
    """coord -> 分量编号(按大小降序编号)。"""
    seen, groups = set(), []
    for s in walk:
        if s in seen:
            continue
        stack, cells = [s], []
        seen.add(s)
        while stack:
            x, y = stack.pop()
            cells.append((x, y))
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb in walk and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        groups.append(cells)
    groups.sort(key=len, reverse=True)
    out = {}
    for i, cells in enumerate(groups):
        for c in cells:
            out[c] = i
    return out, [len(g) for g in groups]


print("== 谁在引用哪份 maze(代码里的实际引用点)")
for dirpath, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in (".git", ".venv-live", ".venv", "__pycache__",
                                            "node_modules", ".uv-cache", "build", "_pages")]
    for fn in files:
        if not fn.endswith((".py", ".js", ".html", ".json")):
            continue
        p = os.path.join(dirpath, fn)
        if os.path.getsize(p) > 900_000:
            continue
        try:
            txt = io.open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in re.finditer(r"[^\s\"']*maze\.json", txt):
            ref = m.group(0)
            if ref.startswith(("assets/", "frontend/", "/static", "../")):
                print("   {:<52} {}".format(os.path.relpath(p, ROOT), ref))

inst = os.path.join(ROOT, "frontend", "static", "assets", "village", "maze.json")
m = load(inst)
its = items(m)
walk = {c for c, x in its if not x.get("collision")}
cmap, sizes = comp_map(walk)
print("\n== 装好的 scene({})".format(os.path.relpath(inst, ROOT)))
print("   可走 {} 格,分成 {} 块,大小 {}".format(len(walk), len(sizes), sizes))

for label, scen in (("case00", os.path.join(ROOT, "case00", "scenario")),
                    ("case01", os.path.join(ROOT, "case01", "injector", "scenario"))):
    adir = os.path.join(scen, "agents")
    if not os.path.isdir(adir):
        continue
    print("   {} 角色出生点落块:".format(label))
    for name in sorted(os.listdir(adir)):
        p = os.path.join(adir, name, "agent.json")
        if not os.path.isfile(p):
            continue
        c = load(p).get("coord")
        if not (isinstance(c, list) and len(c) >= 2):
            continue
        c = (int(c[0]), int(c[1]))
        if c in cmap:
            print("     {:<14} {} → 第 {} 块(共 {} 格){}".format(
                name, c, cmap[c] + 1, sizes[cmap[c]], "" if cmap[c] == 0 else "  ⚠ 不在主区域"))
        else:
            print("     {:<14} {} → ⚠ 该格是墙/不在图里".format(name, c))
