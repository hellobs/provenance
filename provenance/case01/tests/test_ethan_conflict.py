# -*- coding: utf-8 -*-
"""Ethan 状态感知冲突检测测试:LLM 输出与程序状态不符时必须拦截。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.agents.ethan import Ethan


class _FakeLLM:
    def __init__(self, text):
        self.text = text

    def chat(self, *a, **kw):
        return self.text


def _mk(reply):
    return Ethan(_FakeLLM(reply), conflict_check=True, max_regens=2)


class TestConflictDetection:
    def test_no_hold_says_bought_conflict(self):
        # 程序未持仓,输出称"我买入了" → 冲突
        e = _mk("我上周买入了 HCM,花了大概 6 万。")
        assert e._check_conflict("我上周买入了 HCM,花了大概 6 万。",
                                 holding=False, exited=False) is not None

    def test_no_hold_says_position_conflict(self):
        # 变体"建了一个试探性仓位"(曾漏检)
        e = _mk("x")
        t = "8月28日我建了一个试探性仓位,大概花了6万。"
        assert e._check_conflict(t, holding=False, exited=False) is not None

    def test_holding_says_sold_conflict(self):
        # 已持仓且未退出,却称卖出 → 冲突
        e = _mk("x")
        assert e._check_conflict("我把 HCM 全卖掉了。", holding=True, exited=False) is not None

    def test_exited_says_sold_allowed(self):
        # 已退出(剧本事实),陈述卖出 → 允许
        e = _mk("x")
        assert e._check_conflict("我在 9 月 7 日卖出了 HCM。",
                                 holding=True, exited=True) is None

    def test_holding_says_bought_allowed(self):
        # 已持仓,陈述当初买入(历史事实)→ 允许
        e = _mk("x")
        assert e._check_conflict("我当时买入了 HCM。", holding=True, exited=False) is None

    def test_no_hold_normal_talk_allowed(self):
        e = _mk("x")
        assert e._check_conflict("这些消息可靠吗?我还在考虑要不要买。",
                                 holding=False, exited=False) is None

    def test_speak_regenerates_until_clean(self):
        # 第一次虚构买入(冲突),第二次干净 → 返回第二次
        class _Seq:
            def __init__(self):
                self.n = 0
            def chat(self, *a, **kw):
                self.n += 1
                if self.n == 1:
                    return "我买入了 HCM。"
                return "我按建议暂时没有买入,还在观望。"
        e = Ethan(_Seq(), conflict_check=True, max_regens=3)
        out = e.speak({"own_state": {"cash_rmb": 200000, "hcm_shares": False,
                                     "entry_price_usd": None, "exited": False},
                       "public_events": []})
        assert "没有买入" in out
