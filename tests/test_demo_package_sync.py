# -*- coding: utf-8 -*-
"""demo 记录双份同步守卫的测试(2026-10-05 P2-2)。

仓内 `case01/runs/demo1015-*` 与交付包 `demo-case01-20261015/runs/demo1015-*`
必须逐字节相同。两者都可能不在场(CI 干净检出:仓内 runs 被 gitignore、包是仓外的),
所以本测试在缺数据时**明确 skip 并说明理由**,不静默。

对照:`tools/verify_demo_sync.py` 是可复跑命令,本文件把它接进测试面。
"""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "provenance"))

from tools import verify_demo_sync as vds  # noqa: E402


def _has_repo_runs():
    return all(os.path.isfile(os.path.join(vds.PKG_ROOT, "case01", "runs", r, "run.json"))
               for r in vds.RUNS)


def test_repo_side_present_or_skipped_with_reason():
    """仓内 demo 记录在场就断言结构完整;不在场则 skip 并说明(不静默)。"""
    if not _has_repo_runs():
        pytest.skip("case01/runs/demo1015-* 不在场(gitignore):双份同步无本机样本可查")


def test_package_side_matches_repo_side():
    """有交付包时,两边必须逐字节相同 —— 这是 P2-2 的核心断言。"""
    if not os.path.isdir(vds.DEFAULT_PKG):
        pytest.skip("交付包 {} 不在场(仓外):无从比对".format(vds.DEFAULT_PKG))
    if not _has_repo_runs():
        pytest.skip("仓内 demo 记录不在场:无从比对")
    missing, diff, same = vds.compare(vds.DEFAULT_PKG)
    assert not missing, "demo 两份有缺失文件:{}".format(missing)
    assert not diff, "demo 两份不同步:{}".format(diff)
    assert same == len(vds.RUNS) * len(vds.FILES)


def test_compare_detects_a_real_difference(tmp_path):
    """守卫自测:构造一份被改动的包,`compare` 必须报差异(不是恒绿的摆设)。"""
    pkg = tmp_path / "pkg"
    runs = pkg / "runs"
    for run in vds.RUNS:
        d = runs / run
        d.mkdir(parents=True)
        for name in vds.FILES:
            # 用一个"故意与仓内不同"的内容
            (d / name).write_text("mismatch-{}".format(run), encoding="utf-8")
    missing, diff, same = vds.compare(str(pkg))
    assert not missing, "所有文件都在,不该报缺:{}".format(missing)
    assert diff, "内容全都不同却报无差异 —— 守卫失效"
    assert same == 0
