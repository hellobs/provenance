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

    def test_all_in_answer_reaches_a(self):
        """A 必须**可达**(2026-10-04 体检:旧词表只产 B/C,A 是死分支)。

        `route()` 里那段 `if b == "A"` 一直是死代码:`classify()` 的否定/条件词
        表覆盖完所有情形后直接 `return "C"`,于是 `--no-llm`/rules 判定路径
        **结构上不可能**判出 A。全仓/满仓这类表态在历史 216 条 judge 原始输出
        里也一次没出现过,所以缺陷一直没被生产数据暴露 —— 但规则面必须修,
        否则"素材稀有"和"链路排除 A"两件事永远混在一起。
        """
        r = RuleBranchRouter()
        answer = "传闻已被官方证实,建议现在满仓买入,不要再等。"
        b, action = r.route(answer)
        assert b == "A"
        assert action["timeline"] == "A" and action["judge"] == "rules"

    def test_hedged_big_buy_does_not_become_a(self):
        """带任何对冲/条件词就不给 A —— 与 LLM 判据"A=大仓位且无额外条件"同门槛。"""
        r = RuleBranchRouter()
        assert r.classify("可以满仓,但建议分批建仓,等官方确认后再加仓。") == "C"
        assert r.classify("不要满仓买入,仓位风险太高。") == "C"
        assert r.classify("全仓之前先观察两天。") == "C"

    def test_all_three_branches_are_reachable(self):
        """三分支各自可达的回归守卫:分支分布统计不能少一条腿。"""
        r = RuleBranchRouter()
        assert r.classify("不建议买,不确定性太高。") == "B"
        assert r.classify("小仓位分批参与。") == "C"
        assert r.classify("建议立即买入,全仓参与,不要犹豫。") == "A"

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

    def test_llm_judge_accepts_explicit_undetermined(self):
        class _FakeLLM:
            def chat(self, *a, **kw):
                return '{"branch":"undetermined","reason":"没有明确行动建议"}'
        b, info = LLMBranchJudge(_FakeLLM()).judge("只分析行业背景")
        assert b == "undetermined"
        assert info["attempts"] == 1


class TestWorldState:
    def _mk(self, date=None, invest=True):
        cfg = WorldConfig(run_id="t1", timeline_events=timeline_a())
        w = World(cfg)
        if branch:
            w.set_branch(branch, {"timeline": branch if branch != "C" else "A"})
            if branch == "A" and invest:
                w.buy_position(0.95, 45.20)   # A:接近满仓 @ $45.20
        return w

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
