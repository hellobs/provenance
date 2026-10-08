# -*- coding: utf-8 -*-
"""检查 tilemap.json 的 Collisions 图层:tile 是否带 collide 属性、是否被用作阻挡。"""
import json, collections

p = r"D:\zzr\provenance\provenance\frontend\static\assets\village\tilemap\tilemap.json"
tm = json.load(open(p, encoding="utf-8"))

layers = []
def walk(layers, out):
    for l in layers:
        if l.get("type") == "group":
            walk(l.get("layers", []), out)
        else:
            out.append(l)
walk(tm.get("layers", []), layers)

names = [l.get("name") for l in layers]
print("总图层:", len(layers))

col = None
for l in layers:
    if l.get("name") == "Collisions":
        col = l; break
if col is None:
    print("[FAIL] 没有名为 Collisions 的 tilelayer")
else:
    print("Collisions 图层: type=%s, 尺寸 %sx%s, encoding=%s, compression=%s" % (
        col.get("type"), col.get("width"), col.get("height"), col.get("encoding"), col.get("compression")))
    # 数据
    data = col.get("data")
    nonempty = [g for g in data if g] if isinstance(data, list) else None
    if nonempty is None and isinstance(data, str):
        print("  data 是字符串(需 base64 解码),先跳过数据层面")
        nonempty = None
    if nonempty is not None:
        bys = collections.Counter(nonempty)
        print("  非空 tile 数:", len(nonempty), "| 去重 gid 数:", len(bys))
        print("  前 8 个高频 gid:", bys.most_common(8))

# tilesets:查各 tileset 的 tile 带 collide/阻挡类 property
print("\n=== 每个 tileset 中带 collide 属性或其 tile 有 collide 属性的情况 ===")
for ts in tm.get("tilesets", []):
    firstgid = ts.get("firstgid")
    prop = ts.get("properties") or []
    tprops = ts.get("tileproperties") or {}
    tile_set = ts.get("tiles")
    collide_props = []
    # tilelevel property
    tiles_conf = ts.get("tiles") or {}
    for tid, tdef in tiles_conf.items():
        pr = tdef.get("properties") or []
        for pp in pr:
            if pp.get("name") == "collide":
                collide_props.append((int(tid), pp.get("value")))
    print("  tileset '%s' (firstgid=%d): tileset级properties=%s, 含 collide 属性的 tile 数=%d %s" % (
        ts.get("name"), firstgid, prop, len(collide_props),
        ("样例:"+str(collide_props[:4]) if collide_props else "")))

# 关键 tile: tilesets 的 tile 级 collide property
print("\n=== Collisions 图层用到的高频 gid 是否对应载 collide=true 的 tile ===")
# 构建 gid->(tileset, tileid, collide)
gid_map = {}
for ts in tm.get("tilesets", []):
    fg = ts.get("firstgid")
    for tid, tdef in (ts.get("tiles") or {}).items():
        pr = {pp.get("name"): pp.get("value") for pp in (tdef.get("properties") or [])}
        gid_map[int(fg) + int(tid)] = (ts.get("name"), tid, pr.get("collide"))
if isinstance(col.get("data"), list):
    used = set(g for g in col["data"] if g)
    collide_gids_used = [g for g in used if gid_map.get(g) and gid_map[g][2]]
    print("  Collisions 用到的 gid 数:", len(used))
    print("  其中载 collide property 的 gid 数:", len(collide_gids_used))
    if not collide_gids_used:
        print("  >>> Collisions 图层使用到的 tile **没有 collide 属性** -> 前端 setCollisionByProperty 匹配不到,永不碰撞")
        # 看看这些 gid 实际映射到哪个贴图、有无别的属性
        sample = list(used)[:8]
        for g in sample:
            print("     gid=%s -> %s" % (g, gid_map.get(g)))
    else:
        print("  >>> Collisions 图层存在带 collide 的 tile,数据是有效的")