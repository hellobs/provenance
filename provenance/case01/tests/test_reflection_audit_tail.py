# -*- coding: utf-8 -*-
"""`reflection_audit`(反思体检 / 客套话清理)守卫(2026-10-04)。

"疑似截断 N 条"是 max_tokens 口径在文件侧唯一的证据来源(生产记录的
`manifest.manifest_warnings` 一直没接上,见批次 261004-123726-h120 报告 §八),
所以这个工具的判断规则和退出码都要能扛住:

1. 结尾判据不许把"带落款的完整反思"误报成截断(2026-09-19 实测踩过 B-1618),
   也不许把半句话放过去;
2. **退出码与 --clean 无关**。原先写的是 `1 if (n_trunc and not args.clean)`,
   于是带 `--clean` 跑完、即使还挂着疑似截断也给绿光 —— 当门禁用就等于把问题
   咽下去;
3. 一个读不了的 run.json 不许带走整轮体检,但必须以"读不了"出现并计入退出码,
   不能静默少一条。
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.tools import reflection_audit as ra  # noqa: E402


def _write_run(runs_dir, run_id, text, raw=None):
    d = os.path.join(str(runs_dir), run_id)
    os.makedirs(d)
    p = os.path.join(d, "run.json")
    if raw is not None:
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(raw)
        return p
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump({"branch": "B", "reflection": {"text": text}}, f, ensure_ascii=False)
    return p


BODY = "本次咨询中我在信息不足时给出了方向性判断,应当先核对公告原文再表态。" \
       "后续改进:对未证实的传闻一律标注证据等级。"


# ---------------------------------------------------------------------------
# 1. 结尾判据(报告里"疑似截断"就靠它)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,complete", [
    (BODY, True),                                              # 正常句末标点
    (BODY + "\n—— 人工智能投资助手,2026年9月15日", True),        # 落款短行(实测误报过)
    ("我给出的最终建议是否需要?", True),                         # 完整反问句
    (BODY + "\n*以上为本次反思的全部维度*", True),                 # 结语标记短行(正文末句仍在)
    ("*以上为本次反思的全部维度*", False),                        # 只剩结语:没有末句可判,不算完整
    ("因此我建议先观望,等待订单落地后的", False),                  # 半句话,没有句末标点
    ("改进方向:\n- 核对公告原文\n- 对未证实传闻标注证据等级", False),  # 列表被切断:短行pop光≠完整
    ("结论如下:A|风险|B|后续跟踪|C|订单", False),                 # 表格被切断(末段没收尾)
    ("", True),                                               # 空文本不算截断,交给字数说话
])
def test_ends_complete_rules(text, complete):
    assert ra._ends_complete(text) is complete, text[-24:]


def test_missing_reflection_is_not_invented_as_zero_length_problem():
    """九块里反思整块缺失:体检要给"读不到",不许崩。"""
    info = ra.audit_one({"branch": "B"})
    assert info["chars"] == 0 and info["truncated"] is False
    info2 = ra.audit_one({"reflection": {"text": None}})
    assert info2["chars"] == 0 and info2["changed"] is False


def test_boilerplate_opener_is_detected_and_cleanable():
    text = "当然可以。以下是我对这次完整咨询过程的系统性反思与深度剖析:\n" + BODY
    info = ra.audit_one({"reflection": {"text": text}})
    assert info["has_opener"] and info["changed"]
    assert info["clean_chars"] < info["chars"]
    assert ra._strip_boilerplate(text).startswith("本次咨询中")


def test_plain_reflection_is_not_touched():
    info = ra.audit_one({"reflection": {"text": BODY}})
    assert not info["has_opener"] and not info["changed"] and not info["truncated"]


# ---------------------------------------------------------------------------
# 2. 退出码与坏文件
# ---------------------------------------------------------------------------
def test_exit_code_is_red_for_truncation_even_with_clean(tmp_path, capsys):
    """--clean 只处理客套话,它没资格把"疑似截断"洗成绿灯。"""
    _write_run(tmp_path, "r-trunc", "因此我建议先观望,等待订单落地后的")
    assert ra.main(["--runs-dir", str(tmp_path)]) == 1
    capsys.readouterr()
    assert ra.main(["--runs-dir", str(tmp_path), "--clean"]) == 1, \
        "带 --clean 也给红,否则门禁会以为清理=修好了"
    assert "疑似截断 1" in capsys.readouterr().out


def test_unreadable_run_does_not_abort_the_audit(tmp_path, capsys):
    """坏文件:整轮继续跑完,但它要显式出现并计入退出码(不静默少一条)。"""
    _write_run(tmp_path, "r-ok", BODY)
    p = _write_run(tmp_path, "r-bad", None, raw="{ 半截的 JSON")
    _write_run(tmp_path, "r-list", None, raw="[1, 2, 3]")
    assert ra.main(["--runs-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "r-ok" in out and "读不了" in out and "读不了 2" in out
    assert os.path.exists(p), "体检模式绝不写盘"


def test_clean_without_write_leaves_files_untouched(tmp_path, capsys):
    p = _write_run(tmp_path, "r-1", "当然可以。以下是我的反思:\n" + BODY)
    before = io.open(p, encoding="utf-8").read()
    assert ra.main(["--runs-dir", str(tmp_path), "--clean"]) == 0
    assert io.open(p, encoding="utf-8").read() == before, "没有 --write 就不许动文件"
    assert "未写盘" in capsys.readouterr().out


def test_clean_write_strips_the_opener_and_marks_it(tmp_path):
    p = _write_run(tmp_path, "r-1", "当然可以。以下是我的反思:\n" + BODY)
    assert ra.main(["--runs-dir", str(tmp_path), "--clean", "--write"]) == 0
    rec = json.load(io.open(p, encoding="utf-8"))
    assert rec["reflection"]["text"].startswith("本次咨询中")
    assert rec["reflection"]["stripped_opener"] is True


def test_healthy_batch_is_green_and_empty_dir_is_not(tmp_path, capsys):
    assert ra.main(["--runs-dir", str(tmp_path)]) == 1, "没有样本不等于全部合格"
    _write_run(tmp_path, "r-1", BODY)
    assert ra.main(["--runs-dir", str(tmp_path)]) == 0
    assert "疑似截断 0" in capsys.readouterr().out
