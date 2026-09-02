"""实时模拟 + 可视化服务(FastAPI + WebSocket 版)

框架驱动:framework Game + Simulator + LiveCompressor,推送 framework 契约消息。
- 页面渲染:Jinja2 模板(复用现有前端)
- 实时推送:WebSocket /ws(双向,为 Unity 交互铺路)
"""
import os
import json
import queue
import threading
import asyncio
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
from mavisframework.runtime.logger import get_logger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 模块级日志:各处 except 容错统一走这里,不再静默吞错
log = get_logger("provenance.live", level="info")

# ---------------------------------------------------------------------------
# checkpoint 倾向序列加载(带缓存)
# ---------------------------------------------------------------------------
# 缓存:checkpoint 目录 -> (mtime签名, {agent: [(dt, {goal:value}), ...]})
# 目录 mtime 变化(新 checkpoint 落盘)才重扫——避免 explain/export 每次
# 重复解析几百个 json 文件(几百文件时可达数百 ms)
_series_cache = {}
_series_cache_lock = threading.Lock()


def _dir_mtime_sig(ckpt_dir: str) -> str:
    """目录内 simulate-*.json 的数量 + 最新 mtime,作为缓存失效签名"""
    import glob as _g
    files = _g.glob(os.path.join(ckpt_dir, "simulate-*.json"))
    if not files:
        return ""
    latest = max(os.path.getmtime(p) for p in files)
    return "{}-{}".format(len(files), int(latest))


def load_tendency_series(ckpt_dir: str, agent: str):
    """读取某角色在 ckpt_dir 下的倾向序列(带缓存)。

    返回 [(datetime, {goal: value}), ...] 按时间排序;
    缓存键 = 目录 mtime 签名,新 checkpoint 落盘自动失效。
    """
    import datetime as _dt

    sig = _dir_mtime_sig(ckpt_dir)
    with _series_cache_lock:
        cached = _series_cache.get(ckpt_dir)
        if cached and cached[0] == sig:
            return cached[1].get(agent, [])

    import glob as _g

    all_series = {}
    files = sorted(_g.glob(os.path.join(ckpt_dir, "simulate-*.json")))
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as f:
                c = json.load(f)
        except Exception as e:
            log.warning("load_tendency_series 解析存档失败,跳过(p={}): {}".format(p, e))
            continue
        t = os.path.basename(p).replace("simulate-", "").replace(".json", "")
        try:
            dt = _dt.datetime.strptime(t, "%Y%m%d-%H%M")
        except ValueError as e:
            log.warning("load_tendency_series 时间格式非法,跳过(p={}, time={}): {}".format(p, t, e))
            continue
        for aname, ag in (c.get("agents") or {}).items():
            vt = (ag.get("status") or {}).get("value_tendency") or {}
            if vt:
                all_series.setdefault(aname, []).append((dt, vt))
    with _series_cache_lock:
        _series_cache[ckpt_dir] = (sig, all_series)
    return all_series.get(agent, [])


# 窗口条目时间反查缓存:ckpt_dir -> (签名, {agent: {条目签名: [时间...]}})
_window_time_cache = {}
_window_time_cache_lock = threading.Lock()


def _window_entry_sig(entry: dict) -> str:
    """窗口条目签名:action + feedback(旧存档无 time 时用于跨 checkpoint 匹配)"""
    try:
        fb = json.dumps(entry.get("feedback") or {}, sort_keys=True, default=str)
    except Exception:
        fb = ""
    return "{}||{}".format(str(entry.get("action", "")), fb)


def _backfill_window_times(ckpt_dir: str, agent: str, window: list) -> None:
    """为旧存档窗口条目(无 time 字段)反查模拟时间。

    新代码 observe_consequence 会写入 time;旧存档缺该字段。
    反查策略:扫描各 checkpoint 快照里该 agent 的 tendency_window,
    按 (action, feedback) 签名 + 出现次序,映射到最早出现的 checkpoint 时间。
    同签名多条:第 k 条取第 k 次出现的 checkpoint 时间(不全都标同一时刻)。
    找不到的条目保持空串(前端显示 "–")。
    """
    if not ckpt_dir or not os.path.isdir(ckpt_dir):
        return
    import glob as _g
    import datetime as _dt

    need = [w for w in window if isinstance(w, dict) and not w.get("time")]
    if not need:
        return
    sig = _dir_mtime_sig(ckpt_dir)
    with _window_time_cache_lock:
        cached = _window_time_cache.get(ckpt_dir)
        if cached and cached[0] == sig:
            times_map = cached[1].get(agent, {})
        else:
            times_map = None
    if times_map is None:
        # 扫描所有 checkpoint:记录每个签名的出现时间序列
        files = sorted(_g.glob(os.path.join(ckpt_dir, "simulate-*.json")))
        seen: Dict[str, list] = {}
        for p in files:
            t = os.path.basename(p).replace("simulate-", "").replace(".json", "")
            try:
                dt = _dt.datetime.strptime(t, "%Y%m%d-%H%M")
            except ValueError:
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    c = json.load(f)
            except Exception:
                continue
            ag = (c.get("agents") or {}).get(agent)
            if not ag:
                continue
            win = (ag.get("status") or {}).get("tendency_window") or []
            for w in win:
                if not isinstance(w, dict):
                    continue
                s = _window_entry_sig(w)
                seen.setdefault(s, []).append(dt)
        times_map = {s: [x.strftime("%Y%m%d-%H:%M") for x in v] for s, v in seen.items()}
        with _window_time_cache_lock:
            _window_time_cache[ckpt_dir] = (sig, {agent: times_map} if times_map else {})
    if not times_map:
        return
    # 按出现次序填时间:同签名第 k 条取第 k 次出现
    occ: Dict[str, int] = {}
    for w in window:
        if not isinstance(w, dict) or w.get("time"):
            continue
        s = _window_entry_sig(w)
        times = times_map.get(s)
        if not times:
            continue
        k = occ.get(s, 0)
        occ[s] = k + 1
        if k < len(times):
            w["time"] = times[k]


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
    role_types = {}
    if server is not None and server.game is not None:
        for name, agent in server.game.agents.items():
            tendency[name] = agent.get_tendency()
            role_types[name] = getattr(agent, "role_type", "user") or "user"
    # 专家干预记录(供曲线画"干预时刻"竖线)
    # 跨模拟隔离:interventions.json 全局共享,只返回当前模拟的干预
    # (旧记录无 simulation 字段 = 其他模拟/旧数据,不显示——避免"标了干预却不动"误导)
    interventions = []
    current_sim = sim_state.get("name", "")
    iv_path = os.path.join(BASE_DIR, "results/checkpoints", "interventions.json")
    if os.path.exists(iv_path):
        try:
            all_iv = json.load(open(iv_path, encoding="utf-8"))
            interventions = [x for x in all_iv if x.get("simulation") == current_sim]
        except Exception as e:
            log.warning("读取 interventions.json 失败: {}".format(e))
            interventions = []
    # embedding 稳定性健康度(后果反馈降级监控)
    embedding_health = {}
    if server is not None and server.game is not None:
        try:
            engine = getattr(server.game, "consequence", None)
            if engine is not None and hasattr(engine, "health"):
                embedding_health = engine.health()
        except Exception as e:
            log.warning("读取 embedding 健康度失败: {}".format(e))
            embedding_health = {}
    return JSONResponse({
        "ok": True,
        "simulation": current_sim,  # 当前模拟名(前端过滤干预归属)
        "goals": constraints,       # 治理约束(期望,面板可调)
        "tendency": tendency,       # 价值倾向(内化结果,只读)
        "interventions": interventions,  # 专家干预审计(仅当前模拟,曲线竖线标记)
        "role_types": role_types,   # 角色类型(ai_tool/user,面板徽标)
        "embedding_health": embedding_health,  # embedding 稳定性(降级率/错误)
    })


@app.get("/api/explain")
async def explain_agent(agent: str = ""):
    """倾向成因解释(可解释性面板):构成分解 + 窗口明细 + 干预因果链

    回答"AI 的价值倾向为什么是现在这个值":
    ① 构成分解:value_tendency = α×底色(initial_tendency) + (1-α)×窗口均值
       (α 随累计体验衰减:体验少=人设主导,体验多=行为主导)
    ② 窗口明细:最近 N 条体验,每条含 行动/对齐度/反馈(为什么这条体验
       把倾向拉向某目标)
    ③ 干预因果链:每次专家干预(约束跳变)→ 之后倾向的滞后收敛量化
       (直接作为"AI 价值可被人为改变/内化滞后"的叙事证据)
    """
    global server
    agent = agent.strip()
    if not agent:
        return JSONResponse({"ok": False, "errors": ["缺少角色名"]})

    # ---- 运行中 agent 的内存状态(最准) ----
    live_agent = None
    if server is not None and getattr(server, "game", None) is not None:
        live_agent = server.game.agents.get(agent)
    if live_agent is None:
        return JSONResponse({"ok": False, "errors": ["角色不在运行中的模拟里: {}".format(agent)]})

    # ① 构成分解
    vt = live_agent.get_tendency() or {}
    base = getattr(live_agent, "initial_tendency", None) or {}
    obs = int(getattr(live_agent, "_tendency_obs", 0) or 0)
    # α 优先取引擎审计元信息(自适应过渡期唯一真相源),避免这里二次推导公式漂移
    meta = (live_agent.status or {}).get("tendency_meta") or {}
    if meta.get("alpha") is not None:
        alpha = float(meta["alpha"])
        decay_total = int(meta.get("decay_total") or 0)
    else:
        # 回退:与 observe_consequence 相同的自适应公式
        decay_total = max(1, int(getattr(live_agent, "_window_size", 15) or 15))
        alpha = round(max(0.1, 1.0 - obs / decay_total), 4)
    window = live_agent.status.get("tendency_window") or []
    # 窗口均值(与 observe_consequence 相同的指数加权口径,仅用于展示)
    win_mean = {}
    if window:
        n = len(window)
        goals = set()
        for w in window:
            goals.update(w.get("feedback", w).keys())
        for g in goals:
            vals = [w.get("feedback", w).get(g, 0.0) for w in window]
            weights = [0.5 ** (n - 1 - i) for i in range(n)]
            wsum = sum(weights) or 1.0
            win_mean[g] = sum(v * wt for v, wt in zip(vals, weights)) / wsum
    decomposition = {}
    for g in vt:
        base_v = base.get(g, 0.0)
        exp_v = win_mean.get(g, 0.0)
        decomposition[g] = {
            "tendency": round(vt[g], 4),
            "alpha": round(alpha, 4),
            "base_component": round(alpha * base_v, 4),
            "experience_component": round((1 - alpha) * exp_v, 4),
            "base_value": round(base_v, 4),
            "window_mean": round(exp_v, 4),
        }

    # ② 窗口明细(最近 N 条体验,正序=从早到晚;每条含 模拟时间/行动/对齐度/反馈)
    ckpt_dir = ""
    if compressor is not None:
        ckpt_dir = getattr(compressor, "checkpoints_folder", "") or ""
    if ckpt_dir and not os.path.isabs(ckpt_dir):
        ckpt_dir = os.path.join(BASE_DIR, ckpt_dir)
    details = []
    # 旧存档窗口条目无 time:从 checkpoint 快照反查近似模拟时间
    _backfill_window_times(ckpt_dir, agent, window)
    for w in window:
        feedback = w.get("feedback", w) if isinstance(w, dict) else {}
        details.append({
            "time": str(w.get("time", "")) if isinstance(w, dict) else "",
            "action": str(w.get("action", ""))[:160] if isinstance(w, dict) else "",
            "alignment": {k: round(v, 4) for k, v in (w.get("alignment", {}) or {}).items()} if isinstance(w, dict) else {},
            "feedback": {k: round(v, 4) for k, v in feedback.items()},
        })

    # ③ 干预因果链:读 checkpoints 的倾向序列,量化每次干预后倾向迁移
    chain = []
    if ckpt_dir and os.path.isdir(ckpt_dir):
        import datetime as _dt

        # 倾向序列(带缓存)
        series = load_tendency_series(ckpt_dir, agent)
        # 干预记录(该角色的、当前模拟的,按 sim_time 排序)
        iv_path = os.path.join(BASE_DIR, "results/checkpoints", "interventions.json")
        my_ivs = []
        if os.path.exists(iv_path):
            try:
                ivs = json.load(open(iv_path, encoding="utf-8"))
                cur_sim = sim_state.get("name", "") or ""
                my_ivs = sorted(
                    [x for x in ivs if x.get("agent") == agent
                     # 历史干预(未写 simulation 字段)视为与当前会话兼容,不丢弃
                     and (not x.get("simulation") or x.get("simulation") == cur_sim)],
                    key=lambda x: str(x.get("sim_time", "")),
                )
            except Exception as e:
                log.warning("explain 读取 interventions.json 失败: {}".format(e))
                my_ivs = []
        # 对每次干预:记录干预前最近倾向 + 干预后 2 小时倾向(量化内化滞后)
        for iv in my_ivs:
            try:
                ivt = _dt.datetime.strptime(str(iv.get("sim_time", "")), "%Y%m%d-%H:%M")
            except (ValueError, TypeError) as e:
                log.warning("explain 干预时间非法,跳过干预(agent={}, sim_time={}): {}".format(agent, iv.get("sim_time"), e))
                continue
            before = None
            after = None
            # before = 干预时刻及之前最近一个倾向快照
            # after = (ivt, ivt+2h] 内最后一个快照;若窗口内无点,则取 ivt 后
            # 第一个可用快照(迁移量不因无点而静默为 +0),并保证与 before 不同
            after_in_window = None
            for dt, v in series:
                if dt <= ivt:
                    before = v
                elif dt <= ivt + _dt.timedelta(hours=2):
                    after_in_window = v  # 持续取,保留窗口内最后一个
                else:
                    break
            if after_in_window is not None:
                after = after_in_window
            else:
                # 2h 内无点:后退到 ivt 后第一个点(可能 >2h)
                for dt2, v2 in series:
                    if dt2 > ivt:
                        after = v2
                        break
            # before/after 落到同一快照(干预恰在落盘点瞬时):向 after 方向
            # 推进到下一个不同快照,避免迁移量退化为 0
            if before is not None and after is not None and before == after:
                for _d2, _v2 in series:
                    if _d2 > ivt and _v2 != after:
                        after = _v2
                        break
            oldc = {k: round(vv, 4) for k, vv in (iv.get("old_constraints") or {}).items()}
            newc = {k: round(vv, 4) for k, vv in (iv.get("new_constraints") or {}).items()}
            # 倾向迁移量:干预后与干预前的差(取共现目标)
            shift = {}
            if before and after:
                for g in after:
                    if g in before:
                        shift[g] = round(after[g] - before[g], 4)
            chain.append({
                "real_time": iv.get("time", ""),
                "sim_time": iv.get("sim_time", ""),
                "old_constraints": oldc,
                "new_constraints": newc,
                "tendency_before": {k: round(v, 4) for k, v in (before or {}).items()},
                "tendency_after_2h": {k: round(v, 4) for k, v in (after or {}).items()},
                "tendency_shift_2h": shift,
            })

    return JSONResponse({
        "ok": True,
        "agent": agent,
        "value_tendency": {k: round(v, 4) for k, v in vt.items()},
        "constraints": {k: round(v, 4) for k, v in (live_agent.get_constraints() or {}).items()},
        "alpha": round(alpha, 4),
        "experience_count": obs,
        "window_size": len(window),
        "decomposition": decomposition,
        "window_details": details,
        "intervention_chain": chain,
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
    # 清洗:拒绝非目标名(如数字 "1")与 0 权重项(前端拖动/添加产生的垃圾)
    import re as _re
    cleaned = {}
    for g, v in goals.items():
        gs = str(g).strip()
        if not gs or _re.match(r"^\d", gs):
            continue  # 数字开头 = 误输入,丢弃
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv <= 0:
            continue  # 0 权重目标无治理意义,丢弃
        cleaned[gs] = fv
    if not cleaned:
        return JSONResponse({"ok": False, "errors": ["清洗后无有效目标(拒绝数字/0权重项)"]})
    goals = cleaned
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

    # 1.5) 同步运行中治理实例(关键:agent._governance 指向 game.governance
    #      同一对象,不更新则 consequence.feedback 仍按旧约束计算,
    #      干预只改了文件不改内存 → 倾向曲线"不动"、内化失效)
    if server is not None and getattr(server, "game", None) is not None:
        live_gov = getattr(server.game, "governance", None)
        if live_gov is not None:
            live_gov.data.setdefault("roles", {})[name] = dict(goals)

    # 2) 记录干预审计(可审计链)
    try:
        import time as _time, datetime
        # 干预时刻对应的模拟时间(供前端曲线画竖线)
        sim_time = ""
        try:
            sim_time = server.game._timer.get_date("%Y%m%d-%H:%M")
        except Exception as e:
            log.warning("读取模拟时间失败,干预 sim_time 留空: {}".format(e))
        audit_path = os.path.join(BASE_DIR, "results/checkpoints", "interventions.json")
        audit = []
        if os.path.exists(audit_path):
            with open(audit_path, "r", encoding="utf-8") as f:
                audit = json.load(f)
        audit.append({
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sim_time": sim_time,
            "simulation": sim_state.get("name", ""),  # 干预归属的模拟(跨模拟隔离)
            "agent": name,
            "old_constraints": old,
            "new_constraints": goals,
            "operator": "expert",
        })
        os.makedirs(os.path.dirname(audit_path), exist_ok=True)
        with open(audit_path, "w", encoding="utf-8") as f:
            json.dump(audit, f, ensure_ascii=False, indent=2)
    except Exception as e:
        # 审计失败不阻断主流程,但必须记录——干预无审计会破坏可审计链
        log.error("写入干预审计失败(agent={}): {}".format(name, e), exc_info=True)

    return JSONResponse({"ok": True, "name": name, "constraints": goals})


@app.get("/api/export-chart")
async def export_chart(agent: str = ""):
    """导出指定角色的倾向曲线 PNG(matplotlib 后端渲染,替代前端 canvas)

    数据源:当前模拟的 checkpoints(simulate-*.json 的 value_tendency 序列)+
    governance.json(约束)+ interventions.json(干预)。约束按干预时间画分段阶梯虚线。
    返回 PNG 二进制,前端按钮直接下载。
    """
    import io
    from fastapi.responses import Response

    agent = agent.strip()
    if not agent:
        return JSONResponse({"ok": False, "errors": ["缺少角色名"]})

    # 当前模拟检查点目录(绝对路径,避免 cwd 依赖)
    ckpt_dir = ""
    if compressor is not None:
        ckpt_dir = getattr(compressor, "checkpoints_folder", "") or ""
    if ckpt_dir and not os.path.isabs(ckpt_dir):
        ckpt_dir = os.path.join(BASE_DIR, ckpt_dir)
    if not ckpt_dir or not os.path.isdir(ckpt_dir):
        return JSONResponse({"ok": False, "errors": ["模拟检查点目录不可用: {}".format(ckpt_dir)]})

    # 读倾向序列(带缓存:目录 mtime 变化才重扫,几百 checkpoint 时避免重复解析)
    import datetime as _dt

    series = load_tendency_series(ckpt_dir, agent)
    if not series:
        return JSONResponse({"ok": False, "errors": ["该角色暂无倾向数据"]})

    # 约束(当前值)与干预(分段阶梯)
    gov_path = os.path.join(BASE_DIR, "governance.json")
    cons = {}
    if os.path.exists(gov_path):
        try:
            cons = json.load(open(gov_path, encoding="utf-8")).get("roles", {}).get(agent, {})
        except Exception as e:
            log.warning("export-chart 读取 governance.json 失败: {}".format(e))
            cons = {}
    iv_path = os.path.join(BASE_DIR, "results/checkpoints", "interventions.json")
    ivs = []
    if os.path.exists(iv_path):
        try:
            ivs = json.load(open(iv_path, encoding="utf-8"))
        except Exception as e:
            log.warning("export-chart 读取 interventions.json 失败: {}".format(e))
            ivs = []
    my_ivs = sorted(
        [x for x in ivs if x.get("agent") == agent and x.get("simulation") == sim_state.get("name", "")],
        key=lambda x: str(x.get("sim_time", "")),
    )

    # ---- matplotlib 渲染 ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from matplotlib.font_manager import FontProperties
    except Exception as e:
        return JSONResponse({"ok": False, "errors": ["matplotlib 不可用: {}".format(e)]})

    try:
        FONT = FontProperties(fname=r"C:\Windows\Fonts\msyh.ttc")
    except Exception:
        FONT = None

    times = [t for t, _ in series]
    t0, t1 = times[0], times[-1]
    goal_names = sorted(cons.keys()) or sorted(series[0][1].keys())
    COLORS = ["#2d6cdf", "#e07b39", "#2f9e44", "#c92a2a", "#9c36b5", "#0b7285"]
    color_of = {g: COLORS[i % len(COLORS)] for i, g in enumerate(goal_names)}

    fig, ax = plt.subplots(figsize=(11, 4.6))
    # 倾向实线
    for g in goal_names:
        ts, vs = [], []
        for t, vt in series:
            if g in vt:
                ts.append(t)
                vs.append(vt[g])
        if ts:
            ax.plot(ts, vs, color=color_of[g], linewidth=2.2, label="{} 倾向".format(g))
    # 约束分段阶梯虚线:起始段=最早干预前的 old(干预前的制度期望),
    # 之后每个干预时刻跳到 new;无干预时=当前 governance
    # 注意:interventions.json 是全局共享的(跨模拟),只取落在本图时间
    # 范围内的干预参与阶梯——图外的旧模拟干预不应用于本图的起始约束
    # (否则橙色 RC 虚线会画在旧模拟的 old_constraints 上,与前端不一致)
    steps = []  # (datetime, constraints)
    in_range_ivs = []  # 图内干预(用于起始约束选择)
    for iv in my_ivs:
        try:
            ivt = _dt.datetime.strptime(str(iv.get("sim_time", "")), "%Y%m%d-%H:%M")
        except (ValueError, TypeError) as e:
            log.warning("export-chart 干预时间非法,跳过(agent={}, sim_time={}): {}".format(agent, iv.get("sim_time"), e))
            continue
        steps.append((ivt, dict(iv.get("new_constraints") or cur)))
        if t0 <= ivt <= t1:
            in_range_ivs.append(iv)
    # 起始约束:图内有干预 → 最早图内干预的 old_constraints(干预前);
    # 图内无干预 → 当前 governance(图外旧干预不属于本模拟,不得污染)
    start_cons = dict(cons)
    if in_range_ivs:
        first_iv = in_range_ivs[0]
        oldc = first_iv.get("old_constraints")
        if isinstance(oldc, dict) and oldc:
            # 过滤数字键(误输入的垃圾目标,如 "1")
            oldc_clean = {k: v for k, v in oldc.items() if not str(k)[0].isdigit()}
            # 只保留当前约束维度内的目标(维度对齐:old 可能是旧模拟的 4 维,
            # 当前约束 3 维;跨维度混合会让虚线缺线/错位)
            start_cons = {
                k: v for k, v in {**cons, **oldc_clean}.items() if k in cons
            }
    # 画阶梯:每个目标一条虚线,段内用该时刻的约束值
    for g in goal_names:
        segs = []  # (start_t, value)
        for ivt, newc in steps:
            if g in newc:
                segs.append((ivt, newc[g]))
        # 起始段:t0 ~ 第一个干预,用干预前约束(start_cons)
        prev_t, prev_v = t0, start_cons.get(g, 0)
        for ivt, v in segs:
            if ivt <= t0 or ivt > t1:
                continue
            if ivt > prev_t:
                ax.hlines(prev_v, prev_t, ivt, color=color_of[g], linestyle="--", linewidth=1.4, alpha=0.7)
            prev_t, prev_v = ivt, v
        ax.hlines(prev_v, prev_t, t1, color=color_of[g], linestyle="--", linewidth=1.4, alpha=0.7)
    # 干预竖线
    for iv in my_ivs:
        try:
            ivt = _dt.datetime.strptime(str(iv.get("sim_time", "")), "%Y%m%d-%H:%M")
        except (ValueError, TypeError) as e:
            log.warning("export-chart 干预时间非法,跳过干预竖线(agent={}, sim_time={}): {}".format(agent, iv.get("sim_time"), e))
            continue
        if t0 <= ivt <= t1:
            ax.axvline(ivt, color="#e07b39", linewidth=1.6, linestyle=":", alpha=0.8)

    ax.set_title("{} — 价值倾向演变(实线=内化, 虚线=约束期望, 橙线=干预)".format(agent),
                 fontproperties=FONT, fontsize=13)
    ax.set_ylim(0, 1.0)
    ax.grid(True, alpha=0.3)
    # 不用 matplotlib 图例(会撑满画面);改为底部一行小字说明各色含义
    if goal_names:
        legend_txt = "  ".join(
            "{}: {}".format(g, color_of[g]) for g in goal_names
        )
        # 用色块+文字在底部画紧凑图例
        import matplotlib.patches as mpatches

        handles = [
            mpatches.Patch(color=color_of[g], label=g) for g in goal_names
        ]
        ax.legend(handles=handles, prop=FONT, fontsize=8, loc="lower center",
                  ncol=len(goal_names), frameon=True, bbox_to_anchor=(0.5, -0.32))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    # 中文文件名需 RFC 5987 编码(starlette 头仅支持 latin-1)
    from urllib.parse import quote

    fname = "tendency_{}.png".format(agent)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''{}".format(quote(fname))},
    )


def run_simulation(name, sim_config, start_step, step, stride):
    """后台线程运行模拟(框架驱动:framework Game + Simulator,不依赖 modules)"""
    global server, compressor
    try:
        import mavisframework.core.agent_core as fw_agent
        from mavisframework.core.timer import Timer
        from mavisframework.runtime.game import Game
        from mavisframework.runtime.simulator import Simulator

        fw_agent.chat_callback = on_chat_line
        # 绝对路径,避免 cwd 依赖导致检查点写到错误目录
        checkpoints_folder = os.path.join(BASE_DIR, "results/checkpoints", name)

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
        # 供 /api/goals 暴露 embedding 稳定性健康度(降级监控)
        game.consequence = consequence
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
        sim_state["name"] = name  # 当前模拟名(干预记录归属,跨模拟隔离)
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
    return await _render_index(request, embed="")


@app.get("/embed", response_class=HTMLResponse)
@app.get("/embed/scene", response_class=HTMLResponse)
@app.get("/embed/goals", response_class=HTMLResponse)
@app.get("/embed/explain", response_class=HTMLResponse)
async def embed_index(request: Request):
    """嵌入模式(供外部治理平台 iframe 引用):
    - /embed/scene   : 仅 Phaser 场景(无浮动面板,嵌入 canvas 位)
    - /embed/goals   : 仅治理约束面板(约束滑条+倾向曲线+解释)
    - /embed/explain : 倾向成因解释面板(构成分解+窗口明细+干预因果链)
    共享同一 WebSocket/数据源;通过 URL 参数控制 index.html 面板显隐。
    """
    path = request.url.path.rstrip("/")
    mode = "scene"
    if path.endswith("goals"):
        mode = "goals"
    elif path.endswith("explain"):
        mode = "explain"
    return await _render_index(request, embed=mode)


async def _render_index(request: Request, embed: str = ""):
    speed = int(request.query_params.get("speed", 0))
    zoom = float(request.query_params.get("zoom", 0))
    if speed < 0:
        speed = 0
    elif speed > 5:
        speed = 5
    play_speed = 2 ** speed
    payload = load_initial_payload(sim_state["start_time"], sim_state["stride"])
    # Phaser 脚本:本地 vendor 优先(断网可用),否则回退 CDN
    # 注意:必须用绝对路径(/static/...)——相对路径在 /embed/* 子路径下
    # 会被解析成 /embed/static/... 导致 404(独立页 / 恰好正常掩盖了问题)
    local_phaser = os.path.join(BASE_DIR, "frontend/static/vendor/phaser.min.js")
    if os.path.exists(local_phaser):
        phaser_src = "/static/vendor/phaser.min.js"
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
            "embed": embed,
            **payload,
        },
    )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    q: "asyncio.Queue" = asyncio.Queue()
    manager.register(ws, q)
    # 初始消息:确认连接 + 快照(追赶进度)
    await ws.send_json({"type": "init"})
    if compressor is not None and compressor.started:
        await ws.send_json(compressor.snapshot())

    # 独立心跳任务:每 5 秒无条件发 ping,不依赖队列是否为空。
    # 旧实现只在 q 超时才发 ping——模拟突发式推送(每步 6 角色批量)的
    # 空档期(建日程/LLM 慢)可能超过客户端看门狗容忍,导致误判断线重载。
    stop_hb = asyncio.Event()

    async def heartbeat():
        try:
            while not stop_hb.is_set():
                await asyncio.sleep(5.0)
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    return
        except asyncio.CancelledError:
            pass

    hb_task = asyncio.create_task(heartbeat())
    try:
        while True:
            data = await q.get()
            await ws.send_json(data)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        stop_hb.set()
        hb_task.cancel()
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
        while not os.path.exists(os.path.join(BASE_DIR, "results/checkpoints", name)):
            name = input(f"'{name}' doesn't exists, please re-enter the simulation name: ")
    else:
        if os.path.exists(os.path.join(BASE_DIR, "results/checkpoints", name)):
            # 存档名冲突:自动追加时间戳后缀(后台/服务化场景无 stdin,不能阻塞等输入)
            import time as _time
            suffix = _time.strftime("%m%d-%H%M%S")
            name = f"{name}-{suffix}"
            print(f"Simulation name '{args.name}' already exists, using '{name}'")

    checkpoints_folder = os.path.join(BASE_DIR, "results/checkpoints", name)
    if args.resume:
        sim_config = load_config_from_log(checkpoints_folder)
        if sim_config is None:
            print("No checkpoint file found to resume running.")
            exit(0)
        start_step = sim_config["step"]
        print("resume from step {} @ {}".format(start_step, sim_config["time"]))
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
