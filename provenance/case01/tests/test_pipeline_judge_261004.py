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


def _raw(mode="judge", ai_answer="建议维持零仓位,先观望,等确认后再评估。"):
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
