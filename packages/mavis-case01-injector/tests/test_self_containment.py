# -*- coding: utf-8 -*-
"""包自包含守卫(阶段 3 设计稿 §4 步骤 4 的验收,固化为测试)。

mavis_case01_injector 的 src 里,除 `_providers.py`(过渡 seam)外,任何模块
不得 import case01 —— 否则"独立可挂载"名存实亡。CI 与本地都跑这个包测试。
"""
import os
import re

import pytest

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src",
                    "mavis_case01_injector")
_RULE = re.compile(r"^\s*(?:from|import)\s+case01", re.M)


def _py_files():
    for dirpath, dirs, files in os.walk(_SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def test_no_case01_imports_outside_providers():
    bad = []
    for p in _py_files():
        if os.path.basename(p) == "_providers.py":
            continue
        src = open(p, encoding="utf-8", errors="replace").read()
        if _RULE.search(src):
            bad.append(os.path.relpath(p, _SRC))
    assert not bad, "包内出现 case01 import(只准在 _providers.py): {}".format(bad)


def test_providers_is_the_only_seam():
    assert os.path.isfile(os.path.join(_SRC, "_providers.py")),         "_providers.py 不见了:过渡 seam 被删除时,请同步改为调用方注入并更新本测试"


def test_world_subpackage_is_pure():
    """world 子包(设计稿 §1:随迁的纯净层)连 _providers 都不准依赖。"""
    for p in _py_files():
        rel = os.path.relpath(p, _SRC).replace(os.sep, "/")
        if not rel.startswith("world/"):
            continue
        src = open(p, encoding="utf-8", errors="replace").read()
        assert "_providers" not in src,             "world 子包不得依赖 _providers: {}".format(p)
