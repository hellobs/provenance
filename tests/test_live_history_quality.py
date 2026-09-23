# -*- coding: utf-8 -*-
"""5010 对外面(聚合索引 + run-detail)的契约守卫。

2026-09-21:用户决定把 **5010 升格为唯一对接契约**。此前 5010 的聚合索引没有质检口径、
`/api/run-detail` 又把整个 `run.json`(含 `injector` 分支元信息、`branch_action`、
`consistency`、`debug`)原样交出去 —— 平台若按 5010 取数,会把自相矛盾的记录建单、
把实验底牌递给专家。这个文件把修复钉住。

2026-09-22(CI 必红的原因):`case01/runs` 按约定**不入库**(只入约定与代码),CI 上一条
成品记录都没有,而此前这些用例直接断言"至少该有一条" —— 于是 CI 必红。真正的问题不是
断言写错了,而是 **expert-safe 信息边界在 CI 上根本没被守过**。现在改成:签入夹具
(`tests/fixtures/`)驱动,数据根用 `CASE01_RUNS_ROOT` / `CASE00_CHECKPOINTS_ROOT` /
`RESULTS_COMPRESSED_ROOT` 指过去(见 `live/history.py::_data_root`),CI 与本地跑**同一套
代码路径**;本机有真实记录时,再对真实记录跑一遍(真实记录里的漂移不能只在本地静默溜过)。
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

# 签入夹具(CI 与本地都在)—— 保证"没有本机数据"时这些用例仍在真测,而不是空转
# 注意:目录名不能叫 `checkpoints/` —— `.gitignore` 里有一条裸 `checkpoints/`,
# 会把夹具静默吃掉(本地照绿、CI 才红)。所以这里叫 `case00_checkpoints`。
FIXTURE = {"runs": os.path.join(_this_dir, "fixtures", "case01_runs"),
           "checkpoint": os.path.join(_this_dir, "fixtures", "case00_checkpoints"),
           "compressed": os.path.join(_this_dir, "fixtures", "compressed")}
# 本机真实数据(按约定不入库;CI 上没有)
REAL_RUNS = os.path.join(_PKG, "case01", "runs")
REAL_CKPTS = os.path.join(_PKG, "results", "checkpoints")
REAL_COMPRESSED = os.path.join(_PKG, "results", "compressed")

HIDDEN = ("questionable", "debug")
_STRIPPED = ("injector", "branch_action", "consistency", "debug", "quality", "branch")


def _call(coro):
    return json.loads(asyncio.run(coro).body.decode("utf-8"))


def _run_ids(root):
    if not os.path.isdir(root):
        return []
    return [n for n in sorted(os.listdir(root))
            if os.path.isfile(os.path.join(root, n, "run.json"))]


def _runs_roots():
    """夹具 + 本机真实记录。真实记录不在 CI 里;本机有就一起验。

    为什么本机还要再跑一遍真实的:夹具只能钉住我**想到**的口径,真实记录里
    才会出现没想到的漂移(比如某条记录少了 `consistency`)。
    """
    roots = [FIXTURE["runs"]]
    if _run_ids(REAL_RUNS):
        roots.append(REAL_RUNS)
    return roots


def _is_fixture(root):
    """夹具那一遍是**严格**的:夹具内容由我们掌握,任何"空转"都说明夹具坏了。"""
    return os.path.abspath(root) == os.path.abspath(FIXTURE["runs"])


def test_fixture_pins_every_quality_verdict(monkeypatch):
    """夹具把 `quality_of` 的口径逐条钉住:ok / questionable(两种)/ debug。

    夹具一旦被换成"随手造的数据",这条会先红 —— 免得其它用例的严格性悄悄失效。
    """
    monkeypatch.setenv("CASE01_RUNS_ROOT", FIXTURE["runs"])
    body = _call(H.list_all_runs(include_questionable=True))
    got = {r["run_id"]: r["quality"] for r in body["runs"] if r.get("source") == "review"}
    assert got == {"fx-judge-branchA": "ok",
                   "fx-preset-inconsistent": "questionable",
                   "fx-judge-failed": "questionable",
                   "fx-debug-truncated": "debug"}, got


def test_aggregate_reports_quality_on_every_case01_run(monkeypatch):
    """聚合索引必须带质检字段,且与 5002 同源(直接复用 case01.full_context.quality_of)。"""
    for root in _runs_roots():
        monkeypatch.setenv("CASE01_RUNS_ROOT", root)
        body = _call(H.list_all_runs(include_questionable=True))
        rows = [r for r in body["runs"] if r.get("source") == "review"]
        assert rows, "{} 下没有 case01 成品记录".format(root)
        for r in rows:
            assert r["quality"] in ("ok", "questionable", "debug", "unverified"), r
            assert "consistency" in r and "branch_source" in r and "debug" in r


def test_aggregate_hides_questionable_but_says_so(monkeypatch):
    """默认滤掉 questionable/debug —— 但**不静默**:excluded 里报明理由。"""
    for root in _runs_roots():
        monkeypatch.setenv("CASE01_RUNS_ROOT", root)
        full = _call(H.list_all_runs(include_questionable=True))
        dflt = _call(H.list_all_runs())
        hidden = [r for r in full["runs"] if r.get("quality") in HIDDEN]
        if not hidden:
            # 真实数据里暂时没有可疑记录时,只验开关语义;夹具那遍不允许为空(否则是空转)
            assert not _is_fixture(root), "夹具里必须有可疑/调试记录"
            assert dflt["count"] == full["count"]
            continue
        assert dflt["count"] == full["count"] - len(hidden)
        assert dflt["excluded"]["count"] == len(hidden)
        assert {x["run_id"] for x in dflt["excluded"]["runs"]} == {r["run_id"] for r in hidden}
        assert "include_questionable" in dflt["excluded"]["reason"]
        # 可疑记录只是"默认不给",不是"删了":全量口径下它必须在
        assert {r["run_id"] for r in full["runs"]} >= {r["run_id"] for r in hidden}


def test_run_detail_defaults_to_expert_safe_view(monkeypatch):
    """专家视图不得出现实验元信息(与 full-context 同一信息边界)。"""
    for root in _runs_roots():
        monkeypatch.setenv("CASE01_RUNS_ROOT", root)
        ids = _run_ids(root)
        assert ids, "{} 下没有成品记录可测".format(root)
        for rid in ids:
            body = _call(H.run_detail("review", rid))
            assert body["ok"] and body["view"] == "expert-safe", body
            data = body["data"]
            for bad in _STRIPPED:
                assert bad not in data, (rid, bad)
            # 专家审核真正要读的东西必须在
            assert "reflection" in data and "router" in data
            assert "turns" in data and "events" in data


def test_run_detail_raw_view_is_opt_in(monkeypatch):
    """原始全文只在显式 `?raw=1` 时给(引擎侧/内部排查用),且必须逐字等于磁盘记录。"""
    # 记录里**必须**存在、而专家视图必须剥掉的键。注意不含 `quality`:按约定记录里
    # 不落盘质检副本(单一来源在 `quality_of()` 现算),所以它不可能出现在 raw 里。
    must_strip = {"injector", "branch_action", "consistency", "branch", "debug", "manifest"}
    for root in _runs_roots():
        monkeypatch.setenv("CASE01_RUNS_ROOT", root)
        ids = _run_ids(root)
        assert ids, "{} 下没有成品记录可测".format(root)
        stripped = set()
        for rid in ids:
            safe = _call(H.run_detail("review", rid))
            raw = _call(H.run_detail("review", rid, raw=True))
            assert raw["view"] == "raw" and raw["ok"]
            with open(os.path.join(root, rid, "run.json"), encoding="utf-8") as f:
                assert raw["data"] == json.load(f), "raw 视图必须逐字等于磁盘上的 run.json"
            # raw 与 expert-safe 必须**真的不同**,否则这条契约只是名义上的
            stripped |= set(raw["data"]) - set(safe["data"])
            assert set(safe["data"]) - {"_view"} <= set(raw["data"]), rid
        if _is_fixture(root):
            assert must_strip <= stripped, must_strip - stripped


def test_non_case01_sources_are_unverified_not_hidden(monkeypatch):
    """case00 痕迹/压缩成品没有一致性戳 → unverified,**不该**被当成可疑记录滤掉。

    (判不了 ≠ 有问题:滤掉它们等于悄悄少给平台数据。)
    """
    for ck, comp, strict in ((FIXTURE["checkpoint"], FIXTURE["compressed"], True),
                             (REAL_CKPTS, REAL_COMPRESSED, False)):
        monkeypatch.setenv("CASE00_CHECKPOINTS_ROOT", ck)
        monkeypatch.setenv("RESULTS_COMPRESSED_ROOT", comp)
        body = _call(H.list_all_runs())
        others = [r for r in body["runs"] if r.get("source") in ("checkpoint", "compressed")]
        if strict:
            # 夹具那遍必须两类都有,否则"不过滤"这条断言是空转
            assert {r["source"] for r in others} == {"checkpoint", "compressed"}, others
        for r in others:
            assert r["quality"] == "unverified", r
            assert r["consistency"] == "unverified", r


def test_pagination_and_unknown_params(monkeypatch):
    """分页要真生效;不认识的参数不许静默吞掉(回 ignored_params)。

    2026-09-23 实测:加参数之前 `?limit=`/`?quality=` 传了全部无效且**没有任何提示**,
    平台侧会以为过滤生效了。
    """
    class _Req:
        def __init__(self, params):
            self.query_params = params

    for k, v in FIXTURE.items():
        monkeypatch.setenv({"runs": "CASE01_RUNS_ROOT",
                            "checkpoint": "CASE00_CHECKPOINTS_ROOT",
                            "compressed": "RESULTS_COMPRESSED_ROOT"}[k], v)
    full = _call(H.list_all_runs(_Req({}), include_questionable=True))
    page = _call(H.list_all_runs(_Req({}), include_questionable=True, limit=2, offset=1))
    assert page["count"] == 2 and page["total"] == full["total"] == 6, (page["count"],
                                                                       page["total"])
    assert [r["run_id"] for r in page["runs"]] == [r["run_id"] for r in full["runs"][1:3]]
    only = _call(H.list_all_runs(_Req({}), include_questionable=True, source="review"))
    assert {r["source"] for r in only["runs"]} == {"review"} and only["total"] == 4
    warn = _call(H.list_all_runs(_Req({"quality": "ok", "foo": "1"})))
    assert warn["ignored_params"] == ["foo", "quality"], warn.get("ignored_params")
    assert "quality" in warn["ignored_note"]


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
