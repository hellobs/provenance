# -*- coding: utf-8 -*-
"""过渡 shim(阶段 3 增量 2):实现已迁至 mavis_case01_injector.llm;下版删除本文件。

以 sys.modules 自替换实现全等透传:monkeypatch/reload 等模块级操作的语义
与原模块完全一致(操作的就是实现模块本体)。
"""
import sys as _sys

import mavis_case01_injector.llm as _impl

_sys.modules[__name__] = _impl
