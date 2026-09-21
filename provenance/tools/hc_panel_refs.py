# -*- coding: utf-8 -*-
import re
s = open(r"D:\zzr\provenance\provenance\frontend\templates\main_script.html", encoding="utf-8").read()
for g in ["goals-panel", "timeline-panel", "goals-header", "goals-sliders", "chat-content"]:
    lines = re.findall(r"[^\n]*getElementById\(\"" + g + r"\"\)[^\n]*", s)
    print("=== %s: %d 处 ===" % (g, len(lines)))
    for i, line in enumerate(lines):
        print("   %d> %s" % (i, line.strip()[:120]))