# -*- coding: utf-8 -*-
"""打印一批记录的"分支 vs AI T0 立场"一致性矩阵 —— 交付/评审前自查用。

用法(在 provenance/provenance 下):

    python -m case01.tools.consistency_report                    # case01/runs
    python -m case01.tools.consistency_report --runs-dir case01/runs_archive_20260919
    python -m case01.tools.consistency_report --only-bad         # 只列不一致/判不了的

退出码:0=全部一致;1=存在不一致或判不了的记录(方便放进流程里当门禁)。

判定读的是**记录自己盖的戳**(`consistency.verdict` / `consistency.method`),
没盖戳的旧记录才现算 `quick_scan` 兜底,并把每条的口径印在"判定口径"一栏。
理由与 backfill 同源:立场判官生效后,自查工具若继续现算关键词快筛,报出来的
就不是这批记录主张的结论(实测 39 条里 13 条与戳不符,其中含 2 条判官判
 inconsistent 的分歧样本被快筛洗成"一致")。
"正面/谨慎/条件"三元组恒为现算 —— 它是判官的**输入侧**证据,不是判定结果,
用来核对"判官看到的文本还在不在记录里"。
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


def _stamped(d):
    """读记录**自己盖的**一致性戳,返回 (verdict, method, reason);没盖给 None。

    2026-10-04 实测:本工具原先一律现算 `quick_scan(d)`,于是它报的判定和记录里
    的 `consistency.verdict` 是两套口径。批次 261004-123726-h120 的 39 条里
    **13 条对不上**:立场判官盖 inconsistent 的 -016/-025/-039(正是本批要交给
    专家评审的分歧样本)被现算快筛说成 consistent —— 评审前自查会把它们**藏起来**;
    另有 11 条判官判了 consistent 的,快筛判不了,于是退出码非 0,门禁在一批
    合格记录上无故亮红。同一个错误 backfill 已经修过(不能用关键词快筛覆盖
    立场判官戳),这里不能留着。
    """
    cs = d.get("consistency")
    if not isinstance(cs, dict):
        cs = {"verdict": cs if isinstance(cs, str) else ""}
    verdict = (cs.get("verdict") or "").strip()
    if not verdict:
        return None
    return verdict, cs.get("method") or "(未记 method)", cs.get("reason") or ""


def collect(runs_dir):
    """每行 = (run_id, 分支, 判定, 立场信号三元组, 末日持仓, 理由, 口径)。

    口径最后一栏显式带出来:混合口径(有戳的读戳、没戳的现算)如果不说,
    读的人会把"判不了"当成判官没生效。
    """
    rows = []
    for p in sorted(glob.glob(os.path.join(runs_dir, "*", "run.json"))):
        try:
            with io.open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:  # noqa: BLE001 - 坏记录也要报出来
            rows.append((os.path.basename(os.path.dirname(p)), "?", "unknown",
                         (0, 0, 0), None, "读不了: {}".format(e), "(文件读失败)"))
            continue
        t0 = _t0_ai_text(d)
        sig = _count_signals(t0)
        sh = d.get("state_history") or []
        last = (sh[-1] or {}).get("state") or {}
        held = last.get("held_fraction")
        stamped = _stamped(d)
        if stamped:
            verdict, method, reason = stamped
        else:
            verdict, reason = quick_scan(d)
            method = "现算 quick_scan(记录无戳)"
        rows.append((os.path.basename(os.path.dirname(p)), d.get("branch", "?"),
                     verdict, sig, held, reason, method))
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
    print("{:<40} {:<4} {:<8} {:>16} {:>10}  {}".format(
        "run_id", "分支", "判定", "正面/谨慎/条件", "末日持仓", "判定口径"))
    for run_id, branch, verdict, sig, held, _reason, method in show:
        print("{:<40} {:<4} {:<8} {:>16} {:>10}  {}".format(
            run_id, branch, LABEL.get(verdict, verdict), str(sig),
            "-" if held is None else held, method))
    bad = [r for r in rows if r[2] == "inconsistent"]
    unknown = [r for r in rows if r[2] == "unknown"]
    recomputed = sum(1 for r in rows if str(r[6]).startswith("现算"))
    print("\n合计 {} 条:一致 {} / 不一致 {} / 判不了 {}".format(
        len(rows), len(rows) - len(bad) - len(unknown), len(bad), len(unknown)))
    print("口径:{} 条读记录自带戳,{} 条无戳现算 quick_scan 兜底".format(
        len(rows) - recomputed, recomputed))
    for r in bad + unknown:
        print("  {} [{}] ({}) {}".format(r[0], r[1], r[6], r[5]))
    return 1 if (bad or unknown) else 0


if __name__ == "__main__":
    sys.exit(main())
