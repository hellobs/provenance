# -*- coding: utf-8 -*-
"""事实层（复用 case01 World）测试:分支动作、状态快照、与记录的衔接。

纯逻辑,不加载 mavis、不联网。
"""
from case01.injector.nodes import default_nodes
from case01.injector.record import to_case01_record
from case01.injector.worldfacts import Case01Facts


def _run_facts(branch: str):
    facts = Case01Facts(branch, run_id="facts-" + branch)
    nodes = default_nodes(branch, roles=["Investment AI", "Ethan Lin"])
    infos = [facts.apply_node(n) for n in nodes]
    return facts, nodes, infos


def test_branch_a_buys_and_exits():
    facts, nodes, infos = _run_facts("A")
    first = infos[0]["state"]
    assert first["hcm_shares"] is True
    assert first["entry_price_usd"] == 45.20
    assert first["held_fraction"] == 0.95
    assert first["cash_rmb"] < 200000.0

    # 09-07 退出,价格 27.40
    exit_state = [i["state"] for i in infos if i["date"] == "2026-09-07"][0]
    assert exit_state["exited"] is True
    assert exit_state["exit_price_usd"] == 27.40
    assert facts.pnl_pct() is not None and facts.pnl_pct() < 0


def test_branch_b_never_buys():
    _facts, _nodes, infos = _run_facts("B")
    assert all(i["state"]["hcm_shares"] is False for i in infos)
    assert all(i["state"]["cash_rmb"] == 200000.0 for i in infos)


def test_branch_c_has_no_auto_position_but_uses_a_timeline():
    facts, nodes, infos = _run_facts("C")
    assert facts.timeline_key == "A"
    assert all(i["state"]["hcm_shares"] is False for i in infos)


def test_world_audit_records_branch_and_events():
    facts, _nodes, _infos = _run_facts("A")
    actions = [entry.get("action") for entry in facts.audit()]
    assert "set_branch" in actions
    assert "release_event" in actions


def test_record_uses_world_state_when_present():
    facts, nodes, infos = _run_facts("A")
    record = {
        "schema_version": "injector-0.1", "run_id": "r", "mode": "mavis",
        "branch": "A", "roles": ["Investment AI", "Ethan Lin"],
        "nodes": [
            {"node_id": n.node_id, "date": n.date, "world_state": i["state"],
             "events": [dict(e, date=n.date) for e in n.events]}
            for n, i in zip(nodes, infos)
        ],
        "world_audit": facts.audit(),
        "condition_monitor": [],
        "summary": {"node_count": len(nodes)},
    }
    mapped = to_case01_record(record, branch="A")
    assert len(mapped["state_history"]) == len(nodes)
    assert mapped["state_history"][0]["state"]["hcm_shares"] is True
    # world 审计被合并进 audit,且不再报 state_history 缺口
    assert any(a.get("action") == "set_branch" for a in mapped["audit"])
    assert not any(g.startswith("state_history") for g in mapped["compat"]["gaps"])
    assert not any(g.startswith("world_audit") for g in mapped["compat"]["gaps"])


def test_record_without_world_state_reports_gap():
    record = {
        "schema_version": "injector-0.1", "run_id": "r", "mode": "dry-run",
        "nodes": [{"node_id": "node-1", "date": "2026-08-27", "events": [], "world_state": None}],
        "summary": {},
    }
    mapped = to_case01_record(record, branch="C")
    assert mapped["state_history"] == []
    gaps = mapped["compat"]["gaps"]
    assert any(g.startswith("state_history") for g in gaps)
    assert any("Branch C" in g for g in gaps)
