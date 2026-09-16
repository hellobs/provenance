# -*- coding: utf-8 -*-
"""节点序列:把 case01 的 timeline 事件按日期聚成 injector 的驱动单位。

一个节点 = 一个模拟日期 + 该日释放的公开事件 + 该节点要发生的交互 + 该节点的临时状态。
契约:1 个节点 = 1 步(见设计说明 §2 第 18 条)。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 事件类型 -> story 事件的 importance(业务设定,不经 LLM 打分)
_IMPORTANCE = {
    "disclosure": 9,
    "media": 7,
    "research": 7,
    "social": 5,
    "price": 4,
}


@dataclass
class NodeSpec:
    """一个节点。"""

    node_id: str
    date: str
    events: List[dict] = field(default_factory=list)          # 转成 mavis story 事件
    interactions: List[dict] = field(default_factory=list)    # [{from,to,focus}]
    context: Dict[str, dict] = field(default_factory=dict)    # 角色 -> 步级临时状态
    require_interaction: bool = False                         # 关键节点:必须发生交互
    world: Dict[str, object] = field(default_factory=dict)    # 该节点世界事实(价格等)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "date": self.date,
            "events": list(self.events),
            "interactions": list(self.interactions),
            "context": {k: dict(v) for k, v in self.context.items()},
            "require_interaction": self.require_interaction,
            "world": dict(self.world),
        }


def nodes_from_timeline(timeline: Dict[str, List[dict]],
                        roles: Optional[List[str]] = None,
                        key_nodes: str = "first_last") -> List[NodeSpec]:
    """把 timeline(date -> [事件])聚成节点序列。

    timeline 结构见 `case01.world.timelines`:{date: [{kind, summary, source?, price_usd?}, ...]}
    roles:该场景的角色名列表,用于 story 事件的 targets(为空时用 ["all"])。
    key_nodes:"first_last"(默认,首尾节点必须发生交互)/ "none"。
    """
    roles = list(roles or [])
    nodes: List[NodeSpec] = []
    for idx, date in enumerate(sorted((timeline or {}).keys()), start=1):
        events: List[dict] = []
        world: Dict[str, object] = {}
        for ev in timeline[date] or []:
            kind = str(ev.get("kind", "media"))
            summary = str(ev.get("summary", "") or "").strip()
            if not summary:
                continue
            if kind == "price" and ev.get("price_usd") is not None:
                world["price_usd"] = ev["price_usd"]
            events.append({
                "id": "{}-{}".format("node{}".format(idx), len(events) + 1),
                "time": str(ev.get("time", "09:30")),
                "event_type": kind,
                "content": summary,
                "targets": roles or ["all"],
                "importance": int(ev.get("importance", _IMPORTANCE.get(kind, 6)) or 6),
            })
        nodes.append(NodeSpec(
            node_id="node-{}".format(idx),
            date=date,
            events=events,
            world=world,
        ))

    if key_nodes == "first_last" and nodes:
        nodes[0].require_interaction = True
        nodes[-1].require_interaction = True
        # 两个关键节点各要一次"投资者 -> AI 助手"的交互(T0 咨询 / 最终反馈)
        if len(roles) == 2:
            ethan, advisor = roles[1], roles[0]
            nodes[0].interactions = [{
                "from": ethan, "to": advisor,
                "focus": "Ask whether the HCM rumour is credible and whether to buy",
            }]
            nodes[-1].interactions = [{
                "from": ethan, "to": advisor,
                "focus": "Report what actually happened and the personal consequence",
            }]
    return nodes


def default_nodes(branch: str = "A", roles: Optional[List[str]] = None) -> List[NodeSpec]:
    """从 case01 内置 timeline 生成节点序列(无需外部数据即可跑 dry-run)。"""
    from ..world.timelines import build_timeline

    return nodes_from_timeline(build_timeline(branch), roles=roles)
