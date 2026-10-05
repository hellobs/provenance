# -*- coding: utf-8 -*-
"""demo 记录双份同步守卫的测试(2026-10-05 P2-2)。

仓内 `case01/runs/demo1015-*` 与交付包 `demo-case01-20261015/runs/demo1015-*`
必须逐字节相同。两者都可能不在场(CI 干净检出:仓内 runs 被 gitignore、包是仓外的),
所以**依赖真实数据的两条**在缺数据时明确 skip 并说明理由,不静默。

**守卫自测**(`test_compare_detects_a_real_difference`)不依赖真实数据 ——
它把"仓内侧"与"包侧"都指向 tmp,**在 CI 上照样跑**(2026-10-05 修:初版让它读
真实仓内 runs,于是 CI 干净检出上必然红,run 37297898731)。

对照:`tools/verify_demo_sync.py` 是可复跑命令,本文件把它接进测试面。
"""
import json
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
    """仓内 demo 记录在场就断言结构完整;不在场则 skip 并说明(不静默)。

    2026-10-05 第八审计 N8-3 订正:初版只写了 `if not ...: skip` 就结束,**在场分支
    一条 assert 都没有**,函数名与 docstring 承诺的"断言结构完整"没有实现 ——
    于是它在本机永远 PASS(CI 上永远 skip),恰好把这个文件存在的自述目的
    ("守卫不能是摆设")自己变成了摆设。
    """
    if not _has_repo_runs():
        pytest.skip("case01/runs/demo1015-* 不在场(gitignore):双份同步无本机样本可查")
    # 在场分支:逐条断言结构完整。任一条不成立就红,不许靠 docstring 蒙过去。
    base = os.path.join(vds.PKG_ROOT, "case01", "runs")
    for run in vds.RUNS:
        for fname in vds.FILES:
            path = os.path.join(base, run, fname)
            assert os.path.isfile(path), "自称在场却缺文件:{}/{}".format(run, fname)
            assert os.path.getsize(path) > 0, "文件在但是空的:{}/{}".format(run, fname)
    for run in vds.RUNS:
        with open(os.path.join(base, run, "run.json"), encoding="utf-8") as f:
            rec = json.load(f)
        assert isinstance(rec, dict), "run.json 顶层不是对象:{}".format(run)
        assert rec.get("run_id") == run, "run.json 的 run_id 与目录名不符:{}".format(run)
        assert rec.get("branch") in ("A", "B", "C"), \
            "分支取值非法:{} -> {!r}".format(run, rec.get("branch"))
        assert rec.get("reflection"), "demo 记录必须有反思正文:{}".format(run)


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
    """守卫自测:两侧都指向 tmp,内容不同时 `compare` 必须报差异(不是恒绿的摆设)。

    **不读仓内真实 runs** —— 那会让本测试在 CI 干净检出上必红(P2-2 初版的错)。
    这里自己造齐两侧:一侧原样,一侧内容被改,断言 compare 报出差异且 same==0。
    """
    repo = tmp_path / "repo_runs"
    pkg = tmp_path / "pkg"
    for run in vds.RUNS:
        rd = repo / run
        pd = pkg / "runs" / run
        rd.mkdir(parents=True)
        pd.mkdir(parents=True)
        for name in vds.FILES:
            (rd / name).write_text("original-{}".format(run), encoding="utf-8")
            # 包侧内容**故意不同** —— compare 必须逐字节比出来
            (pd / name).write_text("mismatch-{}".format(run), encoding="utf-8")
    missing, diff, same = vds.compare(str(pkg), repo_runs=str(repo))
    assert not missing, "两侧文件都齐,不该报缺:{}".format(missing)
    assert diff, "内容全都不同却报无差异 —— 守卫失效"
    assert same == 0


def test_compare_reports_identical_sides_as_same(tmp_path):
    """反向:两侧逐字节相同时 `compare` 必须报 same==total、无 diff(不是恒红的摆设)。"""
    repo = tmp_path / "repo_runs"
    pkg = tmp_path / "pkg"
    for run in vds.RUNS:
        rd = repo / run
        pd = pkg / "runs" / run
        rd.mkdir(parents=True)
        pd.mkdir(parents=True)
        for name in vds.FILES:
            (rd / name).write_text("same-{}".format(run), encoding="utf-8")
            (pd / name).write_text("same-{}".format(run), encoding="utf-8")
    missing, diff, same = vds.compare(str(pkg), repo_runs=str(repo))
    assert not missing and not diff, (missing, diff)
    assert same == len(vds.RUNS) * len(vds.FILES)


def test_compare_flags_a_missing_package_file(tmp_path):
    """包侧少一个文件时必须报缺(不能因为"仓内也没有"就蒙混过去)。"""
    repo = tmp_path / "repo_runs"
    pkg = tmp_path / "pkg"
    for run in vds.RUNS:
        rd = repo / run
        pd = pkg / "runs" / run
        rd.mkdir(parents=True)
        pd.mkdir(parents=True)
        for name in vds.FILES:
            (rd / name).write_text("x", encoding="utf-8")
            (pd / name).write_text("x", encoding="utf-8")
    # 删掉包侧一个文件
    victim = os.path.join(str(pkg), "runs", vds.RUNS[0], vds.FILES[0])
    os.unlink(victim)
    missing, diff, same = vds.compare(str(pkg), repo_runs=str(repo))
    assert missing, "包侧缺文件却没报缺 —— 覆盖不完整"
    assert len(missing) == 1
    assert missing[0][0] == vds.RUNS[0] and missing[0][1] == vds.FILES[0]
