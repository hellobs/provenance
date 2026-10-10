# -*- coding: utf-8 -*-
"""一致性判据改造的守卫测试(2026-10-03,批次 261003-192735 体检后定案)。

改造要解决两件事,都是体检实测出来的:

**D1 词表只认英文。** `_BUY_WORDS`/`_COND_WORDS`/`_NEGATORS`/`_NEG_PHRASES`
全是英文,而 qwen3:8b 的 T0 回答是中文 → pos/neg/cond 常年 0/0/0 → verdict 恒
`unknown` → 平台侧 19/21 判 unverified。历史 25 条基线之所以正常,是因为 4b
答的是英文(实测样例 0% 中文字符)。

**D2 子串误匹配,而且在持续丢样本。** `_BUY_WORDS` 里的 `invest` 会命中语料里的
推特账号名 `@LenaInvests`。实测 21 条里 2 条(018/021)因此被判 `inconsistent`
→ `quality=questionable` → `serve.py` 默认从 `/api/runs` 排除 → **样本对平台隐身**。
被误判的那句原文恰恰是警告:
"社交媒体的乐观言论(如@LenaInvests)可能加剧短期波动,需警惕追高风险"。

**定案(用户拍板):立场判官为主 + quick_scan 作独立第二判据,两法分歧写进 reason。**

为什么必须是"立场判官"而不是直接复用 `LLMBranchJudge`:judge 模式记录的分支
**本身就是** `LLMBranchJudge` 判出来的(`branch_action.source="judge"`)。拿同一个
判官、同一套 `JUDGE_PROMPT`、同一段文本再问一遍,等于自己问自己、自己判自己,几乎
必然回 `consistent` —— 那比 `unknown` 更糟,因为 `consistent` 会被平台当成通过凭证。
立场判官问的是**另一个问题**(AI 对买入的立场是什么,不是该归到哪个分支),
所以才是真交叉校验。`test_stance_prompt_is_not_the_branch_prompt` 钉住这一点。

实现时必须守住的既有契约(来自 case01/tests/test_consistency.py,别改坏)
--------------------------------------------------------------
1. **`check_branch_consistency` 原样保留**。它是公开函数,已有四条测试钉住
   `method == "llm_judge"` 那套语义(quick_scan 先筛、unknown 才上 LLM)。
   新判据走新增的 `judge_consistency`,不去动它。
2. **`_count_signals` 改匹配方式后,`test_signal_counting` 那个精确断言
   `(1, 1, 1)` 必须仍然成立**。所以:ASCII 词用**前缀词边界** `\\b{w}`
   (保住 investing/investment 这类派生词),非 ASCII(中文)词用普通子串
   —— 中文没有词边界,加边界会把词全弄坏。
3. **`run_pipeline` 只能把显式的 `router_llm` 传下去,不能传它内部
   `OllamaClient()` 自动构造的那个**。否则
   `test_pipeline_stamps_consistency_and_source` 与
   `test_pipeline_require_consistent_blocks_writing` 这两个 dry_run 测试
   会在单测里发起真实 LLM 请求(变慢 + 引入网络依赖)。
4. **没有 T0 对话 / 判官报错时,一律落 `unknown`,绝不能因为"没证据"放行**。
5. `run_case01` 与 `run_pipeline` 两条落盘路径都要把判官 client 传进
   `attach_consistency`,否则新判据在这两条路上都不会生效。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 这份测试是**先写规格、后补实现**的:立场判官(STANCE_PROMPT / LLMStanceJudge /
# judge_consistency)还没有落地。pytest 收集期报 ImportError 会**中断整个测试套件**
# (不是只跳过本文件),那会让"一条命令跑完全绿"直接不可用 —— 所以这里显式跳过,
# 并把缺什么写在消息里。实现一落地,这个 skip 自动失效,全部用例立刻开始生效。
try:
    from case01.consistency import (  # noqa: E402
        STANCE_TO_BRANCH, attach_consistency, judge_consistency,
        _count_signals, quick_scan)
    from case01.world.branch import (  # noqa: E402
        JUDGE_PROMPT, STANCE_PROMPT, LLMStanceJudge)
except ImportError as exc:                                  # pragma: no cover
    pytest.skip(
        "一致性判据改造尚未实现(批次 261003-192735 跑完中,期间不得改 case01 源码):"
        "缺 {}".format(exc),
        allow_module_level=True)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
class _StubLLM:
    """按预设序列返回内容;记录每次调用的 max_tokens。"""

    def __init__(self, responses):
        self._r = list(responses)
        self.calls = 0
        self.seen = []

    def chat(self, messages, temperature=0.7, max_tokens=1024, **kw):
        self.calls += 1
        self.seen.append({"max_tokens": max_tokens, "system": messages[0]["content"]})
        return self._r[min(self.calls - 1, len(self._r) - 1)]


class _BoomLLM:
    def chat(self, *a, **kw):
        raise RuntimeError("ollama gone")


def _rec(branch, ai_text):
    """造一条最小记录:T0 当天有一轮 AI 回答。"""
    return {
        "run_id": "t-" + branch,
        "branch": branch,
        "start_date": "2026-08-27",
        "turns": [
            {"speaker": "ethan", "date": "2026-08-27", "text": "值得买吗?"},
            {"speaker": "investment_ai", "date": "2026-08-27", "text": ai_text},
        ],
    }


ZH_BUY = "建议现在重仓买入该股,目标价看至60美元。"
ZH_WAIT = "目前不宜买入,建议维持零仓位观望,等公告确认后再评估。"
ZH_COND = "建议待正式合同签署后再分批建仓,条件满足时买入不超过20%仓位。"
ZH_HANDLE = "社交媒体的乐观言论(如@LenaInvests)可能加剧短期波动,需警惕追高风险。"


# ===========================================================================
# 1. 反同义反复:立场判官必须问另一个问题
# ===========================================================================
def test_stance_prompt_is_not_the_branch_prompt():
    """钉死:立场判官 ≠ 分支判官。复用同一套提示词就退化成橡皮章了。"""
    assert STANCE_PROMPT != JUDGE_PROMPT
    assert STANCE_PROMPT.strip() and JUDGE_PROMPT.strip()


def test_stance_prompt_asks_about_stance_not_about_branch():
    """立场判官问的是"买不买",不是"归到哪条线"——这才是独立的那个问题。"""
    assert "立场" in STANCE_PROMPT
    assert "buy_now" in STANCE_PROMPT and "conditional" in STANCE_PROMPT
    assert "unclear" in STANCE_PROMPT


def test_stance_judge_outputs_one_of_four_stances():
    for stance, raw in (
        ("buy_now", '{"stance":"buy_now","reason":"建议立即重仓"}'),
        ("wait", '{"stance":"wait","reason":"维持零仓位"}'),
        ("conditional", '{"stance":"conditional","reason":"待签约后分批"}'),
        ("unclear", '{"stance":"unclear","reason":"没说清当前动作"}')):
        got, info = LLMStanceJudge(_StubLLM([raw])).judge("任意回答")
        assert got == stance, (got, info)
        assert info["judge"] == "llm"


def test_stance_judge_retries_and_falls_back_to_unclear():
    """空/坏输出要重试;试完仍不行返回 unclear,**不许瞎猜**。"""
    j = LLMStanceJudge(_StubLLM(["", "   ", "还是不行"]), max_attempts=3)
    got, info = j.judge("任意回答")
    assert got == "unclear", (got, info)
    assert info["attempts"] == 3
    assert info.get("raw_outputs"), "原始输出要留着复盘(不允许静默)"


def test_stance_judge_does_not_guess_on_garbage():
    """返回了合法 JSON 但 stance 不在枚举里 → unclear,不许就近归类。"""
    got, _ = LLMStanceJudge(_StubLLM(['{"stance":"bullish","reason":"看涨"}]'])).judge("x")
    assert got == "unclear"


def test_stance_to_branch_covers_exactly_abc():
    assert STANCE_TO_BRANCH == {"buy_now": "A", "wait": "B", "conditional": "C"}


# ===========================================================================
# 2. 立场判官为主:verdict 由它决定
# ===========================================================================
def test_consistent_when_llm_stance_matches_branch():
    for branch, stance, raw in (
        ("A", "buy_now", '{"stance":"buy_now","reason":"立即重仓"}'),
        ("B", "wait", '{"stance":"wait","reason":"维持零仓位"}'),
        ("C", "conditional", '{"stance":"conditional","reason":"待签约后分批"}')):
        out = judge_consistency(_rec(branch, ZH_WAIT), llm=_StubLLM([raw]))
        assert out["verdict"] == "consistent", (branch, out)
        assert out["method"].startswith("llm_stance"), out


def test_inconsistent_when_llm_stance_contradicts_branch():
    out = judge_consistency(
        _rec("B", ZH_WAIT), llm=_StubLLM(['{"stance":"buy_now","reason":"立即重仓"}']))
    assert out["verdict"] == "inconsistent", out
    assert "buy_now" in out["reason"], out


def test_unclear_stance_yields_unknown_not_consistent():
    """判官说"unclear"时必须回 unknown —— 绝不能因为"没证据"就放行。"""
    out = judge_consistency(_rec("B", ZH_WAIT), llm=_StubLLM([ '{"stance":"unclear","reason":"说不清"}']))
    assert out["verdict"] == "unknown", out


def test_quick_scan_kept_as_second_opinion():
    """第二判据必须真的跑了并把结果带出来(否则"两法分歧"无从谈起)。"""
    out = judge_consistency(_rec("B", ZH_WAIT), llm=_StubLLM(['{"stance":"wait","reason":"观望"}']))
    assert "quick_scan" in out, out
    assert out["quick_scan"]["verdict"] in ("consistent", "inconsistent", "unknown")


def test_disagreement_is_surfaced_in_reason():
    """★ 两法分歧必须写进 reason —— 不许悄悄挑一个信。

    构造:正文里有明确的正面信号(重仓),quick_scan 判 C 线"明确支持买入、
    无条件化方案" → inconsistent;而立场判官读完整段后判"条件化参与" → consistent。
    两法真打起来了,以立场判官(主判据)定 verdict,但分歧必须留痕。
    """
    out = judge_consistency(
        _rec("C", ZH_BUY), llm=_StubLLM(['{"stance":"conditional","reason":"分批参与"}']))
    assert out["quick_scan"]["verdict"] == "inconsistent", out["quick_scan"]
    assert out["verdict"] == "consistent", "立场判官是主判据,verdict 跟它"
    assert out["disagreement"] is True, "分歧要被标记出来"
    assert "disagree" in out["reason"], out["reason"]
    assert "quick_scan" in out["reason"], out["reason"]


def test_agreement_produces_no_disagreement_flag():
    out = judge_consistency(_rec("B", ZH_WAIT), llm=_StubLLM(['{"stance":"wait","reason":"观望"}']))
    assert out["disagreement"] is False, out


def test_no_llm_falls_back_to_quick_scan_and_says_so():
    """没有 LLM(降级/纯规则跑)时退回 quick_scan,且 method 必须如实写。"""
    out = judge_consistency(_rec("B", ZH_WAIT), llm=None)
    assert out["method"] == "quick_scan", out
    assert "stance" not in out or out.get("stance") is None


def test_llm_crash_does_not_silently_become_consistent():
    """★ 判官炸了不许静默放行 —— 必须落成 unknown 并留下原因。"""
    out = judge_consistency(_rec("B", ZH_WAIT), llm=_BoomLLM())
    assert out["verdict"] == "unknown", out
    assert "ollama gone" in out["reason"], out["reason"]


# ===========================================================================
# 3. D2:子串误匹配(正在丢样本的那个)
# ===========================================================================
def test_social_handle_is_not_a_buy_signal():
    """★ @LenaInvests 里的 Invest 不是"支持买入"。这句话本身是警告追高。"""
    pos, neg, cond = _count_signals(ZH_HANDLE)
    assert pos == 0, "推特账号名被当成了买入信号(实测导致 2/21 样本被判 questionable 隐身)"


def test_real_english_buy_words_still_count():
    """修词边界不能把真的买入信号一起修没了。"""
    pos, _, _ = _count_signals("I would recommend buying this stock now.")
    assert pos >= 1, "英文买入信号必须仍然命中"


def test_english_verb_forms_still_match_after_boundary_fix():
    """investing / investment 这类派生词要仍然算 —— 用的是前缀词边界,不是全词。"""
    pos, _, _ = _count_signals("Long-term investing remains attractive.")
    assert pos >= 1, "investing 不该因为加了词边界就失效"


def test_camelcase_handle_does_not_match_without_at():
    """即使去掉 @,LenaInvests 这种驼峰里的 Invest 也不该命中。"""
    pos, _, _ = _count_signals("市场传言主要来自 LenaInvests 等账号。")
    assert pos == 0, "驼峰账号名仍被误判为买入信号"


def test_handle_neutralization_keeps_the_rest_of_the_sentence():
    """账号名要被抹掉,但同一段里真正的信号不能被一起抹掉。"""
    pos, neg, _ = _count_signals("@GrowthTrack 建议买入。我维持零仓位观望。")
    assert pos >= 1, "账号名之外的买入信号必须保留"
    assert neg >= 1, "同段的否定信号也必须保留"
    assert pos >= 1 and neg >= 1, \
        "账号名被抹掉了,但真信号一个都不能少(否则就修过头了)"


# ===========================================================================
# 4. D1:中文信号
# ===========================================================================
def test_chinese_buy_signal_is_counted():
    pos, _, _ = _count_signals(ZH_BUY)
    assert pos >= 1, "中文买入信号必须被数到(否则中文样本永远 unknown)"


def test_chinese_cautious_signal_is_counted():
    _, neg, _ = _count_signals(ZH_WAIT)
    assert neg >= 1, "中文谨慎/否定信号必须被数到"


def test_chinese_conditional_signal_is_counted():
    _, _, cond = _count_signals(ZH_COND)
    assert cond >= 1, "中文条件化信号必须被数到"


def test_chinese_negation_beats_buy_word_in_same_sentence():
    """『不建议买入』含买入词但是否定 → 判谨慎,不能判正面。"""
    pos, neg, _ = _count_signals("当前不建议买入,估值偏高。")
    assert pos == 0, "否定句里的买入词被算成正面信号了"
    assert neg >= 1


def test_chinese_quick_scan_can_reach_a_verdict():
    """中文正文要能走完 quick_scan 得出 consistent/inconsistent,不是恒 unknown。"""
    v1, _ = quick_scan(_rec("B", ZH_WAIT))
    v2, _ = quick_scan(_rec("A", ZH_BUY))
    assert v1 == "consistent", v1
    assert v2 == "consistent", v2


# ===========================================================================
# 5. attach_consistency 接线
# ===========================================================================
def test_attach_consistency_stamps_method_and_disagreement():
    rec = _rec("B", ZH_WAIT)
    out = attach_consistency(rec, branch_source="judge", llm=_StubLLM(
        ['{"stance":"wait","reason":"维持零仓位观望"}']))
    cs = out["consistency"]
    assert cs["branch_source"] == "judge"
    assert cs["method"].startswith("llm_stance"), cs
    assert "disagreement" in cs, cs
    assert "quick_scan" in cs, cs


def test_attach_consistency_does_not_mutate_input():
    rec = _rec("B", ZH_WAIT)
    attach_consistency(rec, branch_source="judge", llm=_StubLLM(
        ['{"stance":"wait","reason":"观望"}']))
    assert "consistency" not in rec


def test_pipeline_still_delegates_to_attach_consistency():
    from mavis_case01_injector import pipeline
    import inspect
    assert "attach_consistency" in inspect.getsource(pipeline._attach_consistency)


@pytest.mark.parametrize("verdict_ok", [True, False])
def test_verdict_is_one_of_three(verdict_ok):
    out = judge_consistency(_rec("B", ZH_WAIT), llm=_StubLLM([ '{"stance":"wait","reason":"观望"}']))
    assert out["verdict"] in ("consistent", "inconsistent", "unknown")
