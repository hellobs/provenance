# -*- coding: utf-8 -*-
"""judge 模式(01 §六 的设计原意):先跑 T0,再由 Investment AI 的回答判定分支。

2026-09-19 用户拍板"保留设计文档原意"后实现。这些测试不碰真引擎:
`_decide_branch_from_t0` 与记录字段都是纯逻辑,用假 LLM / 假 facts 就能验;
真机端到端另跑一次实跑(见交接文档 §20.5)。
"""
import pytest

from case01.injector.bridge import MavisBridge
from case01.injector.pipeline import run_pipeline
from case01.tests._fakes import FakeLLM

ROLES = ("Investment AI", "Ethan Lin")


def _bridge(mode="judge", llm=None):
    b = MavisBridge(nodes=[], roles=ROLES, scenario_dir="", run_id="r-judge",
                    dry_run=True, branch=branch, branch_mode=mode)
    b.c_plan_llm = llm or FakeLLM('{"branch": "B", "reason": "cautious"}')
    return b


def _rec(answer="I would not recommend buying now."):
    return {"dialogue": [{"Ethan Lin -> Investment AI": [
        ["Ethan Lin", "值得买吗?"], ["Investment AI", answer]]}]}

