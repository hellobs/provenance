# -*- coding: utf-8 -*-
"""2026-10-04 体检挖出的两条落盘路径口径漂移,在此钉死。

背景:同一枚一致性戳有两条落盘路径 —— case01 编排器(批量线)与 injector 映射器
(live 演示线,由 `case01/vizkit/live_run.py:_map_run` 起 `python -m …pipeline
--from-record … --reflect`)。10-03 晚把立场判官定为主判据(b800d31),但映射器
只认显式传入的 `router_llm`,而那个 CLI 根本没有这个参数 —— 实测 203/203 条成品
全是关键词 `quick_scan`,判官修复在演示主路径上零验证。同时映射出的清单把实跑
`branch_mode=judge`(raw 里带 LLM 判据原文)覆盖成 CLI 默认的 `preset`,
把"AI 判定"记成了"可控对照"。

本文件全部离线:判官后端用 monkeypatch 注入的假客户端,不发真实请求。
"""
import json

import pytest

from case01.injector.pipeline import run_pipeline


def _raw(branch="B", mode="judge", ai_answer="建议维持零仓位,先观望,等确认后再评估。"):
    return {
        "schema_version": "injector-0.1", "run_id": "r-judge-1", "mode": "mavis",
        "branch": branch, "branch_mode": mode, "branch_source": "judge",
        "judge_info": {"detected": branch, "reason": "cannot confirm, stay flat",
                       "judge": "llm"},
        "roles": ["Investment AI", "Ethan Lin"], "scenario_dir": "",
        "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1,
                   "released_events": [], "events": [],
                   "dialogue": [{"x": [["Ethan Lin", "现在要不要买?"],
                                       ["Investment AI", ai_answer]]}],
                   "world_state": {"date": "2026-08-27", "branch": branch}}],
        "world_audit": [], "summary": {"node_count": 1}, "c_plan": {},
    }


class _StanceClient:
    """假判官后端:按预设脚本回 stance JSON;`boom=True` 时每次都抛。"""

    def __init__(self, payload=None, boom=False):
        self.payload = payload if payload is not None else {
            "stance": "wait", "reason": "AI 明确维持零仓位"}
        self.boom = boom
        self.calls = []

    def chat(self, messages, temperature=0.1, max_tokens=512, **kw):
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        if self.boom:
            raise RuntimeError("ollama 连不上")
        return json.dumps(self.payload, ensure_ascii=False)


class _FakeReflection:
    """替掉真实反思/Router(它们要调大模型),只留 pipeline 需要的返回结构。"""

    @staticmethod
    def run_reflection(llm, rec, max_tokens=4096):
        return {"material": "材料", "text": "反思文本", "stripped_opener": False}

    @staticmethod
    def run_router(llm, text, material=""):
        return {"raw": "{}", "issues": [], "expert_pool_version": "v1"}


def _patch_backend(monkeypatch, client):
    """把映射器"真跑时自己造本地后端"这条路接到假客户端上。"""
    import mavis_case01_injector.llm as llm_mod
    monkeypatch.setattr(llm_mod, "local_client_from_env", lambda *a, **k: client)


def test_real_run_mapping_uses_stance_judge(monkeypatch, tmp_path):
    """实跑(reflect,非 dry_run)即使调用方没给 client,也必须走立场判官。"""
    client = _StanceClient()
    _patch_backend(monkeypatch, client)
    rec = run_pipeline(branch="B", raw_record=_raw(), reflect=True, dry_run=False,
                       out_path=str(tmp_path / "run.json"),
                       reflection_mod=_FakeReflection)
    assert client.calls, "立场判官一次都没被调用 —— 又在真跑记录上暗降回 quick_scan"
    cs = rec["consistency"]
    assert cs["method"] == "llm_stance", cs
    assert cs["verdict"] == "consistent", cs          # wait → B 线,与记录一致
    assert cs["stance"] == "wait"
    assert not any("立场判官未启用" in w
                   for w in (rec.get("manifest", {}).get("manifest_warnings") or []))


def test_stance_judge_failure_is_not_disguised_as_quick_scan(monkeypatch, tmp_path):
    """判官后端每次都报错 → unknown,且**底层报错原文进 reason**。

    两条红线:①不许退回 quick_scan 冒充"有结论"(method 仍是 llm_stance);
    ②不许因为"没有反证"就给 consistent —— quick_scan 这边其实判 consistent,
    分歧必须留在记录上(disagreement),而不是被主判据的失败掩盖。
    """
    _patch_backend(monkeypatch, _StanceClient(boom=True))
    rec = run_pipeline(branch="B", raw_record=_raw(), reflect=True, dry_run=False,
                       out_path=str(tmp_path / "run.json"),
                       reflection_mod=_FakeReflection)
    cs = rec["consistency"]
    assert cs["method"].startswith("llm_stance"), cs
    assert cs["verdict"] == "unknown", cs
    assert "ollama 连不上" in cs["reason"]
    assert cs["disagreement"] is True, "quick_scan 说 consistent,这个分歧必须可见"


def test_real_run_without_backend_records_the_degradation(monkeypatch, tmp_path):
    """真跑却建不出判官后端:退回 quick_scan 可以,但**必须记在记录上**并说出来。"""
    import mavis_case01_injector.llm as llm_mod

    def _boom(*a, **k):
        raise RuntimeError("本地后端配置缺失")
    monkeypatch.setattr(llm_mod, "local_client_from_env", _boom)
    rec = run_pipeline(branch="B", raw_record=_raw(), reflect=True, dry_run=False,
                       out_path=str(tmp_path / "run.json"),
                       reflection_mod=_FakeReflection)
    assert rec["consistency"]["method"] == "quick_scan"
    warns = rec.get("manifest", {}).get("manifest_warnings") or []
    assert any("consistency: 立场判官未启用" in w for w in warns), warns


def test_dry_run_still_sends_no_real_request(monkeypatch, tmp_path):
    """dry_run 且没给 client:照旧 quick_scan 诚实降级,试跑不碰大模型。"""
    def _explode(*a, **k):
        raise AssertionError("dry_run 不该构造判官后端(会发真实请求)")
    import mavis_case01_injector.llm as llm_mod
    monkeypatch.setattr(llm_mod, "local_client_from_env", _explode)
    rec = run_pipeline(branch="B", raw_record=_raw(), dry_run=True,
                       out_path=str(tmp_path / "run.json"))
    assert rec["consistency"]["method"] == "quick_scan"
    assert not any("consistency: 立场判官未启用" in w
                   for w in (rec.get("manifest", {}).get("manifest_warnings") or []))


def test_manifest_branch_mode_follows_the_run_that_actually_happened(tmp_path):
    """清单里的 branch_mode 取实跑真相(raw),不被映射器 CLI 的 preset 默认覆盖。"""
    rec = run_pipeline(branch="B", raw_record=_raw(mode="judge"), dry_run=True,
                       out_path=str(tmp_path / "run.json"))
    assert rec["manifest"]["branch_mode"] == "judge"
    assert rec["branch_action"]["source"] == "judge"


def test_manifest_branch_mode_stays_preset_for_preset_runs(tmp_path):
    """反例守住:预设对照(mode=preset)不能被改成立即判定 —— 只如实转录。"""
    rec = run_pipeline(branch="B", raw_record=_raw(mode="preset"), dry_run=True,
                       out_path=str(tmp_path / "run.json"))
    assert rec["manifest"]["branch_mode"] == "preset"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
