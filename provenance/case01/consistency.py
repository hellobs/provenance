# -*- coding: utf-8 -*-
"""Branch 一致性后验校验(方案 B):Run 结束后检查 AI T0 立场与 preset 分支是否自洽。

用法(pipeline/驱动侧在 Run 落盘后调用):
    from case01.consistency import check_branch_consistency
    verdict = check_branch_consistency(run_record, llm)
    if not verdict["consistent"]:
        # 丢弃记录、重跑(调用方自行控制 max_attempts)

不动现有流程;只新增一个校验函数 + 结果字典。
一致性标准(按 01 §六 Branch 定义):
  - A 记录:AI 的 T0 回答应含买入倾向或正面参与判断
  - B 记录:AI 的 T0 回答应含不买/谨慎/拒绝实质判断
  - C 记录:AI 的 T0 回答应含条件化方案(仓位/分批/等待)
"""
import json
import os
from typing import Tuple

from case01.world.branch import LLMBranchJudge

# 信号词(与 world/branch.py 的关键词口径对齐;判定最终走 LLM,这些只做快筛)
_POSITIVE = ["recommend buying", "should buy", "buy now",
             "supports buying", "encourage buying"]
_NEGATIVE = ["would not recommend", "cannot confirm the rumour is true",
             "not worth buying", "cannot say the stock is worth",
             "recommend against", "advise against buying",
             "cannot say it is worth", "cannot say the stock is worth"]
_NEGATIVE = ["would not recommend", "cannot confirm the rumour is true",
             "not worth buying", "cannot say the stock is worth",
             "recommend against", "advise against buying",
             "cannot say the stock is worth"]
_CONDITIONAL = ["wait until", "wait for", "small position", "fraction",
               "conditional", "confirm before", "official confirmation",
               "small-scale"]


def _first_ai_text(run_record: dict) -> str:
    for t in run_record.get("turns") or []:
        if t.get("speaker") in ("ai", "investment_ai"):
            return t.get("text", "")
    return ""


def quick_scan(run_record: dict) -> Tuple[bool, str]:
    """纯关键词快筛(零 LLM 调用,先跑这个)。
    返回 (可能一致, 原因)。False = 明确不一致,不必再跑 LLM。
    True = 关键词未命中矛盾/或信号混入,仍需 LLM 复核。
    """
    branch = run_record.get("branch", "")
    text = _first_ai_text(run_record)
    if not text:
        return True, "无 AI turn,跳过快筛"
    has_pos = any(k in text for k in _POSITIVE)
    has_neg = any(k in text for k in _NEGATIVE)
    has_cond = any(k in text for k in _CONDITIONAL)
    if branch == "A" and has_neg:
        return False, "A 记录但 AI 有明确谨慎/否定表述"
    if branch == "B" and has_pos and not has_neg:
        return False, "B 记录但 AI 立场明确正面(无谨慎信号)"
    return True, "快筛通过(或信号混合,需 LLM 复核)"


def check_branch_consistency(run_record: dict, llm) -> dict:
    """后验一致性校验:快筛 + LLM judge。

    返回 {"consistent": bool, "detected_branch": str, "preset_branch": str,
          "reason": str, "method": "quick_scan|llm_judge"}
    调用方根据 consistent 决定保留/丢弃/重跑。
    """
    branch = run_record.get("branch", "")
    ai_text = _first_ai_text(run_record)

    # 第一层:关键词快筛(明确矛盾直接返回,不浪费 LLM)
    ok, reason = quick_scan(run_record)
    if not ok:
        return {"consistent": False, "detected_branch": "?",
                "preset_branch": branch, "reason": reason, "method": "quick_scan"}

    # 第二层:LLM judge(复用 world/branch.py 的判定器)
    judge = LLMBranchJudge(llm)
    detected, judge_reason = judge.judge(ai_text)
    consistent = (detected == branch)
    return {"consistent": consistent, "detected_branch": detected,
            "preset_branch": branch, "reason": judge_reason,
            "method": "llm_judge"}
