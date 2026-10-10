# -*- coding: utf-8 -*-
"""把全库 case01 的反思与 Router 拆出的问题导成 CSV,供"该派哪类专家"做决定。

只读:读成品记录根 + 意见台账,不写记录根、不碰任何写口(8060/`/api/expert/decision`)。

两份输出(UTF-8 BOM + CRLF,Excel 双击即开):
  expert_demand_by_issue.csv      一行一个 issue;没拆出问题的记录也占一行(类别列空 + 写明原因)
  expert_demand_by_category.csv   专业类别 × 判据代次 的汇总

口径全部取现成实现,不在这里重算一遍(两份实现必然漂移,本仓已经踩过多次):
  可建单状态 = `review_export.build_review_package` 的 `status`(与 5010 交接包同一个函数)
  类别 ID    = `expert_pool.resolve_category`(ID 优先,旧输出才按名称/别名)
  意见份数   = `expert_review_app._opinion_tally`(与队列与 `/api/expert/marks` 同一处)
  质量       = `full_context.quality_of`

用法(在 `provenance/` 下):
  python -m case01.tools.expert_demand_export                      # 写到 <记录根>/../exports
  python -m case01.tools.expert_demand_export --out D:\\somewhere  # 或指定目录
  python -m case01.tools.expert_demand_export --only-quality ok,unverified
"""
import argparse
import csv
import json
import os
import sys
import collections

if __package__ in (None, ""):                       # 脚本形态直跑也要能 import case01
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))

from case_engine.paths import data_root
from case01.expert_pool import load_expert_pool, resolve_category
from case01.full_context import quality_of
from case01.review_export import build_review_package
from live.reflections import load_marks          # `live/` 在包根,不在 case01 下

QUOTE_CHARS = 200          # 引文只作定位用;整段正文在 run.json 里,别把 CSV 撑爆
LEGACY_GEN = "无清单(10-04 前)"
# 交给外部做派工决定时只需这六列(全量列留着做复核)
LEAN_KEYS = ("run_id", "branch", "issue_id", "category", "summary", "routing_reason")
FIELDS = [
    ("run_id", "run_id"),
    ("branch", "分支"),
    ("branch_source", "分支来源"),
    ("model", "生成模型"),
    ("judge_prompt_version", "判据代次"),
    ("quality", "质量"),
    ("review_status", "可建单"),
    ("status_note", "不可建单原因"),
    ("reflection_chars", "反思字数"),
    ("issue_id", "问题号"),
    ("category", "专业类别"),
    ("category_id", "类别ID"),
    ("in_pool", "类别在池内"),
    ("secondary", "次级类别"),
    ("risk", "风险"),
    ("summary", "问题陈述句"),
    ("routing_reason", "路由理由"),
    ("evidence_ids", "证据句编号"),
    ("evidence_quote", "证据引文(截断)"),
    ("n_opinions", "已收意见份数"),
    ("note", "备注"),
]


def _model_and_gen(rec):
    m = rec.get("manifest") or {}
    return (m.get("judge_model") or "未记录",
            str(m.get("judge_prompt_version") or "")[:8] or "无清单(10-04 前)")


def _rows(pool, tally_by_task):
    root = data_root("case01.records", env_var="CASE01_RUNS_ROOT",
                     what="case01 成品记录根")
    cats = {c["id"]: c["name"] for c in pool["categories"]}
    for rid in sorted(os.listdir(root)):
        path = os.path.join(root, rid, "run.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception as exc:                       # 读坏了要说出来,不是跳过当没有
            print("[!] 读不了,跳过: {}: {}: {}".format(path, type(exc).__name__, exc))
            continue
        q = quality_of(rec)
        pkg = build_review_package(rec, rid, pool=pool)
        model, gen = _model_and_gen(rec)
        refl = ((rec.get("reflection") or {}).get("text") or "").strip()
        issues = ((rec.get("router") or {}).get("issues") or [])
        triage = {(t.get("issue_id"), t.get("reason")) for t in (pkg.get("manual_triage") or [])
                  if isinstance(t, dict)}
        base = {"run_id": rid, "branch": rec.get("branch") or "",
                "branch_source": (rec.get("branch_action") or {}).get("source") or "",
                "model": model, "judge_prompt_version": gen,
                "quality": q.get("quality") or "", "review_status": pkg.get("status") or "",
                "status_note": ";".join(pkg.get("blocked_reasons") or [])
                or ";".join(sorted({c for _i, c in triage})) or "",
                "reflection_chars": len(refl), "n_opinions": ""}
        if not issues:
            yield dict(base, issue_id="", category="", category_id="", in_pool="",
                       secondary="", risk="", summary="", routing_reason="",
                       evidence_ids="", evidence_quote="",
                       note="反思为空" if not refl else "Router 没拆出问题")
            continue
        for it in issues:
            cid = str(it.get("expert_category_id") or "").strip()
            cat = resolve_category(category_id=cid, field=it.get("field", ""), pool=pool)
            resolved = (cat or {}).get("id") or ""
            sec = ";".join(cats.get(str(x).strip().upper(), str(x))
                           for x in (it.get("secondary_expert_category_ids") or []))
            yield dict(base,
                       issue_id=it.get("id") or "",
                       category=it.get("field") or cats.get(resolved, "") or "",
                       category_id=resolved,
                       in_pool="是" if resolved else "否",
                       secondary=sec,
                       risk=it.get("risk") or "",
                       summary=(it.get("summary") or "").strip(),
                       routing_reason=(it.get("routing_reason") or it.get("risk_note") or "").strip(),
                       evidence_ids=";".join(it.get("evidence_sentence_ids") or []),
                       evidence_quote=(it.get("evidence_quote") or "").replace("\n", " ")[:QUOTE_CHARS],
                       n_opinions=tally_by_task.get((rid, str(it.get("id")), resolved), 0),
                       note="" if resolved else "类别名不在当前池(派工时会被漏掉)")


def summarize(rows):
    agg = collections.defaultdict(lambda: {"issues": 0, "high": 0, "runs": set(),
                                           "buildable": 0})
    for r in rows:
        key = (r["category"] or "(无类别)", r["judge_prompt_version"])
        a = agg[key]
        if not r["issue_id"]:
            continue
        a["issues"] += 1
        a["runs"].add(r["run_id"])
        a["high"] += (r["risk"] == "high")
        a["buildable"] += (r["review_status"] == "ready")
    out = []
    for (cat, gen), a in agg.items():
        out.append({"专业类别": cat, "判据代次": gen, "issue 数": a["issues"],
                    "high 风险": a["high"], "涉及记录数": len(a["runs"]),
                    "其中可建单(ready)": a["buildable"]})
    out.sort(key=lambda r: (-r["issue 数"], r["专业类别"]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="导出 case01 反思/问题 → 专家派工 CSV(只读)")
    ap.add_argument("--out", default="", help="输出目录(默认 <成品记录根>/../exports)")
    ap.add_argument("--only-quality", default="",
                    help="只留这些质量值,逗号分隔(如 ok,unverified);默认全给并带上质量列")
    ap.add_argument("--lean", action="store_true",
                    help="只出派工要用的六列(run_id/分支/问题号/专业类别/问题陈述句/路由理由)")
    ap.add_argument("--drop-legacy", action="store_true",
                    help="丢掉没有运行清单段(10-04 前)的记录:那批类别口径未收敛,派工会被带偏")
    args = ap.parse_args(argv)

    root = data_root("case01.records", env_var="CASE01_RUNS_ROOT", what="case01 成品记录根")
    out_dir = args.out or os.path.join(os.path.dirname(root), "exports")
    os.makedirs(out_dir, exist_ok=True)
    pool = load_expert_pool()
    tally = _opinion_tally_safe()

    keep = {x.strip() for x in args.only_quality.split(",") if x.strip()}
    rows = [r for r in _rows(pool, tally) if not keep or r["quality"] in keep]
    if args.drop_legacy:
        rows = [r for r in rows if r["judge_prompt_version"] != LEGACY_GEN]
    fields = [f for f in FIELDS if not args.lean or f[0] in LEAN_KEYS]

    by_issue = os.path.join(out_dir, "expert_demand_by_issue.csv")
    with open(by_issue, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([zh for _k, zh in fields])
        for r in rows:
            w.writerow([r.get(k, "") for k, _zh in fields])
    by_cat = os.path.join(out_dir, "expert_demand_by_category.csv")
    srows = summarize(rows)
    with open(by_cat, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(srows[0].keys()))
        w.writeheader()
        w.writerows(srows)

    n_rec = len({r["run_id"] for r in rows})
    n_iss = sum(1 for r in rows if r["issue_id"])
    print("记录 {} 条 | issue {} 条 | 池外类别 {} 条".format(
        n_rec, n_iss, sum(1 for r in rows if r["in_pool"] == "否")))
    print("-> {}".format(by_issue))
    print("-> {}".format(by_cat))
    return 0


def _opinion_tally_safe():
    try:
        from case01.expert_review_app import _opinion_tally
        return _opinion_tally(load_marks())["by_task"]
    except Exception as exc:                           # 台账读不动不影响派工表主体
        print("[!] 意见台账没读到,`已收意见份数` 全记 0:{}: {}".format(
            type(exc).__name__, exc))
        return {}


if __name__ == "__main__":
    sys.exit(main())
