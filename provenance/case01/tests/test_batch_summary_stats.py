# -*- coding: utf-8 -*-
"""批次汇总数字的口径守卫(2026-10-04)。

`batch_run._summarize` 写 summary.json/md、`batch_analyze._md` 写 analysis.md,
这两个渲染器就是报告的最终出口。2026-10-03 的真实事故不是算错,而是**同一份文件
里出现两个分母**:质量均值摘掉了"没跑成"的 9 条、pass 率却仍按全量 43 条算,
报告自相矛盾(76.7% vs 真实 97.1%)。修完之后体检又打到同一族:
`耗时` 一节的均值/合计统计的是**台账全部行(含失败尝试)**,而结尾的口径说明
写的是"只统计成功读到 run.json 的记录" —— 说明盖不住数字,读者拿 44 行的
`seconds_mean` 去乘 43 条的有效样本,得到第三个数。

所以这里钉两件事:
1. **数字与文字同一总体**,总体不同就必须把两个数都写出来;
2. 渲染器不许在空批次/全失败批次上崩(那正是最需要它说话的时刻)。
"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.tools import batch_analyze as ba  # noqa: E402
from case01.tools import batch_run  # noqa: E402

ARGS = SimpleNamespace(model="qwen3:8b", timeline=None, lanes=3,
                       duration_min=120, disable_thinking=True,
                       python="python.exe")


def _row(run_id, ok=True, **kw):
    r = {"run_id": run_id, "ok": ok, "returncode": 0 if ok else 1,
         "attempt": 1 if ok else 2, "seconds": 500.0, "branch": "B",
         "consistency": "consistent", "turns": 4, "events": 13,
         "reflection_chars": 1800, "reflection_score": 90,
         "reflection_status": "review", "reflection_failure_reasons": [],
         "issues": 3, "issues_final": 3, "read_ok": ok,
         "model_requested": "qwen3:8b", "model_actual": "qwen3:8b"}
    r.update(kw)
    return r


def _ledger(tmp_path, *rows):
    d = tmp_path / "b1"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "ledger.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return str(d)


# ---------------------------------------------------------------------------
# 1. _summarize:分布用有效样本,耗时用全部台账行,而且必须各自说清楚
# ---------------------------------------------------------------------------
def test_summarize_keeps_failed_rows_out_of_distributions(tmp_path):
    out = _ledger(tmp_path,
                  _row("r-1"),
                  _row("r-2", branch="C"),
                  _row("r-3", ok=False, returncode=124, seconds=1200.0,
                       reflection_chars=0, reflection_score=None,
                       reflection_status="", read_ok=False, issues=0))
    s = batch_run._summarize(out, "b1", {"done": 3, "ok": 2, "failed": 1}, ARGS)
    assert s["counts"] == {"done": 3, "ok": 2, "failed": 1, "ledger_rows": 3}
    assert s["branches"] == {"B": 1, "C": 1}, "失败行不许进分支分布"
    assert s["reflection"]["n"] == 2
    assert s["reflection"]["chars_min"] == 1800, "失败行的 0 字不许拉低区间"
    assert sum(s["reflection"]["status_dist"].values()) == s["reflection"]["n"], \
        "质量门分布必须与均值同总体"
    assert s["router"]["issues_total"] == 6


def test_summarize_states_which_population_the_seconds_use(tmp_path):
    """耗时含失败尝试(失败也烧 GPU 时间),但这一点必须写在数字旁边。"""
    out = _ledger(tmp_path, _row("r-1", seconds=400.0), _row("r-2", seconds=600.0),
                  _row("r-3", ok=False, seconds=1200.0))
    s = batch_run._summarize(out, "b1", {"done": 3, "ok": 2, "failed": 1}, ARGS)
    assert s["seconds_total"] == 2200.0, "失败尝试的耗时该算进成本"
    assert s["seconds_mean"] == 733.3
    assert s["seconds_n"] == 3, "分母要落在文件里,读者不必猜它是不是那 2 条"
    md = open(os.path.join(out, "summary.md"), encoding="utf-8").read()
    assert "含失败尝试" in md and "台账全部 3 行" in md, md[-400:]
    assert "分支/反思/Router 三节只统计成功读到" in md, \
        "口径说明要分别说清两类总体,不能一句话盖掉耗时"


def test_summarize_survives_empty_and_all_failed_ledger(tmp_path):
    """空台账/全失败:不许崩、不许把 None 说成 0。"""
    out = _ledger(tmp_path)
    s = batch_run._summarize(out, "b1", {"done": 0, "ok": 0, "failed": 0}, ARGS)
    assert s["counts"]["ledger_rows"] == 0
    assert s["reflection"]["n"] == 0 and s["reflection"]["chars_mean"] is None
    assert s["seconds_mean"] is None and s["seconds_total"] == 0
    out2 = _ledger(tmp_path / "x", _row("r-1", ok=False, seconds=1200.0))
    s2 = batch_run._summarize(out2, "b2", {"done": 1, "ok": 0, "failed": 1}, ARGS)
    assert s2["reflection"]["n"] == 0 and s2["seconds_total"] == 1200.0
    assert open(os.path.join(out2, "summary.md"), encoding="utf-8").read()


def test_summarize_drops_unparseable_ledger_lines_without_counting_them(tmp_path):
    out = tmp_path / "b3"
    out.mkdir()
    with open(out / "ledger.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(_row("r-1"), ensure_ascii=False) + "\n")
        f.write("{ 坏行\n")
    s = batch_run._summarize(str(out), "b3", {"done": 1, "ok": 1, "failed": 0}, ARGS)
    assert s["counts"]["ledger_rows"] == 1, "坏行不能伪装成一条记录"


# ---------------------------------------------------------------------------
# 2. _md:散文里的分母必须等于 json 里的分母
# ---------------------------------------------------------------------------
def _rows_with_one_failure(n=10):
    rows = [ba._row("r-%02d" % i, {
        "branch": "B", "consistency": {"verdict": "consistent"},
        "turns": [1] * 4, "events": [1] * 13,
        "reflection": {"text": "字" * (2000 + i),
                       "quality": {"score": 95, "status": "pass",
                                   "failure_reasons": []}},
        "router": {"status": "", "issues": [1, 2], "postprocess": {}}})
        for i in range(n)]
    rows[0] = ba._row("r-00-failed", {
        "branch": "B", "consistency": {"verdict": "unknown"},
        "turns": [1] * 4, "events": [1] * 13,
        "reflection": {"text": "(反思生成失败)",
                       "quality": {"score": None, "status": "error",
                                   "error": "HTTP 500", "failure_reasons": []}},
        "router": {"status": "error", "error": "HTTP 500", "issues": [],
                   "postprocess": {}}})
    return rows


def test_md_pass_rate_uses_the_scored_population():
    """pass 率的分母 = 摘掉"没跑成"之后的条数,且两个数都要印在句子里。"""
    a = ba.analyze(_rows_with_one_failure(), 0, "t")
    md = ba._md(a)
    scored = a["excluded_failed"]["n_scored"]
    assert a["excluded_failed"]["n"] == 1 and scored == 9
    assert "（{}/{}，分母已摘除".format(scored, scored) in md, md[md.find("pass 率"):][:200]
    assert sum(a["reflection"]["status_dist"].values()) == scored


def test_md_lists_every_quarantined_record_with_a_reason():
    """摘出去的记录必须逐条有名有因(只给条数 = 读者无从复核)。"""
    a = ba.analyze(_rows_with_one_failure(), 0, "t")
    md = ba._md(a)
    assert "r-00-failed" in md and "HTTP 500" in md
    assert "已排除在上述均值之外" in md


def test_md_reports_zero_issue_share_over_statistic_participating_runs():
    r = ba.analyze(_rows_with_one_failure(), 0, "t")["router"]
    assert r["n"] == 9 and r["issues_total"] == 18
    assert r["zero_issue_runs"] == 0


def test_md_renders_empty_and_all_failed_batches_without_crashing():
    """空批次与全失败批次都要能出文件:分析器在最需要说话的时候不许炸。"""
    empty = ba._md(ba.analyze([], 0, "empty"))
    assert "有效记录:**0** 条" in empty
    allbad = ba.analyze([ba._row("x-%d" % i, {
        "branch": "", "consistency": {},
        "reflection": {"text": "(失败)",
                       "quality": {"score": None, "status": "error",
                                   "error": "timeout", "failure_reasons": ["超时"]}},
        "router": {"status": "skipped", "error": "上游没跑", "issues": [],
                   "postprocess": {}}}) for i in range(3)], 2, "allbad")
    assert allbad["excluded_failed"]["n"] == 3
    assert allbad["excluded_failed"]["n_scored"] == 0
    assert allbad["reflection"]["chars_mean"] is None
    md = ba._md(allbad)
    assert "分母为参与统计的 0 条" in md
    assert "⚠️ 反思/Router 未跑成的记录:3 条" in md
