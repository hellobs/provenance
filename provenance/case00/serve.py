# -*- coding: utf-8 -*-
"""case00 只读服务(FastAPI,端口 5003)。

case00 = provenance 原有的 6 角色投资咨询场景(最一开始那套多 agent 设定)。
它的实时可视化仍是平台入口 `live_fastapi.py`(**5001**);本文件只提供**只读**的
运行存档浏览面,与 case01 的 `case01/serve.py`(**5002**)形状对称。

为什么不做成 5002 那样的"契约服务":
- case00 的存档是 checkpoints(每步一个 `simulate-*.json` 快照 + `decisions.json` +
  `conversation.json`),不是 case01 那种单文件 `run.json`,没有 reflection/router;
- case00 **已冻结、后续不维护**,所以这里只做"能看"的最小面,不承诺机器可读契约。

存档不复制:2.2 GB 仍在 `results/checkpoints/`,本服务只读它并给索引与摘要。
"""
import argparse
import json
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BASE_DIR)
CKPT_ROOT = os.path.join(REPO_ROOT, "results", "checkpoints")
SCENARIO_DIR = os.path.join(BASE_DIR, "scenario")

app = FastAPI(title="GTC Case 00 · 存档只读浏览")


def _run_dirs():
    if not os.path.isdir(CKPT_ROOT):
        return []
    names = []
    for name in sorted(os.listdir(CKPT_ROOT)):
        path = os.path.join(CKPT_ROOT, name)
        if os.path.isdir(path):
            names.append(name)
    return names


def _summary(run_id):
    """一个 run 的摘要:快照步数 / 模拟时间范围 / 决策条数 / 对话条数。

    只读目录结构与小文件,不遍历 2.2 GB 的大存档内容。
    """
    path = os.path.join(CKPT_ROOT, run_id)
    if not os.path.isdir(path):
        return None
    snaps = sorted(f for f in os.listdir(path)
                   if f.startswith("simulate-") and f.endswith(".json"))
    times = [f[len("simulate-"):-len(".json")] for f in snaps]
    info = {
        "run_id": run_id,
        "n_snapshots": len(snaps),
        "first_time": times[0] if times else "",
        "last_time": times[-1] if times else "",
        "has_decisions": os.path.exists(os.path.join(path, "decisions.json")),
        "has_conversation": os.path.exists(os.path.join(path, "conversation.json")),
    }
    return info


@app.get("/health")
def health():
    return {"status": "ok", "case": "case00", "runs": len(_run_dirs()),
            "frozen": True, "archive_root": os.path.relpath(CKPT_ROOT, REPO_ROOT)}


@app.get("/api/runs")
def list_runs():
    runs = [s for s in (_summary(r) for r in _run_dirs()) if s]
    runs = [r for r in runs if r["n_snapshots"] > 0]
    return JSONResponse({"case": "case00", "frozen": True, "count": len(runs), "runs": runs})


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    info = _summary(run_id)
    if info is None or info["n_snapshots"] == 0:
        return JSONResponse({"ok": False, "errors": ["没有这个 run: {}".format(run_id)]}, status_code=404)
    path = os.path.join(CKPT_ROOT, run_id)
    snaps = sorted(f for f in os.listdir(path)
                   if f.startswith("simulate-") and f.endswith(".json"))
    agents = []
    try:
        with open(os.path.join(path, snaps[-1]), "r", encoding="utf-8") as f:
            snap = json.load(f)
        for name, ag in (snap.get("agents") or {}).items():
            status = ag.get("status") or {}
            agents.append({
                "name": name,
                "coord": ag.get("coord"),
                "location": ag.get("location", ""),
                "currently": ag.get("currently", ""),
                "role_type": ag.get("role_type", "user"),
                "value_tendency": status.get("value_tendency") or {},
                "action": str(ag.get("action", ""))[:120],
            })
    except Exception as exc:  # noqa: BLE001
        info["error"] = "读最后一个快照失败: {}".format(exc)
    info["agents"] = sorted(agents, key=lambda a: a["name"])
    info["snapshots"] = snaps
    return JSONResponse(info)


@app.get("/", response_class=HTMLResponse)
def index():
    runs = [s for s in (_summary(r) for r in _run_dirs()) if s and s["n_snapshots"] > 0]
    rows = []
    for r in runs:
        rows.append(
            "<li><b>{}</b> · {} 步 · {} → {}{}{}</li>".format(
                r["run_id"], r["n_snapshots"], r["first_time"], r["last_time"],
                " · decisions" if r["has_decisions"] else "",
                " · conversation" if r["has_conversation"] else "",
            )
        )
    html = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>case00 · 存档只读浏览</title>
<style>
 body {{ margin:0; font-family:"Microsoft YaHei",system-ui,sans-serif; background:#f4f6f5; color:#223; }}
 header {{ background:#1d3a2f; color:#fff; padding:10px 18px; }}
 header h1 {{ font-size:16px; margin:0; }}
 main {{ max-width:880px; margin:18px auto; padding:0 12px; }}
 .note {{ background:#fff; border:1px solid #dde4e0; border-radius:10px; padding:12px 16px; color:#456; font-size:13px; }}
 li {{ line-height:1.9; font-size:14px; }}
 code {{ background:#eef2f0; padding:1px 5px; border-radius:4px; }}
</style></head><body>
<header><h1>GTC Case 00 · 存档只读浏览</h1></header>
<main>
 <div class="note">
  case00 = 仓库原有的 6 角色投资咨询场景，<b>已冻结、后续不维护</b>。
  实时可视化仍是 <code>live_fastapi.py</code>（5001）；本服务（5003）只读
  <code>results/checkpoints/</code>，存档不复制。
 </div>
 <h2 style="font-size:14px;color:#456;">可用存档（{count} 个）</h2>
 <ul>{rows}</ul>
</main></body></html>""".format(count=len(rows), rows="".join(rows) or "<li>（空）</li>")
    return HTMLResponse(html)


def main():
    ap = argparse.ArgumentParser(description="case00 存档只读服务")
    ap.add_argument("--port", type=int, default=5003)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
