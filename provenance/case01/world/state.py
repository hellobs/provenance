# -*- coding: utf-8 -*-
"""World / System 状态控制层(GTC Case 01 纯逻辑核心,不依赖 LLM)。

职责(对照 01/03 文档):
- 维护 World 真相:当前模拟日期、Branch、已释放的公开事件、Ethan 私人状态
- Branch 路由结果与 Timeline 推进(数据驱动:Timeline A/B 是配置数据)
- 信息权限控制:对 Investment AI 只暴露「已释放且可检索」的公开信息;
  对 Ethan 只暴露「当前日期普通市场参与者可知」的公开事件 + 自身状态;
  隐藏 Branch/未来时间线/未披露私人信息。
- 程序决定事实,LLM 只负责表达(冲突由上层检测重生成)。

纯逻辑、可单测:不 import LLM/网络。所有日期用字符串 %Y-%m-%d 比较(ISO 字典序)。
"""
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass
class EthanState:
    """Ethan 的事实状态(程序控制,LLM 只表达)"""
    cash_rmb: float = 200_000.0
    hcm_shares: bool = False          # 是否持有 HCM
    held_fraction: float = 0.0        # 投入占总资金(200k)的份额(0~0.95)
    entry_price_usd: Optional[float] = None   # 平均买入价
    exit_price_usd: Optional[float] = None    # 卖出价(已退出时)
    exited: bool = False
    personal_note: str = ""            # 已发生的个人后果(披露节点前不对外)
    hidden_context: str = ""           # 未披露的私人背景(创业启动金等)

    def pnl_pct(self) -> Optional[float]:
        """相对买入价的盈亏比例(简化,不模拟股数/汇率/手续费)"""
        if not self.hcm_shares or self.entry_price_usd is None:
            return None
        ref = self.exit_price_usd if self.exited else self.entry_price_usd
        return (ref - self.entry_price_usd) / self.entry_price_usd

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PublicEvent:
    """公开市场事件(进入 Financial Data / Ethan 可见状态)"""
    date: str
    kind: str          # disclosure | media | research | social | price
    summary: str       # 自然语言摘要
    source: str = ""
    price_usd: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WorldConfig:
    """一次 Run 的配置(数据驱动:剧本即配置)"""
    run_id: str = ""
    start_date: str = "2026-08-27"
    end_date: str = "2026-09-15"
    timeline: str = "A"                    # 由 Branch 决定: A→"A", B→"B", C→"A"
    # Timeline 事件表:日期 → 该日释放的公开事件(按序)
    timeline_events: Dict[str, List[dict]] = field(default_factory=dict)
    # Ethan 交易动作(程序按 Branch 预设)
    ethan_action: dict = field(default_factory=dict)


class World:
    """World/System:持有真相,控制可见性"""

    def __init__(self, config: WorldConfig):
        self.config = config
        self.date = config.start_date
        self.branch: str = ""              # 未定:"" / "A" / "B" / "C"
        self.ethan = EthanState()
        self.released: List[PublicEvent] = []   # 已释放公开事件(全量,时间序)
        self._last_released_date: str = ""      # 上次已释放到的日期(空=尚未释放)
        self.log: List[dict] = []               # 世界动作审计

    # ------------------------------------------------------------------
    # 日期推进
    # ------------------------------------------------------------------
    def advance_to(self, date: str):
        """推进到指定日期,释放 (上次释放点, date] 区间事件(时间序,防重)。

        首次调用(尚未释放任何事件)释放 ≤ date 的全部事件——T0 当天(08-27)
        的公告/媒体即属"当前日期已发生",应立即可见。
        """
        if date < self.date:
            raise ValueError("cannot rewind: {} -> {}".format(self.date, date))
        for d in sorted(self.config.timeline_events.keys()):
            if d > date:
                break
            if self._last_released_date and d <= self._last_released_date:
                continue
            for ev in self.config.timeline_events[d]:
                payload = dict(ev)
                payload.setdefault("date", d)
                pe = PublicEvent(**payload)
                self.released.append(pe)
                self.log.append({"t": d, "action": "release_event",
                                 "kind": pe.kind, "summary": pe.summary[:80]})
        if self.config.timeline_events:
            # 记录已释放到的最大剧本日期
            rel = [d for d in sorted(self.config.timeline_events.keys()) if d <= date]
            if rel:
                self._last_released_date = rel[-1]
        self.date = date

    # ------------------------------------------------------------------
    # Branch 设定(Ethan 交易状态随之初始化)
    # ------------------------------------------------------------------
    def set_branch(self, branch: str, action: dict):
        """记录 Branch 标签与剧本选择。Ethan 交易动作由上层(编排)调用
        buy_position/exit_position 显式执行(一动作一审计,不在这里内联)。"""
        self.branch = branch
        self.config.timeline = action.get("timeline", branch)
        self.ethan = EthanState()  # 重置为初始(20 万现金,无持仓)
        self.log.append({"t": self.date, "action": "set_branch", "branch": branch})

    # ------------------------------------------------------------------
    # 可见性(信息权限核心)
    # ------------------------------------------------------------------
    def investment_ai_visible(self) -> dict:
        """Investment AI 可见:当前日期 + 截至当前已释放的公开事件(可检索)。"""
        return {
            "current_date": self.date,
            "released_events": [e.to_dict() for e in self.released],
        }

    def ethan_visible(self) -> dict:
        """Ethan 可见:当前日期 + 自身状态 + 截至当前已发生的公开事件。
        隐藏:个人后果(private note)只在最终反馈节点披露。
        """
        st = self.ethan.to_dict()
        # 默认不披露个人后果;披露由上层在最终节点调用 ethan_disclose_personal()
        st.pop("personal_note", None)
        st.pop("hidden_context", None)
        return {
            "current_date": self.date,
            "own_state": st,
            "public_events": [e.to_dict() for e in self.released],
        }

    def disclose_personal_consequence(self):
        """最终反馈节点:允许 Ethan 表达个人后果。"""
        return {"personal_note": self.ethan.personal_note,
                "hidden_context": self.ethan.hidden_context}

    # ------------------------------------------------------------------
    # 状态更新(市场推进后,Ethan 持仓随价格变化)
    # ------------------------------------------------------------------
    def update_market(self, price_usd: Optional[float] = None):
        """按当前价格更新 Ethan 账面(简化:只记录参考价;真实盈亏在退出/期末算)"""
        if price_usd is not None and self.ethan.hcm_shares and not self.ethan.exited:
            self.ethan.exit_price_usd = price_usd  # 期末参考价(未退出时)
            self.log.append({"t": self.date, "action": "mark_to_market",
                             "price": price_usd})

    def exit_position(self, price_usd: float):
        """Ethan 卖出(程序预设,如 Branch A 于 09-07 退出)"""
        self.ethan.exited = True
        self.ethan.exit_price_usd = price_usd
        # 简化资产:已投入部分按跌幅计,未投入现金保留
        held_fraction = self.ethan.held_fraction  # 已投入占总资金的份额
        invested = 200_000.0 * held_fraction
        cash_kept = 200_000.0 * (1.0 - held_fraction)
        self.ethan.cash_rmb = cash_kept + invested * (
            price_usd / self.ethan.entry_price_usd)
        self.log.append({"t": self.date, "action": "exit_position",
                         "price": price_usd,
                         "cash": round(self.ethan.cash_rmb, 2)})

    def buy_position(self, fraction: float, price_usd: float):
        """Ethan 买入 HCM(fraction=投入占总资金 200k 的份额,0<fraction<=0.95)。

        Branch A 满仓 ≈0.95(留少量现金);Branch C 按解析出的条件化仓位(如 0.2)。
        """
        if self.ethan.hcm_shares:
            raise ValueError("already holding HCM")
        f = max(0.0, min(fraction, 0.95))
        self.ethan.hcm_shares = True
        self.ethan.held_fraction = f
        self.ethan.entry_price_usd = price_usd
        self.ethan.cash_rmb = 200_000.0 * (1.0 - f)
        self.log.append({"t": self.date, "action": "buy_position",
                         "fraction": f, "price": price_usd,
                         "cash": round(self.ethan.cash_rmb, 2)})

    # ------------------------------------------------------------------
    # 审计
    # ------------------------------------------------------------------
    def audit_note(self, t: str, action: str, **kw):
        """写入一条审计记录(非状态变更的观测/判定,如条件监测)。"""
        self.log.append({"t": t, "action": action, **kw})

    def audit(self) -> List[dict]:
        return list(self.log)
