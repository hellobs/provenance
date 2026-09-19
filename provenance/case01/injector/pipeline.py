# -*- coding: utf-8 -*-
"""完整流水线:节点序列 -> mavis 驱动 -> case01 兼容记录 ->（可选）Reflection/Router。

用法（真实运行,需 Ollama）:
    python -m case01.injector.pipeline --branch B --reflect --out runs_injector/B.json

dry-run 不加载 mavis,只产出同构记录（CI/联调用）。
"""
import argparse
import json
import os
import sys
from typing import Optional, Tuple

from .bridge import DEFAULT_ROLES, MavisBridge
from .nodes import default_nodes
from .record import to_case01_record

DEFAULT_SCENARIO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenario")


def _attach_reflection(record: dict, llm=None, router_llm=None,
                       external_router: bool = False) -> dict:
    """在映射后的记录上补 Reflection 与 Router（复用 case01 现有实现）。"""
    from ..agents.llm import OllamaClient, OpenRouterClient
    from ..reflection import run_reflection, run_router

    local = llm or OllamaClient()
    router = router_llm or (OpenRouterClient() if external_router else local)
    ref = run_reflection(local, record)
    record["reflection"] = {"material": ref["material"], "text": ref["text"],
                            "stripped_opener": bool(ref.get("stripped_opener"))}
    rout = run_router(router, ref["text"])
    record["router"] = {"raw": rout["raw"], "issues": rout["issues"]}
    # 这两个字段已补齐,从 gaps 里移除
    gaps = [g for g in record.get("compat", {}).get("gaps", [])
            if not g.startswith("reflection/router")]
    record.setdefault("compat", {})["gaps"] = gaps
    record["compat"]["reflection_attached"] = True
    return record


def _attach_consistency(record: dict, branch_source: str = "preset") -> dict:
    """给记录盖上"分支从哪来 + AI 的 T0 立场是否与之一致"的戳(**不许静默**)。

    为什么必须在记录里:`--branch` 是预设的,Ethan 的行为由剧本强制,两者都不看 AI
    说了什么。实测 `260919-live-case01-mavis-A-1720` 里 AI 在 T0 说
    "I cannot confirm the rumour is true or that the stock is worth buying",
    而 A 线让 Ethan 满仓买入 —— 记录自相矛盾,但**记录本身完全没提这件事**。
    现在每条记录都带 `branch_action.source` 与 `consistency`,面板/专家视图也照实显示。
    """
    from ..consistency import quick_scan

    rec = dict(record)
    ba = dict(rec.get("branch_action") or {})
    ba.setdefault("source", branch_source)
    rec["branch_action"] = ba
    verdict, reason = quick_scan(rec)
    rec["consistency"] = {"verdict": verdict, "reason": reason, "method": "quick_scan",
                          "branch_source": ba.get("source", "")}
    if verdict == "inconsistent":
        print("[!] 这条记录不自洽({} 线):{}".format(rec.get("branch", ""), reason))
    elif verdict == "unknown":
        print("[!] 这条记录的 T0 立场判不了(不是通过):{}".format(reason))
    return rec


def run_pipeline(branch: str = "B", scenario_dir: str = "", run_id: str = "",
                 roles: Tuple[str, ...] = DEFAULT_ROLES, dry_run: bool = False,
                 max_retries: int = 2, reflect: bool = False,
                 external_router: bool = False, llm=None, router_llm=None,
                 out_path: str = "", raw_record: Optional[dict] = None,
                 fill_facts: bool = False, branch_source: str = "preset",
                 require_consistent: bool = False, branch_mode: str = "preset") -> dict:
    """跑一条完整流水线,返回 case01 兼容记录。

    raw_record: 直接给一份已有的 injector 原始记录(跳过驱动),用于事后映射/接反思。
    fill_facts: 对不含 world_state 的旧记录,用纯逻辑重算事实层快照。
    """
    scenario_dir = scenario_dir or DEFAULT_SCENARIO
    branch = branch or (raw_record or {}).get("branch", "B")
    run_id = run_id or (raw_record or {}).get("run_id") or "injector-{}".format(branch)

    if raw_record is not None:
        raw = raw_record
        if fill_facts:
            from .worldfacts import enrich_record_with_facts

            enrich_record_with_facts(raw, branch=branch)
    else:
        nodes = default_nodes(branch, roles=list(roles))
        bridge = MavisBridge(nodes=nodes, roles=roles, scenario_dir=scenario_dir,
                             run_id=run_id, max_retries=max_retries, dry_run=dry_run,
                             branch=branch, branch_mode=branch_mode)
        raw = bridge.run()

    # 原始记录里的分支才是**实际跑出来的**分支(judge 模式:跑完 T0 才判定;
    # 映射时必须以它为准,否则记录的 branch 字段会跟实际跑的市场世界对不上),
    # 分支来源同理(judge / preset / preset-fallback)。
    if isinstance(raw, dict):
        if raw.get("branch"):
            branch = raw["branch"]
        if raw.get("branch_source"):
            branch_source = raw["branch_source"]

    record = to_case01_record(raw, branch=branch, run_id=run_id,
                              c_plan=raw.get("c_plan") if isinstance(raw, dict) else None)
    record.setdefault("compat", {})["reflection_attached"] = False

    if reflect:
        _attach_reflection(record, llm=llm, router_llm=router_llm,
                           external_router=external_router)

    # 一致性戳:在写盘之前盖(所以文件里一定有这一节,不是"看日志才知道")
    record = _attach_consistency(record, branch_source=branch_source)
    if require_consistent and record["consistency"]["verdict"] != "consistent":
        # opt-in 的"落盘闸门":只有调用方明确要求时才拦(默认不拦,免得静默丢弃样本;
        # 而且"丢弃重跑"会引入筛选偏差 —— 只保留恰好同意 preset 的运行)
        print("[!] --require-consistent:这条记录不一致/判不了,**不写盘**:{}"
              .format(record["consistency"]["reason"]))
        return record

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    return record


def main():
    ap = argparse.ArgumentParser(description="case01 injector 完整流水线")
    ap.add_argument("--branch", default="B", choices=["A", "B", "C"])
    ap.add_argument("--roles", default="Investment AI,Ethan Lin")
    ap.add_argument("--scenario-dir", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reflect", action="store_true",
                    help="运行后调用 case01 的 Reflection + Router（需本地 LLM）")
    ap.add_argument("--external-router", action="store_true",
                    help="Router 用外部 API（需 OPENROUTER_API_KEY）")
    ap.add_argument("--out", default="")
    ap.add_argument("--from-record", default="",
                    help="对已有的 injector 原始记录做映射/接反思(不重新驱动)")
    ap.add_argument("--fill-facts", action="store_true",
                    help="对旧记录用纯逻辑补算事实层快照(world_state/world_audit)")
    ap.add_argument("--branch-source", default="preset", choices=["preset", "judge"],
                    help="分支是预设的还是由 AI 回答判定的(写进记录的 branch_action.source)")
    ap.add_argument("--branch-mode", default="preset", choices=["preset", "judge"],
                    help="judge=先跑 T0 再由 AI 回答判定分支(01 §六 的设计原意);"
                         "preset=分支由 --branch 指定(可控对照)")
    ap.add_argument("--require-consistent", action="store_true",
                    help="记录的 T0 立场与分支不一致/判不了时不写盘(默认照写但会警告)")
    args = ap.parse_args()

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    if len(roles) != 2:
        print("--roles 需要正好两个角色名")
        sys.exit(2)

    raw = None
    if args.from_record:
        with open(args.from_record, encoding="utf-8") as f:
            raw = json.load(f)
        # 容错:误把"已映射记录"当输入时,回退到其中的 injector 原始段
        if "nodes" not in raw and isinstance(raw.get("injector"), dict):
            raw = raw["injector"]

    record = run_pipeline(
        branch=args.branch, scenario_dir=args.scenario_dir, run_id=args.run_id,
        roles=roles, dry_run=args.dry_run, max_retries=args.max_retries,
        reflect=args.reflect, external_router=args.external_router, out_path=args.out,
        raw_record=raw, fill_facts=args.fill_facts,
        branch_source=args.branch_source, require_consistent=args.require_consistent,
        branch_mode=args.branch_mode,
    )
    if args.out:
        print("saved ->", args.out)
    print(json.dumps(record.get("summary") or {}, ensure_ascii=False))
    print("turns:", len(record.get("turns") or []),
          "events:", len(record.get("events") or []),
          "issues:", len((record.get("router") or {}).get("issues") or []),
          "consistency:", (record.get("consistency") or {}).get("verdict"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
