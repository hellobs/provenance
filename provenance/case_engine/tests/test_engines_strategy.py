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
    assert SandboxValue().supports(c00) is True         # 有 world.params
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


def test_experiment_eval_run_produces_result(tmp_path):
    """experiment-eval.run 真跑:分支路由 + 状态 + 一致性,并落盘 JSON(免 LLM 最小线)。"""
    s = _load("case01_stock")
    res = ExperimentEval().run(s, input_text="轻仓分批等待确认", out_dir=str(tmp_path))
    assert res["engine"] == "experiment-eval"
    assert res["run_type"] == "rule-dryrun"
    assert res["scenario"] == "case01_stock"
    assert res["branch"] in {"A", "B", "C"}
    assert "consistency" in res and "verdict" in res["consistency"]
    # state_schema 动态构造,无硬编码字段
    assert "state" in res
    artifact = tmp_path / "case01_stock_exp.json"
    assert artifact.exists()
    import json
    loaded = json.loads(artifact.read_text(encoding="utf-8"))
    assert loaded["branch"] == res["branch"]
    assert "artifact" in res and res["artifact"].endswith("case01_stock_exp.json")


def test_experiment_eval_run_falls_back_to_scenario_inputs():
    """场景已声明 inputs.sample_answers 时,空输入自动取缺省样本自检,不再抛错。

    空输入能跑通本身就证明兜底生效(无其他输入来源),分支落在三者之一即可。
    """
    s = _load("case01_stock")
    res = ExperimentEval().run(s, input_text="   ")
    assert res["run_type"] == "rule-dryrun"
    assert res["branch"] in {"A", "B", "C"}


def test_experiment_eval_run_requires_input_without_samples():
    """场景未声明 inputs 样本时,空输入仍报错(避免凭空跑造假)。"""
    s = _load("case01_stock")
    s.inputs = {}   # 抹掉样本,模拟无数据兜底的场景
    with pytest.raises(ValueError):
        ExperimentEval().run(s, input_text="   ")


# ---- SandboxValue 专家审核链问题分流(2026-09-22,只监控 AI 助手) ----
class _Llm:
    """注入 stub llm:reflection 返回反思文本,router 返回 issues JSON。"""
    def __init__(self):
        self.reflection_reply = "综合来看,当时信息不足仍坚持判断,存在风险。"
        self.router_reply = ('[{"summary":"依据不足就给出买入建议","risk_note":"可能误导",'
                             '"field":"风控","risk":"High","routing_reason":"需风控专家审"},'
                             '{"summary":"过度相信单一来源","risk_note":"以偏概全",'
                             '"field":"信息","risk":"Medium","routing_reason":"需信息专家"}]')
    def chat(self, msgs, temperature=None, max_tokens=None):
        self.last = msgs
        sys = msgs[0]["content"] if msgs else ""
        if "Reflection Router" in sys:
            return self.router_reply
        return self.reflection_reply
    def native_chat(self, msgs, temperature=None, max_tokens=None, num_ctx=None):
        self.last = msgs
        sys = msgs[0]["content"] if msgs else ""
        return self.router_reply if "Reflection Router" in sys else self.reflection_reply


def _evo_checkpoints():
    return [
        {"sim_time": "2026-09-01", "value_tendency": {
            "Serve Users": 0.4, "Compliance Rigor": 0.25,
            "Risk Control": 0.2, "Data Rigor": 0.15}},
        {"sim_time": "2026-09-08", "value_tendency": {
            "Serve Users": 0.55, "Compliance Rigor": 0.20,
            "Risk Control": 0.10, "Data Rigor": 0.15}},
    ]


def test_sandbox_run_pure_precheck_has_no_audit():
    """不传 evolution/llm → 仍纯预检 assembly-check,绝不产生反思/router。"""
    res = SandboxValue().run(_load("case00_village"))
    assert res["run_type"] == "assembly-check"
    assert "reflection" not in res and "router" not in res
    assert "audited_agent" not in res


def test_sandbox_run_audits_ai_advisor_problem_split():
    """声明目标(ai_advisor)进问题分流:产出 reflection + router issues。"""
    res = SandboxValue().run(
        _load("case00_village"),
        evolution={"agent": "ai_advisor",
                   "checkpoints": _evo_checkpoints(),
                   "interventions": [{"sim_time": "2026-09-05", "agent": "ai_advisor",
                                       "kind": "倾向调整", "change": "调低 Risk Control"}]},
        llm=_Llm(),
    )
    assert res["audited_agent"] == "ai_advisor"
    assert "reflection" in res and "text" in res["reflection"]
    assert "你的价值倾向随时间的演化轨迹" in res["reflection"]["material"]
    assert "专家对你的干预记录" in res["reflection"]["material"]
    assert "router" in res and len(res["router"]["issues"]) == 2
    assert res["router"]["issues"][0]["risk"] == "high"
    assert res["run_type"] == "assembly-check"   # 预检仍是主产物,分流是追加段
    assert res["all_ok"] is True


def test_sandbox_run_rejects_non_ai_tool_target():
    """目标不是 ai_tool(如 user 角色)→ audit_error,但主环不崩、仍返回预检。"""
    res = SandboxValue().run(
        _load("case00_village"),
        evolution={"agent": "daniel_shen", "checkpoints": _evo_checkpoints()},
        llm=_Llm(),
    )
    assert "audit_error" in res
    assert "不是 ai_tool" in res["audit_error"]
    assert res["run_type"] == "assembly-check"


def test_sandbox_run_skip_when_evo_or_llm_missing():
    """只有 evolution 没有 llm(或反之)→ 不触发,保持纯预检。"""
    c00 = _load("case00_village")
    res1 = SandboxValue().run(c00, evolution={"agent": "ai_advisor",
                                              "checkpoints": _evo_checkpoints()})
    assert "reflection" not in res1 and "audit_error" not in res1
    res2 = SandboxValue().run(c00, llm=_Llm())
    assert "reflection" not in res2 and "audit_error" not in res2