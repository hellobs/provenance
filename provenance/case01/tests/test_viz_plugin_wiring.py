# -*- coding: utf-8 -*-
"""case01 侧可视化接线:插件面路径 + 回退路径 + 多消费者对话。

覆盖:
1. 插件面存在:bridge 挂 VizForwarder 适配器、不再覆盖全局 chat_callback,
   总线事件能经适配器到达 fanout,close() 退订干净。
2. 插件面不存在(monkeypatch 特性探测为 False,等价模拟 import 失败):
   bridge 回退到旧写法(on_story 回调 + 全局 chat_callback),close() 清回。
3. 对话多消费者:subscribe_chat_line 两个订阅者与老写法 chat_callback
   都收到同一句,不再互相顶掉;单订阅者抛错被隔离。
"""
import pytest

from case01.injector.bridge import DEFAULT_ROLES, MavisBridge, _plugin_surface_available
from case01.injector.nodes import default_nodes
from mavis_vizkit import Fanout, Visualizer

SCENARIO = str(
    pytest.importorskip("pathlib").Path(__file__).resolve().parent.parent
    / "injector" / "scenario"
)


class _Recorder(Visualizer):
    name = "recorder"

    def __init__(self):
        self.events = []

    def on_event(self, ev):
        self.events.append(ev)

    def close(self):
        self.events.append({"type": "close"})


def _install_stub_provider(monkeypatch):
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


@pytest.fixture
def env(monkeypatch, tmp_path):
    """隔离 agent_core 的全局 chat 状态 + 装配 stub provider。"""
    import mavisframework.core.agent_core as ac

    # 都交给 monkeypatch 管:结束后自动还原,不手工保存/恢复,避免污染模块级状态。
    # (订阅者集合在测试里会被原地 add/remove,monkeypatch 按引用还原,干净。)
    monkeypatch.setattr(ac, "chat_callback", None)
    monkeypatch.setattr(ac, "_chat_subscribers", set())
    _install_stub_provider(monkeypatch)
    monkeypatch.setenv("MAVIS_CHECKPOINTS_ROOT", str(tmp_path / "checkpoints"))
    monkeypatch.delenv("CASE01_ETHAN_BASE_URL", raising=False)
    monkeypatch.delenv("CASE01_ETHAN_MODEL", raising=False)


def _bridge_with_fanout(recorders, monkeypatch, tmp_path):
    nodes = default_nodes("A", roles=list(DEFAULT_ROLES))
    bridge = MavisBridge(
        nodes=nodes, roles=DEFAULT_ROLES, scenario_dir=SCENARIO,
        run_id="test-viz", dry_run=False, visualizers=recorders,
    )
    return bridge, nodes


def test_plugin_mode_mounts_adapter_and_skips_chat_override(env, monkeypatch, tmp_path):
    assert _plugin_surface_available() is True
    rec = _Recorder()
    bridge, _nodes = _bridge_with_fanout([rec], monkeypatch, tmp_path)
    bridge._build_mavis()

    # 适配器已挂进 Simulator 插件管理;不再覆盖全局 chat_callback
    assert bridge._adapter is not None
    assert bridge.simulator._pmgr is not None
    assert bridge.simulator._pmgr.plugins == [bridge._adapter]
    import mavisframework.core.agent_core as ac

    assert ac.chat_callback is None
    # init 事件由 fanout 直接发出
    assert rec.events[0]["type"] == "init"
    bridge.close()


def test_plugin_mode_bus_reaches_fanout(env, monkeypatch, tmp_path):
    rec = _Recorder()
    bridge, _nodes = _bridge_with_fanout([rec], monkeypatch, tmp_path)
    bridge._build_mavis()

    # 直接经插件总线发三类事件 → 适配器 → fanout
    bridge.simulator._pmgr.emit({
        "type": "agent", "name": "Investment AI",
        "state": {"coord": [1, 1], "action": {"event": {"describe": "分析数据"}},
                  "location": "资料室", "currently": "工作"},
        "time": "20260827-09:30",
    })
    bridge.simulator._pmgr.emit({
        "type": "story", "id": "e1", "event_type": "market",
        "content": "波动", "targets": ["all"], "time": "09:30",
    })
    bridge.simulator.emit_chat_line("Investment AI", "你好")

    types = [e["type"] for e in rec.events]
    assert types.count("agent") == 1
    assert types.count("story") == 1
    assert types.count("chat_line") == 1
    agent_ev = next(e for e in rec.events if e["type"] == "agent")
    assert agent_ev["action"] == "分析数据"      # dict action 被收敛成文本
    chat_ev = next(e for e in rec.events if e["type"] == "chat_line")
    assert (chat_ev["speaker"], chat_ev["text"]) == ("Investment AI", "你好")
    bridge.close()


def test_plugin_mode_teardown_unsubscribes_chat(env, monkeypatch, tmp_path):
    import mavisframework.core.agent_core as ac

    rec = _Recorder()
    bridge, _nodes = _bridge_with_fanout([rec], monkeypatch, tmp_path)
    bridge._build_mavis()

    # 先触发真实运行里由 simulate 触发的那次自动订阅(否则 close 前根本没有订阅者,
    # 无法区分"订阅过又退订"与"从未订阅")。
    bridge.simulator._ensure_plugins(None, {})
    ac._emit_chat_line("Investment AI", "退订前")
    assert any(e.get("type") == "chat_line" and e.get("text") == "退订前"
               for e in rec.events)

    bridge.close()          # plugin_teardown 退订对话订阅并 teardown 适配器
    assert rec.events[-1]["type"] == "close"
    # 退订后的对话不再到达 fanout,说明退订干净("退订前"那条属退订前的历史记录)
    ac._emit_chat_line("X", "再发一句")
    assert not any(e.get("type") == "chat_line" and e.get("text") == "再发一句"
                   for e in rec.events)


def test_real_chain_agent_core_emit_reaches_fanout(env, monkeypatch, tmp_path):
    """真实链路:agent_core._emit_chat_line → 插件总线 → 适配器 → fanout。

    走 mavis 的单一分发点 _emit_chat_line(而非 simulator.emit_chat_line),
    覆盖"由 Simulator.simulate 触发的自动订阅"这条真实路径。
    """
    import mavisframework.core.agent_core as ac

    rec = _Recorder()
    bridge, _nodes = _bridge_with_fanout([rec], monkeypatch, tmp_path)
    bridge._build_mavis()

    # 真实运行里由 simulate 触发的那次自动订阅
    bridge.simulator._ensure_plugins(None, {})
    ac._emit_chat_line("Investment AI", "真实链路")
    got = [e for e in rec.events if e.get("type") == "chat_line"]
    assert any((e.get("speaker"), e.get("text")) == ("Investment AI", "真实链路")
               for e in got)

    # 收尾:退订 + teardown,退订后再发不再到达 fanout
    bridge.close()
    ac._emit_chat_line("X", "收尾后再发")
    assert not any(e.get("type") == "chat_line" and e.get("text") == "收尾后再发"
                   for e in rec.events)


def test_fallback_path_uses_chat_callback_and_clears(env, monkeypatch, tmp_path):
    rec = _Recorder()
    monkeypatch.setattr("case01.injector.bridge._plugin_surface_available",
                        lambda: False)
    bridge, _nodes = _bridge_with_fanout([rec], monkeypatch, tmp_path)
    import mavisframework.core.agent_core as ac

    assert ac.chat_callback is None
    bridge._build_mavis()

    # 回退路径:挂全局 chat_callback、接 on_story,不建插件管理
    assert bridge._adapter is None
    assert bridge.simulator._pmgr is None
    # 按方法相等 == 比较:每次属性访问都会新建绑定期程对象,`is` 恒假
    assert ac.chat_callback == bridge._viz_on_chat_line
    assert bridge.simulator.on_story == bridge._viz_on_story
    ac.chat_callback("Investment AI", "回退对话")
    assert any(e.get("type") == "chat_line" for e in rec.events)

    bridge.close()          # 回退收尾:把全局钩子清回 None
    assert ac.chat_callback is None


def test_two_chat_consumers_both_receive_no_clobber(env, monkeypatch, tmp_path):
    """同进程两个消费者都收到同一句对话(插件面下不再互相顶掉)。"""
    import mavisframework.core.agent_core as ac

    got_old, got_a, got_b = [], [], []

    def _old(s, t):
        got_old.append((s, t))

    def _a(s, t):
        got_a.append((s, t))

    def _b(s, t):
        got_b.append((s, t))

    ac.chat_callback = _old          # 老写法仍有效
    ac.subscribe_chat_line(_a)
    ac.subscribe_chat_line(_b)
    try:
        ac._emit_chat_line("Investment AI", "同一句")
        assert got_old == [("Investment AI", "同一句")]
        assert got_a == [("Investment AI", "同一句")]
        assert got_b == [("Investment AI", "同一句")]
    finally:
        ac.unsubscribe_chat_line(_a)
        ac.unsubscribe_chat_line(_b)


def test_single_chat_subscriber_error_isolated(env, monkeypatch, tmp_path):
    import mavisframework.core.agent_core as ac

    good, bad_bucket = [], []

    def _good(s, t):
        good.append((s, t))

    def _bad(s, t):
        bad_bucket.append(1)
        raise RuntimeError("boom")

    ac.subscribe_chat_line(_bad)
    ac.subscribe_chat_line(_good)
    try:
        ac._emit_chat_line("S", "T")   # _bad 抛错被隔离,_good 仍收到
        assert good == [("S", "T")]
    finally:
        ac.unsubscribe_chat_line(_bad)
        ac.unsubscribe_chat_line(_good)


def test_adapter_forwards_mapping_with_fake_bridge():
    """纯映射:适配器把三类总线事件转发给桥接侧对应发射方法。"""
    from case01.injector.viz_plugin import VizForwarder  # 惰性 import,避免模块收集时加载 mavis

    sent = {"agent": 0, "story": 0, "chat": 0}

    class _FakeBridge:
        def _emit_agent(self, name, state, time):
            sent["agent"] += 1

        def _viz_on_story(self, ev):
            sent["story"] += 1

        def _viz_on_chat_line(self, s, t):
            sent["chat"] += 1

    viz = VizForwarder(_FakeBridge())
    viz.on_event({"type": "agent", "name": "A", "state": {}, "time": "t"})
    viz.on_event({"type": "story", "id": "e"})
    viz.on_event({"type": "chat_line", "speaker": "A", "text": "hi"})
    viz.on_event({"type": "time", "time": "t"})   # time 不转发
    assert sent == {"agent": 1, "story": 1, "chat": 1}