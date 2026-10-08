# -*- coding: utf-8 -*-
"""临时取证:连 5010 /ws,收集 type=agent 消息,打印 path/coord,判断后端是否给合法路径。"""
import asyncio, json, sys

async def main():
    try:
        import websockets
    except Exception as e:
        print("无 websockets 库:", e); return
    uri = "ws://127.0.0.1:5010/ws"
    async with websockets.connect(uri, max_size=5_000_000, open_timeout=8) as ws:
        count = 0
        while count < 12:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=15)
            except Exception as e:
                print("recv ended:", e); break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if isinstance(msg, dict):
                t = msg.get("type")
                if t == "agent":
                    count += 1
                    name = msg.get("name", "?")
                    coord = msg.get("coord")
                    path = msg.get("path")
                    act = (msg.get("action") or "")[:40]
                    c = f"coord={coord}"
                    if path:
                        c += f" path_len={len(path)} path_head={path[:4]}"
                    else:
                        c += " path=none"
                    print(f"[agent] {name}: {c} | {act}")
                elif t in ("snapshot", "init"):
                    print(f"[{t}] keys={list(msg.keys())[:8]} agents={len(msg.get('agents') or [] or (msg.get('snapshot') or {}).get('agents') or ()) if isinstance(msg, dict) else '?'}")
    print("done")

asyncio.run(main())