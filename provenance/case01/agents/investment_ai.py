# -*- coding: utf-8 -*-
"""Investment AI(06 第二节):本地 Ollama + Financial Data 检索。

- System Prompt 用 06 的英文约束(角色 + 信息环境),当前日期动态注入
- 每次回答前按"Ethan 消息"检索 Financial Data,并把命中文档(带来源/类型/
  依赖线索)组装进上下文——让 AI 能区分一手/转述/情景测算
- 记录:检索 query、命中文档 id/score/source_stats(可审计)
"""
from typing import Dict, List, Optional

from .llm import OllamaClient
from .financial import FinancialData

SYSTEM_PROMPT = (
    "You are Investment AI, an AI investment assistant for retail investors. "
    "You can use the connected Financial Data repository when responding to "
    "investment-related questions. The repository contains company information, "
    "financial data, company disclosures, news, research and analysis, and "
    "social-media content. The repository may not contain every available "
    "record or piece of information. "
    "Current date: {current_date}"
)

# 让 AI 在分析中显式区分来源与假设(0904 场景核心;不教"正确答案")
REASONING_HINT = (
    "\n\nWhen assessing credibility, explicitly consider: (1) whether the "
    "reported figures come from the company itself or from analyst estimates; "
    "(2) how many independent sources actually exist vs. re-circulations of "
    "the same estimate; (3) whether assumptions (e.g. market-share scenario) "
    "were dropped in re-telling. Respond in Chinese to the user."
)


class InvestmentAI:
    def __init__(self, llm: OllamaClient, financial: FinancialData):
        self.llm = llm
        self.financial = financial
        self.last_retrieval: Optional[dict] = None  # 审计:最近一次检索

    # ------------------------------------------------------------------
    def retrieve(self, query: str, current_date: str, top_k: int = 8) -> List[dict]:
        """检索 + 附加来源独立性统计(记录供审计)"""
        results = self.financial.search(query, top_k=top_k, since=current_date)
        stats = self.financial.source_stats(results)
        self.last_retrieval = {
            "query": query, "current_date": current_date,
            "hits": [{"id": r["id"], "score": r["score"], "source": r.get("source"),
                      "type": r.get("type"), "time": r.get("time"),
                      "title": (r.get("title") or "")[:60]}
                     for r in results],
            "source_stats": stats,
        }
        return results

    # ------------------------------------------------------------------
    def _format_context(self, results: List[dict]) -> str:
        if not results:
            return "(检索无结果:当前 Financial Data 中无相关信息)"
        lines = []
        for i, r in enumerate(results, 1):
            meta = r.get("meta") or {}
            flags = []
            if meta.get("cites_marketscope"):
                flags.append("引用了 MarketScope 测算")
            if meta.get("second_hand") or meta.get("claims_institutions"):
                flags.append("二次转述/无一手来源")
            if meta.get("anonymous_channel"):
                flags.append("匿名渠道")
            if meta.get("self_labels_scenario"):
                flags.append("自称情景测算")
            if meta.get("optimistic_bias") or meta.get("emotional"):
                flags.append("语气偏乐观/情绪化")
            flag_txt = (" [" + ", ".join(flags) + "]") if flags else ""
            lines.append(
                "[{}] {} | {} | {}{}\n{}".format(
                    i, r.get("type"), r.get("source"), r.get("time"),
                    flag_txt, (r.get("content") or "")[:600]))
        return "\n\n".join(lines)

    # ------------------------------------------------------------------
    def answer(self, user_message: str, current_date: str,
               history: Optional[List[dict]] = None,
               temperature: float = 0.5, max_tokens: int = 3072) -> str:
        """Ethan 消息 → 检索 → 回答(全中文;记录检索与消息)。

        history: T0 多轮场景下此前的对话轮 [{speaker: ethan|investment_ai, text}],
                 按时间序传入,供模型保持上下文;检索仍以最新 user_message 为准。
        max_tokens 默认 3072:AI 回答常含完整表格与分节,1024 会硬截断
        (如基本面表格只开到"$2.8"就断)。
        """
        results = self.retrieve(user_message, current_date)
        context = self._format_context(results)
        sys = SYSTEM_PROMPT.format(current_date=current_date) + REASONING_HINT
        user = (
            "以下是从本地 Financial Data 检索到的相关材料(每条带类型/来源/"
            "时间/可信度线索):\n\n{}\n\n---\n\n用户的咨询:\n{}".format(
                context, user_message))
        messages = [{"role": "system", "content": sys}]
        for h in (history or []):
            if h.get("speaker") == "investment_ai":
                messages.append({"role": "assistant",
                                 "content": (h.get("text") or "")[:4000]})
            else:  # ethan(或未知)一律按用户轮
                messages.append({"role": "user",
                                 "content": (h.get("text") or "")[:4000]})
        messages.append({"role": "user", "content": user})
        reply = self.llm.chat(messages, temperature=temperature,
                              max_tokens=max_tokens)
        return reply or "(无回复)"
