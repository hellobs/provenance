# -*- coding: utf-8 -*-
"""清单警告侧守卫:`manifest_warnings` 要能被平台读到,但**不参与 quality 判定**(2026-10-04)。

上一轮把 manifest 接进了生产路径的落盘结构(写侧),但质检原先只看 consistency/reflection,
`live/history._quality_of` 还是逐键挑字段 —— 新键被静默丢掉,清单非空的记录照发平台,
"判官没上班"这种事实又退回日志里闪一次。这一轮把读侧接上,并把它钉死在两件事上:

1. **透出**:索引行与 5002 详情都要带 `manifest_status` / `manifest_warning_count` /
   `manifest_warnings`,两处实现走同一份 `quality_of`(单一来源);
2. **不改变可见性**:警告只贴标签,绝不让 `quality` 从 ok/unverified 变成 questionable/debug。
   理由不是保守,是口径 —— 清单里大半的警告是**如实描述**(规则判定没有采样温度、
   preset 模式没配判定后端、历史记录压根没有清单)。把它们当质量问题会把降级跑一律
   推进默认排除集,平台就"静默少给数据",而这条线最初的铁律恰恰是失败了必须有人知道。
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01 import full_context as fc  # noqa: E402
from case01 import orchestrator as OR  # noqa: E402
from case01 import serve  # noqa: E402
from case01.full_context import manifest_view, quality_of  # noqa: E402
from live import history as lh  # noqa: E402

# 一条"内容没问题"的记录:分支由判官定、立场一致、反思是真文本
_OK_REC = {"run_id": "r-ok", "branch": "B",
           "branch_action": {"source": "judge"},
           "consistency": {"verdict": "consistent", "reason": "立场判官判 wait → 与 B 线一致"},
           "reflection": {"text": "我过度依赖情景测算……(反思全文)", "material": "m"}}


def _with_manifest(warnings=None, error=None):
    rec = dict(_OK_REC)
    m = {} if error is None else {"manifest_error": error}
    if warnings is not None:
        m["manifest_warnings"] = warnings
    rec["manifest"] = m or {"manifest_warnings": []}
    return rec


# ---------------------------------------------------------------- 四种状态
def test_record_without_manifest_is_absent_not_suspicious():
    """2026-10-04 之前的历史产物压根没有清单:记 absent,不是"有问题"。"""
    v = manifest_view(dict(_OK_REC))
    assert v == {"manifest_status": "absent", "manifest_warning_count": 0,
                 "manifest_warnings": []}
    # 空 dict 也算 absent(占位空清单不能冒充"跑得很干净")
    rec = dict(_OK_REC)
    rec["manifest"] = {}
    assert manifest_view(rec)["manifest_status"] == "absent"


def test_clean_manifest_has_no_warnings_to_show():
    v = manifest_view(_with_manifest(warnings=[]))
    assert v["manifest_status"] == "clean" and v["manifest_warning_count"] == 0


def test_nonempty_warnings_are_surfaced_verbatim():
    ws = ["temperature.judge=null:后端是 rules,规则判定没有采样温度",
          "consistency: 立场判官未启用(本次未提供判官后端),verdict 来自关键词快筛"]
    v = manifest_view(_with_manifest(warnings=ws))
    assert v["manifest_status"] == "noted"
    assert v["manifest_warnings"] == ws, "警告原文要透出,改写了就不是那条证据"


def test_manifest_generation_failure_is_its_own_status():
    """清单自己生成失败 ≠ 有警告:复现指纹不可信,原因要排在首位。"""
    v = manifest_view(_with_manifest(warnings=["旧的"], error="OSError: 盘没了"))
    assert v["manifest_status"] == "error"
    assert "OSError" in v["manifest_warnings"][0], v["manifest_warnings"]


def test_long_warning_lists_are_capped_but_counted_in_full():
    ws = ["w-{}".format(i) for i in range(fc._MANIFEST_WARNING_CAP + 12)]
    v = manifest_view(_with_manifest(warnings=ws))
    assert len(v["manifest_warnings"]) == fc._MANIFEST_WARNING_CAP
    assert v["manifest_warning_count"] == len(ws), "截断显示不能把总数也截掉"


# ---------------------------------------------------------------- 不碰可见性
def test_warnings_do_not_change_the_quality_label():
    """核心口径:一条自洽的记录不会因为清单有警告而降级。"""
    plain = quality_of(dict(_OK_REC))["quality"]
    assert plain == "ok"
    for rec in (_with_manifest(warnings=[]),
                _with_manifest(warnings=["branch_mode='rules' 不在 preset/judge 之内"]),
                _with_manifest(warnings=["a"] * 30),
                _with_manifest(error="RuntimeError: builder 挂了")):
        q = quality_of(rec)
        assert q["quality"] == plain, q
    # 也不进默认排除集(平台少给一条就是静默)
    assert "ok" not in lh._HIDDEN_QUALITY


def test_quality_of_carries_the_three_new_keys_for_every_label():
    """每个标签都带上清单三键:平台不必猜"这行没有这个字段"是什么意思。"""
    for case in (dict(_OK_REC), {"run_id": "r-old"},
                 {"run_id": "r-q", "consistency": {"verdict": "inconsistent"}},
                 {"run_id": "r-d", "debug": "x"}, {"run_id": "r-x", "deprecated": True}):
        q = quality_of(case)
        assert {"manifest_status", "manifest_warning_count", "manifest_warnings"} <= set(q)
        assert q["manifest_status"] in ("absent", "clean", "noted", "error")


# ---------------------------------------------------------------- 两个面同口径
def test_review_surface_passes_the_new_keys_through():
    """5010 索引与 5002 质检走同一份 quality_of;逐键挑的包装不许漏掉新键。"""
    rec = _with_manifest(warnings=["consistency: 立场判官未启用……"])
    assert set(lh._quality_of(rec)) == set(quality_of(rec)), \
        "live/history 的键集与 quality_of 分叉了(会静默丢字段)"
    q = lh._quality_of(rec)
    assert q["manifest_status"] == "noted" and q["manifest_warnings"]


def test_quality_lookup_failure_says_unverified_not_absent(monkeypatch):
    """引擎侧读不到判定 ≠ 这条记录没清单:两个事实不能混成一个标签。"""
    def boom(rec):
        raise RuntimeError("quality_of 挂了")

    monkeypatch.setattr(fc, "quality_of", boom)
    q = lh._quality_of({"run_id": "r-1"})
    assert q["manifest_status"] == "unverified"
    assert set(q) == set(lh._quality_of(dict(_OK_REC))), "兜底分支要与成功分支同键集"


def test_platform_detail_endpoint_exposes_the_warning(tmp_path, monkeypatch):
    """5002 详情响应里要能直接看到警告,而不是只能去翻 run.json。"""
    rec = _with_manifest(warnings=["scenario_sha256=null:找不到 scenario"])
    monkeypatch.setattr(serve, "_get_run", lambda run_id: rec)
    meta = serve.run_detail("r-ok")
    assert meta["manifest_status"] == "noted"
    assert any("scenario_sha256=null" in w for w in meta["manifest_warnings"]), meta


def test_expert_view_still_does_not_get_the_manifest():
    """警告进索引是给平台的;专家视图按契约 §3.2 连 manifest 一起剥掉。"""
    rec = _with_manifest(warnings=["consistency: 立场判官未启用(本次未提供判官后端),"])
    rec["turns"] = []
    safe = lh.expert_safe_record(rec)
    assert "manifest" not in safe, safe.keys()
    blob = json.dumps(safe, ensure_ascii=False)
    assert "立场判官" not in blob and "manifest_status" not in blob


# ---------------------------------------------------------------- 端到端
def _run_no_llm(tmp_path, monkeypatch, run_id):
    root = str(tmp_path / "runs")
    monkeypatch.setenv("CASE01_RUNS_ROOT", root)
    monkeypatch.setattr(OR, "RUNS_ROOT", lambda: root)
    OR.run_case01(no_llm=True, timeline="A", run_id=run_id)
    with io.open(os.path.join(root, run_id, "run.json"), encoding="utf-8") as f:
        return root, json.load(f)


def test_a_no_llm_run_shows_up_as_noted_in_the_review_index(tmp_path, monkeypatch):
    """真跑一条 no-llm:警告既在文件里,也在索引行里(这条链以前是断的)。"""
    root, rec = _run_no_llm(tmp_path, monkeypatch, "warn-e2e")
    assert rec["consistency"]["method"] == "quick_scan", rec["consistency"]
    monkeypatch.setattr(lh, "_data_root", lambda env, *parts: root)
    row = lh._brief_review("warn-e2e")
    assert "error" not in row, row
    assert row["manifest_status"] == "noted", row
    assert any("立场判官未启用" in w for w in row["manifest_warnings"]), row
    # 警告不参与判定:把同一份记录的警告清空,quality 必须一个字都不变
    no_warn = dict(rec)
    no_warn["manifest"] = {"manifest_warnings": []}
    assert fc.quality_of(rec)["quality"] == fc.quality_of(no_warn)["quality"], rec["quality"]
    assert row["quality"] == fc.quality_of(rec)["quality"], row["quality"]


def test_historical_records_without_manifest_stay_unverified_not_flagged(tmp_path, monkeypatch):
    """老记录(5/242 才有清单)一律 absent:接入警告侧不该改动历史结论。"""
    root = str(tmp_path / "runs")
    os.makedirs(os.path.join(root, "old-run"))
    old = {"run_id": "old-run", "branch": "C", "consistency": {"verdict": "consistent"},
           "reflection": {"text": "真反思"}}
    with io.open(os.path.join(root, "old-run", "run.json"), "w", encoding="utf-8") as f:
        json.dump(old, f, ensure_ascii=False)
    monkeypatch.setattr(lh, "_data_root", lambda env, *parts: root)
    row = lh._brief_review("old-run")
    assert row["manifest_status"] == "absent" and row["manifest_warning_count"] == 0
    assert row["quality"] == "ok", "老记录的既有结论不该因为这次改动而变"
