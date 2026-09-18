# -*- coding: utf-8 -*-
"""审查页插件:把整份 run 记录渲染成静态 HTML(离线插件)。

HTML 的具体排版由调用方提供(page_fn / index_fn 渲染回调),本包只做
"收集记录 → 逐条写页 → 写总览" 的编排,不绑定任何业务渲染器。
"""
import os

from .. import Visualizer, register


class ReportVisualizer(Visualizer):
    name = "report"

    def __init__(self, out_dir: str = "", index: bool = True,
                 page_fn=None, index_fn=None):
        """out_dir 写盘目录;page_fn(rec)->html 单页渲染;index_fn(recs)->html 总览。"""
        if not callable(page_fn) or not callable(index_fn):
            raise ValueError(
                "report 插件需要 page_fn(单页渲染回调) 与 index_fn(总览渲染回调),"
                "由调用方提供,不应猜默认渲染器")
        self.page_fn = page_fn
        self.index_fn = index_fn
        self.out_dir = out_dir
        self.index = bool(index)
        self.records = []
        self.written = []

    def on_record(self, record: dict) -> None:
        self.records.append(record)
        if not self.out_dir:
            return
        os.makedirs(self.out_dir, exist_ok=True)
        rid = str(record.get("run_id", "run"))
        path = os.path.join(self.out_dir, rid + ".html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.page_fn(record))
        self.written.append(path)

    def write_index(self) -> str:
        if not (self.out_dir and self.records):
            return ""
        path = os.path.join(self.out_dir, "index.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.index_fn(self.records))
        self.written.append(path)
        return path

    def close(self) -> None:
        if self.index:
            self.write_index()


register("report", ReportVisualizer)