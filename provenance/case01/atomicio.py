# -*- coding: utf-8 -*-
"""case01 侧的原子落盘入口 —— **实现只有一份**,在 `case_engine/atomicio.py`。

这里只是转发(单一来源):两个包各自实现一份"写临时文件再换名"迟早会漂移,
一份改了另一份照旧。case01 内部统一 `from ..atomicio import write_json_atomic`,
不直接依赖 case_engine 的模块路径,将来这层挪窝时改这一个文件即可。

不在这里 import 任何业务/可选依赖,保持零成本导入。
"""
from case_engine.atomicio import (  # noqa: F401 - 转发面,故意原样暴露
    atomic_write_text, write_json_atomic, write_text_atomic,
)

__all__ = ["atomic_write_text", "write_json_atomic", "write_text_atomic"]
