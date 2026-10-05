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


def test_write_side_rejects_relative_path(monkeypatch):
    """写侧对相对路径必须**当场报错**(2026-10-05 补)。

    此前 `RUNS_ROOT()` 是裸 `os.environ.get(...) or 默认根`,相对值时写侧按 cwd
    落盘、读侧却抛 AmbiguousPathError —— 读写不对称:同一条配置写侧"能跑",
    界面上却读不到。这条把对称性钉死。
    """
    from case_engine.paths import AmbiguousPathError
    monkeypatch.setenv("CASE01_RUNS_ROOT", "case01/runs")
    with pytest.raises(AmbiguousPathError):
        OR.RUNS_ROOT()


def test_write_side_and_read_side_agree_on_relative_rejection(monkeypatch):
    """读写两侧对"相对路径"的判定必须一致:都拒绝,而不是一侧拒绝一侧接受。"""
    from case_engine.paths import AmbiguousPathError
    monkeypatch.setenv("CASE01_RUNS_ROOT", "some/relative/root")
    with pytest.raises(AmbiguousPathError):
        OR.RUNS_ROOT()
    with pytest.raises(AmbiguousPathError):
        LH._data_root("CASE01_RUNS_ROOT", "case01", "runs")


def test_serve_module_constant_tracks_the_env_on_reload(tmp_path, monkeypatch):
    """只读面 `case01/serve.py` 在 import 时读环境变量;重加载后应与写侧一致。"""
    env = str(tmp_path / "records")
    monkeypatch.setenv("CASE01_RUNS_ROOT", env)
    serve = importlib.reload(importlib.import_module("case01.serve"))
    try:
        assert serve.RUNS_ROOT == OR.RUNS_ROOT()
    finally:
        monkeypatch.delenv("CASE01_RUNS_ROOT", raising=False)
        importlib.reload(serve)


def test_serve_module_rejects_relative_root_on_reload(monkeypatch):
    """只读面同样必须拒绝相对值(2026-10-05 补,GTC 体检 N5/N8)。

    此前 `serve.py` 是**裸读** `os.environ.get(...)`,相对值时按 cwd 解释 ——
    与写侧 `RUNS_ROOT()` 的抛错不对称。虽然这是只读面,但"同一配置在两侧
    给出不同答案"本身就是错的,而且它 import 时求值、错得静默。
    """
    from case_engine.paths import AmbiguousPathError
    monkeypatch.setenv("CASE01_RUNS_ROOT", "case01/runs")
    try:
        with pytest.raises(AmbiguousPathError):
            importlib.reload(importlib.import_module("case01.serve"))
    finally:
        monkeypatch.delenv("CASE01_RUNS_ROOT", raising=False)
        importlib.reload(importlib.import_module("case01.serve"))


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
