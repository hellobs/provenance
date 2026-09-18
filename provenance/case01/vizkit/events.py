# -*- coding: utf-8 -*-
"""事件归一化:把 run 记录 / mavis 实时回调,变成统一的可视化事件流。

契约直接采用 mavis `runtime.protocol` 的键名(前端/Unity/平台都按它解析):
- {"type":"init","agents":[...],"time":str}
- {"type":"time","time":str}
- {"type":"agent","name","coord","path","action","location","currently","time"}
- {"type":"chat_line","speaker","text"}
- {"type":"story","id","event_type","content","targets","time"}
- {"type":"snapshot","agents":{name: {...}}, "time"}
任何插件(小镇 Phaser / 审查页 / 平台嵌入 / 控制台)都只按这套键读取。
"""
from typing import Dict, List, Optional


def init_event(agents: List[str], time: str = "") -> dict:
    return {"type": "init", "agents": list(agents), "time": time}


def time_event(time: str, step: Optional[int] = None) -> dict:
    ev = {"type": "time", "time": time}
    if step is not None:
        ev["step"] = int(step)
    return ev


def as_text(value) -> str:
    """把 mavis 的字段安全转成字符串。

    `AgentState.action` 实际是 `action.to_dict()`(dict),`location` 可能是列表;
    前端按字符串处理(`msg.action.slice(...)`),这里统一收敛,避免前端 TypeError。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("describe", "description", "text", "summary", "action"):
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                return v
        parts = [as_text(v) for v in value.values() if v not in (None, "", [], {})]
        return " / ".join(p for p in parts if p)
    if isinstance(value, (list, tuple)):
        return ":".join(as_text(v) for v in value if v not in (None, ""))
    return str(value)


def agent_event(name: str, coord=None, action="", location="",
                currently="", path=None, time: str = "",
                role_type: str = "user") -> dict:
    return {
        "type": "agent", "name": name,
        "coord": list(coord) if coord else [],
        "path": list(path or []),
        "action": as_text(action),
        "location": as_text(location),
        "currently": as_text(currently),
        "role_type": role_type or "user",
        "time": time,
    }


def chat_event(speaker: str, text: str, time: str = "") -> dict:
    ev = {"type": "chat_line", "speaker": speaker, "text": text}
    if time:
        ev["time"] = time
    return ev


def story_event(ev: dict, time: str = "") -> dict:
    return {
        "type": "story", "id": ev.get("id", ""),
        "event_type": ev.get("event_type", ""), "content": ev.get("content", ""),
        "targets": list(ev.get("targets") or []),
        "time": time or str(ev.get("time", "")),
    }


def snapshot_event(agents: Dict[str, dict], time: str = "") -> dict:
    return {"type": "snapshot", "agents": dict(agents or {}), "time": time}


def normalize_record(record: dict) -> dict:
    """兼容两种记录布局:bridge 原始记录(顶层 nodes)与映射后的 run.json(injector.nodes)。"""
    if record.get("nodes"):
        return record
    inner = record.get("injector")
    if isinstance(inner, dict) and inner.get("nodes"):
        merged = dict(record)
        merged["nodes"] = inner.get("nodes")
        merged.setdefault("roles", inner.get("roles") or [])
        merged.setdefault("run_id", inner.get("run_id") or record.get("run_id", ""))
        return merged
    return record


def events_from_record(record: dict) -> List[dict]:
    """一份 run 记录 → 可视化事件序列(离线回放)。

    兼容 bridge 原始记录与映射后的 run.json(节点在 injector.nodes)。
    顺序:init → 逐节点(time → story* → agent* → chat_line* → snapshot)
    """
    record = normalize_record(record)
    nodes = record.get("nodes") or []
    out: List[dict] = [init_event(list(record.get("roles") or []))]
    for node in nodes:
        t = str(node.get("date", ""))
        out.append(time_event(t, node.get("step")))
        for ev in node.get("events") or []:
            out.append(story_event(ev, t))
        for name, state in (node.get("agents") or {}).items():
            out.append(agent_event(name, (state or {}).get("coord"),
                                   (state or {}).get("action", ""),
                                   (state or {}).get("location", ""),
                                   (state or {}).get("currently", ""),
                                   (state or {}).get("path"), t))
        for block in node.get("dialogue") or []:
            if not isinstance(block, dict):
                continue
            for lines in block.values():
                for line in lines or []:
                    if isinstance(line, (list, tuple)) and len(line) == 2:
                        out.append(chat_event(str(line[0]), str(line[1]), t))
        if node.get("agents"):
            out.append(snapshot_event({n: dict(st) for n, st in node["agents"].items()}, t))
        elif node.get("world_state"):
            out.append(snapshot_event({"world": node["world_state"]}, t))
    return out


def live_hooks(fanout) -> Dict[str, object]:
    """返回可直接传给 mavis Simulator 的回调(在线事件流,协议键)."""
    def on_agent(name, state, step, sim_time):
        state = state or {}
        fanout.emit(agent_event(name, state.get("coord"), state.get("action", ""),
                                state.get("location", ""), state.get("currently", ""),
                                state.get("path"), sim_time))

    def on_step(config):
        fanout.emit(time_event(config.get("time", ""), config.get("step")))

    def on_chat_line(speaker, text):
        fanout.emit(chat_event(speaker, text))

    def on_story(ev):
        fanout.emit(story_event(dict(ev or {})))

    return {"on_agent": on_agent, "on_step": on_step,
            "on_chat_line": on_chat_line, "on_story": on_story}
