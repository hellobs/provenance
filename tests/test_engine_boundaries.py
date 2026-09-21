# -*- coding: utf-8 -*-
"""引擎与案例的边界守卫(2026-09-21 体检新增)。

`case_engine` 是**通用**编排层:它只能依赖 stdlib 与 mavis 内核,不许 import 具体案例
(case01 / case00 / live / vizkit)—— 一旦依赖具体案例,"同一份引擎跑多个场景"就不成立了,
而那正是 0920 通用化的全部意义。

同时守住另一件事:5010 对外契约里承诺的路径必须真的在代码里注册(文档与实现不许分叉)。
"""
import io
import os
import re

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "provenance")
ENGINE = os.path.join(_PKG, "case_engine")

CASE_LAYER = re.compile(r"^\s*(from|import)\s+(case01|case00|live|mavis_vizkit)\b")

# 5010 唯一契约承诺的路径 → 由哪一侧实现(源码里必须能找到这个字面量)
CONTRACT_ROUTES = {
    "case00 face": {
        "src": [os.path.join(_PKG, "live", "routes.py"), os.path.join(_PKG, "live", "history.py")],
        "paths": ["/api/runs", "/api/run-detail/{source}/{run_id}", "/embed/scene",
                  "/embed/goals", "/embed/timeline", "/embed/explore", "/health",
                  "/api/undo-intervention"],
    },
    "case01 face": {
        "src": [os.path.join(_PKG, "case01", "review_app.py"),
                os.path.join(_REPO, "packages", "mavis-vizkit", "src", "mavis_vizkit",
                             "plugins", "live.py")],
        "paths": ["/api/review/runs", "/api/review/run/{run_id}", "/api/review/live",
                  "/control/restart", "/embed/scene"],
    },
}


def test_engine_does_not_depend_on_any_case():
    bad = []
    for dirpath, dirs, files in os.walk(ENGINE):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "tests")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            for i, line in enumerate(io.open(p, encoding="utf-8", errors="replace").read().split("\n"), 1):
                if CASE_LAYER.search(line):
                    bad.append((os.path.relpath(p, _PKG).replace("\\", "/"), i, line.strip()[:70]))
    assert not bad, "case_engine 依赖了具体案例(通用性会破): {}".format(bad)


def test_engine_third_party_imports_are_expected():
    """case_engine 的第三方依赖必须是**预期之内**的,且重依赖只在函数内懒加载。

    实测:fastapi(5004 只读服务)、yaml(声明解析)是硬依赖;torch/transformers
    只在 `llm.LocalHFClient._deps()` 里 import —— 所以没用本地 HF 后端的环境不必装它们。
    """
    allowed = {"mavisframework", "yaml", "fastapi", "torch", "transformers", "starlette",
               "pydantic"}
    lazy_only = {"torch", "transformers"}
    bad, lazy_violation = set(), []
    for dirpath, dirs, files in os.walk(ENGINE):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "tests")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            for i, line in enumerate(io.open(os.path.join(dirpath, fn), encoding="utf-8",
                                             errors="replace").read().split("\n"), 1):
                m = re.match(r"^(\s*)(?:from\s+([a-zA-Z_][\w.]*)\s+import|import\s+([a-zA-Z_][\w.]*))",
                             line)
                if not m:
                    continue
                indent, mod = m.group(1), (m.group(2) or m.group(3)).split(".")[0]
                if mod in lazy_only and not indent:
                    lazy_violation.append((fn, i, line.strip()))
                if mod not in allowed:
                    import sys
                    if mod not in set(getattr(sys, "stdlib_module_names", set())) \
                            and mod != "case_engine":
                        bad.add(mod)
    assert not bad, "case_engine 引入了非预期依赖: {}".format(sorted(bad))
    assert not lazy_violation, "重依赖必须懒加载(函数内 import): {}".format(lazy_violation)


@pytest.mark.parametrize("face", sorted(CONTRACT_ROUTES))
def test_contract_paths_are_implemented(face):
    spec = CONTRACT_ROUTES[face]
    src = "\n".join(io.open(p, encoding="utf-8", errors="replace").read()
                    for p in spec["src"] if os.path.isfile(p))
    assert src, "{}: 找不到源码".format(face)
    missing = [p for p in spec["paths"] if '"{}"'.format(p) not in src
               and "'{}'".format(p) not in src]
    assert not missing, "{}: 契约承诺但代码里没有的路由 {}".format(face, missing)


def test_contract_doc_mentions_those_paths():
    """文档侧也要有:契约文档里承诺的路径必须与上面那张表一致(谁改一边都会红)。"""
    doc = os.path.join(_PKG, "docs", "平台对接契约_5010唯一入口.md")
    if not os.path.isfile(doc):
        pytest.skip("契约文档不在")
    txt = io.open(doc, encoding="utf-8").read()
    all_paths = [p for spec in CONTRACT_ROUTES.values() for p in spec["paths"]]
    missing = [p for p in all_paths if p not in txt]
    assert not missing, "契约文档没写这些路径(文档与实现分叉): {}".format(missing)
