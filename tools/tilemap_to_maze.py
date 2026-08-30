#!/usr/bin/env python3
"""tilemap_to_maze.py — 无约束的 Tiled 地图 → provenance maze.json 转换工具

与 tiled_to_maze.py(旧工具, GUI + 旧项目目录假设)不同:
- 纯 CLI,零 GUI 依赖,零目录结构假设
- 输入 = Tiled 导出的任意 JSON 地图文件(命令行指定)
- 输出 = provenance 引擎可直接消费的 maze.json
- 语义标注复用 Tiled 图层机制:
    Sector Blocks 图层(不同 gid = 不同区域)
    Arena Blocks 图层(不同 gid = 不同场所)
    Object Interaction Blocks 图层(不同 gid = 不同对象)
  gid → 名称的映射由 --map 参数指定的 JSON 文件提供(或 --auto 生成待确认表)

用法:
    python tilemap_to_maze.py input.json -o maze.json \
        --sector-layers "Sector Blocks" --arena-layers "Arena Blocks" \
        --object-layers "Object Interaction Blocks" --world "the Ville"
    # 首次运行先生成 gid 映射表:
    python tilemap_to_maze.py input.json --auto-gid-map -o gid_map.json
    # 编辑 gid_map.json 填好名称后:
    python tilemap_to_maze.py input.json -o maze.json --gid-map gid_map.json
"""
import argparse
import json
import os
import sys
from collections import defaultdict


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def collect_layer_gids(tilemap, layer_names):
    """收集指定图层的所有非零 gid 及其 tile 坐标"""
    gid_tiles = defaultdict(list)  # gid -> [(x, y), ...]
    for layer in tilemap.get("layers", []):
        if layer.get("type") != "tilelayer":
            continue
        if layer.get("name") not in layer_names:
            continue
        data = layer.get("data", [])
        w = layer.get("width", 0)
        for idx, gid in enumerate(data):
            if not gid:
                continue
            gid_tiles[gid].append((idx % w, idx // w))
    return gid_tiles


def collect_object_rects(tilemap, layer_names):
    """收集对象层中的矩形对象: 返回 [{name, x, y, width, height}, ...]
    对象名 = 房间/场所名(如 "Trading Floor"),矩形覆盖区域即该地址的格子。
    """
    rects = []
    for layer in tilemap.get("layers", []):
        if layer.get("type") != "objectgroup":
            continue
        if layer.get("name") not in layer_names:
            continue
        for obj in layer.get("objects", []):
            name = obj.get("name", "").strip()
            if not name:
                continue
            x, y = obj.get("x", 0), obj.get("y", 0)
            w, h = obj.get("width", 0), obj.get("height", 0)
            if w <= 0 or h <= 0:
                continue
            # 对象坐标是像素,Tiled 矩形含边框,换算到 tile 格
            rects.append({"name": name, "x": x, "y": y,
                          "w": w, "h": h})
    return rects


def rect_to_tiles(rect, tile_size):
    """像素矩形 → tile 坐标集合(含边界)"""
    x0 = int(rect["x"] // tile_size)
    y0 = int(rect["y"] // tile_size)
    x1 = int((rect["x"] + rect["w"]) // tile_size)
    y1 = int((rect["y"] + rect["h"]) // tile_size)
    return [(x, y) for x in range(x0, x1) for y in range(y0, y1)]


def auto_gid_map(tilemap, sector_layers, arena_layers, object_layers):
    """自动生成 gid 映射表(名称待填)"""
    sectors = collect_layer_gids(tilemap, sector_layers)
    arenas = collect_layer_gids(tilemap, arena_layers)
    objects = collect_layer_gids(tilemap, object_layers)
    out = {"world": "", "sector": {}, "arena": {}, "object": {}}
    for gid in sorted(sectors):
        out["sector"][str(gid)] = {"name": "SECTOR_" + str(gid),
                                    "tiles": len(sectors[gid])}
    for gid in sorted(arenas):
        out["arena"][str(gid)] = {"name": "ARENA_" + str(gid),
                                  "tiles": len(arenas[gid])}
    for gid in sorted(objects):
        out["object"][str(gid)] = {"name": "OBJECT_" + str(gid),
                                   "tiles": len(objects[gid])}
    return out


def build_maze(tilemap, gid_map, sector_layers, arena_layers, object_layers,
               room_layers=None, sector_rect_layers=None):
    """生成 maze.json。

    地址标注优先用对象层(room_layers):每个矩形对象 = 一个场所,
    对象名 = 场所名,父级(sector)由 sector_rect_layers 的矩形对象给出;
    无对象层时回退 gid 模式(sector/arena/object 图层的 gid 映射)。
    """
    w = tilemap.get("width", 0)
    h = tilemap.get("height", 0)
    world_name = gid_map.get("world", "the Ville")
    tile_size = tilemap.get("tilewidth", 32)
    addr_keys = ["world", "sector", "arena", "game_object"]

    # ---- 对象层模式(推荐) ----
    room_rects = collect_object_rects(tilemap, room_layers or []) if room_layers else []
    sector_rects = collect_object_rects(tilemap, sector_rect_layers or []) if sector_rect_layers else []
    if room_rects:
        # sector 矩形:名字即 sector 名,覆盖区域提供 sector 归属
        # room 矩形:名字即 arena 名,覆盖区域提供 arena 归属
        tiles = []
        for y in range(h):
            for x in range(w):
                addr = [world_name]
                sector = None
                for srect in sector_rects:
                    if (x, y) in rect_to_tiles(srect, tile_size):
                        sector = srect["name"]
                        break
                arena = None
                for rrect in room_rects:
                    if (x, y) in rect_to_tiles(rrect, tile_size):
                        arena = rrect["name"]
                        break
                if sector:
                    addr.append(sector)
                if arena:
                    addr.append(arena)
                tiles.append({"coord": [x, y], "address": addr})
        return {
            "world": world_name,
            "tile_size": tile_size,
            "size": [h, w],
            "map": {"width": w, "height": h},
            "camera": {"start_x": 0, "start_y": 0},
            "tile_address_keys": addr_keys,
            "tiles": tiles,
        }

    # ---- gid 模式(兼容现有) ----
    sector_gids = collect_layer_gids(tilemap, sector_layers)
    arena_gids = collect_layer_gids(tilemap, arena_layers)
    object_gids = collect_layer_gids(tilemap, object_layers)

    # gid -> 名称
    sector_names = {int(k): v["name"] for k, v in gid_map.get("sector", {}).items()}
    arena_names = {int(k): v["name"] for k, v in gid_map.get("arena", {}).items()}
    object_names = {int(k): v["name"] for k, v in gid_map.get("object", {}).items()}

    # 每格:最全地址 = world + sector + arena + object(缺则降级)
    tiles = []
    for y in range(h):
        for x in range(w):
            addr = [world_name]
            sector = sector_names.get(next((g for g in sector_gids if (x, y) in sector_gids[g]), 0))
            arena = arena_names.get(next((g for g in arena_gids if (x, y) in arena_gids[g]), 0))
            obj = object_names.get(next((g for g in object_gids if (x, y) in object_gids[g]), 0))
            if sector:
                addr.append(sector)
            if arena:
                addr.append(arena)
            if obj:
                addr.append(obj)
            tiles.append({"coord": [x, y], "address": addr})

    return {
        "world": world_name,
        "tile_size": tile_size,
        "size": [h, w],  # [height, width],与 Maze(config["size"]) 一致
        "map": {"width": w, "height": h},
        "camera": {"start_x": 0, "start_y": 0},
        "tile_address_keys": addr_keys,
        "tiles": tiles,
    }


def main():
    p = argparse.ArgumentParser(description="Tiled JSON 地图 → provenance maze.json(无约束转换)")
    p.add_argument("input", help="Tiled 导出的 JSON 地图文件")
    p.add_argument("-o", "--output", default="maze.json", help="输出 maze.json 路径")
    p.add_argument("--sector-layers", nargs="+", default=["Sector Blocks"],
                   help="区域标注图层名(可多个)")
    p.add_argument("--arena-layers", nargs="+", default=["Arena Blocks"],
                   help="场所标注图层名")
    p.add_argument("--object-layers", nargs="+", default=["Object Interaction Blocks"],
                   help="对象标注图层名")
    p.add_argument("--gid-map", default="", help="gid→名称映射 JSON(名称已填好)")
    p.add_argument("--auto-gid-map", action="store_true",
                   help="只生成 gid 映射表(名称待填)到 -o 指定文件")
    p.add_argument("--room-layers", nargs="+", default=[],
                   help="对象层(矩形=场所,对象名=场所名),推荐模式")
    p.add_argument("--sector-rect-layers", nargs="+", default=[],
                   help="对象层(矩形=区域/父级,对象名=区域名)")
    p.add_argument("--tiled", default="", help="Tiled 可执行文件路径(输入是 .tmx 时用于导出 json)")
    args = p.parse_args()

    if not os.path.exists(args.input):
        print("错误: 输入文件不存在: {}".format(args.input), file=sys.stderr)
        sys.exit(1)

    # 输入是 .tmx:先用 Tiled 命令行导出 json(接手人在 Tiled 里编辑的是 .tmx)
    # 流程:编辑 .tmx → 本工具自动导出 json → 转换 maze.json
    if args.input.lower().endswith(".tmx"):
        import subprocess
        tiled_exe = args.tiled or "tiled"
        export_json = os.path.splitext(args.input)[0] + ".exported.json"
        print("检测到 .tmx 输入,用 Tiled 导出 json: {} → {}".format(args.input, export_json))
        try:
            r = subprocess.run([tiled_exe, "--export-map", "json",
                                args.input, export_json],
                               capture_output=True, timeout=120)
        except FileNotFoundError:
            print("错误: 找不到 Tiled 可执行文件 '{}'。请用 --tiled 指定路径"
                  "(如 C:/Program Files/Tiled/tiled.exe),或先在 Tiled 里手动导出 json。"
                  .format(tiled_exe), file=sys.stderr)
            sys.exit(1)
        if r.returncode != 0:
            print("Tiled 导出失败: {}".format(r.stderr.decode(errors="replace")), file=sys.stderr)
            sys.exit(1)
        args.input = export_json
        print("Tiled 导出成功: {}".format(export_json))

    tilemap = load_json(args.input)

    if args.auto_gid_map:
        out = auto_gid_map(tilemap, args.sector_layers, args.arena_layers, args.object_layers)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("已生成 gid 映射表: {}".format(args.output))
        print("请编辑该文件,把 SECTOR_x/ARENA_x/OBJECT_x 改成真实房间名,再带 --gid-map 运行。")
        return

    if args.gid_map:
        gid_map = load_json(args.gid_map)
    else:
        print("警告: 未提供 --gid-map,将用占位名称生成(建议先 --auto-gid-map 生成并填写)。")
        gid_map = auto_gid_map(tilemap, args.sector_layers, args.arena_layers, args.object_layers)

    maze = build_maze(tilemap, gid_map, args.sector_layers, args.arena_layers, args.object_layers)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(maze, f, ensure_ascii=False, indent=2)
    total = len(maze["tiles"])
    addressed = sum(1 for t in maze["tiles"] if len(t["address"]) > 1)
    print("完成: {} → {}".format(args.input, args.output))
    print("地图 {w}x{h}, tile {ts}px, 共 {total} 格, 其中 {addressed} 格有语义地址".format(
        w=maze["map"]["width"], h=maze["map"]["height"], ts=maze["tile_size"],
        total=total, addressed=addressed))


if __name__ == "__main__":
    main()
