# -*- coding: utf-8 -*-
"""场景发现层(引擎通用):自动扫描 cases/ 目录下的 scenario.yaml。

服务/CLI 的公共入口:从一个 cases 根目录自动发现所有声明式场景,返回
`ScenarioInfo`(case_id / 源路径 / 声明摘要)。发现是「宽容扫描」——单个
坏文件记录 problem、不拖垮整体;engine 合法性交给 `engines.resolve` 校验。

本模块只依赖 `config`/`engines`,不 import 任何具体场景或运行产物,保持纯净。
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional

from case_engine.config import load_yaml, validate
from case_engine.engines import resolve

SCENARIO_FILENAME = "scenario.yaml"


def default_cases_root() -> str:
    """定位 cases 根目录:case_engine/ 上一级(provenance/provenance)下的 cases;环境变量可覆盖。

    供 cli/serve 复用的唯一取根入口,避免各处重复定位漂移。
    """
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("CASE_ENGINE_CASES_ROOT", os.path.join(base, "cases"))


@dataclass
class ScenarioInfo:
    case_id: str
    path: str
    name: str = ""
    engine: str = ""
    n_roles: int = 0
    n_state_fields: int = 0
    problem: str = ""            # 非空 = 该场景加载失败(发现问题不抛,保留可见)

    def summary(self) -> dict:
        return {
            "case_id": self.case_id,
            "name": self.name,
            "engine": self.engine,
            "n_roles": self.n_roles,
            "n_state_fields": self.n_state_fields,
            "scenario_path": os.path.relpath(self.path),
            "problem": self.problem or None,
        }


def _case_dirs(cases_root: str) -> List[str]:
    if not os.path.isdir(cases_root):
        return []
    return sorted(
        d for d in os.listdir(cases_root)
        if os.path.isdir(os.path.join(cases_root, d))
    )


def discover(cases_root: str) -> List[ScenarioInfo]:
    """扫描 cases_root 下所有含 scenario.yaml 的目录,逐个声明化。"""
    out: List[ScenarioInfo] = []
    for cid in _case_dirs(cases_root):
        yaml_path = os.path.join(cases_root, cid, SCENARIO_FILENAME)
        if not os.path.isfile(yaml_path):
            continue
        info = ScenarioInfo(case_id=cid, path=yaml_path)
        try:
            cfg = load_yaml(yaml_path)
            errs = validate(cfg)
            if errs:
                raise ValueError("; ".join(errs))
            info.name = cfg.name
            info.engine = resolve(cfg.engine)
            info.n_roles = len(cfg.roles)
            info.n_state_fields = len(cfg.state_schema)
        except Exception as exc:  # noqa: BLE001 —— 宽容扫描:单个坏场景不拖垮整体
            info.problem = "{}({})".format(type(exc).__name__, exc)
        out.append(info)
    return out


def by_id(cases_root: str, case_id: str) -> Optional[ScenarioInfo]:
    """按 case_id 精确取一个场景;不存在返回 None。"""
    for info in discover(cases_root):
        if info.case_id == case_id:
            return info
    return None