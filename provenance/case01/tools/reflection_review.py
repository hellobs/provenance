# -*- coding: utf-8 -*-
"""反思质量人工标注框架:八维覆盖 / 切题度 / 忠实度(补"反思生成质量"的人工证据)。

用法(与 router_review 同一套路):
  1. 导出标注表:  python -m case01.tools.reflection_review --export
  2. 评分:        python -m case01.tools.reflection_review --score <填好的.csv>

标注列(人工填,每条反思一行):
  - dim1..dim8 : 八个反思维度是否被实质覆盖(1/0)。维度定义见 instructions 行,
    与 GTC 06 文档第 6 节的八维一致(判断质量/信息与证据/假设与不确定性/
    利益与立场/行动与后果/结果与过程/需要帮助的问题/学到什么);
  - relevance  : 切题度 1-5(5 = 全程针对本次具体事件,1 = 泛泛而谈);
  - fidelity   : 忠实度 1/0(1 = 未编造记录中不存在的事实);
  - style_ok   : 1/0(1 = 无客套开头、无尾部追问、无 emoji)。

评分口径:平均八维覆盖数、切题度均值、忠实率、样式合格率——即"反思生成质量"
的人工证据;M4 专家审核接入后可用同一框架复测。
"""
import csv
import glob
import json
import os
import sys

CK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
RUNS = os.path.join(CK, "case01", "runs")
OUT = os.path.join(CK, "results", "analysis", "reflection_review")

DIMS = ["判断质量(哪些合理/哪些值得质疑)", "信息与证据(是否充分/一致/缺什么)",
        "假设与不确定性(哪里做了假设,依据多大)", "利益与立场(利益相关方差异/冲突)",
        "行动与后果(直接/间接/长期后果)", "结果与判断质量(区分运气与过程)",
        "需要进一步帮助的问题(超出可靠判断范围的)", "学到什么(保留/重新考虑什么)"]

FIELDS = (["run_id", "text_len"] + ["dim{}".format(i) for i in range(1, 9)]
          + ["relevance", "fidelity", "style_ok", "note"])


def export() -> int:
    from case01.reflection import _strip_boilerplate, _strip_tail_offer, _strip_emoji
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "reflection_review_sheet.csv")
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerow(["# 八维定义"] + DIMS + ["切题度 1-5", "1=未编造事实", "1=无客套/追问/emoji", ""])
        for p in sorted(glob.glob(os.path.join(RUNS, "*", "run.json")), reverse=True):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            rid = d.get("run_id") or os.path.basename(os.path.dirname(p))
            text = (d.get("reflection") or {}).get("text") or ""
            if not text:
                continue
            style_ok = "1" if (_strip_boilerplate(text) == text
                               and _strip_tail_offer(text) == text
                               and _strip_emoji(text) == text) else "0"
            w.writerow([rid, len(text)] + [""] * 8 + ["", "", style_ok, ""])
            n += 1
    print("导出 {} 条反思 -> {}".format(n, path))
    return 0


def score(path: str) -> int:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if not (r.get("run_id") or "").startswith("#")]
    labelled = [r for r in rows if any((r.get("dim{}".format(i)) or "").strip()
                                       for i in range(1, 9))]
    if not labelled:
        print("标注表还没有人工标注(dim1..dim8 全空):", path)
        return 2
    cov = []
    for r in labelled:
        cov.append(sum(1 for i in range(1, 9)
                       if (r.get("dim{}".format(i)) or "").strip() == "1"))
    rel = [float(r["relevance"]) for r in labelled
           if (r.get("relevance") or "").strip()]
    fid = [r["fidelity"].strip() == "1" for r in labelled
           if (r.get("fidelity") or "").strip()]
    sty = [r["style_ok"].strip() == "1" for r in labelled
           if (r.get("style_ok") or "").strip()]

    def _mean(xs):
        return round(sum(xs) / len(xs), 3) if xs else None
    result = {
        "labelled": len(labelled), "total": len(rows),
        "mean_dims_covered": _mean(cov),
        "n_full_coverage": sum(1 for c in cov if c == 8),
        "n_below_six": sum(1 for c in cov if c < 6),
        "mean_relevance": _mean(rel),
        "fidelity_rate": _mean(fid),
        "style_ok_rate": _mean(sty),
    }
    out = os.path.join(os.path.dirname(path), "reflection_review_score.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("-> ", out)
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="反思质量:标注表导出/评分")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--score", default="", help="评分:填好的 csv 路径")
    args = ap.parse_args()
    if args.score:
        return score(args.score)
    return export()


if __name__ == "__main__":
    sys.exit(main())
