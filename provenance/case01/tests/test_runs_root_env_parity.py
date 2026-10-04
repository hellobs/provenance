# -*- coding: utf-8 -*-
"""数据根 `CASE01_RUNS_ROOT` 的读写同口径守卫。

立这条的经过(2026-10-04 实测):文档(`docs/平台对接契约_5010唯一入口.md`、
`case01/docs/Governance平台对接说明.md`)都写"数据根可用 `CASE01_RUNS_ROOT` 覆盖",
但当时**只有读侧认这个变量**,`case01/orchestrator.RUNS_ROOT` 写死 `case01/runs`。
后果正好落在演示场景上:换根起界面 → 现场跑一条 → 记录落在默认根 → 正在展示的界面
里没有这条,看起来像"跑失败了"。

所以这里钉三件事:环境变量优先、置空回落默认、**写侧与读侧解出同一个根**,
最后用一条真跑(no-llm,不碰 GPU)证明整圈闭合。
"""
import importlib
import io
import json
import os
import sys

import pytest

_PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from case01 import orchestrator as OR  # noqa: E402
from case01.orchestrator import run_case01  # noqa: E402
from live import history as LH  # noqa: E402

DEFAULT_ROOT = os.path.join(_PKG, "case01", "runs")


def test_env_var_has_priority_over_default_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CASE01_RUNS_ROOT", str(tmp_path / "records"))
    assert OR.RUNS_ROOT() == str(tmp_path / "records")


@pytest.mark.parametrize("value", [None, ""])
def test_blank_or_unset_falls_back_to_repo_runs(tmp_path, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("CASE01_RUNS_ROOT", raising=False)
    else:
        monkeypatch.setenv("CASE01_RUNS_ROOT", value)
    got = OR.RUNS_ROOT()
    assert os.path.normcase(got) == os.path.normcase(DEFAULT_ROOT), got


def test_write_side_and_read_side_resolve_the_same_root(tmp_path, monkeypatch):
    """同一个环境变量值,写侧与 5010 读侧必须解出同一个目录 —— 这是本条守卫的全部意义。"""
    env = str(tmp_path / "records")
    monkeypatch.setenv("CASE01_RUNS_ROOT", env)
    assert OR.RUNS_ROOT() == LH._data_root("CASE01_RUNS_ROOT", "case01", "runs")
    monkeypatch.delenv("CASE01_RUNS_ROOT")
    assert OR.RUNS_ROOT() == LH._data_root("CASE01_RUNS_ROOT", "case01", "runs")


def test_serve_module_constant_tracks_the_env_on_reload(tmp_path, monkeypatch):
    """只读面 `case01/serve.py` 在 import 时读环境变量;重加载后应与写侧一致。"""
    env = str(tmp_path / "records")
    monkeypatch.setenv("CASE01_RUNS_ROOT", env)
    serve = importlib.reload(importlib.import_module("case01.serve"))
    try:
        assert serve.RUNS_ROOT == OR.RUNS_ROOT()
    finally:
        monkeypatch.delenv("CASE01_RUNS_ROOT")
        importlib.reload(serve)


def test_run_lands_in_env_root_and_read_side_finds_it(tmp_path, monkeypatch):
    """真跑一条(no-llm 固定文本,不占 GPU):落在覆盖根,且读侧能按 run_id 取回。"""
    env = tmp_path / "records"
    env.mkdir()
    monkeypatch.setenv("CASE01_RUNS_ROOT", str(env))
    rec = run_case01(no_llm=True, timeline="B", run_id="env-root-probe")

    written = env / "env-root-probe" / "run.json"
    assert written.is_file(), "写侧没认环境变量,记录落回了默认根"
    loaded = json.load(io.open(str(written), encoding="utf-8"))
    assert loaded["run_id"] == "env-root-probe" and loaded["branch"] == "B"
    # 读侧闭合:5010 记录面用的就是这条取数路径
    brief = LH._brief_review("env-root-probe")
    assert brief["run_id"] == "env-root-probe"
    assert not brief.get("error"), "界面侧读不到刚写的记录: {}".format(brief["error"])
    # 关键:默认根里不能留下这条(否则每次跑测都在污染真实记录目录)
    assert not os.path.exists(os.path.join(DEFAULT_ROOT, "env-root-probe"))
