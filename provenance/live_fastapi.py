"""实时模拟 + 可视化服务(FastAPI + WebSocket 版)

框架驱动:framework Game + Simulator + LiveCompressor,推送 framework 契约消息。
- 页面渲染:Jinja2 模板(复用现有前端)
- 实时推送:WebSocket /ws(双向,为 Unity 交互铺路)
"""
import os
import json
import queue
import threading
from typing import Dict, List

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from mavisframework.config.loader import personas, load_config, load_config_from_log
from mavisframework.runtime.compressor import LiveCompressor

from mavisframework.runtime.protocol import AgentState, TimeMsg, ChatLineMsg, validate_message

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Provenance Live (FastAPI)")
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE_DIR, "frontend/static")),
    name="static",
)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "frontend/templates"))


# ---------------------------------------------------------------------------
# WebSocket 连接管理(线程安全的广播)
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self._queues: Dict[WebSocket, "asyncio.Queue"] = {}
        self._lock = threading.Lock()

    def register(self, ws: WebSocket, q) -> None:
        with self._lock:
            self._queues[ws] = q

    def unregister(self, ws: WebSocket) -> None:
        with self._lock:
            self._queues.pop(ws, None)

    def broadcast(self, data: dict) -> None:
        """线程安全:向所有连接队列投放消息(WebSocket 发送由各自的协程执行)"""
        if not validate_message(data):
            print(f"[protocol] 非契约消息: type={data.get('type')}", flush=True)
        with self._lock:
            for q in self._queues.values():
                q.put_nowait(data)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._queues)


manager = ConnectionManager()

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
sim_state = {"status": "idle", "error": "", "start_time": "", "stride": 2}
compressor = None
server = None


def conversation_text(conversation, step_time):
    text = ""
    if step_time in conversation:
        for chats in conversation[step_time]:
            for persons, chat in chats.items():
                text += f"\n地点：{persons.split(' @ ')[1]}\n\n"
                for c in chat:
                    text += f"{c[0]}：{c[1]}\n"
    return {step_time: text}


def on_agent(name, agent_data, step, sim_time):
    global compressor, server
    if compressor is None:
        return
    agent_state, _, description = compressor.add_agent(name, agent_data, step, sim_time)
    conv_text = {}
    if server is not None and sim_time in server.game.conversation:
        conv_text = conversation_text(server.game.conversation, sim_time)
    # 读取角色类型(user/ai_tool),供前端控制台标识 AI 工具角色
    role_type = "user"
    goal_score = None
    goal_alignment = {}
    try:
        if server is not None and name in server.game.agents:
            role_type = getattr(server.game.agents[name], "role_type", "user") or "user"
            _status = server.game.agents[name].status or {}
            goal_alignment = _status.get("goal_alignment") or {}
            # IVD:约束是"期望基准",与当下行动的逐目标对齐度做加权和,
            # 得到"行动对制度约束的整体对齐度"(前端 Constraint alignment 指标)
            _gov = getattr(server.game, "governance", None)
            if _gov is not None and goal_alignment:
                cons = _gov.get_constraints(name)
                if cons:
                    vals = [w * goal_alignment.get(g, 0.0) for g, w in cons.items()]
                    goal_score = sum(vals)
    except Exception:
        pass
    msg: AgentState = {
        "type": "agent",
        "name": agent_state["name"],
        "coord": agent_state["coord"],
        "path": agent_state["path"],
        "action": agent_state["action"],
        "location": agent_state["location"],
        "currently": agent_data.get("currently", ""),
        "conversation": conv_text,
        "role_type": role_type,
        "goal_score": goal_score,
        "goal_alignment": goal_alignment,
    }
    if description:
        msg["description"] = description
    manager.broadcast(msg)


def on_step(config):
    msg: TimeMsg = {"type": "time", "time": config["time"]}
    manager.broadcast(msg)


def on_chat_line(speaker, text):
    msg: ChatLineMsg = {"type": "chat_line", "speaker": speaker, "text": text}
    manager.broadcast(msg)


@app.get("/api/goals")
async def get_goals():
    """返回所有角色的治理约束(期望)与价值倾向(内化结果)"""
    global server
    # 约束来自 governance.json(制度层)
    from mavisframework.runtime.governance import Governance
    gov_path = os.path.join(BASE_DIR, "governance.json")
    constraints = {}
    if os.path.exists(gov_path):
        gov = Governance()
        gov.load(gov_path)
        constraints = gov.all_constraints()
    # 倾向来自运行中 Agent 的 value_tendency
    tendency = {}
    if server is not None and server.game is not None:
        for name, agent in server.game.agents.items():
            tendency[name] = agent.get_tendency()
    return JSONResponse({
        "ok": True,
        "goals": constraints,       # 治理约束(期望,面板可调)
        "tendency": tendency,      # 价值倾向(内化结果,只读)
    })


@app.post("/api/goals")
async def update_goals(request: Request):
    """更新某角色的治理约束(专家设定期望目标权重)

    IVD 语义:约束存在于 governance.json(制度层),不写入 agent.json(AI 本体)。
    记录干预审计(interventions.json):时间/角色/旧值→新值。
    约束不直接注入 prompt——仅作为客观后果反馈的对照基准。
    """
    global server
    body = await request.json()
    name = str(body.get("name", "")).strip()
    goals = body.get("goals")
    if not name:
        return JSONResponse({"ok": False, "errors": ["缺少角色名"]})
    if not isinstance(goals, dict) or not goals:
        return JSONResponse({"ok": False, "errors": ["约束应为非空 dict(目标:权重)"]})
    # 校验权重总和为 1(容差 1e-6)
    try:
        total = sum(float(v) for v in goals.values())
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "errors": ["约束权重值必须都是数字"]})
    if abs(total - 1.0) > 1e-6:
        return JSONResponse({"ok": False, "errors": [f"约束权重总和应为 1,得到 {round(total, 4)}"]})

    # 1) 写 governance.json(制度层,非 AI 本体)
    from mavisframework.runtime.governance import Governance
    gov_path = os.path.join(BASE_DIR, "governance.json")
    gov = Governance()
    if os.path.exists(gov_path):
        gov.load(gov_path)
    old = gov.get_constraints(name)
    gov.set_constraints(name, goals)  # set_constraints 内已 save

    # 2) 记录干预审计(可审计链)
    try:
        import time as _time, datetime
        audit_path = os.path.join(BASE_DIR, "results/checkpoints", "interventions.json")
        audit = []
        if os.path.exists(audit_path):
            with open(audit_path, "r", encoding="utf-8") as f:
                audit = json.load(f)
        audit.append({
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "agent": name,
            "old_constraints": old,
            "new_constraints": goals,
            "operator": "expert",
        })
        os.makedirs(os.path.dirname(audit_path), exist_ok=True)
        with open(audit_path, "w", encoding="utf-8") as f:
            json.dump(audit, f, ensure_ascii=False, indent=2)
    except Exception as e:
        pass  # 审计失败不阻断主流程

    return JSONResponse({"ok": True, "name": name, "constraints": goals})


def run_simulation(name, sim_config, start_step, step, stride):
    """后台线程运行模拟(框架驱动:framework Game + Simulator,不依赖 modules)"""
    global server, compressor
    try:
        import mavisframework.core.agent_core as fw_agent
        from mavisframework.core.timer import Timer
        from mavisframework.runtime.game import Game
        from mavisframework.runtime.simulator import Simulator

        fw_agent.chat_callback = on_chat_line
        checkpoints_folder = f"results/checkpoints/{name}"

        # 用存档里的时间建时钟(存档 time 已是下一步时间)
        timer = Timer(start=sim_config["time"]["start"])
        conversation = {}
        conv_path = os.path.join(checkpoints_folder, "conversation.json")
        if os.path.exists(conv_path):
            with open(conv_path, "r", encoding="utf-8") as f:
                conversation = json.load(f)

        compressor = LiveCompressor(checkpoints_folder, "frontend/static")
        # 框架存储:默认用纯 stdlib 的 SimpleStore(零 llama_index 依赖,任何环境可跑)
        for agent_name, acfg in sim_config.get("agents", {}).items():
            base = sim_config.get("agent_base", {})
            assoc = dict(base.get("associate", {}))
            assoc["embedding"] = {"provider": "simple"}
            if "agent_base" not in sim_config:
                sim_config["agent_base"] = {}
            sim_config["agent_base"]["associate"] = assoc

        # 业务配置:relationships 注入 Agent,story 注入 Simulator
        relationships, story = [], []
        scenario_dir = os.path.join(BASE_DIR, "scenarios/investment")
        rel_path = os.path.join(scenario_dir, "relationships.json")
        if os.path.exists(rel_path):
            with open(rel_path, "r", encoding="utf-8") as f:
                relationships = json.load(f).get("relations", [])
        story_path = os.path.join(scenario_dir, "story.json")
        if os.path.exists(story_path):
            with open(story_path, "r", encoding="utf-8") as f:
                story = json.load(f).get("events", [])
        # 每个 Agent 都能查到与任意角色的关系(注入到 agent_base 供所有角色共享)
        if relationships:
            sim_config.setdefault("agent_base", {})["relationships"] = relationships

        # LLM 并发与角色数匹配:config 未显式指定 concurrency 时,取角色数
        # (首轮并行建日程是瓶颈,让并发=角色数,避免第 N 个 agent 干等)
        agent_count = len(sim_config.get("agents", {}))
        llm_cfg = sim_config.setdefault("agent_base", {}).setdefault("think", {}).setdefault("llm", {})
        if llm_cfg.get("concurrency", 0) <= 0:
            llm_cfg["concurrency"] = max(1, agent_count)

        # IVD:挂接治理约束 + 客观后果反馈
        # governance.json 在 BASE_DIR(平台根),提供期望目标权重
        from mavisframework.runtime.governance import Governance
        from mavisframework.runtime.consequence import ConsequenceEngine
        gov_path = os.path.join(BASE_DIR, "governance.json")
        governance = Governance()
        if os.path.exists(gov_path):
            governance.load(gov_path)
        consequence = ConsequenceEngine()

        game = Game(name, "frontend/static", sim_config, conversation, timer=timer,
                    governance=governance, consequence_fn=consequence.feedback)
        game.reset_game()

        # 薄封装,让 on_agent 能读到 server.game.conversation
        class _Server:
            pass

        server = _Server()
        server.game = game

        simulator = Simulator(
            max_workers=max(1, len(game.agents)),
            export_decisions=True,
            decisions_path=os.path.join(checkpoints_folder, "decisions.json"),
            roles=_collect_roles(),
            story=story,
            on_story=lambda ev: manager.broadcast(ev),
        )
        sim_state["status"] = "running"
        if step <= 0:
            while True:
                simulator.simulate(
                    game, sim_config, 1, stride,
                    start_step=start_step,
                    checkpoints_folder=checkpoints_folder,
                    on_step=on_step, on_agent=on_agent,
                )
                start_step += 1
        else:
            simulator.simulate(
                game, sim_config, step, stride,
                start_step=start_step,
                checkpoints_folder=checkpoints_folder,
                on_step=on_step, on_agent=on_agent,
            )
        sim_state["status"] = "done"
        manager.broadcast({"type": "done"})
    except Exception as e:
        from mavisframework.runtime.logger import get_logger

        get_logger("simulation").error(f"simulation crashed: {e}", exc_info=True)
        sim_state["status"] = "error"
        sim_state["error"] = str(e)
        manager.broadcast({"type": "error", "message": str(e)})


def _discover_agent_names():
    """动态发现 agents 目录下的所有角色名(不写死,支持任意角色数)"""
    agents_root = os.path.join(BASE_DIR, "frontend/static/assets/village/agents")
    if os.path.isdir(agents_root):
        names = [
            n for n in sorted(os.listdir(agents_root))
            if os.path.exists(os.path.join(agents_root, n, "agent.json"))
        ]
        if names:
            return names
    return personas


def _collect_roles() -> dict:
    """收集 角色名 -> 职位 映射(决策导出用,来自 agent.json 的 duty.position)"""
    roles = {}
    for name in _discover_agent_names():
        p = os.path.join(BASE_DIR, "frontend/static/assets/village/agents", name, "agent.json")
        if os.path.exists(p):
            import json as _json
            with open(p, "r", encoding="utf-8") as f:
                d = _json.load(f)
            roles[name] = (d.get("duty") or {}).get("position", "")
    return roles


def load_initial_payload(start_datetime, stride):
    from datetime import datetime
    persona_init_pos = {}
    description = {}
    # 动态发现所有角色:遍历 agents 目录(不写死 personas,支持任意角色数)
    names = _discover_agent_names()
    for name in names:
        json_path = os.path.join(
            BASE_DIR, "frontend/static", f"assets/village/agents/{name}/agent.json"
        )
        if not os.path.exists(json_path):
            continue
        with open(json_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)
        persona_init_pos[name] = json_data["coord"]
        description[name] = {
            "currently": json_data["currently"],
            "scratch": json_data["scratch"],
        }
    return {
        "start_datetime": datetime.strptime(start_datetime, "%Y%m%d-%H:%M").isoformat(),
        "stride": stride,
        "sec_per_step": stride,
        "persona_init_pos": persona_init_pos,
        "all_movement": {"description": description, "conversation": {}},
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    speed = int(request.query_params.get("speed", 0))
    zoom = float(request.query_params.get("zoom", 0))
    if speed < 0:
        speed = 0
    elif speed > 5:
        speed = 5
    play_speed = 2 ** speed
    payload = load_initial_payload(sim_state["start_time"], sim_state["stride"])
    # Phaser 脚本:本地 vendor 优先(断网可用),否则回退 CDN
    local_phaser = os.path.join(BASE_DIR, "frontend/static/vendor/phaser.min.js")
    if os.path.exists(local_phaser):
        phaser_src = "static/vendor/phaser.min.js"
    else:
        phaser_src = "https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.js"
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "persona_names": list(payload["persona_init_pos"].keys()),
            "step": 1,
            "play_speed": play_speed,
            "zoom": zoom,
            "live_mode": True,
            "phaser_src": phaser_src,
            **payload,
        },
    )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    q: "asyncio.Queue" = __import__("asyncio").Queue()
    manager.register(ws, q)
    # 初始消息:确认连接 + 快照(追赶进度)
    await ws.send_json({"type": "init"})
    if compressor is not None and compressor.started:
        await ws.send_json(compressor.snapshot())
    try:
        while True:
            data = await q.get()
            await ws.send_json(data)
    except WebSocketDisconnect:
        manager.unregister(ws)
    except Exception:
        manager.unregister(ws)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="live simulation server (FastAPI)")
    parser.add_argument("--name", type=str, default="", help="The simulation name")
    parser.add_argument("--start", type=str, default="20250213-09:30", help="The starting time of the simulated ville")
    parser.add_argument("--resume", action="store_true", help="Resume running the simulation")
    parser.add_argument("--step", type=int, default=0, help="The simulate step (<=0 means run forever)")
    parser.add_argument("--stride", type=int, default=2, help="The step stride in minute")
    parser.add_argument("--port", type=int, default=5001, help="The server port")
    args = parser.parse_args()

    name = args.name
    if len(name) < 1:
        name = input("Please enter a simulation name (e.g. sim-test): ")

    if args.resume:
        while not os.path.exists(f"results/checkpoints/{name}"):
            name = input(f"'{name}' doesn't exists, please re-enter the simulation name: ")
    else:
        if os.path.exists(f"results/checkpoints/{name}"):
            # 存档名冲突:自动追加时间戳后缀(后台/服务化场景无 stdin,不能阻塞等输入)
            import time as _time
            suffix = _time.strftime("%m%d-%H%M%S")
            name = f"{name}-{suffix}"
            print(f"Simulation name '{args.name}' already exists, using '{name}'")

    checkpoints_folder = f"results/checkpoints/{name}"
    if args.resume:
        sim_config = load_config_from_log(checkpoints_folder)
        if sim_config is None:
            print("No checkpoint file found to resume running.")
            exit(0)
        start_step = sim_config["step"]
    else:
        sim_config = load_config(args.start, args.stride, _discover_agent_names())
        start_step = 0

    sim_state["start_time"] = sim_config["time"]["start"]
    sim_state["stride"] = args.stride

    sim_thread = threading.Thread(
        target=run_simulation,
        args=(name, sim_config, start_step, args.step, args.stride),
        daemon=True,
    )
    sim_thread.start()

    print(f"Live simulation '{name}' started (FastAPI). Open http://127.0.0.1:{args.port}/")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
