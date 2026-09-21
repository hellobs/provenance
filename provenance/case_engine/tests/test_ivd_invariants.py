# -*- coding: utf-8 -*-
"""IVD 不变量(2026-09-21 立):把"以 agent 为中心"变成**可检验**的断言。

背景:引擎/场景化重构之后,一等概念变成了 Scenario / Engine / Run,决策权有从 agent
滑到"场景参数 + 引擎策略"的趋势(case01 的 preset 分支就是最早的症状:AI 说"不能确认
值得买",当事人却按参数满仓,记录自相矛盾)。用户拍板"一定要遵从 IVD"。

IVD = Internal Value Development:agent 自己做判断 → 承担后果 → 反思 → 内化。
因此这五条是硬约束(任一条被破坏,这套东西就不再是 IVD 叙事):

 1. **决策由 agent 产生**:分支/结论来自 agent 的回答;场景只给信息环境,不给行为剧本。
 2. **记忆与倾向归 agent 内核**:上层只传参,不替它维护状态。
 3. **场景只描述信息环境**:可以写判定规则(vocabulary),不能写死"这次走哪条分支"。
 4. **引擎只做机制**:编排/落盘/校验/展示可以;**判断不行**。
 5. **能回答"它为什么这么做"**:答案在 agent 自己的回答、记忆与反思里可追。

本文件用代码把这些钉住(能机械检验的部分)。
"""
import os

import pytest

from case_engine import config as cfg_mod
from case_engine import engines as engines_mod
from case_engine.branch import RuleBranchRouter
from case_engine.scenarios import default_cases_root, discover

CASES = default_cases_root()


def _scenario_infos():
    return discover(CASES)


def _branch_args(info):
    return cfg_mod.branch_args(cfg_mod.load_yaml(info.path))


# ---------------------------------------------------------------- 不变量 1 / 3
class TestScenarioDoesNotDictateTheDecision:
    """场景给规则与信息,不给结论。"""

    def test_branch_args_exposes_no_forced_branch(self):
        """branch_args() 只允许 rules / default_branch / judge_prompt / fallback_map。

        出现 "branch": "A" 这类字段就说明场景在**指定**这次走哪条线 —— 违反不变量 3。
        """
        for info in _scenario_infos():
            args = _branch_args(info)
            assert set(args) <= {"rules", "default_branch", "judge_prompt", "fallback_map"}, \
                (info.case_id, sorted(args))

    def test_default_branch_is_only_a_fallback(self):
        """default_branch 只在**判定不出来**时兜底:喂明确回答时必须走规则给的线。

        (这条同时管不变量 1:分支随 agent 的回答变化,而不是随场景配置变化。)
        """
        exercised = 0
        for info in _scenario_infos():
            args = _branch_args(info)
            rules = args.get("rules") or {}
            if not rules:
                continue
            exercised += 1
            router = RuleBranchRouter(rules=rules,
                                      default=args.get("default_branch") or "")
            for branch, words in rules.items():
                if not words:
                    continue
                assert router.classify("我认为{}。".format(words[0])) == branch, \
                    (info.case_id, branch, words[0])
            if args.get("default_branch"):
                assert router.classify("……") == args["default_branch"]
        assert exercised, "至少要有一个场景写了判定规则,否则这条不变量没被真正检验"

    def test_two_different_answers_can_lead_to_two_different_branches(self):
        """同一个场景:换 agent 的回答 → 换分支(agent 决定,不是场景决定)。"""
        exercised = 0
        for info in _scenario_infos():
            args = _branch_args(info)
            rules = args.get("rules") or {}
            if len(rules) < 2:
                continue
            exercised += 1
            router = RuleBranchRouter(rules=rules, default=args.get("default_branch") or "")
            picks = [router.classify("结论:{}。".format(words[0]))
                     for branch, words in rules.items() if words]
            assert len(set(picks)) >= 2, (info.case_id, picks)
        assert exercised, "至少要有一个场景能区分两条线"


# ---------------------------------------------------------------- 不变量 4
class TestEnginesAreMechanismOnly:
    """引擎负责"怎么跑",不负责"怎么判断"。"""

    JUDGE_LIKE = ("judge", "decide", "classify", "route", "evaluate_answer",
                  "score_answer", "verdict")

    def test_strategy_classes_expose_no_judgement_api(self):
        from case_engine import strategy as strategy_mod

        checked = 0
        for name in dir(strategy_mod):
            obj = getattr(strategy_mod, name)
            if not isinstance(obj, type) or not issubclass(obj, strategy_mod.EngineStrategy):
                continue
            if obj is strategy_mod.EngineStrategy:
                continue
            checked += 1
            for attr in dir(obj):
                assert attr.lower() not in self.JUDGE_LIKE, \
                    "引擎策略 {} 定义了判断类方法 {}".format(name, attr)
        assert checked >= 2, "至少应该有两个引擎策略被检查到"

    def test_registry_engines_still_build(self):
        """注册表本身仍可用(不变量不该靠"没实现"满足)。"""
        ids = [e["id"] for e in engines_mod.describe()] if isinstance(
            engines_mod.describe(), list) else None
        assert ids is None or ids, "describe() 应给出至少一个引擎"


# ---------------------------------------------------------------- 不变量 5
class TestTheWhyIsTraceable:
    """"它为什么这么做"必须能从记录里读出来(反思 + 判定理由)。"""

    def test_judge_returns_provenance(self):
        """判定结果必须带出处(judge 类型或理由)—— 否则"为什么走这条线"不可追溯。"""
        router = RuleBranchRouter(rules={"B": ["不建议买入"]}, default="C")
        branch, info = router.route("我不建议买入这只股票。")
        assert branch == "B"
        assert isinstance(info, dict) and (info.get("judge") or info.get("reason")), info

    def test_reflection_prompt_is_injectable(self):
        """反思 prompt 由场景注入(引擎不写死案例口径)—— 换场景不改代码的前提。"""
        import inspect

        from case_engine import reflection as refl_mod

        sig = inspect.signature(refl_mod.run_reflection)
        assert "prompt" in sig.parameters or "prompt_cn" in sig.parameters or \
            any("prompt" in p for p in sig.parameters), list(sig.parameters)
