# -*- coding: utf-8 -*-
"""injector 记录 -> case01 run.json 的字段对齐测试（阶段 3 验收项的自动化部分）。"""
import json
import os

import pytest

from case01.injector.record import CASE01_TOP_KEYS, to_case01_record

RUNS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs")


def _sample_bridge_record() -> dict:
    return {
        "schema_version": "injector-0.1",
        "run_id": "map-1",
        "mode": "dry-run",
        "roles": ["Investment AI", "Ethan Lin"],
        "scenario_dir": "scenario",
        "nodes": [{
            "node_id": "node-1",
            "date": "2026-08-27",
            "step": 1,
            "released_events": ["node1-1"],
            "events": [{
                "id": "node1-1", "event_type": "disclosure",
                "content": "HCM confirmed product validation", "date": "2026-08-27",
            }],
            "context": {},
            "interactions": [{"from": "Ethan Lin", "to": "Investment AI", "focus": "is it credible"}],
            "interaction_started": True,
            "retries": 1,
            "world": {"price_usd": 45.8},
            "dialogue": [{
                "Ethan Lin -> Investment AI @ floor": [
                    ["Ethan Lin", "Is it credible?"],
                    ["Investment AI", "No official order yet."],
                ]
            }],
        }],
        "summary": {"node_count": 1, "interaction_started": 1, "retries": 1},
    }


def test_mapped_record_has_all_case01_top_keys():
    mapped = to_case01_record(_sample_bridge_record(), branch="A")
    assert set(CASE01_TOP_KEYS).issubset(set(mapped))
    assert mapped["compat"]["missing_keys"] == []
    # 原始节点记录作为新增段保留
    assert mapped["injector"]["nodes"][0]["node_id"] == "node-1"


def test_turns_events_and_final_feedback_mapping():
    mapped = to_case01_record(_sample_bridge_record(), branch="A")
    assert mapped["turns"] == [
        {"speaker": "ethan", "date": "2026-08-27", "text": "Is it credible?"},
        {"speaker": "ai", "date": "2026-08-27", "text": "No official order yet."},
    ]
    assert mapped["final_feedback"]["ethan"] == "Is it credible?"
    assert mapped["final_feedback"]["ai"] == "No official order yet."
    assert mapped["events"][0]["kind"] == "disclosure"
    assert mapped["retrievals"][0]["mode"] == "injection"
    actions = [a["action"] for a in mapped["audit"]]
    assert actions == ["release_events", "interaction"]
    assert mapped["audit"][1]["retries"] == 1
    # 没有真值的字段显式留空,并在 gaps 里标注
    assert mapped["state_history"] == []
    assert any("state_history" in g for g in mapped["compat"]["gaps"])


def test_mapping_covers_real_case01_run_json():
    """与仓库里真实的 run.json 交叉校验（demo run 不入库,CI 上自动跳过）。"""
    path = os.path.join(RUNS, "demo-3", "run.json")
    if not os.path.exists(path):
        pytest.skip("demo run 不在本地（gitignored）")
    with open(path, encoding="utf-8") as f:
        real = json.load(f)
    mapped = to_case01_record(_sample_bridge_record(), branch="C")
    missing = [k for k in real.keys() if k not in mapped]
    assert missing == [], "缺少 case01 run.json 的顶层键: {}".format(missing)
