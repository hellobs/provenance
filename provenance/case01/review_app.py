# -*- coding: utf-8 -*-
"""case01 成品记录的交互式审阅面板(FastAPI,端口 5004)。

做法对齐仓库里既有的嵌入面板(`live/routes.py` 的 `_render_reflections_page`):
**自包含 HTML + 原生 JS + fetch 打自己的 JSON API**,不依赖 Phaser、不需要构建步骤。

看的是 case01 的成品记录 `case01/runs/<run_id>/run.json`,分九块:
概览 / 对话(turns) / 检索(retrievals) / 事件(events) / 状态(state_history) /
反思(reflection) / 问题分流(router) / 注入器(injector) / 审计(audit)。

本服务**只读**、不跑模拟,所以不受"同一时刻只允许一个实时可视化"的限制,可随时开着。

对外面(给仝牧平台 iframe 用):

- `/`              完整页(带大标题)
- `/embed/review`  嵌入面:同一页,压缩版式(去掉大标题与页边距),贴合 iframe 尺寸
- `/combined`      **两窗一页**:左=实时小镇(Phaser),右=本面板;整体可再被 iframe 引用,
                   所以平台插一个 iframe 就能同时拿到"过程"与"结果"
- 深链参数:`?run=<run_id>` 指定默认记录、`?tab=<页签 id>` 指定默认页签、`?embed=1` 压缩版式、
  `/combined?live=<小镇地址>` 指定小镇源(默认 case01 的实时面 5010;case00 那个小镇传 5001)

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
    # branch_summary 在 run.json 里**并不存在**——它是服务端推导的一句话。
    # 用 5002 契约(serve.py -> full_context._branch_summary)的同一个函数,两边口径就不会分叉。
    # (之前这里直接 data.get("branch_summary") 拿到 None,下拉标签里就成了 "—"。)
    from . import full_context as fc
    return {
        "run_id": run_id,
        # 引擎归属:mavis 路径的记录有 injector 段;旧引擎(archive d41cdec)没有。
        # 面板要靠它分组与选默认值,不能只靠 run_id 猜。
        "engine": "mavis" if "injector" in data else "legacy",
        "branch": data.get("branch", ""),
        "branch_summary": fc._branch_summary(data.get("branch", ""), data.get("branch_action") or {}),
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
                  padding:5px 8px; font-size:13px; max-width:560px; }
  /* 一行"说人话"的口径说明。之前没有它,而标签又是黑话("mavis · demo-A-mavis — A"),
     于是用户反馈看不懂——面板不该要求读者先读过设计文档。 */
  .legend { max-width:1080px; margin:10px auto 0; padding:0 14px; color:#5b6b66;
            font-size:12px; line-height:1.9; }
  .legend b { color:#2d6cdf; }
  .legend .br { color:#b26a00; font-weight:600; }
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
  /* 嵌入模式(?embed=1 或 /embed/review):给仝牧平台用 iframe 引用时用。
     去掉大标题与页边距、压缩表头,让面板贴合 iframe 尺寸,而不是自带一整套页面外框。 */
  body.embed { background:#fff; }
  body.embed header { padding:6px 10px; gap:10px; }
  body.embed header h1 { display:none; }
  body.embed header select { max-width:240px; font-size:12px; }
  body.embed header .meta { font-size:11px; }
  body.embed .legend { display:none; }   /* 嵌入时由平台自带上下文,省这点高度 */
  body.embed .wrap { margin:0; padding:6px 8px 10px; gap:10px; max-width:none; }
  body.embed nav { position:static; flex:0 0 132px; }
  body.embed .card { padding:9px 12px; margin-bottom:9px; }
</style>
</head>
<body>
<header>
  <h1>GTC Case 01 · 成品记录审阅</h1>
  <select id="pick"></select>
  <span class="meta" id="hmeta">加载中…</span>
</header>
<div class="legend">
  这六条记录 = <b>三条剧情线</b> × <b>两个引擎版本</b>。
  <span class="br">A 线</span>＝建议买入（当事人投入接近全部资金）；
  <span class="br">B 线</span>＝不建议买入（当事人没买）；
  <span class="br">C 线</span>＝条件化等待（按条件执行，可能一直不触发）。
  <b>mavis 新架构</b>＝当前的 case01（注入器挂在 mavis 上）；<b>旧引擎对照</b>＝归档的旧实现，留着做对照、不是废弃数据。
  左边九块依次是：概览 · 对话（逐轮问答）· 检索（当天查到什么）· 事件（剧情事件何时释放）·
  状态（资金与持仓）· 反思（8 维反思全文）· 问题分流（拆成待专家审的问题）·
  注入器（节点与事件定义）· 审计（每一步的操作留痕）。鼠标停在页签上也有说明。
  <br>注意<b>两种日期别混</b>：记录名里的日期（如 <code>live-B-20260919-1424</code>）是<b>真实运行时间</b>；
  记录里的 <code>start_date</code> → <code>end_date</code>（08-27 → 09-15）是<b>模拟剧情日期</b>，属于案例内部时间线。
  每次实跑都会生成一个带真实时间的名字，所以同一分支跑多次也能分辨。
</div>
<div class="wrap">
  <nav id="nav"></nav>
  <main id="main"></main>
</div>
<script>
const TABS = [
  ["overview",   "概览",     () => null,                                  "这一次运行的来龙去脉与结果总览"],
  ["turns",      "对话",     d => (d.turns || []).length,                 "咨询者与 Investment AI 的逐轮问答"],
  ["retrievals", "检索",     d => (d.retrievals || []).length,            "当轮查到了哪些资料（以及那天有没有注入新事实）"],
  ["events",     "事件",     d => (d.events || []).length,                "剧情事件按日期释放的流水"],
  ["states",     "状态",     d => (d.state_history || []).length,         "资金、持仓、买卖价逐日快照"],
  ["reflection", "反思",     d => ((d.reflection || {}).text ? 1 : 0),    "运行后的 8 维结构化反思全文"],
  ["router",     "问题分流", d => ((d.router || {}).issues || []).length,  "从反思里拆出的待专家审的问题（含风险等级）"],
  ["injector",   "注入器",   d => ("injector" in d) ? (((d.injector || {}).nodes) || []).length : null,
                                                                          "注入器的节点、释放了哪些事件、事件怎么定义"],
  ["audit",      "审计",     d => (d.audit || []).length,                 "每一步操作的可审计留痕"],
];
let DATA = null, TAB = "overview";

// ---- 嵌入 / 深链参数 ----
//   ?embed=1       压缩版式(去大标题与页边距),供外部平台 iframe 引用
//   /embed/review  等价于 ?embed=1
//   ?run=<run_id>  指定默认记录(不填则落在第一条 mavis 记录)
//   ?tab=<id>      指定默认页签(overview/turns/retrievals/events/states/reflection/router/injector/audit)
const Q = new URLSearchParams(location.search);
const EMBED = Q.get("embed") === "1" || location.pathname.indexOf("/embed/") === 0;
if (EMBED) { document.body.classList.add("embed"); }
const WANT_RUN = Q.get("run") || "";
const WANT_TAB = Q.get("tab") || "";
if (WANT_TAB && TABS.some(t => t[0] === WANT_TAB)) { TAB = WANT_TAB; }

const esc = s => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");

function badges(o) {
  return Object.entries(o || {}).map(([k, v]) =>
    `<span class="chip">${esc(k)} ${typeof v === "number" ? v.toFixed(3) : esc(v)}</span>`).join("");
}

function renderNav() {
  document.getElementById("nav").innerHTML = TABS.map(([id, label, count, tip]) => {
    let n = "";
    if (DATA) { const c = count(DATA); n = (c === null || c === undefined) ? "" : `<span class="n">${c}</span>`; }
    return `<button class="${id === TAB ? "on" : ""}" data-t="${id}" title="${esc(tip || "")}">${label}${n}</button>`;
  }).join("");
  document.querySelectorAll("nav button").forEach(b => b.onclick = () => { TAB = b.dataset.t; render(); });
}

function paneOverview(d) {
  // 旧引擎记录(archive d41cdec)没有 injector / summary / compat 段。
  // 之前这里无条件读它们,于是把"没有这一段"渲染成 mode= · schema= 与"节点 0 个"——
  // 那是谎报。现在按有没有 injector 分段渲染,并明说旧引擎缺哪一段。
  const hasInj = ("injector" in d);
  const s = d.summary || {}, inj = d.injector || {}, ba = d.branch_action || {}, cp = d.compat || {};
  const fb = d.final_feedback || {};
  const nodes = inj.nodes || [];
  const released = nodes.reduce((a, n) => a + ((n.released_events || []).length), 0);
  const allEvents = nodes.reduce((a, n) => a + ((n.events || []).length), 0);
  const dash = v => (v === undefined || v === null || v === "") ? "—" : esc(v);
  const rows = [
    ["run_id", esc(d.run_id)],
    ["引擎记录", hasInj
      ? '<span class="chip k">mavis 新架构（成品三线）</span>'
      : '<span class="chip">旧引擎对照 · 无注入器段</span>'],
    ["branch", `<span class="chip k">${esc(d.branch)}</span> ${esc(d.branch_summary || "")}`],
    ["日期区间", `<span class="num">${dash(d.start_date)} → ${dash(d.end_date)}</span>`],
    ["判定方式", dash(ba.judge)],
  ];
  if (hasInj) {
    rows.push(["注入器", `mode=${dash(inj.mode)} · schema=${dash(inj.schema_version)} · 角色 ${esc((inj.roles || []).join(" / ")) || "—"}`]);
    rows.push(["节点", `<span class="num">${dash(s.node_count)} 个（释放事件 ${released} 条 / 事件定义 ${allEvents} 条）</span>`]);
    rows.push(["交互", `<span class="num">interaction_started ${dash(s.interaction_started)} · 重试 ${dash(s.retries)} · 耗时 ${dash(s.elapsed_s)} 秒</span>`]);
  } else {
    rows.push(["节点 / 注入器", '该记录早于 mavis 路径，<b>没有 <code>injector</code> 与 <code>summary</code> 段</b>；不是"0 个节点"']);
  }
  if ("compat" in d) {
    rows.push(["记录完整度", `compat.level=${dash(cp.level)} · 缺键 ${((cp.missing_keys || []).length)} 个 · 反思已附 ${esc(cp.reflection_attached)}`]);
  }
  return `
  <div class="card"><h2>这一次运行</h2>
    <div class="kv">${rows.map(([k, v]) => `<div class="k">${k}</div><div>${v}</div>`).join("")}</div>
  </div>
  <div class="card"><h2>最终反馈（${esc(fb.date || "")}）</h2>
    <div class="m"><b>咨询者</b></div><div class="t">${esc(fb.ethan)}</div>
    <div class="m" style="margin-top:10px"><b>Investment AI</b></div><div class="t">${esc(fb.ai)}</div>
  </div>` + (nodes.length ? `
  <div class="card"><h2>各节点释放量</h2>
    ${nodes.map(n => {
      const rel = (n.released_events || []).length, tot = (n.events || []).length || 1;
      return `<div style="margin-bottom:8px"><div class="m num">${esc(n.node_id)} · ${esc(n.date)} · step ${esc(n.step)} —— 释放 ${rel}/${tot}</div>
        <div class="bar"><i style="width:${Math.round(rel / tot * 100)}%"></i></div></div>`;
    }).join("")}
  </div>` : "");
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
  // 两种记录形态都要认:
  //   mavis(成品): {date, mode, query, injected:[{kind,source,summary}]}
  //   旧引擎对照:  {current_date, query, hits:[{id,score,source,type,time,title}], source_stats}
  // 之前只读 mavis 那一套,旧记录的命中明细(hits/source_stats)被整个丢掉。
  const r = d.retrievals || [];
  if (!r.length) return '<div class="card"><div class="empty">无检索</div></div>';
  const dash = v => (v === undefined || v === null || v === "") ? "—" : esc(v);
  return r.map((x, i) => {
    const date = x.date || x.current_date || "";
    const head = [`#${i + 1}`];
    if (date) head.push(esc(date));
    if (x.mode) head.push("mode=" + esc(x.mode));
    let body;
    if (Array.isArray(x.injected)) {
      body = x.injected.length
        ? x.injected.map(f => `<div style="margin-bottom:6px"><span class="chip k">${esc(f.kind)}</span> <span class="t">${esc(f.summary)}</span>${f.source ? ` <span class="chip">${esc(f.source)}</span>` : ""}</div>`).join("")
        : '<div class="m">该日 <b>未注入任何事实</b>（injected 为空——这是记录内容，不是面板读不到）</div>';
    } else if (Array.isArray(x.hits)) {
      const st = x.source_stats || {};
      body = `<div class="m">旧引擎形态：命中 ${x.hits.length} 条 · 来源 ${dash(st.n_sources)} 个 · 其中二手 ${dash(st.second_hand_count)} 条</div>
        <table><thead><tr><th>时间</th><th>来源</th><th>类型</th><th>标题</th><th>相关分</th></tr></thead>
        <tbody>${x.hits.map(h => `<tr><td class="num">${esc(h.time)}</td><td>${esc(h.source)}</td>
          <td>${esc(h.type)}</td><td>${esc(h.title)}</td><td class="num">${esc(h.score)}</td></tr>`).join("")}</tbody></table>`;
    } else {
      body = '<div class="m">该记录未存检索明细</div>';
    }
    return `<div class="card">
      <div class="m">${head.join(" · ")}</div>
      <div class="t"><b>查询：</b>${esc(x.query)}</div>
      <div style="margin-top:8px">${body}</div></div>`;
  }).join("");
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
  // 空数值渲染成 "—" 而不是留白:留白看着像面板坏了,实际是"那天没有这个值"。
  const cell = v => (v === undefined || v === null || v === "") ? '<span style="color:#bbb">—</span>' : esc(v);
  return `<div class="card"><table><thead><tr>
    <th>日期</th><th>现金(元)</th><th>持 HCM</th><th>仓位</th><th>买入价</th><th>卖出价</th><th>已退出</th>
  </tr></thead><tbody>${h.map(x => { const s = x.state || {}; return `<tr>
    <td class="num">${cell(s.date || x.date)}</td><td class="num">${cell(s.cash_rmb)}</td>
    <td>${s.hcm_shares ? "是" : "否"}</td><td class="num">${cell(s.held_fraction)}</td>
    <td class="num">${cell(s.entry_price_usd)}</td><td class="num">${cell(s.exit_price_usd)}</td>
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
    const isMavis = ("injector" in DATA);
    const eng = isMavis ? "mavis 新架构" : "旧引擎对照（无注入器段）";
    const bl = String(DATA.branch_summary || "").split(/[,，/]/)[0].trim();
    const tail = isMavis ? `${s.node_count ?? "—"} 节点 · ${s.elapsed_s ?? "—"} 秒` : "对照记录";
    document.getElementById("hmeta").innerHTML =
      `${esc(DATA.run_id)} · ${esc(eng)} · ${esc(DATA.branch)} 线${bl ? "（" + esc(bl) + "）" : ""} · ${tail}　` +
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
  // 下拉按引擎**分组**,并先说人话:哪条剧情线(带含义) -> 记录 id。
  // 旧写法 "mavis · demo-A-mavis — A 建议买入…" 前缀是黑话、又被 select 宽度截掉,
  // 于是只剩看不懂的字母(用户反馈过"我看不懂")。
  const ENG_LABEL = { mavis: "mavis 新架构（成品三线）", legacy: "旧引擎对照（归档实现，留作对比）" };
  const BRANCH_ORDER = { A: 0, B: 1, C: 2 };
  const shortBranch = x => String(x.branch_summary || "").split(/[,，/]/)[0].trim();
  const optLabel = x => `${x.branch || "?"} 线 · ${shortBranch(x) || "—"} ｜ ${x.run_id}`;
  const optHtml = x => `<option value="${esc(x.run_id)}">${esc(optLabel(x))}</option>`;
  sel.innerHTML = ["mavis", "legacy"].map(eng => {
    const items = d.runs.filter(x => x.engine === eng)
                       .sort((a, b) => (BRANCH_ORDER[a.branch] ?? 9) - (BRANCH_ORDER[b.branch] ?? 9));
    if (!items.length) return "";
    return `<optgroup label="${esc(ENG_LABEL[eng] || eng)}">${items.map(optHtml).join("")}</optgroup>`;
  }).join("");
  sel.onchange = () => pick(sel.value);
  // 默认落在成品三线(mavis)上,不要落在旧引擎对照记录上——
  // 否则一打开看到的就是"注入器:无注入器记录"那条,最容易让人以为面板坏了。
  // ?run= 显式指定的优先(嵌入方深链某条记录时用)。
  const first = (WANT_RUN && d.runs.find(x => x.run_id === WANT_RUN))
             || d.runs.find(x => x.engine === "mavis") || d.runs[0];
  if (first) { await pick(first.run_id); }
  else { document.getElementById("main").innerHTML = '<div class="card"><div class="empty">记录根下没有 run.json</div></div>'; }
}
boot();
</script>
</body>
</html>"""


# 两窗一页:左=实时小镇(Phaser),右=成品记录审阅。整体再被 iframe 引用也成立,
# 所以仝牧平台插**一个** iframe 就能同时拿到"过程"与"结果"两个窗口。
_DEFAULT_LIVE_TOWN = (os.environ.get("CASE01_LIVE_TOWN_URL")
                      or "http://127.0.0.1:5010/embed/scene")

_COMBINED = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>case01 · 实时小镇 + 成品记录</title>
<style>
  html, body { height:100%; margin:0; font-family:"Microsoft YaHei",system-ui,sans-serif; }
  body { background:#0f1a16; }
  .bar { height:32px; display:flex; align-items:center; gap:10px; padding:0 12px;
         background:#1d3a2f; color:#cfe3d8; font-size:12px; }
  .bar b { color:#fff; }
  .bar .sp { margin-left:auto; }
  .bar a { color:#cfe3d8; }
  .split { display:flex; height:calc(100% - 32px); gap:6px; padding:6px; box-sizing:border-box; }
  .pane { flex:1 1 50%; min-width:0; background:#fff; border:1px solid #2a4a3c;
          border-radius:8px; overflow:hidden; display:flex; flex-direction:column; }
  .pane h2 { margin:0; padding:6px 10px; font-size:12px; font-weight:500; color:#456;
             background:#f4f6f5; border-bottom:1px solid #dde4e0; }
  .pane iframe { flex:1; width:100%; border:0; }
</style>
</head>
<body>
<div class="bar"><b>GTC Case 01</b> · 实时小镇（Phaser）＋ 成品记录审阅
  <span class="sp">小镇源 <code>__LIVE__</code>｜同一时刻只应有一个实时面在跑｜
  <a href="/embed/review" target="_blank" rel="noreferrer">单独打开结果窗 ↗</a></span></div>
<div class="split">
  <div class="pane">
    <h2>① 实时小镇（Phaser，mavis 推演）</h2>
    <iframe src="__LIVE__" title="实时小镇"></iframe>
  </div>
  <div class="pane">
    <h2>② 成品记录（Reflection / Router / 注入器）</h2>
    <iframe src="/embed/review" title="成品记录审阅"></iframe>
  </div>
</div>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_PAGE)


@app.get("/embed/review", response_class=HTMLResponse)
def embed_review():
    """嵌入面:与 / 同一页,靠前端识别 /embed/ 路径切到压缩版式。

    单独做这一个路由(而不是把面板塞进 5002)是为了不污染冻结的对接契约;
    本服务只读、不跑模拟,所以随时可以开着。
    """
    return HTMLResponse(_PAGE)


@app.get("/combined", response_class=HTMLResponse)
def combined(live: str = ""):
    """实时小镇 + 成品记录,两窗一页;整体可再被 iframe 引用(给仝牧平台用)。

    ?live= 指定小镇地址(默认 case01 的实时面 5010/embed/scene;
    要看 case00 那个小镇就传 http://127.0.0.1:5001/embed/scene)。
    本页只负责把给定地址嵌进来,**不负责起服务**——"任何时刻只允许一个实时面在跑"
    这条规则由起服务的人守(见 docs/case00_case01_并列说明.md 第三节)。
    """
    url = (live or "").strip() or _DEFAULT_LIVE_TOWN
    if not (url.startswith("http://") or url.startswith("https://")):
        url = _DEFAULT_LIVE_TOWN          # 只接受 http(s),别的一律回落到默认
    return HTMLResponse(_COMBINED.replace("__LIVE__", url))


def main():
    ap = argparse.ArgumentParser(description="case01 成品记录审阅面板")
    ap.add_argument("--port", type=int, default=5004)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
