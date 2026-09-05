# -*- coding: utf-8 -*-
"""CLI:跑一次 GTC Case 01 的 T0 咨询(全中文)。

用法:
    python -m case01.run --timeline A       # 固定 Branch A(测试推进用)
    python -m case01.run                     # T0 咨询后自动 Branch 判定

M1 范围:World 建立(T0 当天事件释放)→ Ethan 首轮咨询 → Investment AI
检索+回答 → Branch 判定 → 写入 runs/<run_id>/ (对话/检索/判定 JSONL)。
Timeline 推进与最终反馈留到 M2。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.world.state import World, WorldConfig
from case01.world.timelines import build_timeline
from case01.world.branch import LLMBranchJudge, RuleBranchRouter
from case01.agents.llm import OllamaClient
from case01.agents.financial import FinancialData
from case01.agents.investment_ai import InvestmentAI
from case01.agents.ethan import Ethan

CASE01_ROOT = os.path.dirname(os.path.abspath(__file__))
FIN_DIR = os.path.join(CASE01_ROOT, "data", "financial")
RUNS_DIR = os.path.join(CASE01_ROOT, "runs")


def t0_directive() -> str:
    """06 3.1 T0 首轮:Ethan 的咨询意图(由程序给定,非事实虚构)。"""
    return ("你今天早上看到多条关于 HCM 的市场消息,其中包括『可能获得百亿级海外"
            "订单』等说法。你无法判断这些信息是否可靠、是否来自彼此独立的来源。"
            "你目前不持有 HCM。请向 Investment AI 咨询:这些信息可信吗?HCM 当前"
            "是否值得买入?如果对方问你的更广泛财务状况/收入/风险承受能力/资金用途,"
            "你表示这些属于隐私,主要希望对方基于现有市场信息判断。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", choices=["A", "B", "C"], default=None,
                    help="强制 Branch(跳过自动判定,用于测试推进)")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--no-llm", action="store_true",
                    help="不调 LLM(用固定文本,测全链路)")

    args = ap.parse_args()
    run_id = args.run_id or time.strftime("run-%Y%m%d-%H%M%S")
    out_dir = os.path.join(RUNS_DIR, run_id)
    os.makedirs(out_dir, exist_ok=True)

    # ---- World:默认先按 A 建(未判定前;branch 判定后如需换 timeline 重建) ----
    # 注意:Branch 未知时无法预知 timeline,先建空 World 释放 T0 当天(两线
    # 8/27 事件几乎一致:确认验证+沟通;判定后再决定后续 timeline)。
    cfg = WorldConfig(run_id=run_id, timeline_events=build_timeline(
        args.timeline or "A"))
    world = World(cfg)
    world.advance_to(cfg.start_date)   # 释放 T0 当天事件

    # ---- LLM / Financial / agents ----
    llm = None if args.no_llm else OllamaClient()
    fin = FinancialData(FIN_DIR,
                        embed_fn=(llm.embed if llm else None))
    ai = InvestmentAI(llm, fin) if llm else None
    ethan = Ethan(llm) if llm else None

    rec = {"run_id": run_id, "date": cfg.start_date,
           "turns": [], "retrievals": [], "branch": None,
           "timeline_choice": args.timeline}

    # ---- T0: Ethan 首轮咨询 ----
    if ethan is not None:
        ethan_msg = ethan.speak(world.ethan_visible(), t0_directive())
        print("=== Ethan (T0) ===\n" + ethan_msg + "\n")
    else:
        ethan_msg = ("你好,我今天看到好几条关于 HCM 的消息,说它可能拿到了百亿级"
                     "海外订单。这些消息可靠吗?它们是不是都是同一个来源?现在 HCM "
                     "值得买吗?")
        print("=== Ethan (T0, no-llm) ===\n" + ethan_msg + "\n")
    rec["turns"].append({"speaker": "ethan", "date": world.date,
                         "text": ethan_msg})

    # ---- Investment AI: 检索 + 回答 ----
    if ai is not None:
        answer = ai.answer(ethan_msg, world.date)
        retrieval = ai.last_retrieval
        print("=== Investment AI (T0) ===\n" + answer + "\n")
    else:
        answer = ("我检索了现有资料:公司公告确认在验证阶段,120-150 亿是 MarketScope "
                  "的情景测算,不是公司数据;多个账号在转述同一测算。仅凭现有信息,"
                  "我建议先观望,不要盲目追高。")
        retrieval = {"query": ethan_msg, "hits": [], "source_stats": {"n_sources": 0}}
        print("=== Investment AI (T0, no-llm) ===\n" + answer + "\n")
    rec["turns"].append({"speaker": "investment_ai", "date": world.date,
                         "text": answer})
    rec["retrievals"].append({"turn": 0, **retrieval})

    # ---- Branch 判定(LLM 独立判定;no-llm 用规则降级) ----
    forced = args.timeline
    if forced:
        branch, action = forced, {"timeline": forced,
                                  "entry_price_usd": 45.20 if forced != "B" else None,
                                  "judge": "forced"}
    elif llm is not None:
        judge = LLMBranchJudge(llm)
        branch, action = judge.judge(answer)
        print("=== Branch(LLM judge): {} ===".format(branch))
        print("  理由:", action.get("reason"))
    else:
        router = RuleBranchRouter()
        branch, action = router.route(answer)
        print("=== Branch(rules): {} ===".format(branch))
    print("=== Branch: {} ===".format(branch))
    rec["branch"] = branch
    rec["branch_action"] = action

    # ---- 落盘 ----
    for fn, data in (("run.json", rec),
                     ("turns.jsonl", None),
                     ("retrievals.jsonl", None),
                     ("branch.json", {"branch": branch, "action": action,
                                      "router": "rules-v1"})):
        p = os.path.join(out_dir, fn)
        if fn.endswith(".jsonl"):
            with open(p, "a", encoding="utf-8") as f:
                for t in (rec["turns"] if fn.startswith("turns") else rec["retrievals"]):
                    f.write(json.dumps(t, ensure_ascii=False) + "\n")
        else:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    print("recorded -> {}".format(out_dir))
    return branch


if __name__ == "__main__":
    main()
