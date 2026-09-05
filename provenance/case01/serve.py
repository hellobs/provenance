# -*- coding: utf-8 -*-
"""Case 01 只读数据服务(FastAPI)。

供 Governance Platform(Tongmu 团队)接入 Reflection → Expert Review 链路:
  1. GET /api/runs                     运行索引(简短,含 Branch 元信息供平台内部用)
  2. GET /api/runs/{run_id}            单个 Run 详情(含 Reflection 原文 / Router
                                       拆分结果 / Audit 链,结构化,供平台建 Expert
                                       Review Task 与持久化)
  3. GET /api/runs/{run_id}/full-context  专家"View Full Context"自然语言全文
                                       (04 六.3 / 05 三:不以 JSON/字段/事件对象形式
                                       暴露)

边界(04 十一 / 05 七):
  · 本服务只读 case01 已完成的 Run 产物;不做任何写操作。
  · 专家任务状态机、持久化、专家池、冲突轮次、训练材料池归集均由
    平台侧实现;本服务不持有这些状态。
  · 不暴露 .secrets.json / Financial Data 原始语料 / Ollama 与外部模型地址。
启动(任一):python serve.py | uvicorn serve:app | uvicorn case01.serve:app --port 5002
"""
import os
import re
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# 支持两种启动方式:python serve.py / uvicorn serve:app(在 case01 目录),
# 以及 python -m case01.serve 或 uvicorn case01.serve:app(在 provenance 目录)。
try:  # 包内导入(作为 case01.serve)
    from . import full_context as fc
except ImportError:  # 直接脚本运行(在 case01 目录)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from case01 import full_context as fc

RUNS_ROOT = os.environ.get(
    "CASE01_RUNS_ROOT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs"),
)

app = FastAPI(
    title="GTC Case 01 · Run 只读数据服务",
    description="向 Governance Platform 提供 Case 01 已完成 Run 的索引、结构化"
                "治理数据(Raw Reflection / Router / Audit)与专家 Full Context"
                "自然语言全文。只读。",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 开发期跨域放开;接入平台后可按域名收紧
    allow_methods=["GET"],
    allow_headers=["*"],
)

# run_id 只允许字母数字、点、下划线、短横线,杜绝路径穿越。
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _get_run(run_id: str) -> dict:
    if not _SAFE_RUN_ID.match(run_id):
        raise HTTPException(status_code=404, detail="run not found")
    rec = fc.load_run(RUNS_ROOT, run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run not found")
    return rec


@app.get("/", tags=["meta"])
def index() -> dict:
    return {
        "service": "GTC Case 01 run data (read-only)",
        "endpoints": [
            "GET /api/runs",
            "GET /api/runs/{run_id}",
            "GET /api/runs/{run_id}/full-context",
        ],
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/api/runs", tags=["runs"])
def list_runs() -> dict:
    """全部已完成 Run 的索引(简短字段;branch 仅供平台内部关联使用)。"""
    runs = fc.list_runs(RUNS_ROOT)
    return {"runs": runs, "count": len(runs)}


@app.get("/api/runs/{run_id}", tags=["runs"])
def run_detail(run_id: str) -> dict:
    """单个 Run 详情。

    Platform 侧用于:按问题建 Expert Review Task、关联审计时间线、判断
    Reflection / Router 是否已就绪。专家看到的文本一律走 full-context。
    """
    rec = _get_run(run_id)
    ba = rec.get("branch_action") or {}
    ref = rec.get("reflection") or {}
    router = rec.get("router") or {}
    meta = {
        "run_id": rec.get("run_id") or run_id,
        "start_date": rec.get("start_date", ""),
        "end_date": rec.get("end_date", ""),
        "branch": rec.get("branch", ""),
        "branch_summary": fc._branch_summary(rec.get("branch", ""), ba),
        "n_turns": len(rec.get("turns") or []),
        "n_retrievals": len(rec.get("retrievals") or []),
        "n_events": len(rec.get("events") or []),
        "final_feedback_date": (rec.get("final_feedback") or {}).get("date", ""),
        "reflection": {
            "generated": bool(ref.get("text")),
            "text": ref.get("text", ""),
        },
        "router": {
            "ran": bool(router.get("issues") is not None),
            "issues": router.get("issues", []),
        },
        "audit": rec.get("audit", []),
    }
    return meta


@app.get("/api/runs/{run_id}/full-context", tags=["runs"])
def full_context(run_id: str) -> dict:
    """专家“View Full Context”:自然语言完整记录。

    内容与信息边界见 full_context.build_full_context;不含 Branch 预设等
    实验元信息。
    """
    rec = _get_run(run_id)
    return {
        "run_id": rec.get("run_id") or run_id,
        "format": "text/plain; charset=utf-8",
        "full_context": fc.build_full_context(rec),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=5002)
