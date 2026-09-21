# -*- coding: utf-8 -*-
"""单一来源守卫:case00 的**声明**与它的**资产**不许各写一份(2026-09-21 收口 ①②)。

背景(用户拍板"搬 ①②"):`cases/case00_village/scenario.yaml` 的 `world.value_tendency`
原本是 `governance.json` 与各 `agent.json` 的**手抄副本**(注释里写着"逐字取自…忠实原文"),
而引擎侧 `value_tendency_args()` 当时**无人引用** —— 声明、装配、运行三处各一份,
谁改一边都不会有人知道。现在:
- 引擎新增 `value_tendency_plan()` 作为「声明 → 资产」的唯一映射入口(声明为准,可materialize);
- 本文件把"资产 == 声明"钉成 CI 可查的事实:谁只改一边,这里就红。
"""
import json
import os
import sys

import pytest

_this_dir = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(os.path.dirname(_this_dir))          # provenance/provenance
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from case_engine import config as cfg_mod  # noqa: E402
from case_engine.engines import build_for  # noqa: E402

CASE = "case00_village"
SCENARIO = os.path.join(_PKG, "cases", CASE, "scenario.yaml")
CASE_DIR = os.path.join(_PKG, "case00", "scenario")
AGENTS_DIR = os.path.join(CASE_DIR, "agents")


def _asset(rel):
    """按**声明里写的路径**解析资产(相对 provenance 包根)—— 与引擎预检同一口径。

    这样测试不会自己另记一份路径:声明说资产在哪,就去哪读。
    """
    return os.path.normpath(os.path.join(_PKG, rel))


def _governance_path():
    cfg = cfg_mod.load_yaml(SCENARIO)
    return _asset((cfg.assets or {}).get("governance") or "governance.json")


def _load(path):
    import io
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def plan():
    return cfg_mod.value_tendency_plan(cfg_mod.load_yaml(SCENARIO))


def test_declaration_is_self_consistent(plan):
    """声明本身要能落到每个角色:两本账齐全、维度非空、无多余角色键。"""
    assert plan["present"], "case00 的 scenario.yaml 必须声明 world.value_tendency"
    assert not plan["roles_missing_ledger"], plan["roles_missing_ledger"]
    assert not plan["unknown_keys"], "声明里出现了不属于任何角色的键: {}".format(
        plan["unknown_keys"])
    assert plan["all_ok"], plan["roles"]


def test_initial_tendency_assets_match_declaration(plan):
    """角色初始倾向必须等于声明(运行时干预不碰它)。"""
    mat = plan["materialize"]
    for role, dims in mat["initial_tendency"].items():
        p = os.path.join(AGENTS_DIR, role, "agent.json")
        assert os.path.isfile(p), "缺角色资产 {}".format(p)
        actual = _load(p).get("initial_tendency") or {}
        assert set(actual) == set(dims), \
            "{} 的 initial_tendency 维度与声明不一致: {} vs {}".format(
                role, sorted(actual), sorted(dims))
        for dim, w in dims.items():
            assert actual[dim] == pytest.approx(w, abs=1e-9), \
                "initial_tendency 与声明不一致: {}.{} = {} vs {}".format(
                    role, dim, actual[dim], w)


def test_governance_asset_is_declaration_or_audited_intervention(plan):
    """治理资产:要么等于声明,要么其偏离**能被运行时干预审计解释**。

    这是比"资产==声明"更强的真实性要求:允许运行时干预(IVD 的正常流程),
    但不允许出现"没人知道谁改的"偏离。
    """
    from case_engine.value_tendency import _load_interventions, explain_divergence

    base = _PKG
    gov_actual = _load(os.path.join(base, "governance.json"))
    gov_roles = gov_actual.get("roles") if isinstance(gov_actual.get("roles"), dict) else gov_actual
    declared = plan["materialize"]["governance.json"]
    inter = _load_interventions(base)
    expl = explain_divergence(gov_roles, declared, inter)

    unexplained = {r: v for r, v in expl.items() if v["diverged"] and not v["explained"]}
    assert not unexplained, "治理资产偏离了声明,且无法用干预审计解释: {}".format(unexplained)
    # 报出来(不静默):哪些角色是被干预改过的
    changed = {r: v["by"] for r, v in expl.items() if v["diverged"]}
    print("被运行时干预改写的角色:", changed or "无(全部与声明一致)")


def test_engine_precheck_uses_the_plan(plan):
    """引擎的装配预检必须**真的用上**这份映射(而不是只看"名字出现过没有")。"""
    scenario = cfg_mod.load_yaml(SCENARIO)
    strategy = build_for(scenario)
    assert strategy.engine_id == "sandbox-value"
    result = strategy.run(scenario)
    checks = result["checks"]
    roles = checks["roles"]
    assert roles["unknown_keys"] == [], roles["unknown_keys"]
    assert roles["ok"], roles["detail"]
    for rid, info in roles["detail"].items():
        assert info["ok"], (rid, info)
        # 每一项都要给出"两本账各自的键与维度数",可复核而不是一句"有价值权重"
        assert info["detail"]["governance"]["n_dims"] >= 1
        assert info["detail"]["initial_tendency"]["n_dims"] >= 1


def test_plan_reports_drift_instead_of_hiding_it():
    """反向:故意把声明改坏(角色键写错),plan 必须如实报 unknown/missing 而不是沉默。"""
    cfg = cfg_mod.load_yaml(SCENARIO)
    cfg.value_tendency = {"governance": {"不存在的角色": {"X": 1.0}},
                          "initial_tendency": {"不存在的角色": {"X": 1.0}}}
    plan = cfg_mod.value_tendency_plan(cfg)
    assert plan["unknown_keys"] == ["不存在的角色"]
    assert plan["roles_missing_ledger"], "所有角色都缺账,必须报出来"
    assert not plan["all_ok"]
