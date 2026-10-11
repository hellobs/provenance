"""FastAPI 路由:页面 / 嵌入 / API / WebSocket。

拆分自 live_fastapi.py;全局状态从 live.state 读取(模块属性,run_simulation 注入)。
"""
import datetime as _dt
import json
import math
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from live import state
from live.state import manager, log
from live import interventions as _interventions
from live.interventions import InterventionContext
from live.simulation import load_initial_payload
from live.chart import render_tendency_png
from live.reflections import (
    load_marks,
    marked_node_ids,
    rebuild_jsonl,
)

app = FastAPI(title="Provenance Live (FastAPI)")

# 被平台嵌入时需要跨源取数:白名单用环境变量给,并把取值打出来(不静默)。
# 2026-09-23 安全体检:默认**不再是** `*` —— 本服务没有鉴权,`*` 等于让任意网页都能
# 一个 fetch 把成品记录读走。默认只给本机来源;平台侧跨源取数请显式设域名。
# (iframe 嵌入本身不走 CORS,不受影响。)
import os as _os

from fastapi.middleware.cors import CORSMiddleware as _CORSMiddleware

from live.netguard import resolve_embed_origins as _resolve_embed_origins

_EMBED_ENV = _os.environ.get("EMBED_ALLOW_ORIGINS", "")
_EMBED_ORIGINS = _resolve_embed_origins(_EMBED_ENV)
app.add_middleware(
    _CORSMiddleware,
    allow_origins=_EMBED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
print("[embed] CORS allow_origins = {}{}".format(
    _EMBED_ORIGINS,
    "" if _EMBED_ENV.strip() else "  (未设 EMBED_ALLOW_ORIGINS → 只允许本机来源;平台侧跨源取数请显式设域名)"))

# 响应压缩(2026-09-24 体检):此前 5010 **完全不压** —— 实测带 `Accept-Encoding: gzip`
# 取最大的一条 case00 痕迹详情,仍是 954 223 bytes 原样返回。这些响应是 JSON,
# 键名重复度极高,压缩后通常降一个数量级;平台侧即便走内网,单页近 1 MB 也要等。
# minimum_size=1024:小响应(/health、单条计数)保持原样,不给小包加 20 字节头。
# 只影响 HTTP;WebSocket(推演实时帧)不走这里。
from fastapi.middleware.gzip import GZipMiddleware as _GZipMiddleware  # noqa: E402

app.add_middleware(_GZipMiddleware, minimum_size=1024)
print("[http] 响应压缩 = gzip(minimum_size=1024)")

app.mount(
    "/static",
    StaticFiles(directory=os.path.join(state.BASE_DIR, "frontend/static")),
    name="static",
)
# 统一历史数据界面(/api/runs + /embed/explore)来自共享模块;case01 实时面同样挂载。
from live.history import router as history_router  # noqa: E402 —— 局部导入避免顶层循环依赖
app.include_router(history_router)
templates = Jinja2Templates(directory=os.path.join(state.BASE_DIR, "frontend/templates"))
# 顶栏外部工具链接(与 live/history.py 同口径):mavis 的 config_tool,独立进程,默认 8060
import os as _os  # noqa: E402
templates.env.globals["extra_nav_links"] = [
    {"label": "Config tool ↗", "url": _os.environ.get("MAVIS_CONFIG_TOOL_URL",
                                                      "http://127.0.0.1:8060/"),
     "title": "mavis role/scenario configuration tool (separate process; override the "
              "address with MAVIS_CONFIG_TOOL_URL)"},
]


# ---------------------------------------------------------------------------
# 页面(主页面 + 嵌入模式)
# ---------------------------------------------------------------------------
# `_discover_agent_names` / `load_initial_payload` 的实现在 live/simulation.py
# (引擎侧装配,与 run_simulation 同一模块),本层只调用。此前两条各有一份副本,
# 且 simulation 那份全仓无人调用(2026-10-06 逐字节比对:两份只差一行注释)。


async def _active_components() -> list:
    """当前 case00 活动引擎(case00 的 case_id 映射沙盒价值引擎)激活的组件清单。

    以场景声明的 `meta.engine`(通常 sandbox-value)为准;读取失败回退到
    sandbox-value 的组件(其含 governance),保证 case00 治理面板不缺位。
    """
    try:
        from case_engine import engines as _eng
        from case_engine.config import load_yaml

        p = os.path.join(state.BASE_DIR, "cases", "case00_village", "scenario.yaml")
        eid = _case00_engine_id()
        if os.path.isfile(p):
            cfg = load_yaml(p)
            eid = getattr(cfg, "engine", None) or eid
        return _eng.components(eid)
    except Exception:  # noqa: BLE001 —— 派生失败回退 sandbox-value 组件,不缺治理
        return ["scene", "chat", "governance", "timeline", "reflection"]


def _case00_engine_id() -> str:
    """case00 场景声明的引擎 id(2026-09-27 迁至 live/interventions.py,保留别名)。"""
    return _interventions._case00_engine_id(state.BASE_DIR)


def sandbox_rollback_blocked() -> bool:
    """**沙盒场景不允许时间轴回滚**(2026-09-21 用户要求)。

    沙盒(生成式价值权重)跑的正是"决策→后果→反思→内化":后果一旦发生就成了经历。
    回滚干预等于把后果从经历里抹掉,与这条线的前提直接冲突 —— 所以服务端拒绝,
    前端也不渲染撤销入口(见 `undo_allowed` 上下文 + main_script 的按钮渲染)。
    (实现已迁至 live/interventions.py;此处保留别名供页面上下文使用。)
    """
    return _interventions.sandbox_rollback_blocked(state.BASE_DIR)


async def _render_index(request: Request, embed: str = ""):
    speed = int(request.query_params.get("speed", 0))
    zoom = float(request.query_params.get("zoom", 0))
    if speed < 0:
        speed = 0
    elif speed > 5:
        speed = 5
    play_speed = 2 ** speed
    payload = load_initial_payload(state.sim_state["start_time"], state.sim_state["stride"])
    # Phaser 脚本:本地 vendor 优先(断网可用),否则回退 CDN
    # 注意:必须用绝对路径(/static/...)——相对路径在 /embed/* 子路径下
    # 会被解析成 /embed/static/... 导致 404(独立页 / 恰好正常掩盖了问题)
    local_phaser = os.path.join(state.BASE_DIR, "frontend/static/vendor/phaser.min.js")
    if os.path.exists(local_phaser):
        phaser_src = "/static/vendor/phaser.min.js"
    else:
        phaser_src = "https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.js"
    comps = await _active_components()
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
            # 治理/权重/干预时间轴面板是否可用的唯一判断:由活动引擎组件决定
            "governance": "governance" in comps,
            "engine_components": comps,
            # 时间轴"撤销(回滚)"入口是否渲染:沙盒场景不给(见 sandbox_rollback_blocked)
            "undo_allowed": not sandbox_rollback_blocked(),
            **payload,
        },
    )


@app.get("/health")
async def health():
    """统一探活/结束状态协议(与 case01 vizkit 的 /health 字段对齐)。

    case00 没有"重开一局"(那是 case01 的协议),故 can_restart / restart_ready 恒为
    False;前端据此不显示重开按钮。finished/finish_reason 供探活与"已结束不得静默"。
    体检发现:此前 case00 无此端点,若前端因故轮询 /health 会一直 404。
    """
    st = state.sim_state
    status = st.get("status", "")
    return {
        "service": "case00-live",
        "finished": status == "done",
        "finish_reason": ("run_finished" if status == "done" else ""),
        "can_restart": False,
        "restart_ready": False,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # 5010 唯一实时入口时,首页 = 完整 case00 可视化平台:
    # 中央小镇场景(Phaser)在动,右侧治理约束面板(滑条/倾向/解释)+ 底部干预时间轴同屏。
    # 传 embed=""(而非 "goals"):main_script 的 wantScene = !embedMode 才成立,
    # 否则会不建 Phaser、主区空白、只剩治理面板(用户:点运行后只有面板没有小镇,异常)。
    # 纯面板仍走各自的 /embed/*(goals / explain / timeline / scene)。
    return await _render_index(request)


@app.get("/embed", response_class=HTMLResponse)
@app.get("/embed/scene", response_class=HTMLResponse)
@app.get("/embed/goals", response_class=HTMLResponse)
@app.get("/embed/explain", response_class=HTMLResponse)
@app.get("/embed/timeline", response_class=HTMLResponse)
@app.get("/embed/reflections", response_class=HTMLResponse)
async def embed_index(request: Request):
    """嵌入模式(供外部治理平台 iframe 引用):
    - /embed/scene       : 仅 Phaser 场景(无浮动面板,嵌入 canvas 位)
    - /embed/goals       : 仅治理约束面板(约束滑条+倾向曲线+解释)
    - /embed/explain     : 倾向成因解释面板(构成分解+窗口明细+干预因果链)
    - /embed/timeline    : 干预时间轴面板(全部角色干预事件 + 撤销 + 详情)
    - /embed/reflections : 反思标记面板(人机协同闭环,LoRA 线数据入口)
    统一数据界面 /embed/explore 由共享模块 live/history.py 提供(case00/case01 通用)。
    共享同一 WebSocket/数据源;通过 URL 参数控制 index.html 面板显隐。
    """
    path = request.url.path.rstrip("/")
    mode = "scene"
    if path.endswith("goals"):
        mode = "goals"
    elif path.endswith("explain"):
        mode = "explain"
    elif path.endswith("timeline"):
        mode = "timeline"
    elif path.endswith("reflections"):
        return _render_reflections_page()
    return await _render_index(request, embed=mode)


# ---------------------------------------------------------------------------
# /api/goals(约束 GET/POST)
# ---------------------------------------------------------------------------
@app.get("/api/goals")
async def get_goals():
    """返回所有角色的治理约束(期望)与价值倾向(内化结果)"""
    server = state.server
    # 约束来自 governance.json(制度层)
    from mavisframework.runtime.governance import Governance
    gov_path = os.path.join(state.BASE_DIR, "governance.json")
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
    current_sim = state.current_sim_name()
    iv_path = state.checkpoint_file("interventions.json")
    if os.path.exists(iv_path):
        try:
            all_iv = json.load(open(iv_path, encoding="utf-8"))
            interventions = [x for x in all_iv if x.get("simulation") == current_sim]
        except Exception as e:
            log.warning("failed to read interventions.json: {}".format(e))
            interventions = []
    # embedding 稳定性健康度(后果反馈降级监控)
    embedding_health = {}
    if server is not None and server.game is not None:
        try:
            engine = getattr(server.game, "consequence", None)
            if engine is not None and hasattr(engine, "health"):
                embedding_health = engine.health()
        except Exception as e:
            log.warning("failed to read the embedding health status: {}".format(e))
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


# ---------------------------------------------------------------------------
# 写端点共用守卫(2026-09-24 深度体检,与 vizkit /control/restart 同一口径):
# 这些是会**改变状态**的口子,而浏览器里一个 <form> 就能发出 text/plain 等跨站
# 简单请求(不需要 CORS 预检)——响应读不到但副作用照样发生。所以只认
# application/json;没带 Content-Type 的空 body POST(老页面 / curl)仍放行。
# ---------------------------------------------------------------------------
async def _json_body(request: Request):
    """JSON-only 守卫 + 容错解析。

    返回 (body, None) 或 (None, JSONResponse):body 非 dict(空 body / 坏 JSON /
    JSON 数组)时按 {} 处理,由各端点自身的字段校验兜住 —— 不抛 500。
    """
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype and ctype != "application/json":
        return None, JSONResponse(
            {"ok": False, "errors": ["Content-Type: application/json is required (form submissions are not accepted)"]},
            status_code=415)
    try:
        body = await request.json()
    except Exception:      # noqa: BLE001 —— 没有 body 是正常的(老页面/curl);坏 body 走字段校验
        return {}, None
    return (body if isinstance(body, dict) else {}), None


@app.post("/api/goals")
async def update_goals(request: Request):
    """更新某角色的治理约束(专家设定期望目标权重)。

    逻辑已迁至 live/interventions.py 的 WeightAdjustStrategy(干预策略注册表,
    2026-09-27);本端点保留路径为薄壳(前端/就绪探针依赖),也可经统一分发口
    `POST /api/intervention/goals` 调用。
    """
    body, err = await _json_body(request)
    if err is not None:
        return err
    res = _interventions.get("goals")().apply(_intervention_ctx(), body)
    return _wrap_intervention_result("goals", res)


def _intervention_ctx() -> InterventionContext:
    """按当前运行状态构建干预上下文(策略不直接读 live.state,保证可测)。"""
    try:
        ckpt_dir = state.current_ckpt_dir()
    except Exception:  # noqa: BLE001 —— 未运行/压缩器缺失:ckpt 留空
        ckpt_dir = ""
    return InterventionContext(
        server=state.server,
        base_dir=state.BASE_DIR,
        ckpt_dir=ckpt_dir or "",
        sim_name=state.current_sim_name(),
        sim_time=state.current_sim_time("%Y%m%d-%H:%M"),
    )


def _wrap_intervention_result(strategy_id: str, res) -> JSONResponse:
    """策略返回普通 dict → 200;InterventionResult → 自带状态码。

    策略返回 None = 策略自身的 bug(如漏了 return)—— 显式 500 + log,
    不静默包成 "null" 响应体让调用方猜。
    """
    if res is None:
        log.error("intervention strategy {!r} returned None (a missing return in the strategy?)".format(strategy_id))
        return JSONResponse({"ok": False,
                             "errors": ["intervention strategy {!r} returned an empty result (implementation defect)".format(
                                 strategy_id)]}, status_code=500)
    if isinstance(res, _interventions.InterventionResult):
        return JSONResponse(dict(res), status_code=res.status)
    return JSONResponse(res)


@app.post("/api/intervention/{strategy_id}")
async def run_intervention(strategy_id: str, request: Request):
    """统一干预分发口:按 strategy_id 调注册表里的策略。

    这是"新增干预 = 写类 + register,零改动路由"的兑现口:新策略注册后
    自动可经本端点调用,无需新增任何路由。旧路径(/api/goals 等)保留为
    薄壳,前端与就绪探针零改动。未知策略 404(带已注册清单,不静默)。
    """
    body, err = await _json_body(request)
    if err is not None:
        return err
    if not _interventions.known(strategy_id):
        return JSONResponse(
            {"ok": False,
             "errors": ["unknown intervention strategy: {!r}; registered: {}".format(
                 strategy_id, " / ".join(_interventions.all_ids()))]},
            status_code=404)
    res = _interventions.get(strategy_id)().apply(_intervention_ctx(), body)
    return _wrap_intervention_result(strategy_id, res)


@app.get("/api/interventions")
async def list_interventions():
    """已注册干预策略清单(镜像引擎的枚举端点;对接方据此发现能力)。"""
    return {"ok": True, "count": len(_interventions.all_ids()),
            "strategies": [_interventions.describe(sid)
                           for sid in _interventions.all_ids()]}


@app.post("/api/undo-intervention")
async def undo_intervention(request: Request):
    """撤销一次专家干预:回滚到该次干预的 old_constraints。

    逻辑已迁至 live/interventions.py 的 UndoInterventionStrategy(干预策略注册表,
    2026-09-27);本端点保留路径为薄壳,也可经 `POST /api/intervention/undo` 调用。
    """
    body, err = await _json_body(request)
    if err is not None:
        return err
    res = _interventions.get("undo")().apply(_intervention_ctx(), body)
    return _wrap_intervention_result("undo", res)


# ---------------------------------------------------------------------------
# /api/explain(倾向成因解释)
# ---------------------------------------------------------------------------
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
    server = state.server
    agent = agent.strip()
    if not agent:
        return JSONResponse({"ok": False, "errors": ["the agent name is missing"]})

    # ---- 运行中 agent 的内存状态(最准) ----
    live_agent = None
    if server is not None and getattr(server, "game", None) is not None:
        live_agent = server.game.agents.get(agent)
    if live_agent is None:
        return JSONResponse({"ok": False, "errors": ["the agent is not in the running simulation: {}".format(agent)]})

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
    # 窗口均值(与 observe_consequence 相同的口径,仅用于展示)
    # 近因性按模拟时间衰减:权重 = decay_per_hour^age_hours,age=当前模拟时间−条目time
    decay_ph = float(getattr(live_agent, "_tendency_decay_per_hour", 0.6) or 0.6)
    _now = None
    try:
        _now = live_agent._timer.get_date()
    except Exception:
        _now = None
    win_mean = {}
    if window:
        n = len(window)
        import datetime as _dt
        ages = []
        for i, w in enumerate(window):
            t_str = str(w.get("time", "") or "")
            if t_str and _now is not None:
                try:
                    t_entry = _dt.datetime.strptime(t_str, "%Y%m%d-%H:%M")
                    ages.append(max(0.0, (_now - t_entry).total_seconds() / 3600.0))
                    continue
                except (ValueError, TypeError):
                    pass
            ages.append(float(n - i) * 0.25)
        weights = [decay_ph ** a for a in ages]
        goals = set()
        for w in window:
            goals.update(w.get("feedback", w).keys())
        for g in goals:
            vals = [w.get("feedback", w).get(g, 0.0) for w in window]
            wsum = sum(weights) or 1.0
            win_mean[g] = sum(v * wt for v, wt in zip(vals, weights)) / wsum
    decomposition = {}
    # 归一化(2026-09-24 第十一轮体检):引擎在 observe_consequence 里是
    #   ①先算窗口加权均值 ②raw = α×底色 + (1-α)×均值 ③按约束删目标 ④**整体归一化**,
    # 而面板以前直接把 ①/② 的**未归一化**分量与 ④ 之后的 tendency 摆在一起,
    # 于是屏幕上出现"48.4% = 底色 2.5% + 体验 13.3%"这种自己都不等的等式
    # (实测 stock-en8:三个目标偏差 -0.18 ~ -0.25)。
    # 现在改成:把**已经显示出来的那个 tendency** 按"混合前两个来源的原始占比"劈开 ——
    #   base 占比 = α×底色 / raw,体验占比 = (1-α)×均值 / raw
    # 这样两项之和**恒等于** tendency(屏幕上那个等式再也不会自相矛盾);
    # 归一化的系数(Σraw)与原始值照旧给出,口径不藏。
    _constraints = set((live_agent.get_constraints() or {}).keys())
    raw = {}
    for g in set(win_mean) | set(base):
        raw[g] = alpha * base.get(g, 0.0) + (1 - alpha) * win_mean.get(g, 0.0)
    if _constraints:
        raw = {g: v for g, v in raw.items() if g in _constraints}
    raw_total = sum(raw.values()) or 1.0
    for g in vt:
        base_v = base.get(g, 0.0)
        exp_v = win_mean.get(g, 0.0)
        raw_v = raw.get(g, 0.0)
        if raw_v > 0:
            base_share = alpha * base_v / raw_v
        else:
            # 没有可拆的来源(该目标既无底色也无窗口反馈):全算到底色项,
            # 至少保证"两项之和 = tendency"这条不破。
            base_share = 1.0
        decomposition[g] = {
            "tendency": round(vt[g], 4),
            "alpha": round(alpha, 4),
            "base_component": round(vt[g] * base_share, 4),
            "experience_component": round(vt[g] * (1.0 - base_share), 4),
            # 原始值(未归一化)照旧给出,便于排查口径
            "base_value": round(base_v, 4),
            "window_mean": round(exp_v, 4),
            "raw_component": round(raw_v, 4),
        }
    # 归一化本身也是口径的一部分:不写出来,别人看到"底色 6.5% 而 α×底色=2.5%"会以为算错了
    normalization = {
        "raw_total": round(raw_total, 4),
        "factor": round(1.0 / raw_total, 4),
        "constraints_applied": bool(_constraints),
        "note": ("两个分量按'混合前来源占比'劈开,相加恒等于 tendency;"
                 "引擎口径是先把两项混合、按约束过滤、再整体归一"),
    }

    # ② 窗口明细(最近 N 条体验,正序=从早到晚;每条含 模拟时间/行动/对齐度/反馈)
    ckpt_dir = state.current_ckpt_dir()
    details = []
    # 旧存档窗口条目无 time:从 checkpoint 快照反查近似模拟时间
    state._backfill_window_times(ckpt_dir, agent, window)
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
        series = state.load_tendency_series(ckpt_dir, agent)
        # 干预记录(该角色的、当前模拟的,按 sim_time 排序)
        iv_path = state.checkpoint_file("interventions.json")
        my_ivs = []
        if os.path.exists(iv_path):
            try:
                ivs = json.load(open(iv_path, encoding="utf-8"))
                cur_sim = state.current_sim_name()
                my_ivs = sorted(
                    [x for x in ivs if x.get("agent") == agent
                     # 历史干预(未写 simulation 字段)视为与当前会话兼容,不丢弃
                     and (not x.get("simulation") or x.get("simulation") == cur_sim)],
                    # 同 sim_time 的多条按墙钟 time 兜底(第十一轮体检:连点会在同一模拟分钟留下多条)
                    key=state.intervention_sort_key,
                )
            except Exception as e:
                log.warning("explain: failed to read interventions.json: {}".format(e))
                my_ivs = []
        # 对每次干预:记录干预前最近倾向 + 干预后 2 小时倾向(量化内化滞后)
        for iv in my_ivs:
            try:
                ivt = _dt.datetime.strptime(str(iv.get("sim_time", "")), "%Y%m%d-%H:%M")
            except (ValueError, TypeError) as e:
                log.warning("explain: invalid intervention time, skipping it (agent={}, sim_time={}): {}".format(agent, iv.get("sim_time"), e))
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
        "normalization": normalization,
        "window_details": details,
        "intervention_chain": chain,
    })


# ---------------------------------------------------------------------------
# /api/timeline(干预时间轴)
# ---------------------------------------------------------------------------
@app.get("/api/timeline")
async def timeline_data():
    """干预时间轴数据(全部角色混排,按 sim_time 排序)。

    供底部时间轴横条 / /embed/timeline 使用:
    - events: 每次干预(角色/时间/old→new 权重/备注/operator)
    - 每个事件附 tendency_before/after(干预前最近快照 + 干预后 2h 内最后快照,
      与 explain 干预链同口径;数据源 = checkpoints 倾向序列,目录 mtime 缓存)
    - undo 记录也列出(operator=undo,便于时间轴上展示"撤销"事件本身)
    返回按 sim_time 升序;sim_time 字符串格式统一 %Y%m%d-%H:%M。
    """
    cur_sim = state.current_sim_name()
    # 当前模拟时间(用于横轴范围上限与实时定位)
    cur_sim_time = state.current_sim_time("%Y%m%d-%H:%M")
    # checkpoint 目录(倾向序列来源)
    ckpt_dir = state.current_ckpt_dir()
    # 读干预记录(所有角色;当前模拟的优先,历史无 simulation 字段的兼容展示)
    iv_path = state.checkpoint_file("interventions.json")
    ivs = []
    if os.path.exists(iv_path):
        try:
            all_ivs = json.load(open(iv_path, encoding="utf-8"))
            ivs = sorted(
                [x for x in all_ivs if x.get("agent")
                 and (not x.get("simulation") or x.get("simulation") == cur_sim)],
                # 同 sim_time 的多条按墙钟 time 兜底(第十一轮体检)
                key=state.intervention_sort_key,
            )
        except Exception as e:
            log.warning("timeline: failed to read interventions.json: {}".format(e))
            ivs = []
    # 每角色倾向序列(一次加载,事件按 agent 取)
    series_by_agent = {}
    if ckpt_dir and os.path.isdir(ckpt_dir):
        agents = sorted(set(str(x.get("agent", "")) for x in ivs))
        for ag in agents:
            series_by_agent[ag] = state.load_tendency_series(ckpt_dir, ag)
    # 组装事件
    import datetime as _dt

    events = []
    for iv in ivs:
        agent = str(iv.get("agent", ""))
        sim_t = str(iv.get("sim_time", ""))
        oldc = {k: round(float(v), 4) for k, v in (iv.get("old_constraints") or {}).items()}
        newc = {k: round(float(v), 4) for k, v in (iv.get("new_constraints") or {}).items()}
        # 迁移量(与 explain 同口径):干预前最近 + 干预后 2h 内最后
        before = None
        after = None
        try:
            ivt = _dt.datetime.strptime(sim_t, "%Y%m%d-%H:%M")
        except (ValueError, TypeError):
            ivt = None
        if ivt is not None:
            series = series_by_agent.get(agent, [])
            after_in_win = None
            for dt, v in series:
                if dt <= ivt:
                    before = v
                elif dt <= ivt + _dt.timedelta(hours=2):
                    after_in_win = v
                else:
                    break
            after = after_in_win
            if after is None and series:
                # 2h 内无点:取干预后第一个快照(不为空则不静默为 0)
                for dt2, v2 in series:
                    if dt2 > ivt:
                        after = v2
                        break
            if before is not None and after is not None and before == after:
                for _d3, _v3 in series:
                    if _v3 != after:
                        after = _v3
                        break
        shift = {}
        if before and after:
            for g in after:
                if g in before:
                    shift[g] = round(after[g] - before[g], 4)
        events.append({
            "agent": agent,
            "sim_time": sim_t,
            "real_time": str(iv.get("time", "")),
            "operator": str(iv.get("operator", "expert")),
            "revoked": bool(iv.get("revoked")),
            "note": str(iv.get("note", "") or ""),
            "old_constraints": oldc,
            "new_constraints": newc,
            "tendency_before": {k: round(v, 4) for k, v in (before or {}).items()},
            "tendency_after": {k: round(v, 4) for k, v in (after or {}).items()},
            "tendency_shift": shift,
        })
    return JSONResponse({
        "ok": True,
        "simulation": cur_sim,
        "start_time": str(state.sim_state.get("start_time", "") or ""),
        "cur_sim_time": cur_sim_time,
        "events": events,
    })


# ---------------------------------------------------------------------------
# /api/export-chart(倾向曲线 PNG)
# ---------------------------------------------------------------------------
@app.get("/api/export-chart")
async def export_chart(agent: str = ""):
    """导出指定角色的倾向曲线 PNG(matplotlib 后端渲染,替代前端 canvas)"""
    from fastapi.responses import Response
    from urllib.parse import quote

    agent = agent.strip()
    if not agent:
        return JSONResponse({"ok": False, "errors": ["the agent name is missing"]})

    ckpt_dir = state.current_ckpt_dir()
    cur_sim = state.current_sim_name()
    iv_path = state.checkpoint_file("interventions.json")
    ivs = state.read_json(iv_path, default=[]) or []

    png = render_tendency_png(ckpt_dir, agent,
                              constraints=None, my_ivs=ivs, cur_sim=cur_sim)
    if png is None:
        return JSONResponse({"ok": False, "errors": ["this agent has no tendency data yet (or matplotlib is unavailable)"]})

    # 中文文件名需 RFC 5987 编码(starlette 头仅支持 latin-1)
    fname = "tendency_{}.png".format(agent)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''{}".format(quote(fname))},
    )


# ---------------------------------------------------------------------------
# /api/reflections + /embed/reflections(反思标记,人机协同闭环 → LoRA 线)
# ---------------------------------------------------------------------------
@app.get("/api/reflections")
async def list_reflections():
    """反思列表(人机协同闭环数据源):

    - pending: 运行中 agent 记忆流里的 thought 节点(Concept),未被标记
    - marked:  已标记(读 reflection_marks.json,含 verdict/correction)
    反思文本只在运行期存在(checkpoint 快照仅存 node_id 引用),
    因此标记时会把文本一并写档,保证事后可审计/可导出。
    """
    server = state.server
    marks = load_marks()
    marked_ids = marked_node_ids()
    marked = [m for m in marks if not state.current_sim_name()
              or m.get("simulation") == state.current_sim_name()]

    pending = []
    if server is not None and getattr(server, "game", None) is not None:
        for name, agent in server.game.agents.items():
            try:
                concepts = agent.associate.retrieve_thoughts()
            except Exception as e:
                log.warning("retrieve_thoughts failed (agent={}): {}".format(name, e))
                continue
            for c in concepts:
                if c.node_id in marked_ids:
                    continue
                pending.append({
                    "node_id": c.node_id,
                    "agent": name,
                    "sim_time": c.create.strftime("%Y%m%d-%H:%M"),
                    "text": c.describe,
                    "poignancy": c.poignancy,
                })
    # 最早的在前,便于专家按时间线处理
    pending.sort(key=lambda x: x["sim_time"])
    return JSONResponse({
        "ok": True,
        "simulation": state.current_sim_name(),
        "pending": pending[:80],   # 上限 80 条,避免面板过载
        "marked": marked[-50:],    # 最近 50 条已标记
    })


@app.post("/api/reflections/mark")
async def mark_reflection(request: Request):
    """专家标记一条反思:verdict ∈ correct/incorrect/partial(+可选纠正文本)。

    逻辑已迁至 live/interventions.py 的 ReflectionMarkStrategy(干预策略注册表,
    2026-09-27);本端点保留路径为薄壳,也可经 `POST /api/intervention/mark` 调用。
    """
    body, err = await _json_body(request)
    if err is not None:
        return err
    res = _interventions.get("mark")().apply(_intervention_ctx(), body)
    return _wrap_intervention_result("mark", res)


@app.get("/api/reflections/export.jsonl")
async def export_reflections_jsonl():
    """导出 LoRA 线训练数据(JSONL,每行一个标记样本)。"""
    from fastapi.responses import PlainTextResponse
    out = rebuild_jsonl()
    with open(out, "r", encoding="utf-8") as f:
        content = f.read()
    return PlainTextResponse(
        content,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=reflection_marks.jsonl"},
    )


def _render_reflections_page() -> HTMLResponse:
    """反思标记面板(独立 HTML,无 Phaser;自连 /api/reflections)。"""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Reflection marking · expert in the loop</title>
<style>
  body { margin: 0; font-family: "Microsoft YaHei", system-ui, sans-serif; background: #f4f6f5; color: #223; }
  header { background: #1d3a2f; color: #fff; padding: 10px 18px; display: flex; justify-content: space-between; align-items: center; }
  header h1 { font-size: 16px; margin: 0; }
  header a { color: #cfe3d8; font-size: 12px; }
  main { max-width: 860px; margin: 18px auto; padding: 0 12px; }
  .card { background: #fff; border: 1px solid #dde4e0; border-radius: 10px; padding: 12px 16px; margin-bottom: 12px; }
  .card .meta { color: #778; font-size: 12px; margin-bottom: 6px; }
  .card .meta b { color: #2d6cdf; }
  .card .thought { font-size: 15px; line-height: 1.55; }
  .actions { margin-top: 8px; display: flex; gap: 8px; }
  .actions button { border: 1px solid #c4cfd0; background: #fff; border-radius: 6px; padding: 4px 12px; cursor: pointer; font-size: 13px; }
  .actions button.ok:hover { background: #e7f5ec; border-color: #2f9e44; }
  .actions button.bad:hover { background: #fdecec; border-color: #c92a2a; }
  .actions button.part:hover { background: #fff4e6; border-color: #e07b39; }
  textarea { width: 100%; box-sizing: border-box; border: 1px solid #c4cfd0; border-radius: 6px; min-height: 44px; font-size: 13px; margin-top: 6px; padding: 6px; display: none; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
  .badge.correct { background: #e7f5ec; color: #2f9e44; }
  .badge.incorrect { background: #fdecec; color: #c92a2a; }
  .badge.partial { background: #fff4e6; color: #b26a00; }
  h2 { font-size: 14px; color: #456; margin: 22px 0 8px; }
  .empty { color: #99a; text-align: center; padding: 30px; }
  .marked .thought { font-size: 13px; color: #556; }
  .marked .corr { font-size: 13px; color: #b26a00; margin-top: 4px; }
</style>
</head>
<body>
<header>
  <h1>Reflection marking · the expert-in-the-loop closed circuit</h1>
  <a href="/api/reflections/export.jsonl">Export JSONL (LoRA line)⇩</a>
</header>
<main>
  <h2>Reflections awaiting a mark</h2>
  <div id="pending"><div class="empty">Loading...</div></div>
  <h2>Marked</h2>
  <div id="marked"><div class="empty">None yet</div></div>
</main>
<script>
const VERDICTS = [["correct","✓ correct"],["incorrect","✗ incorrect"],["partial","◐ partially correct"]];
// 转义helper(2026-10-06 修):本节的反思正文/角色名/专家纠正文本此前直接拼进 innerHTML ——
// 模型产物里出现 `<`、`&` 会被当标签解析,专家在纠正框里输入 `<` 同样会破坏渲染。
const esc = s => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
let picks = {};

async function refresh() {
  const r = await fetch("/api/reflections");
  const d = await r.json();
  const box = document.getElementById("pending");
  if (!d.ok || !d.pending.length) { box.innerHTML = '<div class="empty">no reflections awaiting a mark yet (they appear once the run produces reflections)</div>'; }
  else {
    box.innerHTML = "";
    for (const p of d.pending) {
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `<div class="meta"><b>${esc(p.agent)}</b> · ${esc(p.sim_time)} · P.${esc(p.poignancy)}</div>
        <div class="thought">${esc(p.text)}</div>
        <div class="actions">${VERDICTS.map(([v, t]) =>
          `<button class="${v === "correct" ? "ok" : v === "incorrect" ? "bad" : "part"}" data-v="${v}">${t}</button>`).join("")}</div>
        <textarea placeholder="Correction text (required for incorrect / partially correct)..." style="display:none"></textarea>
        <div class="actions"><button data-v="__submit" style="display:none;background:#2d6cdf;color:#fff;">Submit the mark</button></div>`;
      const ta = card.querySelector("textarea");
      const submit = card.querySelector('[data-v="__submit"]');
      card.querySelectorAll(".actions button").forEach(b => {
        b.onclick = () => {
          const v = b.dataset.v;
          if (v === "__submit") {
            submitMark(p, ta.value);
          } else {
            picks[p.node_id] = v;
            ta.style.display = (v === "correct") ? "none" : "block";
            submit.style.display = "block";
          }
        };
      });
      box.appendChild(card);
    }
  }
  const mbox = document.getElementById("marked");
  if (!d.marked.length) { mbox.innerHTML = '<div class="empty">Nothing marked yet</div>'; }
  else {
    mbox.innerHTML = "";
    for (const m of d.marked.slice().reverse()) {
      const card = document.createElement("div");
      card.className = "card marked";
      card.innerHTML = `<div class="meta"><b>${esc(m.agent)}</b> · ${esc(m.sim_time)} · <span class="badge ${esc(m.verdict)}">${esc(m.verdict)}</span></div>
        <div class="thought">${esc(m.thought)}</div>` +
        (m.correction ? `<div class="corr">Correction: ${esc(m.correction)}</div>` : "");
      mbox.appendChild(card);
    }
  }
}

async function submitMark(p, correction) {
  const verdict = picks[p.node_id] || "correct";
  const body = { agent: p.agent, node_id: p.node_id, sim_time: p.sim_time,
                 text: p.text, verdict, correction };
  const r = await fetch("/api/reflections/mark", { method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) });
  if (r.ok) { await refresh(); } else { alert("Marking failed: " + (await r.text())); }
}

refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>"""
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# WebSocket /ws
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    import asyncio

    await ws.accept()
    import asyncio as _asyncio
    q: "asyncio.Queue" = _asyncio.Queue()
    manager.register(ws, q)
    # 初始消息:确认连接 + 快照(追赶进度)
    await ws.send_json({"type": "init"})
    if state.compressor is not None and state.compressor.started:
        await ws.send_json(state.compressor.snapshot())
    # 已经结束/报错时才连进来的人:必须立刻告知,否则页面只显示一个不动的小镇,
    # 用户会判断"可视化里根本没反应"(2026-09-19 实测反馈)。**不允许静默**。
    if state.sim_state.get("status") == "done":
        await ws.send_json({"type": "done", "reason": "run_finished"})
    elif state.sim_state.get("status") == "error":
        await ws.send_json({"type": "error", "message": state.sim_state.get("error", "")})

    # 独立心跳任务:每 5 秒无条件发 ping,不依赖队列是否为空。
    # 旧实现只在 q 超时才发 ping——模拟突发式推送(每步 6 角色批量)的
    # 空档期(建日程/LLM 慢)可能超过客户端看门狗容忍,导致误判断线重载。
    stop_hb = _asyncio.Event()

    async def heartbeat():
        try:
            while not stop_hb.is_set():
                await _asyncio.sleep(5.0)
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    log.debug("心跳终止(连接已关闭)", exc_info=True)
                    return
        except _asyncio.CancelledError:
            pass

    hb_task = _asyncio.create_task(heartbeat())
    try:
        while True:
            data = await q.get()
            await ws.send_json(data)
    except WebSocketDisconnect:
        pass
    except RuntimeError as e:
        # 客户端断开后仍试图 send 的常见竞态("Cannot call send once a close message"):
        # 正常的断线,不该当异常打 traceback 刷屏(体检发现 live_case00.out 反复出现)。
        log.debug("websocket send 竞态(连接已关闭): %s", e)
    except Exception:
        # 断线是常态;但非断线的异常必须留痕,否则前端黑屏时无从排查。
        log.warning("websocket 连接异常关闭", exc_info=True)
    finally:
        stop_hb.set()
        hb_task.cancel()
        manager.unregister(ws)
