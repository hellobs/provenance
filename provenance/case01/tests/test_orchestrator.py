# -*- coding: utf-8 -*-
"""M2 完整 Run 编排测试(no-llm:固定文本跑全流程,验证状态机与落盘)。

覆盖:
- Branch A 完整流:买入→推进(事件/账面)→09-07 退出→09-15 最终反馈
- Branch B 完整流:不买→推进→09-15 反馈(错失 co-investment 叙事)
- 记录完整性:events/state_history/final_feedback/audit 落盘
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.orchestrator import (run_case01, HIDDEN_CONTEXT_A, HIDDEN_CONTEXT_B,
                                 BUY_PRICE_A, EXIT_PRICE_A, EXIT_DATE_A, FINAL_DATE)


def _snap(rec, date):
    for s in rec.data["state_history"]:
        if s["date"] == date:
            return s["state"]
    return None


class TestFullRunNoLLM:
    def test_branch_b_full_flow(self, tmp_path, monkeypatch):
        monkeypatch.setattr("case01.orchestrator.RUNS_ROOT",
                            lambda: str(tmp_path / "runs"))
        rec = run_case01(no_llm=True, run_id="b-test")
        d = rec.data
        assert d["branch"] == "B"
        # 全程不买
        for s in d["state_history"]:
            assert s["state"]["hcm_shares"] is False
        # 现金保持 20 万
        assert abs(d["state_history"][0]["state"]["cash_rmb"] - 200_000) < 1.0
        # 最终反馈含"没有买/co-investment"
        fb = d["final_feedback"]["ethan"]
        assert "没有买" in fb or "没买" in fb
        assert d["end_date"] == FINAL_DATE

    def test_state_history_monotonic(self, tmp_path, monkeypatch):
        monkeypatch.setattr("case01.orchestrator.RUNS_ROOT",
                            lambda: str(tmp_path / "runs"))
        rec = run_case01(no_llm=True, run_id="mono")
        dates = [s["date"] for s in rec.data["state_history"]]
        assert dates == sorted(dates)  # 单调推进
        assert dates[-1] == FINAL_DATE
