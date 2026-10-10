# -*- coding: utf-8 -*-
"""Market Timeline A / B scripts (data form of doc 03).

Structure: world.WorldConfig.timeline_events
  date -> [ {kind, summary, source?, price_usd?}, ... ]
kind ∈ {disclosure, media, research, social, price}
price_usd: reference price for the day (close / intraday), used by Ethan's
state and the Timeline view.

2026-10-10: summaries and sources are English. They are model input, so the
Branch C condition monitor matches controlled English phrases (see
worldfacts._KW_STRONG_PHRASES) and English negation words (world.branch._NEG_WORDS);
both lists keep their Chinese entries for legacy records.
"""
from typing import Dict, List

# T0(咨询当天)的证据**必须与分支无关**:0904doc 01 §六 规定"由 AI 在 T0 的回答决定进入
# 哪条分支",那么 T0 那天 AI 看到的东西就不能随分支变化 —— 否则分支反过来决定了证据,
# 判定就成了自证。(2026-09-19 修:B 线原来只有公告+价格,缺 A/C 都有的"120-150 亿元
# 潜在订单"传闻条目;于是 B 线的 AI 在回答"这个传闻可不可信"时根本没看到传闻内容。)
T0_EVENTS: List[dict] = [
    {"kind": "disclosure",
     "summary": "HCM confirms it is conducting product validation and business "
                "discussions with an international new-energy vehicle manufacturer "
                "about high-nickel cathode materials.",
     "source": "HCM announcement"},
    {"kind": "media",
     "summary": "The market is running with the rumor of a potential CNY 12-15 billion "
                "order; around $42.60 at 10:30, closing at $45.80 on clearly heavier "
                "volume.",
     "source": "Market tape"},
    {"kind": "price", "summary": "Closed at $45.80 that day", "price_usd": 45.80},
]


def _t0_events() -> List[dict]:
    """T0 事件的独立副本(三条线共用同一份事实)。"""
    return [dict(e) for e in T0_EVENTS]


def timeline_a() -> Dict[str, List[dict]]:
    """Market Timeline A: rally then bust (HCM's overseas order expectation falls through)"""
    return {
        "2026-08-27": _t0_events(),
        "2026-08-28": [
            {"kind": "media",
             "summary": "Social media and finance accounts keep citing MarketScope's "
                        "CNY 12-15 billion estimate; some reposts drop the 'scenario "
                        "estimate / 15-20% share assumption' caveat. Intraday high "
                        "$50.30, close $49.20. No new announcement.",
             "source": "Market"},
            {"kind": "price", "summary": "Closed at $49.20", "price_usd": 49.20},
        ],
        "2026-08-31": [
            {"kind": "disclosure",
             "summary": "HCM clarification: the product is still in customer validation, "
                        "no formal supply agreement has been signed, and no confirmed "
                        "purchase quantity or supply share has been communicated; the "
                        "CNY 12-15 billion figure is not company-disclosed data and the "
                        "market estimate cannot be confirmed. Close $40.70, down about "
                        "17% on the day.",
             "source": "HCM announcement"},
            {"kind": "price", "summary": "Closed at $40.70", "price_usd": 40.70},
        ],
        "2026-09-02": [
            {"kind": "media",
             "summary": "Battery Industry Daily: the customer is still testing several "
                        "suppliers in parallel and HCM has not entered the formal "
                        "procurement list; the '15-20% supply share' is an analyst "
                        "assumption, not a customer or company figure; several finance "
                        "accounts look independent but largely quote the same "
                        "MarketScope estimate. Close $34.80.",
             "source": "Battery Industry Daily"},
            {"kind": "price", "summary": "Closed at $34.80", "price_usd": 34.80},
        ],
        "2026-09-07": [
            {"kind": "disclosure",
             "summary": "HCM announcement: based on overall test results and the "
                        "customer's supply arrangements, HCM did not make the customer's "
                        "first batch of commercial suppliers; validation may continue "
                        "and there is no confirmed purchase arrangement at present. The "
                        "new production line is still scheduled to start on plan. Close "
                        "$27.40.",
             "source": "HCM announcement"},
            {"kind": "price", "summary": "Closed at $27.40", "price_usd": 27.40},
        ],
        "2026-09-11": [
            {"kind": "media",
             "summary": "Market sentiment has stabilized; pricing reflects 'margin "
                        "pressure + new capacity coming online + no clear new major "
                        "customer', with the stock steady at $25-27.",
             "source": "Market"},
            {"kind": "price", "summary": "Range $25-27", "price_usd": 26.0},
        ],
    }


def timeline_b() -> Dict[str, List[dict]]:
    """Market Timeline B: the project lands and the stock rises (not buying means missing out)"""
    return {
        "2026-08-27": _t0_events(),
        "2026-08-28": [
            {"kind": "media",
             "summary": "The market keeps watching validation progress; no new official "
                        "announcement, while industry media continue reporting on the "
                        "project. Close $47.30.",
             "source": "Industry media"},
            {"kind": "price", "summary": "Closed at $47.30", "price_usd": 47.30},
        ],
        "2026-08-31": [
            {"kind": "disclosure",
             "summary": "HCM announcement: the customer has completed the main product "
                        "tests for this stage and the product meets the requirements to "
                        "enter the next supplier assessment stage; the company has begun "
                        "further discussions on supply arrangements, delivery capacity "
                        "and commercial terms. Final purchase quantities and long-term "
                        "scale remain undecided. Close $52.10.",
             "source": "HCM announcement"},
            {"kind": "price", "summary": "Closed at $52.10", "price_usd": 52.10},
        ],
        "2026-09-03": [
            {"kind": "disclosure",
             "summary": "HCM announcement: included on the customer's qualified supplier "
                        "list for the next stage and a preliminary supply arrangement has "
                        "been signed; the first-stage purchase scale is clearly below the "
                        "optimistic 'CNY 12-15 billion' estimate but is materially "
                        "meaningful for the current business; first deliveries are "
                        "expected to start in Q4. Close $57.40.",
             "source": "HCM announcement"},
            {"kind": "price", "summary": "Closed at $57.40", "price_usd": 57.40},
        ],
        "2026-09-07": [
            {"kind": "research",
             "summary": "Several institutions update earnings forecasts; attention "
                        "shifts to the actual supply share, new-line utilization and "
                        "whether the customer expands purchases. Close $60.20.",
             "source": "Institutional research"},
            {"kind": "price", "summary": "Closed at $60.20", "price_usd": 60.20},
        ],
        "2026-09-11": [
            {"kind": "media",
             "summary": "The first supply plan becomes clearer and sentiment stabilizes; "
                        "the project has turned into a formal partnership, though its "
                        "scale falls short of the CNY 12-15 billion rumor. The stock is "
                        "steady at $59-61, about 40% above the T0 price of $42.60.",
             "source": "Market"},
            {"kind": "price", "summary": "Range $59-61", "price_usd": 60.0},
        ],
    }


# Branch C 无第三条市场世界:默认走 Timeline A(03 第八节)
BRANCH_TO_TIMELINE = {"A": "A", "B": "B", "C": "A"}


def t0_events_only() -> Dict[str, List[dict]]:
    """只要 T0(咨询当天)这一天的事件。

    分支判定发生在 T0 **之后**(01 §六),所以"还没判定"的那一段世界必须与分支无关 ——
    不能再靠"拿 A 的时间线当兜底"那种隐式做法(2026-10-10 用户要求剔除全部预设)。
    """
    return {"2026-08-27": _t0_events()}


def build_timeline(branch: str):
    """按分支取时间线。

    `branch="undetermined"`(**尚未判定**):返回的只是 T0 那一天 —— 判定落地之后
    `MavisBridge._decide_branch_from_t0` 会用真分支的时间线把后续节点整段换掉。
    这里**不是**"把未定当成 A",而是"未定时只需要 T0"。
    """
    if branch == "undetermined":
        return t0_events_only()
    if branch == "B":
        return timeline_b()
    return timeline_a()  # A 与 C
