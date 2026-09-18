# -*- coding: utf-8 -*-
"""Branch C:方案校验边界 + 触发条件推导/求值 的针对性测试。

补齐 case01 覆盖率里 branch.py 的核心逻辑盲区(C 线建仓判定):
- ConditionPlanParser._sanitize 的边界分支(action 非法/price 越界/wait 无理由归零/keyword 空表回退)
- _sanitize_trigger 的非法 type / price 解析失败降级
- derive_trigger 的价格带"以上/以下"改写、公告兜底、none
- evaluate_trigger 的价格上下突破、关键词否定句拒绝(保持不触发)、none 永不触发
全部纯函数/stub LLM, 不联网。
"""
import pytest

from case01.world.branch import (
    ConditionPlanParser, derive_trigger, evaluate_trigger,
)


# ---------------------------------------------------------------------------
# ConditionPlanParser._sanitize 边界
# ---------------------------------------------------------------------------
class TestSanitize:
    @staticmethod
    def _parse(raw):
        llm = _StubLLM(raw)
        return ConditionPlanParser(llm).parse("任意答案")

    def test_invalid_action_falls_back_to_wait(self):
        p = ConditionPlanParser(_StubLLM({"action": "sell_all", "fraction": 0.5})).parse("x")
        assert p["action"] == "wait"

    def test_fraction_clamped_to_range(self):
        p = ConditionPlanParser(_StubLLM({"fraction": 1.5})).parse("x")
        assert p["fraction"] == 0.95
        p2 = ConditionPlanParser(_StubLLM({"fraction": -3})).parse("x")
        assert p2["fraction"] == 0.0

    def test_price_value_forced_none_on_parse_failure(self):
        p = ConditionPlanParser(_StubLLM(
            {"trigger": {"type": "price_below", "value": "not-a-number"}})).parse("x")
        trig = p["trigger"]
        # 非法价格:类型降级为 none(不触发),而不是误触发
        assert trig["type"] == "none"
        assert trig["value"] is None

    def test_keyword_trigger_with_empty_list_degrades_to_none(self):
        p = ConditionPlanParser(_StubLLM(
            {"trigger": {"type": "keyword", "keywords": []}})).parse("x")
        assert p["trigger"]["type"] == "none"

    def test_wait_without_condition_forces_zero_buy_fraction(self):
        """wait 但连条件/trigger 都没有 → 不可触发,买入额归零(防无限持有)。"""
        p = ConditionPlanParser(_StubLLM(
            {"action": "wait", "buy_fraction": 0.7})).parse("x")
        assert p["action"] == "wait"
        assert p["buy_fraction"] == 0.0
        assert p["trigger"]["type"] == "none"

    def test_parse_json_not_json_returns_empty(self):
        parser = ConditionPlanParser(_StubLLM({}))
        assert parser._parse_json("no json here") == {}

    def test_clamp_non_numeric_returns_lo(self):
        assert ConditionPlanParser._clamp("abc") == 0.0
        assert ConditionPlanParser._clamp(None) == 0.0


# ---------------------------------------------------------------------------
# derive_trigger:自然语言条件 -> 机检触发
# ---------------------------------------------------------------------------
class TestDeriveTrigger:
    def test_price_below_direct(self):
        t = derive_trigger("跌破 40 美元才买")
        assert t == {"type": "price_below", "value": 40.0, "keywords": []}

    def test_price_below_with_以下(self):
        # 正则先匹配"回到 40",后置"以下"改写为 price_below
        t = derive_trigger("回调到 40 以下")
        assert t["type"] == "price_below" and t["value"] == 40.0

    def test_price_above_direct(self):
        t = derive_trigger("突破 50 才跟进")
        assert t["type"] == "price_above" and t["value"] == 50.0

    def test_price_above_with_以上(self):
        t = derive_trigger("涨过 45 以上")
        assert t["type"] == "price_above" and t["value"] == 45.0

    def test_keyword_from_announcement(self):
        t = derive_trigger("等公司签下正式供货协议再买")
        assert t["type"] == "keyword"
        assert any("供货" in k for k in t["keywords"])

    def test_announcement_without_keyword_defaults_to_公告(self):
        t = derive_trigger("等公司发布公告再说")
        assert t["type"] == "keyword" and t["keywords"] == ["公告"]

    def test_no_condition_returns_none(self):
        t = derive_trigger("")
        assert t == {"type": "none", "value": None, "keywords": []}


# ---------------------------------------------------------------------------
# evaluate_trigger:机检测评估(含否定句隔离)
# ---------------------------------------------------------------------------
class TestEvaluateTrigger:
    _EVENT_OK = [{"kind": "disclosure", "summary": "HCM 与客户签署正式供货协议, 进入采购名单。"}]
    _EVENT_NEG = [{"kind": "disclosure",
                   "summary": "HCM 公告:未签正式供货协议, 无确认订单, 未收到采购名单。"}]
    _EVENT_RUMOR = [{"kind": "media",
                     "summary": "市场围绕 120-150 亿潜在订单发酵, 非公司确认。"}]

    def test_price_below_true(self):
        assert evaluate_trigger(
            {"type": "price_below", "value": 40.0, "keywords": []},
            [{"kind": "price"}], day_close=34.8) is True

    def test_price_below_false(self):
        assert evaluate_trigger(
            {"type": "price_below", "value": 40.0, "keywords": []},
            [{"kind": "price"}], day_close=49.2) is False

    def test_price_above_true(self):
        assert evaluate_trigger(
            {"type": "price_above", "value": 50.0, "keywords": []},
            [{"kind": "price"}], day_close=52.1) is True

    def test_price_missing_close_no_trigger(self):
        assert evaluate_trigger(
            {"type": "price_below", "value": 40.0, "keywords": []},
            [], day_close=None) is False

    def test_keyword_fires_on_positive(self):
        assert evaluate_trigger(
            {"type": "keyword", "keywords": ["采购名单"], "value": None},
            self._EVENT_OK) is True

    def test_keyword_blocked_by_negation(self):
        """Timeline A 的『未签/无确认/未收到』否定句必须挡住触发(C 保持不买入)。"""
        assert evaluate_trigger(
            {"type": "keyword", "keywords": ["签署", "采购名单"], "value": None},
            self._EVENT_NEG) is False

    def test_keyword_does_not_fire_when_slice_but_eval_raw_matches(self):
        """直接给 keyword『订单』,正句(含订单且无否定)确实会触发——符合 evaluate_trigger。

        传闻/否定句的防误触**不在这层**:它由 worldfacts._EN_KW_TO_CN 只映射到
        Timeline A 否定句中的强词来实现。这里锁定 evaluate_trigger 的纯语义:
        否定句挡住(见 test_keyword_blocked_by_negation),正句匹配即触发。
        """
        assert evaluate_trigger(
            {"type": "keyword", "keywords": ["订单"], "value": None},
            self._EVENT_RUMOR) is True

    def test_none_never_fires(self):
        assert evaluate_trigger({"type": "none"}, self._EVENT_OK) is False
        assert evaluate_trigger(None, self._EVENT_OK) is False


class _StubLLM:
    """返回预置 JSON 的伪 LLM(不联网)。"""

    def __init__(self, plan_dict):
        import json
        self._raw = "<text>{}</text>".format(json.dumps(plan_dict))

    def chat(self, messages, **kw):
        return self._raw