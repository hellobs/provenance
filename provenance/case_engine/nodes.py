# -*- coding: utf-8 -*-
"""节点序列:把 timeline 事件按日期聚成注入驱动的单位(引擎通用)。

一个节点 = 一个模拟日期 + 该日释放的公开事件 + 该节点要发生的交互 +
该节点的临时状态。契约:1 个节点 = 1 步(见设计说明 §2 第 18 条)。

通用化要点(2026-09-20,从原 case 仓 injector/nodes 模块迁移):
- 事件类型->importance 映射属通用事件契约,保留;
- final_date、关键节点交互的 focus 文案全部由调用方注入(引擎零业务词)。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 事件类型 -> story 事件的 importance(通用契约,不经 LLM 打分)
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
    events: List[dict] = field(default_factory=list)          # 转成 story 事件
    interactions: List[dict] = field(default_factory=list)    # [{from,to,focus}]
    context: Dict[str, dict] = field(default_factory=dict)    # 角色 -> 步级临时状态
    require_interaction: bool = False                         # 关键节点:必须发生交互
    world: Dict[str, object] = field(default_factory=dict)    # 该节点世界事实(如价格)

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
                        key_nodes: str = "first_last",
                        final_date: str = "",
                        append_final: bool = True,
                        interaction_templates: Dict[str, dict] = None) -> List[NodeSpec]:
    """把 timeline(date -> [事件])聚成节点序列。

    timeline 结构:{date: [{kind, summary, source?, price_usd?}, ...]}
    roles:该场景的角色名列表,用于 story 事件的 targets(为空时用 ["all"])。
    key_nodes:"first_last"(默认,首尾节点必须发生交互)/ "none"。
    final_date:最终反馈日(由场景指定);晚于最后一条事件时补一个空节点,
        使"最终反馈"落在正确日期上。
    interaction_templates: 关键节点交互模板 {"first": {from,to,focus},
        "final": {from,to,focus}};focus 文案由场景提供(引擎不内置)。
    """
    roles = list(roles or [])
    templates = interaction_templates or {}
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

    if append_final and final_date and (not nodes or nodes[-1].date < final_date):
        nodes.append(NodeSpec(node_id="node-final", date=final_date, events=[]))

    if key_nodes == "first_last" and nodes:
        nodes[0].require_interaction = True
        nodes[-1].require_interaction = True
        # 两个关键节点各要一次"对角(提问方 -> 评估方)"的交互(T0 咨询 / 最终反馈)
        if len(roles) == 2 and "first" in templates:
            t0 = templates["first"]
            nodes[0].interactions = [{
                "from": t0.get("from", roles[1]),
                "to": t0.get("to", roles[0]),
                "focus": t0.get("focus", ""),
            }]
            tf = templates.get("final") or t0
            nodes[-1].interactions = [{
                "from": tf.get("from", roles[1]),
                "to": tf.get("to", roles[0]),
                "focus": tf.get("focus", t0.get("focus", "")),
            }]
    return nodes