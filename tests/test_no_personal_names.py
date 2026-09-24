# -*- coding: utf-8 -*-
r"""命名规范守卫:项目文档里不得出现个人称呼(统一用角色表述)。

规则(2026-09-21 起):文档与提交信息一律不写个人姓名/称呼,改用角色词 ——
平台侧 / 需求方 / 实现侧 / 研究侧。禁用词在下面 FORBIDDEN 里**以转义形式书写**:
这样本文件自身不含字面姓名,连"清扫脚本把守卫自己的词表也替换掉"这种事故也不会再发生
(2026-09-21 真实发生过一次:清扫把词表换成了替换词,守卫于是把"平台侧"当成违规词)。

`.githooks/commit-msg` 是同一规则对**提交信息**的守卫(需 `git config core.hooksPath .githooks`)。
"""
import io
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "provenance")
_MAVIS = os.path.join(os.path.dirname(_REPO), "mavis")


def _u(*codes):
    return tuple(c.encode("ascii").decode("unicode_escape") for c in codes)


# 含:平台侧相关的人名 / 需求方称呼 / 实现侧实现者 / 平台英文名 / 协作方 / 研究侧署名
FORBIDDEN = _u(r"\u4edd\u7267", r"\u9648\u603b", r"\u5b66\u59d0") + \
    ("Leo", "luohaozhe", "Tongmu", "tongmu", "ZZR", "Ruige")

SKIP_DIRS = {".git", "node_modules", "__pycache__", "build", ".pytest_cache", ".uv-cache",
             "_pages", ".venv", ".venv-live", "runs_html", ".trae-html-share-packages"}


def _docs(root):
    if not os.path.isdir(root):
        return
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith((".md", ".html", ".txt")):
                yield os.path.join(dp, f)


def test_no_personal_names_in_docs():
    bad = []
    for root in (_PKG, _MAVIS):
        for p in _docs(root):
            try:
                lines = io.open(p, encoding="utf-8", errors="replace").read().split("\n")
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                for w in FORBIDDEN:
                    if w in line:
                        bad.append("{}:{}: {}".format(
                            os.path.relpath(p, _REPO).replace("\\", "/"), i, w))
    assert not bad, "文档里出现个人称呼(请改为角色表述):\n" + "\n".join(bad[:20])


def test_commit_msg_hook_exists_in_both_repos():
    """提交信息守卫要真的在(否则"提交信息也不许带名字"只是口头约定)。"""
    for root in (_REPO, _MAVIS):
        hook = os.path.join(root, ".githooks", "commit-msg")
        assert os.path.isfile(hook), "缺提交信息钩子: {}".format(hook)
        txt = io.open(hook, encoding="utf-8", errors="replace").read()
        for w in FORBIDDEN:
            assert w in txt, "钩子里没有禁用词 {}".format(w)
