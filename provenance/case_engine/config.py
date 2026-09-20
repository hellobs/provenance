# -*- coding: utf-8 -*-
"""ScenarioConfig —— scenario.yaml 的加载、校验与类型安全访问(引擎通用)。

设计原则:
- 只定义引擎真正消费的字段;case 专属字段统一落在 `world` 标准段(assets/params/
  value_tendency),不设 custom 逃生舱;
- 「默认值兜底」:能安全缺省的字段给出中性默认(case 不填就不开)。
- 从文件加载,不做整目录扫描(场景目录遍历归 cli/serve,不在本文件)。
"""
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from case_engine.engines import DEFAULT_ENGINE, known


_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")


@dataclass
class Role:
    id: str
    display_name: str = ""
    type: str = "ai_tool"          # ai_tool | user
    llm: str = "local"             # local | external
    system_prompt: str = ""
    conflict_rules: List[dict] = field(default_factory=list)
    max_tokens: int = 2048
    temperature: float = 0.5


@dataclass
class Config:
    meta: dict = field(default_factory=dict)
    roles: List[Role] = field(default_factory=list)
    # 状态字段 schema 原样透传(与 engine.world.EntityState 消费格式一致,
    # 每个字段为 {"initial": Any, "type": str});不在此转换,避免引擎/配置漂移。
    state_schema: Dict[str, dict] = field(default_factory=dict)
    reflection: dict = field(default_factory=dict)
    router: dict = field(default_factory=dict)
    consistency: dict = field(default_factory=dict)
    retrieval: dict = field(default_factory=dict)
    branch: dict = field(default_factory=dict)
    # timeline:声明式时间线(A/B 线价格事件),由 branch 路由选线;经 nodes 装配。
    timeline: dict = field(default_factory=dict)
    # value_tendency:案例的价值权重(如 case00 的 governance + initial_tendency),
    # 标准段 world.value_tendency,sandbox-value 真读取校验。
    value_tendency: dict = field(default_factory=dict)
    # assets / params:沙盒资产的资产路径与沙盒参数(原 case00 的 scenario_assets /
    # sandbox_params,从 custom 提升为 world 标准段;彻底废除 custom 逃生舱)。
    assets: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)

    @property
    def case_id(self) -> str:
        return self.meta.get("case_id", "")

    @property
    def name(self) -> str:
        return self.meta.get("name", "")

    @property
    def engine(self) -> str:
        """场景绑定的引擎 id(meta.engine);未声明回退默认引擎。"""
        return self.meta.get("engine") or DEFAULT_ENGINE

    def state_initial(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, f in self.state_schema.items():
            out[key] = f.get("initial") if f.get("initial") is not None else None
        return out


# 既有 world/state.py 里 EthanState 用到的内置工厂:由 Config.state_initial() 提供


def _coerce(proto) -> Config:
    meta = proto.get("meta") or {}
    roles = []
    for item in proto.get("roles") or []:
        item = item or {}
        roles.append(Role(
            id=str(item.get("id", "")),
            display_name=str(item.get("display_name", "")
                             or item.get("id", "")),
            type=str(item.get("type", "ai_tool")),
            llm=str(item.get("llm", "local")),
            system_prompt=str(item.get("system_prompt", "")),
            conflict_rules=list(item.get("conflict_rules") or []),
            max_tokens=int(item.get("max_tokens", 2048) or 2048),
            temperature=float(item.get("temperature", 0.5) or 0.5),
        ))
    world = proto.get("world") or {}
    state_schema = dict(world.get("state_schema") or {})
    value_tendency = dict(world.get("value_tendency") or {})
    assets = dict(world.get("assets") or {})
    params = dict(world.get("params") or {})
    return Config(
        meta=meta,
        roles=roles,
        state_schema=state_schema,
        value_tendency=value_tendency,
        assets=assets,
        params=params,
        reflection=dict(proto.get("reflection") or {}),
        router=dict(proto.get("router") or {}),
        consistency=dict(proto.get("consistency") or {}),
        retrieval=dict(proto.get("retrieval") or {}),
        branch=dict(proto.get("branch") or {}),
        timeline=dict(proto.get("timeline") or {}),
    )


def load(data: Any, **overrides) -> Config:
    """从已解析的 dict 构造 Config;overrides 直接合并进 Config(供测试/注入)。"""
    cfg = _coerce(data)
    if overrides:
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    return cfg


def load_yaml(yaml_path: str) -> Config:
    """从 scenario.yaml 加载;路径不存在或 YAML 非法抛出 FileNotFoundError/ValueError。"""
    import yaml

    if not os.path.exists(yaml_path):
        raise FileNotFoundError("scenario.yaml 不存在: {}".format(yaml_path))
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _coerce(data)


def validate(cfg: Config) -> List[str]:
    """返回校验错误列表(空 = 通过)。供 cli/serve 在启动前检查。"""
    errs: List[str] = []
    if not cfg.case_id:
        errs.append("meta.case_id 必填")
    elif not _SAFE_CASE_ID.match(cfg.case_id):
        errs.append("meta.case_id 只能是字母数字/下划线/短横线,且以字母数字开头")
    if not cfg.roles:
        errs.append("roles 至少一个")
    else:
        ids = [r.id for r in cfg.roles if not r.id]
        if ids:
            errs.append("存在没有 id 的角色")
    if len({r.id for r in cfg.roles}) != len(cfg.roles):
        errs.append("roles.id 重复")
    declared_engine = cfg.meta.get("engine")
    if declared_engine and not known(declared_engine):
        errs.append("meta.engine 未注册: {!r}".format(declared_engine))
    return errs


def validated(cfg: Config) -> Config:
    """validate 通过返回自身;有错抛 ValueError(拼好全部信息)。"""
    errs = validate(cfg)
    if errs:
        raise ValueError("scenario 配置不合法: " + "; ".join(errs))
    return cfg


def consistency_signals(cfg: Config) -> Dict[str, List[str]]:
    """把 scenario 的 consistency 段转成 case_engine.consistency 需要的 signals 结构。

    scenario.yaml 里用 buy_words/cond_words/negators/neg_phrases(可读、业务词)
    转成引擎的 {buy_words, cond_words, negators, neg_phrases}。缺省为空 → 引擎全 unknown,
    与 defaultValue 兜底一致。
    """
    c = cfg.consistency
    return {
        "buy_words": list(c.get("buy_words") or []),
        "cond_words": list(c.get("cond_words") or []),
        "negators": list(c.get("negators") or []),
        "neg_phrases": list(c.get("neg_phrases") or []),
    }


def value_tendency_args(cfg: Config) -> Dict[str, Any]:
    """从标准段取价值权重两本账(governance + initial_tendency),供 sandbox 预检消费。

    语义原样透传(不改写/不归一,权重忠实保留);缺任一账则回空,由引擎如实标注。
    """
    vt = cfg.value_tendency or {}
    return {
        "governance": dict(vt.get("governance") or {}),
        "initial_tendency": dict(vt.get("initial_tendency") or {}),
        "present": bool(vt),
    }


def reflection_args(cfg: Config) -> Dict[str, Any]:
    """从 scenario.reflection 组装 case_engine.reflection.run_reflection 的可注入参数。

    对应参数:reflection_prompt=prompt_cn, reflection_system=system_prompt,
    max_tokens/temperature 由 run_reflection 的注入覆盖(temperature 引擎固定 0.4,
    max_tokens 由调用方显式传参决定——这里不带,避免冲突)。
    speaker_labels 由 case 的 roles 推断:ai_tool 角色当"助手"/"assistant",
    user 角色当"提问方";缺省用引擎中性值。
    """
    r = cfg.reflection
    labels: Dict[str, str] = {}
    assistant_ids = {x.id for x in cfg.roles if x.type == "ai_tool"}
    asker_ids = tuple(x.id for x in cfg.roles if x.type == "user")
    if assistant_ids:
        labels["assistant"] = next(iter(assistant_ids))
    if asker_ids:
        labels["asker"] = asker_ids[0]
    return {
        "reflection_prompt": str(r.get("prompt_cn")
                                 or r.get("prompt") or ""),
        "reflection_system": str(r.get("system_prompt") or ""),
        "speaker_labels": labels or None,
        "asker_speaker": asker_ids,
        "fb_asker_key": str(r.get("fb_asker_key") or "client"),
    }


def branch_args(cfg: Config) -> Dict[str, Any]:
    """从 scenario.branch 组装 RuleBranchRouter / LLMBranchJudge 的可注入参数。

    把 scenario 的 no_buy/refuse 归到分支 X、conditional/anti_allin 归到分支 Y 的
    语义映射恢复为有序 rules(顺序即优先级):先 B(拒绝/不参与) 后 C(条件化),
    其余 → default。judge_prompt 供 LLMBranchJudge 使用。
    """
    b = cfg.branch
    rules: Dict[str, List[str]] = {}
    no_buy = list(b.get("no_buy") or [])
    refuse = list(b.get("refuse") or [])
    conditional = list(b.get("conditional") or [])
    anti_allin = list(b.get("anti_allin") or [])
    # 原实现语义:no_buy/refuse → B;conditional/anti_allin → C
    if no_buy or refuse:
        rules["B"] = no_buy + refuse
    if conditional or anti_allin:
        rules["C"] = conditional + anti_allin
    return {
        "rules": rules,
        "default_branch": str(b.get("default_branch") or ""),
        "judge_prompt": str(b.get("judge_prompt") or ""),
        "fallback_map": dict(b.get("fallback_map") or {}),
    }


def select_timeline(cfg: Config, branch: str):
    """分支 → 选一条时间线(声明式)。返回 (line_id, date->[events]) 或 None。

    优先级:
    1. timeline.branch_map 的 {branch: line_id};
    2. branch fallback_map 里携带的 timeline 分派(如 {A:{timeline:"A"}});
    3. timeline.lines 仅一条 → 直接用它。
    找不到或未声明 timeline → None(该场景无时间线,保持旧最小行为)。
    """
    t = cfg.timeline
    lines = dict(t.get("lines") or {})
    if not lines:
        return None
    # branch → line_id
    line_id = (t.get("branch_map") or {}).get(branch, "")
    if not line_id:
        b = cfg.branch.get("fallback_map") or {}
        meta = b.get(branch) or {}
        line_id = str(meta.get("timeline", ""))
    if not line_id:
        line_id = next(iter(lines), "")
    events = lines.get(line_id)
    if events is None:
        return None
    tl = {str(d): [dict(e) for e in (evs or [])] for d, evs in events.items()}
    header = {
        "line_id": line_id,
        "t0": [dict(e) for e in (t.get("t0") or [])],
        "interactions": dict(t.get("interactions") or {}),
        "final_date": str((cfg.meta or {}).get("end_date") or ""),
        "start_date": str((cfg.meta or {}).get("start_date") or ""),
        "role_ids": [r.id for r in cfg.roles],
    }
    return (line_id, tl, header)


def merge_t0(events_by_date: dict, t0_events, start_date: str = "") -> dict:
    """把 T0(咨询当天,与分支无关)的证据放进 T0 咨询日,作为独立起始节点。

    优先放 start_date(咨询日)当日;该日已有事件则在最前插入(T0 是当天先发生的事实)。
    start_date 为空则并入最小日(兼容缺省)。
    """
    t0 = list(t0_events or [])
    if not t0:
        return events_by_date
    out = dict(events_by_date)
    if not out:
        return out
    day = start_date or min(out.keys())
    evs = [dict(e) for e in t0] + [dict(e) for e in (out.get(day) or [])]
    out[day] = evs
    return out