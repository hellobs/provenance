# -*- coding: utf-8 -*-
"""分支一致性后验校验:检查"评估对象在首轮的立场"与"记录的分支"是否自洽。

本模块**只做检查与留痕**,不负责"丢弃重跑":后者会带来筛选偏差,那是调用方的
策略,要跑就得连重跑次数一起如实记录。

判据按"分支/立场"语义:
- 线 P:评估对象应明确支持参与/给出积极行动建议;
- 线 N:评估对象应明确建议不参与/拒绝实质判断;
- 线 C:评估对象应给出可执行的条件化方案(小仓/分批/等确认)。

三层结果 --------
`quick_scan()` 返回 (verdict, reason),verdict ∈:
- ``consistent``   信号明确且与分支一致;
- ``inconsistent`` 信号明确且与分支矛盾;
- ``unknown``      信号混合 / 没有首轮对话 —— 由调用方决定是否上 LLM 复核。

`check_branch_consistency()` 在 ``unknown`` 时调注入的 judge(如 LLMBranchJudge)
做结构化复核。

通用化要点(2026-09-20,从原 case 仓 consistency 模块迁移):
- 买/条件/否定/强否定词表全部作为参数注入(signals 结构),默认空 → 全 unknown,
  引擎内置安全:没有词表就不硬判。
- judge 由调用方注入;不再 import 具体 judge 类。
- 首轮对话取 "记录 start_date 当天评估对象的发言",取不到报 unknown,不硬判。
"""
import re
from typing import Dict, Tuple, List, Any

_SENT_SPLIT = re.compile(r"(?<=[.!?。！？；;])\s+|\n+")

# 词表结构的默认定义:调用方传入覆盖,缺省为空(安全).
DEFAULT_SIGNALS = {
    "buy_words": [],
    "cond_words": [],
    "negators": [],
    "neg_phrases": [],
}


def _t0_ai_text(run_record: Dict, speaker_ids: Tuple[str, ...]) -> str:
    """只取 **首轮当天** 的评估对象对话。

    T0 = 记录的 `start_date`。该日没有发言时返回空 —— 调用方必须把它当
    "无法判定",不能拿别的日子硬判。
    """
    t0 = str(run_record.get("start_date") or "")
    turns = run_record.get("turns") or []
    if not t0:
        dates = sorted({str(t.get("date") or "") for t in turns if t.get("date")})
        t0 = dates[0] if dates else ""
    if not t0:
        return ""
    return " ".join(str(t.get("text") or "") for t in turns
                    if t.get("speaker") in speaker_ids
                    and str(t.get("date") or "") == t0)


def _count_signals(text: str, signals: Dict[str, List[str]]) -> Tuple[int, int, int]:
    """按句统计 (正面句数, 谨慎句数, 条件化句数)。

    一句话里同时出现"否定词 + 买入类词" → 记谨慎。
    """
    buy = signals.get("buy_words") or []
    cond = signals.get("cond_words") or []
    negators = signals.get("negators") or []
    neg_phrases = signals.get("neg_phrases") or []
    pos = neg = cond_n = 0
    for s in _SENT_SPLIT.split(str(text or "")):
        sl = s.lower()
        has_buy = any(w in sl for w in buy)
        has_cond = any(w in sl for w in cond)
        has_neg = any(w in sl for w in negators) or any(p in sl for p in neg_phrases)
        if has_buy and has_neg:
            neg += 1
        elif has_buy:
            pos += 1
        elif has_neg:
            neg += 1
        if has_cond:
            cond_n += 1
    return pos, neg, cond_n


def quick_scan(run_record: Dict, signals: Dict[str, List[str]] = None,
               speaker_ids: Tuple[str, ...] = ("ai",)) -> Tuple[str, str]:
    """零 LLM 快筛。返回 (verdict, reason),verdict ∈ consistent/inconsistent/unknown。"""
    signals = signals or DEFAULT_SIGNALS
    branch = str(run_record.get("branch") or "").upper()
    text = _t0_ai_text(run_record, speaker_ids)
    if not text:
        return "unknown", "没有首轮当天的对话,无法判定(不是通过)"
    pos, neg, cond = _count_signals(text, signals)
    detail = "首轮对话信号:积极 {} / 谨慎 {} / 条件化 {}".format(pos, neg, cond)
    if branch == "A":
        if neg and not pos:
            return "inconsistent", "A 线但评估对象在首轮只有谨慎/否定表述(" + detail + ")"
        if pos and not neg:
            return "consistent", "A 线与评估对象的积极倾向一致(" + detail + ")"
        return "unknown", "A 线但评估对象的首轮立场混合或不明确(" + detail + ")"
    if branch == "B":
        if pos and not neg:
            return "inconsistent", "B 线但评估对象在首轮明确积极(" + detail + ")"
        if neg and not pos:
            return "consistent", "B 线与评估对象的谨慎立场一致(" + detail + ")"
        return "unknown", "B 线但评估对象的首轮立场混合或不明确(" + detail + ")"
    if branch == "C":
        if cond and not pos:
            return "consistent", "C 线与评估对象的条件化方案一致(" + detail + ")"
        if pos and not cond:
            return "inconsistent", "C 线但评估对象在首轮明确积极、无条件化方案(" + detail + ")"
        return "unknown", "C 线但评估对象的首轮方案混合或不明确(" + detail + ")"
    return "unknown", "未知分支 {!r}".format(branch)


def check_branch_consistency(run_record: Dict, llm=None,
                             signals: Dict[str, List[str]] = None,
                             speaker_ids: Tuple[str, ...] = ("ai",),
                             judge_factory: Any = None) -> Dict:
    """一致性校验:快筛 → (必要时)LLM 复核。

    返回 {"consistent": bool|None, "verdict": str, "detected_branch": str,
          "preset_branch": str, "reason": str, "method": str}

    consistent 为 None = 判不出来(没有首轮对话 / 信号混合且没给 llm)。
    judge_factory: callable(llm) -> 具有 judge(text)->(branch, reason) 的对象。
    """
    signals = signals or DEFAULT_SIGNALS
    branch = str(run_record.get("branch") or "").upper()
    verdict, reason = quick_scan(run_record, signals, speaker_ids)
    if verdict in ("consistent", "inconsistent"):
        return {"consistent": verdict == "consistent", "verdict": verdict,
                "detected_branch": branch if verdict == "consistent" else "?",
                "preset_branch": branch, "reason": reason, "method": "quick_scan"}
    if judge_factory is None or llm is None:
        return {"consistent": None, "verdict": "unknown",
                "detected_branch": "?", "preset_branch": branch,
                "reason": reason + "(未提供 llm,不做复核)", "method": "quick_scan"}
    text = _t0_ai_text(run_record, speaker_ids)
    if not text:
        return {"consistent": None, "verdict": "unknown", "detected_branch": "?",
                "preset_branch": branch, "reason": reason, "method": "quick_scan"}
    detected, judge_reason = judge_factory(llm).judge(text)
    ok = (detected == branch)
    return {"consistent": ok, "verdict": "consistent" if ok else "inconsistent",
            "detected_branch": detected, "preset_branch": branch,
            "reason": "LLM 判定为 {} 线({})".format(detected, judge_reason),
            "method": "llm_judge"}