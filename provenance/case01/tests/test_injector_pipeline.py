# -*- coding: utf-8 -*-
"""完整流水线（dry-run）与 Reflection 挂接的测试（不联网、不加载 mavis 时走 dry-run）。"""
import json
import os

from case01.injector.pipeline import run_pipeline
from case01.injector.record import CASE01_TOP_KEYS


def test_pipeline_from_existing_raw_record(tmp_path):
    raw = {
        "schema_version": "injector-0.1", "run_id": "raw-1", "mode": "mavis",
        "branch": "A", "roles": ["Investment AI", "Ethan Lin"],
        "nodes": [{
            "node_id": "node-1", "date": "2026-08-27", "step": 1,
            "events": [{"id": "e1", "event_type": "disclosure", "content": "x", "date": "2026-08-27"}],
            "world_state": {"date": "2026-08-27", "branch": "A", "hcm_shares": True},
            "dialogue": [{"A -> B": [["Ethan Lin", "q"], ["Investment AI", "a"]]}],
            "interactions": [], "interaction_started": True, "retries": 0,
        }],
        "world_audit": [{"t": "2026-08-27", "action": "set_branch", "branch": "A"}],
        "summary": {"node_count": 1},
    }
    out = tmp_path / "mapped.json"
    record = run_pipeline(raw_record=raw, out_path=str(out))
    assert out.exists()
    assert record["run_id"] == "raw-1"
    assert len(record["state_history"]) == 1
    assert len(record["turns"]) == 2
    assert any(a.get("action") == "set_branch" for a in record["audit"])

def test_pipeline_run_id_override_applies_in_mapping_path(tmp_path):
    """`--run-id` 在"从原始记录映射"这条路径上也要生效。

    2026-09-19 实测踩到:传了 `--run-id live-B-mavis`,run_pipeline 把它算出来了,
    却没传给 to_case01_record,于是输出记录的 run_id 沿用了原始记录的 `live-B`。
    后果是两个面不一致——5002 契约按记录里的 run_id 列(显示 live-B),
    结果面板按目录名列(显示 live-B-mavis),同一条记录出现两个名字。
    """
    raw = {
        "schema_version": "injector-0.1", "run_id": "raw-1", "mode": "mavis",
        "branch": "B", "roles": ["Investment AI", "Ethan Lin"],
        "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1, "events": [],
                   "world_state": {"date": "2026-08-27", "branch": "B"}}],
        "world_audit": [], "summary": {"node_count": 1},
    }
    out = tmp_path / "mapped.json"
    record = run_pipeline(raw_record=raw, run_id="live-B-mavis", out_path=str(out))
    assert record["run_id"] == "live-B-mavis"
    assert json.load(open(out, encoding="utf-8"))["run_id"] == "live-B-mavis"
    # 不传 run_id 时仍沿用原始记录里的 —— 既有行为不变
    assert run_pipeline(raw_record=raw)["run_id"] == "raw-1"


def test_final_node_at_case01_final_date():
    """最终反馈必须落在 01 文档固定的 2026-09-15,而不是最后一条事件日。"""
    from case01.injector.nodes import default_nodes

    nodes = default_nodes("B", roles=["Investment AI", "Ethan Lin"])
    assert nodes[-1].date == "2026-09-15"
    assert nodes[-1].events == []
    assert nodes[-1].require_interaction is True
    assert nodes[-1].interactions[0]["from"] == "Ethan Lin"
    assert "actually happened" in nodes[-1].interactions[0]["focus"]
    # 09-11 那条事件节点仍存在,且不是最终节点
    assert any(n.date == "2026-09-11" for n in nodes[:-1])


def test_legacy_record_events_rebuilt_from_timeline(tmp_path):
    raw = {
        "schema_version": "injector-0.1", "run_id": "legacy-2", "mode": "mavis",
        "nodes": [{"node_id": "node-1", "date": "2026-08-27", "released_events": ["node1-1"]}],
        "summary": {"node_count": 1},
    }
    record = run_pipeline(raw_record=raw, fill_facts=True)
    assert record["events"], "旧记录应按 timeline 补算事件原文"
    assert record["events"][0]["kind"]
