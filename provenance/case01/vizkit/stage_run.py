# -*- coding: utf-8 -*-
"""布景播放:把一份**手写的像素场景脚本**推给实时可视化插件,不需要引擎、不需要 LLM。

为什么要它(2026-09-19,平台侧:"用素材先搭建个场景,不然交提案和呈现时看不到仿真的
差异化场景"):提案/呈现要的是一个**随时能放、随时能截**的场景,不能等 Ollama、
也不能等平台对接。这个播放器只做一件事——按脚本时间把统一事件(init/time/agent/
chat_line/story/snapshot)推进已有的小镇界面,于是"布景"就是一个 JSON 文件。

脚本格式(JSON)::

    {
      "title": "投资咨询小镇 · 布景",
      "loop": true,                       // 放完是否从头再来(false/缺省 = 放完停住)
      "size": 4,                          // 可选:缩放(交给前端 zoom 参数)
      "steps": [
        {"t": 0.0, "time": "20260827-09:30"},
        {"t": 1.0, "move": {"AI Advisor": [12, 9]}},          // 自动走迷宫正交路线
        {"t": 3.0, "say": ["AI Advisor", "先看今天的公告。"]},
        {"t": 4.0, "story": {"event_type": "disclosure", "content": "…",
                             "targets": ["AI Advisor"], "time": "09:30"}},
        {"t": 5.0, "act": {"AI Advisor": {"action": "读公告", "location": "Trading Floor"}}}
      ]
    }

`move` 会走"横竖不斜穿"的迷宫路径(与实跑同一套 BFS),`say` 说话,`story` 放剧情事件,
`act` 只改动作/位置文本(不动人)。

用法(在 provenance/provenance 下)::

    python -m case01.vizkit.stage_run --scenario-dir case00/scenario \
        --roles "AI Advisor,Daniel Shen,Kevin Su,Michael Chen,Mr. Zhou,Wendy Lin" \
        --script case00/scenario/stage_village.json --port 5020
"""
import argparse
import json
import os
import sys
import time

import mavis_vizkit
from .live_run import PKG_ROOT, ROLE_TEXTURE_ALIAS

FRONTEND = os.path.join(PKG_ROOT, "frontend")


def _scene_start(scenario_dir, roles):
    """布景开场:每个角色摆在场景配置里的初始格(与实跑一致),不必手写坐标。"""
    agents = {}
    for r in roles:
        p = os.path.join(scenario_dir, "agents", r, "agent.json")
        try:
            with open(p, encoding="utf-8") as f:
                cfg = json.load(f)
            agents[r] = {"coord": list(cfg.get("coord") or []),
                         "action": "", "location": "",
                         "currently": str(cfg.get("currently") or "")}
        except Exception as e:  # noqa: BLE001 - 缺配置就留空,不静默
            print("[stage] 读不到 {} 的初始坐标: {}".format(r, e), flush=True)
    return agents


def _load_script(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class _Maze:
    """场景迷宫(用来把"走到某格"变成"横竖不斜穿"的路径)。取不到就退化成直线。"""

    def __init__(self, scenario_dir):
        self.maze = None
        try:
            from mavisframework.config.loader import load_config
            from mavisframework.runtime.game import Game
            from mavisframework.core.timer import Timer

            cfg = load_config(start_time="20260827-09:30", stride=0,
                              config_path=os.path.join(scenario_dir, "config.json"),
                              assets_root="")
            game = Game("stage", os.path.abspath(scenario_dir), cfg, {},
                        timer=Timer("20260827-09:30"), governance=None,
                        consequence_fn=None)
            first = next(iter(game.agents.values()), None)
            self.maze = getattr(first, "maze", None)
        except Exception as e:  # noqa: BLE001 - 没有引擎也要能布景(退化成直线)
            print("[stage] 迷宫不可用,移动将走直线: {}".format(e), flush=True)

    def path(self, src, dst):
        if not self.maze or not src or not dst or list(src) == list(dst):
            return []
        try:
            return [[int(c[0]), int(c[1])] for c in self.maze.find_path(list(src), list(dst))]
        except Exception:  # noqa: BLE001
            return []


def play(live, script, maze, speed=1.0, stop_after=None, roles=None):
    """按脚本推事件。stop_after=秒数时按"墙钟"提前停(给自测/截图用)。"""
    steps = sorted(script.get("steps") or [], key=lambda s: float(s.get("t", 0)))
    t0 = time.time()
    cur = {r: None for r in (roles or [])}
    for st in steps:
        wait = float(st.get("t", 0)) / max(speed, 0.01) - (time.time() - t0)
        if wait > 0:
            time.sleep(wait)
        if stop_after is not None and (time.time() - t0) > stop_after:
            return
        if st.get("time"):
            live.on_event({"type": "time", "time": st["time"]})
        for name, dst in (st.get("move") or {}).items():
            path = maze.path(cur.get(name), dst)
            cur[name] = list(dst)
            live.on_event({"type": "agent", "name": name, "coord": list(dst),
                           "path": path, "action": st.get("action", ""),
                           "location": st.get("location", ""), "time": st.get("time", "")})
        for name, info in (st.get("act") or {}).items():
            live.on_event({"type": "agent", "name": name,
                           "coord": cur.get(name) or info.get("coord") or [0, 0],
                           "path": [], "action": info.get("action", ""),
                           "location": info.get("location", ""), "time": st.get("time", "")})
        # say 允许两种写法:["角色","台词"](一句) 或 [["角色","台词"], ...](多句)——手写脚本更省事
        say = st.get("say") or []
        if len(say) == 2 and isinstance(say[0], str) and isinstance(say[1], str):
            say = [say]
        for speaker, text in say:
            live.on_event({"type": "chat_line", "speaker": speaker, "text": text})
        if st.get("story"):
            live.on_event(dict(st["story"], type="story"))
        if st.get("snapshot"):
            live.on_event({"type": "snapshot", "agents": st["snapshot"],
                           "time": st.get("time", "")})


def main(argv=None):
    ap = argparse.ArgumentParser(description="像素场景布景播放器(不跑推演)")
    ap.add_argument("--scenario-dir", required=True, help="场景目录(含 maze.json 与 agents/)")
    ap.add_argument("--roles", required=True, help="逗号分隔的角色名(顺序即贴图别名顺序)")
    ap.add_argument("--alias", default="", help="可选:角色=贴图名,逗号分隔")
    ap.add_argument("--script", required=True, help="布景脚本 JSON")
    ap.add_argument("--port", type=int, default=5020)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--speed", type=float, default=1.0, help="播放倍速")
    ap.add_argument("--loop", action="store_true", help="放完从头再来")
    ap.add_argument("--stop-after", type=float, default=None, help="放 N 秒后停住(自测用)")
    args = ap.parse_args(argv)

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    alias = {}
    for pair in (args.alias or "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            alias[k.strip()] = v.strip()
    alias = alias or {r: r for r in roles}

    live = mavis_vizkit.create(
        "live", host=args.host, port=args.port, roles=roles, alias=alias,
        scenario_dir=os.path.abspath(args.scenario_dir),
        static_root=os.path.join(FRONTEND, "static"),
        template_dir=os.path.join(FRONTEND, "templates"))
    live.start()
    script = _load_script(args.script)
    maze = _Maze(os.path.abspath(args.scenario_dir))
    print("布景已启动: {}  ({};Ctrl+C 结束)".format(live.url(), script.get("title", "")))
    live.on_event({"type": "init", "agents": roles})
    # 开场把所有角色摆到场景初始格(前端要先有画面;否则空镇)
    start = _scene_start(os.path.abspath(args.scenario_dir), roles)
    live.on_event({"type": "snapshot", "agents": start, "time": "20260827-09:30"})
    try:
        while True:
            play(live, script, maze, speed=args.speed,
                 stop_after=args.stop_after, roles=roles)
            if not (args.loop or script.get("loop")):
                break
            live.begin_run()          # 重放前把上一遍的画面清干净
    except KeyboardInterrupt:
        print("已停止")
    finally:
        live.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
