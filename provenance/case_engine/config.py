# -*- coding: utf-8 -*-
"""ScenarioConfig —— scenario.yaml 的加载、校验与类型安全访问(引擎通用)。

设计原则:
- 只定义引擎真正消费的字段;case 专属字段经 `custom` 原样透传,不在此建模;
- 「默认值兜底」:能安全缺省的字段给出中性默认(case 不填就不开)。
- 从文件加载,不做整目录扫描(场景目录遍历归 cli/serve,不在本文件)。
"""
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
    custom: dict = field(default_factory=dict)

    @property
    def case_id(self) -> str:
        return self.meta.get("case_id", "")

    @property
    def name(self) -> str:
        return self.meta.get("name", "")

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
    return Config(
        meta=meta,
        roles=roles,
        state_schema=state_schema,
        reflection=dict(proto.get("reflection") or {}),
        router=dict(proto.get("router") or {}),
        consistency=dict(proto.get("consistency") or {}),
        retrieval=dict(proto.get("retrieval") or {}),
        custom=dict(proto.get("custom") or {}),
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
    return errs


def validated(cfg: Config) -> Config:
    """validate 通过返回自身;有错抛 ValueError(拼好全部信息)。"""
    errs = validate(cfg)
    if errs:
        raise ValueError("scenario 配置不合法: " + "; ".join(errs))
    return cfg