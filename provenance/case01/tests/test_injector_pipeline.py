# -*- coding: utf-8 -*-
"""完整流水线（dry-run）与 Reflection 挂接的测试（不联网、不加载 mavis 时走 dry-run）。"""
import json
import os

from case01.injector.pipeline import run_pipeline
from case01.injector.record import CASE01_TOP_KEYS


def test_pipeline_dry_run_writes_mapped_record(tmp_path):
    out = tmp_path / "record.json"
    record = run_pipeline(branch="B", dry_run=True, out_path=str(out))

    assert out.exists()
    saved = json.load(open(out, encoding="utf-8"))
    assert set(CASE01_TOP_KEYS).issubset(set(saved))
    assert saved["branch"] == "B"
    assert saved["start_date"] == "2026-08-27"
    assert saved["final_feedback"]["date"] == saved["end_date"]
    # dry-run 下关键节点视为已发生交互
    assert saved["summary"]["interaction_started"] >= 2
    assert saved["compat"]["reflection_attached"] is False


def test_pipeline_attach_reflection_uses_case01_modules(tmp_path, monkeypatch):
    calls = {}

    def _fake_reflection(llm, rec, max_tokens=3072):
        calls["reflection"] = {"llm": llm, "run_id": rec.get("run_id")}
        return {"material": "材料", "text": "反思文本"}

    def _fake_router(llm, text, material=""):
        calls["router"] = {"llm": llm, "text": text}
        return {"raw": "{}", "issues": [{"summary": "s"}]}

    import case01.reflection as refl
    monkeypatch.setattr(refl, "run_reflection", _fake_reflection)
    monkeypatch.setattr(refl, "run_router", _fake_router)

    record = run_pipeline(branch="B", dry_run=True, reflect=True, llm="LLM")

    assert record["reflection"] == {"material": "材料", "text": "反思文本"}
    assert record["router"]["issues"] == [{"summary": "s"}]
    assert calls["reflection"]["llm"] == "LLM"
    assert calls["router"]["text"] == "反思文本"
    assert record["compat"]["reflection_attached"] is True
    assert not any(g.startswith("reflection/router") for g in record["compat"]["gaps"])
