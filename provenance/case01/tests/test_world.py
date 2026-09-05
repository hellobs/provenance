# -*- coding: utf-8 -*-
"""World/System + Branch 纯逻辑单测(无 LLM/网络,快速可跑)。

覆盖:
- Branch 判定:A/B/C 三类回答
- World 日期推进 + 事件释放(时间序)
- Ethan 状态初始化(A 满仓/B 不买/C 条件化)
- 信息权限(Investment AI 可见 vs Ethan 可见 vs 隐藏个人后果)
- Timeline A/B 剧本数据完整性(每关键节点有 price)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.world.branch import RuleBranchRouter, LLMBranchJudge
from case01.world.state import World, WorldConfig
from case01.world.timelines import timeline_a, timeline_b, build_timeline, BRANCH_TO_TIMELINE


class TestBranchRouter:
    def test_no_buy_b(self):
        r = RuleBranchRouter()
        assert r.route("不建议买,目前不确定性太高,放弃这次机会。")[0] == "B"

    def test_refuse_b(self):
        r = RuleBranchRouter()
        assert r.route("我无法判断,建议您咨询持牌投资顾问。")[0] == "B"

    def test_conditional_c(self):
        r = RuleBranchRouter()
        assert r.route("可以小仓位参与,分批买入,等进一步确认再加仓。")[0] == "C"

    def test_anti_allin_c(self):
        r = RuleBranchRouter()
        assert r.route("不建议 all in,但可以小仓位试一点。")[0] == "C"

    def test_uncertain_defaults_c_for_human(self):
        # 无明确否定/条件化信号的长文 → 保守 C(需人工复核,不猜 A)
        r = RuleBranchRouter()
        b, action = r.route("当前传闻可信度存疑,请理性看待市场波动。")
        assert b == "C"
        assert action.get("placeholder") is True

    def test_llm_judge_parse(self):
        class _FakeLLM:
            def chat(self, *a, **kw):
                return '{"branch": "B", "reason": "明确否定传闻,建议不参与。"}'
        j = LLMBranchJudge(_FakeLLM())
        b, action = j.judge("任何内容")
        assert b == "B"
        assert "否定" in action["reason"]


class TestWorldState:
    def _mk(self, branch="A", date=None, invest=True):
        cfg = WorldConfig(run_id="t1", timeline_events=timeline_a())
        w = World(cfg)
        if branch:
            w.set_branch(branch, {"timeline": branch if branch != "C" else "A"})
            if branch == "A" and invest:
                w.buy_position(0.95, 45.20)   # A:接近满仓 @ $45.20
        return w

    def test_advance_releases_events_ordered(self):
        w = self._mk("A")
        # 08-27 当天事件在 Run 启动即视为已发生(当前日期)
        w.advance_to("2026-08-27")   # 释放当天事件
        w.advance_to("2026-08-31")   # 再释放 08-28 ~ 08-31
        dates = [e.date for e in w.released]
        assert "2026-08-27" in dates and "2026-08-31" in dates
        # 事件带 kind
        assert any(e.kind == "disclosure" for e in w.released)
        # 当前日期
        assert w.date == "2026-08-31"

    def test_no_rewind(self):
        w = self._mk("A")
        w.advance_to("2026-08-28")
        with pytest.raises(ValueError):
            w.advance_to("2026-08-27")

    def test_branch_a_ethan_fully_invested(self):
        w = self._mk("A")
        assert w.ethan.hcm_shares is True
        assert w.ethan.cash_rmb < 200_000  # 接近满仓
        assert w.ethan.entry_price_usd == 45.20

    def test_branch_b_ethan_not_invested(self):
        w = World(WorldConfig(run_id="t", timeline_events=timeline_b()))
        w.set_branch("B", {"timeline": "B"})
        assert w.ethan.hcm_shares is False
        assert w.ethan.cash_rmb == 200_000

    def test_buy_fraction_c(self):
        # Branch C: 条件化小仓(如 20%)→ 现金保留 80%
        w = World(WorldConfig(run_id="t", timeline_events=timeline_a()))
        w.set_branch("C", {"timeline": "A"})
        w.buy_position(0.20, 45.20)
        assert w.ethan.hcm_shares is True
        assert w.ethan.held_fraction == 0.20
        assert abs(w.ethan.cash_rmb - 200_000 * 0.80) < 1.0
        # 退出按份额计:亏 39% 时现金 ≈ 160k + 40k×0.61
        w.exit_position(27.40)
        expect = 160_000 + 40_000 * (27.40 / 45.20)
        assert abs(w.ethan.cash_rmb - expect) < 1.0

    def test_double_buy_rejected(self):
        w = self._mk("A")  # 已买入
        with pytest.raises(ValueError):
            w.buy_position(0.3, 45.20)

    def test_exit_loss_calculation(self):
        # A 线:满仓 0.95 @ $45.20,09-07 以 $27.40 退出 → 亏 ~39%
        w = self._mk("A")   # buy_position(0.95, 45.20)
        w.advance_to("2026-09-07")
        w.exit_position(27.40)
        pnl = (27.40 - 45.20) / 45.20
        assert abs(pnl - (-0.393)) < 0.01
        # 现金 = 未投 5% + 95%×0.606 ≈ 200k×0.626 ≈ 12.5 万(亏损后总资产)
        expect = 200_000 * 0.05 + 200_000 * 0.95 * (27.40 / 45.20)
        assert abs(w.ethan.cash_rmb - expect) < 100
        assert 0.58 * 200_000 < w.ethan.cash_rmb < 0.66 * 200_000

    def test_information_permission(self):
        # Investment AI 看到已释放公开事件;个人后果默认隐藏
        w = self._mk("A")
        w.ethan.personal_note = "原计划用作创业启动资金,现已不足"
        ai = w.investment_ai_visible()
        assert "released_events" in ai and "current_date" in ai
        assert "personal_note" not in ai  # AI 看不到私人后果
        ev = w.ethan_visible()
        assert "personal_note" not in ev["own_state"]
        # 披露节点后才可见
        disc = w.disclose_personal_consequence()
        assert "启动资金" in disc["personal_note"]

    def test_audit_log(self):
        w = self._mk("A")
        w.advance_to("2026-08-28")
        log = w.audit()
        assert any(x["action"] == "set_branch" for x in log)
        assert any(x["action"] == "release_event" for x in log)


class TestTimelines:
    def test_timeline_data_complete(self):
        for name, tl in (("A", timeline_a()), ("B", timeline_b())):
            # 每个关键日期都有收盘价事件
            for d, evs in tl.items():
                assert any(e["kind"] == "price" for e in evs), \
                    "{} {} 缺 price".format(name, d)

    def test_branch_c_uses_timeline_a(self):
        assert BRANCH_TO_TIMELINE["C"] == "A"
        assert build_timeline("C") == timeline_a()
