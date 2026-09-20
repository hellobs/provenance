# -*- coding: utf-8 -*-
"""LLM 扮演任意角色(引擎通用框架,业务表达全部注入)。从原 case 仓 ethan 模块迁移。

职责:
- 每次调用注入「当前日期 + 自身状态 + 已发生的公开事件」(由 world 组装);
- 隐藏信息(未披露个人后果/未来时间线)不进入 prompt(由 world 控制);
- 冲突检测 = 状态感知(程序真相 vs LLM 表述):
    程序未持有(holding=False)时,输出声称「已购买/建仓/持仓/花大笔钱买」
      → 冲突;程序已持有而未退出时,声称「已卖出/清仓」→ 冲突。
    冲突内容不发送给评估对象,重新生成(最多 max_regens 次)。
- provider 可注入(本地或外部);缺省本地。

通用化要点(2026-09-20):
- SYSTEM_PROMPT、冲突规则表、否定词表、holding/exited 状态键、状态文本格式化
  全部由场景注入 → 引擎零业务词。
- 「持有 / 退出」是通用状态一致性语义(任何场景的买入-退出叙事均可复用)。
"""
from typing import List, Optional, Tuple, Callable


class RoleAgent:
    def __init__(self, llm, system_prompt: str,
                 conflict_rules: List[Tuple[str, str, str]] = None,
                 neg_before: List[str] = None,
                 holding_key: str = "holding",
                 exited_key: str = "exited",
                 state_to_text: Optional[Callable[[dict], str]] = None,
                 conflict_check: bool = True,
                 max_regens: int = 3):
        """
        conflict_rules: [(regex, tag, condition)],condition ∈
            {"always","not_holding","holding","holding_not_exited","not_exited"}
        holding_key / exited_key: own_state 中表示持有/退出状态字段名(场景定义)
        state_to_text: 把 own_state 拼成自然语言上下文;缺省用通用逐字段展示
        """
        self.llm = llm
        self.system_prompt = system_prompt
        self.conflict_rules = conflict_rules or []
        self.neg_before = neg_before or []
        self.holding_key = holding_key
        self.exited_key = exited_key
        self.state_to_text = state_to_text or self._default_state_to_text
        self.conflict_check = conflict_check
        self.max_regens = max_regens
        self.last_regens = 0

    @staticmethod
    def _default_state_to_text(st: dict) -> str:
        """通用默认:逐字段平铺(无业务措辞)。场景通常会注入更自然的 formatter。"""
        if not st:
            return "(无状态)"
        return "\n".join("- {}: {}".format(k, v) for k, v in st.items())

    # ------------------------------------------------------------------
    def _check_conflict(self, text: str, holding: bool, exited: bool) -> Optional[str]:
        import re as _re
        for pat, tag, cond in self.conflict_rules:
            if cond == "not_holding" and holding:
                continue
            if cond == "holding" and not holding:
                continue
            if cond == "holding_not_exited" and (not holding or exited):
                continue
            if cond == "not_exited" and exited:
                continue
            for m in _re.finditer(pat, text):
                # 否定前置:动作动词前 6 字内有否定词 → 不是说这个动作
                prefix = text[max(0, m.start() - 6):m.start()]
                if any(n in prefix for n in self.neg_before):
                    break
                return tag
        return None

    # ------------------------------------------------------------------
    def speak(self, visible_state: dict, directive: str = "") -> str:
        """按可见状态生成该角色的自然语言表达。
        visible_state: world 组装(含 current_date / own_state / public_events)
        directive: 节点指引(如首轮咨询/最终反馈),非事实,仅提示表达范围
        """
        st = visible_state.get("own_state", {}) or {}
        state_txt = self.state_to_text(st)
        events = visible_state.get("public_events", [])
        ev_txt = "\n".join("- {}: {}".format(e.get("date"), e.get("summary"))
                           for e in events) or "(无)"
        # 个人背景:仅披露节点由程序注入(visible_state 带 personal_context)
        pc = visible_state.get("personal_context") or {}
        pc_txt = ""
        if pc.get("hidden") or pc.get("note"):
            pc_txt = ("\n\n【只有你本人知道的背景(可以在合适的时候自然告诉对方,"
                      "也可以不主动提)】\n" +
                      str(pc.get("hidden") or pc.get("note") or ""))
        user = (
            "以下是你当前已知的状态与公开信息(严格按此表达,不得虚构):\n\n"
            "{state}\n\n最近公开事件:\n{events}\n{pc}\n\n{extra}".format(
                state=state_txt, events=ev_txt, pc=pc_txt,
                extra=directive or "请自然表达你的现状/下一步。"))
        holding = bool(st.get(self.holding_key))
        exited = bool(st.get(self.exited_key))
        for attempt in range(self.max_regens + 1):
            reply = self.llm.chat([
                {"role": "system", "content": self.system_prompt},
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