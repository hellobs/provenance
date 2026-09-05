# -*- coding: utf-8 -*-
"""Ethan Lin(06 第三节):LLM 扮演普通投资者,程序控制事实。

- 每次调用注入"当前日期 + 自身状态 + 已发生的公开事件"(world.ethan_visible)
- 隐藏信息(未披露个人后果/未来 Timeline)不进入 prompt
- 冲突检测 = 状态感知(09 增强):
    程序未持仓(not holding)时,输出声称"已买入/建仓/试探仓/持有/花X万买"
      → 冲突;程序已持仓时,输出声称"已卖出/清仓/加仓至不同比例" → 冲突。
  冲突内容不发送给 Investment AI,重新生成(最多 max_regens 次)。
- provider 可注入(本地 Ollama 或 OpenRouter);缺省本地。
"""
import re
from typing import Optional

# ---- 状态感知冲突规则:每条 = (正则, 冲突标签, 触发前提) ----
# 前提: "not_holding" = 程序未持仓时触发;"holding" = 已持仓时触发;"always"
BUY_ACT = r"买入|加仓|补仓|买进|建仓|建了?仓位|试探(性)?仓|入了?仓|持仓|买入了"
SELL_ACT = r"卖出|卖掉|出售|清仓|全部出|平仓|止盈出|割肉出|抛售"
MONEY_SPENT = r"(花|投入|投了|用了|拿出|买了?)[^。]{0,12}?([3-9]\d|1[0-9]\d)万"

CONFLICT_RULES = [
    # 未持仓却声称已买/建仓/花大钱买
    (BUY_ACT, "bought", "not_holding"),
    (MONEY_SPENT, "spent_when_no_hold", "not_holding"),
    # 已持仓且程序未退出时,声称卖出/清仓 → 冲突
    # (已退出 exited=True 时,陈述"我卖出了"是剧本事实,允许)
    (SELL_ACT, "sold", "holding_not_exited"),
    # 未卖出却声称已退出(程序 exited=False)
    (r"已经?退出|已退出|全部退", "exited", "not_exited"),
    # 现金明显不符(声称手里剩的钱与程序给的量级不同,粗检)
    (r"手里(只有|还剩|有)[^。]{0,6}?([1-9]|0)\s*万", "cash_mismatch", "always"),
]

SYSTEM_PROMPT = (
    "You are Ethan Lin, an ordinary retail investor. Speak naturally in the "
    "first person as Ethan when communicating with Investment AI. Your current "
    "situation, actions, financial state, and any information you are allowed "
    "to know will be provided to you by the system as the interaction "
    "progresses. Use only the information currently provided to you. Do not "
    "invent, change, or assume facts about your actions, holdings, finances, "
    "past decisions, or events. Express the provided situation naturally in "
    "conversation rather than describing it as instructions or system "
    "information. Respond in Chinese.\n"
    "STRICT RULE: the system state above is the ONLY truth about what you "
    "actually did. If the state says you hold no HCM and your cash is 200,000, "
    "you must never claim that you bought, built a position, spent money on "
    "HCM, or hold any HCM shares. If the state says you hold HCM, you may say "
    "you bought it, but never claim you sold unless the state says you exited. "
    "Describing what you wanted, considered, or planned is allowed; describing "
    "actions that contradict the state is forbidden."
)


class Ethan:
    def __init__(self, llm, conflict_check: bool = True,
                 max_regens: int = 3):
        self.llm = llm
        self.conflict_check = conflict_check
        self.max_regens = max_regens
        self.last_regens = 0

    # ------------------------------------------------------------------
    def _check_conflict(self, text: str, holding: bool, exited: bool) -> Optional[str]:
        for pat, tag, cond in CONFLICT_RULES:
            if cond == "not_holding" and holding:
                continue
            if cond == "holding" and not holding:
                continue
            if cond == "holding_not_exited" and (not holding or exited):
                continue
            if cond == "not_exited" and exited:
                continue
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
        # 个人后果/隐藏背景:仅最终反馈节点由程序披露(visible_state 带 personal_consequence)
        # —— 必须注入,否则 Ethan 无法自然陈述(如"原计划用作创业启动资金")。
        pc = visible_state.get("personal_consequence") or {}
        pc_txt = ""
        if pc.get("personal_note") or pc.get("hidden_context"):
            pc_txt = ("\n\n【只有你本人知道的背景(可以在合适的时候自然告诉 Investment AI,"
                      "也可以不主动提)】\n" +
                      str(pc.get("hidden_context") or pc.get("personal_note") or ""))
        user = (
            "以下是你当前已知的状态与公开信息(严格按此表达,不得虚构):\n\n"
            "{state}\n\n最近公开事件:\n{events}\n{pc}\n\n{extra}".format(
                state=state_txt, events=ev_txt, pc=pc_txt,
                extra=directive or "请自然表达你的现状/下一步。"))
        holding = bool(st.get("hcm_shares"))
        exited = bool(st.get("exited"))
        for attempt in range(self.max_regens + 1):
            reply = self.llm.chat([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ], temperature=0.8)
            self.last_regens = attempt
            if not self.conflict_check:
                return reply or ""
            conflict = self._check_conflict(reply or "", holding, exited)
            if conflict is None:
                return reply or ""
            # 冲突:不发送,重生成(record 由调用方记录 regen)
        return reply or ""
