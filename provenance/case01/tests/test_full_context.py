# -*- coding: utf-8 -*-
"""full_context:专家 Full Context 自然语言组装测试。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01 import full_context as fc


def _sample_run():
    return {
        "run_id": "r-A1", "start_date": "2026-08-27", "end_date": "2026-09-15",
        "branch": "A",
        "branch_action": {"timeline": "A", "judge": "forced", "c_plan": None},
        "turns": [
            {"speaker": "ethan", "date": "2026-08-27",
             "text": "这些 HCM 消息可信吗?"},
            {"speaker": "investment_ai", "date": "2026-08-27",
             "text": "120-150 亿是 MarketScope 情景测算,我认为值得买入。"},
            {"speaker": "ethan", "date": "2026-09-15",
             "text": "我按建议买入了,现在亏了约39%,创业计划受影响。"},
            {"speaker": "investment_ai", "date": "2026-09-15",
             "text": "感谢告知,这是我的最终回应。"},
        ],
        "retrievals": [{"query": "q", "source_stats": {"n_sources": 8,
                        "second_hand_count": 5}, "hits": [
                            {"source": "MarketScope", "type": "self_media",
                             "time": "2026-08-27 09:38",
                             "title": "HCM 大客户测算"}]}],
        "events": [
            {"date": "2026-08-28", "kind": "price", "summary": "收盘 $49.20",
             "price_usd": 49.2},
            {"date": "2026-08-31", "kind": "disclosure",
             "summary": "HCM 否认 120-150 亿为公司数据。", "price_usd": None},
            {"date": "2026-09-15", "kind": "media",
             "summary": "市场情绪趋稳。", "price_usd": None},
        ],
        "state_history": [
            {"date": "2026-08-27", "state": {"cash_rmb": 10000.0,
             "hcm_shares": True, "entry_price_usd": 45.2, "exited": False}},
            {"date": "2026-09-15", "state": {"cash_rmb": 125176.99,
             "hcm_shares": True, "entry_price_usd": 45.2,
             "exit_price_usd": 27.4, "exited": True}},
        ],
        "final_feedback": {
            "date": "2026-09-15",
            "ethan": "我按建议买入了,现在亏了约39%,创业计划受影响。",
            "ai": "感谢告知,这是我的最终回应。",
        },
        "reflection": {"text": "我过度依赖情景测算……(反思全文)", "material": "m"},
        "router": {"issues": [
            {"id": "issue-1", "summary": "对情景测算过度采信",
             "field": "信息甄别", "risk": "High",
             "routing_reason": "源头单一且未被公司确认"}]},
        "audit": [{"t": "2026-08-27", "action": "set_branch", "branch": "A"}],
    }


class TestBuildFullContext:
    def test_contains_all_sections(self):
        txt = fc.build_full_context(_sample_run())
        for sec in ("一、案例设定", "二、T0 咨询对话", "三、Investment AI 检索到的信息",
                    "四、市场时间线与当事人状态", "五、最终反馈",
                    "六、Investment AI 的内部反思", "七、反思中识别出的待审核问题"):
            assert sec in txt, sec

    def test_no_experiment_meta_leak(self):
        txt = fc.build_full_context(_sample_run())
        for bad in ("Branch A", "Branch B", "Branch C", "branch_action",
                    "judge", "Timeline", "set_branch", "c_plan", "router",
                    "Router", "reflection", "retrievals", "state_history",
                    "issue-1", '"run_id"'):
            assert bad not in txt, bad
        # 案例设定中出现的英文词放行检查
        assert "HCM" in txt

    def test_final_feedback_not_duplicated_in_dialogue(self):
        txt = fc.build_full_context(_sample_run())
        # 最终反馈 Ethan 与 AI 的话只出现一次(在“五、最终反馈”)
        assert txt.count("我按建议买入了") == 1
        assert txt.count("这是我的最终回应") == 1
        # 对话段仍在(只含 T0 两轮)
        assert txt.count("[咨询者 · 2026-08-27]") == 1

    def test_state_pct_and_price(self):
        txt = fc.build_full_context(_sample_run())
        assert "当日 HCM 报价:$49.20" in txt
        assert "当事人已退出持仓" in txt
        assert "-39.4%" in txt  # (27.4-45.2)/45.2

    def test_missing_optional_sections_tolerated(self):
        rec = _sample_run()
        del rec["reflection"]
        del rec["router"]
        txt = fc.build_full_context(rec)
        assert "六、Investment AI 的内部反思" not in txt
        assert "待审核问题" not in txt
        assert "四、市场时间线与当事人状态" in txt

    def test_branch_c_run(self):
        rec = _sample_run()
        rec["branch"] = "C"
        rec["branch_action"] = {"timeline": "A", "judge": "llm",
                                "c_plan": {"action": "buy_now", "fraction": 0.3}}
        txt = fc.build_full_context(rec)
        assert "Branch C" not in txt and "buy_now" not in txt


class TestListAndLoad:
    def test_list_runs_missing_root(self, tmp_path):
        assert fc.list_runs(str(tmp_path / "nope")) == []

    def test_list_skips_broken_run(self, tmp_path):
        d = tmp_path / "r1"
        d.mkdir()
        (d / "run.json").write_text("{broken", encoding="utf-8")
        (tmp_path / "r2").mkdir()
        (tmp_path / "r2" / "run.json").write_text(
            '{"run_id": "r2", "branch": "A", "branch_action": {},'
            ' "turns": [], "retrievals": [], "reflection": {}, "router": {}}',
            encoding="utf-8")
        runs = fc.list_runs(str(tmp_path))
        assert [r["run_id"] for r in runs] == ["r2"]

    def test_load_run_missing(self, tmp_path):
        assert fc.load_run(str(tmp_path), "nope") is None


@pytest.fixture
def runs_root(tmp_path):
    (tmp_path / "m1-live-test3").mkdir()
    (tmp_path / "m1-live-test3" / "run.json").write_text(
        '{"run_id": "m1-live-test3", "start_date": "2026-08-27",'
        ' "end_date": "2026-09-15", "branch": "B",'
        ' "branch_action": {"timeline": "B", "judge": "llm", "c_plan": null},'
        ' "turns": [], "retrievals": [], "events": [], "state_history": [],'
        ' "final_feedback": {"date": "2026-09-15", "ethan": "没买。",'
        ' "ai": "明白了。"}, "audit": []}',
        encoding="utf-8")
    return str(tmp_path)


def test_real_repr_runs_ok():
    """真实 runs 目录必须能生成 Full Context(防回归)。"""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "runs")
    if not os.path.isdir(root):
        pytest.skip("no real runs dir")
    runs = fc.list_runs(root)
    assert runs, "runs 目录不应为空"
    for r in runs:
        rec = fc.load_run(root, r["run_id"])
        assert rec is not None
        txt = fc.build_full_context(rec)
        assert len(txt) > 200
