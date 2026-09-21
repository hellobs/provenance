# -*- coding: utf-8 -*-
"""体检:短接 WS 确认 case00 模拟是否在推进(agent 移动/步进消息)。"""
import asyncio, json, websockets

async def main():
    uri = "ws://127.0.0.1:5010/ws"
    types, agent_events, step_msgs = [], 0, 0
    first_ts = None
    async with websockets.connect(uri, max_size=10_000_000, open_timeout=8) as ws:
        try:
            for _ in range(20):
                raw = await asyncio.wait_for(ws.recv(), timeout=7)
                m = json.loads(raw)
                t = m.get("type")
                types.append(t)
                if t == "agent":
                    agent_events += 1
                if t in ("sim_status", "step", "tick", "progress"):
                    step_msgs += 1
                if t == "done":
                    print("末态 done:", m.get("reason") or m.get("message"))
                    break
        except asyncio.TimeoutError:
            print("(7s 无新消息)")
        except Exception as e:
            print("recv end:", e)
    print("消息类型序列:", types)
    print("agent(移动)事件数=%d, 步进类消息=%d" % (agent_events, step_msgs))

asyncio.run(main())