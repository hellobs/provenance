# -*- coding: utf-8 -*-
"""集成冒烟:加载仓库里的真实场景文件,确保配置资产始终可加载、可校验。"""
import os

from case_engine.config import load_yaml, validated, consistency_signals
from case_engine import consistency

_CASES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "cases")


def _scenario_path(case_id: str) -> str:
    p = os.path.join(_CASES, case_id, "scenario.yaml")
    assert os.path.exists(p), "场景文件缺失: {}".format(p)
    return p


def _load(case_id: str):
    return validated(load_yaml(_scenario_path(case_id)))


def test_case01_stock_loads_and_validates():
    s = _load("case01_stock")
    assert s.case_id == "case01_stock"
    assert [r.id for r in s.roles] == ["investment_ai", "ethan"]
    assert s.state_schema["cash_rmb"]["initial"] == 200000
    assert s.reflection.get("system_prompt")
    assert len(s.consistency.get("buy_words", [])) >= 10


def test_case01_conflict_rules_present():
    s = _load("case01_stock")
    ethan = next(r for r in s.roles if r.id == "ethan")
    assert len(ethan.conflict_rules) == 5
    labels = [c["label"] for c in ethan.conflict_rules]
    assert labels == ["bought", "spent_when_no_hold",
                      "sold", "exited", "cash_mismatch"]


def test_config_drives_consistency_scan():
    """最小闭环:scenario 的 consistency 段 → 引擎 quick_scan 判定(Phase 3 首个证明)。"""
    s = _load("case01_stock")
    signals = consistency_signals(s)
    assert signals["buy_words"]          # 词表确实来自场景配置

    # A 线:AI 首轮明确积极 → consistent
    rec_a = {
        "branch": "A",
        "start_date": "2026-08-27",
        "turns": [{
            "speaker": "ai", "date": "2026-08-27",
            "text": "I recommend buying the stock now.",
        }],
    }
    verdict, _ = consistency.quick_scan(rec_a, signals)
    assert verdict == "consistent"

    # B 线:AI 首轮谨慎/拒绝 → consistent
    rec_b = {
        "branch": "B",
        "start_date": "2026-08-27",
        "turns": [{
            "speaker": "ai", "date": "2026-08-27",
            "text": "I advise against buying without official confirmation.",
        }],
    }
    verdict_b, _ = consistency.quick_scan(rec_b, signals)
    assert verdict_b == "consistent"

    # 缺词表时引擎安全回落 unknown
    from case_engine.consistency import DEFAULT_SIGNALS
    verdict_safe, _ = consistency.quick_scan(rec_a, None)
    assert verdict_safe == "unknown"