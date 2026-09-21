# -*- coding: utf-8 -*-
"""case01 成品记录的交互式审阅面板(自包含 HTML + 原生 JS + JSON API)。

做法对齐仓库里既有的嵌入面板(`live/routes.py` 的 `_render_reflections_page`):
**自包含 HTML + 原生 JS + fetch 打自己的 JSON API**,不依赖 Phaser、不需要构建步骤。

看的是 case01 的成品记录 `case01/runs/<run_id>/run.json`,分九块:
概览 / 对话(turns) / 检索(retrievals) / 事件(events) / 状态(state_history) /
反思(reflection) / 问题分流(router) / 注入器(injector) / 审计(audit)。

**它现在是"挂件",不是独立服务(2026-09-19 用户拍板:只维护一个界面)**
--------------------------------------------------------------------
面板的路由放在一个 `APIRouter` 里,由 case01 的实时面(5010,
`case01/vizkit/live_run.py`)**include 进同一个 FastAPI 应用**,于是:

- 5010 页面的右栏里多一张"结果记录"卡片(与 Chat Log 同款:
  可折叠、可单独打开) —— 小镇(过程)与九块(结果)在**一个界面**里,小镇照旧实时动;
- 不再需要单独占一个端口的 5004 面板服务(已退役,`--port` 仍可单独跑,仅供排障)。

为什么不是把九块写进 mavis-vizkit:那个包**不认识 case01 的字段**
(反思/问题分流/注入器/审计都是业务词),它只提供"调用方可以挂自己的只读面板"
这个通用口子(`extra_tabs`)。所以九块留在这里,由 case01 侧挂上去。

路由清单(相对路径,挂进哪个 app 都一样,不依赖前缀):

- `/review`            面板页(带大标题)
- `/embed/review`      嵌入面:同一页,压缩版式,贴合 iframe
- `/api/review/health` 本面板自己的健康检查(避开实时面的 `/health`)
- `/api/review/runs`   记录列表(+ 当前实跑 run_id,用于提示"这条还没成品记录")
- `/api/review/run/<id>`  单条记录全文(白名单校验,杜绝路径穿越)
- 深链参数:`?run=<run_id>` 指定默认记录、`?tab=<页签 id>` 指定默认页签、`?embed=1` 压缩版式

注意与 5002 的关系:5002(`case01/serve.py`)是**冻结合同面**(`/api/runs` 等,平台对接用),
不要往它上面加 UI;本面板是给人看的,挂在实时面上。
"""
import argparse
import json
import os

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 记录根:默认 case01/runs。可用 CASE01_REVIEW_RUNS_DIR 覆盖——测试指向入库的
# tests/fixtures/records,因为 case01/runs/ 是 gitignored、CI 的全新 checkout 里没有它。
# 这与 case01/viz.py 的 load_run(run_id, runs_dir=...) 是同一个道理。
RUNS_DIR = os.environ.get("CASE01_REVIEW_RUNS_DIR") or os.path.join(BASE_DIR, "runs")

# 当前正在实跑的 run_id(由起服务的人告知,如 case01/vizkit/live_run.py)。
# 用途只有一个:实跑还没跑完时,面板上明确写"这次还在跑/还没生成成品记录",
# 而不是让人对着旧记录猜(**不允许静默**)。
_CURRENT = {"run_id": "", "note": ""}

# 实跑期间的"实时记录"来源(2026-09-19 用户要求:小镇与结果要能同步看全程)。
# 起服务的人注入一个 provider() -> injector 原始记录;面板每 2 秒拉一次
# `/api/review/live`,用与成品记录**同一套映射**(to_case01_record)渲染九块,
# 于是角色一边动、对话/检索/事件/状态/审计一边长出来。跑完映射出成品记录后
# 面板自动切到成品那份(那时才有反思与问题分流)。
_LIVE = {"provider": None, "run_id": "", "total_nodes": 0}

router = APIRouter()


def set_current_run(run_id, note=""):
    """告知面板"当前正在实跑的 run_id"(没有就传空)。"""
    _CURRENT["run_id"] = run_id or ""
    _CURRENT["note"] = note or ""
    return _CURRENT["run_id"]


def set_live_provider(provider, run_id="", total_nodes=0):
    """注入"实时记录"来源。provider 必须是无参可调用对象,返回 injector 原始记录。

    为什么用 provider 而不是每步推一份:实跑与 HTTP 服务在**同一个进程**里
    (uvicorn 跑在插件线程),面板每 2 秒来取一次就行,不需要在引擎线程里
    维护推送节奏——少一处能出错的地方。
    """
    _LIVE["provider"] = provider
    _LIVE["run_id"] = run_id or ""
    _LIVE["total_nodes"] = int(total_nodes or 0)
    if run_id:
        set_current_run(run_id, "实跑跑完会自动映射成成品记录")
    return _LIVE["run_id"]


def clear_live():
    """实跑结束/进程退出时收干净,别让面板一直显示一个已经不动的"实时"。"""
    _LIVE["provider"] = None
    _LIVE["run_id"] = ""
    _LIVE["total_nodes"] = 0


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


@router.get("/api/review/health")
def health():
    return {"status": "ok", "service": "case01 review", "runs": len(_discover_runs())}


@router.get("/api/review/runs")
def list_runs():
    return JSONResponse({"count": len(_discover_runs()),
                         "current_run_id": _CURRENT["run_id"],
                         "current_note": _CURRENT["note"],
                         "runs": [_brief(r) for r in _discover_runs()]})


@router.get("/api/review/live")
def live_record():
    """实跑期间的**实时记录**(与成品记录同一套映射,所以九块能直接渲染)。

    面板每 2 秒来取一次;没有在实跑就返回 live=false,让面板切回成品记录。
    跑完但还没映射出成品记录时,这里仍然给得出(provider 还活着),
    面板会继续显示完整过程,并标注"反思/问题分流跑完才有"——不留空白。
    """
    provider = _LIVE["provider"]
    if provider is None:
        return JSONResponse({"ok": True, "live": False, "run_id": _LIVE["run_id"]})
    # 成品记录已经落盘(自动映射跑完了)→ 立刻报 live=false:面板会切到成品那份
    # (那时才有反思与问题分流)。不这么做的话,面板会一直显示实时那份、永远缺两块。
    if _LIVE["run_id"] and _LIVE["run_id"] in _discover_runs():
        return JSONResponse({"ok": True, "live": False, "mapped": True,
                             "run_id": _LIVE["run_id"]})
    try:
        raw = provider()
    except Exception as exc:  # noqa: BLE001 - 取不到也要让面板拿到原因,不能空着
        return JSONResponse({"ok": False, "live": True, "run_id": _LIVE["run_id"],
                             "errors": ["取实时记录失败: {}: {}".format(
                                 type(exc).__name__, exc)]})
    from .injector.record import to_case01_record
    rec = to_case01_record(raw, branch=raw.get("branch", ""),
                           run_id=raw.get("run_id", "") or _LIVE["run_id"])
    rec["live"] = True
    # 表头要显示"哪条剧情线(含义)"。branch_summary 不在记录里,是服务端推导的
    # (与 5002 契约同源),成品记录那边由 _brief 补,实时这条在这里补。
    try:
        from . import full_context as fc
        rec["branch_summary"] = fc._branch_summary(rec.get("branch", ""),
                                                   rec.get("branch_action") or {})
    except Exception:  # noqa: BLE001 - 推导失败不该让实时记录整体取不到
        rec["branch_summary"] = ""
    done_nodes = len(raw.get("nodes") or [])
    total = _LIVE["total_nodes"] or done_nodes
    return JSONResponse({"ok": True, "live": True, "run_id": rec.get("run_id", ""),
                         "done_nodes": done_nodes, "total_nodes": total,
                         "record": rec})


@router.get("/api/review/run/{run_id}")
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
  /* 中性配色:深色页头(原来是墨绿 #1d3a2f)已去掉,改成浅色页头 + 单一蓝色强调色。
     理由:这块面板要嵌进治理平台/小镇界面,深色大面积色块既不协调也难与别家风格共存;
     中性底 + 一个强调色,别人接手时改 --accent 一处即可。绿色只保留在"风险低"徽标上。 */
  :root { --ink:#1f2937; --mut:#6b7280; --line:#e5e7eb; --line-soft:#f1f3f5;
          --bg:#f6f7f9; --card:#fff; --accent:#2563eb; --accent-soft:#eef4ff;
          --low:#0f9d58; --mid:#c77700; --high:#d93025; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font-size:13px; line-height:1.6;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
         -webkit-font-smoothing:antialiased; }
  header { position:sticky; top:0; z-index:5; background:var(--card); color:var(--ink);
           border-bottom:1px solid var(--line); padding:10px 18px; display:flex; gap:14px;
           align-items:center; flex-wrap:wrap; }
  header h1 { font-size:15px; margin:0; font-weight:600; letter-spacing:.2px; }
  header select { font:inherit; font-size:13px; color:var(--ink); background:var(--card);
                  border:1px solid var(--line); border-radius:8px; padding:5px 8px; max-width:420px; }
  header select:hover { border-color:#cfd6e0; }
  header .meta { font-size:12px; color:var(--mut); margin-left:auto; min-width:0;
                 overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  header .meta a { color:var(--accent); text-decoration:none; }
  header .meta a:hover { text-decoration:underline; }
  code { background:var(--line-soft); border-radius:4px; padding:1px 5px; font-size:12px;
         font-family:ui-monospace,Consolas,monospace; }
  .note { background:#fffbeb; border:1px solid #fde68a; color:#92400e; border-radius:10px;
          padding:8px 12px; font-size:12px; margin-bottom:10px; }
  .note.ok { background:#ecfdf5; border-color:#a7f3d0; color:#065f46; }
  .wrap { max-width:1080px; margin:16px auto; padding:0 14px; display:flex; gap:16px; align-items:flex-start; }
  nav { flex:0 0 152px; background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:6px; position:sticky; top:64px; }
  nav button { display:flex; justify-content:space-between; gap:8px; width:100%; text-align:left;
               border:0; background:transparent; padding:7px 10px; border-radius:8px; cursor:pointer;
               font:inherit; font-size:13px; color:var(--ink); }
  nav button:hover { background:var(--line-soft); }
  nav button.on { background:var(--accent-soft); color:var(--accent); font-weight:600; }
  nav button span.n { color:var(--mut); font-weight:400; font-size:12px; font-variant-numeric:tabular-nums; }
  nav button.on span.n { color:var(--accent); }
  main { flex:1; min-width:0; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:14px 16px; margin-bottom:12px; }
  .card .m { color:var(--mut); font-size:12px; margin-bottom:8px; }
  .card .m b { color:var(--ink); font-weight:600; }
  .t { font-size:13.5px; line-height:1.7; white-space:pre-wrap; word-break:break-word; }
  h2 { font-size:13px; color:var(--mut); font-weight:600; margin:2px 0 12px; }
  .chips { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:8px; }
  .chip { font-size:12px; padding:2px 9px; border-radius:999px; background:var(--line-soft); color:#4b5563; }
  .chip.k { background:var(--accent-soft); color:var(--accent); }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11.5px;
           color:#fff; font-weight:500; }
  .badge.low { background:var(--low); } .badge.medium { background:var(--mid); }
  .badge.high { background:var(--high); } .badge.other { background:#9ca3af; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line-soft); vertical-align:top; }
  th { color:var(--mut); font-weight:500; font-size:12px; border-bottom-color:var(--line); }
  tbody tr:hover { background:#fafbfc; }
  .kv { display:grid; grid-template-columns:170px 1fr; gap:7px 14px; font-size:13px; }
  .kv .k { color:var(--mut); }
  details { margin-top:8px; }
  summary { cursor:pointer; font-size:12px; color:var(--accent); }
  pre { background:#fafbfc; border:1px solid var(--line); border-radius:10px; padding:12px;
        font-size:12px; line-height:1.6; overflow:auto; max-height:420px; white-space:pre-wrap;
        font-family:ui-monospace,Consolas,monospace; }
  .empty { color:#9ca3af; text-align:center; padding:28px; }
  .tl { border-left:2px solid var(--line); margin-left:6px; padding-left:14px; }
  .tl .row { position:relative; margin-bottom:10px; }
  .tl .row::before { content:""; position:absolute; left:-21px; top:6px; width:7px; height:7px;
                     border-radius:50%; background:var(--accent); }
  .bar { height:4px; border-radius:3px; background:var(--line-soft); overflow:hidden; margin-top:5px; }
  .bar i { display:block; height:100%; background:var(--accent); border-radius:3px; opacity:.9; }
  .num { font-variant-numeric:tabular-nums; }
  /* 嵌入模式(?embed=1 或 /embed/review),也是 5010 右栏卡片里那份:
     去掉大标题与页边距、页头压成两行(下拉整行 + 说明一行),贴合 iframe 尺寸。 */
  body.embed { background:#fff; }
  body.embed header { padding:9px 12px; gap:8px; position:static; }
  body.embed header h1 { display:none; }
  body.embed header select { width:100%; max-width:none; font-size:12.5px; }
  body.embed header .meta { margin-left:0; width:100%; font-size:11.5px; white-space:normal; }
  body.embed .wrap { margin:0; padding:8px 10px 12px; gap:10px; max-width:none; }
  body.embed nav { position:static; flex:0 0 132px; }
  body.embed .card { padding:11px 13px; margin-bottom:10px; }
  /* 窄容器:嵌在 5010 右栏那张卡片里时 iframe 只有 ~380px 宽。
     媒体查询量的是 iframe 自己的视口宽度,所以这里能把九块的导航改成**横排**。
     注意 min-width:0 —— flex/grid 项目的 min-width 默认是 auto(按最小内容宽),
     里面只要有一个不换行的长元素(表格/长串),整页就会被撑宽、
     文字被裁在卡片外面(实测踩过:截图里英文段落右边被切掉)。 */
  @media (max-width: 700px) {
    .wrap, nav, main, .card, .kv { min-width:0; }
    /* align-items 必须重置:外层默认 flex-start,而窄版把 .wrap 改成了**竖排**——
       竖排时 align-items 管的是宽度,flex-start 会让每块按内容宽(max-content)排,
       于是长段落不换行、被裁在卡片外(实测:英文段落右边整段被切掉)。 */
    .wrap { flex-direction:column; gap:6px; align-items:stretch; }
    /* 窄版导航改成横排的**紧凑小药丸**,并且**去掉外面那个白盒子**。
       两个坑都要显式覆盖:
       1) `body.embed nav { flex:0 0 132px }` 的特异性比 `nav` 高 —— 竖排容器里
          flex-basis 管的是**高度**,于是导航被撑成 132px 高、按钮再沿交叉轴拉伸
          ("太胖"就是这么来的)。所以要写成 `body.embed nav` 同特异性、且在后面。
       2) 拉伸之后每颗药丸都跟着变高 —— 必须 `align-items:flex-start` 让按钮回到自然高度。 */
    nav, body.embed nav {
      position:static; flex:0 0 auto; align-items:flex-start;
      display:flex; flex-wrap:wrap; gap:2px; padding:3px 2px;
      background:transparent; border:0; border-radius:0; border-bottom:1px solid var(--line);
    }
    nav button { width:auto; padding:2px 6px; font-size:12px; line-height:1.35;
                 border-radius:5px; gap:4px; color:#4b5563; }
    nav button span.n { font-size:11px; }
    nav button:hover { background:var(--line-soft); }
    .card { padding:8px 10px; }
    .kv { grid-template-columns:1fr; gap:2px 0; }
    .kv .k { font-size:12px; }
    pre { max-height:260px; }
    table { font-size:12.5px; }
    th, td { padding:5px 6px; }
    .t { font-size:13px; }
  }
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
  ["overview",   "概览",     () => null,                                  "这一次运行的来龙去脉与结果总览"],
  ["turns",      "对话",     d => (d.turns || []).length,                 "咨询者与 Investment AI 的逐轮问答"],
  ["retrievals", "检索",     d => (d.retrievals || []).length,            "当轮查到了哪些资料（以及那天有没有注入新事实）"],
  ["events",     "事件",     d => (d.events || []).length,                "剧情事件按日期释放的流水"],
  ["states",     "状态",     d => (d.state_history || []).length,         "资金、持仓、买卖价逐日快照"],
  ["reflection", "反思",     d => ((d.reflection || {}).text ? 1 : 0),    "运行后的 8 维结构化反思全文"],
  ["router",     "问题分流", d => ((d.router || {}).issues || []).length,  "从反思里拆出的带风险的行为/判断（含风险等级）"],
  ["injector",   "注入器",   d => ("injector" in d) ? (((d.injector || {}).nodes) || []).length : null,
                                                                          "注入器的节点、释放了哪些事件、事件怎么定义"],
  ["audit",      "审计",     d => (d.audit || []).length,                 "每一步操作的可审计留痕"],
];
let DATA = null, TAB = "overview";
let LIVE_HINT = "";   // 当前实跑尚无成品记录时的一句话提示(见 boot())
// 实时同步(用户要"小镇与结果同步看全程"):有实跑时下拉里多一条"● 正在跑",
// 选中它就按 2 秒拉 /api/review/live 刷新九块;跑完映射出成品记录后自动切过去。
const LIVE_ID = "__live__";
let LIVE_META = null;   // {run_id, done_nodes, total_nodes}
let liveTimer = null;
// 成品记录刚生成时的一句话(绿色) —— 跑完那一刻要有明确提示,不能悄悄换记录。
let MAPPED_NOTE = "";
// 用户手动挑了一条旧记录时,记下当时那一局的 run_id:看门狗就不再把同一局拉回实时,
// 但**新的一局**开始时照样会切过去(重开一局后要能自动跟上)。
let IGNORED_LIVE = "";

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
  const cs = d.consistency || {};
  const badge = { consistent: ["一致", "low"], inconsistent: ["不一致", "high"],
                  unknown: ["判不了", "medium"] }[cs.verdict];
  const rows = [
    ["run_id", esc(d.run_id)],
    ["引擎记录", hasInj
      ? '<span class="chip k">mavis</span>'
      : '<span class="chip">旧引擎</span>'],
    ["branch", `<span class="chip k">${esc(d.branch)}</span> ${esc(d.branch_summary || "")}`],
    ["日期区间（模拟剧情）", `<span class="num">${dash(d.start_date)} → ${dash(d.end_date)}</span>`],
    ["判定方式", dash(ba.judge)],
    // 分支来源与 T0 一致性:预设分支不看 AI 说了什么,自相矛盾的记录必须自己说出来
    // (实测 A-1720:AI 说"不能确认值得买",A 线却让当事人满仓买入)
    ["分支来源", ba.pending
      ? '<span class="chip">judge(待 T0 判定)</span> 本次由 AI 的 T0 回答决定,现在还没跑到'
      : (ba.source === "preset"
        ? '<span class="chip">实验设计预设</span> 不是由 AI 的 T0 回答判定'
        : (ba.source === "judge" ? '<span class="chip k">由 AI 回答判定</span>' : "—"))],
    ["T0 立场一致性", badge
      ? `<span class="badge ${badge[1]}">${badge[0]}</span> <span class="m">${esc(cs.reason || "")}</span>`
      : '<span class="chip">未校验</span>'],
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
  if (!r.text) {
    // 实时看全程时,反思本来就要等运行结束才有材料——一句短的说明就够(用户嫌长句子是废话)。
    return '<div class="card"><div class="empty">' +
      (d.live ? "跑完后才有" : "无反思文本") + '</div></div>';
  }
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
  if (!iss.length && d.live) {
    return '<div class="card"><div class="empty">跑完后才有</div></div>';
  }
  const styleTag = x => (x.style === "question")
    ? '<span class="chip" style="background:#fde68a;color:#92400e" title="模型写成了疑问句,不是行为/判断">仍是疑问句</span>'
    : '';
  return (iss.length ? iss.map(x => `<div class="card">
      <div class="m"><b>${esc(x.id)}</b> ${riskBadge(x.risk)} <span class="chip">${esc(x.field)}</span>${styleTag(x)}</div>
      <div class="t">${esc(x.summary)}</div>
      ${x.risk_note ? `<div class="m" style="margin-top:8px">风险 / 错在哪</div><div class="t">${esc(x.risk_note)}</div>` : ""}
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
  // 实时刷新时保住滚动位置(否则每 2 秒被拉回顶部,没法看长表格)。
  const sc = document.scrollingElement || document.documentElement;
  const y = sc ? sc.scrollTop : 0;
  document.getElementById("main").innerHTML = LIVE_HINT + (DATA
    ? PANES[TAB](DATA)
    : '<div class="card"><div class="empty">选择一次运行</div></div>');
  if (sc) sc.scrollTop = y;
  if (DATA) {
    const s = DATA.summary || {};
    const isMavis = ("injector" in DATA);
    // 引擎只用两个字,不写"（成品三线）""（无注入器段）"这种括注(用户嫌啰嗦)。
    const eng = isMavis ? "mavis" : "旧引擎";
    const bl = String(DATA.branch_summary || "").split(/[,，/]/)[0].trim();
    const tail = isMavis ? `${s.node_count ?? "—"} 节点 · ${s.elapsed_s ?? "—"} 秒` : "对照记录";
    if (DATA.live) {
      // 实时:把"跑到哪了"写在头上(**不允许静默**)。
      const prog = LIVE_META ? `${LIVE_META.done_nodes}/${LIVE_META.total_nodes} 节点` : "";
      document.getElementById("hmeta").innerHTML =
        `<span style="color:#0f9d58;font-weight:600">● 实时</span> ${esc(DATA.run_id)} · ` +
        `${esc(DATA.branch)} 线${bl ? "（" + esc(bl) + "）" : ""} · ${esc(prog)}　` +
        `<a href="/review?run=${encodeURIComponent(DATA.run_id)}" ` +
        `target="_blank">单独看这条 ↗</a>`;
      return;
    }
    document.getElementById("hmeta").innerHTML =
      `${esc(DATA.run_id)} · ${esc(eng)} · ${esc(DATA.branch)} 线${bl ? "（" + esc(bl) + "）" : ""} · ${tail}　` +
      `<a href="/api/review/run/${encodeURIComponent(DATA.run_id)}" target="_blank">原始 JSON ↗</a>`;
  }
}

async function pick(id) {
  if (id === LIVE_ID) { await loadLive(true); return; }
  const r = await fetch(`/api/review/run/${encodeURIComponent(id)}`);
  DATA = await r.json();
  render();
}

// ---- 实时记录:小镇在动,这里每 2 秒跟着长 ----
function stopLive() { if (liveTimer) { clearInterval(liveTimer); liveTimer = null; } }

// 看门狗:没在看实时的时候,也每 5 秒问一次"有没有新的一局开始了"。
// 为什么要它:用户点了"重开一局"之后,新 run_id 的实时记录会出现在 /api/review/live,
// 而面板这时正停在上一局的成品记录上、已经停止轮询 —— 不主动看就会一直显示旧的那条。
function startWatch() {
  if (liveTimer) return;
  liveTimer = setInterval(async () => {
    try {
      const d = await (await fetch("/api/review/live")).json();
      if (d && d.ok && d.live && d.run_id && d.run_id !== IGNORED_LIVE
          && (!DATA || DATA.run_id !== d.run_id)) {
        MAPPED_NOTE = `<div class="note ok">新的一局已开始(${esc(d.run_id)}),已切到实时。</div>`;
        await boot();
      }
    } catch (e) { /* 网络抖动忽略,下一次再问 */ }
  }, 5000);
}

async function loadLive(first) {
  let d;
  try {
    d = await (await fetch("/api/review/live")).json();
  } catch (e) {
    document.getElementById("hmeta").innerHTML = `<span style="color:#d93025">实时记录取不到(${esc(e.message)})</span>`;
    stopLive();
    return;
  }
  if (!d.ok) {
    document.getElementById("hmeta").innerHTML =
      `<span style="color:#d93025">实时记录取不到:${esc((d.errors || []).join(";"))}</span>`;
    return;
  }
  if (!d.live) {
    // 实跑结束了:回到列表,优先落在刚跑完那条的成品记录上。
    stopLive();
    LIVE_META = null;
    if (d.mapped) {
      // 映射刚跑完(面板自动从"实时"切到成品):**明确说一声**,别让人莫名其妙换了内容。
      MAPPED_NOTE = `<div class="note ok">成品记录已生成(含反思与问题分流),已切到这一条。</div>`;
    }
    await boot();
    return;
  }
  LIVE_META = { run_id: d.run_id, done_nodes: d.done_nodes, total_nodes: d.total_nodes };
  const rec = d.record || {};
  rec.branch_summary = rec.branch_summary || "";
  DATA = rec;
  render();
  if (first) {
    stopLive();
    liveTimer = setInterval(() => loadLive(false), 2000);
  }
}

async function boot() {
  const r = await fetch("/api/review/runs");
  const d = await r.json();
  // 正在实跑时,下拉最上面先给一条"● 正在跑" —— 用户要的就是小镇与结果同步看全程。
  let live = { live: false };
  try { live = await (await fetch("/api/review/live")).json(); } catch (e) { live = { live: false }; }
  // 正在实跑、但还没有成品记录时,面板上明确写出来(**不允许静默**):
  // 否则人对着旧记录看,会以为"这条实跑的结果丢了"。
  const cur = String(d.current_run_id || "").trim();
  const curInList = cur && (d.runs || []).some(x => x.run_id === cur);
  LIVE_HINT = MAPPED_NOTE;
  if (!LIVE_HINT && cur && !curInList && !live.live) {
    LIVE_HINT = `<div class="note">实跑 <code>${esc(cur)}</code> 尚未生成成品记录(跑完自动出现)。</div>`;
  }
  const sel = document.getElementById("pick");
  // 下拉就是一条条记录:**不要分组标题**(用户说过那种分组标题是"奇怪的无用的话";
  // 测试 test_dropdown_has_no_group_titles 会盯住页面里不出现分组元素)。
  // 记录名里已经带了引擎(branch 前那段),标签只保留"哪条线(含义) ｜ run_id"。
  const BRANCH_ORDER = { A: 0, B: 1, C: 2 };
  const ENG_ORDER = { mavis: 0, legacy: 1 };
  const shortBranch = x => String(x.branch_summary || "").split(/[,，/]/)[0].trim();
  const optLabel = x => `${x.branch || "?"} 线 · ${shortBranch(x) || "—"} ｜ ${x.run_id}`;
  const optHtml = x => `<option value="${esc(x.run_id)}">${esc(optLabel(x))}</option>`;
  const liveOpt = live.live
    ? `<option value="${LIVE_ID}">● 实时 ｜ ${esc(live.run_id)}（${live.done_nodes}/${live.total_nodes} 节点）</option>`
    : "";
  const sorted = d.runs.slice().sort((a, b) =>
    ((ENG_ORDER[a.engine] ?? 9) - (ENG_ORDER[b.engine] ?? 9)) ||
    ((BRANCH_ORDER[a.branch] ?? 9) - (BRANCH_ORDER[b.branch] ?? 9)) ||
    String(a.run_id).localeCompare(String(b.run_id)));
  sel.innerHTML = liveOpt + sorted.map(optHtml).join("");
  sel.onchange = () => {
    if (sel.value !== LIVE_ID) {
      // 手动挑旧记录:记下当前这一局,看门狗别再把它拉回去(新的一局仍会自动切)
      IGNORED_LIVE = live.run_id || "";
      stopLive();
    }
    MAPPED_NOTE = "";
    pick(sel.value);
  };
  // 有实跑就默认看实时(用户要"同步看全程");否则落在 mavis 记录上,
  // 不要落在旧引擎对照记录上——那条一打开就是"注入器:无注入器记录",最像坏了。
  // ?run= 显式指定的优先(嵌入方深链某条记录时用)。
  if (WANT_RUN) {
    const hit = d.runs.find(x => x.run_id === WANT_RUN);
    if (hit) { sel.value = WANT_RUN; await pick(WANT_RUN); return; }
    if (live.live && live.run_id === WANT_RUN) { sel.value = LIVE_ID; await loadLive(true); return; }
  }
  if (live.live) { sel.value = LIVE_ID; await loadLive(true); return; }
  const first = d.runs.find(x => x.engine === "mavis") || d.runs[0];
  if (first) { sel.value = first.run_id; await pick(first.run_id); startWatch(); }
  else {
    document.getElementById("main").innerHTML =
      '<div class="card"><div class="empty">还没有成品记录;跑一次实跑就会实时长出来，跑完自动归档。</div></div>';
    startWatch();
  }
}
boot();
</script>
</body>
</html>"""


# 两窗一页(`/combined`)已随"只维护一个界面"退役:5010 页面右栏自带
# "结果记录"卡片(与 Chat Log 同款),平台要结果只引 `/embed/review`,
# 要看"过程+结果"就引 5010 首页本身。


@router.get("/review", response_class=HTMLResponse)
def index():
    return HTMLResponse(_PAGE)


@router.get("/embed/review", response_class=HTMLResponse)
def embed_review():
    """嵌入面:与面板页同一份 HTML,靠前端识别 /embed/ 路径切到压缩版式。

    给仝牧平台 iframe 用:只要结果、不要小镇时引这一个地址即可。
    """
    return HTMLResponse(_PAGE)


def attach_to(target, current_run_id="", note=""):
    """把本面板挂到调用方的 FastAPI 应用上,返回该应用(链式)。

    case01 的实时面这样用:小镇(实时)+ 结果(九块)在**同一个服务、同一个界面**,
    不再需要单独占端口的 5004 面板服务。
    """
    if current_run_id or note:
        set_current_run(current_run_id, note)
    target.include_router(router)
    return target


def build_app():
    """独立跑法(排障/单测用):`python -m case01.review_app --port 5004`。"""
    a = FastAPI(title="GTC Case 01 · 成品记录审阅")
    a.include_router(router)

    @a.get("/health")
    def _standalone_health():
        return health()

    return a


# 模块级 app:保持既有测试与排障脚本能直接 `from case01.review_app import app`。
app = build_app()


def main():
    ap = argparse.ArgumentParser(description="case01 成品记录审阅面板(独立跑法)")
    ap.add_argument("--port", type=int, default=5004,
                    help="默认 5004;现在正常用法是挂在实时面 5010 上(见模块 docstring)")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
