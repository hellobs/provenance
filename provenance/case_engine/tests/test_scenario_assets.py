# -*- coding: utf-8 -*-
"""集成冒烟:加载仓库里的真实场景文件,确保配置资产始终可加载、可校验。"""
import os

from case_engine.config import load_yaml, validated, consistency_signals
from case_engine import consistency

_CASES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "cases")


def _scenario_path(case_id: str) -> str:
    p = os.path.join(_CASES, case_id, "scenario.yaml")
    assert os.path.exists(p), "场景文件缺失: {}".format(p)
    return p


def _load(case_id: str):
    return validated(load_yaml(_scenario_path(case_id)))


def test_case01_stock_loads_and_validates():
    s = _load("case01_stock")
    assert s.case_id == "case01_stock"
    assert [r.id for r in s.roles] == ["investment_ai", "ethan"]
    assert s.state_schema["cash_rmb"]["initial"] == 200000
    assert s.reflection.get("system_prompt")
    assert len(s.consistency.get("buy_words", [])) >= 10


def test_case01_conflict_rules_present():
    s = _load("case01_stock")
    ethan = next(r for r in s.roles if r.id == "ethan")
    assert len(ethan.conflict_rules) == 5
    labels = [c["label"] for c in ethan.conflict_rules]
    assert labels == ["bought", "spent_when_no_hold",
                      "sold", "exited", "cash_mismatch"]


def test_config_drives_consistency_scan():
    """最小闭环:scenario 的 consistency 段 → 引擎 quick_scan 判定(Phase 3 首个证明)。"""
    s = _load("case01_stock")
    signals = consistency_signals(s)
    assert signals["buy_words"]          # 词表确实来自场景配置

    # A 线:AI 首轮明确积极 → consistent
    rec_a = {
        "branch": "A",
        "start_date": "2026-08-27",
        "turns": [{
            "speaker": "ai", "date": "2026-08-27",
            "text": "I recommend buying the stock now.",
        }],
    }
    verdict, _ = consistency.quick_scan(rec_a, signals)
    assert verdict == "consistent"

    # B 线:AI 首轮谨慎/拒绝 → consistent
    rec_b = {
        "branch": "B",
        "start_date": "2026-08-27",
        "turns": [{
            "speaker": "ai", "date": "2026-08-27",
            "text": "I advise against buying without official confirmation.",
        }],
    }
    verdict_b, _ = consistency.quick_scan(rec_b, signals)
    assert verdict_b == "consistent"

    # 缺词表时引擎安全回落 unknown
    from case_engine.consistency import DEFAULT_SIGNALS
    verdict_safe, _ = consistency.quick_scan(rec_a, None)
    assert verdict_safe == "unknown"


class _FakeLLM:
    """回显式假 LLM:记录收到的 prompt,返回一段反思。"""
    def __init__(self):
        self.last_messages = None

    def chat(self, messages, **kw):
        self.last_messages = messages
        return ("【反思】我基于当时可获得的证据作出了判断。"
                "后来发生的结果印证了部分判断。我会继续观察。")

    def native_chat(self, messages, **kw):
        return self.chat(messages, **kw)


def test_config_drives_reflection_generation():
    """扩充最小闭环:scenario.reflection → run_reflection 反思生成。

    验证 config 的 reflection 段被真实注入引擎(fb_asker_key/speaker_labels/prompt)
    而非引擎默认。"""
    from case_engine import reflection
    from case_engine.config import reflection_args

    s = _load("case01_stock")
    args = reflection_args(s)
    # case01 的 roles:investment_ai(ai_tool), ethan(user)
    assert args["asker_speaker"] == ("ethan",)
    assert args["speaker_labels"]["asker"] == "ethan"
    assert args["fb_asker_key"] == "ethan"
    # config 的 reflection.prompt_cn 被真实注入引擎(非引擎默认)
    assert args["reflection_prompt"] == s.reflection["prompt_cn"]
    assert s.reflection["prompt_cn"]  # 场景确实带业务稿

    llm = _FakeLLM()
    rec = {
        "start_date": "2026-08-27",
        "turns": [
            {"speaker": "ethan", "date": "2026-08-27", "text": "是否可信?"},
            {"speaker": "ai", "date": "2026-08-27", "text": "尚无官方表态。"},
        ],
        "final_feedback": {"date": "2026-09-15", "ethan": "我没有买入。", "ai": "明白了。"},
    }
    out = reflection.run_reflection(llm, rec, **args)
    assert "我已经" not in out["text"]       # 清洗生效
    assert "【反思】" in out["text"]
    # fb_asker_key=ethan 让案例设定部分真的拿到 ethan 的陈述
    assert "我没有买入" in out["material"]


def test_config_drives_branch_router():
    """扩最小闭环:scenario.branch → RuleBranchRouter 分支判定。

    验证配置的 no_buy/refuse→B、conditional/anti_allin→C、其余→default 语义被
    引擎按规则表真实路由(而非引擎硬编码)。"""
    from case_engine.branch import RuleBranchRouter
    from case_engine.config import branch_args

    s = _load("case01_stock")
    args = branch_args(s)
    assert args["judge_prompt"]            # 有判定 prompt
    assert "B" in args["rules"] and "C" in args["rules"]
    assert args["default_branch"] == "C"

    router = RuleBranchRouter(default=args["default_branch"],
                              rules=args["rules"],
                              fallback_map=args["fallback_map"])
    # 含 no_buy → B(用词表里的词,如实反映规则路由能力)
    assert router.classify("我建议你不要买这只股票") == "B"
    # 含 refuse → B
    assert router.classify("这件事我无法判断") == "B"
    # 含 conditional → C
    assert router.classify("建议先小仓位分批等待确认") == "C"
    # 不含命中词 → default(C)
    assert router.classify("这是个复杂的问题") == "C"
    # 空 → default(C)
    assert router.classify("") == "C"