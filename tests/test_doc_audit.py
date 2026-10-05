# -*- coding: utf-8 -*-
"""doc_audit 状态块工具的守卫(2026-10-03 新增)。

起因(反复踩的坑,这次把根因焊死):
1. `deep_check.py` 以前实跑 `doc_audit --banner` 来验幂等,而 --banner 每次都用**当天
   日期**重建全部状态块 —— 每天第一次体检必然改脏几十个 md(还把多行块改坏,实测一次
   改了 64 份)。修后的口径:`needs_banner` 只在缺块/块重复/状态或说明与 STATUS 表
   不一致时才要求重写;**状态说明没变,旧日期保留**。
2. 体检需要只读模式:`--check` 只报告、不写盘,有待修时退出码 1。
"""
import importlib.util
import io
import os
import re
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "provenance")
_TOOL = os.path.join(_PKG, "tools", "doc_audit.py")
_PY = sys.executable

_spec = importlib.util.spec_from_file_location("doc_audit_tool", _TOOL)
da = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(da)


def _doc(state="现行", note="一份说明", date="2020-01-01", body="## 正文\n\n内容。\n"):
    return ("# 标题\n\n"
            "> **状态**:{}\n"
            "> **最后核对**:{}\n"
            "> **说明**:{}\n\n"
            "{}").format(state, date, note, body)


@pytest.fixture
def known_doc(monkeypatch):
    """把一个临时 rel 登记进 STATUS 表,测试结束还原。"""
    rel = "docs/_audit_fixture.md"
    monkeypatch.setitem(da.STATUS, rel, ("现行", "一份说明"))
    return rel


def test_unchanged_state_keeps_old_date(known_doc):
    """状态与说明一致时,哪怕日期是旧的,也不需要改(这是本次修复的核心)。"""
    assert da.needs_banner(_doc(date="2020-01-01"), known_doc) is False


def test_missing_block_needs_banner(known_doc):
    assert da.needs_banner("# 标题\n\n## 正文(没有状态块)\n", known_doc) is True


def test_duplicated_block_needs_banner(known_doc):
    """叠了两份状态块(历史 bug):必须判为待修,normalize 后收敛成一份。"""
    dup = ("# 标题\n\n"
           "> **状态**:现行\n> **最后核对**:2020-01-01\n> **说明**:一份说明\n"
           "> **状态**:现行\n> **最后核对**:2020-01-02\n> **说明**:一份说明\n\n"
           "## 正文\n")
    assert da.needs_banner(dup, known_doc) is True
    fixed = da.normalize_banner(dup, known_doc, "2026-10-03")
    assert fixed.count("**状态**") == 1
    assert fixed.count("**最后核对**") == 1


def test_state_or_note_mismatch_needs_banner(known_doc):
    assert da.needs_banner(_doc(state="历史存档"), known_doc) is True
    assert da.needs_banner(_doc(note="不一样的说明"), known_doc) is True


def test_normalize_preserves_body(known_doc):
    txt = _doc(body="## 正文\n\n重要结论 **加粗** 与 `code` 都要在。\n")
    fixed = da.normalize_banner(txt, known_doc, "2026-10-03")
    assert "重要结论 **加粗** 与 `code` 都要在。" in fixed


def test_readme_banner_is_fixed_text(monkeypatch):
    """无日期的 README 固定块:逐字一致就不动。"""
    rel = "../README.md"
    assert rel in da.README_BANNER
    txt = "# Provenance\n\n" + da.README_BANNER[rel] + "\n\n## 正文\n"
    assert da.needs_banner(txt, rel) is False
    assert da.needs_banner("# README\n\n什么都没有\n", rel) is True


def test_check_mode_is_readonly_and_clean():
    """集成:`--check` 在真实仓库上必须退出码 0(全部规范)且**不改任何文件**。

    这是"体检永不改工作树"的可执行断言 —— deep_check 曾因实跑 --banner 一次改脏
    64 份 md。跑前跑后各取一次 git 状态快照,有新增差异就是它写盘了。
    """
    def git_status():
        p = subprocess.run(["git", "status", "--porcelain"], cwd=_REPO,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        return set(p.stdout.splitlines())

    before = git_status()
    p = subprocess.run([_PY, "-X", "utf8", _TOOL, "--check"], cwd=_PKG,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    after = git_status()
    assert p.returncode == 0, (
        "doc_audit --check 报有待规范化的 md(跑 --banner 修复):\n" + p.stdout[-2000:])
    assert after == before, (
        "--check 是只读的,却改了工作树:\n新增差异: {}".format(
            sorted(after - before)[:10]))
    assert "待更新 0 个" in p.stdout


def test_index_content_dates_come_from_git_not_mtime():
    """索引的「内容日期」必须是**最后一次提交日**,不是 mtime(GTC 体检 N6)。

    历史缺陷:git log 默认 `core.quotepath=true`,非 ASCII 路径被转义成八进制
    并加外层引号 ⇒ **所有中文文档都匹配不上**,`cdate()` 静默回退 `os.stat().st_mtime`,
    把"这页纸多久没核对过"的信号抹掉(`GTC研究边界声明` 因此显示 10-05 而实际 10-03)。
    修法:git 加 `-c core.quotepath=false`、字典 `setdefault` 只留最新一次、
    匹配失败返回 `未提交` 而不用 mtime 冒充。

    断言:索引里出现的日期,要么是 `未提交`,要么能在 `git log -1` 里找到同值。

    ⚠ `--index` 是**生成物**命令,会写 `docs/文档索引.md`。本测试必须跑在真实
    仓库(`cwd=_PKG`)上——`cdate()` 依赖 git 历史,搬进 tmp 就测不到"提交日 vs mtime"
    这个区别了。代价是**跑一次测试就弄脏工作树**(行序会被重排)。所以前后各存档一次,
    无论测试成败都还原,不给"跑测试顺手改脏仓库"留口子(2026-10-05 实测踩到:
    `pytest tests` 跑完索引 mtime 变 19:26,一度以为 `--check` 破坏了只读契约)。
    """
    idx = os.path.join(_PKG, "docs", "文档索引.md")
    before = _read_bytes(idx) if os.path.isfile(idx) else None
    try:
        _assert_index_dates_match_git(idx)
    finally:
        after = _read_bytes(idx) if os.path.isfile(idx) else None
        if before is not None and after != before:
            _write_bytes(idx, before)


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _write_bytes(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def _assert_index_dates_match_git(idx):
    p = subprocess.run([_PY, "-X", "utf8", _TOOL, "--index"], cwd=_PKG,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-1500:]
    assert os.path.isfile(idx)
    with io.open(idx, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    checked = 0
    bad = []
    repo_root = da.git_repo_root(_PKG)
    for ln in lines:
        if not ln.startswith("| `") or ".md`" not in ln:
            continue
        parts = [x.strip() for x in ln.strip("|").split("|")]
        if len(parts) < 2:
            continue
        rel = parts[0].strip("`")
        date = parts[1] if not parts[1].isdigit() else parts[2]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            continue        # 状态列在前(第二节),或"未提交"
        # 索引里的 rel 是**相对 provenance/**(here)的,可能带 `../` ——
        # 换成仓库根相对路径再问 git(否则路径错、问出来的也不对)
        abs_path = os.path.normpath(os.path.join(_PKG, rel))
        rel_to_root = os.path.relpath(abs_path, repo_root).replace("\\", "/")
        q = subprocess.run(["git", "log", "-1", "--format=%ad", "--date=short",
                            "--", rel_to_root], cwd=repo_root, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        real = (q.stdout or "").strip()
        checked += 1
        if real and real != date:
            bad.append((rel, date, real))

    assert checked > 20, "索引里可核对的日期太少({}),测试没真正生效".format(checked)
    assert not bad, "索引日期与 git 提交日不符(mtime 冒充):\n" + "\n".join(
        "  {} 索引={} git={}".format(*b) for b in bad[:10])


def test_git_last_dates_handles_nonascii_paths():
    """`git_last_dates` 必须能匹配中文路径(quotepath 关掉)。

    这是上一条的单元级钉死:若有人把 `-c core.quotepath=false` 去掉,本测试立刻红。
    """
    dates = da.git_last_dates(_PKG)
    nonascii = [k for k in dates if any(ord(c) > 127 for c in k)]
    assert nonascii, (
        "git_last_dates 一个中文路径都没返回 —— quotepath 转义回归了(去掉 "
        "-c core.quotepath=false 就会这样)")
