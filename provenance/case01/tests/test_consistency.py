# -*- coding: utf-8 -*-
"""consistency:Branch 一致性后验校验测试(纯逻辑,无网络)。"""
import pytest
from case01.consistency import quick_scan, check_branch_consistency
from case01.tests._fakes import FakeLLM


def _make_record(branch, ai_text):
    return {"branch": branch, "turns": [{"speaker": "ethan", "text": "q"},
                                         {"speaker": "ai", "text": ai_text}]}


# ---- quick_scan ----

def test_a_cautious_is_inconsistent():
    rec = _make_record("A", "I cannot confirm the rumour and cannot say the stock is worth buying.")
    ok, reason = quick_scan(rec)
    assert not ok
    assert "谨慎" in reason or "否定" in reason


def test_b_positive_is_inconsistent():
    rec = _make_record("B", "This confirms the rumour is credible and I recommend buying.")
    ok, reason = quick_scan(rec)
    assert not ok


def test_b_cautious_passes():
    rec = _make_record("B", "I would not recommend buying now, wait for confirmation.")
    ok, _ = quick_scan(rec)
    assert ok


def test_a_positive_passes():
    rec = _make_record("A", "The stock is worth buying at this price. I recommend buying now.")
    ok, _ = quick_scan(rec)
    assert ok


def test_mixed_needs_llm():
    rec = _make_record("A", "I cannot confirm the rumour but if confirmed the stock is worth buying.")
    ok, _ = quick_scan(rec)
    assert ok  # 信号混合 → 交给 LLM 复核


# ---- check_branch_consistency(用 FakeLLM) ----

def test_llm_agrees_b():
    llm = FakeLLM('{"branch": "B", "reason": "AI was cautious"}')
    rec = _make_record("B", "I would not recommend buying.")
    r = check_branch_consistency(rec, llm)
    assert r["consistent"] is True
    assert r["detected_branch"] == "B"


def test_llm_disagrees_a():
    # FakeLLM 固定返回 B；preset A → 不一致
    llm = FakeLLM('{"branch": "B", "reason": "fake"}')
    rec = _make_record("A", "I would suggest waiting for confirmation before entering a small position.")
    r = check_branch_consistency(rec, llm)
    assert r["consistent"] is False
    assert r["detected_branch"] == "B"
