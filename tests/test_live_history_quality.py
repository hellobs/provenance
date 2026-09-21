# -*- coding: utf-8 -*-
"""5010 对外面(聚合索引 + run-detail)的契约守卫。

2026-09-21:用户决定把 **5010 升格为唯一对接契约**。此前 5010 的聚合索引没有质检口径、
`/api/run-detail` 又把整个 `run.json`(含 `injector` 分支元信息、`branch_action`、
`consistency`、`debug`)原样交出去 —— 平台若按 5010 取数,会把自相矛盾的记录建单、
把实验底牌递给专家。这个文件把修复钉住。
"""
import asyncio
import json
import os
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_this_dir)
_PKG = os.path.join(_REPO, "provenance")
for _p in (_PKG, _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from live import history as H  # noqa: E402

RUNS = os.path.join(_PKG, "case01", "runs")
HIDDEN = ("questionable", "debug")


def _call(coro):
    return json.loads(asyncio.run(coro).body.decode("utf-8"))


def _runs_on_disk():
    if not os.path.isdir(RUNS):
        return []
    return [n for n in sorted(os.listdir(RUNS))
            if os.path.isfile(os.path.join(RUNS, n, "run.json"))]


def test_aggregate_reports_quality_on_every_case01_run():
    """聚合索引必须带质检字段,且与 5002 同源(直接复用 case01.full_context.quality_of)。"""
    body = _call(H.list_all_runs(include_questionable=True))
    rows = [r for r in body["runs"] if r.get("source") == "review"]
    assert rows, "至少该有一条 case01 成品记录"
    for r in rows:
        assert r["quality"] in ("ok", "questionable", "debug", "unverified"), r
        assert "consistency" in r and "branch_source" in r and "debug" in r


def test_aggregate_hides_questionable_but_says_so():
    """默认滤掉 questionable/debug —— 但**不静默**:excluded 里报明理由。"""
    full = _call(H.list_all_runs(include_questionable=True))
    dflt = _call(H.list_all_runs())
    hidden = [r for r in full["runs"] if r.get("quality") in HIDDEN]
    if not hidden:
        # 数据里暂时没有可疑记录时,至少断言开关语义正确
        assert dflt["count"] == full["count"]
        return
    assert dflt["count"] == full["count"] - len(hidden)
    assert dflt["excluded"]["count"] == len(hidden)
    assert {x["run_id"] for x in dflt["excluded"]["runs"]} == {r["run_id"] for r in hidden}
    assert "include_questionable" in dflt["excluded"]["reason"]


def test_run_detail_defaults_to_expert_safe_view():
    """专家视图不得出现实验元信息(与 full-context 同一信息边界)。"""
    ids = _runs_on_disk()
    assert ids, "没有成品记录可测"
    body = _call(H.run_detail("review", ids[-1]))
    assert body["ok"] and body["view"] == "expert-safe"
    data = body["data"]
    for bad in ("injector", "branch_action", "consistency", "debug", "quality", "branch"):
        assert bad not in data, bad
    # 专家审核真正要读的东西必须在
    assert "reflection" in data and "router" in data
    assert "turns" in data and "events" in data


def test_run_detail_raw_view_is_opt_in():
    """原始全文只在显式 `?raw=1` 时给(引擎侧/内部排查用)。"""
    ids = _runs_on_disk()
    body = _call(H.run_detail("review", ids[-1], raw=True))
    assert body["view"] == "raw"
    assert "injector" in body["data"] or "branch_action" in body["data"]


def test_expert_safe_record_keeps_contract_keys():
    """白名单要覆盖平台契约 §2.2 明确要读的那些键。"""
    rec = {"run_id": "r", "start_date": "d", "end_date": "e", "turns": [1],
           "retrievals": [], "events": [], "state_history": [], "final_feedback": {},
           "audit": [], "condition_monitor": [], "reflection": {"text": "x"},
           "router": {"issues": []}, "injector": {"nodes": []},
           "branch_action": {"source": "preset"}, "debug": "x", "quality": "debug"}
    out = H.expert_safe_record(rec)
    for k in ("run_id", "turns", "retrievals", "events", "state_history",
              "final_feedback", "audit", "condition_monitor", "reflection", "router"):
        assert k in out, k
    assert out["reflection"]["text"] == "x"
