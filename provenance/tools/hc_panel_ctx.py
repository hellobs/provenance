# -*- coding: utf-8 -*-
s = open(r"D:\zzr\provenance\provenance\frontend\templates\main_script.html", encoding="utf-8").read().splitlines()
keys = ['getElementById("goals-panel")', 'getElementById("timeline-panel")', 'getElementById("goals-header")']
for i, l in enumerate(s):
    if any(k in l for k in keys):
        print("-------- 行 %d --------" % (i + 1))
        for j in range(max(0, i - 3), min(len(s), i + 6)):
            print("%4d: %s" % (j + 1, s[j][:120]))