# -*- coding: utf-8 -*-
"""干预策略注册表(InterventionStrategy)——与 case_engine/engines.py 同款的扩展模式。

为什么要有这一层(2026-09-27,用户拍板"做好易扩展,让程序员自主加入新干预"):
- 此前三种干预(调权重/撤销/反思标记)硬编码在 live/routes.py 的三个端点里,
  加第四种干预 = 改服务代码、加路由 —— 与"新增引擎 = 写类 + register,零改动
  消费端"的引擎层扩展性不对等;
- 现在统一为:每种干预是一个 InterventionStrategy,经 `register()` 挂进注册表;
  HTTP 层只剩一个统一分发口 `POST /api/intervention/{strategy_id}`(旧路径保留为
  薄壳,前端与就绪探针零改动)。新增干预 = 写一个类 + 一行 register。

契约(镜像 EngineStrategy 的 supports/describe/run):
- `describe()`  → 元信息(name/scope/契约字段说明),供 GET /api/interventions 枚举;
- `apply(ctx, payload) -> dict | InterventionResult` → 完整处理(含字段校验:
  校验错误沿用历史语义返回 `{"ok": False, "errors": [...]}` + HTTP 200;
  非 200 状态码用 `InterventionResult` 表达)。

ctx(`InterventionContext`)由 HTTP 层每请求构建:策略不 import live.state,
测试用假 ctx 即可覆盖(不需要 monkeypatch 模块全局)。

IVD 语义边界(哪些算"干预"):
- 权重调整(制度层)/ 纠正回流(内容层)是专家驱动的干预;
- 后果反馈(行为层)是自动运行,不是干预;审计层记录一切干预。
"""
import json
import math
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from fastapi.responses import JSONResponse

from live import state
from live.state import write_json_atomic


# ---------------------------------------------------------------------------
# 上下文与结果
# ---------------------------------------------------------------------------

@dataclass
class InterventionContext:
    """一次干预调用可用的运行时上下文(HTTP 层构建;测试可伪造)。

    server   : live.state.server(运行中的模拟;未运行时 None)
    base_dir : provenance 包根(governance.json / results/ 的锚点)
    ckpt_dir : 当前模拟的 checkpoint 目录(未运行时为 "")
    sim_name : 当前模拟名(未运行时为 "")
    sim_time : 当前模拟时间 "%Y%m%d-%H:%M"(未运行时为 "")
    """
    server: Any = None
    base_dir: str = ""
    ckpt_dir: str = ""
    sim_name: str = ""
    sim_time: str = ""
    supports_governance: bool = True


class InterventionResult(dict):
    """非 200 响应(如 undo 的沙盒 403)。

    继承 dict:库调用方(绕过 HTTP 直调策略)对返回值 .get() 不崩;
    HTTP 层用 isinstance 识别并以 status 包装 JSONResponse。
    """
    status: int = 200

    def __init__(self, body: dict, status: int = 200):
        super().__init__(body)
        self.status = status


def _agents_of(ctx: InterventionContext) -> Dict[str, Any]:
    """运行中的 agents(未运行返回空 dict —— 各策略自行决定是否容忍)。"""
    server = ctx.server
    if server is not None and getattr(server, "game", None) is not None:
        return dict(getattr(server.game, "agents", {}) or {})
    return {}


def _live_governance_of(ctx: InterventionContext):
    """运行中的 Governance 实例(agent._governance 指向的同一对象);未运行返回 None。"""
    server = ctx.server
    if server is not None and getattr(server, "game", None) is not None:
        return getattr(server.game, "governance", None)
    return None


def _governance_unsupported(ctx: InterventionContext):
    """本面引擎压根不挂治理时的拒绝(返回 None = 可以继续)。

    为什么不能只靠 `_live_governance_of`:case01 的面不填 `live.state.server`
    (那是 case00 的注入点),所以取到 None 有两种意思——"没在跑"和"这一局没有
    治理实例",而前者在 case00 是合法的(专家可以开跑前预设权重)。得由挂面的进程
    自己声明。不声明的后果实测过:goals 返回 ok:true、把角色名写进
    governance.json(仓根、git 追踪),而这一局行为一点都不变。
    """
    if ctx.supports_governance:
        return None
    return InterventionResult({"ok": False, "errors": [
        "this face's engine carries no governance constraints (case01 does Game(governance=None)): "
        "writing governance.json would be read by nobody, so the intervention is refused rather than reported as success."]},
        status=409)


def _audit_path(ctx: InterventionContext) -> str:
    """干预台账(跨局登记簿)的绝对路径。

    2026-10-08:台账的家从 `results/checkpoints/` 挪到 `data/ledgers/`。
    但 `ctx.base_dir` 是**测试与外部调用可以覆盖的根**(多处测试把 ctx 指到 tmp),
    所以只在"ctx 指着真仓"时才走新解析器。

    ⚠ 判据必须拿**物理常量**(`case_engine.paths.PKG_ROOT/REPO_ROOT`)比,
    **不能拿 `state.BASE_DIR`** —— 那个变量本身就会被测试 patch 到 tmp,用它比等于
    "测试里也判成真仓",于是台账被写到真仓老位置、测试看不到自己 seed 的数据
    (2026-10-08 实测:test_live_api 的 undo/timeline 一整套因此红)。
    """
    from case_engine.paths import PKG_ROOT, REPO_ROOT, data_root
    real = os.path.realpath(ctx.base_dir)
    if real in (os.path.realpath(PKG_ROOT), os.path.realpath(REPO_ROOT)):
        return os.path.join(data_root("ledgers"), "interventions.json")
    return os.path.join(ctx.base_dir, "results", "checkpoints", "interventions.json")


_AUDIT_LOCK = threading.Lock()   # 串行化读-改-写:两专家同时干预不丢审计记录


def _append_audit(ctx: InterventionContext, record: dict) -> None:
    """向 interventions.json 追加一条审计(加锁 + 读-改-原子写)。

    与 live/reflections.append_mark 的 _MARKS_LOCK 同一标准:原子写只保证
    "文件不是半截",不保证并发追加不丢行 —— 丢行要靠锁。
    """
    path = _audit_path(ctx)
    with _AUDIT_LOCK:
        audit = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                audit = json.load(f)
        audit.append(record)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_json_atomic(path, audit)


# ---------------------------------------------------------------------------
# 注册表(镜像 case_engine/engines.py:双表 + register + 枚举访问器)
# ---------------------------------------------------------------------------

STRATEGIES: Dict[str, Dict[str, Any]] = {}       # id -> describe 元信息
_INTERVENTIONS: Dict[str, type] = {}             # id -> 策略类


def register(strategy_id: str, cls: type, meta: Dict[str, Any]) -> None:
    """注册一个干预策略。重复 id / 非法 id 直接抛错(与引擎注册表同口径)。"""
    if not strategy_id or not all(c.isalnum() or c in "-_" for c in strategy_id):
        raise ValueError("invalid intervention strategy id: {!r}".format(strategy_id))
    if strategy_id in STRATEGIES:
        raise ValueError("intervention strategy already registered: {!r}".format(strategy_id))
    STRATEGIES[strategy_id] = dict(meta or {})
    _INTERVENTIONS[strategy_id] = cls


def known(strategy_id: str) -> bool:
    return strategy_id in STRATEGIES


def all_ids() -> List[str]:
    return sorted(STRATEGIES)


def describe(strategy_id: str) -> Dict[str, Any]:
    if strategy_id not in STRATEGIES:
        raise KeyError(strategy_id)
    return {"strategy": strategy_id, **STRATEGIES[strategy_id]}


def get(strategy_id: str) -> type:
    if strategy_id not in _INTERVENTIONS:
        raise KeyError(strategy_id)
    return _INTERVENTIONS[strategy_id]


class InterventionStrategy:
    """干预策略基类:子类实现 describe/_apply,类属性给 id 与显示名。

    `apply` 是对外入口(注册表/HTTP/库调用共用):在委托给子类 `_apply` 之前
    统一做 payload 归一(非 dict → {})—— 策略作为库被直接调用时(绕过 HTTP 层
    的 _json_body)不会因畸形 payload 裸崩 AttributeError。
    """
    strategy_id: str = ""
    name: str = ""

    def describe(self) -> Dict[str, Any]:
        from live.interventions import describe as _registry_describe
        return _registry_describe(self.strategy_id)

    def apply(self, ctx: InterventionContext, payload: Any) -> Any:
        if not isinstance(payload, dict):
            payload = {}
        return self._apply(ctx, payload)

    def _apply(self, ctx: InterventionContext, payload: dict) -> Any:
        raise NotImplementedError("intervention strategy {!r} does not implement _apply".format(
            self.strategy_id))


# ---------------------------------------------------------------------------
# 内置策略 1:goals —— 调整治理约束权重(制度层)
# ---------------------------------------------------------------------------

class WeightAdjustStrategy(InterventionStrategy):
    """专家设定期望目标权重(governance.json 制度层 + 运行中实例内存同步)。"""

    strategy_id = "goals"
    name = "adjust governance constraint weights"

    def _apply(self, ctx: InterventionContext, payload: dict) -> dict:
        blocked = _governance_unsupported(ctx)
        if blocked is not None:
            return blocked
        name = str(payload.get("name", "")).strip()
        goals = payload.get("goals")
        if not name:
            return {"ok": False, "errors": ["the agent name is missing"]}
        if "/" in name or "\\" in name or ".." in name:
            return {"ok": False, "errors": ["invalid agent name (contains path separators): {!r}".format(name)]}
        if not isinstance(goals, dict) or not goals:
            return {"ok": False, "errors": ["constraints must be a non-empty dict (goal: weight)"]}
        # 角色名必须是这一局真实存在的角色:以前 {"name": "查无此人"} 会静默写进
        # governance.json,多出一个谁也用不到的角色。拿不到清单时(没在跑)不拦。
        known_roles = list(_agents_of(ctx).keys())
        if known_roles and name not in known_roles:
            return {"ok": False, "errors": [
                "no such agent in this run: {!r}; agents in this run: {}".format(
                    name, " / ".join(sorted(known_roles)))]}
        # 清洗:拒绝数字开头目标名(误输入)与 0 权重项(前端拖动产生的垃圾)
        import re as _re
        cleaned = {}
        for g, v in goals.items():
            gs = str(g).strip()
            if not gs or _re.match(r"^\d", gs):
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            # NaN/Infinity 必须挡住:NaN 能骗过总和=1 的校验,然后写进
            # governance.json 落成非标准字面量,序列化响应时 500。
            if not math.isfinite(fv):
                return {"ok": False, "errors": [
                    "weights must be finite numbers: goal {!r} received {}".format(gs, v)]}
            if fv <= 0:
                continue
            cleaned[gs] = fv
        if not cleaned:
            return {"ok": False, "errors": ["nothing is left after cleaning (numeric or 0-weight goals are rejected)"]}
        goals = cleaned
        try:
            total = sum(float(v) for v in goals.values())
        except (TypeError, ValueError):
            return {"ok": False, "errors": ["constraint weight values must all be numbers"]}
        if abs(total - 1.0) > 1e-6:
            return {"ok": False, "errors": [
                "constraint weights must sum to 1, got {}".format(round(total, 4))]}

        # 1) 写 governance.json(制度层,非 AI 本体)
        from mavisframework.runtime.governance import Governance
        gov_path = os.path.join(ctx.base_dir, "governance.json")
        # 带路径构造:文件不存在时(全新部署)set_constraints 也能正确落盘,
        # 不会因"跳过 load → path 未指定"而 500(2026-09-27 冒烟实测)
        gov = Governance(gov_path)
        old = gov.get_constraints(name)
        gov.set_constraints(name, goals)  # set_constraints 内已 save

        # 1.5) 同步运行中治理实例:agent._governance 指向 game.governance 同一
        #      对象,不更新内存则 consequence.feedback 仍按旧约束计算,
        #      干预只改了文件不改内存 → 倾向曲线"不动"、内化失效。
        live_gov = _live_governance_of(ctx)
        if live_gov is not None:
            live_gov.data.setdefault("roles", {})[name] = dict(goals)

        # 2) 记录干预审计(可审计链)。失败不阻断主流程,但必须 log(不静默)。
        try:
            import datetime
            _append_audit(ctx, {
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "sim_time": ctx.sim_time,
                "simulation": ctx.sim_name,   # 干预归属的模拟(跨模拟隔离)
                "agent": name,
                "old_constraints": old,
                "new_constraints": goals,
                "operator": "expert",
                "note": str(payload.get("note", "") or "").strip(),
                "intervention": self.strategy_id,   # 干预模态标记(模态对比研究用)
            })
        except Exception as e:
            from live.state import log
            log.error("failed to write the intervention audit (agent={}): {}".format(name, e), exc_info=True)

        return {"ok": True, "name": name, "constraints": goals}


# ---------------------------------------------------------------------------
# 内置策略 2:undo —— 撤销一次权重干预(回滚到 old_constraints)
# ---------------------------------------------------------------------------

def _case00_engine_id(base_dir: str) -> str:
    """case00 场景声明的引擎 id(读不到就按 sandbox-value 处理)。"""
    try:
        from case_engine.config import load_yaml

        p = os.path.join(base_dir, "cases", "case00_village", "scenario.yaml")
        if os.path.isfile(p):
            return getattr(load_yaml(p), "engine", None) or "sandbox-value"
    except Exception:  # noqa: BLE001 —— 回退必须留痕:场景坏了没人知道就等于没坏
        from live.state import log
        log.warning("[embed] failed to read case00_village/scenario.yaml; treating it as sandbox-value",
                    exc_info=True)
    return "sandbox-value"


def sandbox_rollback_blocked(base_dir: str = "") -> bool:
    """**沙盒场景不允许时间轴回滚**(2026-09-21 用户要求)。

    沙盒跑的正是"决策→后果→反思→内化":后果一旦发生就成了经历,回滚等于把
    后果从经历里抹掉,与这条线的前提直接冲突。前端也不渲染撤销入口。
    """
    from live.state import BASE_DIR
    return _case00_engine_id(base_dir or BASE_DIR) == "sandbox-value"


class UndoInterventionStrategy(InterventionStrategy):
    """撤销一次专家干预:约束回滚到该次干预的 old_constraints。"""

    strategy_id = "undo"
    name = "undo an intervention (weight rollback)"

    def _apply(self, ctx: InterventionContext, payload: dict) -> Any:
        blocked = _governance_unsupported(ctx)
        if blocked is not None:
            return blocked
        if sandbox_rollback_blocked(ctx.base_dir):
            return InterventionResult({"ok": False, "errors": [
                "the sandbox scenario offers no timeline rollback: an intervention's consequences belong to the agent's experience,"
                " so rolling back would erase them (decision -> consequence -> reflection -> internalization cannot be rewound). To correct course, intervene again from the governance panel."
            ]}, status=403)
        agent = str(payload.get("agent", "")).strip()
        sim_time = str(payload.get("sim_time", "")).strip()
        rec_time = str(payload.get("time", "")).strip()  # 真实写入时间(区分同刻干预)
        if not agent or not sim_time or not rec_time:
            return {"ok": False, "errors": ["agent/sim_time/time are missing"]}

        audit_path = _audit_path(ctx)
        if not os.path.exists(audit_path):
            return {"ok": False, "errors": ["interventions.json does not exist"]}
        try:
            audit = json.load(open(audit_path, encoding="utf-8"))
        except Exception as e:
            from live.state import log
            log.error("undo: failed to read interventions.json: {}".format(e), exc_info=True)
            return {"ok": False, "errors": ["failed to read the intervention records: {}".format(e)]}

        # 定位目标记录:agent+sim_time+time 三键匹配(同刻多次干预靠 time 区分)
        cur_sim = ctx.sim_name
        target_idx = None
        for i, iv in enumerate(audit):
            if (str(iv.get("agent", "")) == agent
                    and str(iv.get("sim_time", "")) == sim_time
                    and str(iv.get("time", "")) == rec_time
                    and (not iv.get("simulation") or iv.get("simulation") == cur_sim)):
                target_idx = i
                break
        if target_idx is None:
            return {"ok": False, "errors": [
                "no matching intervention record (agent={} sim={} time={})".format(
                    agent, sim_time, rec_time)]}
        target = audit[target_idx]
        if target.get("operator") == "undo" or target.get("revoked"):
            return {"ok": False, "errors": ["this intervention has already been undone; it cannot be undone twice"]}

        old_constraints = dict(target.get("old_constraints") or {})
        if not old_constraints:
            return {"ok": False, "errors": ["the record has no old_constraints, so it cannot be rolled back"]}
        # 回滚目标必须仍在当前约束集;已被后续干预删除时拒绝(保守,不做隐式合并)
        from mavisframework.runtime.governance import Governance

        gov_path = os.path.join(ctx.base_dir, "governance.json")
        gov = Governance(gov_path)   # 带路径构造(同 goals:修文件缺失边界)
        current = gov.get_constraints(agent)
        if not current:
            return {"ok": False, "errors": ["this agent currently has no governance constraints, so there is nothing to roll back"]}
        rollback_goals = dict(old_constraints)
        # 归一化(old_constraints 理论 sum=1,防御旧数据)
        total = sum(float(v) for v in rollback_goals.values()) or 1.0
        rollback_goals = {g: float(v) / total for g, v in rollback_goals.items()}
        gov.set_constraints(agent, rollback_goals)

        # 同步运行中治理实例(否则后果反馈仍按旧约束,倾向不响应回滚)
        live_gov = _live_governance_of(ctx)
        if live_gov is not None:
            live_gov.data.setdefault("roles", {})[agent] = dict(rollback_goals)

        # 追加撤销审计(不删原记录;原记录打 revoked 防重复撤销)。原子写与
        # goals 同标准(2026-09-25 体检发现此处曾是裸 open("w"))。
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
                "note": "undo intervention (rolled back to the pre-intervention state of {})".format(rec_time),
                "undo_of": {"time": target.get("time", ""), "sim_time": sim_time},
                "intervention": self.strategy_id,
            })
            write_json_atomic(audit_path, audit)
        except Exception as e:
            from live.state import log
            log.error("undo: failed to write the audit (agent={}): {}".format(agent, e), exc_info=True)
            return {"ok": False, "errors": ["the rollback succeeded but the audit write failed: {}".format(e)]}

        return {"ok": True, "agent": agent, "constraints": rollback_goals,
                "rollback_to": old_constraints}


# ---------------------------------------------------------------------------
# 内置策略 3:mark —— 反思标记(case01 专家链,LoRA 数据源)
# ---------------------------------------------------------------------------

class ReflectionMarkStrategy(InterventionStrategy):
    """专家标记一条反思:verdict ∈ correct/incorrect/partial(+纠正文本)。"""

    strategy_id = "mark"
    name = "mark a reflection (expert review)"

    def _apply(self, ctx: InterventionContext, payload: dict) -> dict:
        from live.reflections import (append_mark, mark_gate_errors, new_mark,
                                      rebuild_jsonl)

        agent = str(payload.get("agent", "")).strip()
        node_id = str(payload.get("node_id", "")).strip()
        text = str(payload.get("text", "")).strip()
        verdict = str(payload.get("verdict", "")).strip()
        correction = str(payload.get("correction", "") or "").strip()
        sim_time = str(payload.get("sim_time", "")).strip() or ctx.sim_time
        # 判定门与审核面板共用一处实现(live/reflections.mark_gate_errors),
        # 免得两个口对同一个 verdict 给出不同结论。
        errs = mark_gate_errors(agent, text, verdict, correction)
        if errs:
            return {"ok": False, "errors": errs}
        if len(text) > 200_000:
            return {"ok": False, "errors": ["text is too long (>200k characters), which looks like abuse"]}
        # 行为上下文:最近快照的倾向/对齐 + decisions.json 最后一条决策
        context = {}
        ckpt_dir = ctx.ckpt_dir
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
                except Exception as exc:  # noqa: BLE001 - 取不到留空但留痕(不静默)
                    from live.state import log
                    log.warning("[reflections] failed to read the checkpoint snapshot; tendency/alignment left blank: %s", exc)
            if os.path.exists(dec_path):
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
                except Exception as exc:  # noqa: BLE001 - 取不到留空但留痕
                    from live.state import log
                    log.warning("[reflections] failed to read decisions.json; action left blank: %s", exc)

        record = new_mark(agent=agent, simulation=ctx.sim_name,
                          sim_time=sim_time, node_id=node_id, thought=text,
                          verdict=verdict, correction=correction, context=context)
        append_mark(record)
        out = rebuild_jsonl()
        return {"ok": True, "mark": record, "export": out}


# ---------------------------------------------------------------------------
# 内置策略 4:corrective_feedback —— 纠正回流(内容层,扩展性证明)
# ---------------------------------------------------------------------------

class CorrectiveFeedbackStrategy(InterventionStrategy):
    """专家纠正文本回流:把纠正作为一条**体验**写进该 agent 的记忆流。

    IVD 语义:权重回答"往哪偏",纠正回答"具体怎么偏"——《治理手段的边界与
    扩展路径》的内容层杠杆。**只进记忆,不直接改 value_tendency**:直接篡改
    倾向会破坏"程序定事实"的因果链;纠正应作为可被反思检索的经历存在,
    经 observe_consequence 的正常管线起作用。
    """

    strategy_id = "corrective_feedback"
    name = "corrective feedback reflow (written into the agent's memory stream)"

    def _apply(self, ctx: InterventionContext, payload: dict) -> dict:
        agent_name = str(payload.get("agent", "")).strip()
        correction = str(payload.get("correction", "") or "").strip()
        note = str(payload.get("note", "") or "").strip()
        if not agent_name:
            return {"ok": False, "errors": ["agent is missing"]}
        if not correction:
            return {"ok": False, "errors": ["the correction text is missing"]}
        agents = _agents_of(ctx)
        if not agents:
            return {"ok": False, "errors": ["no simulation is running, so the correction cannot be reflowed"]}
        if agent_name not in agents:
            return {"ok": False, "errors": [
                "no such agent in this run: {!r}; agents in this run: {}".format(
                    agent_name, " / ".join(sorted(agents)))]}
        agent = agents[agent_name]
        inject = getattr(agent, "inject_story_event", None)
        if inject is None:
            return {"ok": False, "errors": [
                "this agent does not support memory injection (inject_story_event is missing)"]}

        import datetime
        ev = {
            "id": "corrective-{}".format(
                datetime.datetime.now().strftime("%Y%m%d-%H%M%S")),
            "event_type": "专家纠正",
            "content": correction,
            "importance": 10,      # 业务决策的权重:纠正必须可被反思检索
        }
        inject(ev)

        # 审计(与权重干预同一审计链;无 old/new constraints —— 分析工具
        # (internalization_metrics/behavior_follow)对无约束字段记录天然跳过)
        try:
            _append_audit(ctx, {
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "sim_time": ctx.sim_time,
                "simulation": ctx.sim_name,
                "agent": agent_name,
                "operator": "expert",
                "note": note or "correction reflow: {}".format(correction[:60]),
                "correction": correction,
                "intervention": self.strategy_id,
            })
        except Exception as e:
            from live.state import log
            log.error("correction reflow: failed to write the audit (agent={}): {}".format(agent_name, e),
                      exc_info=True)
        return {"ok": True, "agent": agent_name,
                "injected": True, "correction": correction}


# ---------------------------------------------------------------------------
# 内置注册(import 时自挂;镜像 engines._register_builtin)
# ---------------------------------------------------------------------------

def _register_builtin() -> None:
    register("goals", WeightAdjustStrategy, {
        "name": WeightAdjustStrategy.name,
        "scope": "institution layer",
        "payload": {"name": "agent name", "goals": "{goal: weight} summing to 1", "note": "reason for the intervention (optional)"},
        "desc": "adjust governance constraint weights: reweights the consequence feedback, and the tendency converges with a lag (the IVD main lever)",
    })
    register("undo", UndoInterventionStrategy, {
        "name": UndoInterventionStrategy.name,
        "scope": "institution layer",
        "payload": {"agent": "agent name", "sim_time": "sim time of the intervention", "time": "real write time of the intervention"},
        "desc": "undo one weight intervention: rolls back to old_constraints (always 403 in the sandbox scenario)",
    })
    register("mark", ReflectionMarkStrategy, {
        "name": ReflectionMarkStrategy.name,
        "scope": "case01 expert chain",
        "payload": {"agent": "agent name", "node_id": "reflection node", "text": "the reflection text",
                    "verdict": "correct|incorrect|partial", "correction": "correction text",
                    "sim_time": "sim time (optional)"},
        "desc": "record the quality verdict of a reflection; the correction text goes into the LoRA training data (SFT/DPO)",
    })
    register("corrective_feedback", CorrectiveFeedbackStrategy, {
        "name": CorrectiveFeedbackStrategy.name,
        "scope": "content layer",
        "payload": {"agent": "agent name", "correction": "correction text", "note": "reason (optional)"},
        "desc": "write the expert correction into the agent's memory stream (retrievable by reflection); it does not change the tendency and works through the normal experience pipeline",
    })


_register_builtin()


# ---------------------------------------------------------------------------
# HTTP 路由(共享 router:case00 面 live/routes.py 与 case01 面 build_service
# 都挂载——2026-09-27 体检发现 case01 面缺全部干预端点,平台侧 M4 的反思标记
# 会 404。路径与旧端点一致,两面行为相同。)
# ---------------------------------------------------------------------------
from fastapi import APIRouter, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

router = APIRouter()


def _http_ctx(app) -> InterventionContext:
    """从 live.state + 挂了这个 router 的那个 app 构建上下文。

    `supports_governance` 取自 **app.state** 而不是 live.state 的全局:两面在测试里
    会建在同一进程内,做成模块全局就会互相污染(case01 面一建,后面的 case00 用例
    全部被判成"不挂治理")。生产里两 case 互斥共用 5010,但按进程声明本来就是错的粒度。

    `server`/`sim_name` 同样按面取:全局 `state.server` 与 `state.sim_state["name"]`
    只有 case00 的 live_fastapi 会注入,而 case01 的小镇面把这一局的引擎握在
    MavisBridge 里(全局从来不留)。此前不声明的后果实测过:那面的反思标记照样
    `ok:true` 落盘,但 `simulation` 与 `sim_time` 恒为空串 —— 标记归不到任何一条
    记录,M4 的"哪一局、哪个时刻"就查不回来了(而 `corrective_feedback` 只会说
    "当前没有运行中的模拟",对着动个不停的小镇像坏了)。
    """
    from live import state as _state

    engine = getattr(app.state, "intervention_engine", None) or _state.server
    try:
        ckpt_dir = _state.current_ckpt_dir()
    except Exception:  # noqa: BLE001 —— 未运行/压缩器缺失:ckpt 留空
        ckpt_dir = ""
    return InterventionContext(
        server=engine,
        base_dir=_state.BASE_DIR,
        ckpt_dir=ckpt_dir or "",
        sim_name=(_state.current_sim_name()
                  or getattr(app.state, "intervention_sim_name", "") or ""),
        sim_time=_state.current_sim_time("%Y%m%d-%H:%M", engine=engine),
        supports_governance=getattr(app.state, "engine_has_governance", True),
    )


async def _read_body(request: Request):
    """JSON-only 守卫(与 live/routes._json_body 同语义)。"""
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype and ctype != "application/json":
        return None, JSONResponse(
            {"ok": False,
             "errors": ["Content-Type: application/json is required (form submissions are not accepted)"]},
            status_code=415)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 —— 没有 body 是正常的;坏 body 走字段校验
        return {}, None
    return (body if isinstance(body, dict) else {}), None


def _wrap(strategy_id: str, res) -> JSONResponse:
    if res is None:
        from live.state import log
        log.error("intervention strategy {!r} returned None (a missing return in the strategy?)".format(strategy_id))
        return JSONResponse({"ok": False,
                             "errors": ["intervention strategy {!r} returned an empty result (implementation defect)".format(
                                 strategy_id)]}, status_code=500)
    if isinstance(res, InterventionResult):
        # dict(res):InterventionResult 是 dict 子类,body 存在字典本体里(不是 .body
        # 属性)—— 2026-09-27 体检:此前误用 res.body → AttributeError,catch 到
        # case01 面(挂共享 router)的沙盒 undo 由 403 变 500。与 routes.py 同口径。
        return JSONResponse(dict(res), status_code=res.status)
    return JSONResponse(res)


@router.post("/api/intervention/{strategy_id}")
async def dispatch(strategy_id: str, request: Request):
    body, err = await _read_body(request)
    if err is not None:
        return err
    if not known(strategy_id):
        return JSONResponse(
            {"ok": False,
             "errors": ["unknown intervention strategy: {!r}; registered: {}".format(
                 strategy_id, " / ".join(all_ids()))]}, status_code=404)
    res = get(strategy_id)().apply(_http_ctx(request.app), body)
    return _wrap(strategy_id, res)


@router.get("/api/interventions")
async def listing():
    return {"ok": True, "count": len(all_ids()),
            "strategies": [describe(sid) for sid in all_ids()]}


@router.post("/api/goals")
async def goals_alias(request: Request):
    body, err = await _read_body(request)
    if err is not None:
        return err
    return _wrap("goals", get("goals")().apply(_http_ctx(request.app), body))


@router.post("/api/undo-intervention")
async def undo_alias(request: Request):
    body, err = await _read_body(request)
    if err is not None:
        return err
    return _wrap("undo", get("undo")().apply(_http_ctx(request.app), body))


@router.post("/api/reflections/mark")
async def mark_alias(request: Request):
    body, err = await _read_body(request)
    if err is not None:
        return err
    return _wrap("mark", get("mark")().apply(_http_ctx(request.app), body))
