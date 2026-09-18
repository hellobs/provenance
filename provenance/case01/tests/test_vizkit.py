# -*- coding: utf-8 -*-
"""可插拔可视化测试:契约归一化、注册表、故障隔离、小镇插件、审查页插件、回放。"""
import json
import os

import pytest

from case01 import vizkit
from case01.vizkit import Fanout, create, names
from case01.vizkit.events import events_from_record
from case01.vizkit.plugins.console import ConsoleVisualizer
from case01.vizkit.plugins.town import ROLE_TEXTURE_ALIAS, TownVisualizer
from case01.vizkit.replay import replay_record

SCENARIO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "injector", "scenario")


def _record() -> dict:
    """同时含 bridge 节点结构(在线/回放)与映射后字段(审查页插件)。"""
    return {
        "run_id": "viz-1",
        "branch": "A",
        "roles": ["Investment AI", "Ethan Lin"],
        "events": [{"date": "2026-08-27", "kind": "price", "summary": "close", "price_usd": 45.8}],
        "turns": [{"speaker": "ethan", "date": "2026-08-27", "text": "Is it credible?"}],
        "state_history": [{"date": "2026-08-27",
                           "state": {"cash_rmb": 200000.0, "hcm_shares": False,
                                     "entry_price_usd": None, "exit_price_usd": None}}],
        "reflection": {"text": "反思示例"},
        "router": {"issues": [{"summary": "s", "field": "f", "risk": "high",
                               "routing_reason": "r"}]},
        "nodes": [{
            "node_id": "node-1", "date": "2026-08-27", "step": 1,
            "events": [{"id": "e1", "event_type": "disclosure", "content": "HCM confirms validation",
                        "targets": ["Investment AI", "Ethan Lin"]}],
            "agents": {"Ethan Lin": {"coord": [9, 7], "action": "asking", "location": "Trading Floor"}},
            "dialogue": [{"Ethan Lin -> Investment AI @ floor": [["Ethan Lin", "Is it credible?"],
                                                                ["Investment AI", "Partly."]]}],
            "world_state": {"date": "2026-08-27", "cash_rmb": 200000.0, "hcm_shares": False},
        }],
    }


class TestContract:
    def test_events_from_record_uses_protocol_keys(self):
        evs = events_from_record(_record())
        types = [e["type"] for e in evs]
        assert types[0] == "init"
        for t in ("time", "story", "agent", "chat_line", "snapshot"):
            assert t in types
        agent_ev = next(e for e in evs if e["type"] == "agent")
        # 协议键(前端/Unity/平台按它解析)
        assert set(["type", "name", "coord", "path", "action", "location", "currently", "time"]
                   ).issubset(agent_ev)
        chat_ev = next(e for e in evs if e["type"] == "chat_line")
        assert chat_ev["speaker"] == "Ethan Lin" and chat_ev["text"] == "Is it credible?"

    def test_registry_has_builtin_plugins(self):
        assert {"console", "report", "town"}.issubset(set(names()))
        assert isinstance(create("console"), ConsoleVisualizer)
        with pytest.raises(KeyError):
            create("no-such-plugin")


class TestFanout:
    def test_plugin_failure_is_isolated(self):
        class Boom(vizkit.Visualizer):
            name = "boom"

            def on_event(self, event):
                raise RuntimeError("plugin down")

        errors = []
        good = ConsoleVisualizer()
        fanout = Fanout([Boom(), good], on_error=lambda n, e: errors.append((n, str(e))))
        fanout.emit({"type": "time", "time": "2026-08-27"})
        assert errors and errors[0][0] == "boom"          # 故障被报告
        assert good.counts() == {"time": 1}               # 其它插件不受影响

    def test_unknown_event_type_is_rejected(self):
        errors = []
        fanout = Fanout([ConsoleVisualizer()], on_error=lambda n, e: errors.append(str(e)))
        fanout.emit({"type": "nonsense"})
        assert errors and "未知事件类型" in errors[0]


class TestTownPlugin:
    def test_alias_and_fallback_coords(self, tmp_path):
        out = tmp_path / "town.jsonl"
        town = TownVisualizer(outbox_path=str(out), scenario_dir=SCENARIO,
                              roles=["Investment AI", "Ethan Lin"])
        town.on_record({
            "run_id": "viz-2", "roles": ["Investment AI", "Ethan Lin"],
            "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1, "events": []}],
        })
        msgs = town.messages()
        agents = [m for m in msgs if m["type"] == "agent"]
        assert agents, "老记录缺坐标时应回退到场景初始坐标"
        for m in agents:
            assert m["coord"], "回退坐标不能为空"
            assert m["texture"] == ROLE_TEXTURE_ALIAS[m["name"]]
        # JSONL 落盘(供 WS 回放服务)
        town.close()
        lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == len(msgs)

    def test_init_carries_texture_mapping(self):
        town = TownVisualizer(scenario_dir=SCENARIO, roles=["Investment AI", "Ethan Lin"])
        town.on_event({"type": "init", "agents": ["Investment AI", "Ethan Lin"]})
        init = town.messages()[0]
        assert init["textures"]["Ethan Lin"] == ROLE_TEXTURE_ALIAS["Ethan Lin"]


class TestReportPlugin:
    def test_report_writes_pages(self, tmp_path):
        rep = create("report", out_dir=str(tmp_path))
        rep.on_record(dict(_record(), run_id="viz-3", branch="A"))
        rep.close()
        assert (tmp_path / "viz-3.html").exists()
        assert (tmp_path / "index.html").exists()
        html = (tmp_path / "viz-3.html").read_text(encoding="utf-8")
        assert "viz-3" in html and "<svg" in html


class TestReplay:
    def test_replay_pushes_all_events(self):
        con = ConsoleVisualizer()
        n = replay_record(_record(), Fanout([con]))
        assert n == len(con.events)
        assert con.counts().get("chat_line") == 2

def test_events_from_mapped_run_layout():
    """映射后的 run.json 把节点放在 injector.nodes 里,回放必须兼容。"""
    mapped = {"run_id": "m1", "injector": {
        "run_id": "m1", "roles": ["Investment AI", "Ethan Lin"],
        "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1,
                   "events": [{"id": "e1", "event_type": "price", "content": "close"}],
                   "dialogue": [{"a -> b": [["Ethan Lin", "hi"], ["Investment AI", "ok"]]}]}],
    }}
    evs = events_from_record(mapped)
    types = [e["type"] for e in evs]
    assert types[0] == "init"
    assert "story" in types and "chat_line" in types
    assert sum(1 for e in evs if e["type"] == "chat_line") == 2

def test_replay_falls_back_when_no_coords():
    """旧记录(无逐节点坐标)走 on_record,由插件回退;town 仍能产出 agent 消息。"""
    from case01.vizkit.replay import record_needs_fallback
    from case01.vizkit.plugins.town import TownVisualizer

    old = {"run_id": "old-1", "roles": ["Investment AI", "Ethan Lin"],
           "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1, "events": []}]}
    assert record_needs_fallback(old) is True
    town = TownVisualizer(scenario_dir=SCENARIO, roles=old["roles"])
    n = replay_record(old, Fanout([town]))
    assert n == 0                                   # 回退路径
    assert any(m["type"] == "agent" for m in town.messages())

    fresh = {"run_id": "new-1", "roles": ["Investment AI", "Ethan Lin"],
             "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1, "events": [],
                        "agents": {"Ethan Lin": {"coord": [9, 7]}}}]}
    assert record_needs_fallback(fresh) is False
    con = ConsoleVisualizer()
    assert replay_record(fresh, Fanout([con])) > 0

def test_town_fallback_works_on_mapped_record():
    """映射后的 run.json(节点在 injector.nodes)也必须能拿到回退坐标。"""
    from case01.vizkit.plugins.town import TownVisualizer

    mapped = {"run_id": "m2", "injector": {
        "run_id": "m2", "roles": ["Investment AI", "Ethan Lin"],
        "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1, "events": []}],
    }}
    town = TownVisualizer(scenario_dir=SCENARIO, roles=["Investment AI", "Ethan Lin"])
    town.on_record(mapped)
    agents = [m for m in town.messages() if m["type"] == "agent"]
    assert len(agents) == 2
    assert all(m["coord"] for m in agents)

class TestFieldCoercion:
    """前端按字符串处理 action/location(msg.action.slice),必须收敛掉 dict/list。"""

    def test_dict_action_becomes_readable_text(self):
        from case01.vizkit.events import agent_event

        ev = agent_event("Investment AI", [10, 6],
                         {"describe": "answering Ethan about HCM", "predicate": "正在"},
                         {"a": 1}, None, [], "t", "ai_tool")
        assert isinstance(ev["action"], str) and "answering Ethan" in ev["action"]
        assert isinstance(ev["location"], str)
        assert ev["role_type"] == "ai_tool"

    def test_list_location_is_joined(self):
        from case01.vizkit.events import as_text

        assert as_text(["the Ville", "Trading Center", "Trading Floor"]) == \
            "the Ville:Trading Center:Trading Floor"
        assert as_text(None) == "" and as_text("x") == "x"

    def test_snapshot_prefers_role_keyed_agents(self):
        from case01.vizkit.events import events_from_record

        rec = {"roles": ["A"], "nodes": [{"node_id": "n1", "date": "d", "step": 1,
                                          "agents": {"A": {"coord": [1, 2]}},
                                          "world_state": {"cash_rmb": 1}}]}
        snap = [e for e in events_from_record(rec) if e["type"] == "snapshot"][-1]
        assert "A" in snap["agents"] and "world" not in snap["agents"]
