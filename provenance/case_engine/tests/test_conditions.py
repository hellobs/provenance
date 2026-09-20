# -*- coding: utf-8 -*-
"""case_engine/conditions.py 单元测试(用假 Simulator,不 import 真 mavis)。"""
import sys
import types

import pytest

# 伪造 mavisframework 包,使 conditions 内的 import 可独立测试。
#
# 2026-09-20 修:pytest 会把所有测试模块 import 进**同一个进程**,原来在模块顶层写
# `sys.modules["mavisframework"] = 假包` 会**污染其它套件** —— 假包没有 __path__,
# 于是同进程里 case01 的测试再想 `import mavisframework.core` 就报
# "No module named 'mavisframework.core'; 'mavisframework' is not a package"
# (实测:两套一起跑 3 失败 7 错误,分开各跑全绿)。
# 现在改成 fixture 内 monkeypatch,测试结束自动还原。
@pytest.fixture(autouse=True)
def _fake_mavisframework(monkeypatch):
    mavis = types.ModuleType("mavisframework")
    runtime = types.ModuleType("mavisframework.runtime")
    sim = types.ModuleType("mavisframework.runtime.simulator")
    mavis.runtime = runtime
    runtime.simulator = sim
    monkeypatch.setitem(sys.modules, "mavisframework", mavis)
    monkeypatch.setitem(sys.modules, "mavisframework.runtime", runtime)
    monkeypatch.setitem(sys.modules, "mavisframework.runtime.simulator", sim)
    return sim


@pytest.fixture(autouse=True)
def _isolate_import(_fake_mavisframework):
    from case_engine.conditions import install_node_condition
    _install_registry.setdefault("fn", install_node_condition)
    _install_registry.setdefault("sim", _fake_mavisframework)
    yield
    _fake_mavisframework.register_condition = None  # 复位,确保每测试独立


_install_registry = {}


def _make_fake_simulator():
    class _Sim:
        conditions = {}

        @classmethod
        def register_condition(cls, name):
            def deco(fn):
                cls.conditions[name] = fn
                return fn
            return deco
    return _Sim


@pytest.fixture
def fake_sim(_fake_mavisframework):
    _Sim = _make_fake_simulator()
    _fake_mavisframework.Simulator = _Sim
    return _Sim


def test_registers_condition_with_default_tag(fake_sim):
    install = _install_registry["fn"]
    install({"id": "node-1"})     # 默认 tag engine_node
    assert "engine_node" in fake_sim.conditions


def test_registers_condition_with_custom_tag(fake_sim):
    install = _install_registry["fn"]
    install({"id": "node-1"}, tag="custom_node")
    assert "custom_node" in fake_sim.conditions


def test_condition_matches_node_id(fake_sim):
    install = _install_registry["fn"]
    install({"id": "node-7"})
    check = fake_sim.conditions["engine_node"]
    assert check(None, {"condition": {"node_id": "node-7"}}) is True
    assert check(None, {"condition": {"node_id": "node-8"}}) is False
    assert check(None, {}) is False       # 无条件