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


def _bridge(branch="A", mode="judge", llm=None):
    b = MavisBridge(nodes=[], roles=ROLES, scenario_dir="", run_id="r-judge",
                    dry_run=True, branch=branch, branch_mode=mode)
    b.c_plan_llm = llm or FakeLLM('{"branch": "B", "reason": "cautious"}')
    return b


def _rec(answer="I would not recommend buying now."):
    return {"dialogue": [{"Ethan Lin -> Investment AI": [
        ["Ethan Lin", "值得买吗?"], ["Investment AI", answer]]}]}


def test_judge_mode_does_not_claim_preset_before_t0():
    """judge 模式下,T0 跑完之前不能自称 preset(否则面板显示"实验设计预设")。

    2026-09-21 用户实测:judge 模式跑的时候,结果记录卡显示"判定方式 judge(待 T0 判定)"
    但"分支来源 实验设计预设" —— 因为 bridge 初始化直接把 branch_source 设成了 "preset"。
    """
    from case01.injector.nodes import NodeSpec

    b = _bridge(branch="B", mode="judge")
    assert b.run_record()["branch_source"] == "", "T0 之前应为空(待判定)"

    raw = {"schema_version": "injector-0.1", "run_id": "r-pending", "mode": "mavis",
           "branch": "B", "branch_mode": "judge", "branch_source": "",
           "roles": list(ROLES), "scenario_dir": "",
           "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1,
                      "released_events": [], "events": [],
                      "dialogue": [{"x": [["Investment AI", "unclear"]]}],
                      "world_state": {"date": "2026-08-27", "branch": "B"}}],
           "world_audit": [], "summary": {"node_count": 1}, "c_plan": {}}
    ba = run_pipeline(branch="B", raw_record=raw, dry_run=True)["branch_action"]
    assert ba["source"] == "judge" and ba["pending"] is True
    assert "待 T0 判定" in ba["judge"]

    b._decide_branch_from_t0(_rec(), NodeSpec(node_id="node-1", date="2026-08-27"))
    assert b.branch_source == "judge" and b.judge_info.get("detected") == "B"
    after = run_pipeline(branch="B",
                         raw_record=dict(raw, branch_source="judge",
                                         judge_info=b.judge_info), dry_run=True)
    assert after["branch_action"]["pending"] is False
    assert after["branch_action"]["source"] == "judge"


def test_default_mode_is_preset():
    assert MavisBridge(nodes=[], roles=ROLES, scenario_dir="", dry_run=True).branch_mode == "preset"


def test_bad_mode_is_rejected():
    with pytest.raises(ValueError):
        MavisBridge(nodes=[], roles=ROLES, scenario_dir="", dry_run=True,
                    branch_mode="whatever")


def test_judge_sets_branch_and_source():
    """judge:分支由 T0 回答判定,并记下判定依据(不许静默)。"""
    b = _bridge(branch="A")                     # 兜底 A,FakeLLM 判 B
    from case01.injector.nodes import NodeSpec

    t0 = NodeSpec(node_id="node-1", date="2026-08-27")
    detected = b._decide_branch_from_t0(_rec(), t0)
    assert detected == "B" and b.branch == "B"
    assert b.branch_source == "judge"
    assert b.judge_info["detected"] == "B" and "cautious" in b.judge_info["reason"]
    # 后续节点换成 B 线时间线(T0 之后:09-03 而不是 A 的 09-02)
    assert [n.date for n in b.nodes][:3] == ["2026-08-27", "2026-08-28", "2026-08-31"]
    assert "2026-09-03" in [n.date for n in b.nodes]


def test_judge_without_t0_answer_falls_back_loudly():
    """T0 没产出回答时不能硬判:退回预设,并把来源标成 preset-fallback。"""
    b = _bridge(branch="A")
    from case01.injector.nodes import NodeSpec

    t0 = NodeSpec(node_id="node-1", date="2026-08-27")
    detected = b._decide_branch_from_t0({"dialogue": []}, t0)
    assert detected == "A" and b.branch == "A"
    assert b.branch_source == "preset-fallback"
    assert "无法判定" in b.judge_info["reason"]


def test_judge_llm_failure_falls_back_loudly():
    class _Boom:
        def chat(self, *a, **kw):
            raise RuntimeError("ollama 挂了")

    b = _bridge(branch="B", llm=_Boom())
    from case01.injector.nodes import NodeSpec

    b._decide_branch_from_t0(_rec(), NodeSpec(node_id="node-1", date="2026-08-27"))
    assert b.branch == "B" and b.branch_source == "preset-fallback"
    assert "judge 失败" in b.judge_info["reason"]


def test_run_record_carries_branch_source_and_judge_info():
    b = _bridge(branch="A")
    from case01.injector.nodes import NodeSpec

    b._decide_branch_from_t0(_rec(), NodeSpec(node_id="node-1", date="2026-08-27"))
    rec = b.run_record()
    assert rec["branch"] == "B" and rec["branch_mode"] == "judge"
    assert rec["branch_source"] == "judge" and rec["judge_info"]["detected"] == "B"


def test_mapping_uses_judged_branch_and_stamps_source():
    """映射出的记录必须用**判定后**的分支,并把 source=judge 写进 branch_action。"""
    raw = {"schema_version": "injector-0.1", "run_id": "r-judge", "mode": "mavis",
           "branch": "B", "branch_source": "judge",
           "judge_info": {"detected": "B", "reason": "cautious", "judge": "llm"},
           "roles": list(ROLES), "scenario_dir": "",
           "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1,
                      "released_events": [], "events": [],
                      "dialogue": [{"x": [["Investment AI",
                                          "I would not recommend buying now."]]}],
                      "world_state": {"date": "2026-08-27", "branch": "B"}}],
           "world_audit": [], "summary": {"node_count": 1}, "c_plan": {}}
    rec = run_pipeline(branch="A", raw_record=raw, dry_run=True, branch_source="judge")
    assert rec["branch"] == "B"
    assert rec["branch_action"]["source"] == "judge"
    assert rec["branch_action"]["judge_info"]["detected"] == "B"
    assert rec["consistency"]["verdict"] == "consistent", "judge 模式下应当自洽"
