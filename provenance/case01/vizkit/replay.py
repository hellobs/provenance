# -*- coding: utf-8 -*-
"""回放驱动:把一份 run 记录按事件顺序推给任意可视化插件。

- pacing>0 时按秒间隔推进(用于小镇风格前端的实时观感);
- 输出可另存为 JSONL(交给 WS 回放服务/离线播放器);
    python -m case01.vizkit.replay --record runs/demo-C-mavis/run.json --town out.jsonl
"""
import argparse
import json
import sys
import time
from typing import List, Optional

from . import Fanout, create
from .events import events_from_record, normalize_record
from .plugins.town import TownVisualizer


def record_needs_fallback(record: dict) -> bool:
    """记录里没有逐节点坐标时,需要走"整份记录"路径(由插件自行回退,如 town 用场景初始坐标)。"""
    rec = normalize_record(record)
    nodes = rec.get("nodes") or []
    return any(not (n.get("agents") or {}) for n in nodes)


def replay_record(record: dict, fanout: Fanout, pacing: float = 0.0,
                  sleep=time.sleep) -> int:
    """把记录推给 fanout。

    - 有逐节点坐标:逐条事件回放(支持 pacing);
    - 缺坐标(旧记录):走 on_record,由插件自行回退,返回 0。
    """
    if record_needs_fallback(record):
        fanout.emit_record(record)
        return 0
    events = events_from_record(record)
    for ev in events:
        fanout.emit(ev)
        if pacing > 0:
            sleep(pacing)
    return len(events)


def load_record(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv: Optional[List[str]] = None):
    ap = argparse.ArgumentParser(description="case01 回放驱动(可插拔可视化)")
    ap.add_argument("--record", required=True, help="run.json 路径")
    ap.add_argument("--town", default="", help="小镇风格消息输出 JSONL 路径")
    ap.add_argument("--pacing", type=float, default=0.0, help="每条事件间隔秒(0=不等待)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    record = load_record(args.record)
    vis = [create("console", verbose=args.verbose)]
    if args.town:
        vis.append(create("town", outbox_path=args.town,
                          roles=list(record.get("roles") or [])))
    fanout = Fanout(vis, on_error=lambda n, e: print("plugin {} error: {}".format(n, e)))
    n = replay_record(record, fanout, pacing=args.pacing)
    fanout.close()
    for v in vis:
        if isinstance(v, TownVisualizer):
            print("town messages: {} -> {}".format(len(v.messages()), args.town))
        else:
            print("events: {}{} {}".format(
                n, "(record-mode:旧记录无逐节点坐标,插件内部回退)" if n == 0 else "",
                v.counts()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
