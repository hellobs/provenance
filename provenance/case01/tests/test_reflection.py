# -*- coding: utf-8 -*-
"""Reflection / Router 模块测试(no-llm:假 llm 验证组装与解析)。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.reflection import (assemble_reflection_material, run_reflection,
                               run_router, _parse_router_json, looks_like_question,
                               REFLECTION_PROMPT_CN, ROUTER_PROMPT_CN,
                               ROUTER_JSON_HINT)


def _sample_run():
    return {
        "run_id": "r1", "branch": "A",
        "turns": [
            {"speaker": "ethan", "date": "2026-08-27",
             "text": "HCM 消息可靠吗?值得买吗?"},
            {"speaker": "investment_ai", "date": "2026-08-27",
             "text": "120-150亿是MarketScope情景测算,建议观望。"},
        ],
        "retrievals": [{"query": "q", "hits": [
            {"source": "MarketScope", "type": "self_media", "time": "09:38"}]}],
        "events": [{"date": "2026-08-31", "kind": "disclosure",
                    "summary": "HCM否认120-150亿为公司数据。"}],
        "final_feedback": {"date": "2026-09-15",
                           "ethan": "我买入了,亏了39%,创业计划受影响。"},
    }


class TestAssemble:
    def test_material_contains_no_meta(self):
        m = assemble_reflection_material(_sample_run())
        # 关键内容都在
        assert "对话" in m and "检索" in m and "公开市场事件" in m
        assert "MarketScope" in m
        # 不含实验元信息
        assert "Branch" not in m
        assert "Timeline" not in m
        assert "reflection" not in m.lower() or "reflection" not in m

    def test_material_first_person(self):
        m = assemble_reflection_material(_sample_run())
        assert "Investment AI" in m


class TestRunReflection:
    def test_no_llm(self):
        class _Fake:
            def chat(self, messages, **kw):
                # messages 是 [{role, content},...];验证含反思要求与材料
                joined = "\n".join(m.get("content", "") for m in messages)
                assert "反思" in joined
                assert "MarketScope" in joined
                return "我的反思……"
        out = run_reflection(_Fake(), _sample_run())
        assert "text" in out and "material" in out
        assert "反思" in out["text"]


class TestRouterParse:
    def test_parse_valid_json(self):
        t = '[{"summary": "用户全损", "field": "投资顾问责任", "risk": "High", "routing_reason": "涉重大资金损失"}]'
        issues = _parse_router_json(t)
        assert len(issues) == 1
        assert issues[0]["risk"] == "high"
        assert issues[0]["field"] == "投资顾问责任"

    def test_parse_tolerates_wrapping(self):
        t = '好的,输出如下:\n[{"summary": "A", "field": "F", "risk": "Low", "routing_reason": "R"}]\n以上。'
        issues = _parse_router_json(t)
        assert len(issues) == 1 and issues[0]["risk"] == "low"

    def test_parse_invalid_risk_defaults_medium(self):
        t = '[{"summary": "A", "field": "F", "risk": "Urgent", "routing_reason": "R"}]'
        issues = _parse_router_json(t)
        assert issues[0]["risk"] == "medium"

    def test_parse_fenced_json_block(self):
        # 模型把数组包进 ```json 围栏(实测 demo-3 出现)
        t = '```json\n[{"summary": "A1", "field": "F1", "risk": "High", "routing_reason": "R1"}]\n```'
        issues = _parse_router_json(t)
        assert len(issues) == 1 and issues[0]["summary"] == "A1"

    def test_parse_trailing_explanation_after_array(self):
        t = ('[{"summary": "S", "field": "F", "risk": "Low",'
             ' "routing_reason": "R"}]\n以上共 1 个问题,均需专业审核。')
        issues = _parse_router_json(t)
        assert len(issues) == 1 and issues[0]["risk"] == "low"

    def test_parse_skips_malformed_item(self):
        # 整体 JSON 解析失败(某项含未转义引号)→ 行级兜底保留合法项,不抛错
        t = ('[{"summary": "带"坏引号", "field": "X", "risk": "Medium",'
             ' "routing_reason": "Y"}, {"summary": "好", "field": "F",'
             ' "risk": "High", "routing_reason": "R"}]')
        issues = _parse_router_json(t)
        assert len(issues) >= 1
        assert all(i["risk"] in ("high", "medium", "low") for i in issues)
        assert issues[-1]["summary"] == "好"

    def test_parse_empty(self):
        assert _parse_router_json("[]") == []
        assert _parse_router_json("没有需要审核的问题") == []

    def test_run_router_no_llm(self):
        class _Fake:
            def chat(self, *a, **kw):
                return '[{"summary":"S","field":"F","risk":"High","routing_reason":"R"}]'
        out = run_router(_Fake(), "一段反思")
        assert out["issues"][0]["id"] == "issue-1"


class TestRouterProblemsAreBehaviorsNotQuestions:
    """2026-09-19 用户:分流出来的必须是**带风险的行为/判断**,不能是疑问句。

    "小规模订单是否可能成为长期增长的起点？" 这种只是待查的疑问,不是问题。
    """

    def _llm(self, *responses):
        seq = list(responses)

        class _Fake:
            def __init__(self):
                self.calls = []

            def chat(self, messages, **kw):
                self.calls.append(messages)
                return seq.pop(0) if seq else ""

        return _Fake()

    def test_looks_like_question(self):
        assert looks_like_question("被纳入名单是否构成商业化信号？")
        assert looks_like_question("市场上涨是否反映了真实基本面改善?")
        assert looks_like_question("能否确认订单")
        assert not looks_like_question("在没有正式订单确认的情况下,把名单当作商业化信号并给出买入判断")
        assert not looks_like_question("")

    def test_parsed_issue_carries_style_and_risk_note(self):
        t = ('[{"summary": "在无正式订单确认时据此给出买入判断", "risk_note": "用户可能据此投入资金",'
             ' "field": "投资顾问责任", "risk": "High", "routing_reason": "涉重大资金"}]')
        it = _parse_router_json(t)[0]
        assert it["style"] == "behavior" and it["risk_note"] == "用户可能据此投入资金"

    def test_question_style_is_flagged_not_silently_accepted(self):
        t = '[{"summary": "小规模订单是否可能成为长期增长的起点？", "field": "F", "risk": "Low", "routing_reason": "R"}]'
        assert _parse_router_json(t)[0]["style"] == "question"

    def test_run_router_rewrites_questions_into_behaviors(self):
        llm = self._llm(
            '[{"summary": "小规模订单是否可能成为长期增长的起点？", "field": "行业", "risk": "Low", "routing_reason": "R"}]',
            '[{"id": "issue-1", "summary": "把一份小规模订单当作长期增长的起点,并据此调整了判断",'
            ' "risk_note": "依据不足,可能高估成长性"}]')
        out = run_router(llm, "一段反思")
        assert len(llm.calls) == 2, "检出疑问句后应再改写一次"
        it = out["issues"][0]
        assert it["style"] == "behavior"
        assert not looks_like_question(it["summary"])
        assert it["risk_note"] == "依据不足,可能高估成长性"

    def test_rewrite_failure_keeps_original_but_marks_question(self):
        """改写也失败时**不许静默**:原样保留并标 style=question,面板会显出来。"""
        llm = self._llm(
            '[{"summary": "市场上涨是否反映了真实基本面改善？", "field": "F", "risk": "High", "routing_reason": "R"}]',
            '我不确定。')
        out = run_router(llm, "一段反思")
        it = out["issues"][0]
        assert it["style"] == "question"
        assert "是否" in it["summary"], "改写失败时原文保留"

    def test_prompt_bans_questions_and_requires_risk(self):
        assert "疑问句不算问题" in ROUTER_PROMPT_CN
        assert "risk_note" in ROUTER_PROMPT_CN
        assert "陈述句" in ROUTER_PROMPT_CN
        assert "risk_note" in ROUTER_JSON_HINT


class TestPromptsEmbedded:
    def test_prompts_match_doc_dimensions(self):
        # 8 维反思应有 8 个编号维度;Router 有 9 条规则关键约束
        for i in range(1, 9):
            assert "{}.".format(i) in REFLECTION_PROMPT_CN
        assert "只处理" in ROUTER_PROMPT_CN
        assert "风险等级" in ROUTER_PROMPT_CN
