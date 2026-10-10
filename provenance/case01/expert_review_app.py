# -*- coding: utf-8 -*-
"""专家审核面板(参照实现):给"问题 + 专业类别"逐条下 approve/edit/reject 并写回训练线。

**为什么要有这个文件**
--------------------------------------------------------------------
审核界面按分工由平台侧重写。但我们只发规范、不发样子,对方就不知道
"这个面板原来长这样、这些字段本来有地方填"。所以这里做一份**能跑的最小参照实现**:
路由、字段、文案、以及"填了自由文本之后训练集里到底变成什么",全部是真的。
平台侧照抄交互、换掉自己的账号与库即可,不必反向猜我们的接口。

与 `/embed/review`(九块只读面)的分工:
- 九块面 = **看**(专家读一条记录的全文与证据,不写);
- 本面板 = **判**(专家对一条"问题 + 专业"下结论并给文本),写的就是 LoRA 线的标记。

路由清单(挂在 case01 实时面 5010 上,由 `review_app.attach_to` 一并 include):

- `/review/expert`  面板页(全页版式,带大标题与"← back to the read-only view")
- `/embed/expert`   **嵌入版式**:同一份 HTML,前端按 `/embed/` 路径(或 `?embed=1`)切
                    `body.embed` —— 去掉大标题与本页自己的返回链接(2026-10-09 补;
                    此前它只是别名,嵌进宿主 iframe 后点"← back to the read-only view"会整页跳走、
                    回不到原记录,见 `review_app` 的 `body.embed .expertlink{display:none}`)。
- 深链参数:`?run=<run_id>` 进来直接定位那条记录的第一条任务(只读面点过来时带上);
                    `?from=review|embed` 决定"← back to the read-only view"指回哪一面的原记录。
- `/api/expert/queue`   待审任务队列(由交接包的 task_candidates 展开;除最近若干条外,
                    「已经有人评过」的记录(等第二份 / 已评满 / 争议轮)强制进队列并按状态排序,见 `_recent_runs`)
- `/api/expert/read`    单条记录的专家安全全文(正文 + 自然语言 Full Context)
- `/api/expert/decision` 提交一份意见(approve/edit/reject + 自由文本)
- `/api/expert/marks`   已提交意见的**计数**,不回内容也不回结论(见下)。
                    "几份"按提交主体折、来源非专家的不算,不计数的行在 `excluded` 里
                    分类报数(口径见 `_opinion_tally` 与 `COUNTING_RULE`)
- `/api/expert/audit-trail` 按 Run 回溯整条处理链(Reflection → Router → 分配 → 审核 →
                    冲突 → 最终结果 → 进池),把三处产物按时间合并;平台侧负责的几步如实
                    标 `platform_side`,取不到的标 `missing`,不编造(05 §五 / 01 §十三)

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


def _recent_runs(limit: int, must_include=()):
    """取最近落地的若干条记录,**外加必须进队列的那几条**(不受 mtime 窗口影响)。

    为什么要 `must_include`:这函数原本只按 mtime 取最近 N 条,于是**已经有人评过、但记录
    变旧了的**那些会整条从队列里消失 —— 而 04 §八 要的恰恰是这些:等第二份的那条得有人
    接着评,评满/评崩的那两条统筹要能回头核对。实测过两次:先是全库唯一一份带任务坐标的
    意见挂在 `batch-261007-212235-sample8b15-009`(面板上看不出任何欠账),后来它攒够两份
    之后又因为"只强制 n==1"而从视野里消失。所以强制进来的是**任何有意见的记录**。

    排序**按 run.json 的 mtime,不按 run_id 字典序**(实测错过一次):
    run_id 前缀不统一 —— `batch-261007-203649-arm8bC-001` 与 `probe-1003-1217`、
    `fix8b-1236` 混在一起时,字典序把 p/f 排在 b 前面,"最近十条"就成了十周前的探针。
    同一秒内落地的用 run_id 做第二关键字,保证顺序稳定可复算。
    """
    from live.history import _HIDDEN_QUALITY
    from .review_app import _discover_runs, _runs_dir
    from . import full_context as fc

    by_run = {}
    for run_id in _discover_runs():
        path = os.path.join(_runs_dir(), run_id, "run.json")
        try:
            by_run[run_id] = (os.path.getmtime(path), path)
        except OSError:
            continue
    order = sorted(by_run, key=lambda r: (-by_run[r][0], r))
    window = order[:max(1, min(limit, MAX_QUEUE_RUNS))]
    # 欠第二份的记录排在窗口前面,并且总数一起受 MAX_QUEUE_RUNS 限(每条要读一次 run.json)
    forced = [r for r in must_include if r in by_run and r not in window]
    queue_ids = forced + window
    picked, scanned = [], 0
    for run_id in queue_ids[:MAX_QUEUE_RUNS]:
        scanned += 1
        try:
            with open(by_run[run_id][1], "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:      # noqa: BLE001 —— 读不动的记录不进队列,计数里如实说
            continue
        # 与 5002 契约/交接包同一套默认隐藏口径(探针、调试、可疑、读不动的不派活):
        # 名单只有一份(`live.history._HIDDEN_QUALITY`),这里再写一份就一定分叉。
        if fc.quality_of(data).get("quality") in _HIDDEN_QUALITY:
            continue
        picked.append((run_id, data))
    # 第三个返回值是"这个库里一共有多少条成品记录" —— 队列只扫窗口内那些,
    # 调用方要能算出"这次没读到多少条"(见 expert_queue 的 records_not_scanned)。
    return picked, scanned, len(by_run)


COUNTING_RULE = ("one opinion = the latest version of one review.expert_ref on that task,"
                 " with origin=expert (a missing origin counts as expert); the cases that"
                 " are not counted are reported in excluded (missing task coordinates /"
                 " origin explicitly non-expert / superseded by a newer version from the"
                 " same subject); see `_opinion_tally` on the server for the reasoning")


def _opinion_tally(marks):
    """把盘上的标记行折成"每个任务有几份意见",并**报出三类不计数的情形**(不静默少算)。

    写成一处是因为队列和 `/api/expert/marks` 以前各算各的:两份字典键形状不一致就
    造出过 `tasks_disputed` 恒为 0 的假零(见 `expert_marks`)。

    两条折算是照 2026-10-09 的台账逐行读出来的(全库 6 行的 origin/version/supersedes
    都点过),不是凭口径设计:
    1. 显式 `origin != "expert"` 的行不算意见(缺 `origin` 按 `expert` 算,与《字段映射》
       §二 的"缺省 expert"一致)。10-07 那条面板自测已被改名 `origin=probe`(导出侧来源门
       同样拒收),把它当"第一位专家已交"会让任务凭空显示成"首轮齐"。
    2. 同一 `review.expert_ref` 在一个任务上只算**一份**,取 `version` 最大的那行。
       依据是《补充规范》§2.2「不能让同一专家占两个首轮名额」;而且"一个任务两行"本身
       分不出是两份独立意见还是同一位的修订(《字段映射》§二 承诺的 `active` 服务端从没
       写过,10-09 之前 `version` 还是按任务全部行编的,老行里的 `supersedes` 可能指向
       别人的意见 —— 写侧已改按主体编号,见 `_prior_count`)。按主体折是当前产物**能算的
       那一份**:同机部署下两位专家的 ref 都是 `peer:127.0.0.1`,折完就是一份,这如实反映
       "04 §八 首轮两位"在产物里不可证明(#64)。宁可少数并说明,不多数并谎称"两份一致"。
    键:三段 `(run_id, issue_id, expert_category_id)`;缺前两段(旧探针经
    `/api/reflections/mark` 落的,`review` 块为空)的行不算任务。
    """
    latest, excluded = {}, {"no_coordinates": 0, "non_expert_origin": 0, "superseded": 0}
    for m in marks:
        rv = m.get("review") if isinstance(m.get("review"), dict) else {}
        rid, iid = rv.get("run_id"), rv.get("issue_id")
        if not rid or not iid:
            excluded["no_coordinates"] += 1
            continue
        if str(m.get("origin") or "expert") != "expert":
            excluded["non_expert_origin"] += 1
            continue
        k = (str(rid), str(iid), str(rv.get("expert_category_id") or ""))
        ref = str(rv.get("expert_ref") or m.get("operator") or "unattributed")
        try:
            ver = int(str(rv.get("version") or 1))
        except ValueError:
            ver = 1
        cur = latest.get((k, ref))
        if cur is not None and cur[0] >= ver:
            excluded["superseded"] += 1        # 旧版本留在盘上,但不再算一份
            continue
        if cur is not None:
            excluded["superseded"] += 1
        latest[(k, ref)] = (ver, str(m.get("verdict", "")))
    n_by_task, verdicts_by_task = {}, {}
    for (k, _ref), (_ver, verdict) in latest.items():
        n_by_task[k] = n_by_task.get(k, 0) + 1
        verdicts_by_task.setdefault(k, set()).add(verdict)
    return {"by_task": n_by_task, "verdicts_by_task": verdicts_by_task,
            "excluded": excluded, "opinions_counted": sum(n_by_task.values())}


@router.get("/api/expert/queue")
def expert_queue(limit: int = 10):
    """待审队列:每条"问题 + 专业类别"一行(《补充规范》§1.2 的任务粒度)。

    任务候选直接取交接包的实现(`review_export.build_review_package`),不在这里重写
    建单资格 —— 否则平台和面板看到的是两套判据,而它们会分叉。
    """
    from .review_export import build_review_package
    from live.reflections import load_marks

    # 每条任务已有几份意见、结论是否一致(只到状态为止,见 _review_state 的说明)。
    # "几份"按主体折、非专家来源不算 —— 规则与不计数的三类都在 _opinion_tally 里,
    # 队列与 /api/expert/marks 共用它(以前两处各写一份,其中一份键形状不一致就
    # 造出过恒为 0 的假账)。键用三元组而不是拼好的串:拼串会让 ("a|b","c") 与
    # ("a","b|c") 撞成同一个键。
    tally = _opinion_tally(load_marks())
    n_by_task, verdicts_by_task = tally["by_task"], tally["verdicts_by_task"]
    # 已经有 1 份在等第二份、以及已经评满/评崩的那些记录都必须进队列,哪怕已经不在
    # "最近 N 条"里:统筹要能回头核对"这个任务真的齐了没",只收 n==1 会让评完的任务
    # 从视野里整条消失(实测:那条 E4 任务在面板上一度看不出任何欠账)。
    pending_runs = sorted({k[0] for k, n in n_by_task.items() if n >= 1})

    pool_rows, pool_at = _pool_rows()

    picked, scanned, total_runs = _recent_runs(limit, must_include=pending_runs)
    tasks, triage, blocked = [], [], []
    for run_id, data in picked:
        pkg = build_review_package(data, run_id)
        for c in pkg["task_candidates"]:
            k = (run_id, c["issue_id"], str(c["expert_category_id"] or ""))
            n = n_by_task.get(k, 0)
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
                # 05 §一:任务未点开也要能看见"current review status";05 §六:池状态。
                "n_opinions": n,
                "review_state": _review_state(n, len(verdicts_by_task.get(k, ()))),
                "state_rank": _state_rank(n, len(verdicts_by_task.get(k, ()))),
                "pool": _pool_state(pool_rows, run_id, c["issue_id"],
                                    c["expert_category_id"]),
            })
        for t in pkg["manual_triage"]:
            triage.append({"run_id": run_id, "issue_id": t["issue_id"],
                           "reason": t["reason"]})
        if pkg["blocked_reasons"]:
            blocked.append({"run_id": run_id, "reasons": pkg["blocked_reasons"]})
    # 服务端就把工作列表排好:平台侧直接读这个接口的话,不该要求它自己懂 state_rank 的语义。
    # 面板那边同款排序在 _EXPERT_PAGE 的 TASKS.sort 里。
    tasks.sort(key=lambda t: (t["state_rank"], _RISK_ORDER.get(t["risk"], 3), t["run_id"]))
    return {"n_runs_scanned": scanned, "n_tasks": len(tasks),
            # 队列是**窗口**而不是全库(每条要读一次 run.json,所以 MAX_QUEUE_RUNS 封顶)。
            # 第五轮体检实测:n_tasks 随 limit 漂移(1→20、10→63、50→155),而面板顶部把它当
            # "可建单任务 N 条"直接显示 ⇒ 只给一个数就是谎。这里把窗口与欠扫的条数一起给出去。
            "window": {"limit": limit, "max_runs": MAX_QUEUE_RUNS},
            "total_review_records": total_runs,
            "records_not_scanned": max(0, total_runs - scanned),
            "n_tasks_scope": "n_tasks/n_manual_triage/n_blocked are counts inside this scan window,"
                             " not the whole pending backlog; for totals read"
                             " total_review_records and records_not_scanned",
            "n_manual_triage": len(triage), "n_blocked": len(blocked),
            "tasks": tasks, "manual_triage": triage, "blocked": blocked,
            "verdict_words": sorted(VERDICT_ALIAS),
            "pool_generated_at": pool_at,
            # 计数口径跟着队列一起出:平台看到某条任务 n_opinions=0 而盘上确实有行时,
            # 要能知道是"不算"而不是"没落盘"(三类原因都在 excluded 里)。
            "opinions_counted": tally["opinions_counted"], "excluded": tally["excluded"],
            "counting_rule": COUNTING_RULE,
            "note": "The queue stops at 'candidates for opening a task'; assigning the two experts,"
                    " tallying and the dispute round are the platform's job."
                    " WARNING: **n_tasks is a window count, not a total** - this call reads only"
                    " min(limit, MAX_QUEUE_RUNS) records (plus every record somebody has already"
                    " reviewed); how many were not read is in records_not_scanned. Showing n_tasks"
                    " as 'the whole pending backlog' would turn 100+ records into a few dozen."
                    " Apart from the most recent limit records, every record that already has a"
                    " review (waiting for a second / both in / disputed) is forced into the queue"
                    " (unaffected by the mtime window); tasks are ordered by state_rank: Disputed >"
                    " escalation > needs_second > unclaimed > complete - completed ones sit at the"
                    " tail but are not hidden (this panel does not claim tasks; whether to filter"
                    " them is the platform's call, by state_rank)."
                    " n_opinions is 'how many subjects have submitted', not 'how many rows are on"
                    " disk': " + COUNTING_RULE + "."
                    " review_state is a status word for the coordinator and carries no verdict or"
                    " text from any opinion; pool comes from the last lora_prep --export artifact"
                    " (generated_at tells you how fresh it is), it is not live truth"}


@router.get("/api/expert/read")
def expert_read(run_id: str):
    """专家要读的正文:反思全文 + 自然语言 Full Context(走专家安全视图,不含实验元信息)。"""
    from .review_app import _runs_dir
    from .review_export import build_review_package

    if not run_id or "/" in run_id or ".." in run_id:
        return JSONResponse({"ok": False, "errors": ["invalid run_id"]}, status_code=400)
    path = os.path.join(_runs_dir(), run_id, "run.json")
    if not os.path.isfile(path):
        return JSONResponse({"ok": False, "errors": ["no such record"]}, status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pkg = build_review_package(data, run_id)
    if pkg["blocked_reasons"]:
        return JSONResponse({"ok": False, "blocked": pkg["blocked_reasons"],
                             "errors": ["this record cannot be reviewed right now (the handover package is blocked)"]},
                            status_code=409)
    ref = (data.get("reflection") or {}).get("text") or ""
    return {"ok": True, "run_id": run_id, "reflection_text": ref,
            "reflection_chars": len(ref), "full_context": pkg["full_context"],
            "categories": [c["id"] + " " + c["name"] for c in pkg["expert_categories"]]}


# 判定门的文案是给"写接口的同学"看的,里面是本平台词汇(correct/partial/incorrect)和
# 训练侧实现细节 —— 专家看不懂(实测:截图里那句红字被当成报错)。这里只做**展示层**
# 翻译,判据本身仍在 live/reflections.mark_gate_errors 一处,不在这儿重判一遍。
GATE_WORDS = {
    "verdict=correct must not carry correction text (the training side would"
    " treat that text as the reference answer); clear the text, or change the"
    " verdict to partial/incorrect":
        "If you picked approve, leave the text empty - approve means this reflection"
        " goes into the training set exactly as it is. To change it, pick edit or"
        " reject instead.",
    "incorrect/partial requires correction text":
        "edit and reject both need your text, otherwise this opinion has nothing"
        " to record.",
    "missing agent/text":
        "The record under review has no reflection text, so there is nothing to judge.",
    "verdict must be one of correct/incorrect/partial":
        "Unrecognised verdict field (pick one of approve / edit / reject).",
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
            "unrecognised verdict field: it must be one of approve / edit / reject"]},
            status_code=400)
    if not run_id or "/" in run_id or ".." in run_id:
        return JSONResponse({"ok": False, "errors": ["the record id (run_id) is missing or invalid"]},
                            status_code=400)
    if not issue_id or not category:
        return JSONResponse({"ok": False, "errors": ["say which issue and which discipline you are judging"]},
                            status_code=400)
    if len(text) > MAX_TEXT_CHARS:
        return JSONResponse({"ok": False, "errors": ["text too long (over 200,000 characters); please split it"]},
                            status_code=400)

    # 被审记录:正文与主体一律从**磁盘上的记录**取,不接受调用方传入
    from .review_app import _runs_dir
    path = os.path.join(_runs_dir(), run_id, "run.json")
    if not os.path.isfile(path):
        return JSONResponse({"ok": False, "errors": ["cannot find this record (it may have just been moved away)"]},
                            status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    thought = (data.get("reflection") or {}).get("text") or ""
    if not thought.strip():
        return JSONResponse({"ok": False, "errors": ["this record has no reflection text, so there is nothing to judge"]},
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

    ref = _server_observed_subject(request)
    prior = _prior_count(run_id, issue_id, category, ref)
    review = {
        "run_id": run_id, "issue_id": issue_id, "expert_category_id": category,
        "verdict_platform": word, "reason": reason, "anchor": anchor,
        "version": prior + 1, "supersedes": prior and "v{}".format(prior) or "",
        "formation": str(body.get("formation", "")).strip() or "single",
        "expert_ref": ref,
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


def _prior_count(run_id: str, issue_id: str, category: str, ref: str) -> int:
    """同一位主体(按 `expert_ref`)在这个任务上已有几版 —— 只用来编号,旧意见不覆盖、不删除。

    以前它数的是**该任务的全部行**,于是第二位专家的第一份意见拿到
    `version=2 / supersedes="v1"`,而那个 v1 是**别人**的意见:产物里读起来像"这份取代了
    第一位专家",而《补充规范》§2.2 要的是"同一专家不许占两个名额"。按主体编号之后
    `supersedes` 只可能指向自己的上一版,读侧的折算(`_opinion_tally`)才站得住。
    `origin` 缺省按 `expert` 算,显式非专家的行不算某一版(读侧 `_opinion_tally` 同口径)。
    """
    from live.reflections import load_marks

    n = 0
    for m in load_marks():
        rv = m.get("review") if isinstance(m.get("review"), dict) else {}
        if str(m.get("origin") or "expert") != "expert":
            continue
        if (rv.get("run_id"), rv.get("issue_id"), rv.get("expert_category_id")) == \
                (run_id, issue_id, category) and \
                str(rv.get("expert_ref") or m.get("operator") or "unattributed") == ref:
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
    dpo_errs = validate_dpo(dpo) if dpo else ["this verdict produces no preference pair"]
    return {"sft_output_is": "the original reflection" if verdict == "correct" else "the expert's text",
            "sft_errors": sft_errs, "dpo_errors": dpo_errs,
            "anchor_required": not bool((anchor or {}).get("sentence_ids")),
            "note": "the text of a rejected verdict is 'why the core reasoning does not hold',"
                    " not a finished rewrite; whether the training side treats it as chosen"
                    " is an open research-side question, see the mapping page, section 3"}


def _pool_rows() -> tuple:
    """训练材料池:从 dataset_report.json 的 rows 按任务身份取(0904doc 05 §六)。

    rows 里的身份是 2026-10-07 补的 —— 之前只有 verdict/ok,一个任务对不上号,
    "进没进池"就没有证据可对。报告只在有人跑 `lora_prep --export` 时重写,
    所以必须连 `generated_at` 一起透出来,否则读的人不知道它落后磁盘多久。
    """
    import case01.tools.lora_prep as lp

    path = os.path.join(lp.OUT_ROOT, "dataset_report.json")
    if not os.path.isfile(path):
        return {}, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            rep = json.load(f)
    except Exception:            # noqa: BLE001 —— 读不动就照实说读不动
        return {}, "unreadable"
    by_task = {}
    for row in rep.get("rows") or []:
        key = "|".join([row.get("run_id", ""), row.get("issue_id", ""),
                        row.get("expert_category_id", "")])
        by_task.setdefault(key, []).append(row)
    return by_task, rep.get("generated_at")


def _pool_state(by_task: dict, run_id: str, issue_id: str, category: str) -> dict:
    key = "|".join([run_id, issue_id, category])
    rows = by_task.get(key) or []
    if not rows:
        return {"state": "this task is not in the latest export", "n_rows": 0}
    sft = sum(1 for r in rows if r.get("sft_in"))
    dpo = sum(1 for r in rows if r.get("dpo_in"))
    errs = sorted({e for r in rows for e in (r.get("errors") or [])})
    return {"state": "in SFT {} / in preference pairs {}".format(sft, dpo),
            "n_rows": len(rows), "rejected_reasons": errs}


# ---------------------------------------------------------------------------
# Audit Trail:把一条 Run 的处理链按时间拼成一条可读的记录
# ---------------------------------------------------------------------------
# 为什么要有这个接口(05 §五 / 01 §十三 两处独立点名,此前全仓没实现):
# 链上的数据本来就都在盘上,但散在**三个来源**里,谁想回答"这条反思是谁审的、
# 审了几轮、最后进没进训练集"都得手工对三趟:
#   1. 成品记录 `case01/runs/<run_id>/run.json` —— Reflection 生成、Router 分类;
#   2. 标记台账 `data/ledgers/reflection_marks.json` —— 专家提交的每一份意见;
#   3. 导出报告 `results/lora/dataset_report.json` —— 进没进训练材料池。
# 本接口只做**合并与排序**,不做任何推断:取不到的节点如实标 missing 并给原因,
# 不拿相近的东西冒充(本仓的判据纪律:缺证据 ≠ 有证据)。
#
# 两个边界(都要在返回里说清,不让读的人误以为这是"全部"):
# - 链上"专家分配 / 冲突追加 / 多数裁决"三步按分工在**平台侧**(见队列接口的 note),
#   本接口只能看到平台把结果写回标记之后的那一步,所以那三步的状态是**从产物推不出来的**,
#   如实标 `platform_side` 而不是编一个时间;
# - 5010 面无鉴权,`expert_ref` 同机部署下都是 `peer:127.0.0.1`,所以"谁审的"最多到
#   连接方地址,不是真人身份(与 `_opinion_tally` 同一条现状说明)。
AUDIT_CHAIN_STEPS = (
    ("reflection", "Reflection generated"),
    ("router", "Router classification and routing"),
    ("assignment", "Expert assignment"),
    ("expert_review", "Expert review / edit"),
    ("conflict", "Additional conflict review"),
    ("final_verdict", "Final review outcome"),
    ("training_pool", "Into the training material pool"),
)


def _audit_reflection(data: dict, run_id: str) -> list:
    """链的第一步:反思是什么时候、由谁生成的(时间取记录的 created_at)。"""
    ref = data.get("reflection") or {}
    text = str(ref.get("text") or "")
    created = str((data.get("manifest") or {}).get("created_at") or "")
    events = []
    if text.strip():
        events.append({
            "step": "reflection", "at": created or None,
            "who": str((data.get("manifest") or {}).get("judge_model") or "local model"),
            "action": "Reflection generated",
            "result": "reflection text {} char(s)".format(len(text)),
            "detail": {"quality": (ref.get("quality") or {}).get("status"),
                       "chars": len(text)},
        })
    else:
        events.append({"step": "reflection", "at": None, "who": None,
                       "action": "Reflection generated", "result": "the record has no reflection text",
                       "missing": "the record has no reflection.text", "has_data": False})
    return events


def _audit_router(data: dict) -> list:
    """链的第二步:Router 拆出几条问题、分别归到哪个专业。"""
    ro = data.get("router") or {}
    issues = ro.get("issues") or []
    if not issues and not ro.get("raw"):
        return [{"step": "router", "at": None, "who": None,
                 "action": "Router classification and routing", "result": "the record has no Router artifact",
                 "missing": "the record has no router.issues", "has_data": False}]
    eb = ro.get("executed_by") or {}
    cats = {}
    for i in issues:
        c = str(i.get("expert_category_id") or "(unlabelled)")
        cats[c] = cats.get(c, 0) + 1
    return [{
        "step": "router", "at": None,
        "who": "{} {}@{}".format(eb.get("source") or "?", eb.get("model") or "?",
                                 eb.get("host") or "local") if eb else None,
        "action": "Router classification and routing",
        "result": "{} issue(s) split out: {}".format(
            len(issues), ", ".join("{}x{}".format(k, v) for k, v in sorted(cats.items())) or "none"),
        "detail": {"n_issues": len(issues), "by_category": cats,
                   "executed_by": eb or None,
                   "expert_pool_version": ro.get("expert_pool_version") or None},
    }]


def _audit_platform_gap(step: str, label: str, why: str) -> dict:
    """平台侧负责、产物推不出来的那几步:如实标 void,不编时间也不编人。

    `has_data=False` 是给 `chain[].covered` 用的判据 —— 这条事件只是**占位说明**,
    不代表这一节点在产物里有证据(第一版拿"step 出现过"当 covered,把平台侧三步
    显示成了绿的,`sample8b15-009` 实测暴露)。
    """
    return {"step": step, "at": None, "who": None, "action": label,
            "result": "the platform side owns this", "platform_side": True, "has_data": False, "note": why}


def _audit_expert_events(marks: list, run_id: str) -> list:
    """链的第三段:每条任务上专家提交的每一份意见(含被取代的旧版本 —— 不删)。

    排序按 `marked_at`(带时区,跨机可比);缺它的老行按 `marked_time` 补,再缺就排最后。
    """
    rows = []
    for m in marks:
        rv = m.get("review") if isinstance(m.get("review"), dict) else {}
        if str(rv.get("run_id") or "") != run_id:
            continue
        rows.append(m)

    def _key(m):
        rv = m.get("review") or {}
        t = str(m.get("marked_at") or m.get("marked_time") or "")
        return (t or "~", str(rv.get("issue_id") or ""), int(str(rv.get("version") or 1) or 1))

    rows.sort(key=_key)
    events = []
    for m in rows:
        rv = m.get("review") or {}
        verdict = str(m.get("verdict") or "")
        origin = str(m.get("origin") or "expert")
        events.append({
            "step": "expert_review",
            "at": m.get("marked_at") or m.get("marked_time") or None,
            "who": str(m.get("operator") or "unattributed"),
            "action": "opinion submitted",
            "has_data": True,
            "result": "{}:{} {}".format(
                rv.get("issue_id") or "(no coordinates)",
                rv.get("expert_category_id") or "(no discipline)",
                VERDICT_REVERSE.get(verdict, verdict)),
            "detail": {
                "issue_id": rv.get("issue_id"),
                "expert_category_id": rv.get("expert_category_id"),
                "verdict_platform": rv.get("verdict_platform") or VERDICT_REVERSE.get(verdict),
                "verdict_internal": verdict,
                "version": rv.get("version"),
                "supersedes": rv.get("supersedes") or None,
                "formation": rv.get("formation"),
                "expert_ref": rv.get("expert_ref"),
                "reason": rv.get("reason") or None,
                "has_correction": bool(str(m.get("correction") or "").strip()),
                # origin != expert 的行不算"专家意见"(导出侧同样拒收),链上要显示出来
                "origin": origin,
                "counted": origin == "expert",
            },
        })
    return events


def _audit_pool_events(run_id: str) -> list:
    """链的最后一步:这条 Run 的任务在最近一次导出里进没进池。

    ⚠ 导出报告只在有人跑 `lora_prep --export` 时重写,所以它不是实时真值;
    `generated_at` 必须一起给出去,读的人才知道它落后磁盘多久。
    """
    by_task, generated = _pool_rows()
    rows = [r for k, rs in by_task.items() if k.split("|", 1)[0] == run_id for r in rs]
    if not rows:
        # 没进池 = 这条 Run 的池状态**未知**(报告可能是旧的、或这条 Run 从没导出过),
        # 不是"没进池"这个结论 —— 所以 has_data=False,chain 上显示未覆盖。
        return [{"step": "training_pool", "at": None, "who": None,
                 "action": "Into the training material pool",
                 "result": "no task of this run is in the latest export",
                 "detail": {"report_generated_at": generated}, "has_data": False,
                 "note": "the report is only rewritten when lora_prep --export runs; it may never have been exported"}]
    sft = sum(1 for r in rows if r.get("sft_in"))
    dpo = sum(1 for r in rows if r.get("dpo_in"))
    ev = {
        "step": "training_pool", "at": generated, "who": "lora_prep --export",
        "action": "Into the training material pool",
        "result": "{} task(s): SFT {} / preference pairs {}".format(len(rows), sft, dpo),
        "detail": {"report_generated_at": generated, "n_tasks": len(rows),
                   "sft_in": sft, "dpo_in": dpo,
                   "rejected_reasons": sorted({e for r in rows
                                               for e in (r.get("rejected_reasons")
                                                         or r.get("errors") or [])})},
        # ⚠ at 是"报告生成时间",不是"进池时间" —— 它可能早于这条 Run 的最后一份
        # 意见(10-07 的报告 vs 10-09 的审核)。`at_semantics` 让排序别拿它当
        # 真实发生时间用,见 expert_audit_trail 里的 _sort_key。
        "at_semantics": "report_generated_at",
    }
    if not sft and not dpo:
        # 在报告里出现了、但一份都没进 SFT/偏好对 ⇒ 没真进池,链上不该显示成
        # 已完成(排除原因就在 detail.rejected_reasons 里)。
        ev["has_data"] = False
    return [ev]

@router.get("/api/expert/audit-trail")
def expert_audit_trail(run_id: str = ""):
    """按 Run 回溯整条处理链(05 §五 / 01 §十三)。

    返回一条**按时间排好**的事件列表,覆盖七个节点;取不到的节点如实标 missing /
    platform_side,不拿相近的东西冒充。平台侧只要按 `events` 顺序渲染即可。
    """
    from live.reflections import load_marks

    run_id = str(run_id or "").strip()
    if not run_id or "/" in run_id or ".." in run_id:
        return JSONResponse({"ok": False, "errors": ["invalid run_id"]}, status_code=400)

    from .review_app import _runs_dir
    path = os.path.join(_runs_dir(), run_id, "run.json")
    if not os.path.isfile(path):
        return JSONResponse({"ok": False, "errors": ["no such record"]}, status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    events = []
    events += _audit_reflection(data, run_id)
    events += _audit_router(data)
    events.append(_audit_platform_gap(
        "assignment", "Expert assignment",
        "assignment is executed by the platform (this side stops at 'candidates for"
        " opening a task', see the note on /api/expert/queue); the result only"
        " becomes visible after the platform writes the marks back"))
    events += _audit_expert_events(load_marks(), run_id)

    # 冲突:从产物只能看出"同任务多份结论不一致",看不出平台有没有真开争议轮
    marks = [m for m in load_marks()
             if str(((m.get("review") or {}).get("run_id")) or "") == run_id]
    tally = _opinion_tally([m for m in marks])
    disputed = [k for k, v in tally["verdicts_by_task"].items()
                if len(v) > 1 and tally["by_task"].get(k, 0) >= 2]
    if disputed:
        events.append({
            "step": "conflict", "at": None, "who": None,
            "action": "Additional conflict review",
            "result": "{} task(s) carry two mutually exclusive verdicts -> experts who did not sit in"
                         " the previous round must be added".format(len(disputed)),
            "detail": {"disputed_tasks": ["|".join(k) for k in disputed]},
            "undecidable": "whether a dispute round was really opened, and who was added - the platform side"
                            " owns this, it cannot be seen in the artifacts",
        })
    else:
        events.append({"step": "conflict", "at": None, "who": None,
                       "action": "Additional conflict review",
                       "result": "cannot be determined: fewer than two mutually exclusive opinions right now",
                       "undecidable": "a conflict can only be judged from two mutually exclusive verdicts;"
                                      " with fewer than two, 'no conflict' and 'not reviewed"
                                      " yet' look identical in the artifacts",
                       "has_data": False})

    n_tasks = len(set(
        (str((m.get("review") or {}).get("issue_id")),
         str((m.get("review") or {}).get("expert_category_id")))
        for m in marks if (m.get("review") or {}).get("issue_id")))
    decided = tally["opinions_counted"]
    fv = {
        "step": "final_verdict", "at": None, "who": None,
        "action": "Final review outcome",
        "result": "{} counted opinion(s) received, covering {} task(s)".format(decided, n_tasks),
        "detail": {"opinions_counted": decided, "n_tasks_with_marks": n_tasks,
                   "excluded": tally["excluded"]},
        "note": "majority voting and the final sign-off belong to the platform side; this only"
              " reports how many opinions have been written back",
    }
    if decided == 0:
        # 一份计票意见都没有时,"最终结果"是**推不出来**的,不能显示成"0 份"就算数
        fv["result"] = "cannot be determined: no counted opinion has been written back yet"
        fv["has_data"] = False
        fv["undecidable"] = ("no origin=expert opinion has been written back into the"
                             " artifacts, so there is no final outcome to speak of")
    events.append(fv)
    events += _audit_pool_events(run_id)

    order = {s: i for i, (s, _) in enumerate(AUDIT_CHAIN_STEPS)}

    def _sort_key(e):
        # 时间轴只用来排"真按时间发生"的事件。
        # ⚠ `training_pool` 的 at 是**报告生成时间**,不是进池时间(报告可能早于这条
        # Run 的最后一份意见)⇒ 它不参与时间排序,一律钉到链尾,否则会插在审核中间
        # (`sample8b15-009` 实测:10-07 的池快照排到了 10-09 的审核之前)。
        if e.get("at_semantics") == "report_generated_at":
            return (2, "", order.get(e.get("step"), 99))
        t = str(e.get("at") or "")
        return (1 if not t else 0, t or "", order.get(e.get("step"), 99))

    events.sort(key=_sort_key)
    # covered 只算"产物里有真实证据"的节点:平台侧占位与 missing/undecidable 都不算,
    # 否则链上会出现"绿的但其实什么都没有"(第一版就是这么错的)。
    covered = {e["step"] for e in events if e.get("has_data", True) is not False
               and not e.get("platform_side")}
    return {
        "ok": True,
        "run_id": run_id,
        "chain": [{"step": s, "label": lb,
                   "covered": s in covered} for s, lb in AUDIT_CHAIN_STEPS],
        "events": events,
        "n_events": len(events),
        "boundaries": {
            "what_this_shows": "the parts that **can be seen** in this side's artifacts:"
                              " Reflection generation, Router classification, every opinion"
                              " an expert submitted, and whether a task entered the training"
                              " material pool",
            "what_this_cannot_show": "expert assignment / the dispute round / majority voting are"
                                     " executed on the platform side and cannot be derived from"
                                     " the artifacts - those steps are honestly marked"
                                     " platform_side, with no invented time or owner",
            "who_note": "the 5010 face has no authentication, so operator is the peer address"
                        " observed by the server (peer:127.0.0.1 on the same machine),"
                        " not a real person's identity",
        },
    }


def _review_state(n: int, n_distinct_verdicts: int) -> str:
    """任务级审核状态(0904doc 05 §一 要求队列显示"current review status")。

    `n` = 该任务上**不同主体**的当前意见数(折算规则与理由见 `_opinion_tally`)。
    只给状态,不给结论分布 —— 首轮两份意见互不可见(《补充规范》§3)。这里能说的
    上限是"两份一致/不一致",这正是 04 §八.3 判定 Disputed 所需的最小信息。
    """
    if n == 0:
        return "unclaimed (0 opinions)"
    if n == 1:
        return "first round in progress (1/2)"
    if n == 2:
        return ("first round complete - the two verdicts agree" if n_distinct_verdicts <= 1
                else "first round complete - the two verdicts conflict -> Disputed, 3 more experts needed")
    return "dispute round ({} opinions in total)".format(n)


# 队列排序档(数字小的排前面)。为什么按状态而不是按时间:04 §八 的活儿分三种
# —— 等第二份、等追加、还没人看 —— 一位专家再来时最先该干的是**把别人的首轮补齐**,
# 而不是又开一条新的;按 mtime 取窗口会把这类记录整条藏掉(实测就藏掉过)。
_STATE_RANK = {"disputed": 0, "escalation": 1, "needs_second": 2,
               "unclaimed": 3, "complete": 4}
_RISK_ORDER = {"high": 0, "medium": 1, "low": 2}   # 与面板里的 RISK_ORDER 同口径


def _state_rank(n: int, n_distinct_verdicts: int) -> int:
    if n == 0:
        return _STATE_RANK["unclaimed"]
    if n == 1:
        return _STATE_RANK["needs_second"]
    if n == 2:
        return (_STATE_RANK["disputed"] if n_distinct_verdicts > 1
                else _STATE_RANK["complete"])
    return _STATE_RANK["escalation"]


@router.get("/api/expert/marks")
def expert_marks():
    """只回计数。结论与文本一律不给 —— 首轮两份意见互不可见(《补充规范》§3:71)。

    ⚠ 这不是权限:5010 面无鉴权,知道 run_id 的人仍能从标记文件读到内容。
    这里做的是"界面不替你显示上一份意见",防的是无意的相互影响。
    """
    from live.reflections import load_marks

    marks = load_marks()
    tally = _opinion_tally(marks)
    by_category = {}
    for m in marks:
        rv = m.get("review") if isinstance(m.get("review"), dict) else {}
        cat = rv.get("expert_category_id") or "(no discipline)"
        by_category[cat] = by_category.get(cat, 0) + 1
    by_task, verdicts_by_task = tally["by_task"], tally["verdicts_by_task"]
    disputed = sum(1 for k, v in by_task.items()
                   if len(verdicts_by_task.get(k, ())) > 1 and v >= 2)
    return {"total": len(marks), "with_review_block": sum(
        1 for m in marks if isinstance(m.get("review"), dict)),
        "opinions_counted": tally["opinions_counted"],
        "excluded": tally["excluded"],
        "counting_rule": COUNTING_RULE,
        "by_category": by_category,
        "opinions_per_task": sorted([("|".join(k), v) for k, v in by_task.items()],
                                    key=lambda kv: (-kv[1], kv[0]))[:20],
        "tasks_with_two_or_more": sum(1 for v in by_task.values() if v >= 2),
        "tasks_disputed": disputed,
        "visibility": "this endpoint only returns counts; verdicts and text are not in the response"}


_EXPERT_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Expert Review Panel</title>
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
  .rubric { margin: 8px 0 0; font-size: 12px; color: #1f2328; }
  .rubric summary { cursor: pointer; color: #0b5cad; }
  .rubric ol { margin: 6px 0 6px 18px; padding: 0; }
  .task .st { color: #57606a; }
  .task .st b { color: #cf222e; }
  /* 嵌入版式:与 review_app 的 `body.embed` 同一套判定(`/embed/` 路径或 ?embed=1`)。
     去掉大标题与本页自己的返回链接 —— 在宿主 iframe 里它们是多余的,而且那个
     "← back to the read-only view"会整页跳走(2026-10-09 修:以前嵌进去就没有回来的路)。 */
  body.embed { background:#fff; }
  body.embed header { padding:9px 12px; }
  body.embed header h1 { display:none; }
  body.embed #backlink { display:none; }
  body.embed main { padding:8px 10px 12px; }
</style>
</head>
<body>
<header>
  <h1>Expert Review Panel</h1>
  <p>Submit one opinion for each issue and specialty. The platform can replace this reference interface; it shows <b>the fields and their effects</b>.
     <span class="meta" id="counts"></span>
     <a href="/review" class="meta" id="backlink">← Back to records</a></p>
  <p class="how">How to review: <b>1.</b> Select a task on the left (optionally filter by risk). <b>2.</b> The right side shows
     <b>the selected issue</b> and its location in the source text. Open the full reflection if needed.
     <b>3.</b> Choose a verdict: Approve means the core reflection is sound; Suggest revision means it has value but needs a focused correction;
     Reject means its core reasoning is unsound. The correction box is disabled for Approve.</p>
  <details class="rubric">
    <summary>Review criteria (shared by all experts)</summary>
    <ol>
      <li><b>Facts and causality</b>: Check for factual mistakes, incorrect attribution, and unsupported causal claims.</li>
      <li><b>Learning from this experience</b>: Identify the key judgment issue instead of merely repeating the outcome.</li>
      <li><b>Outcome bias</b>: Do not judge an earlier decision solely by whether the price later rose or fell.</li>
      <li><b>Overgeneralization</b>: One experience should not become an overly broad future rule.</li>
      <li><b>Omitted values</b>: Consider risk exposure, irreversible consequences, and the opportunity cost of action or inaction.</li>
    </ol>
    <div class="meta">These criteria guide your review. You decide the verdict; the system does not score it for you.</div>
  </details>
</header>
<main>
  <div id="left">
    <div class="meta" id="summary">Loading review queue…</div>
    <div id="filter">
      <label for="risk">Filter by risk</label>
      <select id="risk">
        <option value="">All</option>
        <option value="high">High only</option>
        <option value="medium">Medium only</option>
        <option value="low">Low only</option>
      </select>
      <span class="meta" id="riskn"></span>
    </div>
    <div id="queue"><div class="task s">Loading review queue…</div></div>
  </div>
  <div id="work"><h2>Select a task on the left</h2><div class="meta">
     The queue shows review candidates from recent records. Tasks awaiting a second opinion appear first.
     Expand the full reflection to read its source text.</div></div>
</main>
<script>
var TASK = null, FULL = "";
var Q = new URLSearchParams(location.search);
var EMBED = Q.get("embed") === "1" || location.pathname.indexOf("/embed/") === 0;
var FROM = Q.get("from") || "";       // "review" = 从只读面点进来的
var WANT_RUN = Q.get("run") || "";    // 只读面正在看的那条,进来就直接定位
if (EMBED) { document.body.classList.add("embed"); }
// 返回链接要记住来源与那条记录 —— 以前写死 /review,离开后回不到原来那条(2026-10-09)。
// 非嵌入时按 ?from= 拼;没给来源时用 referrer 兜底;都没有才给裸 /review。
(function () {
  var a = document.getElementById("backlink");
  if (!a) return;
  var target = "/review", label = "← back to the read-only view (records)";
  if (FROM === "embed") { target = "/embed/review"; }
  if (WANT_RUN) { target += (target.indexOf("?") >= 0 ? "&" : "?") + "run=" + encodeURIComponent(WANT_RUN); }
  a.href = target;
})();
function esc(s) { var d = document.createElement("div"); d.textContent = (s == null ? "" : String(s)); return d.innerHTML; }
function api(u, o) { return fetch(u, o).then(function (r) { return r.json(); }); }
// 两处刷新(进页面、提交完)用同一个式子,免得第二处把新增的那半句漏掉
function countsLine(m) {
  if (!m) return "";
  var ex = m.excluded || {}, why = [];
  if (ex.no_coordinates) why.push(ex.no_coordinates + " row(s) without task coordinates");
  if (ex.non_expert_origin) why.push(ex.non_expert_origin + " row(s) whose origin is not an expert (self-test/simulated)");
  if (ex.superseded) why.push(ex.superseded + " row(s) superseded by a newer version");
  return " · on-disk marks " + m.total + " row(s), counted as opinions " + m.opinions_counted +
    " submission(s) (folded by submitter; revisions by the same submitter count once), of which " + m.tasks_with_two_or_more +
    " task(s) already have both reviewers" + (why.length ? ("; not counted:" + why.join("、")) : "");
}

var TASKS = [], RISK_ORDER = { high: 0, medium: 1, low: 2 }, POOL_AT = "";

api("/api/expert/queue?limit=10").then(function (q) {
  POOL_AT = q.pool_generated_at ? ("(last export " + q.pool_generated_at + ")")
                               : "(no export artifact yet)";
  TASKS = q.tasks.slice().sort(function (a, b) {
    // 服务端已经按同一式子排过(见 expert_queue 末尾),这里只是重渲染时的稳定性兜底
    var s = (a.state_rank == null ? 9 : a.state_rank) - (b.state_rank == null ? 9 : b.state_rank);
    if (s !== 0) return s;
    var r = RISK_ORDER[a.risk] - RISK_ORDER[b.risk];
    return r !== 0 ? r : (a.run_id < b.run_id ? -1 : 1);
  });
  document.getElementById("summary").textContent =
    "this scan covered " + q.n_runs_scanned + "/" + q.total_review_records +
    " record(s); expand to create review tasks " + q.n_tasks + " items" +
    (q.records_not_scanned ? "(another " + q.records_not_scanned + " record(s) not scanned)" : "(fully scanned)") +
    " · manual triage " + q.n_manual_triage +
    " item(s) · not reviewable " + q.n_blocked + " items";
  if (!q.tasks.length) {
    document.getElementById("queue").innerHTML =
      "<div class='task err'>queue is empty: none of the scanned records offers an 'issue + specialty' task candidate" +
      "(see the manual-triage and not-reviewable counts above).</div>";
  }
  drawQueue();
  document.getElementById("risk").onchange = drawQueue;
  // ?run=<id> 直接定位到那条记录的第一条任务(从只读面点进来时就带这个参数)。
  // 该 run 不在窗口内(队列只扫最近 MAX_QUEUE_RUNS 条)时如实说,不静默什么都不做。
  if (WANT_RUN) {
    var hit = TASKS.filter(function (t) { return t.run_id === WANT_RUN; })[0];
    if (hit) {
      // 高亮要落在**这一条**的节点上:drawQueue 后节点顺序 = 过滤后的 TASKS 顺序,
      // 直接取第一个会把高亮画到别的任务上(只读面点进来时最容易被看出来)。
      var shown = TASKS.filter(function (t) {
        var w = document.getElementById("risk").value; return !w || t.risk === w; });
      pick(hit, document.querySelectorAll("#queue .task")[shown.indexOf(hit)]);
    } else {
      var box = document.getElementById("queue");
      box.insertAdjacentHTML("afterbegin",
        "<div class='task err'>the requested record " + esc(WANT_RUN) +
        " is outside this scan window (the queue only scans the most recent records by mtime); the tasks listed below are inside the window.</div>");
    }
  }
  return api("/api/expert/marks");
}).then(function (m) {
  document.getElementById("counts").textContent = countsLine(m);
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
      esc(t.expert_category_id) + " · " + esc(t.run_id) + " / " + esc(t.issue_id) +
      // 05 §一:未点开也要显示"current review status";05 §四:Disputed 要看得出来
                  "</div><div class='s st'>status:" + (
      String(t.review_state || "?").indexOf("Disputed") >= 0
        ? "<b>" + esc(t.review_state) + "</b>" : esc(t.review_state || "?")) + "</div>";
    d.onclick = function () { pick(t, d); };
    box.appendChild(d);
  });
  if (!box.children.length) {
    box.innerHTML = "<div class='task s'>no tasks in this bucket; try 'all'.</div>";
  }
}

function pick(t, node) {
  TASK = t;
  Array.prototype.forEach.call(document.querySelectorAll(".task.on"), function (n) { n.classList.remove("on"); });
  if (node) node.classList.add("on");
  renderForm();
  api("/api/expert/read?run_id=" + encodeURIComponent(t.run_id)).then(function (r) {
    // 连点两条任务时,先那条的响应可能后到 —— 不丢弃就会出现"看的是 A 的正文、
    // 提交的是 B"这种错位(A 的反思贴在 B 的任务上)。只认当前选中任务的那一次。
    if (TASK !== t) return;
    var chars = document.getElementById("chars"), anch = document.getElementById("anch");
    if (!r.ok) { document.getElementById("body").innerHTML =
      "<span class='err'>" + esc((r.errors || []).join("; ")) + "</span>";
      // 这两个占位符不能停在"…":读不到正文是终态,不是还在加载
      if (chars) chars.textContent = "— (body not read)";
      if (anch) anch.textContent = "—"; return; }
    FULL = r.reflection_text;
    document.getElementById("chars").textContent = r.reflection_chars + " chars";
    // 默认不给整篇反思:《HCI 增量需求》§二"默认展示该专家需要审核的具体问题,
    // 而不是整篇 Reflection"。整篇与 Full Context 都放在点了之后才出现。
    document.getElementById("body").innerHTML =
      "<div class='meta'>the body is collapsed by default per 05 §2: the two buttons below expand it on demand (full text " +
      r.reflection_chars + " chars).</div>" +
      "<button id='reflbtn'>read the full reflection</button>" +
      "<button id='ctxbtn'>expand the natural-language Full Context</button>" +
      "<pre id='refl' style='display:none'></pre><pre id='ctx' style='display:none'></pre>";
    document.getElementById("reflbtn").onclick = function () {
      document.getElementById("refl").textContent = FULL;
      document.getElementById("refl").style.display = "block"; this.style.display = "none";
    };
    document.getElementById("ctxbtn").onclick = function () {
      var p = document.getElementById("ctx");
      p.textContent = r.full_context; p.style.display = "block"; this.style.display = "none";
    };
    // 专家改文落在哪几句:面板把 anchor 原样带回去,平台侧才需要它(见映射页 §三·补 ①)
    document.getElementById("anch").textContent =
      (t.anchor.sentence_ids || []).join(", ") || "(the handover bundle gave no sentence anchors)";
  });
}

function renderForm() {
  var t = TASK, w = document.getElementById("work");
  var pl = t.pool || {};
  w.innerHTML =
    "<h2>Issue " + esc(t.issue_id) + " · specialty " + esc(t.expert_category_id) + "</h2>" +
    "<div>" + esc(t.summary) + "</div>" +
    "<div class='meta'>routing reason:" + esc(t.routing_reason) + "</div>" +
    "<div class='quote'>" + esc(t.evidence_quote) + "</div>" +
    "<div class='meta'>review status:" + esc(t.review_state || "?") +
      " · training-material pool:" + esc(pl.state || "?") + POOL_AT + "</div>" +
    "<div class='meta'>reflection body:<span id='chars'>…</span> · the source sentences you own <code id='anch'>…</code></div>" +
    "<div id='body'><div class='meta'>loading body…</div></div>" +
    "<h3>Your verdict (required)</h3>" +
    "<div>" +
      "<button data-v='approve'>approve</button>" +
      "<button data-v='edit'>suggest edits (edit)</button>" +
      "<button data-v='reject'>reject</button>" +
      "<span class='meta' id='vw'></span>" +
    "</div>" +
    "<h3 id='th'>Text (pick a verdict above first)</h3>" +
    "<textarea id='text' disabled placeholder='unlocks once you pick a verdict'></textarea>" +
    "<div class='warn' id='seg' style='display:none'>just write the revised wording for <b>the sentences you own</b>:"
      + "HCI incremental requirements §2 states that Edit means 'only modify the issue / reflection fragment you own; do not rewrite the whole Reflection'."
      + "<br>but be clear about the consequence: the export side does <b>not</b> merge fragments back into the full text yet, so this revision enters the review material"
      + " and <b>not yet into preference pairs</b> (dimension gate). That is an export-side TODO, not your mistake, and you do not need to rewrite the whole piece.</div>" +
    "<h3>Why (optional; does not affect the training set)</h3>" +
    "<input type='text' id='reason' placeholder='one sentence is enough; kept in the review material'>" +
    "<div style='margin-top:10px'>" +
      "<button id='send' style='background:#1f883d;color:#fff'>submit this opinion</button>" +
      "<span class='meta' id='res'></span>" +
    "</div>" +
    "<div class='meta'>the submitter is recorded server-side (you cannot set it); submitting again on the same task appends a second version and keeps the old one.</div>";
  Array.prototype.forEach.call(w.querySelectorAll("button[data-v]"), function (b) {
    b.onclick = function () {
      Array.prototype.forEach.call(w.querySelectorAll("button[data-v]"), function (n) { n.classList.remove("sel"); });
      b.classList.add("sel");
      t._v = b.dataset.v;
      var box = document.getElementById("text"), th = document.getElementById("th");
      var seg = document.getElementById("seg");
      // approve 时把文本框**锁住并清空**:实测过"switching back to approve only hides the textbox; the value stays"
      // 会撞上门(那条门是对的),但让专家撞见自己的残留输入是界面该负责的事。
      if (b.dataset.v === "approve") {
        box.value = ""; box.disabled = true;
        th.textContent = "Text (not needed for approve: this reflection enters the training set as-is)";
        seg.style.display = "none";
      } else {
        box.disabled = false;
        th.textContent = b.dataset.v === "edit" ? "Your suggested revision (only the fragment you own; required)"
                                               : "Why this does not hold (required)";
        box.placeholder = b.dataset.v === "edit"
          ? "just revise the sentences listed under 'source anchors' above; no need to rewrite the whole reflection"
          : "say clearly where the core logic goes wrong (this text enters the review material, not as ground truth)";
        seg.style.display = b.dataset.v === "edit" ? "block" : "none";
      }
      document.getElementById("vw").textContent = "selected:" + b.textContent;
    };
  });
  document.getElementById("send").onclick = send;
}

function send() {
  var t = TASK, res = document.getElementById("res");
  if (!t._v) { res.innerHTML = "<span class='err'>pick a verdict first</span>"; return; }
  var body = { run_id: t.run_id, issue_id: t.issue_id, expert_category_id: t.expert_category_id,
               verdict: t._v, text: document.getElementById("text").value,
               reason: document.getElementById("reason").value, anchor: t.anchor };
  res.textContent = "submitting…";
  api("/api/expert/decision", { method: "POST", headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(body) }).then(function (r) {
    if (!r.ok) { res.innerHTML = "<span class='err'>" + esc((r.errors || []).join("; ")) + "</span>"; return; }
    var e = r.training_effect || {};
    res.innerHTML = "<span class='ok'>saved as version " + r.mark.review.version + "</span>" +
      "<div class='meta'>training-side text source:" + esc(e.sft_output_is || "?") +
      " · SFT-gate issue " + (e.sft_errors || []).length +
      " · preference-pair issue " + esc((e.dpo_errors || []).join("; ")) + "</div>";
    api("/api/expert/marks").then(function (m) {
      document.getElementById("counts").textContent = countsLine(m);
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
    """`/review/expert` 的嵌入版式:同一份 HTML,前端按 `/embed/` 路径(或 `?embed=1`)
    切到 `body.embed` —— 去掉大标题与本页自己的"← back to the read-only view"链接(2026-10-09 补;
    以前这里只是别名,嵌进宿主 iframe 后那个返回链接会整页跳走、回不到原记录)。"""
    return HTMLResponse(_EXPERT_PAGE)
