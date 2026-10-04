# -*- coding: utf-8 -*-
"""`consistency_report`(评审前自查 + 门禁)判定口径守卫(2026-10-04)。

这个工具是交付前最后一道自查,而且退出码要当门禁用。它原先**无视记录上盖的
一致性戳**,每条都现算关键词快筛 —— 立场判官上线后这就变成两套口径:

批次 261004-123726-h120 实测 39 条里 **13 条与戳不符**:
- -016 / -025 / -039 判官盖的是 `inconsistent`(正是本批要交给专家复核的
  分歧信号),现算快筛却说 `consistent` —— **自查工具会把要紧的样本藏起来**;
- 另有 11 条判官判了 `consistent`、快筛判不了 → 门禁在一批合格记录上无故亮红。

同一个错在 `backfill_consistency` 已经修过(不许用快筛覆盖立场判官戳)。
这里钉住:有戳读戳、没戳才现算兜底,并且**每条都把口径印出来** ——
混合口径不说明,读者就会把"判不了"当成判官没生效。
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.consistency import quick_scan  # noqa: E402
from case01.tools import consistency_report as cr  # noqa: E402


def _write_run(runs_dir, run_id, record):
    d = os.path.join(str(runs_dir), run_id)
    os.makedirs(d)
    with io.open(os.path.join(d, "run.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
    return d


def _rec(branch, ai_text, stamp=None, held=0.0):
    rec = {"branch": branch, "start_date": "2026-08-27",
           "turns": [{"speaker": "ethan", "date": "2026-08-27", "text": "q"},
                     {"speaker": "ai", "date": "2026-08-27", "text": ai_text}],
           "state_history": [{"date": "2026-09-30", "state": {"held_fraction": held}}]}
    if stamp is not None:
        rec["consistency"] = stamp
    return rec


# 快筛看不出的中立陈述(立场判官能判、关键词只能给 unknown)
BLURRY = "Q3 revenue was 12 percent higher than the previous quarter."
# 快筛看得出"谨慎"(与 B 一致)的文本
CAUTIOUS = "I would not recommend buying now; wait for official confirmation."


def test_blurry_text_really_is_unknown_for_the_keyword_scan():
    """控制组:现算快筛对 BLURRY 只能给 unknown,否则下面的守卫没有意义。"""
    assert quick_scan(_rec("B", BLURRY))[0] == "unknown"
    assert quick_scan(_rec("B", CAUTIOUS))[0] == "consistent"


def test_stamped_verdict_wins_over_recomputed_scan(tmp_path):
    """记录盖了 llm_stance 戳时,自查必须报戳里的判定,而不是重算的快筛。"""
    _write_run(tmp_path, "r-001", _rec("B", BLURRY, stamp={
        "verdict": "consistent", "method": "llm_stance", "reason": "立场=wait"}))
    rows = cr.collect(str(tmp_path))
    assert len(rows) == 1
    run_id, branch, verdict, sig, held, reason, method = rows[0]
    assert verdict == "consistent", "判官判了 consistent,工具不许报 unknown"
    assert method == "llm_stance" and "立场=wait" in reason
    assert held == 0.0, "末日持仓要取到数值,不能把整个 state 字典端出来"
    assert sig == (0, 0, 0) or isinstance(sig, tuple), "三元组仍是现算的输入侧证据"


def test_judge_disagreement_is_never_polished_into_agreement(tmp_path):
    """核心红线:判官说不一致,快筛说一致 —— 自查必须把这条列成"不一致"。"""
    _write_run(tmp_path, "r-dis", _rec("B", CAUTIOUS, stamp={
        "verdict": "inconsistent", "method": "llm_stance",
        "reason": "立场=buy_now 与 B 线相反"}))
    rows = cr.collect(str(tmp_path))
    assert rows[0][2] == "inconsistent"
    rc = cr.main(["--runs-dir", str(tmp_path)])
    assert rc == 1, "有分歧样本时门禁必须亮红,而不是替它解释过去"


def test_unstamped_record_falls_back_to_scan_and_says_so(tmp_path):
    """旧记录没有戳:可以现算,但口径那一栏必须写明是现算兜底。"""
    _write_run(tmp_path, "r-legacy", _rec("B", CAUTIOUS))
    rows = cr.collect(str(tmp_path))
    assert rows[0][2] == "consistent"
    assert "现算" in rows[0][6] and "quick_scan" in rows[0][6]


def test_flat_string_stamp_is_also_eaten(tmp_path):
    """台账式的扁平戳(只有字符串)也要能读,不许 AttributeError。"""
    _write_run(tmp_path, "r-flat", _rec("B", BLURRY, stamp="inconsistent"))
    rows = cr.collect(str(tmp_path))
    assert rows[0][2] == "inconsistent"
    assert "未记" in rows[0][6], "口径要说明这条只有字符串戳、没记 method"


def test_empty_stamp_falls_back_instead_of_printing_blank(tmp_path):
    """戳存在但 verdict 为空串:算"没盖",走现算并标注,不许印一个空判定。"""
    _write_run(tmp_path, "r-empty", _rec("B", CAUTIOUS, stamp={"verdict": "",
                                                                "method": "quick_scan"}))
    rows = cr.collect(str(tmp_path))
    assert rows[0][2] == "consistent"
    assert rows[0][6].startswith("现算")


def test_unreadable_run_is_reported_not_skipped(tmp_path):
    """坏文件不许静默消失:它要以"判不了"出现,并且标出是读失败。"""
    d = _write_run(tmp_path, "r-broken", _rec("B", CAUTIOUS))
    with io.open(os.path.join(d, "run.json"), "w", encoding="utf-8") as f:
        f.write("{ 不是 JSON")
    rows = cr.collect(str(tmp_path))
    assert len(rows) == 1 and rows[0][2] == "unknown"
    assert "读不了" in rows[0][5] and "读失败" in rows[0][6]
    assert cr.main(["--runs-dir", str(tmp_path)]) == 1


def test_printed_table_shows_the_population_and_the_mix(tmp_path, capsys):
    """汇总表要报"几条读戳、几条现算",混合口径不说明就是误导。"""
    _write_run(tmp_path, "r-1", _rec("B", BLURRY, stamp={
        "verdict": "consistent", "method": "llm_stance", "reason": "立场=wait"}))
    _write_run(tmp_path, "r-2", _rec("B", CAUTIOUS))
    assert cr.main(["--runs-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "合计 2 条:一致 2 / 不一致 0 / 判不了 0" in out
    assert "1 条读记录自带戳,1 条无戳现算 quick_scan 兜底" in out
    assert "判定口径" in out


def test_only_bad_hides_nothing_when_a_stamp_disagrees(tmp_path, capsys):
    """`--only-bad` 过滤的是戳里的判定,不是重算结果。"""
    _write_run(tmp_path, "r-ok", _rec("B", BLURRY, stamp={
        "verdict": "consistent", "method": "llm_stance", "reason": "x"}))
    _write_run(tmp_path, "r-bad", _rec("A", BLURRY, stamp={
        "verdict": "unknown", "method": "llm_stance", "reason": "判不了"}))
    assert cr.main(["--runs-dir", str(tmp_path), "--only-bad"]) == 1
    out = capsys.readouterr().out
    assert "r-bad" in out and "r-ok" not in out.split("合计")[0]


def test_no_records_is_an_error_not_a_green_light(tmp_path):
    """空目录:门禁不许给 0(没有样本不等于全部一致)。"""
    assert cr.main(["--runs-dir", str(tmp_path)]) == 1
