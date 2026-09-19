# -*- coding: utf-8 -*-
"""打印一批记录的"分支 vs AI T0 立场"一致性矩阵 —— 交付/评审前自查用。

用法(在 provenance/provenance 下):

    python -m case01.tools.consistency_report                    # case01/runs
    python -m case01.tools.consistency_report --runs-dir case01/runs_archive_20260919
    python -m case01.tools.consistency_report --only-bad         # 只列不一致/判不了的

退出码:0=全部一致;1=存在不一致或判不了的记录(方便放进流程里当门禁)。
"""
import argparse
import glob
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from case01.consistency import _count_signals, _t0_ai_text, quick_scan  # noqa: E402

LABEL = {"consistent": "一致", "inconsistent": "不一致", "unknown": "判不了"}


def collect(runs_dir):
    rows = []
    for p in sorted(glob.glob(os.path.join(runs_dir, "*", "run.json"))):
        try:
            with io.open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:  # noqa: BLE001 - 坏记录也要报出来
            rows.append((os.path.basename(os.path.dirname(p)), "?", "unknown",
                         (0, 0, 0), None, "读不了: {}".format(e)))
            continue
        verdict, reason = quick_scan(d)
        t0 = _t0_ai_text(d)
        pos, neg, cond = _count_signals(t0)
        sh = d.get("state_history") or []
        last = (sh[-1] or {}).get("state") or {}
        rows.append((os.path.basename(os.path.dirname(p)), d.get("branch", "?"), verdict,
                     (pos, neg, cond), last.get("held_fraction"), reason))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="打印 Branch 与 AI T0 立场的一致性矩阵")
    ap.add_argument("--runs-dir", default="case01/runs")
    ap.add_argument("--only-bad", action="store_true", help="只列不一致/判不了的")
    args = ap.parse_args(argv)

    rows = collect(args.runs_dir)
    if not rows:
        print("没找到记录:", args.runs_dir)
        return 1
    show = [r for r in rows if (not args.only_bad) or r[2] != "consistent"]
    print("{:<40} {:<4} {:<8} {:>16} {:>10}".format(
        "run_id", "分支", "判定", "正面/谨慎/条件", "末日持仓"))
    for run_id, branch, verdict, sig, held, _reason in show:
        print("{:<40} {:<4} {:<8} {:>16} {:>10}".format(
            run_id, branch, LABEL.get(verdict, verdict), str(sig),
            "-" if held is None else held))
    bad = [r for r in rows if r[2] == "inconsistent"]
    unknown = [r for r in rows if r[2] == "unknown"]
    print("\n合计 {} 条:一致 {} / 不一致 {} / 判不了 {}".format(
        len(rows), len(rows) - len(bad) - len(unknown), len(bad), len(unknown)))
    for r in bad + unknown:
        print("  {} [{}] {}".format(r[0], r[1], r[5]))
    return 1 if (bad or unknown) else 0


if __name__ == "__main__":
    sys.exit(main())
