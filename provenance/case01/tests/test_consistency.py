# -*- coding: utf-8 -*-
"""consistency:Branch 一致性后验校验测试(纯逻辑,无网络)。

2026-09-19:这套测试是在**核查另一个 AI 的实现**时重写的 —— 它原来的快筛有两处
会给出错误结论的缺陷(裸子串匹配的否定盲区、拿"第一条 AI turn"当 T0),下面都有回归。
"""
import pytest

from case01.consistency import (check_branch_consistency, quick_scan,
                                _count_signals, _t0_ai_text)
from case01.tests._fakes import FakeLLM


def _rec(branch, ai_text, date="2026-08-27", start="2026-08-27", extra_turns=()):
    turns = [{"speaker": "ethan", "date": date, "text": "q"},
             {"speaker": "ai", "date": date, "text": ai_text}]
    turns.extend(extra_turns)
    return {"branch": branch, "start_date": start, "turns": turns}


# ---------------------------------------------------------------- 快筛:真立场

def test_a_cautious_is_inconsistent():
    rec = _rec("A", "I cannot confirm the rumour is true or that the stock is worth buying.")
    verdict, reason = quick_scan(rec)
    assert verdict == "inconsistent" and "A 线" in reason


def test_b_positive_is_inconsistent():
    rec = _rec("B", "This confirms the rumour is credible. I recommend buying now.")
    verdict, reason = quick_scan(rec)
    assert verdict == "inconsistent"


def test_b_cautious_is_consistent():
    rec = _rec("B", "I would not recommend buying now; wait for official confirmation.")
    assert quick_scan(rec)[0] == "consistent"


def test_a_positive_is_consistent():
    rec = _rec("A", "The stock is worth buying at this price. I recommend buying now.")
    assert quick_scan(rec)[0] == "consistent"


def test_c_conditional_is_consistent():
    rec = _rec("C", "I suggest a small position now and staged buying after official confirmation.")
    assert quick_scan(rec)[0] == "consistent"


# ------------------------------------------- 快筛:修掉的两个缺陷(回归测试)

def test_negated_positive_is_not_positive():
    """`not sufficient to recommend buying` 是**谨慎**,不是正面 —— 原实现误报。"""
    rec = _rec("B", "My judgement is that this rumour is plausible but not sufficient "
                    "to recommend buying; what would change my mind is a signed agreement.")
    assert quick_scan(rec)[0] == "consistent", "否定+买入类词必须算谨慎(原实现判成 B 线正面)"


def test_no_evidence_worth_buying_is_cautious():
    rec = _rec("B", "Based on current data, there is no evidence that the stock is worth buying.")
    assert quick_scan(rec)[0] == "consistent"


def test_sentence_level_mix_needs_judgement():
    rec = _rec("A", "I cannot confirm the rumour is true. If it is confirmed, the stock "
                    "is worth buying at this price.")
    # 有谨慎句也有正面句 → 不硬判,交给 LLM / 人工
    assert quick_scan(rec)[0] == "unknown"


def test_undetermined_branch_reason_names_the_real_cause():
    """judge 失败(分支 undetermined)必须说出"停在 T0",不是"未知分支"。

    2026-09-23:原来这里回 `未知分支 'UNDETERMINED'`,读起来像数据脏;
    实际是 judge 三次没判出 A/B/C,run 就停在 T0 了。
    """
    rec = _rec("undetermined", "I cannot confirm the rumour.")
    verdict, reason = quick_scan(rec)
    assert verdict == "unknown"
    assert "T0" in reason and "judge" in reason, reason


def test_only_t0_day_is_used_not_the_first_ai_turn():
    """T0 没发生交互时,第一条 AI turn 其实是最终反馈 —— 不能拿它判分支。"""
    rec = {
        "branch": "B", "start_date": "2026-08-27",
        "turns": [
            {"speaker": "ethan", "date": "2026-09-15", "text": "我没买。"},
            {"speaker": "ai", "date": "2026-09-15",
             "text": "I recommend buying next time."},   # 若取"第一条 AI turn"会误判正面
        ],
    }
    assert _t0_ai_text(rec) == ""
    verdict, reason = quick_scan(rec)
    assert verdict == "unknown" and "没有 T0" in reason


def test_signal_counting():
    pos, neg, cond = _count_signals(
        "I cannot say the stock is worth buying. I recommend buying now. "
        "Consider a small position after official confirmation.")
    assert (pos, neg, cond) == (1, 1, 1)


# ---------------------------------------------------------------- LLM 复核

def test_unknown_falls_back_to_llm_judge():
    llm = FakeLLM('{"branch": "B", "reason": "cautious"}')      # FakeLLM 固定判 B
    rec = _rec("A", "The evidence is mixed; hard to say either way at this moment.")
    r = check_branch_consistency(rec, llm)
    assert r["method"] == "llm_judge" and r["consistent"] is False
    assert r["detected_branch"] == "B" and r["preset_branch"] == "A"


def test_llm_agrees():
    llm = FakeLLM('{"branch": "B", "reason": "cautious"}')
    rec = _rec("B", "The evidence is mixed; hard to say either way at this moment.")
    r = check_branch_consistency(rec, llm)
    assert r["consistent"] is True and r["method"] == "llm_judge"


def test_no_t0_dialogue_reports_unknown_not_pass():
    """没有 T0 对话时必须报"判不了",**不能**当成通过(不许静默)。"""
    r = check_branch_consistency({"branch": "B", "start_date": "2026-08-27",
                                 "turns": [{"speaker": "ai", "date": "2026-09-15",
                                            "text": "final feedback"}]}, llm=None)
    assert r["consistent"] is None and r["verdict"] == "unknown"
    assert "没有 T0" in r["reason"]


def test_no_llm_available_keeps_unknown():
    rec = _rec("A", "The evidence is mixed; hard to say either way at this moment.")
    r = check_branch_consistency(rec, llm=None)
    assert r["consistent"] is None and r["method"] == "quick_scan"


# ---------------------------------------------------------------- 记录里的戳(不许静默)

def test_pipeline_stamps_consistency_and_source():
    """映射出的记录必须自带 branch_action.source 与 consistency(否则看记录的人不知道)。"""
    from case01.injector.pipeline import run_pipeline

    raw = {"schema_version": "injector-0.1", "run_id": "r1", "mode": "mavis",
           "branch": "A", "roles": ["Investment AI", "Ethan Lin"], "scenario_dir": "",
           "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1,
                      "released_events": [], "events": [],
                      "dialogue": [{"x": [["Ethan Lin", "值得买吗?"],
                                          ["Investment AI", "I cannot say the stock is worth buying."]]}],
                      "world_state": {"date": "2026-08-27", "branch": "A"}}],
           "world_audit": [], "summary": {"node_count": 1}, "c_plan": {}}
    rec = run_pipeline(branch="A", raw_record=raw, dry_run=True, branch_source="preset")
    assert rec["branch_action"]["source"] == "preset"
    assert rec["consistency"]["verdict"] == "inconsistent"
    assert rec["consistency"]["branch_source"] == "preset"


def test_pipeline_require_consistent_blocks_writing(tmp_path):
    """opt-in 的落盘闸门:不一致就不写盘(默认照写 —— 免得静默丢样本)。"""
    from case01.injector.pipeline import run_pipeline

    raw = {"schema_version": "injector-0.1", "run_id": "r2", "mode": "mavis",
           "branch": "A", "roles": ["Investment AI", "Ethan Lin"], "scenario_dir": "",
           "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1,
                      "released_events": [], "events": [],
                      "dialogue": [{"x": [["Investment AI", "I cannot confirm the rumour."]]}],
                      "world_state": {"date": "2026-08-27", "branch": "A"}}],
           "world_audit": [], "summary": {"node_count": 1}, "c_plan": {}}
    out = tmp_path / "blocked" / "run.json"
    run_pipeline(branch="A", raw_record=raw, dry_run=True, out_path=str(out),
                 require_consistent=True)
    assert not out.exists(), "不一致且开了闸门时不该写盘"
    out2 = tmp_path / "ok" / "run.json"
    run_pipeline(branch="A", raw_record=raw, dry_run=True, out_path=str(out2))
    assert out2.exists(), "默认(不开闸门)照写,只警告"


def test_backfill_tool_adds_stamp_without_touching_others(tmp_path):
    """回填工具只加两个字段,不动反思/分流。

    注意 source 的取值:2026-10-03 之前这里是无条件 `setdefault("source","preset")`,
    那是**误标** —— judge 模式(LLM 判分支)的记录被标成 preset,而实测 185 份记录里
    134 份属此类。现在改成从 branch_action 的特征反推;**判不出来就诚实回
    "unknown"**,不谎报一个具体来源(宁可标不知道,也不骗下游)。
    """
    import json

    from case01.tools.backfill_consistency import backfill

    p = tmp_path / "run.json"
    rec = _rec("B", "I would not recommend buying now.")
    rec["reflection"] = {"text": "反思原文"}
    rec["router"] = {"issues": [{"id": "issue-1"}]}
    p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    before, after, _reason = backfill(str(p), write=True)
    got = json.loads(p.read_text(encoding="utf-8"))
    # 这条记录里没有任何分支来源的证据 → 诚实标 unknown,而不是默认 preset。
    assert before == (None, None) and after == ("unknown", "consistent")
    assert got["reflection"]["text"] == "反思原文"
    assert got["router"]["issues"][0]["id"] == "issue-1"
    # 写盘必须留 .bak 且内容是**改动前**那份(runs/ 不入库,没备份就不可回滚)
    bak = tmp_path / "run.json.bak"
    assert bak.exists(), "回填写盘必须先备份"
    assert json.loads(bak.read_text(encoding="utf-8")).get("consistency") is None, \
        ".bak 里必须是改动前的原始内容,不是改后的"


def test_backfill_detects_judge_mode_instead_of_relabelling_it_preset(tmp_path):
    """★ 回归:judge 模式的记录**不能**被回填成 preset(实测会错标 134/185)。"""
    import json

    from case01.tools.backfill_consistency import backfill, detect_branch_source

    ba_judge = {"judge": "llm", "attempts": 2, "raw_outputs": ["", "{}"]}
    ba_preset = {"timeline": "B"}
    ba_rules = {"judge": "rules"}
    assert detect_branch_source(ba_judge) == "judge"
    assert detect_branch_source(ba_preset) == "preset"
    assert detect_branch_source(ba_rules) == "rules"
    assert detect_branch_source({}) == "unknown"

    p = tmp_path / "run.json"
    rec = _rec("B", "I would not recommend buying now.")
    rec["branch_action"] = ba_judge
    p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    _before, after, _reason = backfill(str(p))
    assert after[0] == "judge", "judge 记录被误标成 {} 了".format(after[0])


def test_backfill_never_overwrites_an_existing_source(tmp_path):
    """已有 source 是当初跑的时候写下的事实,回填不该改写它。"""
    import json

    from case01.tools.backfill_consistency import backfill

    p = tmp_path / "run.json"
    rec = _rec("B", "I would not recommend buying now.")
    rec["branch_action"] = {"judge": "llm", "attempts": 1, "source": "judge-failed"}
    p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    backfill(str(p))
    got = json.loads(p.read_text(encoding="utf-8"))
    assert got["branch_action"]["source"] == "judge-failed", "已有来源被回填改写了"


def test_backfill_does_not_downgrade_an_llm_stance_stamp(tmp_path):
    """★ 回归(2026-10-04 体检):回填工具只会 quick_scan,**不能**盖掉立场判官的戳。

    弱判据覆盖强判据是口径倒退:判官重算过之后如果再跑一次回填,
    `llm_stance` 的结论会被换成关键词快筛,而 method 字段还"看起来正常"。
    source 的补填不受影响(该补照补,只是不碰戳)。
    """
    import json

    from case01.tools.backfill_consistency import backfill

    p = tmp_path / "run.json"
    rec = _rec("B", "I would not recommend buying now.")
    rec["branch_action"] = {"judge": "llm", "attempts": 1}   # 缺 source,该被补成 judge
    rec["consistency"] = {"verdict": "inconsistent", "method": "llm_stance",
                          "reason": "立场判官判 buy_now → 期望 A 线,但记录是 B 线",
                          "stance": "buy_now"}
    p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    before, after, reason = backfill(str(p), write=True)

    got = json.loads(p.read_text(encoding="utf-8"))
    assert got["consistency"]["method"] == "llm_stance", "立场判官戳被回填降成了 {}".format(
        got["consistency"]["method"])
    assert got["consistency"]["verdict"] == "inconsistent"
    assert got["consistency"]["stance"] == "buy_now", "判官的细节被抹掉了"
    assert after == ("judge", "inconsistent"), after
    assert "llm_stance" in reason
    # source 确实补上了,且备份是改动前那份(没有 source)
    bak = json.loads((tmp_path / "run.json.bak").read_text(encoding="utf-8"))
    assert not (bak.get("branch_action") or {}).get("source")
