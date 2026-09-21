# -*- coding: utf-8 -*-
"""多场景只读服务(引擎通用,Phase 4)。

自动发现 `cases/` 下的所有声明式场景,并只读地暴露其**声明摘要**与**单场景详情**。
这是场景引擎的公共浏览/发现入口;不在此提供具体 run 的读取(各 case 的 run 契约
仍归各自的冻结 serve,如 5002/5003)。

端点:
  1. GET /api/scenarios                所有已发现场景的摘要(含 engine 分类)
  2. GET /api/scenarios/{case_id}      单个场景的完整声明视图
  3. GET /                             服务自述

只读;case_id 用安全字符校验,杜绝路径穿越。启动由调用方决定端口
(如 uvicorn case_engine.serve:app --port 5004),本模块不自拉起,避免端口冲突。
"""
import os
import re

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from case_engine.config import load_yaml
from case_engine.engines import describe, supported_by
from case_engine.scenarios import by_id, default_cases_root, discover

# cases 根目录:统一从 scenarios.default_cases_root() 定位(环境变量可覆盖)
CASES_ROOT = default_cases_root()

# case_id 只允许字母数字、下划线、短横线(与 config._SAFE_CASE_ID 一致),杜绝路径穿越。
_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")

app = FastAPI(
    title="Case Engine · 多场景声明浏览",
    description="只读暴露场景引擎已发现的所有声明式场景(cases/*/scenario.yaml)。只读。",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _get_case(case_id: str):
    if not _SAFE_CASE_ID.match(case_id or ""):
        raise HTTPException(status_code=404, detail="scenario not found")
    info = by_id(CASES_ROOT, case_id)
    if info is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    return info


@app.get("/api/engine-components", tags=["engines"])
def engine_components() -> dict:
    """每引擎激活的组件 id 清单(引擎→组件的数据契约,供前端/服务折叠面板)。"""
    from case_engine.engines import ENGINES
    return {
        "count": len(ENGINES),
        "engines": {
            eid: {"name": (m or {}).get("name", ""),
                  "components": list((m or {}).get("components") or [])}
            for eid, m in ENGINES.items()
        },
    }


@app.get("/", tags=["meta"])
def index() -> dict:
    return {
        "service": "Case Engine scenario discovery (read-only)",
        "cases_root": os.path.relpath(CASES_ROOT),
        "endpoints": ["GET /api/scenarios",
                      "GET /api/scenarios/{case_id}"],
        "docs": "/docs",
    }


@app.get("/api/scenarios", tags=["scenarios"])
def list_scenarios() -> dict:
    """所有已发现场景的摘要;按 engine 分类计数。problem 场仍列出但标记。"""
    infos = discover(CASES_ROOT)
    by_engine = {}
    for s in infos:
        by_engine[s.engine] = by_engine.get(s.engine, 0) + 1
    return {
        "count": len(infos),
        "by_engine": by_engine,
        "scenarios": [s.summary() for s in infos],
    }


@app.get("/api/scenarios/{case_id}", tags=["scenarios"])
def scenario_detail(case_id: str) -> dict:
    """单个场景的完整声明视图(引擎分类 + 角色 + 状态字段)。"""
    info = _get_case(case_id)
    if info.problem:
        raise HTTPException(status_code=502, detail=info.problem)
    cfg = load_yaml(info.path)
    engine_ids = supported_by(cfg)
    return {
        "case_id": info.case_id,
        "name": info.name,
        "engine": info.engine,
        "engine_meta": describe(info.engine),
        # 自由搭配:注册表里所有能跑这份场景的引擎(策略 supports 枚举)
        "supported_engines": [{"engine": e, **describe(e)}
                              for e in engine_ids],
        "roles": [{"id": r.id, "type": r.type, "llm": r.llm}
                  for r in cfg.roles],
        "state_schema": {k: v.get("type", "any")
                         for k, v in (cfg.state_schema or {}).items()},
        "n_roles": info.n_roles,
        "n_state_fields": len(cfg.state_schema or {}),
        "scenario_path": os.path.relpath(info.path),
    }