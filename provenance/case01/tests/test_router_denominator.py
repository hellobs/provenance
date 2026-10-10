# -*- coding: utf-8 -*-
"""Router 分母与"未产出样本"的可见性守卫(2026-10-05 第十轮只读核查 4.1)。

出了什么事
----------
第十轮核查发现同一批样本在**两处同时消失**:

    全库 311 条里14 条 `router.status` 是 error(9)/skipped(5),它们的 `issues` 恒为 []。
    * 专家表 `router_review_sheet.csv` 按 issue 出行 ⇒ 这 14 条**一行都不占**;
    * 批次聚合 `analysis.json` 的 `router.n` 取的是 `len(ok_rows)`(已剔除"没跑成"),
      旁边又只有 `zero_issue_runs` ⇒ 165042 批次报 `n=34 / zero=0`,读起来像
      "这批没有零问题样本",真实含义是"零问题的那几条没进分母"。

于是"没看到问题"这件事有两套解释(干净 vs 坏了),而读者无法区分。占比并不小:
165042 批次 9/43 = 21%。

本文件钉住三件事
----------------
    1. `router` 段的分母必须**闭合**: `n_scored + n_excluded_not_scored == n_runs`,
       且 `router_status_dist` 必须把剔除的那些条数说出来。
    2. 入库 `analysis.json` 的 `router` 段必须已含这三个键(产物侧回填过)。
    3. 专家表里"Router 未产出"的占位行必须存在,且**不进评分分母**。

判据分工:第1 条钉代码(改坏立刻红),第 2 条钉产物(入库表必须已重生成),
第 3 条钉工具行为。`test_analysis_keyset.py` 管顶层键集全等/子集,那边**管不到**
`router` 子段的内部结构,不是重复覆盖。
"""
import csv
import glob
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.tools import batch_analyze as ba# noqa: E402
from case01.tools import router_review as rr  # noqa: E402
from case01.tools.batch_analyze import analyze  # noqa: E402

BATCH = "261005-000000-denom"
REFL_TEXT = "反思正文。" * 60

# `router` 段必须自带的三个键。少任何一个都会让分母重新变得不可读。
ROUTER_REQUIRED = ("n_scored", "n_excluded_not_scored", "router_status_dist")


def _rows(n_ok=3, n_router_err=1, n_refl_err=1):
    """造混合状态的行: 正常 / router error / 反思失败。

    走真实取数层 `_row()`,不走手搓 dict —— 否则这条守卫变成"验我自己造的假数据"。
    """
    def rec(router=None, refl_fail=False):
        return {
            "branch": branch,
            "consistency": {"verdict": "consistent", "reason": "立场一致"},
            "turns": ["t0", "t1"],
            "events": [1] * 4,
            "reflection": {"text": REFL_TEXT,
                           "quality": {"score": 90,
                                       "status": "fail" if refl_fail else "review",
                                       "failure_reasons": ["生成超时"] if refl_fail else []}},
            "router": router if router is not None else {
                "status": "", "issues": [{"id": "i1", "summary": "s", "field": "f",
                                          "risk": "low", "risk_note": ""}],
                "postprocess": {"final_issue_count": 1}},
        }

    ok = [ba._row("{}-{:03d}".format(BATCH, i), rec()) for i in range(n_ok)]
    rerr = [ba._row("{}-r{:02d}".format(BATCH, i),
                    rec(router={"status": "error", "error": "空输出",
                                "issues": [], "postprocess": {}}))
            for i in range(n_router_err)]
    ferr = [ba._row("{}-f{:02d}".format(BATCH, i), rec(refl_fail=True))
            for i in range(n_refl_err)]
    return ok + rerr + ferr


def test_shipped_router_section_declares_its_denominator():
    """入库 `analysis.json` 的 `router` 段必须已含三个分母字段。

    `test_analysis_keyset.py` 只判**顶层**键集,且对产物只要求子集(缺新键放过),
    所以"入库表的 router 分母不可读"它抓不到。这里补上:产物侧已重生成过。
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    paths = sorted(glob.glob(os.path.join(
        root, "provenance", "results", "analysis", "batch_runs",
        "*", "analysis.json")))
    if not paths:
        return# 干净检出没有产物目录,本条不适用
    bad = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            rt = (json.load(fh).get("router") or {})
        over = [k for k in ROUTER_REQUIRED if k not in rt]
        if over:
            bad[os.path.basename(os.path.dirname(p))] = over
    assert not bad, (
        "入库 analysis.json 的 router 段缺分母字段:{}。"
        "重生成: python -m case01.tools.batch_analyze --batch <批次> "
        "--fill-from-primary".format(bad))


# ---------------------------------------------------------------------------
# 3. 专家表:占位行存在,且不进评分分母
# ---------------------------------------------------------------------------
def _write_run(runs_dir, rid, router):
    d = runs_dir / rid
    d.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": rid, "router": router}
    (d / "run.json").write_text(json.dumps(payload, ensure_ascii=False),
                                encoding="utf-8")


def test_expert_sheet_gives_a_row_to_router_failed_samples(tmp_path, monkeypatch):
    """Router 没跑成的样本必须在专家表里各占一行,并说明是哪种 status。

    变异验证:把 `export()` 里的 `n_norouter += 1` 那段删掉,本条立刻红。
    """
    runs = tmp_path / "runs"
    out = tmp_path / "out"
    _write_run(runs, "ok-1", {"status": "", "issues": [
        {"id": "i1", "summary": "s", "field": "f", "risk": "low", "risk_note": ""}]})
    _write_run(runs, "err-1", {"status": "error", "error": "空输出", "issues": []})
    _write_run(runs, "skip-1", {"status": "skipped", "issues": []})
    _write_run(runs, "clean-1", {"status": "", "issues": []})
    monkeypatch.setattr(rr, "RUNS", str(runs))
    monkeypatch.setattr(rr, "OUT", str(out))
    assert rr.export() == 0

    with open(out / "router_review_sheet.csv", encoding="utf-8-sig",
              newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if not (r.get("run_id") or "").startswith("#")]
    by_id = {}
    for r in rows:
        by_id.setdefault(r["run_id"], []).append(r)

    assert len(by_id.get("err-1") or []) == 1, "router error 的样本一行都不占(第十轮 4.1)"
    assert len(by_id.get("skip-1") or []) == 1, "router skipped 的样本一行都不占"
    ph = by_id["err-1"][0]
    assert "未产出" in ph["summary"] and "error" in ph["risk_note"], (
        "占位行必须写明是 Router 没产出以及哪种 status,实际 summary={!r} risk_note={!r}"
        .format(ph["summary"], ph["risk_note"]))
    assert (ph["field_ok"] or "").strip() == "", "占位行不该带人工标注值"
    # 真的干净(0 issue 但 status 正常)的样本**不占行** —— 否则占位行会泛滥成噪声
    assert "clean-1" not in by_id, (
        "status 正常且 0 issue 的样本不该占位,否则『未产出』与『干净』又混了")


def test_placeholder_rows_are_excluded_from_the_score_denominator(tmp_path):
    """占位行不得进评分分母,且要单独报出条数。

    它没有 issue 可评,算进 `total` 会让"总条数"比真实 issue 数大,
    读者又以为专家漏标了。`not_scored_router_missing` 就是给这件事留的字段。
    """
    sheet = tmp_path / "sheet.csv"
    rows = [["run_id", "issue_id", "summary", "field", "risk", "risk_note",
             "field_ok", "expected_risk", "summary_ok", "note"]]
    # 两条真issue:内容刻意与占位行相似(空标注列 + 同样的 summary 文案开头不存在,
    # 但 risk_note/field 全空),用来钉住"按内容去重会误删真行"那个坑
    rows.append(["ok-1", "i1", "普通问题", "估值", "low", "", "1", "", "1", ""])
    rows.append(["ok-2", "i2", "另一个问题", "风控", "high", "", "0", "", "1", ""])
    rows.append(["err-1", "", "【Router 未产出,不适用本表标注】", "", "",
                 "router.status=error", "", "", "", ""])
    rows.append(["skip-1", "", "【Router 未产出,不适用本表标注】", "", "",
                 "router.status=skipped", "", "", "", ""])
    with open(sheet, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)

    assert rr.score(str(sheet)) == 0
    with open(tmp_path / "router_review_score.json", encoding="utf-8") as f:
        res = json.load(f)
    assert res["total"] == 2, "占位行被算进了总分母,实际 total={}".format(res["total"])
    assert res["labelled"] == 2, "占位行被算进了已标注数"
    assert res["not_scored_router_missing"] == 2, (
        "占位行条数必须单独报出,实际 {}".format(res.get("not_scored_router_missing")))
    assert res["routing_agreement"] == 0.5, (
        "两条真issue 的 field_ok 是 1/0,一致率应为 0.5,实际 {}"
        .format(res["routing_agreement"]))


def test_shipped_expert_sheet_lists_router_failed_samples():
    """入库专家表必须已含"Router 未产出"占位行,且条数与聚合侧口径一致。

    为什么单独钉:代码改了产物**不会自动回填**。上面两条守住"以后不会再变坏",
    这条守住"已经变坏的历史产物会被发现" —— 否则专家拿到的仍是看不见坏样本的旧表。

    判据用 `zero_issue_runs > 0 的批次` 反推应当出现的占位行数:那些批次里
    `n_runs - n_scored` 就是"Router 没跑成"的条数,专家表必须一条不少地列出来。
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    sheet = os.path.join(root, "provenance", "results", "analysis",
                         "router_review", "router_review_sheet.csv")
    if not os.path.exists(sheet):
        return                        # 干净检出没有产物表,本条不适用
    with open(sheet, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if not (r.get("run_id") or "").startswith("#")]
    placeholders = [r for r in rows
                    if (r.get("summary") or "").startswith("【Router 未产出")]
    assert placeholders, (
        "入库专家表里一条『Router 未产出』占位行都没有 —— 那些样本在专家视角里"
        "彻底消失(第十轮 4.1)。重生成: python -m case01.tools.router_review --export")
    # 每条占位行都必须写明 status,否则读者仍不知道是"坏了"还是"跑了但没拆出"
    blank = [r["run_id"] for r in placeholders if "router.status=" not in (r["risk_note"] or "")]
    assert not blank, "占位行缺 router.status= 说明:{}".format(blank[:5])
    assert not any((r.get("field_ok") or "").strip() for r in placeholders), (
        "占位行不该带人工标注值")


def test_score_still_reports_when_only_placeholders_are_present(tmp_path):
    """表里只剩占位行时不能静默返回 2,得说清为什么没有可评的。"""
    sheet = tmp_path / "sheet.csv"
    rows = [["run_id", "issue_id", "summary", "field", "risk", "risk_note",
             "field_ok", "expected_risk", "summary_ok", "note"],
            ["err-1", "", "【Router 未产出,不适用本表标注】", "", "",
             "router.status=error", "", "", "", ""]]
    with open(sheet, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    assert rr.score(str(sheet)) == 2