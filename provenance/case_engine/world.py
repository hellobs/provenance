# -*- coding: utf-8 -*-
"""World / System 状态控制层(引擎通用核心,不依赖 LLM)。

职责:
- 维护 World 真相:当前模拟日期、Branch、已释放的公开事件、主角(private)状态
- Branch 路由结果与 Timeline 推进(数据驱动:Timeline 是配置数据)
- 信息权限控制:对被评估方(AI 工具)只暴露「已释放且可检索」的公开信息;
  对主角(user)只暴露「当前日期普通参与者可知」的公开事件 + 自身状态;
  隐藏 Branch/未来时间线/未披露私人信息。
- 程序决定事实,LLM 只负责表达(冲突由上层检测重生成)。

通用化要点(2026-09-20,从 case01/world/state.py 迁移):
- 主角状态字段不硬编码,由 `world.state_schema` 动态构建(见 EntityState)。
- 事件数值用通用 `value` 承载(上游场景定义其语义,如价格/数额)。
- 买卖/退出等业务动作收敛为 `update_state(key, value)` + 审计,由场景上层编排。

纯逻辑、可单测:不 import LLM/网络/任何业务包。日期用字符串 %Y-%m-%d 比较(ISO 字典序)。
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any


@dataclass
class EntityState:
    """主角的 private 状态(程序控制,LLM 只表达)。字段由 schema 动态构建。

    schema: Dict[str, {"initial": Any, "type": str}] —— 来自 scenario.yaml 的
    `world.state_schema`。运行时通过 get/set/to_dict 访问,不写死字段名。
    """
    schema: Dict[str, dict] = field(default_factory=dict)
    _values: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for name, spec in (self.schema or {}).items():
            self._values[name] = spec.get("initial")

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value

    def has(self, key: str) -> bool:
        return self._values.get(key)

    def to_dict(self) -> dict:
        return dict(self._values)

    # 预留:按 schema 的数值字段计算相对变化(如涨跌)。场景无需时可忽略。
    def delta_pct(self, ref_key: str, cur_key: str) -> Optional[float]:
        try:
            ref, cur = float(self._values.get(ref_key)), float(self._values.get(cur_key))
        except (TypeError, ValueError):
            return None
        if not ref:
            return None
        return (cur - ref) / ref


@dataclass
class PublicEvent:
    """公开事件(进入检索库 / 主角可见状态)。数值字段用 value 承载场景语义。"""
    date: str
    kind: str            # 场景定义:disclosure | media | research | social | price ...
    summary: str         # 自然语言摘要
    source: str = ""
    value: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WorldConfig:
    """一次 Run 的配置(数据驱动:剧本即配置)。"""
    run_id: str = ""
    start_date: str = ""
    end_date: str = ""
    timeline: str = ""                    # 由 Branch 决定
    timeline_events: Dict[str, List[dict]] = field(default_factory=dict)
    # 主角动作(程序按 Branch 预设),具体字段由场景上层定义
    action: dict = field(default_factory=dict)


class World:
    """World/System:持有真相,控制可见性。"""

    def __init__(self, config: WorldConfig, state_schema: Optional[Dict[str, dict]] = None):
        self.config = config
        self.date = config.start_date
        self.branch: str = ""              # 未定:"" / 场景定义的分支 id
        self.agent = EntityState(state_schema or {})
        self.released: List[PublicEvent] = []     # 已释放公开事件(全量,时间序)
        self._last_released_date: str = ""        # 上次已释放到的日期(空=尚未释放)
        self.log: List[dict] = []                 # 世界动作审计

    # ------------------------------------------------------------------
    # 日期推进
    # ------------------------------------------------------------------
    def advance_to(self, date: str):
        """推进到指定日期,释放 (上次释放点, date] 区间事件(时间序,防重)。

        首次调用(尚未释放任何事件)释放 ≤ date 的全部事件。
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
                if "value" in payload and payload["value"] is None:
                    payload["value"] = None
                pe = PublicEvent(**payload)
                self.released.append(pe)
                self.log.append({"t": d, "action": "release_event",
                                 "kind": pe.kind, "summary": pe.summary})
        if self.config.timeline_events:
            rel = [d for d in sorted(self.config.timeline_events.keys()) if d <= date]
            if rel:
                self._last_released_date = rel[-1]
        self.date = date

    # ------------------------------------------------------------------
    # Branch 设定
    # ------------------------------------------------------------------
    def set_branch(self, branch: str, action: dict):
        """记录 Branch 标签与剧本选择。主角状态重置为 schema 初始值。"""
        self.branch = branch
        self.config.timeline = action.get("timeline", branch)
        self.agent = EntityState(self.agent.schema)  # 重置为初始
        self.log.append({"t": self.date, "action": "set_branch", "branch": branch})

    # ------------------------------------------------------------------
    # 可见性(信息权限核心)
    # ------------------------------------------------------------------
    def tool_visible(self) -> dict:
        """被评估 AI 工具可见:当前日期 + 截至当前已释放的公开事件(可检索)。"""
        return {
            "current_date": self.date,
            "released_events": [e.to_dict() for e in self.released],
        }

    def user_visible(self, private_keys_to_hide: Optional[List[str]] = None) -> dict:
        """主角可见:当前日期 + 自身状态 + 已发生公开事件。

        private_keys_to_hide: 要默认隐藏的状态字段名(如个人后果/隐藏背景)。
        披露由上层在最终节点调用 disclose()。不传则展示全部字段。
        """
        st = self.agent.to_dict()
        if private_keys_to_hide:
            for k in private_keys_to_hide if isinstance(private_keys_to_hide, (list, tuple)) else [private_keys_to_hide]:
                st.pop(k, None)
        return {
            "current_date": self.date,
            "own_state": st,
            "public_events": [e.to_dict() for e in self.released],
        }

    def disclose(self, keys: List[str]) -> dict:
        """最终反馈节点:允许主角表达延迟披露的私人信息。"""
        return {k: self.agent.get(k, None) for k in keys}

    # ------------------------------------------------------------------
    # 状态更新
    # ------------------------------------------------------------------
    def update_state(self, key: str, value: Any):
        """统一状态更新入口(买卖/退出/市价等由场景上层以 named action 调用)。"""
        old = self.agent.get(key)
        self.agent.set(key, value)
        self.log.append({"t": self.date, "action": "update_state", "key": key,
                         "from": old, "to": value})

    # ------------------------------------------------------------------
    # 审计
    # ------------------------------------------------------------------
    def audit_note(self, t: str, action: str, **kw):
        """写入一条审计记录(非状态变更的观测/判定)。"""
        self.log.append({"t": t, "action": action, **kw})

    def audit(self) -> List[dict]:
        return list(self.log)