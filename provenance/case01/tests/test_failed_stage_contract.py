# -*- coding: utf-8 -*-
"""反思/Router 失败路径的契约守卫(2026-10-03)。

为什么要有这个文件:批量实验(批次 261003)里出现过一条真实故障 ——
`orchestrator.py` 的 Reflection+Router 是一个 try 包两步,Router 失败时
末行会把**已经写好的 reflection 覆盖成 `{"text": "(失败)"}`**。后果有两个:

1. **router 字段压根没赋值** → 九块缺一块,`test_every_record_has_the_nine_blocks` 红;
2. **质量分被丢掉** → 统计把"失败"当成一篇 7 字超短反思混进字数均值,
   质量均值被污染(项目自己的铁律:不允许静默 —— 失败必须可辨、不可伪装成结果)。

所以钉住三条:**分开 try**、**失败也写齐九块**、**失败在统计里可摘出来**。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.orchestrator import _failed_reflection, _failed_router  # noqa: E402


def test_failed_reflection_is_structurally_complete():
    """失败占位必须带完整 quality 结构(下游 quality 读取器不能 KeyError)。"""
    r = _failed_reflection(RuntimeError("boom"))
    assert set(r) >= {"material", "text", "quality"}, r
    q = r["quality"]
    # 关键:score 必须是 None 而不是 0 —— 0 会被统计当成"质量极差的反思"
    assert q["score"] is None, "失败不是 0 分,0 分会被当成低质量样本混进均值"
    assert q["status"] == "error", "失败必须可辨,不能伪装成 pass/fail"
    assert q["max_score"] == 100
    assert "error" in q and "RuntimeError" in q["error"], \
        "异常原文要落盘(不允许静默:出事后知道是谁的锅)"


def test_failed_router_is_structurally_complete():
    """Router 占位必须结构齐全,且区分"调了但失败"与"压根没调"。"""
    r = _failed_router(RuntimeError("boom"))
    assert set(r) >= {"raw", "issues", "postprocess", "expert_pool_version",
                      "status", "executed", "error"}, r
    assert r["status"] == "error" and r["executed"] is True
    assert isinstance(r["issues"], list) and r["issues"] == []

    s = _failed_router(RuntimeError("上游没成功"), executed=False)
    assert s["status"] == "skipped" and s["executed"] is False


def test_batch_analyze_excludes_failed_from_means():
    """失败记录必须被摘出均值,且在结果里显式报数(不静默)。"""
    from case01.tools.batch_analyze import analyze, _is_failed

    good = {
        "run_id": "ok-1", "branch": "B", "consistency": "consistent",
        "refl_chars": 2600, "refl_score": 95, "refl_status": "pass",
        "refl_fail_reasons": [], "issues": 5, "issues_final": 5,
        "turns": 4, "events": 13,
        "router_status": "", "refl_error": "", "router_error": "",
    }
    # 模拟 orchestrator 的失败占位被读成一行
    bad = {
        "run_id": "bad-1", "branch": "B", "consistency": "unknown",
        "refl_chars": len("(反思生成失败)"), "refl_score": None,
        "refl_status": "error", "refl_fail_reasons": ["反思生成失败"],
        "issues": 0, "issues_final": 0, "turns": 4, "events": 13,
        "router_status": "error",
        "refl_error": "RuntimeError: boom", "router_error": "RuntimeError: boom",
    }

    assert _is_failed(bad) is True
    assert _is_failed(good) is False

    a = analyze([good, bad], bad=0, title="unit")
    # 均值只算好记录:坏记录的 7 字与 0 issue 不得污染
    assert a["reflection"]["chars_mean"] == 2600, a["reflection"]["chars_mean"]
    assert a["reflection"]["score_mean"] == 95
    assert a["router"]["issues_mean"] == 5
    # 但必须显式报出来
    ex = a["excluded_failed"]
    assert ex["n"] == 1 and ex["n_scored"] == 1, ex
    assert ex["records"][0]["run_id"] == "bad-1"
    assert "RuntimeError" in ex["records"][0]["reason"]


def test_orchestrator_keeps_good_reflection_when_router_fails():
    """Router 失败不得冲掉已经生成好的 reflection(整块数据丢失)。"""
    import inspect
    import case01.orchestrator as orch

    src = inspect.getsource(orch.run_case01)
    # 两个 except 各自独立:Reflection 的 except 只写 reflection,Router 的只写 router
    assert src.count("rec.data[\"reflection\"] = _failed_reflection") == 1, \
        "Reflection 失败路径应只写 reflection"
    assert src.count("rec.data[\"router\"] = _failed_router") == 2, \
        "Router 有「跳过」与「失败」两条路径"
    # 关键:Router 的 except 块里不得再碰 reflection
    router_except = src.split("except Exception as _re:", 2)[-1]
    assert "reflection" not in router_except, \
        "Router 失败分支里出现了 reflection 赋值 —— 会覆盖已生成好的反思"
