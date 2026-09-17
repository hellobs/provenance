# -*- coding: utf-8 -*-
"""injector 记录 -> case01 run.json 兼容记录（阶段 3 的字段对齐层）。

映射原则:
- 只做"结构兼容":case01 run.json 的顶层键必须齐;取值来自 injector 的节点记录。
- 取不到真值的字段（如逐日资金状态）显式留空并在 `compat` 里标注,不伪造。
- 多出来的 `injector` 段是新增键,平台侧按"只增不改"读取即可。
"""
from typing import Dict, List, Optional

# 角色名 -> case01 turn 的 speaker 标识
ROLE_TO_SPEAKER = {"Ethan Lin": "ethan", "Investment AI": "ai"}

# case01 run.json 的顶层键（对照 runs/demo-* 实测）
CASE01_TOP_KEYS = [
    "run_id", "start_date", "end_date", "branch", "branch_action",
    "turns", "retrievals", "events", "state_history", "final_feedback",
    "audit", "condition_monitor", "reflection", "router",
]


def _turns(nodes: List[dict]) -> List[dict]:
    turns: List[dict] = []
    for node in nodes:
        for block in node.get("dialogue") or []:
            if not isinstance(block, dict):
                continue
            for lines in block.values():
                for line in lines or []:
                    if not isinstance(line, (list, tuple)) or len(line) != 2:
                        continue
                    speaker, text = line
                    turns.append({
                        "speaker": ROLE_TO_SPEAKER.get(str(speaker), str(speaker)),
                        "date": node.get("date", ""),
                        "text": str(text),
                    })
    return turns


def _events(nodes: List[dict]) -> List[dict]:
    events: List[dict] = []
    for node in nodes:
        for ev in node.get("events") or []:
            entry = {
                "date": ev.get("date", node.get("date", "")),
                "kind": ev.get("event_type", ""),
                "summary": ev.get("content", ""),
            }
            price = (node.get("world") or {}).get("price_usd")
            if ev.get("event_type") == "price" and price is not None:
                entry["price_usd"] = price
            events.append(entry)
    return events


def _retrievals(nodes: List[dict]) -> List[dict]:
    """mavis 路径下"检索"= 该节点注入的材料清单（injector 侧记录,不伪造检索结果）。"""
    out: List[dict] = []
    for node in nodes:
        docs = [{
            "kind": ev.get("event_type", ""),
            "source": ev.get("source", ""),
            "summary": ev.get("content", ""),
        } for ev in node.get("events") or []]
        out.append({
            "date": node.get("date", ""),
            "query": (node.get("interactions") or [{}])[0].get("focus", "") if node.get("interactions") else "",
            "injected": docs,
            "mode": "injection",   # 与 case01 的向量检索区分
        })
    return out


def _audit(nodes: List[dict]) -> List[dict]:
    audit: List[dict] = []
    for node in nodes:
        date = node.get("date", "")
        audit.append({
            "t": date, "action": "release_events",
            "node_id": node.get("node_id", ""),
            "count": len(node.get("events") or []),
        })
        for req in node.get("interactions") or []:
            audit.append({
                "t": date, "action": "interaction",
                "node_id": node.get("node_id", ""),
                "from": req.get("from", ""), "to": req.get("to", ""),
                "focus": req.get("focus", ""),
                "started": bool(node.get("interaction_started")),
                "retries": int(node.get("retries", 0) or 0),
            })
    return audit


def _final_feedback(nodes: List[dict]) -> dict:
    if not nodes:
        return {"date": "", "ethan": "", "ai": ""}
    last = nodes[-1]
    turns = _turns([last])
    ethan = next((t["text"] for t in reversed(turns) if t["speaker"] == "ethan"), "")
    ai = next((t["text"] for t in reversed(turns) if t["speaker"] == "ai"), "")
    return {"date": last.get("date", ""), "ethan": ethan, "ai": ai}


def _state_history(nodes: List[dict]) -> List[dict]:
    """优先用事实层快照(case01 World 同口径);没有则留空。"""
    out: List[dict] = []
    for node in nodes:
        state = node.get("world_state")
        if state:
            out.append({"date": node.get("date", ""), "state": dict(state)})
    return out


def to_case01_record(record: dict, branch: str = "",
                     t0_rounds: int = 0, c_plan: Optional[dict] = None) -> dict:
    """把 injector 记录映射成 case01 run.json 兼容结构。"""
    nodes = list(record.get("nodes") or [])
    compat_gaps: List[str] = []

    state_history = _state_history(nodes)
    if not state_history:
        compat_gaps.append("state_history:未接入 case01 world 状态机,留空")
    if not record.get("world_audit"):
        compat_gaps.append("world_audit:未接入 case01 world 审计,留空")
    if branch == "C" and not record.get("condition_monitor"):
        compat_gaps.append("condition_monitor:未发生条件监测(真实运行且带 C 方案时由事实层填充)")
    compat_gaps.append("reflection/router:由 case01.reflection 在运行后补齐,此处留空")

    branch = branch or record.get("branch", "")
    if branch == "C":
        has_c_position = (
            any((s.get("state") or {}).get("hcm_shares") for s in state_history)
            or any(a.get("action") == "buy_position"
                   for a in (record.get("world_audit") or [])))
        if not has_c_position:
            compat_gaps.append(
                "Branch C 实际仓位:未建仓(需 Ollama 解析 T0 方案;"
                "dry-run 或条件未触发则不建仓)")

    mapped = {
        "run_id": record.get("run_id", ""),
        "start_date": nodes[0].get("date", "") if nodes else "",
        "end_date": nodes[-1].get("date", "") if nodes else "",
        "branch": branch or "",
        "branch_action": {
            "timeline": branch or "",
            "judge": "preset(mavis 路径不调 LLM judge)",
            "c_plan": dict(c_plan or {}),
            "t0_rounds": int(t0_rounds or 0),
        },
        "turns": _turns(nodes),
        "retrievals": _retrievals(nodes),
        "events": _events(nodes),
        "state_history": state_history,
        "final_feedback": _final_feedback(nodes),
        # world 审计在前(事实层),injector 注入/交互记录在后
        "audit": list(record.get("world_audit") or []) + _audit(nodes),
        "condition_monitor": list(record.get("condition_monitor") or []),
        "reflection": {},
        "router": {},
        # 运行摘要（新增键,便于 CLI/平台快速读取）
        "summary": record.get("summary", {}),
        # 新增段：injector 的原始节点记录（平台按需读取,不影响既有解析）
        "injector": {
            "schema_version": record.get("schema_version", ""),
            "mode": record.get("mode", ""),
            "roles": record.get("roles", []),
            "scenario_dir": record.get("scenario_dir", ""),
            "nodes": nodes,
            "summary": record.get("summary", {}),
        },
        "compat": {
            "level": "schema",
            "gaps": compat_gaps,
        },
    }
    # 映射完成后校验顶层键是否齐（在字典构造之后计算,避免自引用）
    mapped["compat"]["missing_keys"] = [k for k in CASE01_TOP_KEYS if k not in mapped]
    return mapped
