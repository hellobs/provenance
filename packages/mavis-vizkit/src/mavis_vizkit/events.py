# -*- coding: utf-8 -*-
"""事件归一化:把 run 记录 / 实时回调,变成统一的可视化事件流。

契约键名(前端/Unity/平台都按它解析,见 README):
- {"type":"init","agents":[...],"time":str}
- {"type":"time","time":str}
- {"type":"agent","name","coord","path","action","location","currently","time"}
- {"type":"chat_line","speaker","text"}
- {"type":"story","id","event_type","content","targets","time"}
- {"type":"snapshot","agents":{name: {...}}, "time"}

记录布局:节点列表可以挂在任意键下。多数调用方用默认的 `nodes`;
若调用方把节点挂在别的键下(如带版次前缀的映射段),传 `nodes_key=` 即可,
**本包不写死任何调用方的布局键名**。
"""
from typing import Dict, List, Optional


def init_event(agents: List[str], time: str = "") -> dict:
    return {"type": "init", "agents": list(agents), "time": time}


def time_event(time: str, step: Optional[int] = None) -> dict:
    ev = {"type": "time", "time": time}
    if step is not None:
        ev["step"] = int(step)
    return ev


def _event_text(value: dict) -> str:
    """事件对象 -> 可读文本(优先 describe,退化为 主语 谓词 宾语)。"""
    describe = str(value.get("describe") or "").strip()
    if describe:
        return describe
    bits = [str(value.get(k) or "").strip()
            for k in ("subject", "predicate", "object")]
    return " ".join(b for b in bits if b)


def as_text(value) -> str:
    """把常见字段安全转成字符串。

    `action` 可能是 dict(形如 {"event": {...}, "start": ..., "duration": ...}),
    `location` 可能是 list;前端按字符串处理,这里统一收敛,避免 TypeError。
    优先取内层 event 的可读描述,不把 start/duration 拼进来。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("event")
        if isinstance(inner, dict):
            text = _event_text(inner)
            if text:
                return text
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


def normalize_record(record: dict, nodes_key: str = "nodes",
                     meta_key: Optional[str] = None) -> dict:
    """把记录归一成"节点挂在 nodes_key 下"的统一形态,供回放/插件消费。

    兼容两种布局:
    1) 节点直接挂在 `nodes_key`(默认 "nodes")下 → 原样返回;
    2) 节点挂在某个嵌套段(其键由 `meta_key` 指定)下 → 上提合并。
    两个键名都可由调用方指定,**包内不写死调用方的布局名**。

    缺省行为:未给 `meta_key` 时只处理 `nodes_key` 直接命中,不做任何上提——
    需要上提的调用方(其映射段键名是业务侧约定)自行传 `meta_key=`。
    """
    if record.get(nodes_key):
        return record
    if meta_key:
        inner = record.get(meta_key)
        if isinstance(inner, dict) and inner.get(nodes_key):
            merged = dict(record)
            merged[nodes_key] = inner.get(nodes_key)
            merged.setdefault("roles", inner.get("roles") or [])
            merged.setdefault("run_id", inner.get("run_id") or record.get("run_id", ""))
            return merged
    return record


def events_from_record(record: dict, nodes_key: str = "nodes",
                       meta_key: Optional[str] = None) -> List[dict]:
    """一份 run 记录 → 可视化事件序列(离线回放)。

    顺序:init → 逐节点(time → story* → agent* → chat_line* → snapshot)。
    `nodes_key` / `meta_key` 传给 normalize_record;节点挂在别处时由调用方指定。
    """
    record = normalize_record(record, nodes_key=nodes_key, meta_key=meta_key)
    nodes = record.get(nodes_key) or []
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
    """返回可直接传给 Simulator 的回调(在线事件流,协议键)。"""
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