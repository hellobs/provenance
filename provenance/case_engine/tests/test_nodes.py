# -*- coding: utf-8 -*-
"""case_engine/nodes.py 单元测试。"""
from case_engine.nodes import nodes_from_timeline, NodeSpec


def _tl():
    return {
        "2026-01-02": [{"kind": "price", "summary": "close", "price_usd": 45.8}],
        "2026-01-05": [{"kind": "disclosure", "summary": "官宣", "importance": 8}],
        "2026-01-03": [{"kind": "media", "summary": "报道"}],
    }


def test_nodes_sorted_by_date():
    nodes = nodes_from_timeline(_tl())
    assert [n.date for n in nodes] == ["2026-01-02", "2026-01-03", "2026-01-05"]


def test_importance_mapping_default():
    nodes = nodes_from_timeline(_tl())
    by_date = {n.date: n for n in nodes}
    media = by_date["2026-01-03"].events[0]
    assert media["importance"] == 7            # 来自 _IMPORTANCE 默认
    disc = by_date["2026-01-05"].events[0]
    assert disc["importance"] == 8             # 显式覆盖


def test_price_writes_world():
    nodes = nodes_from_timeline(_tl())
    by_date = {n.date: n for n in nodes}
    assert by_date["2026-01-02"].world["price_usd"] == 45.8


def test_empty_summary_dropped():
    tl = {"2026-01-02": [{"kind": "media", "summary": "   "}]}
    nodes = nodes_from_timeline(tl)
    assert nodes[0].events == []


def test_final_node_appended():
    nodes = nodes_from_timeline(_tl(), final_date="2026-02-01")
    assert nodes[-1].date == "2026-02-01"
    assert nodes[-1].node_id == "node-final"
    assert nodes[-1].events == []


def test_no_final_when_last_is_final():
    nodes = nodes_from_timeline(_tl(), final_date="2026-01-05")
    assert nodes[-1].date == "2026-01-05"    # 已有该日,不补空节点


def test_first_last_require_interaction():
    nodes = nodes_from_timeline(_tl())
    assert nodes[0].require_interaction and nodes[-1].require_interaction


def test_interaction_templates_injected():
    tl = {"2026-01-02": [{"kind": "media", "summary": "s"}]}
    nodes = nodes_from_timeline(
        tl, roles=["advisor", "asker"],
        final_date="2026-02-01",
        interaction_templates={
            "first": {"from": "asker", "to": "advisor", "focus": "开场咨询"},
            "final": {"from": "asker", "to": "advisor", "focus": "最终反馈"},
        })
    assert nodes[0].interactions[0]["focus"] == "开场咨询"
    assert nodes[-1].interactions[0]["focus"] == "最终反馈"


def test_no_templates_no_auto_interaction():
    nodes = nodes_from_timeline(_tl(), roles=["a", "b"])
    assert all(n.interactions == [] for n in nodes)


def test_roles_as_targets():
    nodes = nodes_from_timeline(_tl(), roles=["r1", "r2"])
    assert nodes[0].events[0]["targets"] == ["r1", "r2"]


def test_no_roles_targets_all():
    nodes = nodes_from_timeline(_tl())
    assert nodes[0].events[0]["targets"] == ["all"]


def test_nodespec_roundtrip():
    n = NodeSpec(node_id="n1", date="d", events=[{"a": 1}],
                 interactions=[{"i": 1}], context={"k": {"v": 1}},
                 require_interaction=True, world={"w": 2})
    d = n.to_dict()
    assert d["context"] == {"k": {"v": 1}}
    assert d["require_interaction"] is True
    assert d["world"] == {"w": 2}

def test_event_translation_multiset_complete():
    """schema 翻译完整性(2026-09-26 深度体检):时间线事件的 (kind, summary)
    多集必须与节点事件的 (event_type, content) 多集完全一致 —— 1:1 翻译,
    零丢失零重复。字段改名/丢事件在此第一时间暴露。
    """
    from collections import Counter

    tl = {
        "2026-01-01": [
            {"kind": "disclosure", "summary": "公告甲。"},
            {"kind": "price", "summary": "收盘 10"},
        ],
        "2026-01-02": [
            {"kind": "media", "summary": "媒体报道乙。"},
            {"kind": "disclosure", "summary": "公告甲。"},   # 重复内容合法,多集应含两份
        ],
    }
    nodes = nodes_from_timeline(tl, key_nodes="first_last", final_date="2026-01-03",
                                append_final=True)
    src = Counter((e["kind"], e["summary"]) for evs in tl.values() for e in evs)
    node = Counter((e["event_type"], e["content"]) for n in nodes for e in n.events)
    assert src == node, "事件翻译多集不一致: src-node={} node-src={}".format(
        src - node, node - src)
