# -*- coding: utf-8 -*-
"""mavis-vizkit:面向任意 mavis 使用方的观察者/可视化插件集。

本包只消费"事件字典"的键名(契约见 README),**运行时绝不 import mavisframework**;
引擎/注入层只负责产生事件,不关心谁来画。任何可视化后端作为插件注册即可接入。

- 统一事件流:init / time / agent / chat_line / story / snapshot(键名固定)。
- 插件可消费"实时事件流"(on_event)或"整份 run 记录"(on_record)。
- 故障隔离:Fanout 把事件推给多个插件,单个插件异常不打断其它插件。
"""
from typing import Callable, Dict, List

EVENT_TYPES = ("init", "time", "agent", "chat_line", "story", "snapshot")


class Visualizer:
    """插件基类:实现 on_event 即可;需要整份记录时实现 on_record。"""

    name = "base"

    def on_event(self, event: dict) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    def on_record(self, record: dict) -> None:
        """可选:直接消费一份 run 记录(离线插件,如静态审查页)。"""

    def close(self) -> None:
        """可选:收尾(刷盘/关连接)。"""


_REGISTRY: Dict[str, Callable[[], Visualizer]] = {}


def register(name: str, factory: Callable[[], Visualizer]) -> None:
    _REGISTRY[name] = factory


def names() -> List[str]:
    return sorted(_REGISTRY)


def create(name: str, **kwargs) -> Visualizer:
    if name not in _REGISTRY:
        raise KeyError("未知可视化插件 '{}';已注册: {}".format(name, names()))
    return _REGISTRY[name](**kwargs)


class Fanout:
    """把事件同时推给多个插件;单个插件异常不打断其它插件。"""

    def __init__(self, visualizers: List[Visualizer], on_error=None):
        self.visualizers = list(visualizers)
        self.on_error = on_error or (lambda name, exc: None)

    def emit(self, event: dict) -> None:
        etype = event.get("type")
        if etype not in EVENT_TYPES:
            self.on_error("fanout", ValueError("未知事件类型: {}".format(etype)))
            return
        for v in self.visualizers:
            try:
                v.on_event(event)
            except Exception as exc:            # 插件故障隔离:不影响引擎与其它插件
                self.on_error(getattr(v, "name", "?"), exc)

    def emit_record(self, record: dict) -> None:
        for v in self.visualizers:
            try:
                v.on_record(record)
            except Exception as exc:
                self.on_error(getattr(v, "name", "?"), exc)

    def close(self) -> None:
        for v in self.visualizers:
            try:
                v.close()
            except Exception as exc:
                self.on_error(getattr(v, "name", "?"), exc)


# 内置插件注册(import 即注册;不引入第三方依赖)
from .plugins import console as _console     # noqa: E402,F401
from .plugins import report as _report       # noqa: E402,F401
from .plugins import town as _town           # noqa: E402,F401
from .plugins import live as _live           # noqa: E402,F401

from .discovery import load_entry_point_plugins  # noqa: E402,F401