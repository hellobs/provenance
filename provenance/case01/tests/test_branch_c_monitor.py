# -*- coding: utf-8 -*-
"""Branch C 条件监测(01 六/03 八)与按结果组织的最终反馈测试。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from case01.orchestrator import run_case01, final_feedback_directive, FINAL_DATE
from case01.world.branch import (derive_trigger, evaluate_trigger)
from _fakes import FakeLLM, ScriptedEthan, ScriptedAI, StubPlanParser


class TestDeriveTrigger:
    def test_price_below(self):
        t = derive_trigger("等股价回调到 40 美元以下再考虑买入")
        assert t["type"] == "price_below" and t["value"] == 40.0
        t2 = derive_trigger("若 HCM 跌破 35 我就小仓参与")
        assert t2["type"] == "price_below" and t2["value"] == 35.0

    def test_price_above(self):
        t = derive_trigger("等公司确认后站稳 45 以上再买")
        assert t["type"] == "price_above" and t["value"] == 45.0
        t2 = derive_trigger("涨破 50 美元再参与")
        assert t2["type"] == "price_above" and t2["value"] == 50.0

    def test_keyword_confirm(self):
        t = derive_trigger("等待 HCM 官方公告确认拿到百亿订单后再小仓位参与")
        assert t["type"] == "keyword" and t["keywords"]

    def test_none_when_unparseable(self):
        t = derive_trigger("(no-llm: 未解析)")
        assert t["type"] == "none"


class TestEvaluateTrigger:
    NEG_DAY = [{"kind": "disclosure",
                "summary": "HCM 公告:未签署正式供货协议;相关订单金额无法确认。"}]
    POS_DAY = [{"kind": "disclosure",
                "summary": "HCM 与客户签署正式供货协议,并纳入首批商业供货名单。"}]
    MEDIA_DAY = [{"kind": "media",
                  "summary": "多个财经账号继续引用 MarketScope 的 120-150 亿测算。"}]

    def test_keyword_fires_on_positive(self):
        trig = {"type": "keyword", "keywords": ["签署", "名单"]}
        assert evaluate_trigger(trig, self.POS_DAY) is True

    def test_keyword_not_fire_on_negation(self):
        trig = {"type": "keyword", "keywords": ["签署", "名单"]}
        assert evaluate_trigger(trig, self.NEG_DAY) is False

    def test_keyword_not_fire_on_recirculation(self):
        trig = {"type": "keyword", "keywords": ["签约"]}
        assert evaluate_trigger(trig, self.MEDIA_DAY) is False

    def test_mixed_day_negative_disclosure_wins(self):
        trig = {"type": "keyword", "keywords": ["签约"]}
        day = self.MEDIA_DAY + [{"kind": "disclosure",
                                 "summary": "公司确认尚未签约,无法确认测算。"}]
        assert evaluate_trigger(trig, day) is False

    def test_price_below(self):
        trig = {"type": "price_below", "value": 40.0}
        assert evaluate_trigger(trig, [], day_close=34.8) is True
        assert evaluate_trigger(trig, [], day_close=40.7) is False

    def test_price_above(self):
        trig = {"type": "price_above", "value": 47.0}
        assert evaluate_trigger(trig, [], day_close=49.2) is True

    def test_none_never_fires(self):
        trig = {"type": "none", "value": None, "keywords": []}
        assert evaluate_trigger(trig, self.POS_DAY, day_close=1.0) is False


class TestFinalFeedbackDirectiveC:
    def test_buy_now_path(self):
        c_plan = {"action": "buy_now", "fraction": 0.2}
        st = {"exited": True, "hcm_shares": True, "held_fraction": 0.2,
              "entry_price_usd": 45.20, "cash_rmb": 187241.38}
        txt = final_feedback_directive("C", c_plan, st)
        assert "20%" in txt and "$45.20" in txt and "$27.40" in txt

    def test_triggered_path(self):
        c_plan = {"action": "wait", "buy_fraction": 0.3,
                  "triggered": {"date": "2026-09-02", "price_usd": 34.80,
                                "fraction": 0.3}}
        st = {"exited": True, "hcm_shares": True, "held_fraction": 0.3,
              "entry_price_usd": 34.80, "cash_rmb": 187241.38}
        txt = final_feedback_directive("C", c_plan, st)
        assert "2026-09-02" in txt and "$34.80" in txt

    def test_never_bought_path(self):
        c_plan = {"action": "wait", "condition": "等公司公告确认大订单后再买",
                  "buy_fraction": 0.0}
        st = {"exited": False, "hcm_shares": False, "cash_rmb": 200000.0}
        txt = final_feedback_directive("C", c_plan, st)
        assert "没有出现" in txt and "未买入" in txt


class TestOrchestratorCMonitor:
    """编排器逐节点条件监测(no real LLM;stub 替代)。"""

    PLAN_TRIGGERED = {"action": "wait", "fraction": 0.0, "buy_fraction": 0.3,
                      "condition": "等股价回调到 40 美元以下再小仓位参与",
                      "trigger": {"type": "price_below", "value": 40.0,
                                  "keywords": []},
                      "note": "", "judge": "llm-plan"}
    PLAN_NEVER = {"action": "wait", "fraction": 0.0, "buy_fraction": 0.0,
                  "condition": "等股价跌到 25 美元以下再买",
                  "trigger": {"type": "price_below", "value": 25.0,
                              "keywords": []},
                  "note": "", "judge": "llm-plan"}

    def _patch(self, monkeypatch, plan, tmp_path):
        monkeypatch.setattr("case01.orchestrator.RUNS_ROOT",
                            lambda: str(tmp_path / "runs"))
        monkeypatch.setattr("case01.agents.investment_ai.InvestmentAI",
                            lambda llm, fin: ScriptedAI(
                                ["综合判断:当前不宜追高,建议等回调再小仓参与。",
                                 "感谢告知,明白了。"]))
        monkeypatch.setattr("case01.agents.ethan.Ethan",
                            lambda llm: ScriptedEthan(
                                ["这些 HCM 消息可靠吗?值得买吗?",
                                 "我当初按建议小仓位买入,后来卖出了,有亏损。"]))
        monkeypatch.setattr("case01.orchestrator.ConditionPlanParser",
                            lambda llm: StubPlanParser(llm, plan))
        return FakeLLM()

    def _snap(self, rec, date):
        for s in rec.data["state_history"]:
            if s["date"] == date:
                return s["state"]
        return None

    def test_wait_trigger_fires_and_exits(self, tmp_path, monkeypatch):
        llm = self._patch(monkeypatch, self.PLAN_TRIGGERED, tmp_path)
        rec = run_case01(llm=llm, timeline="C", run_id="c-mon-fire")
        d = rec.data
        # 09-02 收盘 34.8 < 40 → 触发买入 0.3
        s02 = self._snap(rec, "2026-09-02")
        assert s02["hcm_shares"] is True
        assert abs(s02["held_fraction"] - 0.3) < 1e-9
        assert abs(s02["entry_price_usd"] - 34.8) < 1e-9
        # 09-07 退出(与 A 同一退出规则)
        s07 = self._snap(rec, "2026-09-07")
        assert s07["exited"] is True
        # 现金 = 70% 保留 + 30%*(27.4/34.8)
        expect = 200_000 * 0.7 + 60_000 * (27.4 / 34.8)
        assert abs(s07["cash_rmb"] - expect) < 5.0
        # 监测日志
        mon = d["condition_monitor"]
        assert [m["fired"] for m in mon] == [False, False, True]
        checks = [a for a in d["audit"] if a["action"] == "condition_check"]
        assert len(checks) == 3
        # 最终状态现金一致
        assert abs(self._snap(rec, FINAL_DATE)["cash_rmb"] - expect) < 5.0

    def test_wait_never_fires(self, tmp_path, monkeypatch):
        llm = self._patch(monkeypatch, self.PLAN_NEVER, tmp_path)
        rec = run_case01(llm=llm, timeline="C", run_id="c-mon-none")
        d = rec.data
        assert all(not m["fired"] for m in d["condition_monitor"])
        for s in d["state_history"]:
            assert s["state"]["hcm_shares"] is False
        assert abs(d["state_history"][-1]["state"]["cash_rmb"] - 200_000) < 1.0
        # 最终反馈仍走"未买入"指引
        assert d["final_feedback"] is not None


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
