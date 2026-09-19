# -*- coding: utf-8 -*-
"""打印迷宫里哪些格子可走 —— 给"布景/摆角色"挑站位用。

为什么需要它:把角色摆到 collidable 格上,前端要么不动、要么直线穿墙
(2026-09-19 实测:脚本里点名去左房两排,截图里人还是散的,就是因为挑的格子不可走)。

用法(在 provenance/provenance 下):

    python -m case01.tools.maze_walkable case00/scenario/maze.json
    python -m case01.tools.maze_walkable case01/injector/scenario/maze.json --x 4 18 --y 4 22

输出是"每行一个 y、每列一个 x"的格子图:`.` 可走,`#` 走不了。
"""
import argparse
import json
import sys


def free_lookup(maze):
    """→ free(x, y):该格能不能站/走。没有显式 tile 的格子算空地。"""
    grid = {}
    for t in maze.get("tiles") or []:
        c = t.get("coord")
        if c:
            grid[(int(c[0]), int(c[1]))] = t
    blocked_keys = ("collision", "collide", "blocked", "block", "obstacle")

    def free(x, y):
        t = grid.get((x, y))
        if t is None:
            return True
        return not any(t.get(k) for k in blocked_keys)

    return free


def main(argv=None):
    ap = argparse.ArgumentParser(description="打印迷宫可走格子图(布景挑站位用)")
    ap.add_argument("maze", help="maze.json 路径")
    ap.add_argument("--x", nargs=2, type=int, default=[0, 0], help="x 范围(默认全域)")
    ap.add_argument("--y", nargs=2, type=int, default=[0, 0], help="y 范围(默认全域)")
    args = ap.parse_args(argv)

    with open(args.maze, encoding="utf-8") as f:
        maze = json.load(f)
    w, h = maze.get("size") or [0, 0]
    x0, x1 = (args.x[0], args.x[1] + 1) if args.x[1] else (0, w)
    y0, y1 = (args.y[0], args.y[1] + 1) if args.y[1] else (0, h)
    free = free_lookup(maze)
    print("{}  size={} tile_size={}   (x={}..{}, y={}..{})".format(
        args.maze, [w, h], maze.get("tile_size"), x0, x1 - 1, y0, y1 - 1))
    ruler = "".join(str(x % 10) for x in range(x0, x1))
    print("      " + ruler)
    for y in range(y0, y1):
        print("  y=%2d  %s" % (y, "".join("." if free(x, y) else "#" for x in range(x0, x1))))
    print("      " + ruler)
    print("  . = 可走   # = 挡住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
