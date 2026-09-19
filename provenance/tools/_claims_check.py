# -*- coding: utf-8 -*-
"""临时:逐条核对"待修问题清单"里的事实主张(问题 1/2/3/9)。"""
import glob
import io
import json
import os
import re
import sys

sys.path.insert(0, ".")
from case01.consistency import quick_scan  # noqa: E402

BOILER = ("当然可以", "当然，可以", "以下是", "好的，以下", "好的,以下", "下面是我")


def check(paths, label):
    print("=" * 96)
    print(label, "共", len(paths), "条")
    for p in sorted(paths):
        run_id = os.path.basename(os.path.dirname(p))
        try:
            d = json.load(io.open(p, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print("  %-42s 读不了: %s" % (run_id, e))
            continue
        verdict, _why = quick_scan(d)
        refl = (d.get("reflection") or {}).get("text") or ""
        head = refl[:26].replace("\n", " ")
        tail = refl[-30:].replace("\n", " ") if refl else ""
        sh = d.get("state_history") or []
        gaps = (d.get("compat") or {}).get("gaps") or []
        sh_gap = [g for g in gaps if g.startswith("state_history")]
        print("  %-42s 一致性=%-12s 反思=%5d字 状态=%2d条 %s" % (
            run_id, verdict, len(refl), len(sh), "gaps:" + ";".join(sh_gap) if sh_gap else ""))
        print("      开头: %s" % head)
        print("      结尾: %s" % tail)
        if refl and any(b in refl[:20] for b in BOILER):
            print("      ↑ 以客套话开头")


check(glob.glob("case01/runs/*/run.json"), "case01/runs(服务目录)")
check(glob.glob("case01/runs_archive_20260919/*/run.json"), "runs_archive_20260919")
