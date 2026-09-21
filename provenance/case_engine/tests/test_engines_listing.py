# -*- coding: utf-8 -*-
"""引擎注册表:必须能列出**全部**引擎(2026-09-21 加)。

背景:配置工具的「引擎」页要列全部已注册引擎,而 `describe()` 无参只返回**默认引擎**
(默认 experiment-eval)—— 照它列会静默漏掉 sandbox-value。CLI 的 `engines` 子命令
一直是直接读私有 `ENGINES`,现在提成公开 `all_ids()`,谁列引擎都走它。
"""
import os
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(os.path.dirname(_this_dir))          # provenance/provenance
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from case_engine import engines as E  # noqa: E402


def test_all_ids_returns_every_registered_engine():
    ids = E.all_ids()
    assert ids == sorted(ids), "排序输出,便于稳定展示"
    assert "experiment-eval" in ids
    assert "sandbox-value" in ids, "describe() 无参只给默认引擎,别照它列"
    for i in ids:
        assert E.known(i), i
        info = E.describe(i)
        assert info.get("name"), i


def test_describe_without_arg_is_only_the_default_engine():
    """这条是回归:无参 describe() 只给默认引擎 —— 别再拿它当"全部引擎"。"""
    one = E.describe()
    assert isinstance(one, dict) and one.get("engine") == E.DEFAULT_ENGINE
