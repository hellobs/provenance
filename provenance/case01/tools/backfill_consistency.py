# -*- coding: utf-8 -*-
"""回填一致性戳:给**已有的** run.json 补上 `branch_action.source` 与 `consistency`。

为什么要单独一个工具:已有的记录是"预设分支但记录里没说"的产物(还带完整反思/分流)。
重跑映射要 ~90 秒 LLM 且会覆盖 reflection/router,风险大;这一步是**纯计算**,
只往记录里加两个字段,不碰其它任何内容。

用法(在 provenance/provenance 下):

    python -m case01.tools.backfill_consistency            # 只看会怎么改(不写盘)
    python -m case01.tools.backfill_consistency --write    # 真写
    python -m case01.tools.backfill_consistency --write --runs-dir case01/runs
"""
import argparse
import glob
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from case01.consistency import quick_scan  # noqa: E402


def backfill(path, write=False):
    with io.open(path, encoding="utf-8") as f:
        rec = json.load(f)
    before = (rec.get("branch_action") or {}).get("source"), (rec.get("consistency") or {}).get("verdict")
    ba = dict(rec.get("branch_action") or {})
    # 旧记录的分支都是运行参数指定的 → preset;judge 模式是后来才有的
    ba.setdefault("source", "preset")
    rec["branch_action"] = ba
    verdict, reason = quick_scan(rec)
    rec["consistency"] = {"verdict": verdict, "reason": reason, "method": "quick_scan",
                          "branch_source": ba.get("source")}
    after = (ba["source"], verdict)
    if write:
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
    return before, after, reason


def main(argv=None):
    ap = argparse.ArgumentParser(description="给已有记录回填一致性戳(纯计算)")
    ap.add_argument("--runs-dir", default="case01/runs")
    ap.add_argument("--write", action="store_true", help="真写盘(默认只看)")
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.runs_dir, "*", "run.json")))
    if not paths:
        print("没找到记录:", args.runs_dir)
        return 1
    n_bad = 0
    for p in paths:
        before, after, reason = backfill(p, write=args.write)
        run_id = os.path.basename(os.path.dirname(p))
        flag = {"consistent": "一致  ", "inconsistent": "不一致", "unknown": "判不了"}.get(after[1], after[1])
        if after[1] != "consistent":
            n_bad += 1
        print("  {:<40} {}   source {} -> {}   verdict {} -> {}".format(
            run_id, flag, before[0] or "-", after[0], before[1] or "-", after[1]))
        if after[1] != "consistent":
            print("      {}".format(reason))
    print("\n合计 {} 条,其中不一致/判不了 {} 条;{}".format(
        len(paths), n_bad, "已写盘" if args.write else "未写盘(--write 才写)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
