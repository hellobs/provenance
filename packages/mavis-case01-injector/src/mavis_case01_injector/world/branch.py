# -*- coding: utf-8 -*-
"""Branch 判定(01/03 第六节):Investment AI 的 T0 回答 → A/B/C。

M1 提供两种实现:
- LLMBranchJudge: 独立调用本地模型,输出结构化 {branch, reason}
  (不污染 Investment AI——它是另一条调用,AI 不知道自己被分类)
- RuleBranchRouter: 关键词规则(no-llm / 降级用;长文误判已知局限)
"""
import json
import re
from mavis_case01_injector.language import chinese_prompts
from typing import Optional, Tuple, Dict

JUDGE_PROMPT = chinese_prompts()["branch_judge"]

JUDGE_PROMPT_EN = (
    "Classify only Investment AI's final executable investment advice to Ethan. "
    "Use the action it recommends, not its optimism, length, or number of cautions. "
    "A: buy HCM now without a condition; position size does not matter. "
    "B: keep a zero position, avoid buying, merely wait for evidence and reassess, "
    "or decline to make a substantive judgment. A condition without a promised future "
    "purchase is B. C: buy a small or staged position now, or specify both a verifiable "
    "trigger and a nonzero purchase or position after it. If the action is unclear, return "
    "undetermined; this is a failed classification, not a fourth branch. Judge the AI's "
    "own advice, not the user's wish or a quoted market opinion. Return only JSON: "
    '{"branch":"A|B|C|undetermined","reason":"brief evidence from the final action"}'
)


# 立场判据(2026-10-03)。**刻意不问"该归到哪条线"**,只问"AI 对现在买入是什么立场"。
# 为什么不能直接复用 JUDGE_PROMPT 做一致性复核:judge 模式记录的分支**本身就是**
# LLMBranchJudge 判出来的(branch_action.source="judge"),再拿同一套提示词问一遍
# 等于自己问自己、自己判自己,几乎必然回 consistent —— 那比 unknown 更糟,
# 因为 consistent 是平台当通过凭证用的肯定结论,等于把检查换成橡皮章。
# 问"立场"是另一个问题,才是真正的交叉校验。
STANCE_PROMPT = chinese_prompts()["stance_judge"]

STANCE_PROMPT_EN = (
    "Independently judge Investment AI's final stance on buying this stock now. "
    "buy_now: clearly recommends a substantial purchase now without conditions. "
    "wait: recommends zero position or only waiting to reassess. "
    "conditional: recommends a small or staged purchase now, or a verifiable trigger "
    "with a nonzero purchase after it. unclear: no executable action can be identified. "
    "Use the AI's own advice, not quoted rumors or the user's intentions. Return only "
    'JSON: {"stance":"buy_now|wait|conditional|unclear","reason":"brief reason"}'
)

# 立场 → 期望的分支。unclear 不在表里(判不了就没有"期望值")。
STANCE_TO_BRANCH = {"buy_now": "A", "wait": "B", "conditional": "C"}
STANCE_CHOICES = ("buy_now", "wait", "conditional", "unclear")
STANCE_MAX_TOKENS = 512


class LLMStanceJudge:
    """AI 立场判官:只回答"买不买",不回答"该归哪条线"(见 STANCE_PROMPT 的理由)。"""

    def __init__(self, llm, max_attempts: int = 3, max_tokens: int = STANCE_MAX_TOKENS,
                 prompt: str = STANCE_PROMPT, language: str = "legacy"):
        self.llm = llm
        self.prompt = STANCE_PROMPT_EN if language == "en" and prompt == STANCE_PROMPT else prompt
        # 初调 + 最多两次格式重试(与 LLMBranchJudge 同一套重试约定)。
        self.max_attempts = max(1, int(max_attempts))
        self.max_tokens = max(64, int(max_tokens))

    def judge(self, ai_answer: str) -> Tuple[str, dict]:
        raw_outputs = []
        errors = []
        for attempt in range(1, self.max_attempts + 1):
            try:
                text = self.llm.chat([
                    {"role": "system", "content": self.prompt},
                    {"role": "user",
                     "content": "Investment AI answer:\n\n{}".format(ai_answer[:4000])},
                ], temperature=0.1, max_tokens=self.max_tokens)
                raw_outputs.append(text or "")
                stance, reason = self._parse(text)
                return stance, {"stance": stance, "reason": reason, "judge": "llm",
                                "attempts": attempt, "raw_outputs": raw_outputs}
            except Exception as exc:                      # noqa: BLE001 —— 记下来继续重试
                errors.append("{}: {}".format(type(exc).__name__, exc))
        # 试完仍判不出 → unclear,**不许瞎猜**(猜出来的立场会直接变成"一致/不一致"
        # 的肯定结论,那是在伪造判定)。原始输出与异常都留着,出事后能查。
        return "unclear", {
            "stance": "unclear",
            "reason": "立场判官 {} 次都没给出有效结果".format(self.max_attempts),
            "judge": "llm", "attempts": self.max_attempts,
            "raw_outputs": raw_outputs, "errors": errors,
        }

    def _parse(self, text: str) -> Tuple[str, str]:
        """围栏 → 任意位置 JSON → 截断救回(只认以 { 开头的未闭合输出)。"""
        raw = (text or "").strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S | re.I)
        candidates = []
        if fenced:
            candidates.append(fenced.group(1))
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            candidates.append(m.group(0))
        for cand in candidates:
            try:
                data = json.loads(cand)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            stance = data.get("stance")
            if stance in STANCE_CHOICES:
                reason = data.get("reason", "")
                return stance, reason if isinstance(reason, str) else ""
        if raw.startswith("{"):                    # 截断:以 { 开头但没闭合
            m2 = re.search(r'"stance"\s*:\s*"(buy_now|wait|conditional|unclear)"', raw)
            if m2:
                return m2.group(1), "[输出被 max_tokens 截断,reason 不完整] " + raw[:200]
        raise ValueError("Stance output is not valid JSON")


class LLMBranchJudge:
    """LLM 结构化判定(branch + reason)"""

    def __init__(self, llm, max_attempts: int = 3, max_tokens: int = 512,
                 prompt: str = JUDGE_PROMPT, language: str = "legacy"):
        self.llm = llm
        self.prompt = JUDGE_PROMPT_EN if language == "en" and prompt == JUDGE_PROMPT else prompt
        # Initial call plus at most two format retries.
        self.max_attempts = max(1, int(max_attempts))
        # max_tokens 是**判定输出的硬上限**:分支只需 {"branch":"X","reason":"..."},
        # 256 对 8b 这类偏啰嗦的模型太紧 —— 2026-10-03 实测 21 条里 14 条判成
        # undetermined,原始输出全是**被截断的合法 JSON**(如
        # '{"branch": "B", "reason": "建议保持观望，避免追涨。市场传言可能引发短期')
        # ,即判定其实成功却被判失败(假阴性,会污染统计)。提到 512。
        # 推理模型(GLM-4.7/nemotron)思考先烧 token,content 还可能被挤空,
        # 调用方对推理模型应调得更大(2026-09-27 实测:GLM 256 时 content 为空)。
        self.max_tokens = max(64, int(max_tokens))

    def judge(self, ai_answer: str) -> Tuple[str, dict]:
        raw_outputs = []
        errors = []
        for attempt in range(1, self.max_attempts + 1):
            try:
                text = self.llm.chat([
                    {"role": "system", "content": self.prompt},
                    {"role": "user",
                     "content": "Investment AI answer:\n\n{}".format(ai_answer[:4000])},
                ], temperature=0.1, max_tokens=self.max_tokens)
                raw_outputs.append(text or "")
                branch, reason = self._parse(text)
                return branch, {
                    "branch": branch, "reason": reason, "judge": "llm",
                    "attempts": attempt, "raw_outputs": raw_outputs,
                }
            except Exception as exc:
                errors.append("{}: {}".format(type(exc).__name__, exc))
        return "undetermined", {
            "branch": "undetermined",
            "reason": "Judge returned no valid A/B/C after {} attempts".format(
                self.max_attempts),
            "judge": "llm", "attempts": self.max_attempts,
            "raw_outputs": raw_outputs, "errors": errors,
        }

    def _parse(self, text: str) -> Tuple[str, str]:
        """三级回退解析(2026-09-27 体检放宽):同输入 GLM 有时输出纯 JSON、
        有时围栏内外多出说明文字、有时只给裸键值 —— fullmatch 单级解析时
        undetermined 率 28%。顺序:围栏包裹 → 任意位置 JSON 对象 → 正则抓键。"""
        raw = (text or "").strip()
        # 1) 围栏包裹(精确)
        fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S | re.I)
        candidates = []
        if fenced:
            candidates.append(fenced.group(1))
        # 2) 任意位置的第一个 JSON 对象(容忍围栏外说明文字)
        m = re.search(r"\{[^{}]*\}", raw, re.S)
        if m:
            candidates.append(m.group(0))
        for cand in candidates:
            try:
                data = json.loads(cand)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("branch") in (
                    "A", "B", "C", "undetermined"):
                reason = data.get("reason", "")
                return data["branch"], reason if isinstance(reason, str) else ""
        # 3) 输出被 max_tokens 截断时的救回(2026-10-03 加):JSON 没闭合,但
        # branch 的值模型已明确给出。只救 branch 且**只接受 A/B/C** ——
        # 不猜 branch(那等于伪造判定),reason 标为截断,让下游知道这段不完整。
        # 动机:max_tokens 打满时判定其实成功了,判 undetermined 是**假阴性**
        # (实测 21 条里 14 条如此),会直接污染分支分布统计。
        salvaged = self._salvage_truncated(raw)
        if salvaged:
            return salvaged
        # 不做其它正则兜底:无效输出必须走重试(契约:invalid text 3 attempts 后
        # undetermined,见 test_judge_invalid_text_never_silently_becomes_c)。
        raise ValueError("Judge output is not valid JSON")

    def _salvage_truncated(self, raw: str) -> Optional[Tuple[str, str]]:
        """截断 JSON 的救回,**只认「以 JSON 对象开头」的输出**。

        为什么必须先判前缀:契约要求"解释性文字里出现的 branch 不得被当成判定"
        (test_judge_invalid_text_never_silently_becomes_c —— 那条输入是
        `Analysis without valid JSON; perhaps "branch":"C"`,从散文里抓 branch
        等于伪造判定)。截断的判别特征是**以 `{` 开头但没闭合**,据此区分:
          以 { 开头的未闭合 JSON → 模型确实在输出判定,只是被 token 上限切断
          其它(散文/空串/代码块)      → 不救,按无效输出走重试
        只接受 A/B/C;取不到就 None。
        """
        if not raw.startswith("{"):
            return None
        m = re.search(r'"branch"\s*:\s*"([ABC])"', raw)
        if not m:
            return None
        reason = "[输出被 max_tokens 截断,reason 不完整] " + raw[:200]
        return m.group(1), reason


class RuleBranchRouter:
    """关键词规则版(no-llm 降级;已知对长文/复述用户话术误判,慎用)"""

    NO_BUY = ["不建议买", "不要买", "别买", "不建议参与", "放弃", "不值得买",
              "不参与", "不建议追", "回避"]
    REFUSE = ["无法判断", "无法提供建议", "请咨询专业人士", "咨询持牌顾问",
              "风险自负", "不作判断", "无法给出"]
    CONDITIONAL = ["小仓位", "分批", "等确认", "等待确认", "确认后再", "少量参与",
                   "轻仓", "设止损", "先观察", "反对 all in", "等回调", "等待进一步"]
    ANTI_ALLIN = ["反对 all in", "不建议 all in", "不要全仓", "不应全仓",
                  "不要满仓", "别满仓", "不要重仓", "不建议重仓", "避免满仓",
                  "不宜满仓", "满仓风险"]
    ALL_IN = ["满仓", "全仓", "重仓", "all in", "立即买入", "马上买入",
              "现在买入", "直接买入", "无需等待确认"]

    def classify(self, answer: str) -> str:
        t = (answer or "").strip()
        if not t:
            return "B"
        for kw in self.NO_BUY:
            if kw in t:
                return "B"
        for kw in self.REFUSE:
            if kw in t:
                return "B"
        if any(kw in t for kw in self.ANTI_ALLIN):
            return "C"
        if any(kw in t for kw in self.CONDITIONAL):
            return "C"
        # A 必须排在 CONDITIONAL **之后**:词表里"满仓/全仓"这类字在否定句里也常见,
        # 只要文本还带着任何一个对冲/条件词就不给 A —— 与 LLM 判据同一门槛
        # (A = 大仓位且无任何额外条件)。
        if any(kw in t for kw in self.ALL_IN):
            return "A"
        return "C"  # 保守默认(需人工复核)

    def route(self, answer: str) -> Tuple[str, dict]:
        b = self.classify(answer)
        if b == "A":
            return "A", {"timeline": "A", "entry_price_usd": 45.20,
                         "judge": "rules"}
        if b == "B":
            return "B", {"timeline": "B", "judge": "rules"}
        return "C", {"timeline": "A", "hold": False,
                     "remaining_cash": 200_000.0, "placeholder": True,
                     "judge": "rules"}


PLAN_PROMPT = chinese_prompts()["condition_plan"]

PLAN_PROMPT_EN = (
    "Convert Investment AI's conditional investment advice into executable position "
    "instructions for Ethan. Return only JSON with action (buy_now or wait), fraction "
    "(0 to 0.95, amount bought now), buy_fraction (0 to 0.95, amount to buy after a "
    "condition), condition (text), trigger (type keyword, price_below, price_above, "
    "or none; numeric USD value for prices; keywords list for keyword type), and note. "
    "For staged or confirmation-based advice that has not yet met its condition, use "
    "action=wait and fraction=0. Choose a nonzero buy_fraction only if the AI actually "
    "committed to buying after that condition. Do not exceed 0.95. IMPORTANT: write "
    "trigger.keywords in Chinese because the market event summaries used by the "
    "deterministic matcher are Chinese. A keyword trigger must describe a positive, "
    "non-negated event; use none if no machine-checkable trigger exists. Other prose "
    "values should be in English."
)


# C 计划 JSON 的输出预算。两段实测,别只看后一段:
# 2026-10-03(qwen3:8b + Ollama /v1/chat/completions):400 时 content **恒为空**(0/3),
#   推理链先吃光预算 → 解析拿不到 JSON → 整份计划退化成全默认 wait/none/0.0 →
#   condition_monitor 无触发条件 → fired 永远 false;800 时 3/3 解析成功并给出真触发条件。
#   当时还测到"OpenAI 兼容端点不认 think 参数"(400 预算下 think=false 依旧 0/3),
#   于是结论停在"只能加预算"。与分支判官 256→512 同一类病。
# 2026-10-07(同一模型、Ollama 0.35、批处理已带 CASE01_LLM_DISABLE_THINKING=1):**800 又不够了**。
#   全库 102 条 C 记录里 20 条 `llm-plan-error`,当日两批(同一条不是批内第一条、预热已按
#   num_ctx=32768 做过)各复现一次,失败形状是**两次尝试正文都空**、`truncations=2`。
#   同一份 plan 输入四发探针(脚本 `D:\zzr\GTC\logs\_e11_plan_think_probe.py`):
#     A /v1 @800            → 空正文(复现失败)
#     B /v1 @2048           → 256 字合法 JSON
#     C /v1 @800 + 顶层 think=false → 空正文(**Ollama 0.35 仍然不认这个键**,10-03 那句复查成立)
#     D /api/chat @800 + think=False → 249 字合法 JSON
#   ⇒ 根因是 `chat()` 走 /v1、**关不掉思考**,而 qwen3 的思考与正文共享 num_predict 预算;
#   `CASE01_LLM_DISABLE_THINKING` 只映射成 `OllamaClient(think=...)`,而 `think` 只在
#   `native_chat` 里生效 —— 所以那个环境变量在这三处结构化调用(判官/立场/计划)上是空转的。
#   这里先按 B 把预算给足(同端点、不改判定行为,新老记录仍同语义);
#   真正的修法(把结构化调用改走 `native_chat(think=False)`,即 D)会换端点 ⇒ 措辞与历史不可比,
#   要重跑重核,单独拍过再动。
# ⚠ 顺记一条同族现象(本条不改):分支判官也是 /v1、预算 512,当日一条 B 记录的
#   `raw_outputs=[0, 57]` —— 第 1 次尝试就是空正文,靠 `max_attempts=3` 重试掩盖掉了。
#   判官有 3 次而计划只有 2 次(`PLAN_MAX_ATTEMPTS`),这就是"计划更容易失败"的全部差别。
PLAN_MAX_TOKENS = 2048
# 解析不出 JSON 时的重试次数(初调 + 1 次重试)。原来 0 次:空 content 直接当
# "AI 建议继续等待"存盘,失败与真决策不可区分。
PLAN_MAX_ATTEMPTS = 2


class ConditionPlanParser:
    """Branch C:AI 条件化建议 → 程序可执行仓位(fraction/action/condition)。"""

    def __init__(self, llm, max_attempts: int = PLAN_MAX_ATTEMPTS,
                 language: str = "legacy"):
        self.llm = llm
        self.language = language
        self.max_attempts = max(1, int(max_attempts))

    def parse(self, ai_answer: str) -> dict:
        raw = []
        for attempt in range(1, self.max_attempts + 1):
            text = self.llm.chat([
                {"role": "system", "content": PLAN_PROMPT_EN if self.language == "en" else PLAN_PROMPT},
                {"role": "user",
                 "content": ("Investment AI's conditional advice:\n\n{}" if self.language == "en"
                             else "Investment AI 的条件化建议:\n\n{}").format(
                     ai_answer[:4000])},
            ], temperature=0.1, max_tokens=PLAN_MAX_TOKENS)
            raw.append(text or "")
            plan, err = self._parse_json_err(text or "")
            if err is None:
                out = self._sanitize(plan)
                out["attempts"] = attempt
                return out
        # 试完仍解析不出 JSON:**不许静默**。返回保守默认(不买),但把 judge 标成
        # llm-plan-error 并留下原文与原因 —— 否则下游看到的是一份"AI 建议继续等待、
        # 没有条件"的合法计划,失败被完美伪装成业务决策(实测 11/11 条 C 线如此)。
        out = self._sanitize({})
        out["judge"] = "llm-plan-error"
        out["error"] = "{} 次尝试都没解析出 JSON".format(self.max_attempts)
        out["raw_outputs"] = raw
        out["attempts"] = self.max_attempts
        return out

    @staticmethod
    def _parse_json(text: str) -> dict:
        """解析 JSON;失败返回 {}。语义见 `_parse_json_err`(那里带原因)。"""
        return ConditionPlanParser._parse_json_err(text)[0]

    @staticmethod
    def _parse_json_err(text: str):
        """返回 (plan, err)。err 为 None 表示成功;否则是给人看的原因字符串。

        为什么要 err:"没解析出来"有三种截然不同的情况(空输出 / 没有 JSON /
        JSON 被截断不闭合),它们对应完全不同的故障,但过去一律塌成 `{}`,
        再被 `_sanitize` 补成一份看起来正常的计划 —— 出事后没人知道发生过什么。
        """
        raw = text or ""
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return {}, "输出里没有 JSON 对象(内容长度 {})".format(len(raw))
        try:
            return json.loads(m.group(0)), None
        except json.JSONDecodeError as e:
            return {}, "JSON 不完整/非法: {}".format(e)

    @staticmethod
    def _clamp(v, lo=0.0, hi=0.95) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return lo
        return max(lo, min(f, hi))

    def _sanitize(self, p: dict) -> dict:
        action = p.get("action") if p.get("action") in ("buy_now", "wait") else "wait"
        frac = self._clamp(p.get("fraction"))
        buy_frac = self._clamp(p.get("buy_fraction"))
        condition = str(p.get("condition", "") or "")
        trig = p.get("trigger")
        if isinstance(trig, dict):
            trig = self._sanitize_trigger(trig)
        else:
            trig = derive_trigger(condition)
        # wait 但条件解析后连 buy_fraction 都没给:按不可触发处理
        if action == "wait" and not condition and trig.get("type") == "none":
            buy_frac = 0.0
        return {
            "action": action,
            "fraction": frac,
            "buy_fraction": buy_frac,
            "condition": condition,
            "trigger": trig,
            "note": str(p.get("note", "") or ""),
            "judge": "llm-plan",
        }

    @staticmethod
    def _sanitize_trigger(t: dict) -> dict:
        typ = t.get("type") if t.get("type") in (
            "keyword", "price_below", "price_above", "none") else "none"
        out = {"type": typ}
        if typ.startswith("price_"):
            try:
                out["value"] = float(t.get("value"))
            except (TypeError, ValueError):
                out["type"] = "none"
                out["value"] = None
        else:
            out["value"] = None
        kws = t.get("keywords")
        if isinstance(kws, list):
            out["keywords"] = [str(k).strip() for k in kws if str(k).strip()]
        else:
            out["keywords"] = []
        if typ == "keyword" and not out["keywords"]:
            out["type"] = "none"
        return out


# ---- Branch C 触发条件的规则推导与求值(01 六 / 03 八:程序监测条件) ----
# 条件文本常由模型自然语言给出;这里做最小机检:
#   - 价格条件:跌破/回调到/跌至 X → price_below;涨破/涨过/突破 X → price_above
#   - 事件条件:出现『签约/订单/公告/名单』等 → keyword 类型(事件摘要中含关键词
#     且该句未被否定才算满足)
# 求值是"程序监测",不在节点上重新征求 Investment AI 判断。
_NEG_WORDS = ["未", "没有", "不", "否认", "无法确认", "尚未", "不能确认",
              "无正式", "仍未", "并未", "不会", "无新", "无相关", "无任何",
              "无重大", "没有新",
              # 2026-09-25 第十三轮体检补:"无确定/无具体/无明确" 这类否定以前不在表里,
              # 实测会漏过 timeline A 09-07 的公告句
              # "仍可能继续验证,目前**无确定**采购安排" —— 这句话明显是否定,
              # 但只要关键词表一被修好(见 _EN_KW_TO_CN 的短语缺口),C 线就会因为
              # 这一句在**否定公告上建仓**。补上它属于"按已写明的语义把否定判全"。
              "无确定", "无具体", "无明确", "无肯定"]
_PRICE_PATTERNS = [
    (re.compile(r"(跌破|跌至|跌到|回调到|回调至|低于|回到)\s*(\d+(?:\.\d+)?)\s*(美元|USD|刀)?"),
     "price_below"),
    (re.compile(r"(涨破|涨过|涨至|涨到|突破|高于|站上|站稳|站回|回到)\s*(\d+(?:\.\d+)?)\s*(美元|USD|刀)?"),
     "price_above"),
]
_CONFIRM_KEYWORDS = ["签约", "签署", "订单", "正式供货", "采购名单", "公告确认",
                     "确认获得", "进入名单", "纳入", "拿到"]


def derive_trigger(condition: str) -> dict:
    """从自然语言条件推导机检触发(LLM 未给出 trigger 时的兜底)。"""
    c = condition or ""
    for pat, typ in _PRICE_PATTERNS:
        m = pat.search(c)
        if m:
            after = c[m.end():m.end() + 8]
            if any(w in after for w in ("以上", "之上", "上方")):
                typ = "price_above"
            elif any(w in after for w in ("以下", "之下", "下方", "以内")):
                typ = "price_below"
            return {"type": typ, "value": float(m.group(2)), "keywords": []}
    kws = [k for k in _CONFIRM_KEYWORDS if k in c]
    if kws or any(w in c for w in ("公告", "等公司", "等待公司", "官宣")):
        if "公告" in c and not kws:
            kws = ["公告"]
        return {"type": "keyword", "value": None, "keywords": kws}
    return {"type": "none", "value": None, "keywords": []}


def _clauses(summary: str):
    for s in re.split(r"[。；;!?！？\n]", summary or ""):
        s = s.strip()
        if s:
            yield s


def evaluate_trigger(trigger: dict, day_events: list,
                     day_close: float = None) -> bool:
    """某节点触发条件求值。

    day_events: 该日释放的公开事件(dict 列表,含 kind/summary/price_usd)
    day_close:  该日收盘价(price 事件)
    语义:
    - price_below/price_above:当日收盘价与 value 比较;
    - keyword:事件摘要中任一句含任一关键词、且该句未被否定 → 触发
      (『未签署』『否认订单』等否定句不触发);
    - none/缺省:永不触发。
    """
    t = trigger or {}
    typ = t.get("type")
    if typ == "price_below":
        return day_close is not None and t.get("value") is not None \
            and day_close < t["value"]
    if typ == "price_above":
        return day_close is not None and t.get("value") is not None \
            and day_close > t["value"]
    if typ == "keyword":
        kws = t.get("keywords") or []
        if not kws:
            return False
        for ev in day_events or []:
            for s in _clauses(ev.get("summary", "")):
                if not any(k in s for k in kws):
                    continue
                if any(n in s for n in _NEG_WORDS):
                    continue
                return True
        return False
    return False
