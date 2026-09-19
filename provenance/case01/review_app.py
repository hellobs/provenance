# -*- coding: utf-8 -*-
"""case01 成品记录的交互式审阅面板(FastAPI,端口 5004)。

做法对齐仓库里既有的嵌入面板(`live/routes.py` 的 `_render_reflections_page`):
**自包含 HTML + 原生 JS + fetch 打自己的 JSON API**,不依赖 Phaser、不需要构建步骤。

看的是 case01 的成品记录 `case01/runs/<run_id>/run.json`,分九块:
概览 / 对话(turns) / 检索(retrievals) / 事件(events) / 状态(state_history) /
反思(reflection) / 问题分流(router) / 注入器(injector) / 审计(audit)。

本服务**只读**、不跑模拟,所以不受"同一时刻只允许一个实时可视化"的限制,可随时开着。

注意与 5002 的关系:5002(`case01/serve.py`)是**冻结合同面**(`/api/runs` 等,平台对接用),
不要往它上面加 UI;审阅面独立成本服务,免得污染契约。
"""
import argparse
import json
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 记录根:默认 case01/runs。可用 CASE01_REVIEW_RUNS_DIR 覆盖——测试指向入库的
# tests/fixtures/records,因为 case01/runs/ 是 gitignored、CI 的全新 checkout 里没有它。
# 这与 case01/viz.py 的 load_run(run_id, runs_dir=...) 是同一个道理。
RUNS_DIR = os.environ.get("CASE01_REVIEW_RUNS_DIR") or os.path.join(BASE_DIR, "runs")

app = FastAPI(title="GTC Case 01 · 成品记录审阅")


def _runs_dir():
    """每次调用都读模块级 RUNS_DIR,便于测试 monkeypatch。"""
    return RUNS_DIR


def _discover_runs():
    """列出记录根下所有带 run.json 的 run。"""
    if not os.path.isdir(_runs_dir()):
        return []
    out = []
    for name in sorted(os.listdir(_runs_dir())):
        path = os.path.join(_runs_dir(), name, "run.json")
        if os.path.isfile(path):
            out.append(name)
    return out


def _brief(run_id):
    """列表用的轻量摘要(只读该 run.json,不做全量返回)。"""
    path = os.path.join(_runs_dir(), run_id, "run.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:  # noqa: BLE001
        return {"run_id": run_id, "error": "读 run.json 失败: {}".format(exc)}
    router = data.get("router") or {}
    return {
        "run_id": run_id,
        "branch": data.get("branch", ""),
        "branch_summary": data.get("branch_summary", ""),
        "start_date": data.get("start_date", ""),
        "end_date": data.get("end_date", ""),
        "n_turns": len(data.get("turns") or []),
        "n_retrievals": len(data.get("retrievals") or []),
        "n_events": len(data.get("events") or []),
        "n_issues": len(router.get("issues") or []),
        "has_reflection": bool((data.get("reflection") or {}).get("text")),
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "case01 review", "runs": len(_discover_runs())}


@app.get("/api/review/runs")
def list_runs():
    return JSONResponse({"count": len(_discover_runs()),
                         "runs": [_brief(r) for r in _discover_runs()]})


@app.get("/api/review/run/{run_id}")
def get_run(run_id: str):
    # 白名单校验:run_id 只能来自已发现的 run,杜绝路径穿越
    if run_id not in _discover_runs():
        return JSONResponse({"ok": False, "errors": ["没有这个 run: {}".format(run_id)]},
                            status_code=404)
    with open(os.path.join(_runs_dir(), run_id, "run.json"), "r", encoding="utf-8") as f:
        return JSONResponse(json.load(f))


_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>GTC Case 01 · 成品记录审阅</title>
<style>
  :root { --ink:#223; --mut:#778; --line:#dde4e0; --bg:#f4f6f5; --card:#fff;
          --accent:#2d6cdf; --green:#1d3a2f; --low:#2f9e44; --mid:#b26a00; --high:#c92a2a; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:"Microsoft YaHei",system-ui,sans-serif; background:var(--bg); color:var(--ink); }
  header { background:var(--green); color:#fff; padding:10px 18px; display:flex; gap:16px;
           align-items:center; flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  header select { background:#26493b; color:#fff; border:1px solid #3c6353; border-radius:6px;
                  padding:5px 8px; font-size:13px; max-width:300px; }
  header .meta { font-size:12px; color:#cfe3d8; margin-left:auto; }
  header a { color:#cfe3d8; font-size:12px; }
  .wrap { max-width:1080px; margin:16px auto; padding:0 14px; display:flex; gap:16px; align-items:flex-start; }
  nav { flex:0 0 148px; background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:8px; position:sticky; top:12px; }
  nav button { display:flex; justify-content:space-between; width:100%; text-align:left; border:0;
               background:transparent; padding:7px 9px; border-radius:7px; cursor:pointer;
               font-size:13px; color:var(--ink); font-family:inherit; }
  nav button:hover { background:#eef2f0; }
  nav button.on { background:#e7efff; color:var(--accent); font-weight:600; }
  nav button span.n { color:var(--mut); font-weight:400; font-size:12px; }
  main { flex:1; min-width:0; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:12px 16px; margin-bottom:12px; }
  .card .m { color:var(--mut); font-size:12px; margin-bottom:6px; }
  .card .m b { color:var(--accent); }
  .t { font-size:14px; line-height:1.6; white-space:pre-wrap; word-break:break-word; }
  h2 { font-size:14px; color:#456; margin:6px 0 10px; }
  .chips { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:8px; }
  .chip { font-size:12px; padding:2px 8px; border-radius:10px; background:#eef2f0; color:#456; }
  .chip.k { background:#e7efff; color:var(--accent); }
  .badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:12px; color:#fff; }
  .badge.low { background:var(--low); } .badge.medium { background:var(--mid); }
  .badge.high { background:var(--high); } .badge.other { background:#889; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--mut); font-weight:500; font-size:12px; }
  .kv { display:grid; grid-template-columns:150px 1fr; gap:6px 12px; font-size:13px; }
  .kv .k { color:var(--mut); }
  details { margin-top:8px; }
  summary { cursor:pointer; font-size:12px; color:var(--accent); }
  pre { background:#f7f9f8; border:1px solid var(--line); border-radius:8px; padding:10px;
        font-size:12px; line-height:1.5; overflow:auto; max-height:420px; white-space:pre-wrap; }
  .empty { color:#99a; text-align:center; padding:26px; }
  .tl { border-left:2px solid var(--line); margin-left:6px; padding-left:14px; }
  .tl .row { position:relative; margin-bottom:10px; }
  .tl .row::before { content:""; position:absolute; left:-21px; top:5px; width:8px; height:8px;
                     border-radius:50%; background:var(--accent); }
  .bar { height:6px; border-radius:3px; background:#eef2f0; overflow:hidden; margin-top:4px; }
  .bar i { display:block; height:100%; background:var(--accent); }
  .num { font-variant-numeric:tabular-nums; }
</style>
</head>
<body>
<header>
  <h1>GTC Case 01 · 成品记录审阅</h1>
  <select id="pick"></select>
  <span class="meta" id="hmeta">加载中…</span>
</header>
<div class="wrap">
  <nav id="nav"></nav>
  <main id="main"></main>
</div>
<script>
const TABS = [
  ["overview",   "概览",     () => null],
  ["turns",      "对话",     d => (d.turns || []).length],
  ["retrievals", "检索",     d => (d.retrievals || []).length],
  ["events",     "事件",     d => (d.events || []).length],
  ["states",     "状态",     d => (d.state_history || []).length],
  ["reflection", "反思",     d => ((d.reflection || {}).text ? 1 : 0)],
  ["router",     "问题分流", d => ((d.router || {}).issues || []).length],
  ["injector",   "注入器",   d => (((d.injector || {}).nodes) || []).length],
  ["audit",      "审计",     d => (d.audit || []).length],
];
let DATA = null, TAB = "overview";

const esc = s => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");

function badges(o) {
  return Object.entries(o || {}).map(([k, v]) =>
    `<span class="chip">${esc(k)} ${typeof v === "number" ? v.toFixed(3) : esc(v)}</span>`).join("");
}

function renderNav() {
  document.getElementById("nav").innerHTML = TABS.map(([id, label, count]) => {
    let n = "";
    if (DATA) { const c = count(DATA); n = (c === null || c === undefined) ? "" : `<span class="n">${c}</span>`; }
    return `<button class="${id === TAB ? "on" : ""}" data-t="${id}">${label}${n}</button>`;
  }).join("");
  document.querySelectorAll("nav button").forEach(b => b.onclick = () => { TAB = b.dataset.t; render(); });
}

function paneOverview(d) {
  const s = d.summary || {}, inj = d.injector || {}, ba = d.branch_action || {}, cp = d.compat || {};
  const fb = d.final_feedback || {};
  const nodes = inj.nodes || [];
  const released = nodes.reduce((a, n) => a + ((n.released_events || []).length), 0);
  const allEvents = nodes.reduce((a, n) => a + ((n.events || []).length), 0);
  return `
  <div class="card"><h2>这一次运行</h2>
    <div class="kv">
      <div class="k">run_id</div><div>${esc(d.run_id)}</div>
      <div class="k">branch</div><div><span class="chip k">${esc(d.branch)}</span> ${esc(d.branch_summary || "")}</div>
      <div class="k">日期区间</div><div class="num">${esc(d.start_date)} → ${esc(d.end_date)}</div>
      <div class="k">判定方式</div><div>${esc(ba.judge || "")}</div>
      <div class="k">注入器</div><div>mode=${esc(inj.mode)} · schema=${esc(inj.schema_version)} · 角色 ${esc((inj.roles || []).join(" / "))}</div>
      <div class="k">节点</div><div class="num">${s.node_count || 0} 个（释放事件 ${released} 条 / 事件定义 ${allEvents} 条）</div>
      <div class="k">交互</div><div class="num">interaction_started ${s.interaction_started || 0} · 重试 ${s.retries || 0} · 耗时 ${s.elapsed_s || 0} 秒</div>
      <div class="k">记录完整度</div><div>compat.level=${esc(cp.level)} · 缺键 ${((cp.missing_keys || []).length)} 个 · 反思已附 ${esc(cp.reflection_attached)}</div>
    </div>
  </div>
  <div class="card"><h2>最终反馈（${esc(fb.date || "")}）</h2>
    <div class="m"><b>咨询者</b></div><div class="t">${esc(fb.ethan)}</div>
    <div class="m" style="margin-top:10px"><b>Investment AI</b></div><div class="t">${esc(fb.ai)}</div>
  </div>
  <div class="card"><h2>各节点释放量</h2>
    ${nodes.map(n => {
      const rel = (n.released_events || []).length, tot = (n.events || []).length || 1;
      return `<div style="margin-bottom:8px"><div class="m num">${esc(n.node_id)} · ${esc(n.date)} · step ${esc(n.step)} —— 释放 ${rel}/${tot}</div>
        <div class="bar"><i style="width:${Math.round(rel / tot * 100)}%"></i></div></div>`;
    }).join("") || '<div class="empty">无节点</div>'}
  </div>`;
}

function paneTurns(d) {
  const t = d.turns || [];
  if (!t.length) return '<div class="card"><div class="empty">无对话</div></div>';
  const who = s => /ai|invest/i.test(s) ? "AI" : "咨询者";
  return t.map(x => `<div class="card">
    <div class="m"><b>${esc(who(x.speaker || ""))}</b> · ${esc(x.speaker)} · ${esc(x.date)}</div>
    <div class="t">${esc(x.text)}</div></div>`).join("");
}

function paneRetrievals(d) {
  const r = d.retrievals || [];
  if (!r.length) return '<div class="card"><div class="empty">无检索</div></div>';
  return r.map((x, i) => `<div class="card">
    <div class="m">#${i + 1} · ${esc(x.date)} · mode=${esc(x.mode)}</div>
    <div class="t"><b>查询：</b>${esc(x.query)}</div>
    <div style="margin-top:8px">${(x.injected || []).map(f => `<div style="margin-bottom:6px">
      <span class="chip k">${esc(f.kind)}</span> <span class="t">${esc(f.summary)}</span></div>`).join("")}</div>
  </div>`).join("");
}

function paneEvents(d) {
  const e = d.events || [];
  if (!e.length) return '<div class="card"><div class="empty">无事件</div></div>';
  return `<div class="card"><div class="tl">${e.map(x => `<div class="row">
    <div class="m"><span class="chip k">${esc(x.kind)}</span> ${esc(x.date)}</div>
    <div class="t">${esc(x.summary)}</div></div>`).join("")}</div></div>`;
}

function paneStates(d) {
  const h = d.state_history || [];
  if (!h.length) return '<div class="card"><div class="empty">无状态历史</div></div>';
  return `<div class="card"><table><thead><tr>
    <th>日期</th><th>现金(元)</th><th>持 HCM</th><th>仓位</th><th>买入价</th><th>卖出价</th><th>已退出</th>
  </tr></thead><tbody>${h.map(x => { const s = x.state || {}; return `<tr>
    <td class="num">${esc(s.date || x.date)}</td><td class="num">${esc(s.cash_rmb)}</td>
    <td>${s.hcm_shares ? "是" : "否"}</td><td class="num">${esc(s.held_fraction)}</td>
    <td class="num">${esc(s.entry_price_usd)}</td><td class="num">${esc(s.exit_price_usd)}</td>
    <td>${s.exited ? "是" : "否"}</td></tr>`; }).join("")}</tbody></table></div>`;
}

function paneReflection(d) {
  const r = d.reflection || {};
  if (!r.text) return '<div class="card"><div class="empty">无反思文本</div></div>';
  return `<div class="card"><div class="m">反思文本 · ${r.text.length} 字</div>
    <div class="t">${esc(r.text)}</div>
    <details><summary>展开交给模型的原始材料（${(r.material || "").length} 字）</summary>
      <pre>${esc(r.material)}</pre></details></div>`;
}

function riskBadge(v) {
  const k = String(v || "").toLowerCase();
  const cls = ["low", "medium", "high"].includes(k) ? k : "other";
  return `<span class="badge ${cls}">${esc(v)}</span>`;
}

function paneRouter(d) {
  const r = d.router || {}, iss = r.issues || [];
  let raw = r.raw; try { raw = JSON.stringify(JSON.parse(raw), null, 2); } catch (e) {}
  return (iss.length ? iss.map(x => `<div class="card">
      <div class="m"><b>${esc(x.id)}</b> ${riskBadge(x.risk)} <span class="chip">${esc(x.field)}</span></div>
      <div class="t">${esc(x.summary)}</div>
      <div class="m" style="margin-top:8px">分流理由</div><div class="t">${esc(x.routing_reason)}</div>
    </div>`).join("") : '<div class="card"><div class="empty">无分流问题</div></div>')
    + `<div class="card"><details><summary>展开模型原始输出（注意：这里 risk 是首字母大写，上面徽标用的是归一化后的小写）</summary>
       <pre>${esc(raw)}</pre></details></div>`;
}

function paneInjector(d) {
  const inj = d.injector || {}, nodes = inj.nodes || [];
  if (!nodes.length) return '<div class="card"><div class="empty">无注入器记录</div></div>';
  return nodes.map(n => `<div class="card">
    <div class="m"><b>${esc(n.node_id)}</b> · ${esc(n.date)} · step ${esc(n.step)}
      · 释放 ${(n.released_events || []).length} / 定义 ${(n.events || []).length}</div>
    <div class="chips">${(n.released_events || []).map(id => `<span class="chip k">${esc(id)}</span>`).join("")}</div>
    <table><thead><tr><th>id</th><th>时间</th><th>类型</th><th>重要性</th><th>对象</th><th>内容</th></tr></thead>
    <tbody>${(n.events || []).map(e => `<tr>
      <td class="num">${esc(e.id)}</td><td class="num">${esc(e.time)}</td><td>${esc(e.event_type)}</td>
      <td class="num">${esc(e.importance)}</td><td>${esc((e.targets || []).join(", "))}</td>
      <td>${esc(e.content)}</td></tr>`).join("")}</tbody></table>
  </div>`).join("");
}

function paneAudit(d) {
  const a = d.audit || [];
  if (!a.length) return '<div class="card"><div class="empty">无审计记录</div></div>';
  return `<div class="card"><table><thead><tr><th>时间</th><th>动作</th><th>类型</th><th>摘要</th></tr></thead>
    <tbody>${a.map(x => `<tr><td class="num">${esc(x.t)}</td><td>${esc(x.action)}</td>
      <td>${esc(x.kind)}</td><td>${esc(x.summary)}</td></tr>`).join("")}</tbody></table></div>`;
}

const PANES = { overview: paneOverview, turns: paneTurns, retrievals: paneRetrievals,
                events: paneEvents, states: paneStates, reflection: paneReflection,
                router: paneRouter, injector: paneInjector, audit: paneAudit };

function render() {
  renderNav();
  document.getElementById("main").innerHTML = DATA
    ? PANES[TAB](DATA)
    : '<div class="card"><div class="empty">选择一次运行</div></div>';
  if (DATA) {
    const s = DATA.summary || {};
    document.getElementById("hmeta").innerHTML =
      `${esc(DATA.run_id)} · branch ${esc(DATA.branch)} · ${s.node_count || 0} 节点 · ${s.elapsed_s || 0} 秒　` +
      `<a href="/api/review/run/${encodeURIComponent(DATA.run_id)}" target="_blank">原始 JSON ↗</a>`;
  }
}

async function pick(id) {
  const r = await fetch(`/api/review/run/${encodeURIComponent(id)}`);
  DATA = await r.json();
  render();
}

async function boot() {
  const r = await fetch("/api/review/runs");
  const d = await r.json();
  const sel = document.getElementById("pick");
  sel.innerHTML = d.runs.map(x =>
    `<option value="${esc(x.run_id)}">${esc(x.run_id)} — ${esc(x.branch || "")} ${esc((x.branch_summary || "").slice(0, 18))}</option>`).join("");
  sel.onchange = () => pick(sel.value);
  if (d.runs.length) { await pick(d.runs[0].run_id); }
  else { document.getElementById("main").innerHTML = '<div class="card"><div class="empty">case01/runs 下没有 run.json</div></div>'; }
}
boot();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_PAGE)


def main():
    ap = argparse.ArgumentParser(description="case01 成品记录审阅面板")
    ap.add_argument("--port", type=int, default=5004)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
