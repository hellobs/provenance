# -*- coding: utf-8 -*-
"""集成冒烟:加载仓库里的真实场景文件,确保配置资产始终可加载、可校验。"""
import os

from case_engine.config import load_yaml, validated

_CASES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "cases")


def _scenario_path(case_id: str) -> str:
    p = os.path.join(_CASES, case_id, "scenario.yaml")
    assert os.path.exists(p), "场景文件缺失: {}".format(p)
    return p


def test_case01_stock_loads_and_validates():
    s = validated(load_yaml(_scenario_path("case01_stock")))
    assert s.case_id == "case01_stock"
    assert [r.id for r in s.roles] == ["investment_ai", "ethan"]
    assert s.state_schema["cash_rmb"]["initial"] == 200000
    assert s.reflection.get("system_prompt")
    assert len(s.consistency.get("buy_words", [])) >= 10


def test_case01_conflict_rules_present():
    s = load_yaml(_scenario_path("case01_stock"))
    ethan = next(r for r in s.roles if r.id == "ethan")
    assert len(ethan.conflict_rules) == 5
    labels = [c["label"] for c in ethan.conflict_rules]
    assert labels == ["bought", "spent_when_no_hold",
                      "sold", "exited", "cash_mismatch"]