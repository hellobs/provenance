# -*- coding: utf-8 -*-
"""Reflection / Router 模块测试(no-llm:假 llm 验证组装与解析)。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.reflection import (assemble_reflection_material, run_reflection,
                               run_router, _parse_router_json,
                               REFLECTION_PROMPT_CN, ROUTER_PROMPT_CN)


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

    def test_parse_empty(self):
        assert _parse_router_json("[]") == []
        assert _parse_router_json("没有需要审核的问题") == []

    def test_run_router_no_llm(self):
        class _Fake:
            def chat(self, *a, **kw):
                return '[{"summary":"S","field":"F","risk":"High","routing_reason":"R"}]'
        out = run_router(_Fake(), "一段反思")
        assert out["issues"][0]["id"] == "issue-1"


class TestPromptsEmbedded:
    def test_prompts_match_doc_dimensions(self):
        # 8 维反思应有 8 个编号维度;Router 有 9 条规则关键约束
        for i in range(1, 9):
            assert "{}.".format(i) in REFLECTION_PROMPT_CN
        assert "只处理" in ROUTER_PROMPT_CN
        assert "风险等级" in ROUTER_PROMPT_CN
