# -*- coding: utf-8 -*-
"""Router 人工校验框架:标注表导出 + 一致率评分(为 M4 专家一致率做准备)。

两步用法:
  1. 导出标注表(专家/用户逐条填 run_*.csv 的三个标注列):
       python -m case01.tools.router_review --export
  2. 评分(填完后):
       python -m case01.tools.router_review --score <填好的.csv>

标注列(人工填):
  - field_ok     : 该 issue 路由的"专业领域"是否合适(1/0)
  - expected_risk: 专家认为的风险等级(high/medium/low,不填 = 认可原标注)
  - summary_ok   : summary 是否是"行为/判断陈述句"而非疑问句、是否忠于反思原文(1/0)

评分口径(科研里的"专家一致率"):
  - 路由一致率 = mean(field_ok);风险校准 = mean(expected_risk == risk,空=认可);
  - 行为句率 = mean(summary_ok);总分 = 三者均值。
  导出表带 instructions 行,Excel 打开即可标。
"""
import csv
import glob
import json
import os
import sys

CK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
RUNS = os.path.join(CK, "case01", "runs")
OUT = os.path.join(CK, "results", "analysis", "router_review")

FIELDS = ["run_id", "issue_id", "summary", "field", "risk", "risk_note",
          "field_ok", "expected_risk", "summary_ok", "note"]


def export() -> int:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "router_review_sheet.csv")
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerow(["# 填法", "",
                    "summary_ok/field_ok 填 1(认可) 或 0(不认可)",
                    "expected_risk 填 high/medium/low(认可原标注就留空)",
                    "", "", "", "", "", ""])
        for p in sorted(glob.glob(os.path.join(RUNS, "*", "run.json")), reverse=True):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            rid = d.get("run_id") or os.path.basename(os.path.dirname(p))
            for i in (d.get("router") or {}).get("issues") or []:
                w.writerow([rid, i.get("id", ""),
                            i.get("summary", ""), i.get("field", ""),
                            str(i.get("risk", "")), i.get("risk_note", ""),
                            "", "", "", ""])
                n += 1
    print("导出 {} 条 issue -> {}".format(n, path))
    return 0


def score(path: str) -> int:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if not (r.get("run_id") or "").startswith("#")]
    labelled = [r for r in rows if (r.get("field_ok") or "").strip()
                or (r.get("expected_risk") or "").strip()
                or (r.get("summary_ok") or "").strip()]
    if not labelled:
        print("标注表还没有人工标注(列 field_ok/expected_risk/summary_ok 全空):", path)
        return 2
    field_ok = [r["field_ok"].strip() == "1" for r in labelled if (r.get("field_ok") or "").strip()]
    summary_ok = [r["summary_ok"].strip() == "1" for r in labelled if (r.get("summary_ok") or "").strip()]
    risk_ok = []
    for r in labelled:
        er = (r.get("expected_risk") or "").strip().lower()
        if er:
            risk_ok.append(er == (r.get("risk") or "").strip().lower())
    def _mean(xs):
        return round(sum(xs) / len(xs), 4) if xs else None
    result = {
        "labelled": len(labelled),
        "total": len(rows),
        "routing_agreement": _mean(field_ok),
        "summary_behavior_rate": _mean(summary_ok),
        "risk_calibration": _mean(risk_ok),
        "overall": _mean([x for x in (_mean(field_ok), _mean(summary_ok), _mean(risk_ok))
                          if x is not None]),
    }
    out = os.path.join(os.path.dirname(path), "router_review_score.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("-> ", out)
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Router 人工校验:标注表导出/评分")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--score", default="", help="评分:填好的 csv 路径")
    args = ap.parse_args()
    if args.score:
        return score(args.score)
    return export()


if __name__ == "__main__":
    sys.exit(main())
