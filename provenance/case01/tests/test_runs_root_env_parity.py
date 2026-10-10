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

DEFAULT_ROOT = os.path.join(_PKG, "case01", "runs")          # 老位置(搬迁前)
NEW_ROOT = os.path.join(os.path.dirname(_PKG), "data", "case01", "runs")   # 新位置(搬迁后)


def _expected_default_root() -> str:
    """默认根按**优先级规则**判定,而不是钉死某一条路径。

    2026-10-08 归类搬迁:成品记录的家从 `case01/runs` 挪到仓根 `data/case01/runs`,
    读侧由 `case_engine.paths.data_root()` 决定 —— 新位置存在就用新,否则老位置存在就用老,
    两个都没有就用新(待创建)。所以"默认根是什么"取决于**盘上现状**:
    本机(有老数据)解析到老位置,CI 干净检出解析到新位置,搬完都解析到新位置。
    这条测试于是钉"规则",同时把两个候选都认下来 —— 钉死某一条会在另一侧假红。
    """
    if os.path.isdir(NEW_ROOT):
        return NEW_ROOT
    if os.path.isdir(DEFAULT_ROOT):
        return DEFAULT_ROOT
    return NEW_ROOT


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
    assert os.path.normcase(got) == os.path.normcase(_expected_default_root()), got
    # 无论解析到哪一代,都必须是"仓内那两处候选之一",不许漂到别处
    cands = {os.path.normcase(DEFAULT_ROOT), os.path.normcase(NEW_ROOT)}
    assert os.path.normcase(got) in cands, got


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
    rec = run_case01(no_llm=True, run_id="env-root-probe")

    written = env / "env-root-probe" / "run.json"
    assert written.is_file(), "写侧没认环境变量,记录落回了默认根"
    loaded = json.load(io.open(str(written), encoding="utf-8"))
    assert loaded["run_id"] == "env-root-probe" and loaded["branch"] == "B"
    # 读侧闭合:5010 记录面用的就是这条取数路径
    brief = LH._brief_review("env-root-probe")
    assert brief["run_id"] == "env-root-probe"
    assert not brief.get("error"), "界面侧读不到刚写的记录: {}".format(brief["error"])
    # 关键:两代"默认根"里都不能留下这条(否则每次跑测都在污染真实记录目录)
    for cand in (DEFAULT_ROOT, NEW_ROOT):
        assert not os.path.exists(os.path.join(cand, "env-root-probe")), cand


def test_data_root_priority_rule(tmp_path, monkeypatch):
    """`data_root()` 的优先级规则本身:新位置 > 老位置 > 新位置(待创建)。

    为什么单独钉:这条规则**随盘上现状变化**,于是同一次改动在本机(有老数据)与
    CI(干净检出)**解析出不同结果** —— 2026-10-08 实测:`test_runs_root_env_parity`
    就因为"钉死了老位置"而在 CI 上红。规则测清楚,两个环境就都能验。
    """
    from case_engine import paths as P
    repo, pkg = tmp_path / "repo", tmp_path / "repo" / "pkg"
    (repo / "data" / "x").mkdir(parents=True)
    (pkg / "old").mkdir(parents=True)
    monkeypatch.setattr(P, "REPO_ROOT", str(repo))
    monkeypatch.setattr(P, "PKG_ROOT", str(pkg))
    monkeypatch.setitem(P.DATA_KINDS, "t.kind", ("data/x", "old"))
    monkeypatch.setattr(P, "_warned", set())

    assert P.data_root("t.kind") == str(repo / "data" / "x")      # 新存在 → 新
    import shutil
    shutil.rmtree(repo / "data" / "x")
    assert P.data_root("t.kind") == str(pkg / "old")             # 只老存在 → 老(并出声)
    shutil.rmtree(pkg / "old")
    assert P.data_root("t.kind") == str(repo / "data" / "x")     # 都没有 → 新(待创建)
    monkeypatch.setenv("CASE01_RUNS_ROOT", str(tmp_path / "abs"))
    assert P.data_root("t.kind", env_var="CASE01_RUNS_ROOT") == str(tmp_path / "abs")  # 环境变量最高优先
