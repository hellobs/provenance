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

- `/review/expert`  面板页(带大标题)
- `/embed/expert`   **同一页的别名,目前没有嵌入版式**:`review_app` 那边有"前端按 `/embed/` 路径切
                    `body.embed` 压缩版式"的机制(见 `review_app.py` 里的 `EMBED` 判定与 CSS),
                    **本面板没有接**,所以引这个地址拿到的与 `/review/expert` 完全一样
                    (含大标题与"← 回到只读面")。要给平台做嵌入版式,照 `review_app` 那套补,
                    别把这两个地址对外说成两种版式。
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


COUNTING_RULE = ("一份意见 = 一个 review.expert_ref 在该任务上的最新版本,且 origin 是 expert"
                 "(缺省按 expert 算);不计数的情形在 excluded 里报数(缺任务坐标 / "
                 "来源显式非专家 / 被同主体新版本取代),理由见服务端 `_opinion_tally`")


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
                # 05 §一:任务未点开也要能看见"当前审核状态";05 §六:池状态。
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
            "n_tasks_scope": "n_tasks/n_manual_triage/n_blocked 都是本次扫描窗口内的数,"
                             "不是全库待审总量;要总量看 total_review_records 与 records_not_scanned",
            "n_manual_triage": len(triage), "n_blocked": len(blocked),
            "tasks": tasks, "manual_triage": triage, "blocked": blocked,
            "verdict_words": sorted(VERDICT_ALIAS),
            "pool_generated_at": pool_at,
            # 计数口径跟着队列一起出:平台看到某条任务 n_opinions=0 而盘上确实有行时,
            # 要能知道是"不算"而不是"没落盘"(三类原因都在 excluded 里)。
            "opinions_counted": tally["opinions_counted"], "excluded": tally["excluded"],
            "counting_rule": COUNTING_RULE,
            "note": "队列只到「建单候选」为止;分配两位专家、计票、争议轮由平台侧负责。"
                    "⚠ **n_tasks 是窗口数不是总量**:本次只读 min(limit, MAX_QUEUE_RUNS) 条记录"
                    "(外加凡有人评过的),没读到的条数写在 records_not_scanned 里 —— "
                    "把 n_tasks 当「全库待审」显示会把一百多条说成几十条。"
                    "除最近 limit 条外,凡「已经有人评过」的记录(等第二份 / 已评满 / 争议轮)"
                    "都强制进队列(不受 mtime 窗口影响);tasks 按 state_rank 排:Disputed > "
                    "争议轮 > 等第二份 > 待领取 > 首轮已齐 —— 已齐的列在队尾但不隐藏(面板不接单,"
                    "要不要过滤由平台按 state_rank 决定)。"
                    "n_opinions 是「几位主体已交」而不是「盘上有几行」:" + COUNTING_RULE + "。"
                    "review_state 是给统筹看的状态词,不含任何一份意见的结论或文本;"
                    "pool 来自上次 lora_prep --export 的产物(generated_at 标明新鲜度),不是实时真值"}


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
    dpo_errs = validate_dpo(dpo) if dpo else ["该结论不生成偏好对"]
    return {"sft_output_is": "原始反思" if verdict == "correct" else "专家文本",
            "sft_errors": sft_errs, "dpo_errors": dpo_errs,
            "anchor_required": not bool((anchor or {}).get("sentence_ids")),
            "note": "rejected 的文本是「核心逻辑为何不成立」,不是已定稿的改写;"
                    "训练侧要不要把它当 chosen 属于研究侧待拍,见映射页 §三·补"}


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
        return {"state": "最近一次导出里没有这条任务", "n_rows": 0}
    sft = sum(1 for r in rows if r.get("sft_in"))
    dpo = sum(1 for r in rows if r.get("dpo_in"))
    errs = sorted({e for r in rows for e in (r.get("errors") or [])})
    return {"state": "进 SFT {} 份 / 进偏好对 {} 份".format(sft, dpo),
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
    ("reflection", "Reflection 生成"),
    ("router", "Router 分类与路由"),
    ("assignment", "专家分配"),
    ("expert_review", "专家审核 / 修改"),
    ("conflict", "冲突追加审核"),
    ("final_verdict", "最终审核结果"),
    ("training_pool", "进入训练材料池"),
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
            "who": str((data.get("manifest") or {}).get("judge_model") or "本地模型"),
            "action": "生成 Reflection",
            "result": "反思正文 {} 字".format(len(text)),
            "detail": {"quality": (ref.get("quality") or {}).get("status"),
                       "chars": len(text)},
        })
    else:
        events.append({"step": "reflection", "at": None, "who": None,
                       "action": "生成 Reflection", "result": "记录里没有反思正文",
                       "missing": "记录缺 reflection.text", "has_data": False})
    return events


def _audit_router(data: dict) -> list:
    """链的第二步:Router 拆出几条问题、分别归到哪个专业。"""
    ro = data.get("router") or {}
    issues = ro.get("issues") or []
    if not issues and not ro.get("raw"):
        return [{"step": "router", "at": None, "who": None,
                 "action": "Router 分类与路由", "result": "记录里没有 Router 产物",
                 "missing": "记录缺 router.issues", "has_data": False}]
    eb = ro.get("executed_by") or {}
    cats = {}
    for i in issues:
        c = str(i.get("expert_category_id") or "(未标)")
        cats[c] = cats.get(c, 0) + 1
    return [{
        "step": "router", "at": None,
        "who": "{} {}@{}".format(eb.get("source") or "?", eb.get("model") or "?",
                                 eb.get("host") or "local") if eb else None,
        "action": "Router 分类与路由",
        "result": "拆出 {} 条问题:{}".format(
            len(issues), "、".join("{}×{}".format(k, v) for k, v in sorted(cats.items())) or "无"),
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
            "result": "由平台侧负责", "platform_side": True, "has_data": False, "note": why}


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
            "action": "提交意见",
            "has_data": True,
            "result": "{}:{} {}".format(
                rv.get("issue_id") or "(无坐标)",
                rv.get("expert_category_id") or "(未标专业)",
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
                 "action": "进入训练材料池",
                 "result": "最近一次导出里没有这条 Run 的任务",
                 "detail": {"report_generated_at": generated}, "has_data": False,
                 "note": "报告只在跑 lora_prep --export 时重写,可能是还没导出过"}]
    sft = sum(1 for r in rows if r.get("sft_in"))
    dpo = sum(1 for r in rows if r.get("dpo_in"))
    ev = {
        "step": "training_pool", "at": generated, "who": "lora_prep --export",
        "action": "进入训练材料池",
        "result": "{} 条任务:SFT {} 份 / 偏好对 {} 份".format(len(rows), sft, dpo),
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
        return JSONResponse({"ok": False, "errors": ["run_id 非法"]}, status_code=400)

    from .review_app import _runs_dir
    path = os.path.join(_runs_dir(), run_id, "run.json")
    if not os.path.isfile(path):
        return JSONResponse({"ok": False, "errors": ["记录不存在"]}, status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    events = []
    events += _audit_reflection(data, run_id)
    events += _audit_router(data)
    events.append(_audit_platform_gap(
        "assignment", "专家分配",
        "分配由平台侧执行(本平台只到「建单候选」为止,见 /api/expert/queue 的 note);"
        "平台写回标记之后才能看到结果"))
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
            "action": "冲突追加审核",
            "result": "{} 条任务两份结论互斥 → 需追加未参与前一轮的专家".format(len(disputed)),
            "detail": {"disputed_tasks": ["|".join(k) for k in disputed]},
            "undecidable": "是否真的开了争议轮、追加了谁 —— 平台侧负责,产物里看不到",
        })
    else:
        events.append({"step": "conflict", "at": None, "who": None,
                       "action": "冲突追加审核",
                       "result": "判定不了:当前不足两份互斥意见",
                       "undecidable": "冲突要两份结论互斥才能判;不足两份时"
                                      "「没有冲突」与「还没审」在产物里长得一样",
                       "has_data": False})

    n_tasks = len(set(
        (str((m.get("review") or {}).get("issue_id")),
         str((m.get("review") or {}).get("expert_category_id")))
        for m in marks if (m.get("review") or {}).get("issue_id")))
    decided = tally["opinions_counted"]
    fv = {
        "step": "final_verdict", "at": None, "who": None,
        "action": "最终审核结果",
        "result": "已收 {} 份计票意见,涉及 {} 条任务".format(decided, n_tasks),
        "detail": {"opinions_counted": decided, "n_tasks_with_marks": n_tasks,
                   "excluded": tally["excluded"]},
        "note": "多数裁决与最终定稿由平台侧负责;这里只报**已写回的**意见数",
    }
    if decided == 0:
        # 一份计票意见都没有时,"最终结果"是**推不出来**的,不能显示成"0 份"就算数
        fv["result"] = "判定不了:还没有写回的计票意见"
        fv["has_data"] = False
        fv["undecidable"] = "产品里没有任何 origin=expert 的意见写回,谈不上最终结果"
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
            "what_this_shows": "本平台产物里**能看到的**那几段:Reflection 生成、Router 分类、"
                              "专家提交的每一份意见、任务是否进训练材料池",
            "what_this_cannot_show": "专家分配/争议轮/多数裁决在平台侧执行,产物里推不出来 —— "
                                     "这几步如实标 platform_side,不编造时间与责任人",
            "who_note": "5010 面无鉴权,operator 是服务端观测到的连接方地址(同机即 "
                        "peer:127.0.0.1),不是真人身份",
        },
    }


def _review_state(n: int, n_distinct_verdicts: int) -> str:
    """任务级审核状态(0904doc 05 §一 要求队列显示"当前审核状态")。

    `n` = 该任务上**不同主体**的当前意见数(折算规则与理由见 `_opinion_tally`)。
    只给状态,不给结论分布 —— 首轮两份意见互不可见(《补充规范》§3)。这里能说的
    上限是"两份一致/不一致",这正是 04 §八.3 判定 Disputed 所需的最小信息。
    """
    if n == 0:
        return "待领取(0 份意见)"
    if n == 1:
        return "首轮进行中(1/2 份)"
    if n == 2:
        return ("首轮齐·两份结论一致" if n_distinct_verdicts <= 1
                else "首轮齐·两份互斥 → Disputed,需追加 3 位")
    return "争议轮(累计 {} 份)".format(n)


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
        cat = rv.get("expert_category_id") or "(未标专业)"
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
  .rubric { margin: 8px 0 0; font-size: 12px; color: #1f2328; }
  .rubric summary { cursor: pointer; color: #0b5cad; }
  .rubric ol { margin: 6px 0 6px 18px; padding: 0; }
  .task .st { color: #57606a; }
  .task .st b { color: #cf222e; }
</style>
</head>
<body>
<header>
  <h1>专家审核面板 · 参照实现</h1>
  <p>一条「问题 + 专业类别」一份意见。界面由平台侧重写,这里是<b>字段与后果的样子</b>。
     <span class="meta" id="counts"></span>
     <a href="/review" class="meta">← 回到只读面(看记录)</a></p>
  <p class="how">怎么做:<b>①</b> 左边选一条任务(可按风险高低筛) → <b>②</b> 右边<b>默认只给
     你要审的那条问题</b>和它在原文里的定位,想看整篇再点"读整篇反思原文" →
     <b>③</b> 选一个结论:认可 = 这段反思核心成立、无需实质修改;<b>建议修改</b> = 方向有价值但有
     遗漏/表述错/泛化过度,只改你负责那几句;<b>不成立</b> = 核心逻辑有实质问题。
     选「认可」时文本框会锁住(写了会被拒)。</p>
  <details class="rubric">
    <summary>审核基准(《技术集成流程》§七:所有专家共用这一套,不分工各写一套答案)</summary>
    <ol>
      <li><b>事实与因果是否成立</b>:有没有事实错误、错误归因,或把相关直接写成因果。</li>
      <li><b>是否真正吸收了本次经历</b>:是识别了判断里的关键问题,还是只把结果复述了一遍。</li>
      <li><b>结果偏见</b>：不能因为后来涨了/跌了,就倒推当时的判断必然对或必然错。</li>
      <li><b>不合理泛化</b>:一次经历不能直接变成一条过宽的未来规则。</li>
      <li><b>是否遗漏重要价值因素</b>:例如用户的风险暴露、不可逆后果、行动与不行动的机会成本。</li>
    </ol>
    <div class="meta">这一栏只是把研究侧定的审核规则摆在你手边;怎么判、判成哪一档都由你决定,
      系统不替你打分。原文见 0904doc《04｜Reflection → Governance Platform 技术集成流程》第七节。</div>
  </details>
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
     队列取最近记录里可建单的候选,并排在前面标出「已有 1 份、在等第二份」的那些;
     读正文请展开"反思全文"。</div></div>
</main>
<script>
var TASK = null, FULL = "";
function esc(s) { var d = document.createElement("div"); d.textContent = (s == null ? "" : String(s)); return d.innerHTML; }
function api(u, o) { return fetch(u, o).then(function (r) { return r.json(); }); }
// 两处刷新(进页面、提交完)用同一个式子,免得第二处把新增的那半句漏掉
function countsLine(m) {
  if (!m) return "";
  var ex = m.excluded || {}, why = [];
  if (ex.no_coordinates) why.push(ex.no_coordinates + " 行没有任务坐标");
  if (ex.non_expert_origin) why.push(ex.non_expert_origin + " 行来源不是专家(自测/模拟)");
  if (ex.superseded) why.push(ex.superseded + " 行是被新版本取代的旧版");
  return " · 盘上标记 " + m.total + " 行,计为意见 " + m.opinions_counted +
    " 份(按提交主体折,同一主体的修订只算一份),其中 " + m.tasks_with_two_or_more +
    " 个任务已集齐两位" + (why.length ? (";不计入:" + why.join("、")) : "");
}

var TASKS = [], RISK_ORDER = { high: 0, medium: 1, low: 2 }, POOL_AT = "";

api("/api/expert/queue?limit=10").then(function (q) {
  POOL_AT = q.pool_generated_at ? ("(上次导出 " + q.pool_generated_at + ")")
                               : "(还没有导出产物)";
  TASKS = q.tasks.slice().sort(function (a, b) {
    // 服务端已经按同一式子排过(见 expert_queue 末尾),这里只是重渲染时的稳定性兜底
    var s = (a.state_rank == null ? 9 : a.state_rank) - (b.state_rank == null ? 9 : b.state_rank);
    if (s !== 0) return s;
    var r = RISK_ORDER[a.risk] - RISK_ORDER[b.risk];
    return r !== 0 ? r : (a.run_id < b.run_id ? -1 : 1);
  });
  document.getElementById("summary").textContent =
    "本次扫描 " + q.n_runs_scanned + "/" + q.total_review_records +
    " 条记录,展开可建单任务 " + q.n_tasks + " 条" +
    (q.records_not_scanned ? "(还有 " + q.records_not_scanned + " 条记录没扫到)" : "(已扫完)") +
    " · 人工分流 " + q.n_manual_triage +
    " 条 · 不可审 " + q.n_blocked + " 条";
  if (!q.tasks.length) {
    document.getElementById("queue").innerHTML =
      "<div class='task err'>队列为空:扫过的这些记录里没有「问题+专业」的建单候选" +
      "(看上面的人工分流与不可审计数)。</div>";
  }
  drawQueue();
  document.getElementById("risk").onchange = drawQueue;
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
      // 05 §一:未点开也要显示"当前审核状态";05 §四:Disputed 要看得出来
                  "</div><div class='s st'>状态:" + (
      String(t.review_state || "?").indexOf("Disputed") >= 0
        ? "<b>" + esc(t.review_state) + "</b>" : esc(t.review_state || "?")) + "</div>";
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
    // 连点两条任务时,先那条的响应可能后到 —— 不丢弃就会出现"看的是 A 的正文、
    // 提交的是 B"这种错位(A 的反思贴在 B 的任务上)。只认当前选中任务的那一次。
    if (TASK !== t) return;
    var chars = document.getElementById("chars"), anch = document.getElementById("anch");
    if (!r.ok) { document.getElementById("body").innerHTML =
      "<span class='err'>" + esc((r.errors || []).join("; ")) + "</span>";
      // 这两个占位符不能停在"…":读不到正文是终态,不是还在加载
      if (chars) chars.textContent = "—(没读到正文)";
      if (anch) anch.textContent = "—"; return; }
    FULL = r.reflection_text;
    document.getElementById("chars").textContent = r.reflection_chars + " 字";
    // 默认不给整篇反思:《HCI 增量需求》§二"默认展示该专家需要审核的具体问题,
    // 而不是整篇 Reflection"。整篇与 Full Context 都放在点了之后才出现。
    document.getElementById("body").innerHTML =
      "<div class='meta'>正文按 05 §二 默认收起:下面两个按钮按需展开(全文 " +
      r.reflection_chars + " 字)。</div>" +
      "<button id='reflbtn'>读整篇反思原文</button>" +
      "<button id='ctxbtn'>展开自然语言 Full Context</button>" +
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
      (t.anchor.sentence_ids || []).join(", ") || "(交接包没给句子定位)";
  });
}

function renderForm() {
  var t = TASK, w = document.getElementById("work");
  var pl = t.pool || {};
  w.innerHTML =
    "<h2>问题 " + esc(t.issue_id) + " · 专业 " + esc(t.expert_category_id) + "</h2>" +
    "<div>" + esc(t.summary) + "</div>" +
    "<div class='meta'>路由理由:" + esc(t.routing_reason) + "</div>" +
    "<div class='quote'>" + esc(t.evidence_quote) + "</div>" +
    "<div class='meta'>审核状态:" + esc(t.review_state || "?") +
      " · 训练材料池:" + esc(pl.state || "?") + POOL_AT + "</div>" +
    "<div class='meta'>反思正文:<span id='chars'>…</span> · 你负责的原文定位 <code id='anch'>…</code></div>" +
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
    "<div class='warn' id='seg' style='display:none'>只写<b>你负责的这几句</b>的改文就对了:"
      + "《HCI 增量需求》§二明确 Edit「仅修改自己负责的问题 / 反思片段,不直接重写整篇 Reflection」。"
      + "<br>但要提前说清后果:导出侧现在还<b>不会</b>把片段合回整篇,所以这份改文会进审核材料、"
      + "<b>暂时进不了偏好对</b>(量纲门)。这是导出侧的待办,不是你写错了,也不需要你改成整篇。</div>" +
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
        th.textContent = b.dataset.v === "edit" ? "你建议的改文(只改你负责的片段,必填)"
                                               : "为什么这条不成立(必填)";
        box.placeholder = b.dataset.v === "edit"
          ? "照上面「原文定位」那几句改就行,不用重写整篇反思"
          : "写清核心逻辑哪里错(这段文字进审核材料,不当标准答案)";
        seg.style.display = b.dataset.v === "edit" ? "block" : "none";
      }
      document.getElementById("vw").textContent = "已选:" + b.textContent;
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
    """`/review/expert` 的别名,**不是**嵌入版式(本文件没有 `body.embed` 那套前端判定)。
    留着是为了与 `review_app` 的路由形状对齐;要做真压缩版式请照 `review_app.py:463` 补。"""
    return HTMLResponse(_EXPERT_PAGE)
