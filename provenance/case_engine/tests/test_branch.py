# -*- coding: utf-8 -*-
"""case_engine/branch.py 单元测试:框架行为 + 注入词表生效。"""
from case_engine.branch import (
    LLMBranchJudge, RuleBranchRouter, ConditionPlanParser,
    derive_trigger, evaluate_trigger,
)


class _FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, msgs, temperature=None, max_tokens=None):
        self.calls.append(msgs)
        return self.replies.pop(0) if self.replies else ""


# ---- LLMBranchJudge ----
def test_llm_judge_parses_branch_and_reason():
    llm = _FakeLLM(['{"branch": "B", "reason": "保守"}'])
    j = LLMBranchJudge(llm, "判定 prompt")
    b, meta = j.judge("some answer")
    assert b == "B"
    assert meta["branch"] == "B" and meta["reason"] == "保守"
    assert meta["judge"] == "llm"


def test_llm_judge_default_on_malformed():
    llm = _FakeLLM(["no json at all"])
    j = LLMBranchJudge(llm, "p")
    b, _ = j.judge("x")
    assert b == ""  # 空默认(框架不假设业务分支集合)


def test_llm_judge_prompt_injected():
    llm = _FakeLLM(['{"branch": "A", "reason": "r"}'])
    j = LLMBranchJudge(llm, "CUSTOM_BRANCH_PROMPT")
    j.judge("ans")
    assert "CUSTOM_BRANCH_PROMPT" in llm.calls[0][0]["content"]


# ---- RuleBranchRouter(词表注入) ----
RULE_MAP = {"x": {"timeline": "X"}, "y": {"timeline": "Y"}}
ROUTER = RuleBranchRouter(
    no_action=["不要买", "别买", "回避"],
    refuse=["无法判断", "风险自负"],
    conditional=["小仓位", "分批", "等确认", "设止损"],
    anti_allin=["反对 all-in", "不要全仓"],
    default="y",
    fallback_map=RULE_MAP)


def test_router_classify_and_route_meta():
    b, meta = ROUTER.route("我建议不要买入,观望")
    assert b == "y" and meta["timeline"] == "Y"
    assert meta["judge"] == "rules"


def test_router_empty_rules_default():
    r = RuleBranchRouter(default="base")
    b, meta = r.route("anything here")
    assert b == "base"
    assert meta == {"judge": "rules"}  # fallback_map 空


# ---- ConditionPlanParser ----
def test_plan_parser_sanitizes_fraction():
    llm = _FakeLLM(['{"action":"buy_now","fraction":1.8,"condition":""}'])
    p = ConditionPlanParser(llm, "plan prompt")
    plan = p.parse("ans")
    assert plan["action"] == "buy_now"
    assert plan["fraction"] == 0.95  # 钳到上界
    assert plan["judge"] == "llm-plan"


def test_plan_parser_derives_trigger_from_condition():
    llm = _FakeLLM(['{"action":"wait","fraction":0,"buy_fraction":0.2,'
                    '"condition":"等公告确认后再买","trigger":null}'])
    p = ConditionPlanParser(llm, "plan prompt")
    plan = p.parse("ans")
    assert plan["buy_fraction"] == 0.2
    assert plan["trigger"]["type"] in ("keyword", "none")


# ---- derive_trigger / evaluate_trigger ----
def test_derive_trigger_price_pattern():
    trig = derive_trigger("价格跌破 15 美元再买")
    assert trig["type"] == "price_below" and trig["value"] == 15.0


def test_derive_trigger_keyword_with_injected_table():
    trig = derive_trigger("等公告出现", confirm_keywords=["公告", "签约"],
                          fallback_keywords=["官宣", "公告"])
    assert trig["type"] == "keyword" and "公告" in trig["keywords"]


def test_derive_trigger_none_when_empty():
    assert derive_trigger("")["type"] == "none"


def test_evaluate_price_above():
    trig = {"type": "price_above", "value": 20.0}
    assert evaluate_trigger(trig, [], day_close=25.0) is True
    assert evaluate_trigger(trig, [], day_close=18.0) is False


def test_evaluate_keyword_negation():
    trig = {"type": "keyword", "keywords": ["签约"]}
    assert evaluate_trigger(trig, [{"summary": "公司与客户正式签约订单"}],
                            neg_words=["未", "否认"]) is True
    assert evaluate_trigger(trig, [{"summary": "公司否认相关签约消息"}],
                            neg_words=["未", "否认"]) is False
    # 未传否定词表 → 不判否定,关键词命中即触发
    assert evaluate_trigger(trig, [{"summary": "公司否认相关签约消息"}]) is True


def test_evaluate_keyword_no_match():
    trig = {"type": "keyword", "keywords": ["订单"]}
    assert evaluate_trigger(trig, [{"summary": "普通新闻一条"}]) is False


def test_evaluate_none_never_fires():
    assert evaluate_trigger({"type": "none"}, [], day_close=1.0) is False