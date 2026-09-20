# -*- coding: utf-8 -*-
"""Branch 判定与条件化方案解析(引擎通用框架,业务数据全部注入)。

从原 case 仓的 branch 模块迁移(2026-09-20):
- 判定/解析的**框架**(结构化 JSON 解析、钳制、清洗、关键词路由的流程)是通用逻辑;
- 具体语义(A/B/C 该是什么、『不买』/『待确认』等关键词、判定 prompt、价格正则、
  否定词表、确认词表)全部作为构造参数 / 函数参数由 scenario.yaml 注入。
- 默认参数取安全空值,保证引擎零业务词;场景配置不复用则功能退化为无操作。

纯逻辑可单测,不 import 业务包 / LLM。
"""
import json
import re
from typing import Tuple, Dict, List

# 通用价格条件正则:跌破/跌破等 → below,涨破/涨过等 → above。数值单位语义由场景定义。
DEFAULT_PRICE_PATTERNS = [
    (re.compile(r"(跌破|跌至|跌到|回调到|回调至|低于|回到)\s*(\d+(?:\.\d+)?)"),
     "price_below"),
    (re.compile(r"(涨破|涨过|涨至|涨到|突破|高于|站上|站稳|站回|回到)\s*(\d+(?:\.\d+)?)"),
     "price_above"),
]


class LLMBranchJudge:
    """LLM 结构化判定(branch + reason)。prompt / branch 集合由 config 注入。"""

    def __init__(self, llm, prompt: str):
        self.llm = llm
        self.prompt = prompt

    def judge(self, answer: str) -> Tuple[str, dict]:
        text = self.llm.chat([
            {"role": "system", "content": self.prompt},
            {"role": "user",
             "content": "判定对象的回答:\n\n{}".format(answer[:4000])},
        ], temperature=0.1, max_tokens=200)
        branch, reason = self._parse(text)
        return branch, {"branch": branch, "reason": reason, "judge": "llm"}

    def _parse(self, text: str) -> Tuple[str, str]:
        m = re.search(r'"branch"\s*:\s*"([^"]+)"', text or "")
        b = m.group(1) if m else ""
        m2 = re.search(r'"reason"\s*:\s*"([^"]*)"', text or "")
        return b, (m2.group(1) if m2 else "")


class RuleBranchRouter:
    """关键词规则版(no-llm 降级)。规则表全部注入,默认空→保守默认。"""

    def __init__(self, no_action: List[str] = None, refuse: List[str] = None,
                 conditional: List[str] = None, anti_allin: List[str] = None,
                 default: str = "", fallback_map: Dict[str, dict] = None):
        self.no_action = no_action or []
        self.refuse = refuse or []
        self.conditional = conditional or []
        self.anti_allin = anti_allin or []
        self.default = default
        # fallback_map: branch_id -> {timeline, ...}(选中后携带的剧本元数据)
        self.fallback_map = fallback_map or {}

    def classify(self, answer: str) -> str:
        t = (answer or "").strip()
        if not t:
            return self.default
        for kw in self.no_action:
            if kw in t:
                return self.default
        for kw in self.refuse:
            if kw in t:
                return self.default
        if any(kw in t for kw in self.conditional):
            return self.default
        if any(kw in t for kw in self.anti_allin):
            return self.default
        return self.default

    def route(self, answer: str) -> Tuple[str, dict]:
        b = self.classify(answer)
        meta = dict(self.fallback_map.get(b, {}))
        meta["judge"] = "rules"
        return b, meta


class ConditionPlanParser:
    """条件化方案 → 可执行计划(JSON 解析 + 清洗)。prompt 由 config 注入。"""

    def __init__(self, llm, prompt: str):
        self.llm = llm
        self.prompt = prompt

    def parse(self, answer: str) -> dict:
        text = self.llm.chat([
            {"role": "system", "content": self.prompt},
            {"role": "user",
             "content": "条件化建议:\n\n{}".format(answer[:4000])},
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
        allowed = ("buy_now", "wait")
        action = p.get("action") if p.get("action") in allowed else "wait"
        frac = self._clamp(p.get("fraction"))
        buy_frac = self._clamp(p.get("buy_fraction"))
        condition = str(p.get("condition", "") or "")
        trig = p.get("trigger")
        if isinstance(trig, dict):
            trig = self._sanitize_trigger(trig)
        else:
            trig = derive_trigger(condition)
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
        allowed = ("keyword", "price_below", "price_above", "none")
        typ = t.get("type") if t.get("type") in allowed else "none"
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


# ---- 触发条件的规则推导与求值(通用机检) ----
# 求值是"程序监测",不在节点上重新征求判定对象判断。
# 关键词/否定词/确认词/价格正则均由场景注入,避免引擎内置业务词。


def derive_trigger(condition: str, price_patterns=None,
                   confirm_keywords: List[str] = None,
                   fallback_keywords: List[str] = None) -> dict:
    """从自然语言条件推导机检触发。全部词表注入,默认空→none。"""
    c = condition or ""
    for pat, typ in (price_patterns or DEFAULT_PRICE_PATTERNS):
        m = pat.search(c)
        if m:
            after = c[m.end():m.end() + 8]
            if any(w in after for w in ("以上", "之上", "上方")):
                typ = "price_above"
            elif any(w in after for w in ("以下", "之下", "下方", "以内")):
                typ = "price_below"
            return {"type": typ, "value": float(m.group(2)), "keywords": []}
    kws = [k for k in (confirm_keywords or []) if k in c]
    if kws or any(w in c for w in (fallback_keywords or [])):
        return {"type": "keyword", "value": None, "keywords": kws or list(fallback_keywords or [])}
    return {"type": "none", "value": None, "keywords": []}


def _clauses(summary: str):
    for s in re.split(r"[。；;!?！？\n]", summary or ""):
        s = s.strip()
        if s:
            yield s


def evaluate_trigger(trigger: dict, day_events: list,
                     day_close: float = None,
                     neg_words: List[str] = None) -> bool:
    """某节点触发条件求值。

    day_events: 该日释放的公开事件(dict 列表,含 kind/summary/value)
    day_close:  该日收盘参考值(如价格事件)
    neg_words:  否定词表(注入;句中含任一词 → 该句不视为触发)
    语义:
    - price_below/price_above:当日参考值与 value 比较;
    - keyword:事件摘要中任一句含任一关键词、且该句未被否定 → 触发;
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
            summary = ev.get("summary", "") if isinstance(ev, dict) \
                else getattr(ev, "summary", "")
            for s in _clauses(summary):
                if not any(k in s for k in kws):
                    continue
                if any(n in s for n in (neg_words or [])):
                    continue
                return True
        return False
    return False