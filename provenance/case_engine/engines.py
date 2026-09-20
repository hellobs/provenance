# -*- coding: utf-8 -*-
"""引擎注册表:scenario.meta.engine → 引擎元信息(引擎通用)。

引擎 = 一类「可被声明的运行方式」。场景在 `meta.engine` 里声明自己挂哪类引擎;
`case_engine.config` 只负责加载/校验这份声明,真正驱动由各引擎(桥)实现。
多引擎 + 多场景配合使用:每个引擎小而纯净、互不依赖。

命名约定:
- id 用连字符小写(experiment-eval / sandbox-value);
- 描述用中性词,不夹带任何具体 case 的业务词(纯度守卫会扫 case_engine/)。
"""
from typing import Dict

# 已知引擎注册表:id → {name, output, primitives, note}
ENGINES: Dict[str, Dict[str, str]] = {
    "experiment-eval": {
        "name": "受控实验 / 评估",
        "output": "run.json",
        "primitives": "branch(分支) / reflection(反思) / consistency(一致性) / retrieval(检索)",
        "note": "把一次咨询压成一条分支线,可审计、可复现",
    },
    "sandbox-value": {
        "name": "生成式价值权重沙盒",
        "output": "checkpoints(simulate-*.json / value_tendency)",
        "primitives": "移动 / 感知 / 调度 / 关系 / 价值倾向演化",
        "note": "角色按价值权重/期望行动,倾向随体验累积演化",
    },
}

# 默认引擎:scene 未声明 engine 时兜底(沿用既有受控实验形态,向后兼容)
DEFAULT_ENGINE = "experiment-eval"


def known(engine_id: str) -> bool:
    """该引擎 id 是否已注册。"""
    return engine_id in ENGINES


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