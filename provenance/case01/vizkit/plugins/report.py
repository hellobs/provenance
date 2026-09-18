# -*- coding: utf-8 -*-
"""审查页插件:把整份 run 记录渲染成静态 HTML(复用 case01.viz,离线插件)。

它是"可插拔"的示范之一:同一个 run 记录,既可以被小镇风格前端回放,
也可以被这个插件渲染成审查页,两者互不依赖。
"""
import os

from .. import Visualizer, register


class ReportVisualizer(Visualizer):
    name = "report"

    def __init__(self, out_dir: str = "", index: bool = True):
        self.out_dir = out_dir
        self.index = bool(index)
        self.records = []
        self.written = []

    def on_record(self, record: dict) -> None:
        from ...viz import index_html, page_html

        self.records.append(record)
        if not self.out_dir:
            return
        os.makedirs(self.out_dir, exist_ok=True)
        rid = str(record.get("run_id", "run"))
        path = os.path.join(self.out_dir, rid + ".html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(page_html(record))
        self.written.append(path)

    def write_index(self) -> str:
        from ...viz import index_html

        if not (self.out_dir and self.records):
            return ""
        path = os.path.join(self.out_dir, "index.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(index_html(self.records))
        self.written.append(path)
        return path

    def close(self) -> None:
        if self.index:
            self.write_index()


register("report", ReportVisualizer)
