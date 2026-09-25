# -*- coding: utf-8 -*-
"""Router 敏感性分析:同一反思文本,本地 qwen3 vs 外部 nemotron 的分流是否稳定。

回答的问题:**换个判定模型,结论稳不稳?**(科研答辩的鲁棒性素材)

方法(2026-09-24 与用户商定):
- 取 N 条成品记录的反思正文(同一文本喂给两个 Router);
- 每个 Router 各跑 R 次(temperature 固定 ROUTER_TEMPERATURE);
- 对比指标:
  - 条数稳定性:各次产出的 issue 数;
  - 风险分布:risk ∈ {high, medium, low} 计数;
  - 跨模型语义重合:对 A 侧每条 summary,与 B 侧所有 summary 算字符 bigram
    Jaccard,取最大值再平均(cross-model match ∈ [0,1];>0.5 视为大体对应);
- 输出 JSON + Markdown 到 results/analysis/router_sensitivity/。
  原始输出逐条存档,便于人工复核(工具 3 的标注表可以基于它)。

诚实边界:bigram Jaccard 是粗粒度词汇重合,不是语义等价;结论以"条数/风险分布
稳定 + 重合度较高"为鲁棒性证据,不做过度解读。原始输出随报告存档可人工复核。
"""
import json
import os
import sys
from collections import Counter
from typing import Dict, List

CK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
RUNS = os.path.join(CK, "case01", "runs")
OUT_ROOT = os.path.join(CK, "results", "analysis", "router_sensitivity")


def _bigrams(s: str) -> set:
    s = "".join(ch for ch in (s or "") if not ch.isspace())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def cross_match(summaries_a: List[str], summaries_b: List[str]) -> float:
    """A 侧每条对 B 侧最大 bigram-Jaccard 的平均;任一侧为空记 0。"""
    if not summaries_a or not summaries_b:
        return 0.0
    bs = [_bigrams(x) for x in summaries_b]
    vals = [max((jaccard(_bigrams(x), y) for y in bs), default=0.0) for x in summaries_a]
    return round(sum(vals) / len(vals), 4)


def risk_dist(issues: List[dict]) -> Dict[str, int]:
    c = Counter(str(i.get("risk", "")).lower() for i in issues)
    return {k: c.get(k, 0) for k in ("high", "medium", "low", "") if c.get(k, 0)}


def pick_reflections(n: int, run_ids: List[str] = None) -> List[dict]:
    """取有反思正文的成品记录(默认最新 N 条;可显式指定 run_id)。"""
    import glob
    out = []
    for p in sorted(glob.glob(os.path.join(RUNS, "*", "run.json")), reverse=True):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        text = (d.get("reflection") or {}).get("text") or ""
        if len(text) >= 300:
            out.append({"run_id": d.get("run_id") or os.path.basename(os.path.dirname(p)),
                        "text": text})
        if len(out) >= n:
            break
    return out


def analyse(reflections: List[dict], repeats: int = 1,
            use_external: bool = True, max_tokens: int = 4096) -> dict:
    from case01.agents.llm import OpenRouterClient, local_client_from_env
    from case01.reflection import run_router

    local = local_client_from_env()
    external = OpenRouterClient() if use_external else None
    models = {"local({})".format(getattr(local, "chat_model", "ollama")): local}
    if external is not None:
        models["external({})".format(external.chat_model)] = external

    rows = []
    for ref in reflections:
        entry = {"run_id": ref["run_id"], "runs": {}}
        for name, llm in models.items():
            reps = []
            for r in range(repeats):
                try:
                    res = run_router(llm, ref["text"], max_tokens=max_tokens)
                    reps.append({
                        "n_issues": len(res["issues"]),
                        "risk_dist": risk_dist(res["issues"]),
                        "issues": [{"summary": i.get("summary", ""),
                                    "field": i.get("field", ""),
                                    "risk": i.get("risk", "")} for i in res["issues"]],
                        "raw": res["raw"],
                        "error": "",
                    })
                    print("[{}] {} 第{}次: {} 条".format(ref["run_id"], name, r + 1,
                                                        len(res["issues"])), flush=True)
                except Exception as exc:  # noqa: BLE001 —— 单次失败留痕继续,不中断整体
                    reps.append({"n_issues": None, "risk_dist": {}, "issues": [],
                                 "raw": "", "error": "{}: {}".format(type(exc).__name__, exc)})
                    print("[{}] {} 第{}次失败: {}".format(ref["run_id"], name, r + 1, exc),
                          flush=True)
            entry["runs"][name] = reps
        names = list(entry["runs"])
        if len(names) == 2:
            a, b = (entry["runs"][names[0]], entry["runs"][names[1]])
            entry["cross_model_match"] = cross_match(
                [i["summary"] for rep in a for i in rep["issues"] if not rep["error"]],
                [i["summary"] for rep in b for i in rep["issues"] if not rep["error"]])
            entry["count_local"] = [rep["n_issues"] for rep in a]
            entry["count_external"] = [rep["n_issues"] for rep in b]
        rows.append(entry)
    return {"rows": rows}


def _md(result: dict) -> str:
    lines = ["# Router 敏感性 — 同一反思 × 双模型", "",
             "| 反思来源 | 本地条数 | 外部条数 | 跨模型重合 | 本地风险分布 | 外部风险分布 |",
             "|---|---|---|---|---|---|"]
    for row in result["rows"]:
        nm = list(row["runs"])
        def _rd(name):
            reps = row["runs"].get(name) or [{}]
            return json.dumps(reps[0].get("risk_dist", {}), ensure_ascii=False)
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            row["run_id"], row.get("count_local"), row.get("count_external"),
            row.get("cross_model_match"), _rd(nm[0]), _rd(nm[-1]) if len(nm) > 1 else ""))
    lines += ["", "> 重合度 = 双方 issue summary 的字符 bigram Jaccard 最大匹配均值,"
              "粗粒度词汇重合;原始输出在同名 JSON 里,可人工复核。"]
    return "\n".join(lines) + "\n"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Router 敏感性:同反思双模型对照")
    ap.add_argument("--n", type=int, default=5, help="取最近 N 条有反思的记录")
    ap.add_argument("--repeats", type=int, default=1, help="每个模型重复次数")
    ap.add_argument("--max-tokens", type=int, default=8192,
                    help="Router 输出上限(nemotron 啰嗦,默认给足;截断会 0 条)")
    ap.add_argument("--runs", default="", help="逗号分隔的 run_id 列表(缺省取最近 N 条)")
    ap.add_argument("--no-external", action="store_true", help="只跑本地")
    ap.add_argument("--out-root", default=OUT_ROOT)
    args = ap.parse_args()

    ids = [x.strip() for x in args.runs.split(",") if x.strip()]
    refs = pick_reflections(args.n, ids)
    if not refs:
        print("没有可用的反思文本(需要 len>=300)")
        return 2
    print("反思来源: {}".format([x["run_id"] for x in refs]), flush=True)
    result = analyse(refs, repeats=args.repeats, use_external=not args.no_external,
                     max_tokens=args.max_tokens)
    os.makedirs(args.out_root, exist_ok=True)
    with open(os.path.join(args.out_root, "router_sensitivity.json"), "w",
              encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.out_root, "router_sensitivity.md"), "w", encoding="utf-8") as f:
        f.write(_md(result))
    print("-> {}\\router_sensitivity.{{json,md}}".format(args.out_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
