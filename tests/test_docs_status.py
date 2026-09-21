# -*- coding: utf-8 -*-
"""文档状态守卫:每份 md 必须带状态块 / 索引必须覆盖全部文档。

2026-09-21:用户要求"过时的文件加上日期或做修改(尤其 md)"。光改一次会再烂回去,
所以把"文档必须自报状态"变成 CI 能红的东西:
- 本仓每份 .md 顶部都要有 `> **状态**:` / `> **最后核对**:`;
- `docs/文档索引.md` 存在,且每份 md 都在索引里出现过(不然新增文档没人知道它现行还是老的)。
"""
import io
import os
import re
import subprocess
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_this_dir)                    # D:\zzr\provenance
_PKG = os.path.join(_REPO, "provenance")

SKIP_DIRS = (".git", "node_modules", "__pycache__", ".venv", ".venv-live", "_shared",
             ".pytest_cache", ".uv-cache", ".audit-tmp", "results", "runs",
             "runs_injector", "runs_html")
BANNER = re.compile(r"^>\s*\*\*状态\*\*", re.M)


def _md_files():
    out = []
    for dirpath, dirs, files in os.walk(_PKG):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith("runs_archive")]
        for fn in files:
            if fn.lower().endswith(".md"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def test_every_md_declares_its_status():
    missing = []
    for p in _md_files():
        txt = io.open(p, encoding="utf-8", errors="replace").read(1500)
        if not BANNER.search(txt):
            missing.append(os.path.relpath(p, _PKG).replace("\\", "/"))
    assert not missing, "这些 md 没有状态块(跑 python tools/doc_audit.py --banner):\n" + "\n".join(
        missing[:20])


def test_status_block_carries_a_date():
    bad = []
    for p in _md_files():
        txt = io.open(p, encoding="utf-8", errors="replace").read(1500)
        if not re.search(r"\*\*最后核对\*\*:\s*\d{4}-\d{2}-\d{2}", txt):
            bad.append(os.path.relpath(p, _PKG).replace("\\", "/"))
    assert not bad, "状态块缺「最后核对」日期:\n" + "\n".join(bad[:20])


def test_doc_index_lists_every_md():
    idx = os.path.join(_PKG, "docs", "文档索引.md")
    assert os.path.isfile(idx), "docs/文档索引.md 不存在(跑 tools/doc_audit.py --index)"
    text = io.open(idx, encoding="utf-8").read()
    missing = []
    for p in _md_files():
        rel = os.path.relpath(p, os.path.join(_PKG, "docs")).replace("\\", "/")
        rel_pkg = os.path.relpath(p, _PKG).replace("\\", "/")
        if rel_pkg == "docs/文档索引.md":
            continue
        # index 里的路径相对 docs/ 写(如 `../case01/README.md`)或相对仓库根,两种都认
        if rel_pkg not in text and rel not in text:
            missing.append(rel_pkg)
    assert not missing, "这些 md 没被文档索引收录:\n" + "\n".join(missing[:20])


def test_doc_index_flags_superseded_docs():
    """索引必须显式标出"已被取代/历史存档"这类状态 —— 光有清单没有状态等于没体检。"""
    text = io.open(os.path.join(_PKG, "docs", "文档索引.md"), encoding="utf-8").read()
    for token in ("现行", "已被取代", "历史存档", "维护约定"):
        assert token in text, token
