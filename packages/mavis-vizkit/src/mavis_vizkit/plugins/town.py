# -*- coding: utf-8 -*-
"""小镇风格插件:把统一事件流翻译成现有 Phaser 前端可消费的消息序列。

要点
----
1. 契约与 `mavis.runtime.protocol` 一致(init/snapshot/agent/chat_line/time/story),
   前端按这些键名解析,不需要知道事件来自哪个场景;
2. 角色贴图别名由调用方传入:`alias = {角色名: 素材池里的贴图名}`,
   本包不内置任何角色的业务映射;
3. 坐标回退:节点缺逐节点坐标时,用场景配置里的初始坐标占位(场景目录也由调用方给),
   否则不落任何本仓路径。
"""
import json
import os
from typing import Dict, List, Optional

from .. import Visualizer, register


def scenario_coords(scenario_dir: str, roles) -> Dict[str, list]:
    """从场景配置读角色初始坐标(坐标回退用)。"""
    out: Dict[str, list] = {}
    for role in (roles or []):
        p = os.path.join(scenario_dir, "agents", role, "agent.json")
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                cfg = json.load(f)
            coord = cfg.get("coord")
            if coord:
                out[role] = list(coord)
        except Exception:
            continue
    return out


class TownVisualizer(Visualizer):
    """小镇风格插件:产出前端消息;可选写入 JSONL 回放文件。"""

    name = "town"

    def __init__(self, alias: Optional[dict] = None,
                 roles: Optional[List[str]] = None,
                 scenario_dir: Optional[str] = None, outbox_path: str = "",
                 nodes_key: str = "nodes", meta_key: Optional[str] = None):
        if not alias:
            raise ValueError(
                "town 插件需要 alias(角色→贴图名映射),由调用方提供,不应猜默认值")
        if not scenario_dir:
            raise ValueError(
                "town 插件需要 scenario_dir(角色坐标场景目录),由调用方提供,"
                "不应回退到任何本仓路径")
        self.alias = dict(alias)
        self.roles = list(roles or self.alias.keys())
        self.scenario_dir = scenario_dir
        self.nodes_key = nodes_key
        self.meta_key = meta_key
        self.outbox_path = outbox_path
        self.fallback = scenario_coords(scenario_dir, self.roles)
        self.outbox: List[dict] = []
        self._fh = None

    # ------------------------------------------------------------------
    def on_event(self, event: dict) -> None:
        msg = self.to_frontend(event)
        if not msg:
            return
        self.outbox.append(msg)
        if self.outbox_path:
            if self._fh is None:
                os.makedirs(os.path.dirname(os.path.abspath(self.outbox_path)), exist_ok=True)
                self._fh = open(self.outbox_path, "a", encoding="utf-8")
            self._fh.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def on_record(self, record: dict) -> None:
        from ..events import events_from_record

        for ev in events_from_record(self._with_coords(record),
                                     nodes_key=self.nodes_key, meta_key=self.meta_key):
            self.on_event(ev)

    # ------------------------------------------------------------------
    def to_frontend(self, event: dict) -> dict:
        """统一事件 -> 前端消息(协议键一致,补贴图别名与坐标回退)。"""
        etype = event.get("type")
        if etype == "agent":
            name = str(event.get("name", ""))
            coord = event.get("coord") or self.fallback.get(name) or []
            return {
                "type": "agent",
                "name": name,
                "texture": self.alias.get(name, name),
                "coord": list(coord),
                "path": list(event.get("path") or []),
                "action": event.get("action", ""),
                "location": event.get("location", ""),
                "currently": event.get("currently", ""),
                "time": event.get("time", ""),
            }
        if etype in ("init", "time", "chat_line", "story", "snapshot"):
            msg = dict(event)
            if etype == "init":
                msg["textures"] = {r: self.alias.get(r, r) for r in (event.get("agents") or [])}
            return msg
        return {}

    def _with_coords(self, record: dict) -> dict:
        """节点缺逐节点坐标时,用场景初始坐标回退。

        先按 nodes_key/meta_key 归一化布局,再补坐标。
        """
        from ..events import normalize_record

        rec = dict(normalize_record(record, nodes_key=self.nodes_key,
                                    meta_key=self.meta_key))
        nodes = []
        for node in rec.get(self.nodes_key) or []:
            node = dict(node)
            if not node.get("agents"):
                node["agents"] = {
                    r: {"coord": self.fallback.get(r, []), "action": "", "location": ""}
                    for r in self.roles if self.fallback.get(r)
                }
            nodes.append(node)
        rec[self.nodes_key] = nodes
        return rec

    # ------------------------------------------------------------------
    def messages(self) -> List[dict]:
        return list(self.outbox)

    def drain(self) -> List[dict]:
        out, self.outbox = self.outbox, []
        return out

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


register("town", TownVisualizer)