# -*- coding: utf-8 -*-
"""平台契约的"数据完整性"守卫(2026-09-21 体检新增)。

契约承诺:一次受控实验的成品记录里,专家审核要读的九块必须齐全;每条记录必须带质检字段。
这是**数据驱动**的检查 —— 对每条真实记录逐条验,而不是只测一条样例。

2026-09-22:`case01/runs` 按约定不入库,CI 上一条都没有,于是这些用例在 CI 上**全是
skip** —— 契约在 CI 上等于没守。现在改成"夹具 + 本机真实记录"的并集:夹具
(`tests/fixtures/case01_runs`)签入,CI 一定跑得到;本机有真实记录时一并验(真实记录里
才会出现没想到的漂移)。
"""
import glob
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "provenance")
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

RUNS_FIXTURE = sorted(glob.glob(os.path.join(_HERE, "fixtures", "case01_runs", "*", "run.json")))
RUNS_REAL = sorted(glob.glob(os.path.join(_PKG, "case01", "runs", "*", "run.json")))
RUNS = RUNS_FIXTURE + RUNS_REAL

# 契约 §2.2 / 专家安全视图白名单:这些块必须在记录里(缺了专家就看不成)
REQUIRED_BLOCKS = ["turns", "retrievals", "events", "state_history", "final_feedback",
                   "audit", "condition_monitor", "reflection", "router"]


def _load(p):
    return json.load(io.open(p, encoding="utf-8"))


def test_fixture_records_are_present():
    """夹具必须在 —— 夹具是这些契约在 CI 上唯一的样本,丢了就是静默空转。"""
    ids = {os.path.basename(os.path.dirname(p)) for p in RUNS_FIXTURE}
    assert ids == {"fx-judge-branchA", "fx-preset-inconsistent", "fx-debug-truncated"}, ids


def test_every_record_has_the_nine_blocks():
    missing = {}
    for p in RUNS:
        d = _load(p)
        lack = [k for k in REQUIRED_BLOCKS if k not in d]
        if lack:
            missing[os.path.basename(os.path.dirname(p))] = lack
    assert not missing, "有记录缺专家要读的块: {}".format(missing)


def test_every_record_carries_reflection_and_valid_quality():
    """质检口径:**只有一处实现**(`full_context.quality_of()` 现算),记录里不落盘副本。

    实测:25 条记录都不存 `quality` 字段 —— 这正是我们想要的(单一来源)。若将来有人把它落盘,
    这里会要求它与现算值一致,防止出现第二份会过期的副本。
    """
    from case01.full_context import quality_of

    allowed = {"ok", "questionable", "debug", "unverified"}
    bad = []
    for p in RUNS:
        d = _load(p)
        rid = os.path.basename(os.path.dirname(p))
        if not (d.get("reflection") or {}).get("text"):
            bad.append((rid, "无反思正文"))
        verdict = (d.get("consistency") or {}).get("verdict")
        if verdict not in ("consistent", "inconsistent", "unknown", None):
            bad.append((rid, "consistency.verdict 取值异常: {}".format(verdict)))
        computed = quality_of(d)["quality"]
        if computed not in allowed:
            bad.append((rid, "现算 quality 取值非法: {}".format(computed)))
        stored = d.get("quality")
        if isinstance(stored, dict) and stored.get("quality") not in (None, computed):
            bad.append((rid, "落盘的 quality 与现算不一致(陈旧副本): {} vs {}".format(
                stored.get("quality"), computed)))
    assert not bad, bad


def test_router_issues_are_reviewable():
    """待审问题必须能被专家审:每个 issue 要有 id、检查项、风险等级、风险说明。"""
    bad = []
    for p in RUNS:
        d = _load(p)
        rid = os.path.basename(os.path.dirname(p))
        seen = set()
        for it in ((d.get("router") or {}).get("issues") or []):
            iid = it.get("id")
            if not iid or iid in seen:
                bad.append((rid, "issue id 缺失或重复: {}".format(iid)))
            seen.add(iid)
            for k in ("field", "risk", "risk_note", "summary"):
                if not str(it.get(k) or "").strip():
                    bad.append((rid, "issue {} 缺 {}".format(iid, k)))
    assert not bad, bad


def test_expert_safe_view_is_lossless_for_review():
    """专家安全视图必须保留九块与反思(剥掉的只能是实验元信息)。"""
    from live.history import expert_safe_record
    for p in RUNS[:5]:
        d = _load(p)
        safe = expert_safe_record(d)
        for k in ("turns", "retrievals", "events", "state_history", "reflection", "router"):
            assert k in safe, (os.path.basename(os.path.dirname(p)), k)
        for bad_key in ("injector", "branch_action", "consistency", "debug", "quality", "branch"):
            assert bad_key not in safe, (os.path.basename(os.path.dirname(p)), bad_key)
        assert safe["reflection"] == (d.get("reflection") or {}), "反思原文不许被改"
