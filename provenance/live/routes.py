"""FastAPI 路由:页面 / 嵌入 / API / WebSocket。

拆分自 live_fastapi.py;全局状态从 live.state 读取(模块属性,run_simulation 注入)。
"""
import json
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from live import state
from live.state import manager, log
from live.chart import render_tendency_png
from live.reflections import (
    VALID_VERDICTS,
    load_marks,
    new_mark,
    rebuild_jsonl,
    marked_node_ids,
    append_mark,
    build_lora_sample,
)

app = FastAPI(title="Provenance Live (FastAPI)")
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(state.BASE_DIR, "frontend/static")),
    name="static",
)
templates = Jinja2Templates(directory=os.path.join(state.BASE_DIR, "frontend/templates"))


# ---------------------------------------------------------------------------
# 页面(主页面 + 嵌入模式)
# ---------------------------------------------------------------------------
def _discover_agent_names():
    """动态发现 agents 目录下的所有角色名(不写死,支持任意角色数)"""
    from mavisframework.config.loader import personas
    agents_root = os.path.join(state.BASE_DIR, "frontend/static/assets/village/agents")
    if os.path.isdir(agents_root):
        names = [
            n for n in sorted(os.listdir(agents_root))
            if os.path.exists(os.path.join(agents_root, n, "agent.json"))
        ]
        if names:
            return names
    return personas


def load_initial_payload(start_datetime, stride):
    from datetime import datetime
    persona_init_pos = {}
    description = {}
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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return await _render_index(request, embed="")


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


@app.post("/api/goals")
async def update_goals(request: Request):
    """更新某角色的治理约束(专家设定期望目标权重)

    IVD 语义:约束存在于 governance.json(制度层),不写入 agent.json(AI 本体)。
    记录干预审计(interventions.json):时间/角色/旧值→新值。
    约束不直接注入 prompt——仅作为客观后果反馈的对照基准。
    """
    server = state.server
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
    gov_path = os.path.join(state.BASE_DIR, "governance.json")
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
        import datetime
        # 干预时刻对应的模拟时间(供前端曲线画竖线)
        sim_time = state.current_sim_time("%Y%m%d-%H:%M")
        audit_path = state.checkpoint_file("interventions.json")
        audit = []
        if os.path.exists(audit_path):
            with open(audit_path, "r", encoding="utf-8") as f:
                audit = json.load(f)
        audit.append({
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sim_time": sim_time,
            "simulation": state.current_sim_name(),  # 干预归属的模拟(跨模拟隔离)
            "agent": name,
            "old_constraints": old,
            "new_constraints": goals,
            "operator": "expert",
            "note": str(body.get("note", "") or "").strip(),  # 专家干预理由(可审计/时间轴展示)
        })
        os.makedirs(os.path.dirname(audit_path), exist_ok=True)
        with open(audit_path, "w", encoding="utf-8") as f:
            json.dump(audit, f, ensure_ascii=False, indent=2)
    except Exception as e:
        # 审计失败不阻断主流程,但必须记录——干预无审计会破坏可审计链
        log.error("写入干预审计失败(agent={}): {}".format(name, e), exc_info=True)

    return JSONResponse({"ok": True, "name": name, "constraints": goals})


@app.post("/api/undo-intervention")
async def undo_intervention(request: Request):
    """撤销一次专家干预:把该角色约束回滚到该次干预的 old_constraints。

    IVD 语义:利益相关者可"修正自己之前的调整"——回滚同样走制度层
    (governance.json)+ 追加 operator=undo 审计记录(撤销本身可审计,
    不抹除历史:interventions.json 保留原记录,另记一条撤销)。
    匹配键:agent + sim_time + 记录写入真实时间(time),避免跨模拟/同名混淆。
    """
    body = await request.json()
    agent = str(body.get("agent", "")).strip()
    sim_time = str(body.get("sim_time", "")).strip()
    rec_time = str(body.get("time", "")).strip()  # 真实写入时间(秒级,区分同刻干预)
    if not agent or not sim_time or not rec_time:
        return JSONResponse({"ok": False, "errors": ["缺少 agent/sim_time/time"]})

    audit_path = state.checkpoint_file("interventions.json")
    if not os.path.exists(audit_path):
        return JSONResponse({"ok": False, "errors": ["interventions.json 不存在"]})
    try:
        audit = json.load(open(audit_path, encoding="utf-8"))
    except Exception as e:
        log.error("撤销读取 interventions.json 失败: {}".format(e), exc_info=True)
        return JSONResponse({"ok": False, "errors": ["读取干预记录失败: {}".format(e)]})

    # 定位目标记录:agent+sim_time+time 三键匹配(同角色同模拟时刻的多次干预靠 time 区分)
    cur_sim = state.current_sim_name()
    target_idx = None
    for i, iv in enumerate(audit):
        if (str(iv.get("agent", "")) == agent
                and str(iv.get("sim_time", "")) == sim_time
                and str(iv.get("time", "")) == rec_time
                and (not iv.get("simulation") or iv.get("simulation") == cur_sim)):
            target_idx = i
            break
    if target_idx is None:
        return JSONResponse({"ok": False, "errors": ["未找到匹配的干预记录(agent={} sim={} time={})".format(agent, sim_time, rec_time)]})
    target = audit[target_idx]
    # 已被撤销的记录不再重复撤销(原记录打 revoked 标记,undo 记录不参与匹配)
    if target.get("operator") == "undo" or target.get("revoked"):
        return JSONResponse({"ok": False, "errors": ["该干预已被撤销,不能重复撤销"]})

    old_constraints = dict(target.get("old_constraints") or {})
    if not old_constraints:
        return JSONResponse({"ok": False, "errors": ["该记录无 old_constraints,无法回滚"]})
    # 回滚目标必须仍存在于当前约束集;若当前治理已不包含这些目标(已被后续干预
    # 删除),回滚会导致维度错乱——此时拒绝并提示(保守策略,不做隐式合并)
    from mavisframework.runtime.governance import Governance

    gov_path = os.path.join(state.BASE_DIR, "governance.json")
    gov = Governance()
    if os.path.exists(gov_path):
        gov.load(gov_path)
    current = gov.get_constraints(agent)
    if not current:
        return JSONResponse({"ok": False, "errors": ["该角色当前无治理约束,无法回滚"]})
    # 回滚 = 整约束替换为该次干预前状态(与干预时 set_constraints 对称)
    rollback_goals = dict(old_constraints)
    # 归一化(old_constraints 理论 sum=1,防御旧数据)
    total = sum(float(v) for v in rollback_goals.values()) or 1.0
    rollback_goals = {g: float(v) / total for g, v in rollback_goals.items()}
    gov.set_constraints(agent, rollback_goals)

    # 同步运行中治理实例(否则后果反馈仍按旧约束,倾向不响应回滚)
    server = state.server
    if server is not None and getattr(server, "game", None) is not None:
        live_gov = getattr(server.game, "governance", None)
        if live_gov is not None:
            live_gov.data.setdefault("roles", {})[agent] = dict(rollback_goals)

    # 追加撤销审计(不删原记录——撤销本身入链,历史完整;原记录打 revoked 标记防重复撤销)
    try:
        import datetime as _dt2
        audit[target_idx]["revoked"] = True
        audit.append({
            "time": _dt2.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sim_time": sim_time,
            "simulation": cur_sim,
            "agent": agent,
            "old_constraints": current,          # 撤销前的当前约束
            "new_constraints": rollback_goals,   # 回滚到的状态
            "operator": "undo",
            "note": "撤销干预(回滚到 {} 干预前状态)".format(rec_time),
            "undo_of": {"time": target.get("time", ""), "sim_time": sim_time},
        })
        with open(audit_path, "w", encoding="utf-8") as f:
            json.dump(audit, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error("撤销审计写入失败(agent={}): {}".format(agent, e), exc_info=True)
        return JSONResponse({"ok": False, "errors": ["回滚成功但审计写入失败: {}".format(e)]})

    return JSONResponse({"ok": True, "agent": agent, "constraints": rollback_goals,
                         "rollback_to": old_constraints})


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
    # 衰减系数从引擎取(think.tendency_decay,缺省 0.8)——口径漂移会误导解释面板
    decay = float(getattr(live_agent, "_tendency_decay", 0.8) or 0.8)
    win_mean = {}
    if window:
        n = len(window)
        goals = set()
        for w in window:
            goals.update(w.get("feedback", w).keys())
        for g in goals:
            vals = [w.get("feedback", w).get(g, 0.0) for w in window]
            weights = [decay ** (n - 1 - i) for i in range(n)]
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
                key=lambda x: str(x.get("sim_time", "")),
            )
        except Exception as e:
            log.warning("timeline 读取 interventions.json 失败: {}".format(e))
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
        return JSONResponse({"ok": False, "errors": ["缺少角色名"]})

    ckpt_dir = state.current_ckpt_dir()
    cur_sim = state.current_sim_name()
    iv_path = state.checkpoint_file("interventions.json")
    ivs = state.read_json(iv_path, default=[]) or []

    png = render_tendency_png(ckpt_dir, agent,
                              constraints=None, my_ivs=ivs, cur_sim=cur_sim)
    if png is None:
        return JSONResponse({"ok": False, "errors": ["该角色暂无倾向数据(或 matplotlib 不可用)"]})

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
                log.warning("retrieve_thoughts 失败(agent={}): {}".format(name, e))
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

    文本随标记一并写档(记忆流只在运行期存在,标记即归档)。
    """
    body = await request.json()
    agent = str(body.get("agent", "")).strip()
    node_id = str(body.get("node_id", "")).strip()
    text = str(body.get("text", "")).strip()
    verdict = str(body.get("verdict", "")).strip()
    correction = str(body.get("correction", "") or "").strip()
    sim_time = str(body.get("sim_time", "")).strip() or state.current_sim_time("%Y%m%d-%H:%M")

    if not agent or not text:
        return JSONResponse({"ok": False, "errors": ["缺少 agent/text"]})
    if verdict not in VALID_VERDICTS:
        return JSONResponse({"ok": False,
                             "errors": ["verdict 必须是 correct/incorrect/partial 之一"]})
    if verdict in ("incorrect", "partial") and not correction:
        return JSONResponse({"ok": False, "errors": ["incorrect/partial 必须填写纠正文本"]})

    # 行为上下文:decisions.json 中该角色最后一条决策(行动/对齐度/倾向/角色/地点)
    context = {}
    ckpt_dir = state.current_ckpt_dir()
    if ckpt_dir and os.path.isdir(ckpt_dir):
        import glob as _g
        files = sorted(_g.glob(os.path.join(ckpt_dir, "simulate-*.json")))
        dec_path = os.path.join(ckpt_dir, "decisions.json")
        if files:
            try:
                snap = json.load(open(files[-1], encoding="utf-8"))
                ag = (snap.get("agents") or {}).get(agent)
                if ag:
                    st = ag.get("status") or {}
                    context["value_tendency"] = st.get("value_tendency") or {}
                    context["goal_alignment"] = st.get("goal_alignment") or {}
            except Exception:
                pass
    if ckpt_dir and os.path.exists(dec_path):
        try:
            dec = json.load(open(dec_path, encoding="utf-8"))
            evs = dec.get("events") or []
            for ev in reversed(evs):
                if ev.get("agent") == agent:
                    context["action"] = ev.get("action", "")
                    context["role"] = ev.get("role", "")
                    context["location"] = ev.get("location", "")
                    context["goal_score"] = ev.get("goal_score")
                    break
        except Exception:
            pass

    record = new_mark(agent=agent, simulation=state.current_sim_name(),
                      sim_time=sim_time, node_id=node_id, thought=text,
                      verdict=verdict, correction=correction, context=context)
    append_mark(record)
    out = rebuild_jsonl()
    return JSONResponse({"ok": True, "mark": record, "export": out})


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
<html lang="zh">
<head>
<meta charset="utf-8">
<title>反思标记 · 人机协同</title>
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
  <h1>反思标记 · 人机协同闭环</h1>
  <a href="/api/reflections/export.jsonl">导出 JSONL(LoRA 线)⇩</a>
</header>
<main>
  <h2>待标记反思</h2>
  <div id="pending"><div class="empty">加载中...</div></div>
  <h2>已标记</h2>
  <div id="marked"><div class="empty">暂无</div></div>
</main>
<script>
const VERDICTS = [["correct","✓ 正确"],["incorrect","✗ 错误"],["partial","◐ 部分正确"]];
let picks = {};

async function refresh() {
  const r = await fetch("/api/reflections");
  const d = await r.json();
  const box = document.getElementById("pending");
  if (!d.ok || !d.pending.length) { box.innerHTML = '<div class="empty">暂无待标记反思(模拟运行并产生反思后出现)</div>'; }
  else {
    box.innerHTML = "";
    for (const p of d.pending) {
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `<div class="meta"><b>${p.agent}</b> · ${p.sim_time} · P.${p.poignancy}</div>
        <div class="thought">${p.text}</div>
        <div class="actions">${VERDICTS.map(([v, t]) =>
          `<button class="${v === "correct" ? "ok" : v === "incorrect" ? "bad" : "part"}" data-v="${v}">${t}</button>`).join("")}</div>
        <textarea placeholder="纠正文本(错误/部分正确时必填)..." style="display:none"></textarea>
        <div class="actions"><button data-v="__submit" style="display:none;background:#2d6cdf;color:#fff;">提交标记</button></div>`;
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
  if (!d.marked.length) { mbox.innerHTML = '<div class="empty">暂无已标记</div>'; }
  else {
    mbox.innerHTML = "";
    for (const m of d.marked.slice().reverse()) {
      const card = document.createElement("div");
      card.className = "card marked";
      card.innerHTML = `<div class="meta"><b>${m.agent}</b> · ${m.sim_time} · <span class="badge ${m.verdict}">${m.verdict}</span></div>
        <div class="thought">${m.thought}</div>` +
        (m.correction ? `<div class="corr">纠正: ${m.correction}</div>` : "");
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
  if (r.ok) { await refresh(); } else { alert("标记失败: " + (await r.text())); }
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
    except Exception:
        pass
    finally:
        stop_hb.set()
        hb_task.cancel()
        manager.unregister(ws)
