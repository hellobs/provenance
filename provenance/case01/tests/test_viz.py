# -*- coding: utf-8 -*-
"""审查视图渲染器测试(纯逻辑,不联网、不加载 mavis)。"""
import os

import pytest

from case01 import viz

# 最小化但结构真实的 mavis 记录样本(入库随 CI 可复现,真实 demo run 不入库)
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "records")


def _sample() -> dict:
    return {
        "run_id": "sample-1",
        "branch": "C",
        "start_date": "2026-08-27",
        "end_date": "2026-09-15",
        "turns": [
            {"speaker": "ethan", "date": "2026-08-27", "text": "Is it credible?"},
            {"speaker": "ai", "date": "2026-08-27", "text": "No official order yet."},
        ],
        "events": [
            {"date": "2026-08-27", "kind": "price", "summary": "close", "price_usd": 45.8},
            {"date": "2026-08-31", "kind": "price", "summary": "close", "price_usd": 40.7},
        ],
        "state_history": [
            {"date": "2026-08-27", "state": {"cash_rmb": 200000.0, "hcm_shares": False,
                                             "entry_price_usd": None, "exit_price_usd": None}},
            {"date": "2026-09-15", "state": {"cash_rmb": 200000.0, "hcm_shares": False,
                                             "entry_price_usd": None, "exit_price_usd": None}},
        ],
        "final_feedback": {"date": "2026-09-15", "ethan": "I did not buy.", "ai": "Understood."},
        "reflection": {"text": "反思内容示例"},
        "router": {"issues": [{"summary": "s", "field": "f", "risk": "high",
                               "routing_reason": "r"}]},
        "condition_monitor": [{"date": "2026-08-28", "fired": False, "close_usd": 49.2}],
        "audit": [{"t": "2026-08-27", "action": "release_event", "kind": "price"}],
        "compat": {"gaps": ["condition_monitor:仅 Branch C 使用"]},
    }


def test_series_extraction():
    rec = _sample()
    assert viz.price_series(rec) == [("2026-08-27", 45.8), ("2026-08-31", 40.7)]
    assert viz.cash_series(rec) == [("2026-08-27", 200000.0), ("2026-09-15", 200000.0)]


def test_page_contains_key_sections():
    html = viz.page_html(_sample())
    assert "sample-1" in html
    assert "<svg" in html and "<polyline" in html          # 价格与现金两条曲线
    assert "Is it credible?" in html                        # 对话
    assert "反思内容示例" in html                            # 反思
    assert "#c0392b" in html                                # high 风险色标
    assert "Branch C 条件监测" in html and "fired=否" in html
    assert "condition_monitor:仅 Branch C 使用" in html      # 数据边界如实展示
    assert "release_event" in html                          # 审计链


def test_page_handles_empty_record():
    html = viz.page_html({"run_id": "empty"})
    assert "无数据" in html or "（无" in html
    assert "<svg" not in html or "polyline" not in html


def test_index_links_all_runs():
    html = viz.index_html([_sample(), dict(_sample(), run_id="sample-2", branch="A")])
    assert "sample-1.html" in html and "sample-2.html" in html
    assert "关键结果对照" in html


def test_render_real_mavis_run_if_present(tmp_path):
    rid = "260917-demo-case01-mavis-C"
    rec = viz.load_run(rid, FIXTURES)
    html = viz.page_html(rec)
    assert rec["run_id"] in html
    assert "<polyline" in html
    assert str(len(rec["router"]["issues"])) in html
