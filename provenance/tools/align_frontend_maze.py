# -*- coding: utf-8 -*-
"""体检收尾:补齐前端 maze.json 缺席的 tilemap 墙格(追加 collision:true 的墙 tile)。

fix_maze_collision.py 只改"已有 tile 的 collision 标志",对缺席 tile 无效(前端 maze
只有 360 个 tile,而 tilemap 有 132 个墙格完全没有 tile 条目 -> 缺席即被当可通行)。
本脚本把这些缺席墙格补成 {coord, collision:true} 的无地址墙 tile,使前端 maze 的
碰撞上界严格 ⊇ tilemap 墙格(不破坏原地址树/活区映射)。
"""
import json, os, shutil

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAZE = os.path.join(BASE, "frontend", "static", "assets", "village", "maze.json")
TILEMAP = os.path.join(BASE, "frontend", "static", "assets", "village",
                       "tilemap", "tilemap.json")

def load(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

mz = load(MAZE)
tm = load(TILEMAP)
col = next(l for l in tm["layers"] if l["name"] == "Collisions")
cw, ch = col["width"], col["height"]
data = col["data"]

mh, mw = mz["size"]
assert (mw, mh) == (cw, ch), "maze 尺寸与 tilemap 不一致,停止"

tm_wall = {(x, y) for y in range(ch) for x in range(cw) if data[y * cw + x]}
have = {tuple(t["coord"]) for t in mz["tiles"]}
missing = sorted(tm_wall - have)

if not missing:
    print("无需补齐:前端 maze 已覆盖全部 %d 个 tilemap 墙格" % len(tm_wall))
else:
    backup = MAZE + ".bak-align"
    shutil.copy2(MAZE, backup)
    print("已备份 -> %s" % os.path.relpath(backup, BASE))
    for coord in missing:
        mz["tiles"].append({"coord": list(coord), "collision": True})
    with open(MAZE, "w", encoding="utf-8") as f:
        json.dump(mz, f, ensure_ascii=False, indent=2)
    print("已补齐 %d 个缺席墙格, tiles 总数 %d -> %d" % (
        len(missing), len(have), len(mz["tiles"])))

    # 复核
    mz2 = load(MAZE)
    wall = {tuple(t["coord"]) for t in mz2["tiles"] if t.get("collision")}
    print("复核:前端 maze collision 墙格=%d, 对 tilemap 缺=%d 多=%d" % (
        len(wall), len(tm_wall - wall), len(wall - tm_wall)))