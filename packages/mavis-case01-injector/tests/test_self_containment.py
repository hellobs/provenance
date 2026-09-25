# -*- coding: utf-8 -*-
"""包自包含守卫(阶段 3 设计稿 §4 步骤 4 的验收,固化为测试)。

mavis_case01_injector 的 src 里,除 `_providers.py`(过渡 seam)外,任何模块
不得 import case01 —— 否则"独立可挂载"名存实亡。CI 与本地都跑这个包测试。
"""
import os
import re
import types

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


def test_mavis_provider_degrades_gracefully(monkeypatch):
    """设计稿双路径验证:case01 缺席时(provider 返回 None),安全包装缺失
    必须优雅跳过而非崩溃(_install_safe_mavis_providers 曾带崩建桥)。"""
    pytest.importorskip("mavisframework")
    import sys
    from mavis_case01_injector import bridge as bridge_mod
    from mavis_case01_injector import _providers

    bridge = bridge_mod.MavisBridge.__new__(bridge_mod.MavisBridge)
    bridge.game = types.SimpleNamespace(agents={})

    # 路径 1:真 import 失败(sys.modules 置 None = 导入期报 ImportError)
    monkeypatch.setitem(sys.modules, "case01.agents", None)
    assert _providers.import_mavis_provider() is None
    bridge._install_safe_mavis_providers()          # None → 优雅 return

    # 路径 2:provider 正常 → 空 agents 也不炸
    monkeypatch.setattr(_providers, "import_mavis_provider", lambda: object())
    bridge._install_safe_mavis_providers()
