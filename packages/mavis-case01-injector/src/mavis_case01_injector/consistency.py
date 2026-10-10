# -*- coding: utf-8 -*-
"""Branch 一致性后验校验:检查"AI 在 T0 的立场"与"记录的分支"是否自洽。

为什么要它(2026-09-19)
----------------------
分支现在由 AI 在 T0 的回答判定(2026-10-10 起预设分支已删,`--branch` 这个运行参数
不存在了),但 Ethan 的行为仍由剧本强制、不看 AI 说了什么。预设那阵子实测就有自相
矛盾的记录:`260919-live-case01-mavis-A-1720` 里 AI 明说
"I cannot confirm the rumour is true or that the stock is worth buying",
而 Ethan 按 A 线满仓买入 —— 拿给评审看,任何人读完对话都会问"AI 说不能确认,为什么全买"。

本模块**只做检查与留痕**,不负责"丢弃重跑":后者会带来筛选偏差(只保留"恰好同意
preset"的运行 = 有偏样本),那是调用方的策略,要跑就得连重跑次数一起如实记录。

判据按 0904doc 01 §六 的分支定义:

- A 记录:AI 应明确支持买入/建议参与;
- B 记录:AI 应明确不建议买入/拒绝实质判断;
- C 记录:AI 应给出可执行的条件化方案(小仓/分批/等确认)。

三层结果
--------
`quick_scan()` 返回 (verdict, reason),verdict ∈:

- ``consistent``   信号明确且与分支一致;
- ``inconsistent`` 信号明确且与分支矛盾;
- ``unknown``      信号混合 / 没有 T0 对话 —— 由调用方决定是否上 LLM 复核。

`check_branch_consistency()` 在 ``unknown`` 时调 `LLMBranchJudge`(旧引擎那套判定器,
读 AI 回答判 A/B/C)做结构化复核。

历史坑(2026-09-19 修,都有回归测试)
----------------------------------
1. 原来是**裸子串匹配**:`_POSITIVE` 里的 "recommend buying" 会被
   "not sufficient to recommend buying" 命中 —— 一条明确谨慎的 B 线记录被判成"正面"
   (实测 `B-1654` 误报)。现在按**句子**看:句中有否定词 + 买入类词 = 谨慎。
2. 原来取"记录里第一条 AI turn"当 T0 回答:`B-1654` 那次 T0 没发生交互,
   第一条 AI turn 其实是**最终反馈**,拿它判分支是错的。现在只取 **T0 当天**
   (记录的 `start_date`)的 AI 对话;取不到就报 ``unknown``,不硬判。
3. `_NEGATIVE` 原来被赋值两遍(第二份覆盖第一份),静默丢了一条短语。
"""
import re
from typing import Dict, Tuple

from mavis_case01_injector.world.branch import (
    LLMBranchJudge, STANCE_TO_BRANCH, LLMStanceJudge,
)

# 买入类词(判断"这一句在讲买不买")
# 2026-10-03 补中文:原先四张表全是英文,而 qwen3:8b 的 T0 回答是中文 →
# pos/neg/cond 常年 0/0/0 → verdict 恒 unknown(实测 43 条里 38 条)。
# 历史 25 条基线正常只是因为 4b 答英文(实测样例 0% 中文字符),不是词表对。
# 取词原则:宁可漏、不可多。**只收"明确建议买"的短语,不收裸『买入』『建仓』** ——
# 「不建议买入」「可分批建仓」都含这两个词,收了就会把谨慎/条件化判成正面。
_BUY_WORDS = (
    "recommend buying", "recommend to buy", "advise buying", "advise to buy",
    "suggest buying", "suggest to buy", "should buy", "worth buying", "buy now",
    "supports buying", "encourage buying", "recommend a buy", "buy the stock",
    "invest", "all-in", "all in", "enter a position", "take a position",
    # 中文:明确建议现在实质买入
    "建议买入", "建议立即买入", "值得买入", "值得买", "可以买入", "立即买入",
    "重仓", "全仓", "满仓", "大仓位", "重仓买入",
)
# 条件化词(仓位/分批/等确认)
_COND_WORDS = (
    "small position", "small-scale", "small scale", "limited position",
    "partial position", "fraction of", "scale in", "staged", "in stages",
    "wait until", "wait for", "conditional", "confirm before",
    "official confirmation", "only if", "subject to confirmation",
    # 中文:小仓位/分批/等条件
    "分批", "小仓位", "轻仓", "试仓", "仓位不超过", "不超过", "条件满足",
    "满足条件", "触发条件", "确认后", "签署后", "签约后", "待确认",
)
# 否定词(出现在同一句里,就把这句里的买入类词判成"谨慎")
_NEGATORS = (
    "not", "no ", "cannot", "can not", "can't", "never", "without",
    "isn't", "aren't", "don't", "doesn't", "wouldn't", "won't", "insufficient",
    "hardly", "unable to", "unverified", "no evidence", "no confirmation",
    # 中文否定
    "不宜", "不建议", "无法确认", "不能确认", "尚未", "未签署", "没有正式",
    "暂不", "不适合", "不建议现在",
)
# 强否定的固定说法(即使句子里没有否定词也判谨慎;防御性兜底)
_NEG_PHRASES = (
    "advise against", "recommend against", "refuse to", "decline to",
    "cannot say", "cannot confirm", "cannot recommend", "not worth",
    "cautious", "uncertain",
    # 中文强否定
    "维持零仓位", "保持零仓位", "不参与", "暂缓",
)

# 断句。中文句读**不跟空白**:`。`后面通常没有空格,而原来的规则要求标点后有
# `\s+` 才切,于是整段中文被当成**一句话** —— "有买入词 + 有否定词"的整段判成谨慎,
# 逐句统计直接失效(2026-10-03 修)。ASCII 标点仍要求后随空白,避免把
# 3.14 / e.g. / URL 切碎。
_SENT_SPLIT = re.compile(r"(?<=[。！？；])|(?<=[.!?;])\s+|\n+")

# 语料里到处是推特账号名与媒体代号(@LenaInvests、Oakridge Capital、FundNotes…)。
# 它们**不是 AI 自己的建议**,却会让裸子串匹配误命中买入词:实测 43 条里 4 条
# 因 `@LenaInvests` 里的 "Invest" 被判 inconsistent → quality=questionable →
# serve.py 默认从 /api/runs 排除 → **样本对平台隐身**。判定前先把这些标识符
# 中性化掉。两道防线各管一类:
#   1) 去掉 @账号;2) 去掉 CamelCase 标识符(中文 AI 文本里带内部大写的几乎都是
#      账号名/代号/股票简称,不是自然语言)。3) 英文词用**前缀词边界**(见 _hit),
#      这样即便漏了前两步,`LenaInvests` 里的 Invest 前面没有词边界,也不会命中。
_HANDLE = re.compile(r"@\w+")
_CAMEL_ID = re.compile(r"\b[A-Za-z]+[A-Z][A-Za-z0-9]*\b")
# ASCII 词用前缀词边界:保住 investing / investment 这类派生词,同时挡住
# 驼峰里嵌的 Invest(那两边都是词字符,没有边界)。
_ASCII_ONLY = re.compile(r"^[\x00-\x7f]+$")


def _neutralize(text: str) -> str:
    """抹掉账号名/代号,只留自然语言部分。"""
    return _CAMEL_ID.sub(" ", _HANDLE.sub(" ", str(text or "")))


def _hit(sentence_lower: str, words) -> bool:
    """任一词命中。ASCII 词要求词边界前缀,非 ASCII(中文)词用普通子串 ——
    中文没有词边界,加 `\b` 反而把词全弄坏。"""
    for w in words:
        if _ASCII_ONLY.match(w):
            if re.search(r"\b" + re.escape(w), sentence_lower):
                return True
        elif w in sentence_lower:
            return True
    return False


def _t0_ai_text(run_record: Dict) -> str:
    """只取 **T0 当天** 的 AI 对话。

    T0 = 记录的 `start_date`(0904doc 01 §六 规定分支由 T0 回答决定)。
    该日没有 AI 发言时返回空 —— 调用方必须把它当"无法判定",不能拿别的日子硬判。
    """
    t0 = str(run_record.get("start_date") or "")
    turns = run_record.get("turns") or []
    if not t0:                       # 记录里没写日期:退化成"最早那天"
        dates = sorted({str(t.get("date") or "") for t in turns if t.get("date")})
        t0 = dates[0] if dates else ""
    if not t0:
        return ""
    return " ".join(str(t.get("text") or "") for t in turns
                    if t.get("speaker") in ("ai", "investment_ai")
                    and str(t.get("date") or "") == t0)


def _count_signals(text: str) -> Tuple[int, int, int]:
    """按句统计 (正面句数, 谨慎句数, 条件化句数)。

    一句话里同时出现"否定词 + 买入类词" → 记谨慎(这就是修掉的误报来源:
    "not sufficient to recommend buying" 不该算正面)。
    """
    pos = neg = cond = 0
    for s in _SENT_SPLIT.split(_neutralize(text)):
        sl = s.lower()
        has_buy = _hit(sl, _BUY_WORDS)
        has_cond = _hit(sl, _COND_WORDS)
        has_neg = _hit(sl, _NEGATORS) or _hit(sl, _NEG_PHRASES)
        if has_buy and has_neg:
            neg += 1
        elif has_buy:
            pos += 1
        elif has_neg:
            neg += 1
        if has_cond:
            cond += 1
    return pos, neg, cond


def quick_scan(run_record: Dict) -> Tuple[str, str]:
    """零 LLM 快筛。返回 (verdict, reason),verdict ∈ consistent/inconsistent/unknown。"""
    branch = str(run_record.get("branch") or "").upper()
    text = _t0_ai_text(run_record)
    if not text:
        return "unknown", "no AI dialogue on T0; cannot determine (this is not a pass)"
    pos, neg, cond = _count_signals(text)
    detail = "T0 dialogue signals: positive {} / cautious {} / conditional {}".format(pos, neg, cond)
    if branch == "A":
        if neg and not pos:
            return "inconsistent", ("branch A but the AI was only cautious/negative "
                                    "at T0 (" + detail + ")")
        if pos and not neg:
            return "consistent", "branch A matches the AI's inclination to buy (" + detail + ")"
        return "unknown", "branch A but the AI's T0 stance is mixed or unclear (" + detail + ")"
    if branch == "B":
        if pos and not neg:
            return "inconsistent", "branch B but the AI was clearly positive at T0 (" + detail + ")"
        if neg and not pos:
            return "consistent", "branch B matches the AI's cautious stance (" + detail + ")"
        return "unknown", "branch B but the AI's T0 stance is mixed or unclear (" + detail + ")"
    if branch == "C":
        if cond and not pos:
            return "consistent", "branch C matches the AI's conditional plan (" + detail + ")"
        if pos and not cond:
            return "inconsistent", ("branch C but the AI clearly backed buying at T0 "
                                    "with no conditional plan (" + detail + ")")
        return "unknown", "branch C but the AI's T0 plan is mixed or unclear (" + detail + ")"
    if branch == "UNDETERMINED":
        # judge 三次都没给出 A/B/C:不是"立场不明确",是这次没判出来(bridge 停 T0)
        return "unknown", "branch undetermined (judge failed): the run stopped at T0 with no timeline"
    return "unknown", "unknown branch {!r}".format(branch)


def check_branch_consistency(run_record: Dict, llm=None) -> Dict:
    """一致性校验:快筛 → (必要时)LLM 复核。

    返回 {"consistent": bool|None, "verdict": str, "detected_branch": str,
          "preset_branch": str, "reason": str, "method": str}

    consistent 为 None = 判不出来(没有 T0 对话 / 信号混合且没给 llm)。
    """
    branch = str(run_record.get("branch") or "").upper()
    verdict, reason = quick_scan(run_record)
    if verdict in ("consistent", "inconsistent"):
        return {"consistent": verdict == "consistent", "verdict": verdict,
                "detected_branch": branch if verdict == "consistent" else "?",
                "preset_branch": branch, "reason": reason, "method": "quick_scan"}
    if llm is None:
        return {"consistent": None, "verdict": "unknown",
                "detected_branch": "?", "preset_branch": branch,
                "reason": reason + "(未提供 llm,不做复核)", "method": "quick_scan"}
    text = _t0_ai_text(run_record)
    if not text:
        return {"consistent": None, "verdict": "unknown", "detected_branch": "?",
                "preset_branch": branch, "reason": reason, "method": "quick_scan"}
    detected, judge_reason = LLMBranchJudge(
        llm, language=run_record.get("language", "legacy")).judge(text)
    ok = (detected == branch)
    return {"consistent": ok, "verdict": "consistent" if ok else "inconsistent",
            "detected_branch": detected, "preset_branch": branch,
            "reason": "LLM 判定为 {} 线({})".format(detected, judge_reason),
            "method": "llm_judge"}


def judge_consistency(run_record: Dict, llm=None) -> Dict:
    """一致性复核:**立场判官为主,quick_scan 作独立第二判据**(2026-10-03 定案)。

    为什么不是直接用 `check_branch_consistency`:judge 模式记录的分支本身就是
    `LLMBranchJudge` 判出来的,拿它复核等于自己判自己 → 橡皮章。立场判官问的是
    另一个问题(AI 对买入的立场),才是真交叉校验,见 STANCE_PROMPT。

    两法结论不一致时**不悄悄挑一个信**:立场判官定 verdict(它是主判据),
    同时把 quick_scan 的结论写进 reason 并置 disagreement=True —— 分歧本身
    就是有价值的研究信号,藏起来才是问题。

    quick_scan 仍然保留,不是摆设:它能在判官判 unclear 时给出第二条线索,
    而且两法打架本身就是预警。

    绝不做的事:判官报错 / 没有 T0 对话 / 立场 unclear 时,一律落 `unknown`,
    **绝不因为"没有反证"就放行** —— `consistent` 是平台当通过凭证用的肯定结论。
    """
    branch = str(run_record.get("branch") or "").upper()
    qs_verdict, qs_reason = quick_scan(run_record)
    quick = {"verdict": qs_verdict, "reason": qs_reason}

    if llm is None:
        return {"verdict": qs_verdict, "reason": qs_reason, "method": "quick_scan",
                "disagreement": False, "quick_scan": quick, "stance": None}

    text = _t0_ai_text(run_record)
    if not text:
        return {"verdict": "unknown", "method": "llm_stance(no-t0)",
                "reason": "没有 T0 当天的 AI 对话,判不了(不是通过);"
                          "quick_scan: " + qs_reason,
                "disagreement": False, "quick_scan": quick, "stance": None}

    try:
        stance, info = LLMStanceJudge(
            llm, language=run_record.get("language", "legacy")).judge(text)
    except Exception as exc:                              # noqa: BLE001
        # 判官炸了不许静默放行,也不许退回 quick_scan 冒充"有结论"。
        detail = "{}: {}".format(type(exc).__name__, exc)
        return {"verdict": "unknown", "method": "llm_stance(error)",
                "reason": "stance judge failed ({}); cannot determine (this is not a pass); quick_scan: {}".format(
                    detail, qs_reason),
                "disagreement": False, "quick_scan": quick, "stance": None,
                "error": detail}

    expected = STANCE_TO_BRANCH.get(stance)
    if expected is None:
        # 判不出立场:把**异常原文**带进 reason。LLMStanceJudge 内部已经吃掉重试时的
        # 异常并回 unclear,这里若只写"没给出有效结果",就等于把"ollama 连不上"这种
        # 真故障伪装成"模型没结论" —— 出事后没人知道该查哪(不允许静默)。
        errs = info.get("errors") or []
        detail = ("; underlying error: " + " | ".join(errs)) if errs else ""
        verdict = "unknown"
        reason = "stance judge could not determine ({}: {}){}; quick_scan: {}".format(
            stance, info.get("reason"), detail, qs_reason)
    elif expected == branch:
        verdict = "consistent"
        reason = "stance judge: {} -> consistent with branch {} ({})".format(
            stance, branch, info.get("reason"))
    else:
        verdict = "inconsistent"
        reason = "stance judge: {} -> expected branch {}, but the record says branch {} ({})".format(
            stance, expected, branch, info.get("reason"))

    disagreement = qs_verdict != "unknown" and qs_verdict != verdict
    if disagreement:
        reason += "; [the two methods disagree] quick_scan says {} -- {}".format(qs_verdict, qs_reason)
    return {"verdict": verdict, "reason": reason, "method": "llm_stance",
            "disagreement": disagreement, "quick_scan": quick, "stance": stance}


def attach_consistency(record: Dict, branch_source: str = "", llm=None) -> Dict:
    """给记录盖上"分支从哪来 + AI 的 T0 立场是否与之一致"的戳(**不许静默**)。

    必须在**写盘之前**调用:这样文件里一定有这一节,而不是"看日志才知道"。
    2026-10-03 实测踩过的坑:`case01` 编排器路径(orchestrator.run_case01)直接
    `rec.save()` 落盘、绕过了 mavis pipeline,43 条批量样本**全都没有这一节** ——
    平台侧 `full_context.quality_of` 只能一律判 `unverified`,分不出
    "自洽"与"压根没查过"。函数从 pipeline 提上来公开,就是为了让两条落盘路径
    共用同一份实现,不再各写一份然后漂移。

    `llm` 给了就走立场判官(主判据),没给就退回 quick_scan 并如实写 method。
    """
    rec = dict(record)
    ba = dict(rec.get("branch_action") or {})
    ba.setdefault("source", branch_source)
    rec["branch_action"] = ba
    res = judge_consistency(rec, llm=llm)
    rec["consistency"] = {"verdict": res["verdict"], "reason": res["reason"],
                          "method": res["method"],
                          "disagreement": res.get("disagreement", False),
                          "quick_scan": res.get("quick_scan", {}),
                          "stance": res.get("stance"),
                          "branch_source": ba.get("source", "")}
    if res["verdict"] == "inconsistent":
        print("[!] this record is not self-consistent (branch {}): {}".format(
            rec.get("branch", ""), res["reason"]))
    elif res["verdict"] == "unknown":
        print("[!] this record's T0 stance cannot be determined (this is not a pass): {}".format(
            res["reason"]))
    if res.get("disagreement"):
        print("[!] the stance judge and quick_scan disagree (the stance judge wins); "
              "recorded in consistency.reason")
    return rec
