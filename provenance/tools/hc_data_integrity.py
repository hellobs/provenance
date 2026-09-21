# -*- coding: utf-8 -*-
"""体检:数据完整性(governance.json / tilemap / checkpoints / maze / agent)"""
import json, os, glob

BASE = r"D:\zzr\provenance\provenance"
issues = []

def load(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        issues.append("%s 解析失败: %s" % (p, e))
        return default

# 1) governance.json
gov = load(os.path.join(BASE, "governance.json"))
if gov is not None:
    roles = gov.get("roles", {})
    print("governance.json roles: %d 个角色" % len(roles))
    for n, r in roles.items():
        ws = {k: round(v, 4) for k, v in r.items() if isinstance(v, (int, float))}
        tot = round(sum(ws.values()), 4)
        print("   %-14s 权重项=%d  sum=%.3f  %s" % (n, len(ws), tot, "OK" if abs(tot - 1.0) < 1e-6 else "!!sum!=1"))
else:
    issues.append("governance.json 缺失/不可读")

# 2) tilemap 可读 + Collisions 图层存在
tm = load(os.path.join(BASE, "frontend/static/assets/village/tilemap/tilemap.json"))
if tm is not None:
    cols = [l["name"] for l in tm.get("layers", []) if l.get("type") == "tilelayer"]
    col = next((l for l in tm["layers"] if l["name"] == "Collisions"), None)
    print("tilemap 图层数=%d; Collisions 存在?%s" % (len(tm.get("layers", [])), bool(col)))
    if col:
        print("   Collisions %dx%d 非空格=%d" % (col["width"], col["height"], sum(1 for g in col["data"] if g)))

# 3) maze.json 一致性(前端 vs 场景)
fs = load(os.path.join(BASE, "frontend/static/assets/village/maze.json"))
tm_w = {(x, y) for y in range(col["height"]) for x in range(col["width"]) if col["data"][y * col["width"] + x]} if col else set()
fe_w = {tuple(t["coord"]) for t in (fs or {}).get("tiles", []) if t.get("collision")}
print("maze(frontend) collision=%d; tilemap墙=%d; 交集=%d; frontend缺=%d" % (
    len(fe_w), len(tm_w), len(fe_w & tm_w), len(tm_w - fe_w)))
if not (fe_w & tm_w):
    issues.append("前端 maze 碰撞与 tilemap 完全没有交集(前端仍无法反映新墙)")

# 4) checkpoints run 记录可读(抽前 3 个最新,读最后一个 simulate-* 快照)
ck = os.path.join(BASE, "results", "checkpoints")
if os.path.isdir(ck):
    runs = sorted(os.listdir(ck))
    print("checkpoints runs: %d 个" % len(runs))
    readable = 0
    for r in runs[-3:]:
        snaps = sorted(glob.glob(os.path.join(ck, r, "simulate-*.json")))
        if snaps:
            snap = load(snaps[-1])
            if snap is not None:
                nagent = len(snap.get("agents") or {})
                readable += 1
                print("   run '%s' 快照=%d步 最后agents=%d readable=OK" % (r, len(snaps), nagent))
            else:
                issues.append("run %s 最后快照不可读" % r)
    print("   最近3个 run 可读: %d/3" % readable)
else:
    print("checkpoints 目录不存在(离线存档无)")
    issues.append("checkpoints 目录缺失")

# 5) agent.json coord 是否压在 tilemap 墙上
for n in sorted(os.listdir(os.path.join(BASE, "frontend/static/assets/village/agents"))):
    p = os.path.join(BASE, "frontend/static/assets/village/agents", n, "agent.json")
    if ".bak" in n or not os.path.exists(p):
        continue
    a = load(p)
    c = tuple(a["coord"]) if a else None
    if c and col and col["data"][c[1] * col["width"] + c[0]]:
        issues.append("agent %s 初始 coord 在墙上: %s" % (n, list(c)))

print("\n===== 问题清单(%d) =====" % len(issues))
for x in issues:
    print("  - " + x)