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
import io
import json
import os
import re
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


# ---------------------------------------------------------------------------
# 方向守卫(2026-10-05 第九轮 N9-1):"谁是原件"曾被文档写反,且没人拦得住
# ---------------------------------------------------------------------------
def test_run_demo_script_generates_into_the_package_and_never_copies():
    """`run_demo.sh` 必须**直接把记录生成在包里**,且不含任何拷贝命令。

    N9-1 的要害不是措辞,是后果:方向含糊时,"演示当天按手册重跑 `run_demo.sh`"
    只会重新生成**包内**那一份,而5010 读的**仓内**那份原地不动——现场看到旧记录、
    交给对方的说明是新记录,而 `verify_demo_sync` 要等有人手动跑才会报差异。
    本条把"脚本不做拷贝"钉成契约:将来谁想改成"写仓内再 cp 到包",必须先改本条
    并同步手册,而不是让两处默默分叉。
    """
    script = os.path.join(vds.DEFAULT_PKG, "run_demo.sh")
    if not os.path.isfile(script):
        pytest.skip("交付包不在场(仓外数据):{}".format(script))
    with io.open(script, encoding="utf-8") as fh:
        text = fh.read()
    # 生成目标指向包内 runs/(这正是"包内是原件"的判据)
    assert "CASE01_RUNS_ROOT" in text, \
        "run_demo.sh 不再设置 CASE01_RUNS_ROOT —— 生成目标变了,拷贝方向结论要重新核实"
    m = re.search(r"CASE01_RUNS_ROOT=['\"]?([^'\"\n]+)", text)
    assert m, "解析不出 CASE01_RUNS_ROOT 的值"
    assert "demo-case01" in m.group(1), \
        "CASE01_RUNS_ROOT 不再指向交付包(现在是 {})—— 若改成写仓内,必须同时补上"\
        "明确的 cp 并更新 verify_demo_sync 的方向说明与手册".format(m.group(1))
    # 不得含拷贝命令(否则方向就不再是单向的"包内生成")
    for pat in (r"\bcp\b", r"\bcopy\b", r"shutil\.copy", r"Copy-Item", r"xcopy"):
        assert not re.search(pat, text), \
            "run_demo.sh 出现了拷贝命令({})—— 与『包内是原件』的单向结论冲突".format(pat)


def test_no_doc_claims_the_copy_runs_repo_to_package():
    """仓内**入库**文档不得再宣称拷贝方向是"仓内 → 包"。

    N9-1 之前 `tools/verify_demo_sync.py` 的 docstring 正是这么写的(还把包描述成
    "手工拷贝"的下游)。它在仓内、被跟踪、也被当权威口径读,所以方向写反会被评审
    当成事实。本条只查**方向措辞**,不查别的 —— 目标是"别再把方向说反",不是
    "锁死文案"(措辞会随别的改动正常演进)。
    """
    doc = os.path.join(_REPO, "provenance", "tools", "verify_demo_sync.py")
    with io.open(doc, encoding="utf-8") as fh:
        text = fh.read()
    # 反向表述 = 同一句里先出现"仓内"、后出现"拷…包"(中间允许隔最多 40 字,
    # 因为真实误写常常是"那份由 run_demo.sh 手工从仓内拷进本包"这种长句)。
    for m in re.finditer(r"仓内.{0,40}?拷(?:进|到|入).{0,20}?包", text, re.S):
        seg = m.group(0)
        if any(w in seg for w in ("写反", "订正", "此前", "原先", "不是本")):
            continue          # 订正说明本身,合法
        pytest.fail("verify_demo_sync.py 里仍宣称『仓内拷进包』:{}"
                    .format(seg.replace("\n", " ").strip()))


# ---------------------------------------------------------------------------
# zip 交付件与包目录同步(2026-10-05 第十二轮 4.3)
# ---------------------------------------------------------------------------
def _make_package(tmp_path, extra=None):
    """造一个最小交付包:两条 run + 一个 SHA256SUMS.txt(+ extra 项覆盖用)。"""
    import hashlib
    pkg = tmp_path / "pkg"
    for run in ("demo1015-A", "demo1015-auto"):
        for name in vds.FILES:
            p = pkg / "runs" / run / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}:{}".format(run, name), encoding="utf-8")
    for rel, content in (extra or {}).items():
        p = pkg / rel.replace("/", os.sep)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    lines = []
    for dp, _dirs, fs in os.walk(str(pkg)):
        for f in fs:
            lp = os.path.join(dp, f)
            rel = os.path.relpath(lp, str(pkg)).replace(os.sep, "/")
            if rel == "SHA256SUMS.txt":
                continue
            lines.append("{} *{}\n".format(
                hashlib.sha256(open(lp, "rb").read()).hexdigest(), rel))
    (pkg / "SHA256SUMS.txt").write_text("# 自验清单\n" + "".join(sorted(lines)),
                                        encoding="utf-8")
    return pkg


def _make_zip(pkg, zip_path):
    import zipfile
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        for dp, _dirs, fs in os.walk(str(pkg)):
            for f in fs:
                lp = os.path.join(dp, f)
                zf.write(lp, os.path.relpath(lp, str(pkg)).replace(os.sep, "/"))
    import hashlib
    h = hashlib.sha256(open(str(zip_path), "rb").read()).hexdigest()
    with io.open(str(zip_path) + ".sha256", "w", encoding="utf-8") as f:
        f.write("{}  {}\n".format(h, os.path.basename(str(zip_path))))


def test_zip_check_is_a_noop_when_nothing_is_packaged(tmp_path):
    """没打 zip 时本项"不适用",不能报成不一致(没交付件 ≠ 不一致)。"""
    pkg = _make_package(tmp_path)
    assert vds.check_zip(str(pkg)) is None


def test_zip_in_sync_with_package_passes(tmp_path):
    pkg = _make_package(tmp_path)
    _make_zip(pkg, tmp_path / "pkg.zip")
    assert vds.check_zip(str(pkg)) == []


def test_zip_missing_sidecar_is_reported(tmp_path):
    """sidecar 缺失也要报:对方无从校验,且"没报错"会被读成 zip 已验证。"""
    pkg = _make_package(tmp_path)
    _make_zip(pkg, tmp_path / "pkg.zip")
    os.remove(str(tmp_path / "pkg.zip.sha256"))
    problems = vds.check_zip(str(pkg))
    assert problems and any(".sha256" in p for p in problems), problems


def test_zip_with_stale_file_is_detected(tmp_path):
    """**本条是第十二轮 4.3 的原 bug**:目录改了、zip 没重打 ⇒ 必须报出来。

    变异验证:把 `check_zip` 的内容比对那段去掉,本条立刻绿(假绿)。
    """
    pkg = _make_package(tmp_path, extra={"README.txt": "v1"})
    _make_zip(pkg, tmp_path / "pkg.zip")
    # 事后订正包内文件(模拟"改了 SHA256SUMS.txt 但忘了重打 zip")
    (pkg / "README.txt").write_text("v2-方向已订正", encoding="utf-8")
    problems = vds.check_zip(str(pkg))
    assert problems, "zip 与目录已不同步却没报出来"
    assert any("README.txt" in p for p in problems), problems


def test_zip_missing_a_newly_added_file_is_detected(tmp_path):
    """目录新增文件但 zip 没重打 ⇒ 也要报(否则对方拿到的是缺件的包)。"""
    pkg = _make_package(tmp_path)
    _make_zip(pkg, tmp_path / "pkg.zip")
    (pkg / "runs" / "demo1015-A" / "extra.json").write_text("{}", encoding="utf-8")
    problems = vds.check_zip(str(pkg))
    assert any("extra.json" in p and "zip 内没有" in p for p in problems), problems


def test_zip_with_leftover_file_is_detected(tmp_path):
    """zip 里有目录已删掉的旧文件 ⇒ 也要报(旧文件同样误导对方)。"""
    pkg = _make_package(tmp_path, extra={"OLD.txt": "上一版残留"})
    _make_zip(pkg, tmp_path / "pkg.zip")
    os.remove(str(pkg / "OLD.txt"))
    problems = vds.check_zip(str(pkg))
    assert any("OLD.txt" in p for p in problems), problems


def test_sidecar_mismatch_is_reported(tmp_path):
    """sidecar 与 zip 不匹配 ⇒ 报(它只证明 zip 没坏,不证明 zip 新)。"""
    pkg = _make_package(tmp_path)
    _make_zip(pkg, tmp_path / "pkg.zip")
    (tmp_path / "pkg.zip.sha256").write_text("0" * 64 + "  pkg.zip\n", encoding="utf-8")
    problems = vds.check_zip(str(pkg))
    assert any(".sha256" in p for p in problems), problems


def test_cli_wires_the_zip_check_into_main(tmp_path, monkeypatch, capsys):
    """`main()` 必须真的调 `check_zip` 并把不同步反映到**退出码**上。

    为什么单列一条(变异验证实测):前七条都只测 `check_zip` 这个函数,
    变异把 `main()` 里的 `check_zip(...)` 换成 `None` 时**测试全绿** ——
    函数被测得很好,而 CLI 根本没调用它。本条把接线钉住:zip 与目录不同步时
    `main()` 必须非 0 退出。
    """
    pkg = _make_package(tmp_path, extra={"README.txt": "v1"})
    _make_zip(pkg, tmp_path / "pkg.zip")
    (pkg / "README.txt").write_text("v2", encoding="utf-8")   # 忘了重打 zip
    monkeypatch.setattr(vds, "compare",
                        lambda package, repo_runs="": ([], [], 16))
    rc = vds.main(["--package", str(pkg)])
    out = capsys.readouterr().out
    assert rc == 1, "zip 与目录不同步但 main() 返回 0(退出码没反映出来)"
    assert "zip" in out.lower(), out
