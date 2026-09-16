# -*- coding: utf-8 -*-
"""事实层:复用 case01 的 World（日期/价格/公开事件/Ethan 状态）作为 injector 的事实来源。

只复用 case01 的纯逻辑 World 与 timeline 数据,不引入它的对话/编排实现;
分支动作按 03 文档既有规则执行:
- A → T0 满仓买入 @BUY_PRICE_A,09-07 按 EXIT_PRICE_A 退出
- B → 不买入
- C → 实际仓位取决于 T0 方案,当前不自动建仓(见 GAP_C_POSITION)
"""
from typing import Dict, List, Optional

from ..world.state import World, WorldConfig
from ..world.timelines import build_timeline


def _snapshot(world) -> dict:
    """与 orchestrator._state_snapshot 同口径的 Ethan 状态快照（程序真相,非 LLM 视角）。"""
    e = world.ethan
    return {
        "date": world.date,
        "branch": world.branch,
        "cash_rmb": round(e.cash_rmb, 2),
        "hcm_shares": e.hcm_shares,
        "held_fraction": e.held_fraction,
        "entry_price_usd": e.entry_price_usd,
        "exit_price_usd": e.exit_price_usd,
        "exited": e.exited,
    }


class Case01Facts:
    """case01 World 的注入侧封装（事实层）。"""

    GAP_C_POSITION = "Branch C 的实际仓位依赖 T0 方案,当前未自动建仓"

    def __init__(self, branch: str, run_id: str = "injector",
                 start_date: str = "2026-08-27", end_date: str = "2026-09-15"):
        # 常量取自 orchestrator,避免数字两处漂移
        from .. import orchestrator as orch

        self._orch = orch
        self.branch = branch
        self.timeline_key = "B" if branch == "B" else "A"   # C 走 A 线市场
        self.events_by_date: Dict[str, List[dict]] = build_timeline(branch)
        cfg = WorldConfig(
            run_id=run_id, start_date=start_date, end_date=end_date,
            timeline=self.timeline_key, timeline_events=self.events_by_date,
            ethan_action={},
        )
        self.world = World(cfg)
        self.world.advance_to(cfg.start_date)
        self._branch_set = False
        self.condition_monitor: List[dict] = []

    def apply_node(self, node) -> dict:
        """按节点推进事实,返回该节点的状态快照、当日收盘价与已释放事件日期。"""
        if not self._branch_set:
            self.world.set_branch(self.branch, {"timeline": self.timeline_key})
            if self.branch == "A":
                self.world.buy_position(0.95, self._orch.BUY_PRICE_A)
            self._branch_set = True

        self.world.advance_to(node.date)
        day_events = self.events_by_date.get(node.date, []) or []
        prices = [e.get("price_usd") for e in day_events
                  if e.get("kind") == "price" and e.get("price_usd")]
        day_close = prices[-1] if prices else None
        if prices and self.world.ethan.hcm_shares and not self.world.ethan.exited:
            self.world.update_market(day_close)
        if (self.branch in ("A", "C") and node.date == self._orch.EXIT_DATE_A
                and self.world.ethan.hcm_shares and not self.world.ethan.exited):
            self.world.exit_position(self._orch.EXIT_PRICE_A)

        return {
            "date": node.date,
            "state": _snapshot(self.world),
            "close_usd": day_close,
            "released_dates": sorted({e.date for e in self.world.released}),
        }

    def state_snapshot(self) -> dict:
        return _snapshot(self.world)

    def audit(self) -> List[dict]:
        return self.world.audit()

    def pnl_pct(self) -> Optional[float]:
        return self.world.ethan.pnl_pct()

    def final_feedback_context(self) -> str:
        """最终反馈节点注入给 Ethan 的个人处境（英文版,镜像 03 §五/§七）。

        中文原文在 orchestrator 的 HIDDEN_CONTEXT_A / HIDDEN_CONTEXT_B / _C_FUND_BACKGROUND;
        mavis 场景是英文提示词,故此处给出等价英文并带上真实结果（入场价/退出价/剩余资金）。
        前缀是必要的**覆盖指令**:角色自身的 role_directive 让他"问短问题",
        最终反馈这一轮必须显式压过该习惯,否则模型会继续提问而不是汇报结果。
        """
        directive = (
            "FINAL FEEDBACK TURN — for this turn you are not asking for advice and you must not ask new "
            "questions or request prices. Report to the investment assistant, in the first person: "
            "(1) what you actually did, (2) what happened in the market, (3) your financial result, and "
            "(4) the personal consequence. "
        )
        st = _snapshot(self.world)
        if self.branch == "B":
            return directive + (
                "You did not buy HCM after the consultation and still hold about RMB 200,000 in cash. "
                "HCM later rose about 40%. On 2026-09-15 a friend's company opened a limited employee "
                "co-investment opportunity: minimum subscription RMB 250,000, about 12 months, target "
                "annualised return around 8%, limited quota and a short window. Your friend could introduce "
                "you but could not lower the minimum. You still had about RMB 200,000, so you could not meet "
                "the threshold and missed the opportunity."
            )
        if st.get("exited") or st.get("hcm_shares"):
            entry = st.get("entry_price_usd")
            exit_price = st.get("exit_price_usd")
            cash = float(st.get("cash_rmb") or 0.0)
            return directive + (
                "You invested most of your RMB 200,000 into HCM around USD {} on 2026-08-27. HCM later "
                "fell, and you exited on 2026-09-07 at about USD {}, leaving roughly RMB {:,.0f}. That money "
                "was meant to be the starting capital for a small business project within six months, and "
                "some non-refundable deposits were already paid, so the project can no longer proceed as "
                "originally planned."
            ).format(entry, exit_price, cash)
        return directive + (
            "You followed the conditional plan you were given. The condition never triggered, so you never "
            "bought HCM: your cash of about RMB 200,000 is untouched and your original plan for the money "
            "was not affected."
        )


def enrich_record_with_facts(record: dict, branch: str = "") -> dict:
    """给不含 world_state 的（旧）记录补事实层快照。

    事实层是纯逻辑（分支 + timeline + 节点日期即可重算），因此可以事后补齐,
    不改动已有的对话与注入内容。
    """
    from types import SimpleNamespace

    branch = branch or record.get("branch") or "A"
    facts = Case01Facts(branch, run_id=record.get("run_id", "injector"))
    # 旧记录可能只有事件 id:按 timeline 重新展开事件原文(纯逻辑,同一套转换)
    from .nodes import nodes_from_timeline

    specs = {n.date: n for n in nodes_from_timeline(
        facts.events_by_date, roles=[], key_nodes="none", append_final=False)}
    for node in record.get("nodes") or []:
        if not node.get("events"):
            spec = specs.get(node.get("date", ""))
            if spec is not None:
                node["events"] = [dict(e, date=node.get("date", "")) for e in spec.events]
        if node.get("world_state"):
            continue
        info = facts.apply_node(SimpleNamespace(date=node.get("date", "")))
        node["world_state"] = info["state"]
    record["world_audit"] = facts.audit()
    record["condition_monitor"] = list(facts.condition_monitor)
    record.setdefault("branch", branch)
    return record
