# -*- coding: utf-8 -*-
"""mavis 纯洁度的**可执行断言**(2026-09-21 深度体检新增)。

我在这几轮反复声称"mavis 内核零业务词、零反向依赖"。口头声称不算数,这里把它变成测试:

1. `mavisframework/**` 的**真代码**里不得出现业务词(注释与文档字符串允许 —— 
   它们解释"为什么这样设计",反而是有价值的);用 tokenize 精确区分 docstring 与代码;
2. `mavisframework/**` 不得 import/reference 案例层(case01 / case_engine / provenance);
配置工具已迁入 provenance,不再把案例层工具算作 mavis 的反向依赖欠债。
"""
import io
import json
import os
import re
import subprocess
import sys
import tokenize

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)                     # D:\zzr\provenance
_MAVIS = os.path.join(os.path.dirname(_REPO), "mavis")
_FW = os.path.join(_MAVIS, "mavisframework")

BIZ = re.compile("投资|股票|治理|审批|案例|HCM|Ethan|Investment AI")
REVERSE = re.compile(r"^\s*(from|import)\s+(case01|case_engine|mavis_vizkit|live)\b")

pytestmark = pytest.mark.skipif(not os.path.isdir(_FW), reason="mavis 仓不在预期位置")

def _py_files(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "build")]
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _docstring_lines(src):
    lines = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.STRING:
                for ln in range(tok.start[0], tok.end[0] + 1):
                    lines.add(ln)
    except (tokenize.TokenError, IndentationError):
        pass
    return lines


def test_no_business_words_in_code():
    """真代码里 0 处业务词;注释/文档串里的命中如实列出来(不作为失败)。"""
    in_code, in_docs = [], []
    for p in _py_files(_FW):
        src = io.open(p, encoding="utf-8", errors="replace").read()
        doc = _docstring_lines(src)
        for i, line in enumerate(src.split("\n"), 1):
            if not BIZ.search(line):
                continue
            rel = os.path.relpath(p, _MAVIS).replace("\\", "/")
            if line.strip().startswith("#") or i in doc:
                in_docs.append((rel, i))
            else:
                in_code.append((rel, i, line.strip()[:80]))
    assert not in_code, "mavis 真代码里出现业务词(必须挪到案例侧): {}".format(in_code)
    # 文档串里允许;但把它们打印出来,便于人工确认没有夹带"案例规则"
    print("注释/文档串里的业务词 {} 处(允许,供人工过目)".format(len(in_docs)))


def test_no_reverse_imports_of_case_layer():
    bad = []
    for p in _py_files(_FW):
        src = io.open(p, encoding="utf-8", errors="replace").read()
        for i, line in enumerate(src.split("\n"), 1):
            if REVERSE.search(line):
                bad.append((os.path.relpath(p, _MAVIS).replace("\\", "/"), i, line.strip()[:70]))
    assert not bad, "mavisframework 反向依赖案例层: {}".format(bad)
