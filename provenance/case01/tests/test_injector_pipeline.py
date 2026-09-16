# -*- coding: utf-8 -*-
"""完整流水线（dry-run）与 Reflection 挂接的测试（不联网、不加载 mavis 时走 dry-run）。"""
import json
import os

from case01.injector.pipeline import run_pipeline
from case01.injector.record import CASE01_TOP_KEYS


def test_pipeline_dry_run_writes_mapped_record(tmp_path):
    out = tmp_path / "record.json"
    record = run_pipeline(branch="B", dry_run=True, out_path=str(out))

    assert out.exists()
    saved = json.load(open(out, encoding="utf-8"))
    assert set(CASE01_TOP_KEYS).issubset(set(saved))
    assert saved["branch"] == "B"
    assert saved["start_date"] == "2026-08-27"
    assert saved["final_feedback"]["date"] == saved["end_date"]
    # dry-run 下关键节点视为已发生交互
    assert saved["summary"]["interaction_started"] >= 2
    assert saved["compat"]["reflection_attached"] is False


def test_pipeline_attach_reflection_uses_case01_modules(tmp_path, monkeypatch):
    calls = {}

    def _fake_reflection(llm, rec, max_tokens=3072):
        calls["reflection"] = {"llm": llm, "run_id": rec.get("run_id")}
        return {"material": "材料", "text": "反思文本"}

    def _fake_router(llm, text, material=""):
        calls["router"] = {"llm": llm, "text": text}
        return {"raw": "{}", "issues": [{"summary": "s"}]}

    import case01.reflection as refl
    monkeypatch.setattr(refl, "run_reflection", _fake_reflection)
    monkeypatch.setattr(refl, "run_router", _fake_router)

    record = run_pipeline(branch="B", dry_run=True, reflect=True, llm="LLM")

    assert record["reflection"] == {"material": "材料", "text": "反思文本"}
    assert record["router"]["issues"] == [{"summary": "s"}]
    assert calls["reflection"]["llm"] == "LLM"
    assert calls["router"]["text"] == "反思文本"
    assert record["compat"]["reflection_attached"] is True
    assert not any(g.startswith("reflection/router") for g in record["compat"]["gaps"])

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
    record = run_pipeline(branch="A", raw_record=raw, out_path=str(out))
    assert out.exists()
    assert record["run_id"] == "raw-1"
    assert len(record["state_history"]) == 1
    assert len(record["turns"]) == 2
    assert any(a.get("action") == "set_branch" for a in record["audit"])

def test_pipeline_fill_facts_on_legacy_record(tmp_path):
    raw = {
        "schema_version": "injector-0.1", "run_id": "legacy-1", "mode": "mavis",
        "roles": ["Investment AI", "Ethan Lin"],
        "nodes": [
            {"node_id": "node-1", "date": "2026-08-27", "events": []},
            {"node_id": "node-2", "date": "2026-09-07", "events": []},
        ],
        "summary": {"node_count": 2},
    }
    out = tmp_path / "legacy_mapped.json"
    record = run_pipeline(branch="A", raw_record=raw, fill_facts=True, out_path=str(out))

    assert len(record["state_history"]) == 2
    first = record["state_history"][0]["state"]
    assert first["hcm_shares"] is True and first["entry_price_usd"] == 45.20
    last = record["state_history"][-1]["state"]
    assert last["exited"] is True and last["exit_price_usd"] == 27.40
    assert any(a.get("action") == "set_branch" for a in record["audit"])

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
    record = run_pipeline(branch="A", raw_record=raw, fill_facts=True)
    assert record["events"], "旧记录应按 timeline 补算事件原文"
    assert record["events"][0]["kind"]
