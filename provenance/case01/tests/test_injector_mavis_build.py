# -*- coding: utf-8 -*-
"""injector 真实路径（mavis 装配）的离线测试。

用 stub LLM provider 替换 mavis 的 provider 工厂,不联网、不起 Ollama;
只验证装配、条件注册、事件注入与回调接线（跑完整 think 属于阶段 2 收尾）。
"""
import os

import pytest

from case01.injector.bridge import DEFAULT_ROLES, MavisBridge
from case01.injector.nodes import default_nodes

SCENARIO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "injector", "scenario",
)


def _install_stub_provider(monkeypatch):
    """把 mavis 的 LLM provider 工厂替换成 stub（Agent 内部是懒 import 该工厂）。"""
    mavis_llm = pytest.importorskip("mavisframework.runtime.llm")
    provider_cls = getattr(mavis_llm, "LLMProvider")

    class _StubProvider(provider_cls):
        def __init__(self, config=None):
            self.config = config or {}

        def completion(self, *args, **kwargs):
            return None

        def is_available(self):
            return True

        def get_summary(self):
            return {"provider": "stub"}

    instance = _StubProvider()
    monkeypatch.setattr(mavis_llm, "create_llm_provider", lambda config=None: instance)
    return instance


def _bridge(dry_run=False, monkeypatch=None, tmp_path=None):
    monkeypatch.setenv("MAVIS_CHECKPOINTS_ROOT", str(tmp_path / "checkpoints"))
    monkeypatch.delenv("CASE01_ETHAN_BASE_URL", raising=False)
    monkeypatch.delenv("CASE01_ETHAN_MODEL", raising=False)
    nodes = default_nodes("A", roles=list(DEFAULT_ROLES))
    bridge = MavisBridge(
        nodes=nodes, roles=DEFAULT_ROLES, scenario_dir=SCENARIO,
        run_id="test-build", dry_run=dry_run,
    )
    return bridge, nodes


def test_build_mavis_wires_hooks_and_agents(monkeypatch, tmp_path):
    _install_stub_provider(monkeypatch)
    bridge, nodes = _bridge(monkeypatch=monkeypatch, tmp_path=tmp_path)
    bridge._build_mavis()

    # 两个角色都建起来了,且不挂 governance
    assert set(bridge.game.agents) == set(DEFAULT_ROLES)
    assert bridge.game.governance is None
    # 两个通用入口被接上
    assert bridge.simulator.external_state == bridge.external_state
    assert bridge.simulator.interaction_request == bridge.interaction_request
    # provider 已初始化（mavis 在 Agent.reset() 里惰性创建,桥接显式调用了 reset_game）
    assert bridge.game.get_agent(DEFAULT_ROLES[0]).llm_available() is True
    assert bridge.game.get_agent(DEFAULT_ROLES[1]).llm_available() is True
    # case01_node 条件已注册
    from mavisframework.runtime.simulator import Simulator
    assert "case01_node" in Simulator.CONDITION_CHECKERS
    # 角色配置来自场景:坐标、角色指令(长回答要求)生效
    advisor = bridge.game.get_agent(DEFAULT_ROLES[0])
    assert list(advisor.coord) == [10, 6]
    assert str(advisor.role_directive).startswith("When an investor")
    ethan = bridge.game.get_agent(DEFAULT_ROLES[1])
    assert list(ethan.coord) == [9, 7]
    assert "ordinary retail investor" in str(ethan.role_directive)


def test_apply_world_sets_node_and_story_condition(monkeypatch, tmp_path):
    _install_stub_provider(monkeypatch)
    bridge, nodes = _bridge(monkeypatch=monkeypatch, tmp_path=tmp_path)
    bridge._build_mavis()
    bridge.activate(nodes[0])
    bridge._apply_world(nodes[0])

    assert bridge._node_state["id"] == nodes[0].node_id
    assert bridge.simulator.story, "本节点应有 story 事件"
    ev = bridge.simulator.story[0]
    assert ev["condition"] == {"type": "case01_node", "node_id": nodes[0].node_id}
    # 当前节点命中,其它节点不命中
    assert bridge.simulator._trigger_fired(ev, bridge.game) is True
    other = dict(ev)
    other["condition"] = {"type": "case01_node", "node_id": "node-999"}
    assert bridge.simulator._trigger_fired(other, bridge.game) is False


def test_external_state_callback_returns_node_context(monkeypatch, tmp_path):
    _install_stub_provider(monkeypatch)
    bridge, nodes = _bridge(monkeypatch=monkeypatch, tmp_path=tmp_path)
    bridge._build_mavis()
    nodes[0].context = {"Ethan Lin": {"stress": 0.6}}
    bridge.activate(nodes[0])
    state = bridge.simulator.external_state("Ethan Lin", 1, "20260827-09:30", bridge.game)
    assert state["stress"] == 0.6
    # 节点自带的关键交互,其主题会额外写入发起方状态
    assert "current task" in state


def test_ethan_external_provider_from_env(monkeypatch, tmp_path):
    _install_stub_provider(monkeypatch)
    bridge, _nodes = _bridge(monkeypatch=monkeypatch, tmp_path=tmp_path)
    monkeypatch.setenv("CASE01_ETHAN_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("CASE01_ETHAN_MODEL", "mini-1")
    monkeypatch.setenv("CASE01_ETHAN_API_KEY", "sk-test")
    bridge._build_mavis()
    ethan_cfg = bridge.config["agents"][DEFAULT_ROLES[1]]
    assert ethan_cfg["think"]["llm"] == {
        "provider": "openai", "model": "mini-1",
        "base_url": "https://api.example.com/v1", "api_key": "sk-test",
    }


def test_stride_between_nodes(monkeypatch, tmp_path):
    _install_stub_provider(monkeypatch)
    bridge, nodes = _bridge(monkeypatch=monkeypatch, tmp_path=tmp_path)
    # 08-27 -> 08-28 = 1 天 = 1440 分钟
    assert bridge._stride_to_next(0) == 1440
    # 最后一个节点不再推进
    assert bridge._stride_to_next(len(nodes) - 1) == 0

def test_dialogue_helpers_with_fake_game(monkeypatch, tmp_path):
    _install_stub_provider(monkeypatch)
    bridge, _nodes = _bridge(monkeypatch=monkeypatch, tmp_path=tmp_path)

    class _FakeGame:
        def __init__(self):
            self.conversation = {}

    bridge.game = _FakeGame()
    assert bridge._dialogue_count() == 0
    assert bridge._dialogue_tail(0) == []

    block = {"Ethan Lin -> Investment AI @ floor": [["Ethan Lin", "hi"], ["Investment AI", "hello"]]}
    bridge.game.conversation["20260827-09:40"] = [block]
    assert bridge._dialogue_count() == 1
    assert bridge._dialogue_tail(0) == [block]
    assert bridge._dialogue_tail(1) == []

def test_story_event_stops_matching_after_node_switch(monkeypatch, tmp_path):
    """隔离验证:切到下一节点后,上一节点的 story 事件不再命中。"""
    _install_stub_provider(monkeypatch)
    bridge, nodes = _bridge(monkeypatch=monkeypatch, tmp_path=tmp_path)
    bridge._build_mavis()

    bridge.activate(nodes[0])
    bridge._apply_world(nodes[0])
    ev0 = bridge.simulator.story[0]
    assert bridge.simulator._trigger_fired(ev0, bridge.game) is True

    bridge.activate(nodes[1])
    bridge._apply_world(nodes[1])
    assert bridge._node_state["id"] == nodes[1].node_id
    assert bridge.simulator._trigger_fired(ev0, bridge.game) is False
    ids = {e["condition"]["node_id"] for e in bridge.simulator.story}
    assert ids == {nodes[1].node_id}

def test_final_node_injects_personal_situation(monkeypatch, tmp_path):
    _install_stub_provider(monkeypatch)
    bridge, nodes = _bridge(monkeypatch=monkeypatch, tmp_path=tmp_path)
    bridge.branch = "B"
    bridge._build_mavis()
    bridge.activate(nodes[-1])
    bridge._apply_world(nodes[-1])
    state = bridge.simulator.external_state("Ethan Lin", len(nodes), "20260915-09:30", bridge.game)
    assert "personal situation" in state
    assert "250,000" in state["personal situation"]

def test_required_interaction_pins_both_agents(monkeypatch, tmp_path):
    """必须交互的节点:两角色被钉到同一格且路径清空(否则强制交互会被移动/异地挡住)。"""
    _install_stub_provider(monkeypatch)
    bridge, nodes = _bridge(monkeypatch=monkeypatch, tmp_path=tmp_path)
    bridge._build_mavis()

    bridge.activate(nodes[0])
    bridge._apply_world(nodes[0])       # T0:require_interaction=True
    coords = {n: bridge.config["agents"][n]["coord"] for n in DEFAULT_ROLES}
    paths = {n: bridge.config["agents"][n]["path"] for n in DEFAULT_ROLES}
    assert coords[DEFAULT_ROLES[0]] == coords[DEFAULT_ROLES[1]]
    assert all(p == [] for p in paths.values())

    # 非交互节点不做钉定
    bridge.activate(nodes[1])
    bridge._apply_world(nodes[1])
    assert nodes[1].require_interaction is False
