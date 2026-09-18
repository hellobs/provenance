# -*- coding: utf-8 -*-
"""隔离验证:未释放事件不进入任何角色的上下文/记忆(执行计划阶段 3 第 2 项)。

分三层取证,互不依赖:
1) 池子层:桥每次只把"当前节点"的事件放进 simulator.story;未释放节点的事件
   根本不在池子里。
2) 判定层:即使把整条时间线都塞进池子,`case01_node` 条件也只放行当前节点,
   且同一事件只会触发一次。
3) 检索层:未释放内容不在记忆里,拿它的原文当检索词也检不到——这一层需要真实
   运行,由 `case01/tools/isolation_probe.py` 实测并落盘。

另外顺带验证另一条注入通道(步级临时状态)同样只暴露当前节点的内容。
"""
import json
import types

from case01.injector.bridge import MavisBridge
from case01.injector.conditions import install_case01_node_condition
from case01.injector.nodes import default_nodes

ROLES = ["Investment AI", "Ethan Lin"]


class _FakeAgent:
    def __init__(self, name):
        self.name = name
        self.injected = []

    def inject_story_event(self, ev):
        self.injected.append(ev.get("content", ""))


class _FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


class _FakeTimer:
    def get_date(self, fmt=None):
        return "0930-09:30"


class _FakeGame:
    def __init__(self, names):
        self.agents = {name: _FakeAgent(name) for name in names}
        self.logger = _FakeLogger()
        self._timer = _FakeTimer()

    def get_agent(self, name):
        return self.agents[name]


def _bridge_with_stub_simulator():
    """建一个 dry_run=False 的桥,但用手写的 simulator 替身(不加载 mavis Game)。"""
    nodes = default_nodes("B", roles=ROLES)
    for node in nodes:          # 去掉交互请求,避免走到需要真实 Game 的钉位逻辑
        node.interactions = []
    bridge = MavisBridge(nodes=nodes, roles=tuple(ROLES), dry_run=False, run_id="iso")
    bridge.simulator = types.SimpleNamespace(story=[])
    bridge.config = {"agents": {name: {} for name in ROLES}}
    return bridge, nodes


def test_pool_only_holds_current_node_events():
    """第 1 层:池子里只有当前节点的事件,别的节点事件不在池子里。"""
    bridge, nodes = _bridge_with_stub_simulator()
    seen_ids = set()
    for node in nodes:
        bridge._apply_world(node)
        pool_ids = {ev["id"] for ev in bridge.simulator.story}
        assert pool_ids == {ev["id"] for ev in node.events}
        # 池子里每条事件的判定条件都指向当前节点
        assert all(ev["condition"]["node_id"] == node.node_id
                   for ev in bridge.simulator.story)
        # 也不可能混进别的节点的内容
        for other in nodes:
            if other is node:
                continue
            assert not pool_ids & {ev["id"] for ev in other.events}
        seen_ids |= pool_ids
    all_ids = {ev["id"] for node in nodes for ev in node.events}
    assert seen_ids == all_ids        # 累加起来才是全量,单次永远是子集


def test_condition_gate_blocks_unreleased_nodes():
    """第 2 层:整条时间线都在池子里时,只有当前节点的事件被注入。"""
    install_case01_node_condition({"id": "node-2"})
    from mavisframework.runtime.simulator import Simulator

    nodes = default_nodes("B", roles=ROLES)
    story = [MavisBridge._as_story_event(ev, node)
             for node in nodes for ev in node.events]
    sim = Simulator(story=story)
    game = _FakeGame(ROLES)

    sim._inject_story(game, {"agents": {name: {} for name in ROLES}})
    injected = set(game.agents["Investment AI"].injected)
    node2 = [ev["content"] for ev in nodes[1].events]
    later = [ev["content"] for node in nodes[2:] for ev in node.events]
    earlier = [ev["content"] for ev in nodes[0].events]

    assert node2 and injected == set(node2)
    assert not injected & set(later)      # 未释放节点:一条都不在
    assert not injected & set(earlier)    # 已过去的节点也不会补发
    assert game.agents["Ethan Lin"].injected == game.agents["Investment AI"].injected


def test_story_event_fires_only_once():
    """同一事件即使条件持续为真,也只注入一次(不重复污染记忆)。"""
    install_case01_node_condition({"id": "node-1"})
    from mavisframework.runtime.simulator import Simulator

    nodes = default_nodes("B", roles=ROLES)
    story = [MavisBridge._as_story_event(ev, nodes[0]) for ev in nodes[0].events]
    sim = Simulator(story=story)
    game = _FakeGame(ROLES)

    config = {"agents": {name: {} for name in ROLES}}
    sim._inject_story(game, config)
    first = list(game.agents["Investment AI"].injected)
    sim._inject_story(game, config)
    assert first and game.agents["Investment AI"].injected == first


def test_external_state_exposes_only_current_node_context():
    """另一条注入通道(步级临时状态)也只暴露当前节点的内容。"""
    bridge, nodes = _bridge_with_stub_simulator()
    nodes[0].context = {ROLES[0]: {"stress": "high"}}
    nodes[2].context = {ROLES[0]: {"secret": "later-node-only"}}

    bridge.activate(nodes[0])
    bridge._apply_world(nodes[0])
    state = bridge.external_state(ROLES[0], 1, "0930-09:30", None)
    assert state == {"stress": "high"}
    assert "later-node-only" not in json.dumps(state, ensure_ascii=False)

    bridge.activate(nodes[2])
    bridge._apply_world(nodes[2])
    state = bridge.external_state(ROLES[0], 3, "0906-09:30", None)
    assert state == {"secret": "later-node-only"}
    assert "stress" not in json.dumps(state, ensure_ascii=False)
