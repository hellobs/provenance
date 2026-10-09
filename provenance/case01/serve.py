# -*- coding: utf-8 -*-
"""Case 01 只读数据服务(FastAPI)。

供 Governance Platform(平台侧 团队)接入 Reflection → Expert Review 链路:
  1. GET /api/runs                     运行索引(简短,含 Branch 元信息供平台内部用)
  2. GET /api/runs/{run_id}            单个 Run 详情(含 Reflection 原文 / Router
                                       拆分结果 / Audit 链,结构化,供平台建 Expert
                                       Review Task 与持久化)
  3. GET /api/runs/{run_id}/full-context  专家"View Full Context"自然语言全文
                                       (04 六.3 / 05 三:不以 JSON/字段/事件对象形式
                                       暴露)

边界(04 十一 / 05 七):
  · 本服务只读 case01 已完成的 Run 产物;不做任何写操作。
  · 本服务只提供 Router 使用的专家**类别目录**;具体专家人员、可用状态、任务分配、
    冲突轮次和训练材料池归集均由平台侧实现。
  · 不暴露 .secrets.json / Financial Data 原始语料 / Ollama 与外部模型地址。
启动(任一):python serve.py | uvicorn serve:app | uvicorn case01.serve:app --port 5002
"""
import os
import re
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 支持两种启动方式:python serve.py / uvicorn serve:app(在 case01 目录),
# 以及 python -m case01.serve 或 uvicorn case01.serve:app(在 provenance 目录)。
try:  # 包内导入(作为 case01.serve)
    from . import full_context as fc
    from .expert_pool import load_expert_pool
    from .reflection import evaluate_reflection_quality
except ImportError:  # 直接脚本运行(在 case01 目录)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from case01 import full_context as fc
    from case01.expert_pool import load_expert_pool
    from case01.reflection import evaluate_reflection_quality

from live.netguard import resolve_embed_origins
from case_engine.paths import data_root

# 2026-10-05:从裸读改为走绝对路径守卫(相对值当场报错,与写侧同语义)。
# 2026-10-08:改走 `data_root()` —— 成品/渲染产物的家从 `case01/` 挪到仓根 `data/case01/`;
# 搬迁期新位置优先、老位置回落并出声。两个环境变量仍是最高优先的显式覆盖口。
RUNS_ROOT = data_root("case01.records", env_var="CASE01_RUNS_ROOT")
RUNS_HTML_ROOT = data_root("case01.html", env_var="CASE01_RUNS_HTML_ROOT")

app = FastAPI(
    title="GTC Case 01 · Run 只读数据服务",
    description="向 Governance Platform 提供 Case 01 已完成 Run 的索引、结构化"
                "治理数据(Raw Reflection / Router / Audit)与专家 Full Context"
                "自然语言全文。只读。",
    version="0.1.0",
)

# 跨源白名单(2026-09-24 对齐 live/routes.py 的安全口径):本服务没有鉴权,
# allow_origins=["*"] 等于让任意网页一个 fetch 就把成品记录读走。默认只给本机来源;
# 平台侧跨源取数请显式设 EMBED_ALLOW_ORIGINS(iframe 嵌入不走 CORS,不受影响)。
_CORS_ORIGINS = resolve_embed_origins(os.environ.get(
    "EMBED_ALLOW_ORIGINS", "http://127.0.0.1:5002,http://localhost:5002"))
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
)
print("[serve] CORS allow_origins = {}{}".format(
    _CORS_ORIGINS,
    "" if os.environ.get("EMBED_ALLOW_ORIGINS", "").strip()
    else "  (未设 EMBED_ALLOW_ORIGINS → 只允许本机来源;平台侧跨源取数请显式设域名)"))

if os.path.isdir(RUNS_HTML_ROOT):
    app.mount("/viewer", StaticFiles(directory=RUNS_HTML_ROOT, html=True),
              name="viewer")

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
            "GET /api/expert-categories",
        ],
        "docs": "/docs",
        "openapi": "/openapi.json",
        "viewer": "/viewer/",
    }


@app.get("/api/expert-categories", tags=["experts"])
def expert_categories() -> dict:
    """Router 当前使用的专家类别池；只读，不包含具体专家人员或可用状态。"""
    return load_expert_pool()


@app.get("/api/runs", tags=["runs"])
def list_runs(include_questionable: bool = False) -> dict:
    """全部已完成 Run 的索引(简短字段;branch 仅供平台内部关联使用)。

    每条记录带 `quality` / `consistency` / `branch_source`(加法字段)。默认**不返回**
    `quality="questionable"` 的记录 —— 那是"预设分支且与 AI 的 T0 立场矛盾"或
    **判定失败(run 停在 T0)**的记录(例:`A-1720` 里 AI 说"不能确认值得买"、
    当事人却满仓),给专家看会直接暴露矛盾内容 / 给平台等于让一条废记录去建单。
    这个"不返回"**不静默**:响应里给 `excluded` 计数与原因,平台也可以
    `?include_questionable=1` 全量取。
    `quality="unverified"`(旧记录没有一致性戳)**照常返回** —— 判不了 ≠ 有问题。
    """
    all_runs = fc.list_runs(RUNS_ROOT)
    # 与评审侧 `live/history._HIDDEN_QUALITY` 同一份口径(那边的注释就是这么写的)。
    # `unreadable` = "有 run 目录但没有 run.json",本函数今天造不出这一类(它只吃
    # 落盘记录),但一旦上游把占位行透传过来,少 hide 它就等于拿一条压根没跑成的
    # run 去让平台建专家任务。四类都必须在 excluded 里点名,不静默。
    hidden = ("questionable", "debug", "deprecated", "unreadable")
    runs = all_runs if include_questionable else [r for r in all_runs if r["quality"] not in hidden]
    dropped = [r for r in all_runs if r["quality"] in hidden]
    out = {"runs": runs, "count": len(runs),
           "filter": {"include_questionable": bool(include_questionable)}}
    if dropped and not include_questionable:
        by_kind = {}
        for r in dropped:
            by_kind.setdefault(r["quality"], []).append(r["run_id"])
        out["excluded"] = {
            "count": len(dropped), "by_kind": by_kind,
            "reason": "questionable=预设分支与 AI 的 T0 立场矛盾,或判定失败(停在 T0);"
                      "debug=调试跑(--nodes 截断等,内容不完整);"
                      "deprecated=记录已被显式标废弃(样本作废);"
                      "unreadable=有 run 目录但 run.json 不在(该次 run 未落盘)。"
                      "加 ?include_questionable=1 可取全量",
        }
    return out


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
    reflection_quality = ref.get("quality")
    if (not isinstance(reflection_quality, dict) or not reflection_quality) and ref.get("text"):
        reflection_quality = evaluate_reflection_quality(
            ref.get("text", ""), ref.get("material", ""), language=rec.get("language", "legacy"))
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
        "has_reflection": bool(ref.get("text")),
        # 质检标记(加法字段):预设分支且 AI 立场不一致 → questionable
        **fc.quality_of(rec),
        "reflection": {
            "generated": bool(ref.get("text")),
            "text": ref.get("text", ""),
            "quality": reflection_quality or {},
        },
        "router": {
            "ran": bool(router.get("issues") is not None),
            "issues": router.get("issues", []),
            "postprocess": router.get("postprocess", {}),
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
    import os

    import uvicorn

    # host 可覆盖(2026-10-05 K3):默认回环。5002 虽然没有写端点,但**同样没有鉴权**,
    # README 的成品记录人人可读;要绑非回环地址必须显式声明 LIVE_ALLOW_REMOTE=1,
    # 与 5010 走同一套守卫(此前这里硬编码 127.0.0.1,config_tool 亦然 —— 一旦有人
    # 改成可配 host 就没有守卫兜底)。
    from live.netguard import require_explicit_remote

    host = os.environ.get("CASE01_SERVE_HOST", "127.0.0.1")
    require_explicit_remote(host, where="5002 case01 只读面")
    uvicorn.run(app, host=host, port=5002)
