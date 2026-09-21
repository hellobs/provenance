# -*- coding: utf-8 -*-
"""体检:短连 5010 /ws,收集消息类型与 agent 事件,判断模拟是否真的在走。"""
import asyncio, json

async def main():
    uri = "ws://127.0.0.1:5010/ws"
    import websockets
    async with websockets.connect(uri, max_size=5_000_000, open_timeout=8) as ws:
        types, agent_events = [], 0
        try:
            for _ in range(12):
                raw = await asyncio.wait_for(ws.recv(), timeout=6)
                m = json.loads(raw)
                t = m.get("type")
                types.append(t)
                if t == "agent":
                    agent_events += 1
                if t in ("done", "error"):
                    print("末态:", t, m.get("reason") or m.get("message"))
        except asyncio.TimeoutError:
            print("(6s 无新消息,连接仍开)")
        except Exception as e:
            print("recv end:", e)
        print("消息类型序列:", types)
        print("agent 事件数:", agent_events)

asyncio.run(main())