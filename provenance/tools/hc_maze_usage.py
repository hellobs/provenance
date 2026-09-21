# -*- coding: utf-8 -*-
"""体检:确认前端 maze.json 是否在运行时被引用(前端图层or静态遗留)。"""
import os, re

root = r"D:\zzr\provenance\provenance\frontend"
pat_maze = re.compile(r"maze")
files_hit = []
for dp, dn, fn in os.walk(root):
    if "node_modules" in dp or "dist" in dp:
        continue
    for f in fn:
        if not f.endswith((".html", ".js", ".py")):
            continue
        p = os.path.join(dp, f)
        try:
            s = open(p, encoding="utf-8").read()
        except Exception:
            continue
        for m in pat_maze.finditer(s):
            ln = s[: m.start()].count("\n") + 1
            files_hit.append((p.replace(root, "[fe]"), ln,
                              s[max(0, m.start() - 60): m.end() + 60].replace("\n", " ")))
for h in files_hit[:40]:
    print("%s:%d  ...%s..." % h)
print("命中文件数:", len(files_hit))