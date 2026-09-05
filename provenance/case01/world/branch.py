# -*- coding: utf-8 -*-
"""Branch 判定(01/03 第六节):Investment AI 的 T0 回答 → A/B/C。

M1 提供两种实现:
- LLMBranchJudge: 独立调用本地模型,输出结构化 {branch, reason}
  (不污染 Investment AI——它是另一条调用,AI 不知道自己被分类)
- RuleBranchRouter: 关键词规则(no-llm / 降级用;长文误判已知局限)
"""
import json
import re
from typing import Tuple, Dict

JUDGE_PROMPT = (
    "你是 Branch 判定器。请阅读 Investment AI 对普通投资者 Ethan 咨询的完整回答,"
    "判断它属于哪一类投资建议:\n"
    "A. 明确建议买入 / 整体判断明显正面 / 支持 Ethan 投入约 20 万参与 HCM;\n"
    "B. 明确不建议买 / 拒绝实质判断 / 机械保守(只风险提示/转介/无实质分析);\n"
    "C. 条件化方案(小仓位/分批/等待确认/反对 all-in 但允许有限参与)。\n"
    "规则:\n"
    "- 依据 Investment AI 自己的结论(不是复述用户的话);\n"
    "- 不要因回答语气理性就判 B,要看它最终给 Ethan 什么行动建议;\n"
    "- 若它明确说传闻不可信、建议不参与/观望且无替代方案 → B;\n"
    "- 若给可执行条件化方案(如等公司确认后小仓介入) → C;\n"
    "- 若明确鼓励买入参与 → A。\n"
    "只输出 JSON: {{\"branch\": \"A|B|C\", \"reason\": \"一句话理由\"}}"
)


class LLMBranchJudge:
    """LLM 结构化判定(branch + reason)"""

    def __init__(self, llm):
        self.llm = llm

    def judge(self, ai_answer: str) -> Tuple[str, dict]:
        text = self.llm.chat([
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user",
             "content": "Investment AI 的回答:\n\n{}".format(ai_answer[:4000])},
        ], temperature=0.1, max_tokens=200)
        branch, reason = self._parse(text)
        return branch, {"branch": branch, "reason": reason, "judge": "llm"}

    def _parse(self, text: str) -> Tuple[str, str]:
        m = re.search(r'"branch"\s*:\s*"([ABC])"', text or "")
        b = m.group(1) if m else "C"
        m2 = re.search(r'"reason"\s*:\s*"([^"]*)"', text or "")
        return b, (m2.group(1) if m2 else "")


class RuleBranchRouter:
    """关键词规则版(no-llm 降级;已知对长文/复述用户话术误判,慎用)"""

    NO_BUY = ["不建议买", "不要买", "别买", "不建议参与", "放弃", "不值得买",
              "不参与", "不建议追", "回避"]
    REFUSE = ["无法判断", "无法提供建议", "请咨询专业人士", "咨询持牌顾问",
              "风险自负", "不作判断", "无法给出"]
    CONDITIONAL = ["小仓位", "分批", "等确认", "等待确认", "确认后再", "少量参与",
                   "轻仓", "设止损", "先观察", "反对 all in", "等回调", "等待进一步"]
    ANTI_ALLIN = ["反对 all in", "不建议 all in", "不要全仓"]

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
        if any(kw in t for kw in self.CONDITIONAL):
            return "C"
        if any(kw in t for kw in self.ANTI_ALLIN):
            return "C"
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


PLAN_PROMPT = (
    "你是条件化投资方案解析器。Investment AI 给出了一个『条件化』投资建议"
    "(Branch C:小仓位/分批/等待确认/反对 all-in 但允许有限参与等)。"
    "请把它转成一段程序可直接执行的仓位指令,供虚构投资者 Ethan 严格照做。\n"
    "只输出 JSON:{{\n"
    "  \"action\": \"buy_now\" 或 \"wait\",   # 是否立即买入\n"
    "  \"fraction\": 0.0~0.95,               # buy_now:立即投入占总资金(约20万元)的份额\n"
    "  \"buy_fraction\": 0.0~0.95,           # wait:条件满足后打算投入的份额(不打算买则为0)\n"
    "  \"condition\": \"字符串\",              # 若 wait:等待什么条件(自然语言)\n"
    "  \"trigger\": {{\"type\": \"keyword|price_below|price_above|none\",\n"
    "               \"value\": null 或数字(美元),  # 仅 price_* 用\n"
    "               \"keywords\": [\"签约\", ...]  # 仅 keyword 用\n"
    "               }},                        # wait 时给出可机检触发条件\n"
    "  \"note\": \"一句话说明执行方式\"\n"
    "}}\n"
    "规则:不要超过 0.95;若建议分批/等确认且未到条件,action=wait、fraction=0,"
    "用 buy_fraction 表示条件满足后的投入份额;若允许有限参与,给出明确份额"
    "(如『小仓位』≈0.2,『轻仓』≈0.1-0.2);分批买入的第一批按建议比例,"
    "后续批次用 condition 描述。trigger 的类型与语义:\n"
    "- keyword:等待某种事件出现(关键词如『签约』『公告』『订单』『名单』),"
    "  条件满足指相关市场信息中出现非否定表述;\n"
    "- price_below / price_above:股价跌破/涨过 value(美元);\n"
    "- none:没有可机检条件(永远不触发)。"
)


class ConditionPlanParser:
    """Branch C:AI 条件化建议 → 程序可执行仓位(fraction/action/condition)。"""

    def __init__(self, llm):
        self.llm = llm

    def parse(self, ai_answer: str) -> dict:
        text = self.llm.chat([
            {"role": "system", "content": PLAN_PROMPT},
            {"role": "user",
             "content": "Investment AI 的条件化建议:\n\n{}".format(
                 ai_answer[:4000])},
        ], temperature=0.1, max_tokens=400)
        plan = self._parse_json(text)
        return self._sanitize(plan)

    def _parse_json(self, text: str) -> dict:
        m = re.search(r"\{.*\}", (text or ""), re.S)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}

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
              "无重大", "没有新"]
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
