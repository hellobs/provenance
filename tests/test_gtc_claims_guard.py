# -*- coding: utf-8 -*-
r"""GTC 提交件口径守卫:主张必须能指出证据,无据比例不得回潮(2026-10-05 立)。

背景(2026-10-05 两轮体检):
- N1:边界声明主张"状态级内化(11/12 为正)",但真实干预的**对照维度集恒为空**,
  该比例只能读作"被干预的维度自身朝新约束方向位移",**读不出**"比未被干预的维度动得更多"。
  披露句必须留在文档里,否则读者会越读越满。
- 4.5:曾流传"后果归因 25/25、内化语言 15/25",但全仓检索**无对应标注表**,
  违反"每个主张都要指出证据"的自定原则,已降级为定性观察。本守卫防止它回潮。

禁用词与 N1 关键句在下面**以转义形式书写**:避免"清扫脚本把守卫自己的词表也替换掉"
(见 test_no_personal_names.py 头顶记的同款事故)。
"""
import io
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "provenance")
_DOCS = os.path.join(_PKG, "docs")

BOUNDARY = os.path.join(_DOCS, "GTC研究边界声明.md")
MANUAL = os.path.join(_DOCS, "核验主张与证据说明书.md")


def _u(*codes):
    return tuple(c.encode("ascii").decode("unicode_escape") for c in codes)


# 无据比例:出现即要求文中同时说明"已作废/无对应标注表",否则报红。
# 以转义书写,守卫自身不含字面违规串。
UNSOURCED_RATIOS = _u(
    r"25/25",
    r"15/25",
    r"\u8fd1\u4e4e\u5168\u90e8",   # 近乎全部
)

# N1 披露句的关键片段(至少出现其一,证明"对照臂为空"被写明)。
# 用较短的稳定词,避免措辞微调就红。
CONTROL_ARM_MARKERS = _u(
    r"\u5bf9\u7167\u81c2",          # 对照臂
    r"\u5bf9\u7167\u7ef4\u5ea6",    # 对照维度
)


def _read(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


def test_unsourced_ratios_are_not_stated_as_fact():
    """25/25、15/25、"近乎全部" 不得作为事实出现;若出现,同文件必须写明其已作废/无标注表。"""
    bad = []
    for path in (BOUNDARY, MANUAL):
        txt = _read(path)
        for w in UNSOURCED_RATIOS:
            if w not in txt:
                continue
            # 允许"已作废"式的历史说明,但必须紧邻否证词,不能光留着数字。
            for line in txt.split("\n"):
                if w in line and not any(k in line for k in _u(
                        r"\u5df2\u4f5c\u5e9f",       # 已作废
                        r"\u65e0\u5bf9\u5e94\u6807\u6ce8\u8868",  # 无对应标注表
                        r"\u4e0d\u5f97\u518d\u5f15\u7528",        # 不得再引用
                )):
                    bad.append("{}: {}".format(os.path.basename(path), line.strip()[:80]))
    assert not bad, "无据比例被当作事实陈述(应改为定性或标注已作废):\n" + "\n".join(bad)


def test_boundary_discloses_control_arm_is_empty():
    """N1 防回潮:边界声明必须写明"对照臂为空",否则 11/12 会被读成比对照强。"""
    txt = _read(BOUNDARY)
    assert any(m in txt for m in CONTROL_ARM_MARKERS), \
        "边界声明缺少「对照臂为空」的披露(读者会把 11/12 读成强于对照)"
    # 披露的实质内容:必须点出该比例只读作"被干预维度自身位移"。
    assert _u(r"\u81ea\u8eab\u7684\u4f4d\u79fb")[0] in txt or "自身位移" in txt, \
        "对照臂说明未点明 11/12 只读作「被干预维度自身位移」"


def test_manual_45_is_qualitative():
    """4.5 已降级:说明书写明定性观察,且不带"覆盖率"式主张。"""
    txt = _read(MANUAL)
    assert _u(r"\u5b9a\u6027\u89c2\u5bdf")[0] in txt, "说明书 4.5 未标为定性观察"
    # 明确"不主张覆盖率"
    assert _u(r"\u4e0d\u4e3b\u5f20\u8986\u76d6\u7387")[0] in txt, \
        "说明书 4.5 未声明「不主张覆盖率」"


def test_guard_actually_catches_violations(tmp_path):
    """守卫自测:构造违规文本必须被上面的规则判红,证明守卫不是空壳。"""
    # 1) 裸比例(无否证词) 命中
    viol = "后果归因 25/25、内化语言 15/25。\n"
    hits = [w for w in UNSOURCED_RATIOS if w in viol]
    assert hits, "构造的违规文本未被词表命中,守卫失效"
    # 2) 带「已作废」的比例 不命中否证检查
    ok = "此前流传的 25/25 已作废,不得再引用。\n"
    assert any(k in ok for k in _u(r"\u5df2\u4f5c\u5e9f", r"\u4e0d\u5f97\u518d\u5f15\u7528")), \
        "否证词未命中,守卫的放行逻辑失效"
    # 3) 对照臂标记必须能匹配到真实文档片段
    assert any(m in _read(BOUNDARY) for m in CONTROL_ARM_MARKERS)
