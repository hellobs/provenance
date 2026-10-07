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
        "本面引擎不挂治理约束(如 case01:Game(governance=None)),"
        "写了 governance.json 也没有实例读它 ⇒ 拒绝执行,不返回成功假象。"]},
        status=409)


def _audit_path(ctx: InterventionContext) -> str:
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
        raise ValueError("非法干预策略 id: {!r}".format(strategy_id))
    if strategy_id in STRATEGIES:
        raise ValueError("干预策略已注册: {!r}".format(strategy_id))
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
        raise NotImplementedError("干预策略 {!r} 未实现 _apply".format(
            self.strategy_id))


# ---------------------------------------------------------------------------
# 内置策略 1:goals —— 调整治理约束权重(制度层)
# ---------------------------------------------------------------------------

class WeightAdjustStrategy(InterventionStrategy):
    """专家设定期望目标权重(governance.json 制度层 + 运行中实例内存同步)。"""

    strategy_id = "goals"
    name = "调整治理约束权重"

    def _apply(self, ctx: InterventionContext, payload: dict) -> dict:
        blocked = _governance_unsupported(ctx)
        if blocked is not None:
            return blocked
        name = str(payload.get("name", "")).strip()
        goals = payload.get("goals")
        if not name:
            return {"ok": False, "errors": ["缺少角色名"]}
        if "/" in name or "\\" in name or ".." in name:
            return {"ok": False, "errors": ["非法角色名(含路径分隔符): {!r}".format(name)]}
        if not isinstance(goals, dict) or not goals:
            return {"ok": False, "errors": ["约束应为非空 dict(目标:权重)"]}
        # 角色名必须是这一局真实存在的角色:以前 {"name": "查无此人"} 会静默写进
        # governance.json,多出一个谁也用不到的角色。拿不到清单时(没在跑)不拦。
        known_roles = list(_agents_of(ctx).keys())
        if known_roles and name not in known_roles:
            return {"ok": False, "errors": [
                "没有这个角色: {!r};本局角色: {}".format(
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
                    "权重必须是有限数:目标 {!r} 收到 {}".format(gs, v)]}
            if fv <= 0:
                continue
            cleaned[gs] = fv
        if not cleaned:
            return {"ok": False, "errors": ["清洗后无有效目标(拒绝数字/0权重项)"]}
        goals = cleaned
        try:
            total = sum(float(v) for v in goals.values())
        except (TypeError, ValueError):
            return {"ok": False, "errors": ["约束权重值必须都是数字"]}
        if abs(total - 1.0) > 1e-6:
            return {"ok": False, "errors": [
                "约束权重总和应为 1,得到 {}".format(round(total, 4))]}

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
            log.error("写入干预审计失败(agent={}): {}".format(name, e), exc_info=True)

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
        log.warning("[embed] case00_village/scenario.yaml 读取失败,按 sandbox-value 处理",
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
    name = "撤销干预(权重回滚)"

    def _apply(self, ctx: InterventionContext, payload: dict) -> Any:
        blocked = _governance_unsupported(ctx)
        if blocked is not None:
            return blocked
        if sandbox_rollback_blocked(ctx.base_dir):
            return InterventionResult({"ok": False, "errors": [
                "沙盒场景不提供时间轴回滚:干预的后果属于角色的经历,回滚会把它抹掉"
                "(决策→后果→反思→内化这条线不允许倒带)。如确需修正,请在治理面板重新干预。"
            ]}, status=403)
        agent = str(payload.get("agent", "")).strip()
        sim_time = str(payload.get("sim_time", "")).strip()
        rec_time = str(payload.get("time", "")).strip()  # 真实写入时间(区分同刻干预)
        if not agent or not sim_time or not rec_time:
            return {"ok": False, "errors": ["缺少 agent/sim_time/time"]}

        audit_path = _audit_path(ctx)
        if not os.path.exists(audit_path):
            return {"ok": False, "errors": ["interventions.json 不存在"]}
        try:
            audit = json.load(open(audit_path, encoding="utf-8"))
        except Exception as e:
            from live.state import log
            log.error("撤销读取 interventions.json 失败: {}".format(e), exc_info=True)
            return {"ok": False, "errors": ["读取干预记录失败: {}".format(e)]}

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
                "未找到匹配的干预记录(agent={} sim={} time={})".format(
                    agent, sim_time, rec_time)]}
        target = audit[target_idx]
        if target.get("operator") == "undo" or target.get("revoked"):
            return {"ok": False, "errors": ["该干预已被撤销,不能重复撤销"]}

        old_constraints = dict(target.get("old_constraints") or {})
        if not old_constraints:
            return {"ok": False, "errors": ["该记录无 old_constraints,无法回滚"]}
        # 回滚目标必须仍在当前约束集;已被后续干预删除时拒绝(保守,不做隐式合并)
        from mavisframework.runtime.governance import Governance

        gov_path = os.path.join(ctx.base_dir, "governance.json")
        gov = Governance(gov_path)   # 带路径构造(同 goals:修文件缺失边界)
        current = gov.get_constraints(agent)
        if not current:
            return {"ok": False, "errors": ["该角色当前无治理约束,无法回滚"]}
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
                "note": "撤销干预(回滚到 {} 干预前状态)".format(rec_time),
                "undo_of": {"time": target.get("time", ""), "sim_time": sim_time},
                "intervention": self.strategy_id,
            })
            write_json_atomic(audit_path, audit)
        except Exception as e:
            from live.state import log
            log.error("撤销审计写入失败(agent={}): {}".format(agent, e), exc_info=True)
            return {"ok": False, "errors": ["回滚成功但审计写入失败: {}".format(e)]}

        return {"ok": True, "agent": agent, "constraints": rollback_goals,
                "rollback_to": old_constraints}


# ---------------------------------------------------------------------------
# 内置策略 3:mark —— 反思标记(case01 专家链,LoRA 数据源)
# ---------------------------------------------------------------------------

class ReflectionMarkStrategy(InterventionStrategy):
    """专家标记一条反思:verdict ∈ correct/incorrect/partial(+纠正文本)。"""

    strategy_id = "mark"
    name = "反思标记(专家审核)"

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
            return {"ok": False, "errors": ["text 超长(>200k 字符),疑似滥用"]}
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
                    log.warning("[reflections] 读 checkpoint 快照失败,倾向/对齐留空: %s", exc)
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
                    log.warning("[reflections] 读 decisions.json 失败,action 留空: %s", exc)

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
    name = "纠正回流(写入 agent 记忆流)"

    def _apply(self, ctx: InterventionContext, payload: dict) -> dict:
        agent_name = str(payload.get("agent", "")).strip()
        correction = str(payload.get("correction", "") or "").strip()
        note = str(payload.get("note", "") or "").strip()
        if not agent_name:
            return {"ok": False, "errors": ["缺少 agent"]}
        if not correction:
            return {"ok": False, "errors": ["缺少 correction(纠正文本)"]}
        agents = _agents_of(ctx)
        if not agents:
            return {"ok": False, "errors": ["当前没有运行中的模拟,无法回流纠正"]}
        if agent_name not in agents:
            return {"ok": False, "errors": [
                "没有这个角色: {!r};本局角色: {}".format(
                    agent_name, " / ".join(sorted(agents)))]}
        agent = agents[agent_name]
        inject = getattr(agent, "inject_story_event", None)
        if inject is None:
            return {"ok": False, "errors": [
                "该 agent 不支持记忆注入(缺 inject_story_event)"]}

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
                "note": note or "纠正回流:{}".format(correction[:60]),
                "correction": correction,
                "intervention": self.strategy_id,
            })
        except Exception as e:
            from live.state import log
            log.error("纠正回流审计写入失败(agent={}): {}".format(agent_name, e),
                      exc_info=True)
        return {"ok": True, "agent": agent_name,
                "injected": True, "correction": correction}


# ---------------------------------------------------------------------------
# 内置注册(import 时自挂;镜像 engines._register_builtin)
# ---------------------------------------------------------------------------

def _register_builtin() -> None:
    register("goals", WeightAdjustStrategy, {
        "name": WeightAdjustStrategy.name,
        "scope": "制度层",
        "payload": {"name": "角色名", "goals": "{目标: 权重} 且 Σ=1", "note": "干预理由(可选)"},
        "desc": "调整治理约束权重:改后果反馈的加权,倾向滞后收敛(IVD 主干预手段)",
    })
    register("undo", UndoInterventionStrategy, {
        "name": UndoInterventionStrategy.name,
        "scope": "制度层",
        "payload": {"agent": "角色名", "sim_time": "干预的模拟时刻", "time": "干预的真实写入时间"},
        "desc": "撤销一次权重干预:回滚到 old_constraints(沙盒场景一律 403)",
    })
    register("mark", ReflectionMarkStrategy, {
        "name": ReflectionMarkStrategy.name,
        "scope": "case01 专家链",
        "payload": {"agent": "角色名", "node_id": "反思节点", "text": "反思原文",
                    "verdict": "correct|incorrect|partial", "correction": "纠正文本",
                    "sim_time": "模拟时刻(可选)"},
        "desc": "标记反思的质量判定,纠正文本进 LoRA 训练数据(SFT/DPO)",
    })
    register("corrective_feedback", CorrectiveFeedbackStrategy, {
        "name": CorrectiveFeedbackStrategy.name,
        "scope": "内容层",
        "payload": {"agent": "角色名", "correction": "纠正文本", "note": "理由(可选)"},
        "desc": "把专家纠正写进 agent 记忆流(可被反思检索);不改倾向,经正常体验管线起作用",
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
             "errors": ["需要 Content-Type: application/json(不支持表单提交)"]},
            status_code=415)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 —— 没有 body 是正常的;坏 body 走字段校验
        return {}, None
    return (body if isinstance(body, dict) else {}), None


def _wrap(strategy_id: str, res) -> JSONResponse:
    if res is None:
        from live.state import log
        log.error("干预策略 {!r} 返回 None(策略实现漏 return?)".format(strategy_id))
        return JSONResponse({"ok": False,
                             "errors": ["干预策略 {!r} 返回空结果(实现缺陷)".format(
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
             "errors": ["未知干预策略: {!r};已注册: {}".format(
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
