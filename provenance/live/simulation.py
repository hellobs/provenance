"""模拟引导(框架 Game + Simulator 的装配与运行),由 live_fastapi.py 在后台线程启动。

与 routes 解耦:这里只负责把 mavisframework 的 Game/Simulator 跑起来,
并把事件推给 live.state.manager / 更新 live.state 的全局引用。
"""
import json
import os

from live import state
from live.state import manager, log


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
    from live import state
    compressor = state.compressor
    server = state.server
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
    value_tendency = {}
    try:
        if server is not None and name in server.game.agents:
            role_type = getattr(server.game.agents[name], "role_type", "user") or "user"
            _agent = server.game.agents[name]
            _status = _agent.status or {}
            goal_alignment = _status.get("goal_alignment") or {}
            # IVD:约束是"期望基准",与当下行动的逐目标对齐度做加权和,
            # 得到"行动对制度约束的整体对齐度"(前端 Constraint alignment 指标)
            _gov = getattr(server.game, "governance", None)
            if _gov is not None and goal_alignment:
                cons = _gov.get_constraints(name)
                if cons:
                    vals = [w * goal_alignment.get(g, 0.0) for g, w in cons.items()]
                    goal_score = sum(vals)
            # 核心观测对象:价值倾向(内化结果)演变,供前端曲线绘制
            value_tendency = _agent.get_tendency() or {}
    except Exception as e:
        # 尽力而为:推送消息失败不应中断广播;记日志便于定位(不刷堆栈,高频路径)
        log.debug("on_agent 读取角色状态失败(name={}): {}".format(name, e))

    from mavisframework.runtime.protocol import AgentState
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
        "value_tendency": value_tendency,
        "time": sim_time,
    }
    if description:
        msg["description"] = description
    manager.broadcast(msg)


def on_step(config):
    manager.broadcast({"type": "time", "time": config["time"]})


def on_chat_line(speaker, text):
    manager.broadcast({"type": "chat_line", "speaker": speaker, "text": text})


def run_simulation(name, sim_config, start_step, step, stride):
    """后台线程运行模拟(框架驱动:framework Game + Simulator,不依赖 modules)"""
    try:
        import mavisframework.core.agent_core as fw_agent
        from mavisframework.core.timer import Timer
        from mavisframework.runtime.game import Game
        from mavisframework.runtime.simulator import Simulator
        from mavisframework.runtime.compressor import LiveCompressor

        fw_agent.chat_callback = on_chat_line
        # 绝对路径,避免 cwd 依赖导致检查点写到错误目录
        checkpoints_folder = os.path.join(state.BASE_DIR, "results/checkpoints", name)

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
        # 全球时区场景:所有角色(含 user)全天在线不睡觉
        sim_config.setdefault("agent_base", {})["no_sleep"] = True
        relationships, story = [], []
        scenario_dir = os.path.join(state.BASE_DIR, "scenarios/investment")
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
        gov_path = os.path.join(state.BASE_DIR, "governance.json")
        governance = Governance()
        if os.path.exists(gov_path):
            governance.load(gov_path)
        consequence = ConsequenceEngine()

        game = Game(name, "frontend/static", sim_config, conversation, timer=timer,
                    governance=governance, consequence_fn=consequence.feedback)
        # 供 /api/goals 暴露 embedding 稳定性健康度(降级监控)
        game.consequence = consequence
        game.reset_game()

        # 薄封装,让 on_agent 能读到 server.game.conversation
        class _Server:
            pass

        server = _Server()
        server.game = game
        state.server = server
        state.compressor = compressor

        simulator = Simulator(
            max_workers=max(1, len(game.agents)),
            export_decisions=True,
            decisions_path=os.path.join(checkpoints_folder, "decisions.json"),
            roles=_collect_roles(),
            story=story,
            on_story=lambda ev: manager.broadcast(ev),
        )
        state.sim_state["status"] = "running"
        state.sim_state["name"] = name  # 当前模拟名(干预记录归属,跨模拟隔离)
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
        state.sim_state["status"] = "done"
        manager.broadcast({"type": "done"})
    except Exception as e:
        from mavisframework.runtime.logger import get_logger
        get_logger("simulation").error(f"simulation crashed: {e}", exc_info=True)
        state.sim_state["status"] = "error"
        state.sim_state["error"] = str(e)
        manager.broadcast({"type": "error", "message": str(e)})


def _discover_agent_names():
    """动态发现 agents 目录下的所有角色名(不写死,支持任意角色数)"""
    agents_root = os.path.join(state.BASE_DIR, "frontend/static/assets/village/agents")
    from mavisframework.config.loader import personas
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
        p = os.path.join(state.BASE_DIR, "frontend/static/assets/village/agents", name, "agent.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            roles[name] = (d.get("duty") or {}).get("position", "")
    return roles


def load_initial_payload(start_datetime, stride):
    from datetime import datetime
    persona_init_pos = {}
    description = {}
    # 动态发现所有角色:遍历 agents 目录(不写死 personas,支持任意角色数)
    for name in _discover_agent_names():
        json_path = os.path.join(
            state.BASE_DIR, "frontend/static", f"assets/village/agents/{name}/agent.json"
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
