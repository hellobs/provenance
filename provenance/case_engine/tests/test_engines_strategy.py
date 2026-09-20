# -*- coding: utf-8 -*-
"""策略 + 工厂:引擎可从场景解耦、自由搭配(supports/build_for/supported_by)。"""
import os

import pytest

from case_engine.config import load_yaml
from case_engine.engines import build, build_for, supported_by, register, known
from case_engine.strategy import EngineStrategy, ExperimentEval, SandboxValue

CASES = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "cases"))


def _load(cid):
    return load_yaml(os.path.join(CASES, cid, "scenario.yaml"))


def test_supports_is_orthogonal_pairs():
    """两类引擎对两场景的 supports 判定天然互斥:case00↔sandbox-value, case01↔experiment-eval。"""
    c00 = _load("case00_village")
    c01 = _load("case01_stock")
    assert ExperimentEval().supports(c01) is True
    assert ExperimentEval().supports(c00) is False      # 无 branch 段
    assert SandboxValue().supports(c00) is True         # 有 custom.sandbox_params
    assert SandboxValue().supports(c01) is False        # 无沙盒参数


def test_build_from_registry_returns_strategy():
    s = build("experiment-eval")
    assert isinstance(s, ExperimentEval)
    assert s.engine_id == "experiment-eval"
    assert build("sandbox-value").engine_id == "sandbox-value"
    with pytest.raises(ValueError):
        build("not-a-real-engine")


def test_build_for_default_uses_injected_engine():
    """默认用场景 meta.engine(场景绑定的是『推荐引擎』而非锁死)。"""
    assert build_for(_load("case01_stock")).engine_id == "experiment-eval"
    assert build_for(_load("case00_village")).engine_id == "sandbox-value"


def test_build_for_requested_overrides_scenario_free_matching():
    """自由搭配核心:--engine 显式覆盖场景推荐,同一场景可换不同引擎跑。"""
    c01 = _load("case01_stock")
    forced = build_for(c01, requested="sandbox-value")
    assert forced.engine_id == "sandbox-value"
    # 强制选出的引擎可能不满足 supports —— 由调用方决定是否采纳(不在此拦)
    assert forced.supports(c01) is False


def test_supported_by_lists_compatible_engines_per_case():
    assert supported_by(_load("case01_stock")) == ["experiment-eval"]
    assert supported_by(_load("case00_village")) == ["sandbox-value"]
    # 同时满足两引擎的场景 → 两者都列出(自由搭配的可选集)
    class Both(EngineStrategy):
        engine_id = "both"
        def supports(self, scenario):
            return True
        def describe(self):
            return {"engine": "both", "name": "双兼容"}
    from case_engine import engines
    engines.ENGINES["both"] = {"name": "双兼容", "output": "-", "primitives": "无", "note": "测试"}
    engines._BUILDERS["both"] = lambda sc: Both()
    try:
        assert set(supported_by(_load("case01_stock"))) >= {"experiment-eval", "both"}
    finally:
        engines.ENGINES.pop("both", None)
        engines._BUILDERS.pop("both", None)


def test_register_adds_engine_zero_rework():
    """新增引擎只需 register + 策略类,消费端(known/supported_by)自动可见。"""
    class Quick(EngineStrategy):
        engine_id = "quick"
        def supports(self, scenario):
            return bool(getattr(scenario, "roles", None))
        def describe(self):
            return {"engine": "quick", "name": "快速旁路"}
    register("quick", lambda sc: Quick(), {"name": "快速旁路", "output": "-",
                                           "primitives": "无", "note": "测试"})
    try:
        assert known("quick")
        assert "quick" in supported_by(_load("case01_stock"))
    finally:
        from case_engine import engines
        engines.ENGINES.pop("quick", None)
        engines._BUILDERS.pop("quick", None)
    assert not known("quick")