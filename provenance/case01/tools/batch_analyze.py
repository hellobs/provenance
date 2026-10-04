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
- 台账行(`--batch`)是扁平摘要:字数/质量分/status/失败原因/issue 计数以
  `reflection_*`、`issues*` 为准,run.json 在场时仍以明细覆盖 —— 两条链
  (`--batch` 与 `--scan-runs`)必须给同一份 row。
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


def _consistency_verdict(d: Dict) -> str:
    """取一致性判定,**两种形状都要吃**:run.json 里是 dict(带 verdict/reason),
    台账里是扁平字符串("consistent"/"unknown"/"")。

    2026-10-03 实测踩过:`_collect_from_ledger` 会把**台账行**当 run.json 喂给 `_row`
    (台账存 `consistency: "unknown"`,run.json 存 `consistency: {verdict: ...}`),
    于是 `(d.get("consistency") or {}).get("verdict")` 直接 AttributeError,
    **整份分析报告写不出来**。此前没暴露,是因为那会儿 consistency 恒为空串,
    `"" or {}` 兜住了 —— 一旦有人真的往记录里盖章,这个工具立刻死。
    """
    cs = d.get("consistency")
    if isinstance(cs, dict):
        return cs.get("verdict") or ""
    if isinstance(cs, str):
        return cs
    return ""


def _row(run_id: str, d: Dict) -> Dict:
    refl = d.get("reflection") or {}
    router = d.get("router") or {}
    quality = refl.get("quality") or {}
    post = router.get("postprocess") or {}
    issues = router.get("issues") or []
    reasons = quality.get("failure_reasons") or []
    row = {
        "run_id": run_id,
        "branch": d.get("branch") or "",
        "consistency": _consistency_verdict(d),
        "refl_chars": _count(refl.get("text")) or len(refl.get("text") or ""),
        "refl_score": quality.get("score"),
        "refl_status": quality.get("status") or "",
        "refl_fail_reasons": reasons,
        "issues": _count(issues),
        "issues_final": post.get("final_issue_count", _count(issues)),
        "turns": _count(d.get("turns")),
        "events": _count(d.get("events")),
        # 失败/未执行的显式标记(2026-10-03):统计要能把它们摘出去,
        # 否则"没跑成"会被当成"跑出来但很差"混进均值。
        "router_status": router.get("status") or "",
        # 口径与 full_context._reflection_failed 保持一致:error 状态,或旧版
        # (2026-10-03 前)无 quality 块只剩占位文本,都算"没跑成";
        # status=fail 不算(那是质量门判真反思差)。
        "refl_error": ((quality.get("error") or "")
                       if quality.get("status") == "error"
                       else ("反思生成失败(旧版失败占位,无 quality 块)"
                             if (refl.get("text") or "").strip() in ("(反思生成失败)", "(失败)")
                             else "")),
        "router_error": (router.get("error") or "") if router.get("status") in ("error", "skipped") else "",
    }
    # 台账行是**扁平摘要**(`reflection_chars/_score/_status/_failure_reasons`
    # 与 `issues/issues_final`,见 batch_run._digest),run.json 是嵌套结构。
    # 原先只吃嵌套侧,台账自带的这些字段全被丢掉、只能靠回读 run.json 补;
    # 一旦 run.json 不在(目录被清、换机分析、事后删档),这条记录就带着
    # refl_chars=0 / issues=0 溜进均值,报出来的是"模型写了一篇 0 字反思、
    # Router 一条 issue 都没挑",而不是"这条的明细读不到"。
    # 现在:嵌套侧给不出东西时以扁平台账字段为准。
    for key, flat_key in (("refl_chars", "reflection_chars"),
                          ("refl_score", "reflection_score"),
                          ("refl_status", "reflection_status"),
                          ("refl_fail_reasons", "reflection_failure_reasons"),
                          ("issues", "issues"),
                          ("issues_final", "issues_final")):
        if row[key] in (None, "", 0, []):
            v = d.get(flat_key)
            if v not in (None, ""):
                row[key] = v
    if not row["refl_error"] and row["refl_status"] == "error":
        row["refl_error"] = (";".join(str(x) for x in (row["refl_fail_reasons"] or []))
                             or "反思生成失败(台账只记了 status=error)")
    return row


def _is_failed(r: Dict) -> bool:
    """这条记录的反思或 Router 是不是"没跑成"(而非"跑出来质量差")。"""
    return bool(r.get("refl_error")) or (r.get("router_status") or "") in ("error", "skipped")


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
    # 台账摘要之外再回读 run.json 补全(与扫目录同口径)。
    # 2026-10-04 修:原先只在 `refl_status` 为空时回读,那是"台账没有质量字段"
    # 时的代理判断;一旦 _row 开始吃扁平字段,这个代理就永远为真、回读整段变成死代码,
    # 而 router.status(error/skipped)只有 run.json 里有 —— 不读就摘不出"Router 没跑成"。
    # 现在恒回读:文件在场就以明细为准,不在场就以台账摘要为准。
    for r in rows:
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
    # 失败/未执行的记录不进质量与产出统计(2026-10-03):它们的 0 分、0 issue
    # 不代表"质量差",而是"这一步没跑成"。混进均值会把结论带偏
    # (此前"反思生成失败"的占位文本就被当成一篇超短反思算进了字数均值)。
    # 摘出来单独报数 —— 不允许静默:条数与原因都写进结果。
    failed = [r for r in rows if _is_failed(r)]
    ok_rows = [r for r in rows if not _is_failed(r)]
    scores = [r["refl_score"] for r in ok_rows if isinstance(r["refl_score"], (int, float))]
    chars = [r["refl_chars"] for r in ok_rows]
    issues = [r["issues"] for r in ok_rows]

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

    # 质量门分布必须与同节的均值**同一总体**(2026-10-03 体检修):原来 status_dist
    # 取全量 rows、而字数/质量均值取 ok_rows,于是 pass 率的分母(43)比它旁边那句
    # "9 条已排除在上述均值之外"大了一圈,报告自相矛盾(261003-165042 报成 76.7%,
    # 真实值是 33/34=97.1%)。同一节里两个口径 = 读者必然读错。
    st = _dist([r["refl_status"] or "(无)" for r in ok_rows])
    cons = _dist([r["consistency"] or "(无)" for r in ok_rows])

    # 失败记录的单列:条数 + 逐条原因,让"没跑成"在报告里看得见
    failed_detail = []
    for r in failed:
        why = r.get("refl_error") or r.get("router_error") or "(未记录原因)"
        failed_detail.append({"run_id": r["run_id"], "reason": str(why)[:200]})

    return {
        "title": title,
        "n_runs": n,
        "n_unreadable": bad,
        "branches": br,
        "branch_top": {"branch": top_branch, "count": top_n,
                       "share": round(top_n / n, 3) if n else None,
                       "collapse_warning": collapse},
        "excluded_failed": {
            "n": len(failed),
            "n_scored": len(ok_rows),
            "note": ("反思或 Router 未跑成的记录已排除在质量/产出统计之外"
                     "(其 0 分与 0 issue 不代表质量)"),
            "records": failed_detail[:50],
        },
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
            "n": len(issues),
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
    ex = a.get("excluded_failed") or {}
    if ex.get("n"):
        L += [
            "",
            "**⚠️ 反思/Router 未跑成的记录:{} 条,已排除在上述均值之外**"
            "（计入统计的有效记录 {} 条）。".format(ex["n"], ex["n_scored"]),
            "",
            "它们的 0 分与 0 issue 不代表质量,而是这一步没执行 —— 混进均值会把"
            "结论带偏,故单列于此。逐条原因:",
            "",
            "| run | 原因 |",
            "| --- | --- |",
        ]
        for rec in ex.get("records", []):
            L.append("| {} | {} |".format(rec["run_id"], rec["reason"].replace("|", "\\|")))
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
        "- 零 issue 记录:{} 条（占 {:.1%}，分母为参与统计的 {} 条）".format(
            r["zero_issue_runs"],
            r["zero_issue_runs"] / r["n"] if r["n"] else 0, r["n"]),
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
        # 分母 = 参与统计的记录数(status_dist 现在只数未跑成的,见上面的注释),
        # 与本节均值同口径,并把两个数都打出来,免得读者再猜分母是谁。
        denom = sum(st.values())
        concl.append("- 反思质量门 pass 率 {:.1%}（{}/{}，分母已摘除“没跑成”的记录）。".format(
            passed / denom if denom else 0, passed, denom))
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
