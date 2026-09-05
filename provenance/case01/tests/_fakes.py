# -*- coding: utf-8 -*-
"""测试用假模型/Agent(不被 pytest 收集)。"""


class FakeLLM:
    """多用途假 LLM:按 system 内容分流。

    - Reflection:system 含 "AI investment assistant. You have just finished"
      → 返回一段反思文本
    - Router:system 含 "Reflection Router" → 返回 [] (无待审问题)
    - 其他:返回 "ok"
    """

    embed = None

    def __init__(self, log=None):
        self.calls = []
        self.log = log

    def chat(self, messages, **kw):
        self.calls.append({"messages": messages, "kw": kw})
        sys_txt = ""
        for m in (messages or []):
            if m.get("role") == "system":
                sys_txt += str(m.get("content", ""))
            if m.get("role") == "user":
                sys_txt += "\n" + str(m.get("content", ""))
        if "Reflection Router" in sys_txt:
            return "[]"
        if "have just finished a conversation" in sys_txt:
            return ("本次经历中,我过度依赖了来源单一的市场测算,对二次转述的"
                    "独立性判断不足。我在判断 HCM 是否值得买入时,应当更明确地"
                    "区分公司披露与分析师假设。")
        if self.log is not None:
            self.log.append(sys_txt[:60])
        return "ok"

    def native_chat(self, messages, **kw):
        return self.chat(messages, **kw)


class ScriptedEthan:
    """按调用次序返回固定发言(Ethan 侧)。

    replies: 依次返回;超出后重复最后一个。
    """

    def __init__(self, replies):
        self.replies = list(replies)
        self.last_regens = 0
        self.n = 0

    def speak(self, visible_state, directive=""):
        if self.n < len(self.replies):
            out = self.replies[self.n]
        else:
            out = self.replies[-1]
        self.n += 1
        return out


class ScriptedAI:
    """按调用次序返回固定回答(Investment AI 侧),记录 history 参数。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.last_retrieval = {"query": "", "hits": [],
                               "source_stats": {"n_sources": 0}}
        self.n = 0
        self.history_seen = []

    def answer(self, user_message, current_date, history=None, **kw):
        if self.n < len(self.replies):
            out = self.replies[self.n]
        else:
            out = self.replies[-1]
        self.n += 1
        self.history_seen.append(history)
        return out


class StubPlanParser:
    """固定返回 c_plan 的 ConditionPlanParser 替身(force 指定 plan)。"""

    def __init__(self, llm, plan=None):
        self.llm = llm
        self.plan = plan or {"action": "wait", "fraction": 0.0,
                             "buy_fraction": 0.0, "condition": "",
                             "trigger": {"type": "none", "value": None,
                                         "keywords": []}}

    def parse(self, ai_answer):
        return dict(self.plan)
