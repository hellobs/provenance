"""测试环境变量隔离守卫:测试代码不得裸写 os.environ,必须走 monkeypatch。

来历(2026-10-05):`tests/test_scenario_builder.py` 在函数体内裸写
`os.environ["CASE_ENGINE_CASES_ROOT"] = str(tmp_path / "_cases")`,
值指向用完即删的 `tmp_path` 且跨文件泄漏、不恢复,
导致 `pytest tests provenance/case_engine/tests` 精确红 12 条
(单跑 case_engine 则全绿,极难排查)。

本守卫在**源码层**静态扫描,把"裸写 os.environ"钉死,
除非显式加 `# env-ok` 白名单注释。生产代码不在扫描范围内 ——
业务代码(如 `case01/run.py` 设 CLI 种子、`case_engine/llm.py` 注入)本就该写 os.environ。
"""
import ast
import io
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATFORM_DIR = os.path.join(REPO_ROOT, "provenance")

# 只扫"测试代码":各层 tests/ 目录下的 *.py
TEST_DIRS = [
    os.path.join(REPO_ROOT, "tests"),
    os.path.join(PLATFORM_DIR, "case01", "tests"),
    os.path.join(PLATFORM_DIR, "case_engine", "tests"),
    os.path.join(REPO_ROOT, "packages", "mavis-case01-injector", "tests"),
    os.path.join(REPO_ROOT, "packages", "mavis-vizkit", "tests"),
]

SKIP_PARTS = (".venv", "__pycache__", "node_modules")


def _iter_test_files():
    for root_dir in TEST_DIRS:
        if not os.path.isdir(root_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS]
            for name in filenames:
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


def _is_module_level(node, tree):
    """判断节点是否在模块顶层(不在任何函数/类里)。"""
    for parent in ast.walk(tree):
        if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(parent):
            if child is node:
                return False
    return True


def _env_subscript_assigns(tree):
    """产出所有 `os.environ[...] = ...`(下标赋值)的节点。"""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for tgt in targets:
            if not isinstance(tgt, ast.Subscript):
                continue
            value = tgt.value
            # os.environ[...]
            if (isinstance(value, ast.Attribute) and value.attr == "environ"
                    and isinstance(value.value, ast.Name) and value.value.id == "os"):
                yield node


def _source_lines(path):
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read().split("\n")


def test_no_bare_environ_writes_in_tests():
    """测试文件里必须用 monkeypatch.setenv,不许裸写 os.environ[...]。"""
    offenders = []
    for path in _iter_test_files():
        lines = _source_lines(path)
        try:
            tree = ast.parse("\n".join(lines), filename=path)
        except SyntaxError:  # pragma: no cover - 语法错的文件由别处暴露
            continue
        for node in _env_subscript_assigns(tree):
            # 模块级 setdefault 之类同样算违规,但给出更精确提示
            lineno = node.lineno
            src_line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
            if "env-ok" in src_line:
                continue
            where = "模块级" if _is_module_level(node, tree) else "函数内"
            offenders.append("{}:{} ({}) {}".format(
                os.path.relpath(path, REPO_ROOT).replace("\\", "/"),
                lineno, where, src_line.strip()))
    assert not offenders, (
        "发现测试代码裸写 os.environ(会跨文件泄漏、污染后续用例)。\n"
        "请改用 monkeypatch.setenv(...);确属有意为之的加 `# env-ok` 注释。\n"
        "违规点:\n  " + "\n  ".join(offenders))


def test_no_bare_environ_writes_via_prepare_helpers():
    """`_xxx_env(..., monkeypatch)` 这类辅助函数必须真的用上 monkeypatch。"""
    offenders = []
    for path in _iter_test_files():
        lines = _source_lines(path)
        try:
            tree = ast.parse("\n".join(lines), filename=path)
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = [a.arg for a in node.args.args]
            if "monkeypatch" not in params:
                continue
            # 函数体内若出现裸写 os.environ 且没有 monkeypatch 调用 → 疑似死参
            body_src = "\n".join(lines[node.lineno - 1: node.end_lineno or node.lineno])
            if "monkeypatch." in body_src:
                continue
            if "os.environ[" in body_src:
                offenders.append("{}:{} def {}".format(
                    os.path.relpath(path, REPO_ROOT).replace("\\", "/"),
                    node.lineno, node.name))
    assert not offenders, (
        "这些函数收了 monkeypatch 却裸写 os.environ(死参数,跨文件泄漏):\n  "
        + "\n  ".join(offenders))
