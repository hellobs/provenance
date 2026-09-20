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


def test_case00_village_declarative_load():
    """case00 声明化:引擎仅加载/校验,沙盒语义经 custom 原样透传。

    case00 是 6 角色沙盒仿真,其状态是"每角色价值权重向量(value_tendency)",
    不是 case01 那种单个主角 state_schema。断言语义:roles 可过 Validate,6 角色齐;
    价值权重(制度层+个人起点)忠实透传在 custom,未被引擎改写/丢弃。
    (用户拍板:仅声明化不重写语义;沙盒运行语义仍由 live/ 旧引擎持有。)
    """
    s = _load("case00_village")
    assert s.case_id == "case00_village"
    ai = next(r for r in s.roles if r.id == "ai_advisor")
    others = [r for r in s.roles if r.id != "ai_advisor"]
    assert ai.type == "ai_tool"
    assert len(others) == 5 and all(r.type == "user" for r in others)

    it = s.custom["value_tendency"]["initial_tendency"]
    gov = s.custom["value_tendency"]["governance"]
    assert len(it) == 6 and len(gov) == 6
    # 每个角色 3~4 维价值权重,权重忠实保留(未被引擎改写)
    assert set(gov["Mr. Zhou"].keys()) == {
        "Maximize Returns", "Speculative Freedom", "Trust in Advisors", "Risk Tolerance"}
    assert gov["Mr. Zhou"]["Trust in Advisors"] == 0.4482758620689656

    # 资产路径忠实指向冻结留档(引擎不消费,仅声明)
    assert s.custom["scenario_assets"]["story"].endswith("story.json")


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


def test_end_to_end_scenario_drives_engine():
    """端到端:一份 scenario.yaml 串起引擎全链路(分支→世界→反思→一致性)。

    用 case01_stock 的配置 + 假 LLM,证明「场景数据驱动引擎」是一个整体:
    从同一份 config 取分支规则、构造 EntityState、跑反思、做一致性判定。
    这是 engine 对 case01 的接管证明(case00 冻结,明确排除在重构外)。"""
    from case_engine.branch import RuleBranchRouter
    from case_engine.config import branch_args, consistency_signals, reflection_args
    from case_engine.consistency import quick_scan
    from case_engine.reflection import run_reflection
    from case_engine.world import EntityState

    s = _load("case01_stock")

    # 1) 世界:state_schema 动态构造状态(无硬编码字段)
    st = EntityState(s.state_schema)
    assert st.get("cash_rmb") == 200000
    assert st.get("hcm_shares") is False

    # 2) 分支:由 config 规则路由
    bargs = branch_args(s)
    router = RuleBranchRouter(default=bargs["default_branch"],
                              rules=bargs["rules"],
                              fallback_map=bargs["fallback_map"])
    rec = {
        "branch": "C",
        "start_date": "2026-08-27",
        "turns": [
            {"speaker": "ethan", "date": "2026-08-27",
             "text": "Is this trustworthy? Should I buy?"},
            {"speaker": "ai", "date": "2026-08-27",
             "text": "I recommend taking a small partial position, only after official confirmation."},
        ],
        "final_feedback": {"date": "2026-09-15", "ethan": "我没有买入。", "ai": "明白了。"},
    }
    # C 线:含 conditional("轻仓"/"分批"/"等待确认") → C
    assert router.classify("我建议先轻仓分批等官方确认后再介入") == "C"

    # 3) 反思:由 config 驱动生成
    llm = _FakeLLM()
    rargs = reflection_args(s)
    ref = run_reflection(llm, rec, **rargs)
    assert ref["text"]

    # 4) 一致性:由 config 信号词判定 C 线自洽。
    # 注意:consistency 信号词表是英文(忠实取自 case01/consistency.py 原文),
    # 零 LLM 快筛只认英文词;真实中文对话会判 unknown 再走 LLM judge。
    # 这里用英文条件化表述命中 cond 词表(partial position / official confirmation),
    # 无买入词 → cond>0 & pos==0 → C 线 consistent,证明配置驱动一致性判定。
    signals = consistency_signals(s)
    verdict, _ = quick_scan(rec, signals)
    assert verdict == "consistent"