# -*- coding: utf-8 -*-
"""回放驱动:把一份 run 记录按事件顺序推给任意可视化插件。

- pacing>0 时按秒间隔推进(用于实时观感);
- 输出可另存为 JSONL(交给 WS 回放服务/离线播放器)。
"""
import argparse
import json
import sys
import time
from typing import List, Optional

from . import Fanout, create
from .events import events_from_record, normalize_record


def record_needs_fallback(record: dict, nodes_key: str = "nodes",
                          meta_key: Optional[str] = None) -> bool:
    """记录里没有逐节点坐标时,需要走"整份记录"路径(由插件自行回退)。"""
    rec = normalize_record(record, nodes_key=nodes_key, meta_key=meta_key)
    nodes = rec.get(nodes_key) or []
    return any(not (n.get("agents") or {}) for n in nodes)


def replay_record(record: dict, fanout: Fanout, pacing: float = 0.0,
                  sleep=time.sleep, nodes_key: str = "nodes",
                  meta_key: Optional[str] = None) -> int:
    """把记录推给 fanout。

    - 有逐节点坐标:逐条事件回放(支持 pacing);
    - 缺坐标(旧记录):走 on_record,由插件自行回退,返回 0。
    """
    if record_needs_fallback(record, nodes_key=nodes_key, meta_key=meta_key):
        fanout.emit_record(record)
        return 0
    events = events_from_record(record, nodes_key=nodes_key, meta_key=meta_key)
    for ev in events:
        fanout.emit(ev)
        if pacing > 0:
            sleep(pacing)
    return len(events)


def load_record(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv: Optional[List[str]] = None):
    ap = argparse.ArgumentParser(description="可插拔可视化回放驱动")
    ap.add_argument("--record", required=True, help="run 记录 JSON 路径")
    ap.add_argument("--town", default="", help="小镇风格消息输出 JSONL 路径")
    ap.add_argument("--report", default="", help="静态审查页输出目录路径")
    ap.add_argument("--roles", default="", help="逗号分隔的角色名(缺省用记录里的 roles)")
    ap.add_argument("--alias", default="", help="逗号分隔 name=texture 的角色贴图别名")
    ap.add_argument("--scenario-dir", default="", help="场景目录(坐标回退;缺省不回退)")
    ap.add_argument("--pacing", type=float, default=0.0, help="每条事件间隔秒(0=不等待)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    record = load_record(args.record)
    roles = [r.strip() for r in args.roles.split(",") if r.strip()] or \
        list(record.get("roles") or [])
    alias = {}
    if args.alias:
        for pair in args.alias.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                alias[k.strip()] = v.strip()

    vis = [create("console", verbose=args.verbose)]
    if args.town:
        vis.append(create("town", outbox_path=args.town, roles=roles,
                          alias=alias or None, scenario_dir=args.scenario_dir))
    if args.report:
        vis.append(create("report", out_dir=args.report, roles=roles))
    fanout = Fanout(vis, on_error=lambda n, e: print("plugin {} error: {}".format(n, e)))
    n = replay_record(record, fanout, pacing=args.pacing)
    fanout.close()
    for v in vis:
        print("events: {}{}".format(
            n, "(record-mode:旧记录无逐节点坐标,插件内部回退)" if n == 0 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())