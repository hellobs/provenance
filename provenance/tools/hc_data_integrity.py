# -*- coding: utf-8 -*-
"""体检:数据完整性(governance.json / tilemap / checkpoints / maze / agent)"""
import json, os, glob

BASE = r"D:\zzr\provenance\provenance"
issues = []
col = None   # §2 里才赋值;tilemap 不可读时 §3/§5 要拿到 None 而不是 NameError 崩在半路

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
        # 先求和再判:此前先逐项 round(4) 再求和,引入 1e-4 级误差,把 Σ 本为 1 的角色
        # 误报成 !!sum!=1(2026-09-27 体检:Mr. Zhou 实际 Σ=1.0000000000000002 被判错)。
        ws = {k: v for k, v in r.items() if isinstance(v, (int, float))}
        tot = sum(ws.values())
        print("   %-14s 权重项=%d  sum=%.6f  %s" % (
            n, len(ws), tot, "OK" if abs(tot - 1.0) < 1e-6 else "!!sum!=1"))
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
# 守的不变式是"前端 maze 碰撞 ⊇ tilemap 墙格"(缺席 tile = 前端把墙当可走,
# align_frontend_maze.py 补的就是这个上界)。旧判据只在**交集为空**时才报,
# 于是"244 个墙里只对上 1 个"这种半对齐状态是绿的 —— 打印了 frontend缺 却没人判。
if tm_w and (tm_w - fe_w):
    issues.append("前端 maze 少 %d 个 tilemap 墙格(缺席即被前端当可通行): %s" % (
        len(tm_w - fe_w), sorted(tm_w - fe_w)[:8]))
if not (fe_w & tm_w):
    issues.append("前端 maze 碰撞与 tilemap 完全没有交集(前端仍无法反映新墙)")

# 4) checkpoints run 记录可读(取**按 mtime 最新的几个目录**,读其最后一个 simulate-* 快照)
ck = os.path.join(BASE, "results", "checkpoints")
if os.path.isdir(ck):
    # 只数目录:`reflection_marks.json` / `reflection_marks.jsonl` / `interventions.json` 是散文件,
    # 不是 run。而且原先按 `sorted(os.listdir)` 取**名字尾巴**当"最新",尾巴落到的往往是
    # `visual-path-test` 这类测试目录;更要紧的是"一条快照都没取到"那种情况既不计数也不报
    # ——2026-10-07 实测它打印"最近3个 run 可读: 2/3"却"问题清单(0)"、退出 0。
    runs = sorted((d for d in os.listdir(ck) if os.path.isdir(os.path.join(ck, d))),
                  key=lambda d: os.path.getmtime(os.path.join(ck, d)))
    print("checkpoints runs: %d 个(只算目录)" % len(runs))
    readable = 0
    sample = runs[-3:]
    for r in sample:
        snaps = sorted(glob.glob(os.path.join(ck, r, "simulate-*.json")))
        if not snaps:
            issues.append("最新 run %s 没有 simulate-* 快照(取样落空,这一条等于没被读到)" % r)
            continue
        snap = load(snaps[-1])
        if snap is None:
            issues.append("run %s 最后快照不可读" % r)
            continue
        nagent = len(snap.get("agents") or {})
        readable += 1
        print("   run '%s' 快照=%d步 最后agents=%d readable=OK" % (r, len(snaps), nagent))
    print("   最新 %d 个 run 可读: %d/%d" % (len(sample), readable, len(sample)))
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