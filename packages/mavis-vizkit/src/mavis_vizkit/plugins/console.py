# -*- coding: utf-8 -*-
"""控制台插件:收集事件并可打印摘要(默认插件,CI/调试用,零依赖)。"""
from .. import Visualizer, register


class ConsoleVisualizer(Visualizer):
    name = "console"

    def __init__(self, verbose: bool = False):
        self.verbose = bool(verbose)
        self.events = []

    def on_event(self, event: dict) -> None:
        self.events.append(dict(event))
        if self.verbose:
            print("[viz:console] {} {}".format(
                event.get("type"), event.get("name") or event.get("speaker")
                or event.get("id") or event.get("time") or ""))

    def on_record(self, record: dict) -> None:
        from ..events import events_from_record

        self.events.extend(events_from_record(record))

    def counts(self) -> dict:
        out = {}
        for ev in self.events:
            out[ev.get("type")] = out.get(ev.get("type"), 0) + 1
        return out


register("console", ConsoleVisualizer)