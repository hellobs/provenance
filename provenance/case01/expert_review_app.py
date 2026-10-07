# -*- coding: utf-8 -*-
"""专家审核面板(参照实现):给"问题 + 专业类别"逐条下 approve/edit/reject 并写回训练线。

**为什么要有这个文件**
--------------------------------------------------------------------
审核界面按分工由平台侧(仝老师那边)重写。但我们只发规范、不发样子,对方就不知道
"这个面板原来长这样、这些字段本来有地方填"。所以这里做一份**能跑的最小参照实现**:
路由、字段、文案、以及"填了自由文本之后训练集里到底变成什么",全部是真的。
平台侧照抄交互、换掉自己的账号与库即可,不必反向猜我们的接口。

与 `/embed/review`(九块只读面)的分工:
- 九块面 = **看**(专家读一条记录的全文与证据,不写);
- 本面板 = **判**(专家对一条"问题 + 专业"下结论并给文本),写的就是 LoRA 线的标记。

路由清单(挂在 case01 实时面 5010 上,由 `review_app.attach_to` 一并 include):

- `/review/expert`  面板页(带大标题)
- `/embed/expert`   嵌入面:同一页,压缩版式,给平台 iframe
- `/api/expert/queue`   待审任务队列(由交接包的 task_candidates 展开)
- `/api/expert/read`    单条记录的专家安全全文(正文 + 自然语言 Full Context)
- `/api/expert/decision` 提交一份意见(approve/edit/reject + 自由文本)
- `/api/expert/marks`   已提交意见的**计数**,不回内容也不回结论(见下)

三条设计口径(都不是审美选择,是规范里读出来的):
1. **两套词汇在服务端桥接**。平台词 `approve/edit/reject`(《补充规范》§3)与本平台
   标记词 `correct/incorrect/partial`(`live/reflections.py`)之间只有三行映射,
   放在服务端;浏览器里不出现本平台词汇,平台侧也不用改词表。
   (不用 LLM 猜映射:那是把可确定的对应关系做成黑盒,违背"判定要能复算"。)
2. **专家身份:内部可留痕、外部不可指定**。请求体里任何 `expert_id`/`operator`/
   `origin` 一律丢弃,提交主体只取服务端观测到的连接方;取不到写 `unattributed`。
   (《补充规范》§3:79「不能信任浏览器传入的任意专家 ID」、§7:183「不能统一写常量 expert」。)
3. **首轮两份意见互不可见**。`/api/expert/marks` 只给条数与专业分布,不给结论、
   不给文本,免得后提交的人被先提交的人带偏(§3:71)。它**不是鉴权**,只是不显示;
   5010 面无鉴权这件事本身记在待拍里。
"""
import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()

# 平台词 → 本平台标记词。语义对应见 docs/给平台侧_专家意见字段与训练线映射.md §一。
VERDICT_ALIAS = {"approve": "correct", "edit": "partial", "reject": "incorrect"}
# 反方向只给队列/统计回显用,不参与判定
VERDICT_REVERSE = {v: k for k, v in VERDICT_ALIAS.items()}

MAX_QUEUE_RUNS = 30          # 队列最多扫多少条记录(每条要读一次 run.json)
MAX_TEXT_CHARS = 200_000     # 与标记端点同一条上限,超长一律拒


def _recent_runs(limit: int):
    """取最近落地的若干条记录。

    排序**按 run.json 的 mtime,不按 run_id 字典序**(实测错过一次):
    run_id 前缀不统一 —— `batch-261007-203649-arm8bC-001` 与 `probe-1003-1217`、
    `fix8b-1236` 混在一起时,字典序把 p/f 排在 b 前面,"最近十条"就成了十周前的探针。
    同一秒内落地的用 run_id 做第二关键字,保证顺序稳定可复算。
    """
    from live.history import _HIDDEN_QUALITY
    from .review_app import _discover_runs, _runs_dir
    from . import full_context as fc

    dated = []
    for run_id in _discover_runs():
        path = os.path.join(_runs_dir(), run_id, "run.json")
        try:
            dated.append((os.path.getmtime(path), run_id, path))
        except OSError:
            continue
    picked, scanned = [], 0
    for _, run_id, path in sorted(dated, key=lambda x: (-x[0], x[1])):
        if len(picked) >= max(1, min(limit, MAX_QUEUE_RUNS)):
            break
        scanned += 1
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:      # noqa: BLE001 —— 读不动的记录不进队列,计数里如实说
            continue
        # 与 5002 契约/交接包同一套默认隐藏口径(探针、调试、可疑、读不动的不派活):
        # 名单只有一份(`live.history._HIDDEN_QUALITY`),这里再写一份就一定分叉。
        if fc.quality_of(data).get("quality") in _HIDDEN_QUALITY:
            continue
        picked.append((run_id, data))
    return picked, scanned


@router.get("/api/expert/queue")
def expert_queue(limit: int = 10):
    """待审队列:每条"问题 + 专业类别"一行(《补充规范》§1.2 的任务粒度)。

    任务候选直接取交接包的实现(`review_export.build_review_package`),不在这里重写
    建单资格 —— 否则平台和面板看到的是两套判据,而它们会分叉。
    """
    from .review_export import build_review_package

    picked, scanned = _recent_runs(limit)
    tasks, triage, blocked = [], [], []
    for run_id, data in picked:
        pkg = build_review_package(data, run_id)
        for c in pkg["task_candidates"]:
            tasks.append({
                "run_id": run_id,
                "issue_id": c["issue_id"],
                "expert_category_id": c["expert_category_id"],
                "summary": c["summary"],
                "risk": c["risk"],
                "routing_reason": c["routing_reason"],
                "evidence_quote": c["evidence_quote"],
                # anchor:意见要落在原文哪几句上。构造偏好对时必填,见映射页 §三·补 ①。
                "anchor": {"sentence_ids": c["evidence_sentence_ids"],
                           "quote": c["evidence_quote"]},
            })
        for t in pkg["manual_triage"]:
            triage.append({"run_id": run_id, "issue_id": t["issue_id"],
                           "reason": t["reason"]})
        if pkg["blocked_reasons"]:
            blocked.append({"run_id": run_id, "reasons": pkg["blocked_reasons"]})
    return {"n_runs_scanned": scanned, "n_tasks": len(tasks),
            "n_manual_triage": len(triage), "n_blocked": len(blocked),
            "tasks": tasks, "manual_triage": triage, "blocked": blocked,
            "verdict_words": sorted(VERDICT_ALIAS),
            "note": "队列只到「建单候选」为止;分配两位专家、计票、争议轮由平台侧负责"}


@router.get("/api/expert/read")
def expert_read(run_id: str):
    """专家要读的正文:反思全文 + 自然语言 Full Context(走专家安全视图,不含实验元信息)。"""
    from .review_app import _runs_dir
    from .review_export import build_review_package

    if not run_id or "/" in run_id or ".." in run_id:
        return JSONResponse({"ok": False, "errors": ["run_id 非法"]}, status_code=400)
    path = os.path.join(_runs_dir(), run_id, "run.json")
    if not os.path.isfile(path):
        return JSONResponse({"ok": False, "errors": ["记录不存在"]}, status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pkg = build_review_package(data, run_id)
    if pkg["blocked_reasons"]:
        return JSONResponse({"ok": False, "blocked": pkg["blocked_reasons"],
                             "errors": ["该记录当前不可审(交接包 blocked)"]},
                            status_code=409)
    ref = (data.get("reflection") or {}).get("text") or ""
    return {"ok": True, "run_id": run_id, "reflection_text": ref,
            "reflection_chars": len(ref), "full_context": pkg["full_context"],
            "categories": [c["id"] + " " + c["name"] for c in pkg["expert_categories"]]}


# 判定门的文案是给"写接口的同学"看的,里面是本平台词汇(correct/partial/incorrect)和
# 训练侧实现细节 —— 专家看不懂(实测:截图里那句红字被当成报错)。这里只做**展示层**
# 翻译,判据本身仍在 live/reflections.mark_gate_errors 一处,不在这儿重判一遍。
GATE_WORDS = {
    "verdict=correct 不应带纠正文本(训练侧会把这段文本当标准答案);"
    "请清空文本,或改判 partial/incorrect":
        "选了「认可(approve)」就不要写文本 —— 认可的意思是这段反思原样进训练集。"
        "想改它请改选「建议修改(edit)」或「不成立(reject)」",
    "incorrect/partial 必须填写纠正文本":
        "「建议修改(edit)」和「不成立(reject)」必须写下你的文本,否则这条意见没有内容可留",
    "缺少 agent/text": "被审的这条记录里没有反思正文,没什么可判的",
    "verdict 必须是 correct/incorrect/partial 之一":
        "结论字段不认识(approve / edit / reject 三选一)",
}


def _for_expert(errs):
    """把门的文案换成专家读得懂的话;认不出的原样透出(不编造、不吞掉)。"""
    return [GATE_WORDS.get(e, e) for e in errs]


def _server_observed_subject(request: Request) -> str:
    """提交主体**只**由服务端观测得到。

    请求体里的同名字段一律不看(见模块 docstring 第 2 条)。5010 面无鉴权,所以这里
    能给的最多就是连接方地址;真身份在平台库里。取不到就写 unattributed,不编造。
    """
    peer = getattr(request, "client", None)
    host = getattr(peer, "host", "") or ""
    return "peer:" + host if host else "unattributed"


@router.post("/api/expert/decision")
async def expert_decision(request: Request):
    """一份专家意见 → 一条训练线标记。

    桥接顺序:平台词映射 → 与被审记录对身份 → 复用标记判定门(与
    `/api/reflections/mark` **同一处实现**) → 写 reflection_marks.json + 刷新 JSONL。
    """
    from live.reflections import (append_mark, mark_gate_errors, new_mark,
                                  rebuild_jsonl)

    try:
        body = await request.json()
    except Exception:      # noqa: BLE001 —— 空 body/坏 JSON 由字段校验兜住,不抛 500
        body = {}
    if not isinstance(body, dict):
        body = {}
    # 客户端不许指定的键,先摘出来(记日志用的字段名都不采信)
    dropped = [k for k in ("expert_id", "expert_ref", "operator", "origin",
                           "agent", "simulation") if body.get(k) is not None]

    word = str(body.get("verdict", "")).strip().lower()
    run_id = str(body.get("run_id", "")).strip()
    issue_id = str(body.get("issue_id", "")).strip()
    category = str(body.get("expert_category_id", "")).strip()
    text = str(body.get("text", "") or "").strip()        # 专家自由文本
    reason = str(body.get("reason", "") or "").strip()    # 为什么这么判(可空)
    anchor = body.get("anchor") if isinstance(body.get("anchor"), dict) else {}

    if word not in VERDICT_ALIAS:
        return JSONResponse({"ok": False, "errors": [
            "结论字段不认识:只能是 approve / edit / reject 三个之一"]},
            status_code=400)
    if not run_id or "/" in run_id or ".." in run_id:
        return JSONResponse({"ok": False, "errors": ["记录号(run_id)缺失或不合法"]},
                            status_code=400)
    if not issue_id or not category:
        return JSONResponse({"ok": False, "errors": ["要指明你判的是哪条问题、哪个专业"]},
                            status_code=400)
    if len(text) > MAX_TEXT_CHARS:
        return JSONResponse({"ok": False, "errors": ["文本过长(超过 20 万字),请分段"]},
                            status_code=400)

    # 被审记录:正文与主体一律从**磁盘上的记录**取,不接受调用方传入
    from .review_app import _runs_dir
    path = os.path.join(_runs_dir(), run_id, "run.json")
    if not os.path.isfile(path):
        return JSONResponse({"ok": False, "errors": ["找不到这条记录(可能刚被移走)"]},
                            status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    thought = (data.get("reflection") or {}).get("text") or ""
    if not thought.strip():
        return JSONResponse({"ok": False, "errors": ["这条记录里没有反思正文,没什么可判的"]},
                            status_code=409)
    agent = _ai_speaker(data)

    verdict = VERDICT_ALIAS[word]
    # 文本**原样**交给判定门,不在这里替专家删掉:approve 收了文本就该当面拒,
    # 而不是悄悄丢一段专家已经写下的话(实测过"切回复正确、文本框只是隐藏、值还在"
    # 这条真实路径,见 live/interventions 的同一道门)。
    correction = text
    errs = mark_gate_errors(agent, thought, verdict, correction)
    if errs:
        return JSONResponse({"ok": False, "errors": _for_expert(errs)}, status_code=400)

    prior = _prior_count(run_id, issue_id, category)
    review = {
        "run_id": run_id, "issue_id": issue_id, "expert_category_id": category,
        "verdict_platform": word, "reason": reason, "anchor": anchor,
        "version": prior + 1, "supersedes": prior and "v{}".format(prior) or "",
        "formation": str(body.get("formation", "")).strip() or "single",
        "expert_ref": _server_observed_subject(request),
    }
    if dropped:
        review["dropped_client_keys"] = dropped
    record = new_mark(agent=agent, simulation=run_id,
                      sim_time=str(data.get("end_date") or ""), node_id="",
                      thought=thought, verdict=verdict, correction=correction,
                      context={}, origin="expert", review=review,
                      operator=review["expert_ref"])
    append_mark(record)
    out = rebuild_jsonl()
    return {"ok": True, "mark": record, "export": out,
            "training_effect": _training_effect(verdict, correction, thought, anchor)}


def _ai_speaker(data: dict) -> str:
    """被反思的主体 = 记录里非 Ethan 的那个说话人。服务端推出,不接受传入。"""
    for turn in (data.get("turns") or []):
        s = str(turn.get("speaker", "")).strip()
        if s and s.lower() not in ("ethan", "user"):
            return s
    return "Investment AI"


def _prior_count(run_id: str, issue_id: str, category: str) -> int:
    """同一"问题 + 专业"已有几份意见(版本号用)。旧意见不覆盖、不删除。"""
    from live.reflections import load_marks

    n = 0
    for m in load_marks():
        rv = m.get("review") if isinstance(m.get("review"), dict) else {}
        if (rv.get("run_id"), rv.get("issue_id"), rv.get("expert_category_id")) == \
                (run_id, issue_id, category):
            n += 1
    return n


def _training_effect(verdict: str, correction: str, thought: str, anchor: dict) -> dict:
    """把"这份意见进训练集会变成什么"当场说清楚(参照实现的意义就在这儿)。

    为什么不算了就算了:实测过一条只有片段的改文 —— chosen 111~163 字对 rejected
    1591~3387 字(相似度 0.026~0.071),`lora_prep` 的量纲门会把它剔出偏好对。
    面板提前告诉专家这件事,比导出时静默少一条有用。
    """
    from case01.tools.lora_prep import validate_dpo, validate_sample
    from live.reflections import build_lora_sample

    built = build_lora_sample({"agent": "review-panel", "verdict": verdict,
                               "thought": thought, "correction": correction,
                               "context": {}})
    sft_errs = validate_sample(built["sample"])
    dpo = built.get("dpo") or {}
    dpo_errs = validate_dpo(dpo) if dpo else ["该结论不生成偏好对"]
    return {"sft_output_is": "原始反思" if verdict == "correct" else "专家文本",
            "sft_errors": sft_errs, "dpo_errors": dpo_errs,
            "anchor_required": not bool((anchor or {}).get("sentence_ids")),
            "note": "rejected 的文本是「核心逻辑为何不成立」,不是已定稿的改写;"
                    "训练侧要不要把它当 chosen 属于研究侧待拍,见映射页 §三·补"}


@router.get("/api/expert/marks")
def expert_marks():
    """只回计数。结论与文本一律不给 —— 首轮两份意见互不可见(《补充规范》§3:71)。

    ⚠ 这不是权限:5010 面无鉴权,知道 run_id 的人仍能从标记文件读到内容。
    这里做的是"界面不替你显示上一份意见",防的是无意的相互影响。
    """
    from live.reflections import load_marks

    marks = load_marks()
    by_category, by_task = {}, {}
    for m in marks:
        rv = m.get("review") if isinstance(m.get("review"), dict) else {}
        cat = rv.get("expert_category_id") or "(未标专业)"
        by_category[cat] = by_category.get(cat, 0) + 1
        key = "{}|{}".format(rv.get("run_id", "(无 run_id)"), rv.get("issue_id", "-"))
        by_task[key] = by_task.get(key, 0) + 1
    return {"total": len(marks), "with_review_block": sum(
        1 for m in marks if isinstance(m.get("review"), dict)),
        "by_category": by_category,
        "opinions_per_task": sorted(by_task.items(), key=lambda kv: -kv[1])[:20],
        "tasks_with_two_or_more": sum(1 for v in by_task.values() if v >= 2),
        "visibility": "本接口只给条数;结论与文本不在响应里"}


_EXPERT_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>专家审核面板(参照实现)</title>
<style>
  body { font: 14px/1.65 system-ui, "Microsoft YaHei", sans-serif; margin: 0;
         background: #f6f8fa; color: #1f2328; }
  header { background: #fff; border-bottom: 1px solid #d0d7de; padding: 10px 16px; }
  header h1 { margin: 0; font-size: 16px; }
  header p { margin: 4px 0 0; color: #57606a; font-size: 12px; }
  main { display: flex; gap: 12px; padding: 12px; align-items: flex-start; }
  #left { width: 400px; flex: none; display: flex; flex-direction: column; gap: 8px; }
  #filter { background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 8px 10px;
            display: flex; align-items: center; gap: 8px; font-size: 13px; }
  #filter select { font: inherit; padding: 3px 6px; border: 1px solid #d0d7de; border-radius: 6px; }
  header .how { margin: 6px 0 0; font-size: 12px; color: #57606a; }
  header a { color: #0b5cad; }
  #queue { background: #fff; border: 1px solid #d0d7de;
           border-radius: 8px; max-height: 76vh; overflow: auto; }
  #work { flex: 1; min-width: 0; background: #fff; border: 1px solid #d0d7de;
          border-radius: 8px; padding: 14px; max-height: 82vh; overflow: auto; }
  .task { padding: 9px 12px; border-bottom: 1px solid #eaeef2; cursor: pointer; }
  .task:hover { background: #f3f4f6; }
  .task.on { background: #ddf4ff; }
  .task .s { color: #57606a; font-size: 12px; }
  .risk-high { color: #cf222e; font-weight: 600; }
  .risk-medium { color: #9a6700; }
  .risk-low { color: #57606a; }
  .risktag { font-size: 11px; border: 1px solid currentColor; border-radius: 10px;
             padding: 0 6px; margin-right: 2px; }
  button { font: inherit; padding: 6px 12px; border: 1px solid #d0d7de;
           border-radius: 8px; background: #fff; cursor: pointer; margin-right: 6px; }
  button:hover { background: #f3f4f6; }
  button.sel { background: #1f883d; color: #fff; border-color: #1f883d; }
  textarea, input[type=text] { width: 100%; box-sizing: border-box; font: inherit;
           padding: 8px; border: 1px solid #d0d7de; border-radius: 8px; }
  textarea { min-height: 130px; }
  .quote { background: #fff8c5; border-left: 3px solid #d4a72c; padding: 6px 10px;
           margin: 8px 0; white-space: pre-wrap; }
  .meta { color: #57606a; font-size: 12px; }
  .err { color: #cf222e; }
  .ok { color: #1a7f37; }
  h2 { font-size: 15px; margin: 0 0 8px; }
  h3 { font-size: 14px; margin: 16px 0 6px; }
  pre { background: #f6f8fa; border: 1px solid #eaeef2; border-radius: 8px;
        padding: 10px; white-space: pre-wrap; word-break: break-word; max-height: 300px;
        overflow: auto; font-size: 12px; }
  .warn { background: #fff1e5; border: 1px solid #ffd8b5; border-radius: 8px;
          padding: 8px 10px; margin: 8px 0; font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>专家审核面板 · 参照实现</h1>
  <p>一条「问题 + 专业类别」一份意见。界面由平台侧重写,这里是<b>字段与后果的样子</b>。
     <span class="meta" id="counts"></span>
     <a href="/review" class="meta">← 回到只读面(看记录)</a></p>
  <p class="how">怎么做:<b>①</b> 左边选一条任务(可按风险高低筛) → <b>②</b> 读右边的反思正文 →
     <b>③</b> 选一个结论:认可 = 这段反思照原样用;<b>建议修改</b> = 写下你认为对的文本;
     <b>不成立</b> = 写清核心逻辑哪里错。选「认可」时文本框会锁住(写了会被拒)。</p>
</header>
<main>
  <div id="left">
    <div class="meta" id="summary">队列加载中…</div>
    <div id="filter">
      <label for="risk">按风险筛</label>
      <select id="risk">
        <option value="">全部</option>
        <option value="high">只看 high</option>
        <option value="medium">只看 medium</option>
        <option value="low">只看 low</option>
      </select>
      <span class="meta" id="riskn"></span>
    </div>
    <div id="queue"><div class="task s">队列加载中…</div></div>
  </div>
  <div id="work"><h2>选左侧一条任务</h2><div class="meta">
     队列取最近记录里可建单的候选;读正文请展开"反思全文"。</div></div>
</main>
<script>
var TASK = null, FULL = "";
function esc(s) { var d = document.createElement("div"); d.textContent = (s == null ? "" : String(s)); return d.innerHTML; }
function api(u, o) { return fetch(u, o).then(function (r) { return r.json(); }); }

var TASKS = [], RISK_ORDER = { high: 0, medium: 1, low: 2 };

api("/api/expert/queue?limit=10").then(function (q) {
  TASKS = q.tasks.slice().sort(function (a, b) {          // 高风险的排前面
    var r = RISK_ORDER[a.risk] - RISK_ORDER[b.risk];
    return r !== 0 ? r : (a.run_id < b.run_id ? 1 : -1);
  });
  document.getElementById("summary").textContent =
    "可建单任务 " + q.n_tasks + " 条 · 人工分流 " + q.n_manual_triage +
    " 条 · 不可审 " + q.n_blocked + " 条(扫了 " + q.n_runs_scanned + " 条记录)";
  if (!q.tasks.length) {
    document.getElementById("queue").innerHTML =
      "<div class='task err'>队列为空:最近的记录里没有「问题+专业」的建单候选" +
      "(看上面的人工分流与不可审计数)。</div>";
  }
  drawQueue();
  document.getElementById("risk").onchange = drawQueue;
  return api("/api/expert/marks");
}).then(function (m) {
  document.getElementById("counts").textContent = m ? (" · 已提交意见 " + m.total +
    " 份,其中 " + m.tasks_with_two_or_more + " 个任务已有两份以上") : "";
});

function drawQueue() {
  var want = document.getElementById("risk").value;
  var n = { high: 0, medium: 0, low: 0 };
  TASKS.forEach(function (t) { n[t.risk] = (n[t.risk] || 0) + 1; });
  document.getElementById("riskn").textContent =
    "high " + (n.high || 0) + " · medium " + (n.medium || 0) + " · low " + (n.low || 0);
  var box = document.getElementById("queue");
  box.innerHTML = "";
  TASKS.filter(function (t) { return !want || t.risk === want; }).forEach(function (t) {
    var d = document.createElement("div");
    d.className = "task";
    d.innerHTML = "<div><span class='risktag risk-" + esc(t.risk) + "'>" + esc(t.risk) +
                  "</span> " + esc(t.summary) + "</div><div class='s'>" +
      esc(t.expert_category_id) + " · " + esc(t.run_id) + " / " + esc(t.issue_id) + "</div>";
    d.onclick = function () { pick(t, d); };
    box.appendChild(d);
  });
  if (!box.children.length) {
    box.innerHTML = "<div class='task s'>这一档没有任务,换「全部」看看。</div>";
  }
}

function pick(t, node) {
  TASK = t;
  Array.prototype.forEach.call(document.querySelectorAll(".task.on"), function (n) { n.classList.remove("on"); });
  if (node) node.classList.add("on");
  renderForm();
  api("/api/expert/read?run_id=" + encodeURIComponent(t.run_id)).then(function (r) {
    if (!r.ok) { document.getElementById("body").innerHTML =
      "<span class='err'>" + esc((r.errors || []).join("; ")) + "</span>"; return; }
    FULL = r.reflection_text;
    document.getElementById("chars").textContent = r.reflection_chars + " 字";
    document.getElementById("body").innerHTML = "<pre>" + esc(FULL) + "</pre>" +
      "<button id='ctxbtn'>展开自然语言 Full Context</button><pre id='ctx' style='display:none'></pre>";
    document.getElementById("ctxbtn").onclick = function () {
      var p = document.getElementById("ctx");
      p.textContent = r.full_context; p.style.display = "block"; this.style.display = "none";
    };
    // 专家改文落在哪几句:面板把 anchor 原样带回去,平台侧才需要它(见映射页 §三·补 ①)
    document.getElementById("anch").textContent =
      (t.anchor.sentence_ids || []).join(", ") || "(交接包没给句子定位)";
  });
}

function renderForm() {
  var t = TASK, w = document.getElementById("work");
  w.innerHTML =
    "<h2>问题 " + esc(t.issue_id) + " · 专业 " + esc(t.expert_category_id) + "</h2>" +
    "<div>" + esc(t.summary) + "</div>" +
    "<div class='meta'>路由理由:" + esc(t.routing_reason) + "</div>" +
    "<div class='quote'>" + esc(t.evidence_quote) + "</div>" +
    "<div class='meta'>反思正文:<span id='chars'>…</span> 字 · 原文定位 <code id='anch'>…</code></div>" +
    "<div id='body'><div class='meta'>正文加载中…</div></div>" +
    "<h3>你的结论(必填)</h3>" +
    "<div>" +
      "<button data-v='approve'>认可(approve)</button>" +
      "<button data-v='edit'>建议修改(edit)</button>" +
      "<button data-v='reject'>不成立(reject)</button>" +
      "<span class='meta' id='vw'></span>" +
    "</div>" +
    "<h3 id='th'>文本(先在上方选一个结论)</h3>" +
    "<textarea id='text' disabled placeholder='选完结论这里才解锁'></textarea>" +
    "<div class='warn' id='seg' style='display:none'>你写的是<b>一段</b>而反思是整篇:导出时会被"
      + "「量纲门」剔出偏好对(chosen 与 rejected 长度差太多)。要么按整篇重写,要么接受这一条只进审核材料、不进训练集。</div>" +
    "<h3>为什么这么判(选填,不影响训练集)</h3>" +
    "<input type='text' id='reason' placeholder='一句理由即可;审核材料里会保留'>" +
    "<div style='margin-top:10px'>" +
      "<button id='send' style='background:#1f883d;color:#fff'>提交这份意见</button>" +
      "<span class='meta' id='res'></span>" +
    "</div>" +
    "<div class='meta'>提交主体由服务端记录(你填不了它);同一任务再提交会追加为第二版,旧版保留。</div>";
  Array.prototype.forEach.call(w.querySelectorAll("button[data-v]"), function (b) {
    b.onclick = function () {
      Array.prototype.forEach.call(w.querySelectorAll("button[data-v]"), function (n) { n.classList.remove("sel"); });
      b.classList.add("sel");
      t._v = b.dataset.v;
      var box = document.getElementById("text"), th = document.getElementById("th");
      var seg = document.getElementById("seg");
      // approve 时把文本框**锁住并清空**:实测过"切回认可、文本框只是隐藏、值还在"
      // 会撞上门(那条门是对的),但让专家撞见自己的残留输入是界面该负责的事。
      if (b.dataset.v === "approve") {
        box.value = ""; box.disabled = true;
        th.textContent = "文本(认可不需要写:这段反思原样进训练集)";
        seg.style.display = "none";
      } else {
        box.disabled = false;
        th.textContent = b.dataset.v === "edit" ? "你建议定稿的文本(必填)" : "为什么这条不成立(必填)";
        box.placeholder = b.dataset.v === "edit"
          ? "写你认为正确的说法。整篇重写才能生成偏好对;只写一段会被量纲门拦下"
          : "写清核心逻辑哪里错(这段文字进审核材料,不当标准答案)";
        seg.style.display = "none";
      }
      document.getElementById("vw").textContent = "已选:" + b.textContent;
    };
  });
  Array.prototype.forEach.call(w.querySelectorAll("#text"), function (box) {
    box.oninput = function () {                        // 写够一段就提醒一次量纲问题
      var seg = document.getElementById("seg");
      if (TASK && TASK._v === "edit" && FULL.length) {
        seg.style.display = box.value.length < FULL.length * 0.5 ? "block" : "none";
      }
    };
  });
  document.getElementById("send").onclick = send;
}

function send() {
  var t = TASK, res = document.getElementById("res");
  if (!t._v) { res.innerHTML = "<span class='err'>先选一个结论</span>"; return; }
  var body = { run_id: t.run_id, issue_id: t.issue_id, expert_category_id: t.expert_category_id,
               verdict: t._v, text: document.getElementById("text").value,
               reason: document.getElementById("reason").value, anchor: t.anchor };
  res.textContent = "提交中…";
  api("/api/expert/decision", { method: "POST", headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(body) }).then(function (r) {
    if (!r.ok) { res.innerHTML = "<span class='err'>" + esc((r.errors || []).join("; ")) + "</span>"; return; }
    var e = r.training_effect || {};
    res.innerHTML = "<span class='ok'>已写入第 " + r.mark.review.version + " 版</span>" +
      "<div class='meta'>训练侧取文:" + esc(e.sft_output_is || "?") +
      " · SFT 门问题 " + (e.sft_errors || []).length +
      " · 偏好对问题 " + esc((e.dpo_errors || []).join("; ")) + "</div>";
    api("/api/expert/marks").then(function (m) {
      document.getElementById("counts").textContent = " · 已提交意见 " + m.total +
        " 份,其中 " + m.tasks_with_two_or_more + " 个任务已有两份以上";
    });
  });
}
</script>
</body>
</html>"""


@router.get("/review/expert", response_class=HTMLResponse)
def expert_index():
    return HTMLResponse(_EXPERT_PAGE)


@router.get("/embed/expert", response_class=HTMLResponse)
def expert_embed():
    """嵌入面:同一页。平台 iframe 引这个地址就能拿到一份可跑的字段样例。"""
    return HTMLResponse(_EXPERT_PAGE)
