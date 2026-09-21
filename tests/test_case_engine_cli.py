# -*- coding: utf-8 -*-
"""case_engine CLI 全子命令冒烟(2026-09-21 深度体检新增)。

以前只测过 `materialize-value-tendency`;`list / show / run / engines` 没有任何守卫,
而它们是别人接入这套引擎的第一入口 —— 命令挂了却没人知道。

覆盖:退出码 + 关键输出片段;并专门断言 `engines` 会列出**全部**引擎(describe() 无参只给默认那个)。
"""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "provenance")
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

import case_engine.cli as cli          # noqa: E402


def test_list_shows_discovered_scenarios(capsys):
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    for case in ("case00_village", "case01_stock", "case02_minimal"):
        assert case in out, "list 没列出 {}:\n{}".format(case, out)


def test_show_prints_scenario_detail(capsys):
    assert cli.main(["show", "case01_stock"]) == 0
    out = capsys.readouterr().out
    assert "case01_stock" in out and ("engine" in out or "引擎" in out), out[:400]


def test_engines_lists_all_registered(capsys):
    assert cli.main(["engines"]) == 0
    out = capsys.readouterr().out
    assert "experiment-eval" in out and "sandbox-value" in out, \
        "engines 只列了默认引擎?:\n{}".format(out)


def test_engines_compat_matrix_for_a_scenario(capsys):
    assert cli.main(["engines", "--case-id", "case00_village"]) == 0
    out = capsys.readouterr().out
    assert "兼容矩阵" in out and "sandbox-value" in out, out


def test_run_experiment_engine_produces_result(capsys):
    """受控实验引擎是免 LLM 的确定性真跑:必须能跑通并给出分支/一致性。"""
    assert cli.main(["run", "case01_stock", "--engine", "experiment-eval"]) == 0
    out = capsys.readouterr().out
    assert "branch" in out.lower() or "分支" in out, out[:400]


def test_run_sandbox_engine_is_assembly_check(capsys):
    """沙盒引擎不假装跑完:它只报装配预检结论。"""
    assert cli.main(["run", "case00_village", "--engine", "sandbox-value"]) == 0
    out = capsys.readouterr().out
    assert "assembly" in out.lower() or "预检" in out or "可装配" in out, out[:400]


def test_unknown_case_exits_nonzero(capsys):
    rc = cli.main(["show", "no_such_case"])
    assert rc != 0, "不存在的场景必须以非 0 退出(否则脚本会以为成功)"
