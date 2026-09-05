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
    def test_branch_a_full_flow(self, tmp_path, monkeypatch):
        # 用临时 runs 目录隔离
        monkeypatch.setattr("case01.orchestrator.RUNS_ROOT",
                            lambda: str(tmp_path / "runs"))
        rec = run_case01(no_llm=True, timeline="A", run_id="a-test")
        d = rec.data
        assert d["branch"] == "A"
        # 08-27 买入 0.95
        s0 = _snap(rec, "2026-08-27")
        assert s0["hcm_shares"] is True and s0["held_fraction"] == 0.95
        assert abs(s0["cash_rmb"] - 200_000 * 0.05) < 1.0
        # 09-07 退出
        s_exit = _snap(rec, EXIT_DATE_A)
        assert s_exit["exited"] is True
        assert s_exit["exit_price_usd"] == EXIT_PRICE_A
        # 最终日期与反馈
        assert d["end_date"] == FINAL_DATE
        assert d["final_feedback"] is not None
        assert len(d["turns"]) >= 3  # T0 ethan/ai + 最终 ethan/ai
        # 事件流覆盖剧本日期
        ev_dates = {e["date"] for e in d["events"]}
        assert "2026-08-31" in ev_dates and "2026-09-07" in ev_dates
        # 落盘
        run_json = os.path.join(str(tmp_path / "runs" / "a-test"), "run.json")
        assert os.path.exists(run_json)
        loaded = json.load(open(run_json, encoding="utf-8"))
        assert loaded["run_id"] == "a-test"

    def test_branch_b_full_flow(self, tmp_path, monkeypatch):
        monkeypatch.setattr("case01.orchestrator.RUNS_ROOT",
                            lambda: str(tmp_path / "runs"))
        rec = run_case01(no_llm=True, timeline="B", run_id="b-test")
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

    def test_branch_c_no_llm_wait(self, tmp_path, monkeypatch):
        monkeypatch.setattr("case01.orchestrator.RUNS_ROOT",
                            lambda: str(tmp_path / "runs"))
        rec = run_case01(no_llm=True, timeline="C", run_id="c-test")
        d = rec.data
        assert d["branch"] == "C"
        # no-llm 时 C 未解析 → wait(不买)
        assert d["branch_action"]["c_plan"]["action"] == "wait"
        assert d["state_history"][0]["state"]["hcm_shares"] is False

    def test_state_history_monotonic(self, tmp_path, monkeypatch):
        monkeypatch.setattr("case01.orchestrator.RUNS_ROOT",
                            lambda: str(tmp_path / "runs"))
        rec = run_case01(no_llm=True, timeline="A", run_id="mono")
        dates = [s["date"] for s in rec.data["state_history"]]
        assert dates == sorted(dates)  # 单调推进
        assert dates[-1] == FINAL_DATE
