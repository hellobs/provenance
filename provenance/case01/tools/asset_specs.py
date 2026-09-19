# -*- coding: utf-8 -*-
"""打印像素素材的**尺寸规格** —— 拼场景前先看这份,省得反复试。

用户的诉求(2026-09-19):"你应该告诉我那些砖块是多大的等"。所以把它做成能随时重跑的工具,
而不是只在聊天里报一串数字。

用法(在 provenance/provenance 下):

    python -m case01.tools.asset_specs
    python -m case01.tools.asset_specs --village frontend/static/assets/village
    python -m case01.tools.asset_specs --maze case00/scenario/maze.json

会打印:瓦片尺寸与地图尺寸、每个 tileset 的图幅/列数/瓦片数、角色贴图与图集的帧规格、
以及 maze(走位/寻路)与 tilemap(画面)两张图的对应关系和注意事项。
"""
import argparse
import io
import json
import os
import struct
import sys

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_size(path):
    """只读 IHDR,不依赖 PIL。"""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] != PNG_SIG:
            return None
        return struct.unpack(">II", head[16:24])
    except OSError:
        return None


def _load_json(path):
    # 有的 Tiled 导出带 BOM
    with io.open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def report_tilemap(village):
    print("== 画面:前端实际加载的 Tiled 地图 ==")
    p = os.path.join(village, "tilemap", "tilemap.json")
    if not os.path.exists(p):
        print("  找不到", p)
        return
    t = _load_json(p)
    tw, th = t.get("tilewidth"), t.get("tileheight")
    w, h = t.get("width"), t.get("height")
    print("  {}: {} 列 x {} 行, 瓦片 {}x{}px  ->  世界 {}x{} px".format(
        os.path.basename(p), w, h, tw, th, (w or 0) * (tw or 0), (h or 0) * (th or 0)))
    print("  图层({} 个): {}".format(len(t.get("layers") or []),
                                    ", ".join(l.get("name", "?") for l in (t.get("layers") or []))))
    print("  tileset(全部 tilewidth={}):".format(tw))
    for ts in (t.get("tilesets") or []):
        cols = ts.get("columns")
        print("    {:<22} firstgid={:<6} 图 {}x{}px  列数={}  瓦片数={}  图={}".format(
            str(ts.get("name")), ts.get("firstgid"), ts.get("imagewidth"),
            ts.get("imageheight"), cols, ts.get("tilecount"),
            os.path.basename(str(ts.get("image")))))


def report_tilesets(village):
    print()
    print("== 素材图幅(tilemap/ 目录下的图) ==")
    d = os.path.join(village, "tilemap")
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith(".png"):
            continue
        s = png_size(os.path.join(d, fn))
        if not s:
            continue
        note = ""
        if s[0] % 32 == 0 and s[1] % 32 == 0:
            note = "  = {} x {} 瓦片(32px 网格)".format(s[0] // 32, s[1] // 32)
        print("  {:<30} {}x{}px{}".format(fn, s[0], s[1], note))


def report_agents(village):
    print()
    print("== 角色:贴图与图集 ==")
    root = os.path.join(village, "agents")
    atlas = os.path.join(root, "sprite.json")
    if os.path.exists(atlas):
        a = _load_json(atlas)
        frames = a.get("frames") or []
        sizes = set()
        for f in frames:
            fr = f.get("frame") or {}
            sizes.add((fr.get("w"), fr.get("h")))
        names = [f.get("filename") for f in frames[:6]]
        print("  sprite.json: {} 帧, 帧尺寸 {}, 例: {}".format(len(frames), sorted(sizes), names))
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if not os.path.isdir(p):
            continue
        tex = os.path.join(p, "texture.png")
        por = os.path.join(p, "portrait.png")
        s_tex = png_size(tex) if os.path.exists(tex) else None
        s_por = png_size(por) if os.path.exists(por) else None
        extra = ""
        if s_tex:
            extra = "  = {} 列 x {} 行(32px 帧)".format(s_tex[0] // 32, s_tex[1] // 32)
        print("  {:<14} texture={}  portrait={}{}".format(
            name, "{}x{}".format(*s_tex) if s_tex else "-",
            "{}x{}".format(*s_por) if s_por else "-", extra))


def report_maze(village, mazes):
    print()
    print("== 走位:引擎用的 maze.json(寻路/碰撞/房间地址) ==")
    for p in mazes:
        if not os.path.exists(p):
            print("  找不到", p)
            continue
        m = _load_json(p)
        size = m.get("size") or []
        # mavis 的 Maze 是 maze_height, maze_width = size  => size = [高, 宽]
        hh, ww = (size + [None, None])[:2]
        print("  {}: size={}  (= [高, 宽] -> {} 列 x {} 行)  tile_size={}  有字段的格子 {} 个".format(
            p, size, ww, hh, m.get("tile_size"), len(m.get("tiles") or [])))
    print("  注意:画面看 tilemap.json(27 列 x 24 行),走位看 maze.json；")
    print("        两张图必须对齐 —— 只改一张会出现『人穿墙』或『卡住不动』。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="打印像素素材规格(砖块/角色/地图)")
    ap.add_argument("--village", default="frontend/static/assets/village",
                    help="素材根目录(默认 frontend/static/assets/village)")
    ap.add_argument("--maze", action="append", default=None,
                    help="maze.json 路径(可多次);默认打印 case00 与 case01 两份")
    args = ap.parse_args(argv)
    mazes = args.maze or ["case00/scenario/maze.json",
                          "case01/injector/scenario/maze.json"]
    report_tilemap(args.village)
    report_tilesets(args.village)
    report_agents(args.village)
    report_maze(args.village, mazes)
    print()
    print("拼场景提示:格子坐标 x 32 = 像素坐标;角色碰撞盒 setSize(30, 40);"
          "相机跟随 --roles 的第一个角色。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
