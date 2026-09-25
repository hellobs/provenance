# -*- coding: utf-8 -*-
"""injector 记录 -> case01 run.json 的字段对齐测试（阶段 3 验收项的自动化部分）。"""
import json
import os

import pytest

from case01.injector.record import CASE01_TOP_KEYS, to_case01_record

# 最小化但结构真实的 run 样本(与真实 runs/ 内同 id 记录同构),入库随 CI 可复现
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "records")


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


def test_injected_items_keep_their_source_and_story_events_do_not():
    """信息出处必须进记录,但**不许**因此进 AI 可见的信息环境(2026-09-24 体检)。

    背景:timeline 数据里一直有 `source`("HCM 公告" / "Battery Industry Daily" / "盘面"),
    但节点事件只搬了 kind 与 content → 实测 28 条记录的 358 条注入项 source **全空**。
    这里两头都钉住:①节点事件与记录里的注入项带出处;②喂给 mavis 的 story 事件仍不带
    (带不带是口径,要研究侧定,实现侧不擅自改)。
    """
    from case01.injector.nodes import nodes_from_timeline
    from case01.injector.bridge import MavisBridge

    timeline = {"2026-08-27": [
        {"kind": "disclosure", "summary": "HCM 公告:正开展产品验证。", "source": "HCM 公告"},
        {"kind": "price", "summary": "收盘 $45.80。", "source": "盘面", "price_usd": 45.8},
    ]}
    nodes = nodes_from_timeline(timeline, roles=["Investment AI", "Ethan Lin"],
                                final_date="2026-08-27", append_final=False)
    ev = nodes[0].events[0]
    assert ev["source"] == "HCM 公告"
    assert nodes[0].events[1]["source"] == "盘面"

    mapped = to_case01_record({"nodes": [{"node_id": nodes[0].node_id, "date": nodes[0].date,
                                          "events": nodes[0].events}]}, branch="A")
    injected = mapped["retrievals"][0]["injected"]
    assert [d["source"] for d in injected] == ["HCM 公告", "盘面"]
    # 顶层 events(契约 §2.2 里**专家能看到**的那块)也必须带出处:
    # 2026-09-25 第十二轮体检发现它一直丢 source —— 于是"专家能看的没有出处、
    # 看不到的 injector 里才有",正好反了。
    assert [e["source"] for e in mapped["events"]] == ["HCM 公告", "盘面"]

    story = MavisBridge._as_story_event(ev, nodes[0])
    assert "source" not in story, "AI 可见的 story 事件不该带出处(那是口径,不是补漏)"


def test_branch_action_timeline_matches_frozen_engine():
    """branch_action.timeline 必须与已冻结的旧引擎记录一致:C 走 Timeline A。

    旧引擎实测(demo-2 是 A、demo-3 是 C)里两条记录的 timeline 都是 "A";
    case01.world.timelines.BRANCH_TO_TIMELINE 也写明 {A:A, B:B, C:A}。
    """
    for branch, expected in (("A", "A"), ("B", "B"), ("C", "A")):
        mapped = to_case01_record(_sample_bridge_record(), branch=branch)
        assert mapped["branch"] == branch
        assert mapped["branch_action"]["timeline"] == expected


def test_mapping_covers_real_case01_run_json():
    """映射必须覆盖 case01 run.json 的顶层键(用入库的 fixture 做交叉校验)。

    fixture 由真实 runs/ 目录内同 id 记录递归收缩而来(保留全部键结构),
    因此断言对真实记录亦成立;真实 demo run 不入库,故以 fixture 为准,
    CI 不再静默跳过。
    """
    path = os.path.join(FIXTURES, "260905-demo-case01-old-C", "run.json")
    with open(path, encoding="utf-8") as f:
        real = json.load(f)
    mapped = to_case01_record(_sample_bridge_record(), branch="C")
    missing = [k for k in real.keys() if k not in mapped]
    assert missing == [], "缺少 case01 run.json 的顶层键: {}".format(missing)
