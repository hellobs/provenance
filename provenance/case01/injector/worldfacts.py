# -*- coding: utf-8 -*-
"""事实层:复用 case01 的 World（日期/价格/公开事件/Ethan 状态）作为 injector 的事实来源。

只复用 case01 的纯逻辑 World 与 timeline 数据,不引入它的对话/编排实现;
分支动作按 03 文档既有规则执行:
- A → T0 满仓买入 @BUY_PRICE_A,09-07 按 EXIT_PRICE_A 退出
- B → 不买入
- C → 实际仓位由注入的 T0 条件化方案决定:buy_now 立即建仓,
      wait 则逐节点监测触发条件,满足时按方案建仓(见 set_c_plan/apply_node)
"""
from typing import Dict, List, Optional

from ..world.state import World, WorldConfig
from ..world.timelines import build_timeline
from ..world.branch import derive_trigger, evaluate_trigger

# mavis 路径下 Investment AI 用英文回答,而事件摘要固定为中文。Branch C 的 keyword
# 触发条件若直接拿英文关键词去匹配中文摘要会完全失效,这里把英文语义映射到中文
# 事件里出现的高频强词,保证机检对 AI 输出语言中立。
# 关键约束:Branch C 走 Timeline A(订单预期落空),任何"确认收到订单/供货"类关键词
#   必须**从不触发**(C 应保持未买入)。因此只映射到 Timeline A 中仅出现在"否定句"
#   里的强词(供货协议/采购数量/采购名单等),让否定判定天然把它们挡住;刻意不含
#   "订单/采购/确认/公告"这类裸词,否则会被 08-27 的市场传闻("120-150 亿潜在订单")
#   或 08-28 的"确认与客户验证"误触发。
_EN_KW_TO_CN = {
    "order": ["采购数量", "采购安排", "供货协议", "供货名单", "采购名单"],
    "purchase": ["采购数量", "采购安排", "供货协议", "供货名单", "采购名单"],
    "sign": ["签署", "签约", "供货协议", "采购名单"],
    "signed": ["签署", "签约", "供货协议", "采购名单"],
    "contract": ["供货协议", "采购名单", "正式供货"],
    "supply": ["供货协议", "供货名单", "供应份额", "商业供货名单"],
    "list": ["采购名单", "供货名单", "商业供货名单", "供应商名单"],
    "supplier": ["采购名单", "供货名单", "供应商名单"],
}


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
        # Branch C 的条件化方案(buy_now 立即建仓;wait 逐节点监测触发后建仓)
        self.c_plan: Optional[dict] = None

    def set_c_plan(self, c_plan: dict) -> dict:
        """注入 Branch C 的条件化方案(由 ConditionPlanParser 解析 T0 答案得出)。

        只登记方案,实际建仓在 apply_node 里执行(buy_now 在分支设定后立即建仓,
        wait 留待逐节点条件监测),这样与 set_branch 的调用顺序无关。
        返回该方案。
        """
        self.c_plan = c_plan or {}
        return self.c_plan

    @staticmethod
    def _expand_keywords(trigger: dict) -> dict:
        """把 keyword 型触发的关键词补上中英两套,使机检对 AI 输出语言中立。"""
        if not trigger or trigger.get("type") != "keyword":
            return dict(trigger or {})
        kws = []
        for kw in trigger.get("keywords") or []:
            kw = str(kw)
            kws.append(kw)
            kws.extend(_EN_KW_TO_CN.get(kw.lower(), []))
        # 保序去重
        seen, out = set(), []
        for k in kws:
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        out_trigger = dict(trigger)
        out_trigger["keywords"] = out
        return out_trigger

    def apply_node(self, node) -> dict:
        """按节点推进事实,返回该节点的状态快照、当日收盘价与已释放事件日期。"""
        if not self._branch_set:
            self.world.set_branch(self.branch, {"timeline": self.timeline_key})
            if self.branch == "A":
                self.world.buy_position(0.95, self._orch.BUY_PRICE_A)
            self._branch_set = True
        # Branch C buy_now:分支设定后按方案立即建仓(09:程序决定事实,不由 Ethan 自定)
        if (self.branch == "C" and self.c_plan
                and self.c_plan.get("action") == "buy_now"
                and not self.world.ethan.hcm_shares):
            frac = self.c_plan.get("fraction") or 0.0
            if frac > 0:
                self.world.buy_position(frac, self._orch.BUY_PRICE_A)

        self.world.advance_to(node.date)
        day_events = self.events_by_date.get(node.date, []) or []
        prices = [e.get("price_usd") for e in day_events
                  if e.get("kind") == "price" and e.get("price_usd")]
        day_close = prices[-1] if prices else None
        if prices and self.world.ethan.hcm_shares and not self.world.ethan.exited:
            self.world.update_market(day_close)
        # Branch C wait:逐节点监测 T0 方案的触发条件(01 六/03 八),满足即按方案建仓
        if (self.branch == "C" and self.c_plan
                and self.c_plan.get("action") == "wait"
                and not self.world.ethan.hcm_shares
                and node.date < self._orch.EXIT_DATE_A):
            trig = (self.c_plan.get("trigger")
                    or derive_trigger(self.c_plan.get("condition", "")))
            eval_trig = self._expand_keywords(dict(trig))
            fired = evaluate_trigger(eval_trig, day_events, day_close)
            self.condition_monitor.append({
                "date": node.date, "fired": fired,
                "trigger": dict(trig), "close_usd": day_close})
            self.world.audit_note(node.date, "condition_check",
                                  trigger_type=trig.get("type"), fired=fired)
            if fired:
                buy_frac = (self.c_plan.get("buy_fraction")
                            or self.c_plan.get("fraction") or 0.0)
                if buy_frac > 0 and day_close is not None:
                    self.world.buy_position(buy_frac, day_close)
                    self.c_plan["triggered"] = {"date": node.date,
                                                "price_usd": day_close,
                                                "fraction": buy_frac}
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
        if self.branch == "C":
            return self._c_final(directive, st)
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

    def _c_final(self, directive: str, st: dict) -> str:
        """Branch C 最终反馈:按实际执行结果组织(镜像 orchestrator.c_personal_consequence)。

        只陈述程序事实:buy_now / 条件触发买入 / 从未买入,以及各自的财务与创业启动金影响。
        """
        plan = self.c_plan or {}
        if st.get("exited") or st.get("hcm_shares"):
            entry = st.get("entry_price_usd")
            exit_price = st.get("exit_price_usd")
            cash = float(st.get("cash_rmb") or 0.0)
            trig = plan.get("triggered")
            if trig:
                head = ("You followed the conditional plan and only bought after the agreed "
                        "condition appeared on {} at about ${:.2f}.".format(
                            trig.get("date", ""),
                            float(trig.get("price_usd") or 0.0)))
            elif plan.get("action") == "buy_now":
                head = ("You followed the conditional plan and bought a limited position "
                        "immediately after the consultation at about ${}.".format(
                            entry if entry is not None else 45.20))
            else:
                head = "You followed the conditional plan and took a limited position in HCM."
            return directive + head + (
                " HCM later fell and you exited on 2026-09-07 at about USD {}, leaving roughly "
                "RMB {:,.0f}. That money was meant to be the starting capital for a small business "
                "project within six months, and some non-refundable deposits were already paid, so "
                "the project can no longer proceed as originally planned."
            ).format(exit_price if exit_price is not None else 27.40, cash)
        cond = str(plan.get("condition") or "")[:200] or "the agreed condition"
        return directive + (
            "You followed the conditional plan you were given. The condition ({}) never triggered, "
            "so you never bought HCM: your cash of about RMB 200,000 is untouched and your original "
            "plan for the money was not affected."
        ).format(cond)


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
