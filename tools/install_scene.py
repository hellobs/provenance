# -*- coding: utf-8 -*-
"""把 Tiled 里做好的新场景装进平台(一条命令:导出 JSON + 换场景 + 重生成 maze.json)。

为什么要有它:换场景要同时动三样东西,漏一样就会出现"画面是新的、人却穿墙/卡住":

1. `frontend/static/assets/village/tilemap/tilemap.json` —— Phaser 渲染用的地图(Tiled 导出);
2. `maze.json` —— 引擎寻路/房间地址用的语义图(address + collision);
3. 换图后角色出生点/会议点是否还可走(不可走要立刻报出来,**不许静默**)。

用法(在仓库根的上一级,即 provenance/ 下):

    # 1) 先看会怎么改(不写盘):同时生成待填名的 gid 表
    python tools/install_scene.py --tmx provenance/frontend/static/assets/village/tilemap/tilemap2.tmx \\
        --maze provenance/case01/injector/scenario/maze.json --dry-run

    # 2) 填好 gid 表里的房间名,再真正安装
    python tools/install_scene.py --tmx ...tilemap2.tmx --gid-map tools/gid_map_tilemap2.json \\
        --maze provenance/case01/injector/scenario/maze.json --install

约定(与既有 maze.json 一致):
- `tiles` 只保留"有地址或标记了 collision"的格子;其余不写(旧文件就是 360 格);
- `collision: true` 来自 Tiled 的 `Collisions` 图层(非 0 即阻挡),不写 false;
- 地址层级 = `Sector Blocks` → `Arena Blocks` → `Object Interaction Blocks`,
  名字由 gid 表给出(未给名字的用 `SECTOR_<gid>` 占位并打印出来催填)。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

TILED_CANDIDATES = (
    r"C:\Program Files\Tiled\tiled.exe",
    r"C:\Program Files (x86)\Tiled\tiled.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tiled\tiled.exe"),
)
LAYERS = {
    "collision": "Collisions",
    "sector": "Sector Blocks",
    "arena": "Arena Blocks",
    "object": "Object Interaction Blocks",
}


def find_tiled(explicit=""):
    if explicit:
        return explicit
    for p in TILED_CANDIDATES:
        if os.path.exists(p):
            return p
    return "tiled"          # 交给 PATH


def export_json(tmx, tiled, out):
    cmd = [tiled, "--export-map", "json", tmx, out]
    print("导出: {}".format(" ".join(cmd)))
    subprocess.run(cmd, check=True)
    if not os.path.exists(out):
        raise RuntimeError("Tiled 没有产出 {}".format(out))
    return out


def clean_map(map_json):
    """去掉没被使用的"外部 tileset 引用"(它们没有 name/image,Phaser 载入会报错),
    并把 tileset 的 image 换成不含本机绝对路径的相对写法。"""
    dropped = []
    kept = []
    for ts in map_json.get("tilesets") or []:
        if not ts.get("name") or not (ts.get("image") or ts.get("tiles")):
            dropped.append(ts.get("source") or ts.get("firstgid"))
            continue
        img = ts.get("image")
        if isinstance(img, str) and (":" in img or img.startswith("/") or ".." in img):
            ts = dict(ts)
            ts["image"] = "tilemap/" + os.path.basename(img.replace("\\", "/"))
        kept.append(ts)
    map_json = dict(map_json)
    map_json["tilesets"] = kept
    return map_json, dropped


def layer_data(map_json, name):
    for l in map_json.get("layers") or []:
        if str(l.get("name", "")).strip() == name:
            return l.get("data") or []
    return []


def _load_gid_map(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def auto_gid_map(map_json, world):
    """按 gid 汇总各语义层的格子数(给人工填名用)。"""
    W = map_json["width"]

    def names_for(layer_name):
        data = layer_data(map_json, layer_name)
        counts = {}
        for g in data:
            if g:
                counts[g] = counts.get(g, 0) + 1
        return counts

    def as_entries(counts, prefix):
        return {str(g): {"name": "{}_{}".format(prefix, g), "tiles": n}
                for g, n in sorted(counts.items())}

    return {"world": world,
            "sector": as_entries(names_for(LAYERS["sector"]), "SECTOR"),
            "arena": as_entries(names_for(LAYERS["arena"]), "ARENA"),
            "object": as_entries(names_for(LAYERS["object"]), "OBJECT")}


def build_maze(map_json, gid_map, world):
    """按旧 maze.json 的口径生成:地址层级 + collision,只保留有内容的格子。"""
    W, H = map_json["width"], map_json["height"]
    coll = layer_data(map_json, LAYERS["collision"])
    sect = layer_data(map_json, LAYERS["sector"])
    aren = layer_data(map_json, LAYERS["arena"])
    obj = layer_data(map_json, LAYERS["object"])

    def name_of(kind, gid):
        entry = (gid_map.get(kind) or {}).get(str(gid)) or {}
        return entry.get("name") or ""

    placeholders = set()
    tiles = []
    for y in range(H):
        for x in range(W):
            i = y * W + x
            address = []
            for kind, data in (("sector", sect), ("arena", aren), ("object", obj)):
                g = data[i] if i < len(data) else 0
                if not g:
                    continue
                nm = name_of(kind, g)
                if not nm:
                    nm = "{}_{}".format(kind.upper(), g)
                # 占位名(自动生成的 SECTOR_x/ARENA_x/OBJECT_x)要报出来催填,
                # 否则房间地址会永远停在 SECTOR_32174 这种机器名上(不静默)
                if re.match(r"^(SECTOR|ARENA|OBJECT)_\d+$", nm):
                    placeholders.add((kind, g, nm))
                # 同名层级只写一次(旧文件里 Break Room/Corridor 是两格同层不同名,不做合并)
                if not address or address[-1] != nm:
                    address.append(nm)
            blocked = bool(coll[i]) if i < len(coll) else False
            if not address and not blocked:
                continue
            t = {"coord": [x, y]}
            if blocked:
                t["collision"] = True
            if address:
                t["address"] = address
            tiles.append(t)
    maze = {
        "world": world,
        "tile_size": map_json.get("tilewidth", 32),
        "size": [H, W],                                   # 引擎要 [高, 宽]
        "map": {"width": W, "height": H},
        "camera": {"start_x": 0, "start_y": 0},
        "tile_address_keys": ["world", "sector", "arena", "game_object"],
        "tiles": tiles,
    }
    return maze, sorted(placeholders)


def check_spawns(maze, scenario_dirs):
    """换图后角色的出生/会议坐标是否还可走(不可走必须立刻看到)。"""
    blocked_cells = {tuple(t["coord"]) for t in maze["tiles"] if t.get("collision")}
    problems = []
    for d in scenario_dirs:
        agents_dir = os.path.join(d, "agents")
        if not os.path.isdir(agents_dir):
            continue
        for name in sorted(os.listdir(agents_dir)):
            p = os.path.join(agents_dir, name, "agent.json")
            if not os.path.exists(p):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:  # noqa: BLE001
                continue
            coord = cfg.get("coord")
            if coord and tuple(coord) in blocked_cells:
                problems.append((name, coord))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="把 Tiled 新场景装进平台(换图 + 重生成 maze.json)")
    ap.add_argument("--tmx", required=True, help="Tiled 地图(.tmx)")
    ap.add_argument("--install", action="store_true", help="真写盘(默认只看)")
    ap.add_argument("--dry-run", action="store_true", help="只看会怎么改")
    ap.add_argument("--tiled", default="", help="tiled.exe 路径(默认自动找)")
    ap.add_argument("--world", default="the Ville")
    ap.add_argument("--gid-map", default="", help="已填好房间名的 gid 表;没有就用占位名")
    ap.add_argument("--maze", action="append", default=[],
                    help="要覆盖的 maze.json(可多次;例如 case01 与 case00 各一份)")
    ap.add_argument("--scenario", action="append", default=[],
                    help="要检查出生点的场景目录(默认由 --maze 的上级推出)")
    ap.add_argument("--map-name", default="tilemap.json", help="安装成哪个地图文件名")
    args = ap.parse_args(argv)

    write = args.install and not args.dry_run
    tmx = os.path.abspath(args.tmx)
    map_dir = os.path.dirname(tmx)
    target_map = os.path.join(map_dir, args.map_name)

    tmp_json = os.path.join(os.environ.get("TEMP", "/tmp"),
                            "scene_export_{}.json".format(int(time.time())))
    map_json = json.load(open(export_json(tmx, find_tiled(args.tiled), tmp_json),
                              encoding="utf-8"))
    map_json, dropped = clean_map(map_json)
    print("地图 {}x{} 格,tile {}px,图层 {} 个".format(
        map_json["width"], map_json["height"], map_json.get("tilewidth"),
        len(map_json.get("layers") or [])))
    if dropped:
        print("去掉未使用的外部 tileset 引用: {}".format(dropped))

    gid_map = _load_gid_map(args.gid_map)
    if not gid_map:
        auto = auto_gid_map(map_json, args.world)
        gid_map = auto
        out_gid = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "gid_map_{}.json".format(
                                   os.path.splitext(os.path.basename(tmx))[0]))
        with open(out_gid, "w", encoding="utf-8") as f:      # 体检/安装都写,便于先填名
            json.dump(auto, f, ensure_ascii=False, indent=1)
        print("没给 --gid-map:用占位名,并把待填名的表写到 {}".format(out_gid))

    maze, placeholders = build_maze(map_json, gid_map, args.world)
    n_addr = sum(1 for t in maze["tiles"] if t.get("address"))
    n_coll = sum(1 for t in maze["tiles"] if t.get("collision"))
    print("maze: tiles={} (有地址 {} / 阻挡 {})".format(len(maze["tiles"]), n_addr, n_coll))
    if placeholders:
        print("**还有 {} 个房间/对象没名字**(请填 gid 表后重跑):".format(len(placeholders)))
        for kind, gid, nm in placeholders:
            print("   {:>8} gid={} 占位名={}".format(kind, gid, nm))

    scenarios = args.scenario or [os.path.dirname(os.path.dirname(os.path.abspath(m)))
                                  for m in args.maze]
    problems = check_spawns(maze, scenarios)
    if problems:
        print("**注意:这些角色的出生坐标在新图里是阻挡格**(要改场景配置或换个位置):")
        for name, coord in problems:
            print("   {} coord={}".format(name, coord))
    else:
        print("出生点检查:所有场景里的角色坐标在新图里都可走")

    if not write:
        print("\n(未写盘;加 --install 才真正替换)")
        return 0

    if os.path.exists(target_map):
        bak = os.path.join(map_dir, "{}.{}.bak.json".format(
            os.path.splitext(args.map_name)[0], time.strftime("%Y%m%d-%H%M")))
        shutil.copy2(target_map, bak)
        print("旧地图已备份: {}".format(os.path.basename(bak)))
    with open(target_map, "w", encoding="utf-8") as f:
        json.dump(map_json, f, ensure_ascii=False, separators=(",", ":"))
    print("新地图已安装: {}".format(target_map))
    for m in args.maze:
        if os.path.exists(m):
            shutil.copy2(m, m + "." + time.strftime("%Y%m%d-%H%M") + ".bak")
        os.makedirs(os.path.dirname(os.path.abspath(m)), exist_ok=True)
        with open(m, "w", encoding="utf-8") as f:
            json.dump(maze, f, ensure_ascii=False, indent=2)
        print("maze 已重生成: {}".format(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
