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
import re

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "provenance")
_MAVIS = os.path.join(os.path.dirname(_REPO), "mavis")


def _u(*codes):
    return tuple(c.encode("ascii").decode("unicode_escape") for c in codes)


# 含:平台侧相关的人名 / 需求方称呼 / 实现侧实现者 / 平台英文名 / 协作方 / 研究侧署名
FORBIDDEN = _u(r"\u4edd\u7267", r"\u9648\u603b", r"\u5b66\u59d0") + \
    ("Leo", "luohaozhe", "Tongmu", "tongmu", "ZZR", "Ruige")

# ⚠ 上面这份是**精确串**,并且被 `test_commit_msg_hook_exists_in_both_repos` 逐词断言要在
# 两个仓的 `.githooks/commit-msg` 里出现 —— 往这里加词等于要同步改钩子(含 mavis 那个仓),
# 所以新增的判据一律进下面这份"只由本测试执行"的表,钩子那侧保持原样。
#
# 补漏的实测理由(2026-10-08 用户指出文档里仍出现真实人名):
# - 精确串是**大小写敏感**的,于是文档里的 `TongMu`(M 大写)与 `leo/main`(全小写)正好躲过去,
#   守卫全绿而名字还在 ⇒ 拉丁名改**大小写不敏感 + 字母边界**匹配。
# - 原表只钉"仝牧"三个字,漏了"仝老师"这种"姓+称呼" ⇒ 加一条称呼模式。
# - 原表没有 `Lusi`(用户点名) ⇒ 进下面这份表。
# - **`ZZR` 偏偏不能大小写不敏感**:它是本机路径 `D:\zzr\...` 的三段字母,实测一放开就把
#   文档与批产物打出 **86** 处假违规(还有测试里的 `zzr_fake_engine_pkg_for_test`)。
#   噪音大到让人想关掉守卫的守卫等于没有守卫 ⇒ 这一个继续只认全大写。
FORBIDDEN_CI = ("Leo", "luohaozhe", "Tongmu", "Ruige", "Lusi")
SURNAME_TITLE = (r"仝\s*(?:老师|先生|女士|总)", r"(?:罗|卢)\s*(?:老师|总)")

SKIP_DIRS = {".git", "node_modules", "__pycache__", "build", ".pytest_cache", ".uv-cache",
             "_pages", ".venv", ".venv-live", "runs_html", ".trae-html-share-packages"}

# 机器路径里的 `zzr` 不是称呼:匹配前先把 `D:\zzr\` / `D:/zzr/` 这一段替掉。
# 只替这一种形状,不动其它内容 —— 免得把正文里真的缩写也洗掉。
_PATH_ZZR = re.compile(r"(?i)[\\/]zzr(?=[\\/])")


def _docs(root):
    if not os.path.isdir(root):
        return
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith((".md", ".html", ".txt", ".py", ".yaml", ".yml", ".json", ".cmd", ".ps1")):
                yield os.path.join(dp, f)


def _violations(line, skip_self=False):
    """返回这一行里的违规判据名(空列表=干净)。"""
    if skip_self:
        return []
    probe = _PATH_ZZR.sub("<path>", line)
    found = [w for w in FORBIDDEN if w in probe]
    found += ["ci:" + w for w in FORBIDDEN_CI
              if re.search(r"(?i)(?<![a-z])%s(?![a-z])" % re.escape(w), probe)]
    found += ["称呼:" + m.group(0) for p in SURNAME_TITLE for m in [re.search(p, probe)] if m]
    return found


def test_no_personal_names_in_docs():
    bad = []
    for root in (_PKG, _MAVIS):
        for p in _docs(root):
            # 守卫自己的词表不算违规(它必须写出这些串才能匹配),与钩子同一豁免
            self_file = os.path.abspath(p) in (os.path.abspath(__file__),)
            try:
                lines = io.open(p, encoding="utf-8", errors="replace").read().split("\n")
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                for v in _violations(line, skip_self=self_file):
                    bad.append("{}:{}: {}".format(
                        os.path.relpath(p, _REPO).replace("\\", "/"), i, v))
    assert not bad, "文档/代码注释里出现个人称呼(请改为角色表述:平台侧 / 需求方 / 实现侧 / 研究侧):\n" + \
        "\n".join(bad[:20])


def test_guard_itself_catches_case_variants():
    """把这次漏掉的那三种写法钉成断言 —— 否则"改好了"只存在于叙述里。

    必要性:上一版守卫对 `TongMu`/`leo` 全绿而文档里还有,正是因为没有反向测试。
    """
    assert _violations("对照会议 TongMu 的要求")            # 大小写变体
    assert _violations("问题 6：leo/main 的 /viewer 未合入")  # 全小写
    assert _violations("审核界面由平台侧(仝老师那边)重写")      # 姓+称呼
    assert _violations("署名 Lusi")                         # 新点名的名字
    # 反向:本机路径与全大写缩写各自的待遇要钉住
    assert _violations("cd D:\\zzr\\provenance\\provenance") == []
    assert _violations("fake = \"zzr_fake_engine_pkg_for_test\"") == []
    assert _violations("研究侧署名 ZZR")


def test_commit_msg_hook_exists_in_both_repos():
    """提交信息守卫要真的在(否则"提交信息也不许带名字"只是口头约定)。

    引擎仓那一半**按它在不在默认同级位置决定跑不跑**:`tools/setup_all.py` 明确允许
    `--engine-dir` 指到任意路径,把"恰好并列在同级"当成断言前提,会让换了目录布局的人
    撞一条与命名规范无关的红测,而报的内容还是"缺提交信息钩子"—— 把人往错方向带
    (2026-10-08 晚实测:同级没有引擎仓时这条就是 FAILED,而不是 skip)。
    本仓那一半照常硬校验,顺序在前,不会被跳过掩盖。
    """
    for root, why in ((_REPO, "本仓"), (_MAVIS, "引擎仓")):
        if why == "引擎仓" and not os.path.isdir(root):
            pytest.skip("引擎仓不在默认同级位置 {};它由 setup_all --engine-dir 决定,"
                        "本仓钩子已在上面校验过".format(root))
        hook = os.path.join(root, ".githooks", "commit-msg")
        assert os.path.isfile(hook), "缺{}提交信息钩子: {}".format(why, hook)
        txt = io.open(hook, encoding="utf-8", errors="replace").read()
        for w in FORBIDDEN:
            assert w in txt, "{}钩子里没有禁用词 {}".format(why, w)
