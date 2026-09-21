# -*- coding: utf-8 -*-
"""让 maze.json(框架逻辑地图)的 collision 对齐场景 Collisions 图层。

背景:用户在 Tiled 里为"新场景"画了 Collisions 图层(tilemap.json 里的 "Collisions"),
但 maze.json 还是旧的,导致框架(读 maze.json 做 BFS 寻路/落点)以为很多墙格可通行,
把角色安排/寻路到真正的墙里 -> 前端穿墙。

策略(保守,只增不减):
    new_collision = maze.json 原有 collision  OR  场景 Collisions 图层非空格
即:绝不把原本阻挡的格改成可通行,只把新画的墙补齐。

终止条件(dry_run=True 只打印,不动文件):校验 agent 落点是否全部可通行。
"""
import argparse
import json
import os
import shutil

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAZE = os.path.join(BASE, "frontend", "static", "assets", "village", "maze.json")
TILEMAP = os.path.join(BASE, "frontend", "static", "assets", "village",
                       "tilemap", "tilemap.json")


def load(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写入 maze.json(否则只 dry-run)")
    args = ap.parse_args()

    maze = load(MAZE)
    tm = load(TILEMAP)

    col = next(l for l in tm["layers"] if l["name"] == "Collisions")
    cw, ch = col["width"], col["height"]
    data = col["data"]
    tiles = maze["tiles"]

    # 合规检查:坐标系必须一致(case00 新场景 27(宽)x24(高),maze size=[24,27]=高x宽)
    mh, mw = maze["size"]
    print("maze  size={}(高x宽) -> w={}, h={}".format(maze["size"], mw, mh))
    print("tilemap Collisions: {}x{}".format(cw, ch))
    assert (mw, mh) == (cw, ch), "maze 尺寸与 tilemap Collisions 不一致,停止(防坐标错位)"

    tm_wall = {
        (x, y) for y in range(ch) for x in range(cw) if data[y * cw + x]
    }
    mz_wall = {tuple(t["coord"]) for t in tiles if t.get("collision")}

    new_mz_wall = tm_wall | mz_wall
    added = sorted(new_mz_wall - mz_wall)
    kept = new_mz_wall & mz_wall

    print("maze 原 collision:{}  tilemap Collisions:{}  ->  对齐后:{}".format(
        len(mz_wall), len(tm_wall), len(new_mz_wall)))
    print("新增阻挡格 {} 个(原 maze 以为可通行):{}".format(
        len(added), added[:20]))

    # 前端初始落点(agent.json coord)是否会被框架用作出生点?一并检查是否压在"新墙"上
    agents_root = os.path.join(BASE, "frontend", "static", "assets", "village", "agents")
    print("\n=== agent.json 初始 coord 在新碰撞层上吗? ===")
    for n in sorted(os.listdir(agents_root)):
        p = os.path.join(agents_root, n, "agent.json")
        if not os.path.exists(p):
            continue
        a = json.load(open(p, encoding="utf-8"))
        c = tuple(a["coord"])
        on_wall = c in tm_wall
        print("  {:<12} coord={}  命中新墙?{}".format(n, list(c), on_wall))

    if not args.write:
        print("\n[dry-run] 未写文件。加 --write 才落盘。")
        return

    backup = MAZE + ".bak-{}".format(len(os.listdir(os.path.dirname(MAZE))))
    shutil.copy2(MAZE, backup)
    print("\n已备份 -> {}".format(os.path.relpath(backup, BASE)))

    modified = 0
    for t in tiles:
        c = tuple(t["coord"])
        want = c in new_mz_wall
        if bool(t.get("collision")) != want:
            t["collision"] = want
            modified += 1
    with open(MAZE, "w", encoding="utf-8") as f:
        json.dump(maze, f, ensure_ascii=False, indent=2)
    print("已更新 {} 个 tile 的 collision 标志。".format(modified))


if __name__ == "__main__":
    main()