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
    # 角色配置来自场景:坐标、角色指令(长回答要求)生效。
    # 坐标**从场景文件读**,不写死 —— 换场景/改碰撞会挪坐标(2026-09-19 就有过一次),
    # 写死的话测试会变成"考场景数据"而不是"考装配是否正确"。
    import io
    import json
    import os

    from case01.injector.pipeline import DEFAULT_SCENARIO as _SCENARIO

    def scenario_coord(role):
        p = os.path.join(_SCENARIO, "agents", role, "agent.json")
        with io.open(p, encoding="utf-8") as f:
            return list(json.load(f)["coord"])

    advisor = bridge.game.get_agent(DEFAULT_ROLES[0])
    assert list(advisor.coord) == scenario_coord(DEFAULT_ROLES[0])
    assert str(advisor.role_directive).startswith("When an investor")
    ethan = bridge.game.get_agent(DEFAULT_ROLES[1])
    assert list(ethan.coord) == scenario_coord(DEFAULT_ROLES[1])
    assert "ordinary retail investor" in str(ethan.role_directive)


def test_build_mavis_uses_relative_assets_root(monkeypatch, tmp_path):
    """装配用的 assets_root 必须是相对根,否则 POSIX 上路径会翻倍。

    mavis 的 load_config 把 assets_root 交给 _resolve_assets_root,后者
    `os.path.join(*assets_root.split("/"))` 会把绝对 POSIX 路径的前导斜杠吃掉、
    变成相对路径;Game.load_static 再把结果拼到 static_root(= 场景目录)下,
    于是 maze.json 变成"场景目录 + 场景目录/maze.json"。Windows 上绝对路径不含
    正斜杠、原样通过,所以这个错在开发机上永远看不见——2026-09-18 合并进 main 后
    首次 CI 红的根因就是它。

    断言取"精确值"而不是 os.path.isabs:被吃掉斜杠的路径在 POSIX 上恰好不是绝对
    路径,用它当断言会假通过。
    """
    _install_stub_provider(monkeypatch)
    bridge, _nodes = _bridge(monkeypatch=monkeypatch, tmp_path=tmp_path)
    bridge._build_mavis()

    assert bridge.config["maze"]["path"] == "maze.json"
    for role in DEFAULT_ROLES:
        assert bridge.config["agents"][role]["config_path"] == os.path.join(
            "agents", role, "agent.json"
        )

    # 不只是字符串对:拼到 static_root 下必须真的能打开,即确实落在场景目录内
    assert os.path.isfile(
        os.path.join(bridge.game.static_root, bridge.config["maze"]["path"])
    )
    for role in DEFAULT_ROLES:
        assert os.path.isfile(os.path.join(
            bridge.game.static_root, bridge.config["agents"][role]["config_path"]
        ))


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

def test_required_interaction_pins_runtime_state(monkeypatch, tmp_path):
    """钉定必须作用在运行时状态(agent.path/coord),否则 mavis 的前置条件仍会判否。"""
    _install_stub_provider(monkeypatch)
    bridge, nodes = _bridge(monkeypatch=monkeypatch, tmp_path=tmp_path)
    bridge._build_mavis()

    a = bridge.game.get_agent(DEFAULT_ROLES[0])
    b = bridge.game.get_agent(DEFAULT_ROLES[1])
    a.path = [[1, 1], [2, 2]]      # 模拟"正在移动"
    b.path = [[3, 3]]

    bridge.activate(nodes[0])
    bridge._apply_world(nodes[0])   # require_interaction=True

    assert a.path == [] and b.path == []
    assert list(a.coord) == list(b.coord)
    # 日程补建在真实模型下生效;stub 模式下只要不抛异常即可
    assert isinstance(a.schedule.daily_schedule, list)
    assert isinstance(b.schedule.daily_schedule, list)

def test_simulated_clock_is_node_based_not_wall_clock(monkeypatch, tmp_path):
    """模拟时钟必须按节点日期起算,不能取墙钟(否则 23 点后 mavis 拒绝对话)。"""
    _install_stub_provider(monkeypatch)
    bridge, nodes = _bridge(monkeypatch=monkeypatch, tmp_path=tmp_path)
    bridge._build_mavis()
    stamp = bridge.game._timer.get_date("%Y-%m-%d %H:%M")
    assert stamp == nodes[0].date + " 09:30"
    # 节点间按 stride 推进到下一节点同一时刻
    stride = bridge._stride_to_next(0)
    bridge.game._timer.forward(stride)
    assert bridge.game._timer.get_date("%Y-%m-%d %H:%M") == nodes[1].date + " 09:30"
