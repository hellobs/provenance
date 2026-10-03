# -*- coding: utf-8 -*-
"""批次数据分析:把一批 run.json 汇总成可解读的统计 + 与历史基线的对比。

为什么要有这个工具
------------------
`batch_run.py` 负责把案例跑出来(台账 ledger.jsonl),但台账只有摘要字段。
要回答科研问题——**分支分布是否偏斜?反思质量稳不稳?Router 产出多少?
与答辩基线(25 条)相比是变好还是变差?——** 需要一次成型的统计。

它回答什么
----------
1. **分支分布**:自动判定是否塌缩到单一分支(这是真实风险:25 条历史里
   B 占 16 条,LLM 判定有往 B 塌的倾向);
2. **反思质量**:字数分布 + 质量门(pass/review/fail)比例 + 失败原因 Top N;
3. **一致性**:consistent / inconsistent / unknown 分布;
4. **Router 产出**:issues 总数与分布,零 issue 记录占比(过不了分流也是一种结论);
5. **与基线对比**:和 `答辩速查表.md` 的 25 条基线并排看,变化要能说出口。

用法
----
    # 分析一个批次(读 ledger.jsonl)
    python -m case01.tools.batch_analyze --batch 261003-124842-main3h

    # 直接扫 runs 目录下所有 run.json(不依赖批次台账)
    python -m case01.tools.batch_analyze --scan-runs --prefix batch-

    # 输出
    <batch>/analysis.json + analysis.md

口径
----
- **只统计真实存在的 run.json**;解析失败的单独计数,不混进分布。
- 反思字数按 `reflection.text` 长度;质量分取 `reflection.quality.score`。
- 基线数字来自 `results/analysis/答辩速查表.md`(25 条,4b 本地模型),
  与本批次(8b)**不是同口径**,对比只作量级参照,不宣称显著性。
"""
import argparse
import glob
import json
import os
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CASE01_DIR = os.path.dirname(HERE)
PROV_DIR = os.path.dirname(CASE01_DIR)
RUNS_DIR = os.path.join(CASE01_DIR, "runs")
BATCH_ROOT = os.path.join(PROV_DIR, "results", "analysis", "batch_runs")

# 答辩基线(25 条,本地 4b)——只作量级参照,不同模型不构成显著性比较
BASELINE = {
    "n": 25,
    "branches": {"A": 2, "B": 16, "C": 7},
    "reflection_chars_mean": 2629,
    "issues_total": 109,
    "consistency": {"consistent": 18, "inconsistent": 3, "unknown": 4},
}


def _load_run(path: str) -> Optional[Dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _count(v) -> int:
    """列表给长度、整数直接给值。台账存的是计数,run.json 存的是列表——
    两种来源都要能读,否则分析器只能吃其中一种。"""
    if isinstance(v, (list, tuple)):
        return len(v)
    return v if isinstance(v, (int, float)) else 0


def _row(run_id: str, d: Dict) -> Dict:
    refl = d.get("reflection") or {}
    router = d.get("router") or {}
    quality = refl.get("quality") or {}
    post = router.get("postprocess") or {}
    issues = router.get("issues") or []
    return {
        "run_id": run_id,
        "branch": d.get("branch") or "",
        "consistency": ((d.get("consistency") or {}).get("verdict") or ""),
        "refl_chars": _count(refl.get("text")) or len(refl.get("text") or ""),
        "refl_score": quality.get("score"),
        "refl_status": quality.get("status") or "",
        "refl_fail_reasons": quality.get("failure_reasons") or [],
        "issues": _count(issues),
        "issues_final": post.get("final_issue_count", _count(issues)),
        "turns": _count(d.get("turns")),
        "events": _count(d.get("events")),
    }


def _collect_from_runs(prefix: str = "") -> tuple:
    """扫 runs 目录。返回 (rows, 坏文件数)。"""
    rows, bad = [], 0
    for p in sorted(glob.glob(os.path.join(RUNS_DIR, "*", "run.json"))):
        run_id = os.path.basename(os.path.dirname(p))
        if prefix and not run_id.startswith(prefix):
            continue
        d = _load_run(p)
        if d is None:
            bad += 1
            continue
        rows.append(_row(run_id, d))
    return rows, bad


def _collect_from_ledger(batch: str) -> tuple:
    """读批次台账。有 run.json 的才算数(台账里失败行不掺进来)。"""
    d = os.path.join(BATCH_ROOT, batch)
    lp = os.path.join(d, "ledger.jsonl")
    if not os.path.exists(lp):
        return [], 0
    rows, bad = [], 0
    seen = set()
    with open(lp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            run_id = rec.get("run_id") or ""
            if not run_id or not rec.get("ok") or run_id in seen:
                continue          # 只留成功且未重复的
            seen.add(run_id)
            rows.append(_row(run_id, rec))
    # 台账摘要缺 quality 组件的,回读 run.json 补全(保证与扫目录同口径)
    for r in rows:
        if r.get("refl_status"):
            continue
        d2 = _load_run(os.path.join(RUNS_DIR, r["run_id"], "run.json"))
        if d2:
            full = _row(r["run_id"], d2)
            r.update({k: v for k, v in full.items() if v not in ("", None, [])})
    return rows, bad


def _dist(values: List, key=lambda x: x) -> Dict:
    out: Dict = {}
    for v in values:
        k = str(key(v))
        out[k] = out.get(k, 0) + 1
    return out


def _mean(vals) -> Optional[float]:
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 1) if vals else None


def _median(vals) -> Optional[float]:
    vals = sorted(v for v in vals if isinstance(v, (int, float)))
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else round((vals[mid - 1] + vals[mid]) / 2, 1)


def analyze(rows: List[Dict], bad: int, title: str) -> Dict:
    n = len(rows)
    scores = [r["refl_score"] for r in rows if isinstance(r["refl_score"], (int, float))]
    chars = [r["refl_chars"] for r in rows]
    issues = [r["issues"] for r in rows]

    fail_reasons: Dict = {}
    for r in rows:
        for reason in (r.get("refl_fail_reasons") or []):
            key = reason if isinstance(reason, str) else json.dumps(reason, ensure_ascii=False)
            fail_reasons[key] = fail_reasons.get(key, 0) + 1

    # 分支塌缩检测:最高占比 >60% 就明确标出来(这是要写进报告的判断,不是只给数字)
    br = _dist([r["branch"] or "(空)" for r in rows])
    top_branch, top_n = ("", 0)
    if br:
        top_branch, top_n = max(br.items(), key=lambda kv: kv[1])
    collapse = bool(n >= 10 and top_n / n > 0.60)

    st = _dist([r["refl_status"] or "(无)" for r in rows])
    cons = _dist([r["consistency"] or "(无)" for r in rows])

    return {
        "title": title,
        "n_runs": n,
        "n_unreadable": bad,
        "branches": br,
        "branch_top": {"branch": top_branch, "count": top_n,
                       "share": round(top_n / n, 3) if n else None,
                       "collapse_warning": collapse},
        "reflection": {
            "chars_mean": _mean(chars), "chars_median": _median(chars),
            "chars_min": min(chars) if chars else None,
            "chars_max": max(chars) if chars else None,
            "score_mean": _mean(scores), "score_median": _median(scores),
            "score_n": len(scores),
            "status_dist": st,
            "fail_reason_top": dict(sorted(fail_reasons.items(),
                                           key=lambda kv: -kv[1])[:8]),
        },
        "router": {
            "issues_total": sum(issues), "issues_mean": _mean(issues),
            "issues_median": _median(issues),
            "zero_issue_runs": sum(1 for c in issues if c == 0),
            "issues_dist": _dist(issues),
        },
        "consistency": cons,
        "per_run": rows,
    }


def _md(a: Dict) -> str:
    L = [
        "# 批次数据分析 · {}".format(a["title"]),
        "",
        "- 有效记录:**{}** 条(另有 {} 个文件解析失败,未计入)".format(
            a["n_runs"], a["n_unreadable"]),
        "",
        "## 1. 分支分布",
        "",
        "| 分支 | 条数 | 占比 |",
        "| --- | --- | --- |",
    ]
    tot = max(a["n_runs"], 1)
    for b, c in sorted(a["branches"].items(), key=lambda kv: -kv[1]):
        L.append("| {} | {} | {:.1%} |".format(b, c, c / tot))
    bt = a["branch_top"]
    L += ["", "**判定:{}**".format(
        "⚠️ 出现塌缩倾向——`{}` 占 {:.1%},单跑一条时分支不再由内容决定,"
        "批量统计分支占比意义有限,需改用 `--timeline` 强制均衡铺。"
        .format(bt["branch"], bt["share"]) if bt["collapse_warning"]
        else "分布未塌缩,分支由 LLM 判定自然产生。")]
    if a["branch_top"]["branch"]:
        L.append("（最高分支:{} · {} 条 · {:.1%}）".format(
            bt["branch"], bt["count"], bt["share"]))
    L += [
        "",
        "## 2. 反思质量",
        "",
        "- 字数:均值 {} · 中位 {} · 区间 {}–{}".format(
            a["reflection"]["chars_mean"], a["reflection"]["chars_median"],
            a["reflection"]["chars_min"], a["reflection"]["chars_max"]),
        "- 质量分:均值 {} · 中位 {}（n={}）".format(
            a["reflection"]["score_mean"], a["reflection"]["score_median"],
            a["reflection"]["score_n"]),
        "- 质量门:{}".format(", ".join(
            "{}={}".format(k, v)
            for k, v in sorted(a["reflection"]["status_dist"].items()))),
    ]
    if a["reflection"]["fail_reason_top"]:
        L += ["", "**失败原因 Top N**", "", "| 原因 | 次数 |", "| --- | --- |"]
        for k, v in a["reflection"]["fail_reason_top"].items():
            L.append("| {} | {} |".format(k[:110], v))
    r = a["router"]
    L += [
        "",
        "## 3. Router 产出",
        "",
        "- issues:合计 {} · 均值 {} · 中位 {}".format(
            r["issues_total"], r["issues_mean"], r["issues_median"]),
        "- 零 issue 记录:{} 条（占 {:.1%}）".format(
            r["zero_issue_runs"], r["zero_issue_runs"] / tot),
        "- 每条记录 issue 数分布:{}".format(
            ", ".join("{}条→{}次".format(k, v)
                      for k, v in sorted(r["issues_dist"].items()))),
        "",
        "## 4. 一致性",
        "",
        "| 判定 | 条数 |", "| --- | --- |",
    ]
    for k, v in sorted(a["consistency"].items(), key=lambda kv: -kv[1]):
        L.append("| {} | {} |".format(k, v))
    L += [
        "",
        "## 5. 与答辩基线对比（25 条 · 本地 4b）",
        "",
        "> **不同模型,只作量级参照,不宣称显著性。** 本批次用 qwen3:8b。",
        "",
        "| 指标 | 基线(4b, n=25) | 本批次(8b, n={}) |".format(a["n_runs"]),
        "| --- | --- | --- |",
        "| 反思均字 | {} | {} |".format(
            BASELINE["reflection_chars_mean"], a["reflection"]["chars_mean"]),
        "| issues 总数 | {} | {} |".format(BASELINE["issues_total"],
                                          r["issues_total"]),
        "| issues/条 | {:.1f} | {} |".format(
            BASELINE["issues_total"] / BASELINE["n"], r["issues_mean"]),
        "| 分支 A/B/C | 2/16/7 | {} |".format(
            "/".join(str(a["branches"].get(x, 0)) for x in "ABC")),
        "",
        "## 6. 结论",
        "",
    ]
    concl = []
    if a["reflection"]["status_dist"]:
        st = a["reflection"]["status_dist"]
        passed = st.get("pass", 0)
        concl.append("- 反思质量门 pass 率 {:.1%}（{}/{}）。".format(
            passed / tot, passed, a["n_runs"]))
    if r["issues_total"] == 0:
        concl.append("- **Router 零产出**:全批次没有拆出任何 issue,分流环节在"
                     "本批次未生效,需先排查再采信其它指标。")
    if bt["collapse_warning"]:
        concl.append("- **分支塌缩**:{} 占 {:.1%},自动判定模式下的分支分布"
                     "不可用于统计,后续批次应强制均衡铺。".format(
                         bt["branch"], bt["share"]))
    concl.append("- Router 每条产出 {} 个 issue，与基线 {:.1f} 个{}。".format(
        r["issues_mean"], BASELINE["issues_total"] / BASELINE["n"],
        "基本持平" if abs((r["issues_mean"] or 0)
                       - BASELINE["issues_total"] / BASELINE["n"]) < 1.0 else "有差距"))
    L.extend(concl)
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="批次数据分析 + 基线对比")
    ap.add_argument("--batch", default="", help="批次号(读其 ledger.jsonl)")
    ap.add_argument("--scan-runs", action="store_true",
                    help="直接扫 case01/runs 下所有 run.json")
    ap.add_argument("--prefix", default="", help="配合 --scan-runs 按 run_id 前缀过滤")
    ap.add_argument("--out", default="", help="输出目录(默认写批次目录)")
    args = ap.parse_args(argv)

    if not args.batch and not args.scan_runs:
        ap.error("给 --batch <批次号> 或 --scan-runs")

    if args.batch:
        rows, bad = _collect_from_ledger(args.batch)
        title = args.batch
    else:
        rows, bad = _collect_from_runs(args.prefix)
        title = "runs 扫描{}".format("/" + args.prefix if args.prefix else "")

    if not rows:
        print("没有可用记录(bad={}）——确认 run.json 是否已落盘".format(bad))
        return 1

    a = analyze(rows, bad, title)
    out_dir = args.out or (os.path.join(BATCH_ROOT, args.batch) if args.batch
                           else BATCH_ROOT)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "analysis.md"), "w", encoding="utf-8") as f:
        f.write(_md(a))

    print("有效 {} 条(坏 {} 个)→ {}".format(a["n_runs"], a["n_unreadable"],
                                        os.path.join(out_dir, "analysis.md")))
    print("分支分布:{}".format(a["branches"]))
    print("反思均字 {} · 质量分均值 {} · issues 合计 {}".format(
        a["reflection"]["chars_mean"], a["reflection"]["score_mean"],
        a["router"]["issues_total"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
