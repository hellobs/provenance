# -*- coding: utf-8 -*-
"""CLI:跑一次完整 GTC Case 01 Run(T0 → Branch → Timeline → 最终反馈)。

用法:
    python -m case01.run                       # 自动判定 Branch
    python -m case01.run --timeline A          # 强制 Branch A
    python -m case01.run --no-llm              # 不调 LLM(固定文本,测全链路)
    python -m case01.run --run-id my-run
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.orchestrator import run_case01


def main():
    ap = argparse.ArgumentParser(description="GTC Case 01 完整 Run")
    ap.add_argument("--timeline", choices=["A", "B", "C"], default=None,
                    help="强制 Branch(跳过自动判定)")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--no-llm", action="store_true",
                    help="不调 LLM(固定文本,测全链路)")
    ap.add_argument("--external-ethan", action="store_true",
                    help="Ethan/Router 用 OpenRouter 外部 API(需 key 配置);"
                         "缺省时与 Investment AI 同用本地 Ollama")
    ap.add_argument("--reflect-only", action="store_true",
                    help="只对已存在的 run 触发 Reflection+Router(不重跑对话)")
    args = ap.parse_args()

    if args.reflect_only:
        from case01.orchestrator import RUNS_ROOT
        import json as _json
        run_id = args.run_id
        p = os.path.join(RUNS_ROOT(), run_id, "run.json")
        if not os.path.exists(p):
            print("run not found:", run_id)
            sys.exit(1)
        rec_data = _json.load(open(p, encoding="utf-8"))
        from case01.agents.llm import OllamaClient, OpenRouterClient
        from case01.reflection import run_reflection, run_router
        local = OllamaClient()
        router = OpenRouterClient() if args.external_ethan else local
        print("=== Reflection ===")
        ref = run_reflection(local, rec_data)
        rec_data["reflection"] = {"material": ref["material"],
                                  "text": ref["text"]}
        print(ref["text"][:500])
        print("\n=== Router ===")
        rout = run_router(router, ref["text"])
        rec_data["router"] = {"raw": rout["raw"], "issues": rout["issues"]}
        print("issues:", len(rout["issues"]))
        for i in rout["issues"]:
            print(" -", i)
        with open(p, "w", encoding="utf-8") as f:
            _json.dump(rec_data, f, ensure_ascii=False, indent=2)
        print("updated ->", p)
        return

    ethan_llm = router_llm = None
    if args.external_ethan and not args.no_llm:
        from case01.agents.llm import OpenRouterClient
        ethan_llm = OpenRouterClient()
        router_llm = OpenRouterClient()

    run_case01(llm=None, timeline=args.timeline, run_id=args.run_id,
               no_llm=args.no_llm, ethan_llm=ethan_llm,
               router_llm=router_llm)


if __name__ == "__main__":
    main()
