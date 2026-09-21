# -*- coding: utf-8 -*-
"""引擎注册与工厂(引擎通用):scenario.meta.engine → 可注入的引擎策略。

分层(设计模式:注册表 + 工厂;策略具体实现见 `strategy.py`):
- `ENGINES`：描述表(id → 元信息),供 known/resolve/describe 与人类浏览。
- `_BUILDERS`：构造表(id → `(scenario) -> EngineStrategy`),工厂从这里产出策略实例。
- `build_for(scenario, requested)`：**自由搭配的核心** —— `requested` 显式覆盖场景
  推荐引擎;未指定则用场景 `meta.engine`(推荐/默认)。
- `supported_by(scenario)`：对一份场景枚举「所有能跑它的引擎」,供服务/CLI 展示兼容矩阵。

设计原则:
- 场景 = 数据;引擎 = 可注入、可组合的策略;**同一场景可与不同引擎自由搭配**(`requested`)。
- 新增引擎只需 `register(id, builder)` + 一个策略类,零改动消费端。
"""
from typing import Any, Callable, Dict, List

from case_engine.strategy import BUILTIN_STRATEGIES, EngineStrategy

# 组件 id 词汇表(渲染到前端时由各服务按需折叠成具体面板):
#   scene     小镇场景       chat        聊天         governance 治理/权重面板
#   timeline  干预时间轴     reflection  反思          overview    概览
#   events    事件           state       状态          retrieval   检索
#   router    问题分流       injector    注入器        audit       审计/一致性
# 一份引擎声明"它通常激活哪些组件";必要时策略.panels(scenario) 可按场景微调。
_COMPONENTS = {
    "experiment-eval": ["scene", "chat", "reflection", "overview", "events",
                        "state", "retrieval", "router", "injector", "audit"],
    "sandbox-value": ["scene", "chat", "governance", "timeline", "reflection"],
}

# 已知引擎注册表:id → {name, output, primitives, components, note}(描述用中性词)
ENGINES: Dict[str, Dict[str, Any]] = {
    "experiment-eval": {
        "name": "受控实验 / 评估",
        "output": "run.json",
        "primitives": "branch(分支) / reflection(反思) / consistency(一致性) / retrieval(检索)",
        "components": _COMPONENTS["experiment-eval"],
        "note": "把一次场景交互压成一条分支线,可审计、可复现",
    },
    "sandbox-value": {
        "name": "生成式价值权重沙盒",
        "output": "checkpoints(simulate-*.json / value_tendency)",
        "primitives": "移动 / 感知 / 调度 / 关系 / 价值倾向演化",
        "components": _COMPONENTS["sandbox-value"],
        "note": "角色按价值权重/期望行动,倾向随体验累积演化",
    },
}

# 默认引擎:scene 未声明 engine 时兜底(沿用既有受控实验形态,向后兼容)
DEFAULT_ENGINE = "experiment-eval"

# 工厂构造表:id → builder(scenario -> EngineStrategy);启动时注册内置策略
_BuildFn = Callable[[Any], EngineStrategy]
_BUILDERS: Dict[str, _BuildFn] = {}


def _register_builtin() -> None:
    for eid, builder in BUILTIN_STRATEGIES.items():
        if eid not in _BUILDERS:
            _BUILDERS[eid] = builder


_register_builtin()


def known(engine_id: str) -> bool:
    """该引擎 id 是否已注册。"""
    return engine_id in ENGINES


def all_ids() -> List[str]:
    """全部已注册引擎 id(排序)。

    2026-09-21 加:配置工具的「引擎」页要列**全部**引擎,而 `describe()` 无参只给默认引擎
    (默认 experiment-eval),照它列会漏掉 sandbox-value。CLI 的 `engines` 子命令一直是
    直接读 `ENGINES`,这里把它提成公开函数,免得各调用方各读各的私有表。
    """
    return sorted(ENGINES)


def resolve(engine_id: str = "") -> str:
    """空值回退到默认引擎;非法 id 抛 ValueError 以阻止错误启动。"""
    engine_id = engine_id or ""
    if not engine_id:
        return DEFAULT_ENGINE
    if engine_id not in ENGINES:
        raise ValueError("未知引擎: {!r}(已注册: {})".format(
            engine_id, ", ".join(sorted(ENGINES))))
    return engine_id


def describe(engine_id: str = "") -> Dict[str, str]:
    """返回引擎元信息;空值解析为默认引擎。"""
    rid = resolve(engine_id)
    return {"engine": rid, **ENGINES[rid]}


def components(engine_id: str = "") -> List[str]:
    """返回引擎激活的组件 id 清单;空值解析为默认引擎。新增组件本身即数据。"""
    rid = resolve(engine_id)
    return list(ENGINES[rid].get("components") or [])


def register(engine_id: str, builder: _BuildFn, meta: Dict[str, str]) -> None:
    """注册一个新引擎:builder(scenario)->EngineStrategy + 描述 meta。消费端零改动。"""
    if not engine_id or not engine_id.isalnum() and not all(
            c.isalnum() or c in "-_" for c in engine_id):
        raise ValueError("非法引擎 id: {!r}".format(engine_id))
    if engine_id in ENGINES:
        raise ValueError("引擎已注册: {!r}".format(engine_id))
    ENGINES[engine_id] = meta
    _BUILDERS[engine_id] = builder


def build(engine_id: str = "", scenario: Any = None) -> EngineStrategy:
    """从工厂产出一个引擎策略实例;engine_id 空则用默认引擎。"""
    rid = resolve(engine_id)
    builder = _BUILDERS.get(rid)
    if builder is None:
        raise ValueError("引擎 {!r} 没有可用的策略构造器".format(rid))
    return builder(scenario)


def build_for(scenario: Any, requested: str = "") -> EngineStrategy:
    """为一场景选出引擎策略(自由搭配核心)。

    - `requested` 非空:用它**覆盖**场景推荐引擎(可自由搭配);
    - 空:用 scenario.meta.engine(推荐/默认)。
    返回的实例是否真能跑该场景由 `strategy.supports(scenario)` 判定(不在此拦)。
    """
    engine_id = requested or getattr(scenario, "engine", "") or DEFAULT_ENGINE
    return build(engine_id, scenario)


def supported_by(scenario: Any) -> List[str]:
    """对一份场景,枚举注册表里「能跑它」的全部引擎 id(supports=True)。"""
    out: List[str] = []
    for eid in ENGINES:
        try:
            if build(eid, scenario).supports(scenario):
                out.append(eid)
        except Exception:  # noqa: BLE001 —— 单个引擎异常不影响枚举整体
            continue
    return out