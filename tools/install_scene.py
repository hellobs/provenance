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
    """换图后角色的出生/会议坐标是否还可走,以及**是否还连得通**。

    2026-09-19 修两个坑(都实测踩到):
    1. 场景目录原来按 `maze.json` 往上推两级算,case01 的 maze 在 `injector/scenario/` 下,
       推出来是 `injector/`,那里没有 `agents/` → 检查被 `continue` **静默跳过**,
       于是打印出"所有角色坐标都可走"这句假话。现在场景目录就是 maze 所在目录,
       并且找不到 `agents/` 会明确警告。
    2. 只查"坐标是不是阻挡格"不够:画墙时很容易把某个房间**整体封死** ——
       格子本身可走,但和主区域不连通,人走不过去。这里顺手算连通块并报出来。
    """
    blocked_cells = {tuple(t["coord"]) for t in maze["tiles"] if t.get("collision")}
    H, W = maze["size"][0], maze["size"][1]
    free = {(x, y) for y in range(H) for x in range(W) if (x, y) not in blocked_cells}
    seen, comps = set(), []
    for c in sorted(free):
        if c in seen:
            continue
        stack, comp = [c], set()
        seen.add(c)
        while stack:
            x, y = stack.pop()
            comp.add((x, y))
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in free and n not in seen:
                    seen.add(n)
                    stack.append(n)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    main = comps[0] if comps else set()

    problems, checked = [], 0
    for d in scenario_dirs:
        agents_dir = os.path.join(d, "agents")
        if not os.path.isdir(agents_dir):
            problems.append(("(找不到场景目录)", d))
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
            if not coord:
                continue
            checked += 1
            c = tuple(coord)
            if c in blocked_cells:
                problems.append((name, "{} 落在这张图的阻挡格上".format(list(c))))
            elif c not in main:
                problems.append((name, "{} 与主区域不连通(被墙隔开)".format(list(c))))
    return problems, checked, len(free), [len(c) for c in comps[:3]]


def nearest_free(coord, blocked, H, W, main):
    """在**主连通块**里找离 coord 最近的空格(给 --fix-spawns 用)。"""
    if tuple(coord) in main:
        return list(coord)
    best, best_d = None, None
    for c in main:
        d = abs(c[0] - coord[0]) + abs(c[1] - coord[1])
        if best_d is None or d < best_d:
            best, best_d = c, d
    return list(best) if best else None


def _render_coord(old_array_text, new):
    """按**原来的排版**渲染坐标数组:原来一行就还一行,原来多行就还多行。

    (第一次实现直接 json.dump 整文件,把 case01 的 `[9, 7]` 拆成 3 行、
     把 case00 本来就多行的压成一行 —— 换坐标不该顺带重排人家的文件。)
    """
    if "\n" not in old_array_text:
        return "[" + ", ".join(str(v) for v in new) + "]"
    return "[\n" + ",\n".join("    " + str(v) for v in new) + "\n  ]"


def fix_spawns(maze, scenario_dirs, write=False):
    """把落在阻挡格/孤岛上的角色坐标挪到主连通块里最近的空格(改 scenario 的 agent.json)。

    默认只报告不写;写盘时留 `.bak`。**必须打印改动明细** —— 改研究侧给的场景数据
    不是小事,不能静默。
    """
    blocked = {tuple(t["coord"]) for t in maze["tiles"] if t.get("collision")}
    H, W = maze["size"][0], maze["size"][1]
    free = {(x, y) for y in range(H) for x in range(W) if (x, y) not in blocked}
    seen, comps = set(), []
    for c in sorted(free):
        if c in seen:
            continue
        st, comp = [c], set()
        seen.add(c)
        while st:
            x, y = st.pop()
            comp.add((x, y))
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in free and n not in seen:
                    seen.add(n)
                    st.append(n)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    main = comps[0] if comps else set()

    changes = []
    for d in scenario_dirs:
        agents_dir = os.path.join(d, "agents")
        if not os.path.isdir(agents_dir):
            continue
        for name in sorted(os.listdir(agents_dir)):
            p = os.path.join(agents_dir, name, "agent.json")
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as f:
                cfg = json.load(f)
            coord = cfg.get("coord")
            if not coord or tuple(coord) in main:
                continue
            new = nearest_free(coord, blocked, H, W, main)
            if not new:
                continue
            changes.append((d, name, list(coord), new))
            if write:
                shutil.copy2(p, p + "." + time.strftime("%Y%m%d-%H%M") + ".bak")
                # **最小改写**:只替换 "coord": [...] 那一段,保留原文件的排版与行尾。
                # (第一次实现是 json.dump 整文件重写,结果把 [9, 7] 拆成 3 行、还吃掉了末尾换行,
                #  diff 全是噪声 —— 改研究侧数据不该顺带重排人家的文件。)
                with open(p, encoding="utf-8") as f:
                    text = f.read()
                new_text, n = re.subn(r'("coord"\s*:\s*)(\[[^\]]*\])',
                                      lambda m: m.group(1) + _render_coord(m.group(2), new),
                                      text, count=1)
                if n:
                    with open(p, "w", encoding="utf-8", newline="") as f:
                        f.write(new_text)
    return changes


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
    ap.add_argument("--fix-spawns", action="store_true",
                    help="把落在阻挡格/孤岛的角色坐标挪到主连通块里最近的空格(改 agent.json,留 .bak)")
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

    scenarios = args.scenario or [os.path.dirname(os.path.abspath(m)) for m in args.maze]
    problems, checked, n_free, top = check_spawns(maze, scenarios)
    print("可走格 {} 个,连通块大小前 3: {}".format(n_free, top))
    if problems:
        print("**注意:这些角色的坐标在新图里有问题**(场景配置要改,或在 Tiled 里放通):")
        for name, why in problems:
            print("   {} {}".format(name, why))
        if args.fix_spawns:
            changes = fix_spawns(maze, scenarios, write=write)
            print("--fix-spawns:{} 个坐标需要挪;{}".format(
                len(changes), "已写盘(留了 .bak)" if write else "未写盘(加 --install 才写)"))
            for d, name, old, new in changes:
                print("   {} {} : {} → {}".format(os.path.basename(os.path.dirname(d)),
                                                  name, old, new))
        else:
            print("   (想自动挪到最近的空格:加 --fix-spawns;它会打印改动明细并留 .bak)")
    else:
        print("出生点检查:{} 个角色坐标都还可走、且都在主连通块里".format(checked))

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
