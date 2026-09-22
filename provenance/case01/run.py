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
    ap.add_argument("--local-hf", action="store_true",
                    help="直接加载本地 HuggingFace 权重,替代 Ollama 服务")
    ap.add_argument("--hf-chat-model",
                    default=os.environ.get("CASE01_HF_CHAT_MODEL", ""),
                    help="本地 chat 模型目录;也可用 CASE01_HF_CHAT_MODEL")
    ap.add_argument("--hf-embed-model",
                    default=os.environ.get("CASE01_HF_EMBED_MODEL", ""),
                    help="本地 embedding 模型目录;也可用 CASE01_HF_EMBED_MODEL")
    ap.add_argument("--hf-device", default=os.environ.get("CASE01_HF_DEVICE", ""),
                    help="本地 HF 推理设备,如 cuda/cuda:0/cpu")
    ap.add_argument("--reflect-only", action="store_true",
                    help="只对已存在的 run 触发 Reflection+Router(不重跑对话)")
    args = ap.parse_args()

    def _local_hf_client():
        if not args.hf_chat_model:
            print("missing --hf-chat-model or CASE01_HF_CHAT_MODEL")
            sys.exit(2)
        from case01.agents.llm import LocalHFClient
        return LocalHFClient(
            chat_model_path=args.hf_chat_model,
            embed_model_path=args.hf_embed_model,
            device=args.hf_device,
        )

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
        local = _local_hf_client() if args.local_hf else OllamaClient()
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
        # 原子写:这里是**就地更新已有成品记录**的路径,半截 JSON 等于把原记录毁掉。
        from case01.atomicio import write_json_atomic
        write_json_atomic(p, rec_data)
        print("updated ->", p)
        return

    local_llm = _local_hf_client() if args.local_hf and not args.no_llm else None
    ethan_llm = router_llm = None
    if args.external_ethan and not args.no_llm:
        from case01.agents.llm import OpenRouterClient
        ethan_llm = OpenRouterClient()
        router_llm = OpenRouterClient()

    run_case01(llm=local_llm, timeline=args.timeline, run_id=args.run_id,
               no_llm=args.no_llm, ethan_llm=ethan_llm,
               router_llm=router_llm)


if __name__ == "__main__":
    main()
