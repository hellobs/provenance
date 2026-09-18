# -*- coding: utf-8 -*-
"""case01 侧薄适配器:把 mavis 通用插件面的事件转发给 case01 的可视化消费。

只做"事件转发",不含任何渲染/业务逻辑;各事件类型对应的 fanout 发射方法
由桥接侧(bridge)提供,本模块不 import mavis-vizkit,也不碰 mavis 内部。

特性探测:本模块在 import 时探测 mavis 插件面(Plugin)是否存在。
- mavis 具备插件面时,VizForwarder 继承 `mavisframework.plugin.Plugin`;
- mavis 还是 main(无插件面)时,本模块可被安全 import(Plugin=None),
  VizForwarder 退化为普通鸭子类型对象,bridge 只有在探测到插件面存在时才会
  实例化它——因此本模块被 import 不产生副作用,也不会让 CI 因缺模块而红。
"""
try:
    from mavisframework.plugin import Plugin

    _PLUGIN_SURFACE = True
except Exception:                       # mavis main:无 plugin 模块
    Plugin = None
    _PLUGIN_SURFACE = False


if Plugin is not None:
    class _Base(Plugin):
        pass
else:
    class _Base:
        name = "case01-viz-forwarder"

        def setup(self, ctx=None):
            pass

        def on_event(self, evt):
            pass

        def teardown(self):
            pass


class VizForwarder(_Base):
    """把插件总线上与可视化相关的事件转发给桥接侧的 fanout 发射方法。

    只转发 agent / story / chat_line 三种事件:它们发到 fanout 不需要 step。
    time / snapshot 不在此转发:快照需要按 step 分组,而插件总线的 time 事件
    不带 step(mavis tutorial-extension §8),桥接侧已有 on_step 回调(config
    自带 step)负责 time/snapshot,这里不再复制一份快照逻辑。
    """

    name = "case01-viz-forwarder"

    def __init__(self, bridge):
        self._bridge = bridge

    def setup(self, ctx=None):
        pass

    def on_event(self, evt):
        t = evt.get("type")
        if t == "agent":
            self._bridge._emit_agent(evt.get("name"), evt.get("state"), evt.get("time"))
        elif t == "story":
            self._bridge._viz_on_story(evt)
        elif t == "chat_line":
            self._bridge._viz_on_chat_line(evt.get("speaker"), evt.get("text"))

    def teardown(self):
        pass