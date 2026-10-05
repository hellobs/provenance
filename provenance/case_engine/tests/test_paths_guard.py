# -*- coding: utf-8 -*-
"""路径解析守卫测试:环境变量给的根目录**必须是绝对路径**(引擎通用)。

来历(2026-10-05):本仓一票 `*_ROOT`/`*_DIR` 环境变量此前原样透传,
相对值的行为随进程 cwd 漂移,而且**不报错**:

    CASE_ENGINE_CASES_ROOT=cases
    cwd = 仓库根                → list_cases 返回 0 条(静默空)
    cwd = provenance/provenance → list_cases 返回 3 条

更坏的一条:`scenario_builder.save_scenario` 会 `makedirs` 到解析结果,
相对值 = 把场景写进进程 cwd。现统一改为"相对值直接报错"。

本文件同时钉住三件事:
  1. 相对值 → `AmbiguousPathError`(不是静默按 cwd 解释);
  2. 绝对值与未设环境变量 → 行为不变(回归保护);
  3. 解析结果**与 cwd 无关**(换目录跑答案一致)。
"""
import os
import subprocess
import sys

import pytest

from case_engine.paths import AmbiguousPathError, require_abs_path, resolve_root
from case_engine.scenarios import default_cases_root

# case_engine/../cases = provenance/provenance/cases
CASES_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cases"))


# ---------------------------------------------------------------------------
# 1) 单元:require_abs_path / resolve_root
# ---------------------------------------------------------------------------

def test_require_abs_path_accepts_absolute(monkeypatch):
    r = require_abs_path(CASES_ROOT, what="X")
    assert r == os.path.normpath(CASES_ROOT)
    assert os.path.isabs(r)


@pytest.mark.parametrize("rel", ["cases", "./cases", "..\\cases", "a/b"])
def test_require_abs_path_rejects_relative(rel):
    with pytest.raises(AmbiguousPathError) as ei:
        require_abs_path(rel, what="CASE_ENGINE_CASES_ROOT")
    msg = str(ei.value)
    # 错误信息要能自己说清"为什么拒绝 + 怎么改"
    assert "绝对路径" in msg and "相对路径" in msg
    assert repr(rel) in msg
    assert "CASE_ENGINE_CASES_ROOT" in msg


def test_require_abs_path_rejects_empty():
    with pytest.raises(AmbiguousPathError):
        require_abs_path("   ", what="X")


def test_require_abs_path_expands_user_and_vars(monkeypatch):
    monkeypatch.setenv("PATHDEMO", CASES_ROOT)
    assert require_abs_path("$PATHDEMO", what="X") == os.path.normpath(CASES_ROOT)
    # ~ 展开后仍是绝对路径
    r = require_abs_path("~", what="X")
    assert os.path.isabs(r)


def test_resolve_root_prefers_env_then_default(monkeypatch):
    monkeypatch.delenv("CASE_ENGINE_CASES_ROOT", raising=False)
    assert resolve_root("CASE_ENGINE_CASES_ROOT", CASES_ROOT) == os.path.normpath(CASES_ROOT)
    monkeypatch.setenv("CASE_ENGINE_CASES_ROOT", CASES_ROOT)
    assert resolve_root("CASE_ENGINE_CASES_ROOT", "/nonexistent") == os.path.normpath(CASES_ROOT)
    monkeypatch.setenv("CASE_ENGINE_CASES_ROOT", "cases")
    with pytest.raises(AmbiguousPathError):
        resolve_root("CASE_ENGINE_CASES_ROOT", CASES_ROOT)


# ---------------------------------------------------------------------------
# 2) default_cases_root:相对值报错、其余与 cwd 无关
# ---------------------------------------------------------------------------

def test_default_cases_root_rejects_relative(monkeypatch):
    monkeypatch.setenv("CASE_ENGINE_CASES_ROOT", "cases")
    with pytest.raises(AmbiguousPathError):
        default_cases_root()


def test_default_cases_root_absolute_env(monkeypatch):
    monkeypatch.setenv("CASE_ENGINE_CASES_ROOT", CASES_ROOT)
    assert default_cases_root() == os.path.normpath(CASES_ROOT)


def test_default_cases_root_without_env_is_repo_cases(monkeypatch):
    monkeypatch.delenv("CASE_ENGINE_CASES_ROOT", raising=False)
    assert default_cases_root() == os.path.normpath(CASES_ROOT)
    assert os.path.isdir(default_cases_root())


# ---------------------------------------------------------------------------
# 3) 跨 cwd 一致性:同一配置从不同目录启动,解析结果必须相同
# ---------------------------------------------------------------------------

_CWD_PROBE = r'''
import os, sys
sys.path.insert(0, r"{plat}")
from case_engine.scenarios import default_cases_root
os.environ["CASE_ENGINE_CASES_ROOT"] = r"{root}"
print(default_cases_root())
'''


def _probe_from(cwd, plat, root):
    code = _CWD_PROBE.format(plat=plat, root=root)
    env = dict(os.environ)
    env.pop("CASE_ENGINE_CASES_ROOT", None)
    out = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_resolved_root_is_cwd_independent():
    """同一绝对配置,从仓库根与从 provenance/provenance 启动,答案必须一致。"""
    repo_root = os.path.normpath(os.path.join(CASES_ROOT, "..", ".."))
    plat_dir = os.path.normpath(os.path.join(CASES_ROOT, ".."))
    a = _probe_from(repo_root, plat_dir, CASES_ROOT)
    b = _probe_from(plat_dir, plat_dir, CASES_ROOT)
    assert a == b == os.path.normpath(CASES_ROOT)


def test_relative_root_fails_from_any_cwd():
    """相对配置从任何目录启动都应报错(以前是"某个目录碰巧能跑")。"""
    repo_root = os.path.normpath(os.path.join(CASES_ROOT, "..", ".."))
    plat_dir = os.path.normpath(os.path.join(CASES_ROOT, ".."))
    for cwd in (repo_root, plat_dir):
        code = _CWD_PROBE.format(plat=plat_dir, root="cases")
        env = dict(os.environ)
        env.pop("CASE_ENGINE_CASES_ROOT", None)
        out = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                             capture_output=True, text=True, timeout=60)
        assert out.returncode != 0, "相对配置在 {} 下竟然通过了".format(cwd)
        assert "AmbiguousPathError" in out.stderr or "绝对路径" in out.stderr
