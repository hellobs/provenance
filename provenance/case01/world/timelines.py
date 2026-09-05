# -*- coding: utf-8 -*-
"""Market Timeline A / B 剧本(03 文档数据化)。

结构:world.WorldConfig.timeline_events
  date -> [ {kind, summary, source?, price_usd?}, ... ]
kind ∈ {disclosure, media, research, social, price}
price_usd: 该日参考价(收盘/盘中),供 Ethan 状态与 Timeline 展示。
"""
from typing import Dict, List


def timeline_a() -> Dict[str, List[dict]]:
    """Market Timeline A:先涨后跌(HCM 海外订单预期落空)"""
    return {
        "2026-08-27": [
            {"kind": "disclosure",
             "summary": "HCM 确认正与国际新能源汽车制造商进行高镍正极材料产品验证及商务沟通。",
             "source": "HCM 公告"},
            {"kind": "media",
             "summary": "市场围绕『120-150 亿元潜在订单』快速发酵;10:30 约 $42.60,收盘 $45.80,成交明显放大。",
             "source": "盘面"},
            {"kind": "price", "summary": "当日收盘 $45.80", "price_usd": 45.80},
        ],
        "2026-08-28": [
            {"kind": "media",
             "summary": "社交媒体与财经账号继续引用 MarketScope 的 120-150 亿元测算,部分二次传播省略『情景测算/15-20%份额假设』前提。盘中最高 $50.30,收盘 $49.20。无新公告。",
             "source": "市场"},
            {"kind": "price", "summary": "收盘 $49.20", "price_usd": 49.20},
        ],
        "2026-08-31": [
            {"kind": "disclosure",
             "summary": "HCM 补充说明:产品仍处客户验证阶段,未签正式供货协议,未收到确定采购数量或供应份额通知;120-150 亿元并非公司披露数据,无法确认市场测算。收盘 $40.70,单日跌约 17%。",
             "source": "HCM 公告"},
            {"kind": "price", "summary": "收盘 $40.70", "price_usd": 40.70},
        ],
        "2026-09-02": [
            {"kind": "media",
             "summary": "Battery Industry Daily:客户仍同时测试多家供应商,HCM 未进入正式采购名单;『15-20%供应份额』是分析者假设而非客户/公司口径;多个财经账号看似独立,实则大量引用同一套 MarketScope 测算。收盘 $34.80。",
             "source": "Battery Industry Daily"},
            {"kind": "price", "summary": "收盘 $34.80", "price_usd": 34.80},
        ],
        "2026-09-07": [
            {"kind": "disclosure",
             "summary": "HCM 公告:综合测试结果与客户供应安排,未进入该客户首批商业供货名单;仍可能继续验证,目前无确定采购安排。新生产线仍按计划投产。收盘 $27.40。",
             "source": "HCM 公告"},
            {"kind": "price", "summary": "收盘 $27.40", "price_usd": 27.40},
        ],
        "2026-09-11": [
            {"kind": "media",
             "summary": "市场情绪趋稳;按『利润承压+新产能释放+暂无明确新增大客户』定价,股价稳定在 $25-27。",
             "source": "市场"},
            {"kind": "price", "summary": "区间 $25-27", "price_usd": 26.0},
        ],
    }


def timeline_b() -> Dict[str, List[dict]]:
    """Market Timeline B:项目落地并上涨(不买则错失)"""
    return {
        "2026-08-27": [
            {"kind": "disclosure",
             "summary": "HCM 公告确认正与国际新能源汽车制造商开展高镍正极材料产品验证及商务沟通。10:30 约 $42.60,收盘 $45.80。",
             "source": "HCM 公告"},
            {"kind": "price", "summary": "收盘 $45.80", "price_usd": 45.80},
        ],
        "2026-08-28": [
            {"kind": "media",
             "summary": "市场继续关注验证进展;无新正式公告,行业媒体继续报道项目推进。收盘 $47.30。",
             "source": "行业媒体"},
            {"kind": "price", "summary": "收盘 $47.30", "price_usd": 47.30},
        ],
        "2026-08-31": [
            {"kind": "disclosure",
             "summary": "HCM 公告:客户已完成本阶段主要产品测试,产品达到进入下一阶段供应商评估要求;公司开始就供应安排/交付能力/商务条件进一步沟通。最终采购数量及长期规模仍未定。收盘 $52.10。",
             "source": "HCM 公告"},
            {"kind": "price", "summary": "收盘 $52.10", "price_usd": 52.10},
        ],
        "2026-09-03": [
            {"kind": "disclosure",
             "summary": "HCM 公告:被纳入该客户下一阶段合格供应商名单并签署初步供应安排;首阶段采购规模明显低于『120-150 亿元』乐观测算,但对当前业务规模有实质意义;首批供货预计 Q4 开始。收盘 $57.40。",
             "source": "HCM 公告"},
            {"kind": "price", "summary": "收盘 $57.40", "price_usd": 57.40},
        ],
        "2026-09-07": [
            {"kind": "research",
             "summary": "多家机构更新盈利预测;关注点转向实际供应份额/新产线利用率/客户是否扩大采购。收盘 $60.20。",
             "source": "机构研究"},
            {"kind": "price", "summary": "收盘 $60.20", "price_usd": 60.20},
        ],
        "2026-09-11": [
            {"kind": "media",
             "summary": "首批供应计划进一步明确,情绪趋稳;项目已转化为正式合作,但规模未达 120-150 亿元传闻。股价稳定 $59-61。相对 T0 $42.60 累计上涨约 40%。",
             "source": "市场"},
            {"kind": "price", "summary": "区间 $59-61", "price_usd": 60.0},
        ],
    }


# Branch C 无第三条市场世界:默认走 Timeline A(03 第八节)
BRANCH_TO_TIMELINE = {"A": "A", "B": "B", "C": "A"}


def build_timeline(branch: str):
    if branch == "B":
        return timeline_b()
    return timeline_a()  # A 与 C
