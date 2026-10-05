# -*- coding: utf-8 -*-
"""`batch_analyze` 取数层的契约守卫(2026-10-04,批次 261004-123726-h120 体检延伸)。

为什么要测这一层:每一份批次报告的数字都从这里出来,而它此前只有"手写 row
喂 analyze"的测试(test_failed_stage_contract),**取数本身没人管**。体检时
踩到的正是取数层的坑:

- run.json 是嵌套结构(`reflection.text` / `reflection.quality`),台账
  `ledger.jsonl` 是扁平摘要(`reflection_chars` / `reflection_score` /
  `reflection_status` / `reflection_failure_reasons`,见 `batch_run._digest`)。
  `_row` 原先只吃嵌套侧 → 台账自带的这四个字段**整段被丢**,只能靠回读
  run.json 补;
- 一旦 run.json 不在(清目录、换机分析、事后删档 —— 本仓已有 12 个 run 目录
  没有 run.json),补读失败是**静默**的,那条记录带着 `refl_chars=0`、
  `refl_status=""` 进均值,报出来变成"模型写了一篇 0 字反思",
  而不是"这条的明细读不到"。这和 2026-10-03 修掉的"占位文本当超短反思"
  是同一个错误的两个方向。

铁律还是一句话:**"没跑成/读不到"不许伪装成"跑出来但很差"。**
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.tools import batch_analyze as ba  # noqa: E402

RUN_ID = "batch-261004-000000-tmp-001"
BATCH = "261004-000000-tmp"

# run.json 里的嵌套原始记录(reflection 文本 1817 字)
REFL_TEXT = "反" * 1817
NESTED = {
    "branch": "B",
    "consistency": {"verdict": "consistent", "reason": "立场与分支一致"},
    "turns": ["t0", "t1", "t2", "t3"],
    "events": [1] * 13,
    "reflection": {"text": REFL_TEXT,
                   "quality": {"score": 90, "status": "review",
                               "failure_reasons": ["未提出可执行的后续改进动作"]}},
    "router": {"status": "", "issues": [1, 2, 3],
               "postprocess": {"final_issue_count": 3}},
}
# 同一批数据在台账里的扁平摘要(batch_run._digest 写出来的形状)
FLAT = {
    "run_id": RUN_ID, "ok": True, "returncode": 0, "attempt": 1,
    "seconds": 495.4, "timeline": "auto",
    "branch": "B", "consistency": "consistent", "turns": 4, "events": 13,
    "reflection_chars": 1817, "reflection_score": 90,
    "reflection_status": "review",
    "reflection_failure_reasons": ["未提出可执行的后续改进动作"],
    "issues": 3, "issues_final": 3, "read_ok": True,
    "model_requested": "qwen3:8b", "model_actual": "qwen3:8b",
}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """造一个隔离的 (台账目录, runs 目录),把模块级常量指过去。"""
    batch_dir = tmp_path / "batch_runs" / BATCH
    batch_dir.mkdir(parents=True)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    monkeypatch.setattr(ba, "BATCH_ROOT", str(tmp_path / "batch_runs"))
    monkeypatch.setattr(ba, "RUNS_DIR", str(runs_dir))
    return {"batch_dir": batch_dir, "runs_dir": runs_dir, "ledger": batch_dir / "ledger.jsonl"}


def _write_ledger(sandbox, *rows):
    with open(sandbox["ledger"], "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_run_json(sandbox, run_id, record):
    d = sandbox["runs_dir"] / run_id
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "run.json", "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 1. 同一条数据的两种形状必须读出同一行(取数层的"同口径")
# ---------------------------------------------------------------------------
def test_flat_ledger_row_reads_the_same_as_nested_run_json(sandbox):
    """台账扁平摘要 → 与 run.json 嵌套结构逐字段同值。

    这条在 2026-10-04 之前是**假**的:`_row` 只认嵌套侧,台账喂进来时
    `refl_chars/refl_score/refl_status/refl_fail_reasons` 全空,
    全靠回读 run.json 兜住。
    """
    nested = ba._row(RUN_ID, NESTED)
    flat = ba._row(RUN_ID, FLAT)
    for key in ("branch", "consistency", "refl_chars", "refl_score",
                "refl_status", "refl_fail_reasons", "issues", "issues_final",
                "turns", "events"):
        assert flat[key] == nested[key], "{}: 台账 {} vs run.json {}".format(
            key, flat[key], nested[key])
    assert flat["refl_chars"] == 1817 and not ba._is_failed(flat)


def test_row_still_eats_both_consistency_shapes(sandbox):
    """run.json 是 dict、台账是字符串,都要吃(2026-10-03 AttributeError 的守卫)。"""
    assert ba._row(RUN_ID, NESTED)["consistency"] == "consistent"
    assert ba._row(RUN_ID, {"consistency": "unknown"})["consistency"] == "unknown"
    assert ba._row(RUN_ID, {})["consistency"] == ""
    assert ba._row(RUN_ID, {"consistency": None})["consistency"] == ""
    assert ba._row(RUN_ID, {"consistency": 7})["consistency"] == ""


def test_ledger_and_scan_paths_agree_end_to_end(sandbox):
    """`--batch`(读台账)与 `--scan-runs`(读目录)必须给同一份 row。

    报告里"台账 39 条"和"目录扫出 39 条"要能互相对上;两条链口径分叉 =
    同一批数据两个结论。

    **唯一的例外是 `source`**(2026-10-05 第七轮 N4):台账行标 `"ledger"`、
    目录扫出来的行标 `"primary"` —— 这正是这条字段存在的意义:两者取数来源
    不同,台账独有字段(seconds/attempt/model_actual)只在台账侧为真。
    所以断言改成"除source 外全等",并顺带钉住这两个值别被写反
    (写反了下游会把补入的一手行当成台账权威)。
    """
    _write_ledger(sandbox, FLAT)
    _write_run_json(sandbox, RUN_ID, NESTED)
    from_ledger, bad1, _gap = ba._collect_from_ledger(BATCH)
    from_runs, bad2 = ba._collect_from_runs(prefix="batch-")
    assert bad1 == bad2 == 0
    assert len(from_ledger) == len(from_runs) == 1
    # N4:来源标记必须逐行带且取值正确
    assert from_ledger[0]["source"] == "ledger", \
        "台账行的 source 应为 ledger,实际 {}".format(from_ledger[0].get("source"))
    assert from_runs[0]["source"] == "primary", \
        "目录扫出的行应为 primary,实际 {}".format(from_runs[0].get("source"))
    # 除 source 外两条链必须完全同形
    lhs = {k: v for k, v in from_ledger[0].items() if k != "source"}
    rhs = {k: v for k, v in from_runs[0].items() if k != "source"}
    assert lhs == rhs, \
        "台账链与目录链口径分叉:\n{} vs\n{}".format(lhs, rhs)


# ---------------------------------------------------------------------------
# 2. 明细缺失不许伪装成"0 字反思"
# ---------------------------------------------------------------------------
def test_missing_run_json_still_carries_the_ledger_summary(sandbox):
    """run.json 不在时,台账自带的字数/质量分/status 必须照样进统计(而不是 0)。"""
    _write_ledger(sandbox, FLAT)                       # 故意不写 run.json
    rows, bad, _gap = ba._collect_from_ledger(BATCH)
    assert len(rows) == 1 and bad == 0
    r = rows[0]
    assert r["refl_chars"] == 1817, "台账写了 1817 字,取数层不该交回 0"
    assert r["refl_score"] == 90 and r["refl_status"] == "review"
    assert r["refl_fail_reasons"] == ["未提出可执行的后续改进动作"]
    a = ba.analyze(rows, bad, BATCH)
    assert a["reflection"]["chars_min"] == 1817, \
        "均值区间里出现 0 字,等于报告写了『模型产出过一篇空反思』"
    assert a["router"]["issues_total"] == 3 and a["router"]["zero_issue_runs"] == 0, \
        "台账写了 3 条 issue,读不到明细就报成『Router 零 issue』是把结论编出来"
    assert a["excluded_failed"]["n"] == 0


def test_run_json_present_overrides_the_summary(sandbox):
    """明细在场时以明细为准(台账只是摘要,run.json 才是原始记录)。"""
    _write_ledger(sandbox, dict(FLAT, reflection_chars=9999))
    _write_run_json(sandbox, RUN_ID, NESTED)
    rows, _bad, _gap = ba._collect_from_ledger(BATCH)
    assert rows[0]["refl_chars"] == 1817


def test_error_status_from_ledger_is_quarantined(sandbox):
    """台账记 `reflection_status=error` 时,"没跑成"要摘出来并带原因,不进均值。"""
    err = dict(FLAT, run_id=RUN_ID + "-err", reflection_chars=10,
               reflection_score=None, reflection_status="error",
               reflection_failure_reasons=["Ollama 连接被拒"])
    _write_ledger(sandbox, FLAT, err)                  # 一条正常、一条 error,无 run.json
    rows, bad, _gap = ba._collect_from_ledger(BATCH)
    a = ba.analyze(rows, bad, BATCH)
    assert a["excluded_failed"]["n"] == 1
    assert a["excluded_failed"]["records"][0]["run_id"] == RUN_ID + "-err"
    assert "Ollama 连接被拒" in a["excluded_failed"]["records"][0]["reason"]
    assert a["reflection"]["chars_min"] == 1817, "10 字的失败行不许拉低区间"
    assert a["router"]["issues_total"] == 3


def test_error_status_without_reason_still_says_so(sandbox):
    """error 但台账没记原因:也要有可读的说明,不能留空让读者猜。"""
    r = ba._row(RUN_ID, dict(FLAT, reflection_status="error",
                             reflection_failure_reasons=[]))
    assert ba._is_failed(r)
    assert r["refl_error"] and "error" in r["refl_error"]


def test_router_error_is_still_caught_when_ledger_already_has_quality(sandbox):
    """回读 run.json 不许因为"台账已经有质量字段"就跳过。

    `router.status` 只有 run.json 里有;台账写了 pass 就不再读明细,Router
    炸过的记录会被算成"干净产出 0 issue"。2026-10-04 修掉了这个 gate。
    """
    broken = json.loads(json.dumps(NESTED))
    broken["router"] = {"status": "error", "executed": True, "error": "HTTP 500",
                        "issues": [], "postprocess": {}}
    _write_ledger(sandbox, FLAT)
    _write_run_json(sandbox, RUN_ID, broken)
    rows, _bad, _gap = ba._collect_from_ledger(BATCH)
    assert rows[0]["router_status"] == "error", "台账有质量摘要≠明细读过了"
    assert ba._is_failed(rows[0])
    a = ba.analyze(rows, 0, BATCH)
    assert a["excluded_failed"]["n"] == 1
    assert a["router"]["issues_total"] == 0 and a["router"]["n"] == 0, \
        "Router 失败的记录不许进 issue 统计"


# ---------------------------------------------------------------------------
# 3. 台账自身的脏数据:坏行/重复行/失败行都不能静默进分布
# ---------------------------------------------------------------------------
def test_unparseable_and_unsuccessful_ledger_lines_are_dropped_not_counted(sandbox):
    with open(sandbox["ledger"], "w", encoding="utf-8") as f:
        f.write(json.dumps(FLAT, ensure_ascii=False) + "\n")
        f.write("{ 这不是合法 JSON\n")                       # 坏行 → bad
        f.write(json.dumps(dict(FLAT, run_id=RUN_ID + "#f", ok=False,
                                returncode=1), ensure_ascii=False) + "\n")
        f.write("\n")                                          # 空行
        f.write(json.dumps(FLAT, ensure_ascii=False) + "\n")   # 重复(重试成功两次)
    rows, bad, _gap = ba._collect_from_ledger(BATCH)
    assert bad == 1, "坏行要单独计数,不能和有效记录混在一起"
    assert len(rows) == 1, "失败行不掺进来,重复行只留一条"


def test_failed_row_count_and_scored_row_count_share_one_population(sandbox):
    """`excluded_failed.n + n_scored == n_runs`,且 status 分布与均值同总体。

    2026-10-03 的真实事故:status_dist 用全量、均值用 ok_rows,于是同一节里
    pass 率的分母(43)比"9 条已排除"那句话大一圈,报告自相矛盾。
    """
    rows = [dict(FLAT, run_id="%s-%03d" % (RUN_ID, i),
                 reflection_status=("error" if i == 0 else "pass"),
                 reflection_score=(None if i == 0 else 95),
                 reflection_chars=(7 if i == 0 else 2000))
            for i in range(10)]
    _write_ledger(sandbox, *rows)
    collected, bad, _gap = ba._collect_from_ledger(BATCH)
    a = ba.analyze(collected, bad, BATCH)
    assert a["n_runs"] == 10
    assert a["excluded_failed"]["n"] + a["excluded_failed"]["n_scored"] == a["n_runs"]
    assert sum(a["reflection"]["status_dist"].values()) == a["excluded_failed"]["n_scored"]
    assert sum(a["consistency"].values()) == a["excluded_failed"]["n_scored"]
    assert a["reflection"]["chars_min"] == 2000, "失败行的 7 字占位不许进区间"


def test_branch_collapse_is_called_out_in_numbers_not_left_to_the_reader(sandbox):
    """塌缩判定:>60% 单一分支(且样本 >=10)必须显式给警告。"""
    rows = [ba._row("b-%03d" % i, dict(FLAT, branch=("B" if i < 9 else "C")))
            for i in range(10)]
    a = ba.analyze(rows, 0, BATCH)
    assert a["branch_top"]["share"] == 0.9 and a["branch_top"]["collapse_warning"]
    few = [ba._row("s-%02d" % i, dict(FLAT, branch="B")) for i in range(4)]
    assert not ba.analyze(few, 0, BATCH)["branch_top"]["collapse_warning"], \
        "4 条样本不足以谈『分布偏斜』,不许报警"


# ---------------------------------------------------------------------------
# 3.5 台账缺段必须暴露(G1,2026-10-05)
# ---------------------------------------------------------------------------
def _primary(sandbox, n, batch=BATCH, start=1):
    """在 runs 目录造 n 条一手 run.json(id 尾号 start..start+n-1)。"""
    for i in range(start, start + n):
        rid = "batch-%s-%03d" % (batch, i)
        _write_run_json(sandbox, rid, dict(NESTED, branch="C"))
    return ["batch-%s-%03d" % (batch, i) for i in range(start, start + n)]


def test_ledger_gap_is_reported_when_ledger_misses_shards(sandbox):
    """台账少记几条时,`gap` 必须给出缺口清单,而不是静默出"完整"表。

    2026-10-05 体检 G1 的真实事故:批次 261004-220441-h120seed 一手 35 条、
    台账 31 条、聚合 29 条,`--batch` 只读台账、全文件 grep "缺/missing" = 0,
    于是报告里 C 分支被少报 5 条而**毫无提示**。
    """
    ids = _primary(sandbox, 5)
    _write_ledger(sandbox, *[dict(FLAT, run_id=r) for r in ids[:3]])   # 只记了前 3 条
    rows, bad, gap = ba._collect_from_ledger(BATCH)
    assert bad == 0
    assert len(rows) == 3, "默认只报缺口,不擅自补齐"
    assert gap is not None, "缺 2 条却给 gap=None,等于把事故抹平"
    assert gap["n_primary"] == 5
    assert gap["missing"] == ids[3:], "缺口清单要对得上(谁被漏了)"
    assert gap["extra"] == [] and gap["filled"] == 0
    a = ba.analyze(rows, bad, BATCH, gap)
    assert a["ledger_gap"] == 2
    assert a["ledger_gap_resolved"] is False
    md = ba._md(a)
    assert "与一手产物不一致" in md, "缺口必须出现在报告正文,不能只进 JSON"
    assert ids[3].split("-")[-1] in md or ids[3] in md


def test_fill_from_primary_backfills_and_marks_resolved(sandbox):
    """`fill_from_primary=True` 时缺口用一手补齐,且 `resolved` 明确为真。"""
    ids = _primary(sandbox, 5)
    _write_ledger(sandbox, *[dict(FLAT, run_id=r) for r in ids[:3]])
    rows, _bad, gap = ba._collect_from_ledger(BATCH, fill_from_primary=True)
    assert len(rows) == 5, "补齐后条数应等于一手条数"
    assert sorted(r["run_id"] for r in rows) == ids
    assert gap["filled"] == 2 and gap["missing"] == ids[3:]
    a = ba.analyze(rows, 0, BATCH, gap)
    assert a["n_runs"] == 5
    assert a["ledger_gap"] == 2 and a["ledger_gap_resolved"] is True
    md = ba._md(a)
    assert "与一手产物不一致" not in md, "已补齐还说『不一致』= 自己诬告自己"
    assert "已用一手补齐" in md


def test_gap_is_none_when_ledger_matches_primary(sandbox):
    """台账与一手一致时不许凭空报警(否则守卫本身变成噪音)。"""
    ids = _primary(sandbox, 3)
    _write_ledger(sandbox, *[dict(FLAT, run_id=r) for r in ids])
    rows, _bad, gap = ba._collect_from_ledger(BATCH)
    assert gap is None and len(rows) == 3
    a = ba.analyze(rows, 0, BATCH, gap)
    assert a["ledger_gap"] == 0 and a["ledger_gap_resolved"] is False
    md = ba._md(a)
    assert "一手" not in md and "不一致" not in md, \
        "没有缺口就不该在报告里提一手对账"


def test_gap_is_none_when_no_primary_shards(sandbox):
    """扫不到一手产物时(换机分析/目录被清)不报警 —— 无从对账 ≠ 有缺口。"""
    _write_ledger(sandbox, FLAT)
    rows, _bad, gap = ba._collect_from_ledger(BATCH)
    assert gap is None and len(rows) == 1


def test_extra_ledger_rows_are_also_flagged(sandbox):
    """台账多出的一手没有的 id(改过名/删档遗留)同样要报,不能只看缺。"""
    ids = _primary(sandbox, 2)
    _write_ledger(sandbox, *[dict(FLAT, run_id=r) for r in ids]
                  + [dict(FLAT, run_id="batch-%s-999" % BATCH)])
    rows, _bad, gap = ba._collect_from_ledger(BATCH)
    assert gap is not None and gap["extra"] == ["batch-%s-999" % BATCH]
    assert gap["missing"] == []


# ---------------------------------------------------------------------------
# 4b. 整份台账缺失(体检 N5)—— G1 修的是"有台账缺段",没修"没台账"
# ---------------------------------------------------------------------------
def test_missing_ledger_falls_back_to_primary_instead_of_reporting_zero(sandbox):
    """台账文件整份不存在时,不能报"0 条"——盘上明明有一手产物。

    真实形态:批次 261004-203137-h120b 的台账在 10-04 事故里永久丢失,29 条一手
    产物健在。旧写法 `return [], 0, None` 会让 `--batch` 报"没有可用记录",
    而 `_primary_ids` 能扫到 29 条 —— 这条路径就是那次事故的复现路径。
    """
    ids = _primary(sandbox, 4)
    assert not os.path.exists(sandbox["ledger"]), "本用例的前提就是台账不存在"
    rows, bad, gap = ba._collect_from_ledger(BATCH, fill_from_primary=True)
    assert len(rows) == 4, "台账没了不等于记录没了,应照一手重建"
    assert bad == 0
    assert gap is not None and gap.get("ledger_missing") is True
    assert gap["n_primary"] == 4


def test_missing_ledger_is_not_confused_with_a_resolved_gap(sandbox):
    """台账全缺时 ledger_gap/resolved 会算成 0/False,那读起来像"与一手一致"。

    所以另开 `ledger_missing` 字段单独标这一形态 —— 别让它混进"无缺口"里。
    """
    ids = _primary(sandbox, 3)
    rows, bad, gap = ba._collect_from_ledger(BATCH)
    a = ba.analyze(rows, bad, BATCH, gap)
    assert a["ledger_missing"] is True
    assert a["n_runs"] == 3
    # 关键:这两个字段的值"看起来正常",正是要靠 ledger_missing 才能区分的原因
    assert a["ledger_gap"] == 0 and a["ledger_gap_resolved"] is False
    md = ba._md(a)
    assert "台账整份缺失" in md, "报告正文必须说清台账没了,不能只进 JSON"
    assert "由一手" in md


def test_no_ledger_and_no_primary_still_returns_empty(sandbox):
    """两头都没有才算真无数据 —— 这时返回空是对的(由 main 决定退出码)。"""
    rows, bad, gap = ba._collect_from_ledger(BATCH, fill_from_primary=True)
    assert rows == [] and gap is None
    a = ba.analyze([], bad, BATCH, gap)
    assert a["ledger_missing"] is False, "没有台账也没有一手,不是'台账缺失'"


def test_present_ledger_without_gap_is_not_marked_missing(sandbox):
    """有台账且与一手一致时,ledger_missing 必须是 False(别把守卫变成噪音)。"""
    ids = _primary(sandbox, 2)
    _write_ledger(sandbox, *[dict(FLAT, run_id=r) for r in ids])
    rows, _bad, gap = ba._collect_from_ledger(BATCH)
    a = ba.analyze(rows, 0, BATCH, gap)
    assert a["ledger_missing"] is False
    md = ba._md(a)
    assert "台账整份缺失" not in md, "没丢台账就不该报丢失"


# ---------------------------------------------------------------------------
# 4. 统计原语:均值/中位数/计数算错就是整份报告错
# ---------------------------------------------------------------------------
def test_median_handles_even_odd_and_empty():
    assert ba._median([1, 2, 3]) == 2
    assert ba._median([1, 2, 3, 4]) == 2.5, "偶数条取中间两数平均,不能直接取右半边"
    assert ba._median([]) is None
    assert ba._median([5, "x", None, 7]) == 6.0, "脏值要筛掉而不是排序时炸"


def test_mean_filters_non_numeric_and_empty():
    assert ba._mean([10, 20]) == 15.0
    assert ba._mean([10, None, "x"]) == 10.0
    assert ba._mean([]) is None


def test_count_accepts_list_length_and_stored_integer():
    """台账存计数、run.json 存列表 —— 两种来源都要能读成长度。"""
    assert ba._count([1, 2, 3]) == 3
    assert ba._count(13) == 13
    assert ba._count(None) == 0
    assert ba._count({"a": 1}) == 0, "字典不是计数口径,给 0 让它显式暴露"
    # 字符串不是 _count 的口径:反思正文的长度由 _row 里那句 `or len(...)` 负责
    assert ba._count("abcdef") == 0
    assert ba._row(RUN_ID, NESTED)["refl_chars"] == 1817


def test_row_of_empty_record_degrades_to_zero_counts_not_crash():
    """九块全缺的历史记录:只能给空值,不许炸掉整份分析。"""
    r = ba._row("legacy-001", {})
    assert r["refl_chars"] == 0 and r["issues"] == 0 and r["turns"] == 0
    assert not ba._is_failed(r), "旧记录没有失败标记时不凭空判它『没跑成』"
    a = ba.analyze([r], 0, "legacy")
    assert a["n_runs"] == 1 and a["reflection"]["score_mean"] is None
