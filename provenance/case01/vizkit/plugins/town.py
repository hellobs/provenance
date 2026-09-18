# -*- coding: utf-8 -*-
"""小镇风格插件:把统一事件流翻译成现有 Phaser 前端可消费的消息序列。

要点
----
1. 契约与 mavis `runtime.protocol` 一致(init/snapshot/agent/chat_line/time/story/ping),
   因此现有"斯坦福小镇风格"前端(provenance `frontend/templates/main_script.html`)
   可以直接复用,不需要改前端;
2. 角色贴图别名:case01 的两个角色(Investment AI / Ethan Lin)不在小镇素材池里,
   这里映射到已有贴图,避免新增美术资源;要换贴图只改这张表;
3. 坐标回退:老记录没有逐节点坐标时,用场景配置里的初始坐标占位,
   保证回放不会因为缺坐标而画不出来(新记录会带 coord,见 bridge 的坐标落盘)。
"""
import json
import os
from typing import Dict, List, Optional

from .. import Visualizer, register

# case01 角色 -> 小镇素材池里的角色名(只影响贴图/头像,不影响语义)
ROLE_TEXTURE_ALIAS = {
    "Investment AI": "AI Advisor",
    "Ethan Lin": "Mr. Zhou",
}

DEFAULT_SCENARIO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "injector", "scenario")


def scenario_coords(scenario_dir: str = "", roles: Optional[List[str]] = None) -> Dict[str, list]:
    """从场景配置读角色初始坐标(坐标回退用)。"""
    scenario_dir = scenario_dir or DEFAULT_SCENARIO
    out: Dict[str, list] = {}
    for role in (roles or ROLE_TEXTURE_ALIAS.keys()):
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

    def __init__(self, outbox_path: str = "", alias: Optional[dict] = None,
                 scenario_dir: str = "", roles: Optional[List[str]] = None):
        self.outbox_path = outbox_path
        self.alias = dict(alias or ROLE_TEXTURE_ALIAS)
        self.roles = list(roles or self.alias.keys())
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

        for ev in events_from_record(self._with_coords(record)):
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
        """老记录缺逐节点坐标时,用场景初始坐标回退。

        先归一化布局(映射后的 run.json 节点在 injector.nodes),再补坐标。
        """
        from ..events import normalize_record

        rec = dict(normalize_record(record))
        nodes = []
        for node in rec.get("nodes") or []:
            node = dict(node)
            if not node.get("agents"):
                node["agents"] = {
                    r: {"coord": self.fallback.get(r, []), "action": "", "location": ""}
                    for r in self.roles if self.fallback.get(r)
                }
            nodes.append(node)
        rec["nodes"] = nodes
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
