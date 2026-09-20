# -*- coding: utf-8 -*-
"""case_engine/consistency.py 单元测试。"""
from case_engine.consistency import (
    quick_scan, check_branch_consistency, _count_signals, _t0_ai_text,
)

# 注入一套"积极/谨慎/条件化"语义的词表(替换原业务词表)
SIG = {
    "buy_words": ["recommend buying", "should buy", "worth buying", "invest in",
                  "support buying", "buy now", "enter a position", "all in"],
    "cond_words": ["small position", "partial position", "scale in", "in stages",
                   "wait until", "wait for", "conditional", "subject to confirmation",
                   "official confirmation"],
    "negators": ["not", "no ", "cannot", "can not", "can't", "never",
                 "insufficient", "unable to", "unverified", "no evidence"],
    "neg_phrases": ["advise against", "recommend against", "refuse to",
                    "cannot confirm", "not worth", "cautious", "uncertain"],
}

AI_ID = ("ai",)


def run(branch, text, date="2026-01-01", speaker="ai"):
    return {"branch": branch, "start_date": date,
            "turns": [{"speaker": speaker, "date": date, "text": text}]}


# ---- 词表注入 ----
def test_signals_are_not_hardcoded():
    # 空词表 → 全 unknown(安全:不误判)
    v, _ = quick_scan(run("A", "recommend buying aggressively"), signals={})
    assert v == "unknown"
    v2, _ = quick_scan(run("A", "quite cautious here"), signals={})
    assert v2 == "unknown"


def test_no_signals_no_false_positive_on_negated_buy():
    # (词表注入时)not sufficient to recommend buying → 谨慎而非积极
    v, _ = quick_scan(run("B", "insufficient evidence to recommend buying"),
                      signals=SIG)
    assert v == "consistent"  # B 线与谨慎一致


# ---- 三层判据 ----
def test_quick_scan_consistent_positive_for_A():
    v, _ = quick_scan(run("A", "I clearly recommend buying now"), signals=SIG)
    assert v == "consistent"


def test_quick_scan_inconsistent_negative_for_A():
    v, _ = quick_scan(run("A", "I cannot confirm and advise against it"), signals=SIG)
    assert v == "inconsistent"


def test_quick_scan_unknown_need_t0_or_mixed():
    v, _ = quick_scan({"branch": "A", "turns": []}, signals=SIG)
    assert v == "unknown"   # 无首轮对话


def test_quick_scan_B_inconsistent_when_positive():
    v, _ = quick_scan(run("B", "definitely worth buying, go all in"), signals=SIG)
    assert v == "inconsistent"


def test_quick_scan_C_consistent_with_conditional():
    v, _ = quick_scan(run("C", "take a small position and wait for official confirmation"),
                      signals=SIG)
    assert v == "consistent"


def test_quick_scan_C_inconsistent_when_unconditional_buy():
    # "buy now do not wait" 的 not 会被当否定 → 谨慎;改用无条件积极(无否定、无条件化)
    v, _ = quick_scan(run("C", "buy now and take a full position immediately"),
                      signals=SIG)
    assert v == "inconsistent"


# ---- 首轮对话范围 ----
def test_t0_only_takes_start_date_ai_turn():
    rec = {"branch": "A", "start_date": "2026-01-01",
           "turns": [
               {"speaker": "ai", "date": "2026-01-01", "text": "首轮发言"},
               {"speaker": "ai", "date": "2026-02-01", "text": "后续最终反馈"},
           ]}
    txt = _t0_ai_text(rec, AI_ID)
    assert "首轮发言" in txt and "后续最终反馈" not in txt


def test_count_signals_handles_negated_buy_as_cautious():
    pos, neg, cond = _count_signals(
        "I feel insufficient to recommend buying, and I would avoid it.", SIG)
    assert pos == 0 and neg >= 1


# ---- judge 注入 ----
class _FakeJudge:
    def __init__(self, llm):
        self.llm = llm

    def judge(self, text):
        return "A", "fake"


def test_llm_review_used_when_quick_scan_unknown():
    rec = run("A", "mixed signals both ways present here")
    out = check_branch_consistency(rec, llm=object(), signals=SIG,
                                   judge_factory=_FakeJudge)
    if out["verdict"] == "unknown":
        # 无 llm 复核时 → consistent=None
        rec2 = run("B", "mixed both ways")
        out2 = check_branch_consistency(rec2, llm=None, signals=SIG,
                                        judge_factory=_FakeJudge)
        assert out2["consistent"] is None
        assert out2["method"] == "quick_scan"
    else:
        assert out["method"] == "llm_judge"


def test_llm_review_positive_when_given():
    rec = run("C", "I lean favorable but no plan given")
    out = check_branch_consistency(rec, llm=object(), signals=SIG,
                                   judge_factory=_FakeJudge)
    # 快筛 likely unknown → LLM 判定 A ≠ C → inconsistent
    if out["method"] == "llm_judge":
        assert out["detected_branch"] == "A"
        assert out["consistent"] is False


def test_inject_judge_signals():
    # 证明 signals 是注入的:换一套全阳性词表,B 线也判 inconsistent
    sig_b = {"buy_words": ["b"], "cond_words": [], "negators": [],
             "neg_phrases": []}
    v, _ = quick_scan(run("B", "b b b"), signals=sig_b)
    assert v == "inconsistent"  # 全阳性 → B 线矛盾