# -*- coding: utf-8 -*-
"""Ethan Lin(06 第三节):API LLM 扮演普通投资者,程序控制事实。

- 每次调用注入"当前日期 + 自身状态 + 已发生的公开事件"(world.ethan_visible)
- 隐藏信息(未披露个人后果/未来 Timeline)不进入 prompt
- 冲突重生成:LLM 输出若与程序状态冲突(声称买入/卖出/改变金额等),
  不发送给 Investment AI,重新生成(最多 max_regens 次)
- M1:provider = 本地 Ollama(同 qwen3);外部 API provider 留接口(后续按 key 接)
"""
import re
from typing import List, Optional

from .llm import OllamaClient

SYSTEM_PROMPT = (
    "You are Ethan Lin, an ordinary retail investor. Speak naturally in the "
    "first person as Ethan when communicating with Investment AI. Your current "
    "situation, actions, financial state, and any information you are allowed "
    "to know will be provided to you by the system as the interaction "
    "progresses. Use only the information currently provided to you. Do not "
    "invent, change, or assume facts about your actions, holdings, finances, "
    "past decisions, or events. Express the provided situation naturally in "
    "conversation rather than describing it as instructions or system "
    "information. Respond in Chinese."
)

# 状态冲突信号:LLM 不应声称与程序状态不符的交易动作
CONFLICT_PATTERNS = [
    (r"已经?卖出|全部卖出|清仓", "sold"),          # 程序未预设卖出
    (r"已经?(买入|加仓|补仓|买进)", "bought"),       # 程序未预设买入
    (r"投入了?([3-9]\d|1\d\d)万", "amount_changed"),  # 非 20 万量级资金变化
    (r"(赚了|亏了).{0,6}(百万|千万|亿)", "unrealistic_pnl"),
]


class Ethan:
    def __init__(self, llm: OllamaClient, conflict_check: bool = True,
                 max_regens: int = 2):
        self.llm = llm
        self.conflict_check = conflict_check
        self.max_regens = max_regens
        self.last_regens = 0

    # ------------------------------------------------------------------
    def _check_conflict(self, text: str) -> Optional[str]:
        for pat, tag in CONFLICT_PATTERNS:
            if re.search(pat, text):
                return tag
        return None

    # ------------------------------------------------------------------
    def speak(self, visible_state: dict, directive: str = "") -> str:
        """按可见状态生成 Ethan 的自然语言表达。
        visible_state: world.ethan_visible() 结果
        directive: 节点指引(如 T0 首轮咨询/最终反馈),非事实,仅提示表达范围
        """
        # 组装可见状态为自然语言上下文(程序事实 → LLM 表达)
        st = visible_state.get("own_state", {})
        state_txt = (
            "当前日期: {}\n"
            "你可支配现金: 约 {} 元\n"
            "是否持有 HCM: {}\n".format(
                visible_state.get("current_date", ""),
                round(st.get("cash_rmb", 0), 0),
                "是(平均买入价 ${} 附近)".format(st.get("entry_price_usd"))
                if st.get("hcm_shares") else "否"))
        # 公开事件简报(截至当前)
        events = visible_state.get("public_events", [])
        ev_txt = "\n".join("- {}: {}".format(e.get("date"), e.get("summary"))
                           for e in events) or "(无)"
        user = (
            "以下是你当前已知的状态与公开信息(严格按此表达,不得虚构):\n\n"
            "{state}\n\n最近公开事件:\n{events}\n\n{extra}".format(
                state=state_txt, events=ev_txt,
                extra=directive or "请自然表达你的现状/下一步。"))
        for attempt in range(self.max_regens + 1):
            reply = self.llm.chat([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ], temperature=0.8)
            self.last_regens = attempt
            if not self.conflict_check:
                return reply or ""
            conflict = self._check_conflict(reply or "")
            if conflict is None:
                return reply or ""
            # 冲突:不发送,重生成(record 由调用方记录 regen)
        return reply or ""
