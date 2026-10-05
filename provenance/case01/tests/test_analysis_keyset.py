# -*- coding: utf-8 -*-
"""批次聚合产物 `analysis.json` 的键集守卫(2026-10-05 第九轮只读核查 N9-3)。

为什么要有这条:
    第六轮 M1 给 `manifest` 钉了精确键集(`test_manifest_keyset.py`),**同一族问题
    在 `analysis.json` 上没有守卫**:分析器 10-05 加了 `ledger_missing` 字段(体检 N5),
    盘上 7 份入库 `analysis.json` 一起都没有,而当时**没有任何测试会发现这件事** ——
    谁引用"批次统计"就拿到一份缺字段的表,且没人被报红。

    差别在于:manifest 是**每次运行现场生成**的,键集漂移立刻影响新产出;
    `analysis.json` 是**入库产物**,代码改了不会回填历史文件,于是
    "代码键集" 与 "产物键集" 天然会分叉。所以这条守卫刻意分成两半:

      1. `test_analyze_output_keyset_is_pinned` —— 钉住 `analyze()` **当前产出**的
         精确键集。加/删键立刻报红,逼人同步本文件(报红是设计意图)。
      2. `test_shipped_analysis_is_subset_of_analyzer` —— 入库产物里出现的键
         **必须是分析器产出键集的子集**。历史产物缺新字段是正常的(不回填),
         所以这一条只抓"产物里有分析器根本不产出的键"(真 drift,例如手改过、
         或字段被重命名而产物没跟上),不去要求产物与代码逐键全等。

    与 `test_manifest_keyset.py` 的区别:那边要求**精确全等**(manifest 每次现场生成,
    可以全等);这边对产物只要求**子集**(产物不回填)。别把这两个判据混用。
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.tools import batch_analyze as ba  # noqa: E402
from case01.tools.batch_analyze import analyze  # noqa: E402

BATCH = "261005-000000-keyset"
RUN_ID = "batch-261005-000000-keyset-001"
REFL_TEXT = "反思正文。" * 60

# `analyze()` 当前产出的**精确**顶层键集(2026-10-05 实测)。
# 加/删键时必须同步这里 —— 报红是设计意图,不是要人去放宽它。
EXPECTED_KEYS = frozenset({
    "title",                        # 批次号
    "n_runs",                       # 本表实际统计条数
    "n_unreadable",                 # 解析失败被排除的条数
    "ledger_gap",                   # 台账缺段条数(整份缺失时为 0,别据此判断完整)
    "ledger_gap_resolved",          # 缺口已用一手补齐(此时 n_runs == 一手条数)
    "ledger_missing",               # 台账**整份**不存在(体检 N5;与 resolved 区分)
    "ledger_gap_detail",            # 对账明细:missing/extra/filled/ledger_missing
    "branches",                     # 分支分布
    "branch_top",                   # 最高分支 + 塌缩警告
    "excluded_failed",              # "没跑成"的记录(不摘进质量统计)
    "reflection",                   # 反思字数分布(**质量分也在这一节**,没有独立 quality 键)
    "router",                       # Router 分支统计
    "consistency",                  # 一致性口径
    "per_run",                      # 逐条明细
})

# 平台/文档真正会读的键。方向反了比键集漂移更严重:声明必有的键产不出来。
REQUIRED_KEYS = frozenset({
    "title", "n_runs", "n_unreadable", "branches", "reflection",
    "per_run", "ledger_gap", "ledger_missing",
})


def _rows():
    """经**真实取数层** `_row()` 造行,而不是手搓一个 dict。

    手搓的行只覆盖本测试用到的那几个字段,与 `analyze()` 真实吃进去的形状会漂 ——
    那样这条守卫就变成"在验我自己造的假数据"。走 `_row()` 才与生产路径同形。
    """
    record = {
        "branch": "B",
        "consistency": {"verdict": "consistent", "reason": "立场与分支一致"},
        "turns": ["t0", "t1"],
        "events": [1] * 4,
        "reflection": {"text": REFL_TEXT,
                       "quality": {"score": 90, "status": "review",
                                   "failure_reasons": []}},
        "router": {"status": "", "issues": [1, 2],
                   "postprocess": {"final_issue_count": 2}},
    }
    return [ba._row(RUN_ID, record)]


def test_analyze_output_keyset_is_pinned():
    """`analyze()` 的顶层键集必须与 EXPECTED_KEYS **精确全等**。

    变异验证:把 `analyze()` 里的 `ledger_missing` 删掉,本条立刻红。
    """
    a = analyze(_rows(), 0, BATCH)
    got = frozenset(a.keys())
    assert got == EXPECTED_KEYS, (
        "analyze() 顶层键集漂移。\n"
        "  少: {}\n  多: {}\n"
        "处理:代码改了就同步本文件 EXPECTED_KEYS,并检查入库 analysis.json 是否要重生成"
        "(`python -m case01.tools.batch_analyze --batch <批次> --fill-from-primary`)。"
        .format(sorted(EXPECTED_KEYS - got), sorted(got - EXPECTED_KEYS)))
    assert REQUIRED_KEYS <= got, (
        "声明必需的键产不出来:{}".format(sorted(REQUIRED_KEYS - got)))


def test_shipped_analysis_is_subset_of_analyzer():
    """入库 `analysis.json` 的键必须是分析器产出键集的**子集**。

    为什么只要子集:入库产物是历史快照,代码加字段后**不回填**(既有纪律),
    所以"产物缺新键"是正常的、放过;"产物有分析器压根不产出的键"才是真drift
    (字段被重命名、手改过、或从别处拷来的),必须报红。
    若哪天要让产物与代码逐键全等,做法是重生成全部批次而不是放宽这条判据。
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    pattern = os.path.join(root, "provenance", "results", "analysis",
                           "batch_runs", "*", "analysis.json")
    paths = sorted(glob.glob(pattern))
    if not paths:
        # 产物目录整体不入库时(干净检出的常态)本条不适用,不是失败
        return
    extra = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            got = frozenset(json.load(fh).keys())
        over = got - EXPECTED_KEYS
        if over:
            extra[os.path.basename(os.path.dirname(p))] = sorted(over)
    assert not extra, (
        "入库 analysis.json 出现分析器不产出的键(真drift):{}\n"
        "多半是字段被重命名而产物没重新生成。".format(extra))


def test_per_run_row_declares_its_source():
    """`per_run` 每行必须带 `source`,标明这条是台账行还是一手补入的。

    体检 N4:补齐的记录与台账原有记录在产物侧**同形**,下游拿到 `per_run`
    分不出哪几条是台账独有字段(seconds/attempt/model_actual)齐的、哪几条是
    拿一手 run.json 造的 —— 那些字段在补入行上是空的。判"这条结论建立在什么
    数据上"必须能追到来源,否则不可追。

    这里钉的是**键存在**,不钉具体取值:"台账" 与 "一手" 都算对,记错值由上面
    两条(键集全等 + 子集)之外的产物校验负责;这条只保证"不会静默丢掉来源信息"。
    """
    a = analyze(_rows(), 0, BATCH)
    rows = a.get("per_run") or []
    if not rows:
        return                       # 空表时不适用
    missing = [r for r in rows if "source" not in r]
    assert not missing, (
        "per_run 有 {} 行缺 `source`,下游无法分辨台账行与一手补入行:{}"
        .format(len(missing), missing[:3]))