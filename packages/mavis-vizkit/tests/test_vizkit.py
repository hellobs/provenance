# -*- coding: utf-8 -*-
"""可插拔可视化测试:契约归一化、注册表、故障隔离、小镇插件、审查页插件、回放。"""
import json

import pytest

import mavis_vizkit as vizkit
from mavis_vizkit import Fanout, create, names
from mavis_vizkit.events import events_from_record
from mavis_vizkit.plugins.console import ConsoleVisualizer
from mavis_vizkit.plugins.town import TownVisualizer
from mavis_vizkit.replay import replay_record

from conftest import ALIAS, ROLE_A, ROLE_B


def _record() -> dict:
    """同时含节点结构(在线/回放)与映射后字段(审查页插件)。"""
    return {
        "run_id": "viz-1",
        "branch": "A",
        "roles": [ROLE_A, ROLE_B],
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
            "events": [{"id": "e1", "event_type": "disclosure", "content": "confirms validation",
                        "targets": [ROLE_A, ROLE_B]}],
            "agents": {ROLE_A: {"coord": [9, 7], "action": "asking", "location": "Trading Floor"}},
            "dialogue": [{"A -> B @ floor": [[ROLE_A, "Is it credible?"],
                                             [ROLE_B, "Partly."]]}],
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
        assert set(["type", "name", "coord", "path", "action", "location", "currently", "time"]
                   ).issubset(agent_ev)
        chat_ev = next(e for e in evs if e["type"] == "chat_line")
        assert chat_ev["speaker"] == ROLE_A and chat_ev["text"] == "Is it credible?"

    def test_registry_has_builtin_plugins(self):
        assert {"console", "report", "town", "live"}.issubset(set(names()))
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
        assert errors and errors[0][0] == "boom"
        assert good.counts() == {"time": 1}

    def test_unknown_event_type_is_rejected(self):
        errors = []
        fanout = Fanout([ConsoleVisualizer()], on_error=lambda n, e: errors.append(str(e)))
        fanout.emit({"type": "nonsense"})
        assert errors and "未知事件类型" in errors[0]

    def test_default_on_error_logs_instead_of_silence(self, caplog):
        """默认(不传 on_error)必须留痕:插件炸了不能既没输出也没日志。"""
        import logging

        class Boom(vizkit.Visualizer):
            name = "boom2"

            def on_event(self, event):
                raise RuntimeError("plugin down")

        fanout = Fanout([Boom()])
        with caplog.at_level(logging.WARNING, logger="mavis_vizkit"):
            fanout.emit({"type": "time", "time": "2026-08-27"})
        assert any("boom2" in r.message for r in caplog.records), "插件异常必须记日志"
        assert any(r.levelno >= logging.WARNING for r in caplog.records)


class TestTownPlugin:
    def test_alias_and_fallback_coords(self, tmp_path, scenario_dir):
        out = tmp_path / "town.jsonl"
        town = TownVisualizer(alias=ALIAS, outbox_path=str(out), scenario_dir=scenario_dir,
                              roles=[ROLE_A, ROLE_B])
        town.on_record({
            "run_id": "viz-2", "roles": [ROLE_A, ROLE_B],
            "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1, "events": []}],
        })
        msgs = town.messages()
        agents = [m for m in msgs if m["type"] == "agent"]
        assert agents, "老记录缺坐标时应回退到场景初始坐标"
        for m in agents:
            assert m["coord"], "回退坐标不能为空"
            assert m["texture"] == ALIAS[m["name"]]
        town.close()
        lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == len(msgs)

    def test_init_carries_texture_mapping(self, scenario_dir):
        town = TownVisualizer(alias=ALIAS, scenario_dir=scenario_dir, roles=[ROLE_A, ROLE_B])
        town.on_event({"type": "init", "agents": [ROLE_A, ROLE_B]})
        init = town.messages()[0]
        assert init["textures"][ROLE_A] == ALIAS[ROLE_A]


class TestReportPlugin:
    def test_report_writes_pages(self, tmp_path):
        def page(rec):
            return "<html><svg>page {} {}</svg></html>".format(
                rec.get("run_id"), rec.get("branch"))

        def index(recs):
            return "<html>index {}</html>".format(len(recs))

        rep = create("report", out_dir=str(tmp_path), page_fn=page, index_fn=index)
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
    """节点挂在调用方给定的嵌套段(此处以 "injector" 为例)时,由调用方传 meta_key。"""
    mapped = {"run_id": "m1", "injector": {
        "run_id": "m1", "roles": [ROLE_A, ROLE_B],
        "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1,
                   "events": [{"id": "e1", "event_type": "price", "content": "close"}],
                   "dialogue": [{"a -> b": [[ROLE_A, "hi"], [ROLE_B, "ok"]]}]}],
    }}
    evs = events_from_record(mapped, meta_key="injector")
    types = [e["type"] for e in evs]
    assert types[0] == "init"
    assert "story" in types and "chat_line" in types
    assert sum(1 for e in evs if e["type"] == "chat_line") == 2


def test_replay_falls_back_when_no_coords(scenario_dir):
    """旧记录(无逐节点坐标)走 on_record,由插件回退;town 仍能产出 agent 消息。"""
    from mavis_vizkit.replay import record_needs_fallback

    old = {"run_id": "old-1", "roles": [ROLE_A, ROLE_B],
           "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1, "events": []}]}
    assert record_needs_fallback(old) is True
    town = TownVisualizer(alias=ALIAS, scenario_dir=scenario_dir, roles=old["roles"])
    n = replay_record(old, Fanout([town]))
    assert n == 0                                   # 回退路径
    assert any(m["type"] == "agent" for m in town.messages())

    fresh = {"run_id": "new-1", "roles": [ROLE_A, ROLE_B],
             "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1, "events": [],
                        "agents": {ROLE_A: {"coord": [9, 7]}}}]}
    assert record_needs_fallback(fresh) is False
    con = ConsoleVisualizer()
    assert replay_record(fresh, Fanout([con])) > 0


def test_town_fallback_works_on_mapped_record(scenario_dir):
    """映射后的记录(节点在 meta_key 指定的段)也必须能拿到回退坐标。"""
    mapped = {"run_id": "m2", "injector": {
        "run_id": "m2", "roles": [ROLE_A, ROLE_B],
        "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1, "events": []}],
    }}
    town = TownVisualizer(alias=ALIAS, scenario_dir=scenario_dir, roles=[ROLE_A, ROLE_B],
                          meta_key="injector")
    town.on_record(mapped)
    agents = [m for m in town.messages() if m["type"] == "agent"]
    assert len(agents) == 2
    assert all(m["coord"] for m in agents)


class TestFieldCoercion:
    """前端按字符串处理 action/location(msg.action.slice),必须收敛掉 dict/list。"""

    def test_dict_action_becomes_readable_text(self):
        from mavis_vizkit.events import agent_event

        ev = agent_event("Alice", [10, 6],
                         {"describe": "answering about HCM", "predicate": "正在"},
                         {"a": 1}, None, [], "t", "ai_tool")
        assert isinstance(ev["action"], str) and "answering about" in ev["action"]
        assert isinstance(ev["location"], str)
        assert ev["role_type"] == "ai_tool"

    def test_list_location_is_joined(self):
        from mavis_vizkit.events import as_text

        assert as_text(["the Ville", "Trading Center", "Trading Floor"]) == \
            "the Ville:Trading Center:Trading Floor"
        assert as_text(None) == "" and as_text("x") == "x"

    def test_agent_state_action_dict_uses_inner_event_describe(self):
        """真实结构:{event:{describe,...}, obj_event, start, duration}。"""
        from mavis_vizkit.events import as_text

        raw = {
            "event": {"subject": "投资 AI", "predicate": "分析", "object": "公告",
                      "describe": "Analyzing filings", "address": ["a"], "emoji": ""},
            "obj_event": None,
            "start": "20260828-09:15:00",
            "duration": 20,
        }
        assert as_text(raw) == "Analyzing filings"
        raw["event"]["describe"] = ""
        assert as_text(raw) == "投资 AI 分析 公告"

    def test_snapshot_prefers_role_keyed_agents(self):
        from mavis_vizkit.events import events_from_record

        rec = {"roles": ["A"], "nodes": [{"node_id": "n1", "date": "d", "step": 1,
                                          "agents": {"A": {"coord": [1, 2]}},
                                          "world_state": {"cash_rmb": 1}}]}
        snap = [e for e in events_from_record(rec) if e["type"] == "snapshot"][-1]
        assert "A" in snap["agents"] and "world" not in snap["agents"]