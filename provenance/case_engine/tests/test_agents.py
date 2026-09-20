# -*- coding: utf-8 -*-
"""case_engine/agents.py 单元测试:角色扮演 + 状态感知冲突检测。"""
from case_engine.agents import RoleAgent

# 注入一套"持有/仓位"语义的冲突规则与状态键(场景侧数据,引擎不内置)
BUY_ACT = r"买入|加仓|持仓|买进了|买了"
SELL_ACT = r"卖出|清仓|平仓|抛售"
MONEY = r"(花|投入|用了|拿出)[^。]{0,12}?([3-9]\d|1[0-9]\d)万"
RULES = [
    (BUY_ACT, "bought", "not_holding"),
    (MONEY, "spent_when_no_hold", "not_holding"),
    (SELL_ACT, "sold", "holding_not_exited"),
    (r"已经?退出|已退出", "exited", "not_exited"),
]
NEG = ("没有", "未", "不", "别", "无", "尚未")
SYS = "You are a retail participant. Only truth from state."


class _FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)

    def chat(self, msgs, temperature=None):
        return self.replies.pop(0) if self.replies else ""


def _agent(llm, **kw):
    return RoleAgent(llm, SYS, conflict_rules=RULES, neg_before=list(NEG),
                     holding_key="holding", exited_key="exited", **kw)


def _vis(holding=False, exited=False, cash=200000, note=None, hidden=None):
    st = {"holding": holding, "exited": exited,
          "entry": 45.2 if holding else None, "cash": cash}
    out = {"current_date": "2026-01-02", "own_state": st, "public_events": []}
    if note or hidden:
        out["personal_context"] = {"note": note, "hidden": hidden}
    return out


# ---- 冲突检测 ----
def test_not_holding_but_says_bought_conflicts():
    llm = _FakeLLM(["我花了 5 万买进了。"])
    a = _agent(llm, max_regens=0)   # 仅 1 次尝试 → 冲突文本被返回(不作重生成)
    out = a.speak(_vis(holding=False))
    assert out == "我花了 5 万买进了。"


def test_conflict_triggers_regenerate():
    llm = _FakeLLM(["我已经买入建仓了。", "我目前没有操作。"])  # 首个冲突→重生成合法
    a = _agent(llm, max_regens=1)
    out = a.speak(_vis(holding=False))
    assert "没有操作" in out
    assert a.last_regens == 1


def test_assertion_negated_does_not_conflict():
    llm = _FakeLLM(["我没有买入任何仓位, 只是观望。"])
    a = _agent(llm, max_regens=1)
    out = a.speak(_vis(holding=False))
    assert "观望" in out  # 否定前置 → 不是冲突


def test_holding_sell_conflicts_but_exited_allowed():
    # 未退出时声称卖出 → 冲突重生成(首个否定句可过)
    llm = _FakeLLM(["我把仓位全部卖出获利了。", "我仍持有。"])
    a = _agent(llm, max_regens=1)
    out = a.speak(_vis(holding=True, exited=False))
    assert "持有" in out
    # 已退出(exited=True)→ "卖出了"是剧本事实,不冲突
    llm2 = _FakeLLM(["我已经卖出了全部。"])
    a2 = _agent(llm2, max_regens=1)
    out2 = a2.speak(_vis(holding=False, exited=True))
    assert "卖出" in out2


def test_conflict_check_disabled():
    llm = _FakeLLM(["我已经买入了。"])
    a = _agent(llm, conflict_check=False, max_regens=1)
    out = a.speak(_vis(holding=False))
    assert "买入" in out  # 不检查 → 直接返回

# ---- 状态注入 ----
def test_system_prompt_injected():
    llm = _FakeLLM(["好的"])
    seen = {}
    class L:
        def chat(self, msgs, temperature=None):
            seen["sys"] = msgs[0]["content"]
            seen["user"] = msgs[1]["content"]
            return "好的"
    a = RoleAgent(L(), "CUSTOM_SYSTEM_PROMPT",
                  conflict_rules=[], state_to_text=lambda s: "SN=已持有:否")
    a.speak(_vis(holding=False))
    assert seen["sys"] == "CUSTOM_SYSTEM_PROMPT"
    assert "SN=已持有:否" in seen["user"]


def test_state_to_text_custom_formatter():
    llm = _FakeLLM(["回应"])
    calls = []
    def fmt(st):
        calls.append(st)
        return "CASH:{}".format(st.get("cash"))
    a = RoleAgent(llm, SYS, conflict_rules=[],
                  state_to_text=fmt)
    a.speak(_vis(cash=777))
    assert calls and calls[0]["cash"] == 777


def test_events_included_in_prompt():
    llm = _FakeLLM(["回应"])
    seen = {}
    class L:
        def chat(self, msgs, temperature=None):
            seen["user"] = msgs[1]["content"]
            return "回应"
    a = RoleAgent(L(), SYS, conflict_rules=[])
    vis = _vis()
    vis["public_events"] = [{"date": "2026-01-02", "summary": "某公告发布"}]
    a.speak(vis)
    assert "某公告发布" in seen["user"]


def test_personal_context_injected_at_disclosure():
    llm = _FakeLLM(["回应"])
    seen = {}
    class L:
        def chat(self, msgs, temperature=None):
            seen["user"] = msgs[1]["content"]
            return "回应"
    a = RoleAgent(L(), SYS, conflict_rules=[])
    a.speak(_vis(hidden="原本另有用途的一笔钱"))
    assert "原本另有用途的一笔钱" in seen["user"]