# -*- coding: utf-8 -*-
"""批次数据分析:把一批 run.json 汇总成可解读的统计 + 与历史基线的对比。

为什么要有这个工具
------------------
`batch_run.py` 负责把案例跑出来(台账 ledger.jsonl),但台账只有摘要字段。
要回答科研问题——**分支分布是否偏斜?反思质量稳不稳?Router 产出多少?
与答辩基线(25 条)相比是变好还是变差?——** 需要一次成型的统计。

它回答什么
----------
1. **分支分布**:自动判定是否塌缩到单一分支(这是真实风险:25 条历史里
   B 占 16 条,LLM 判定有往 B 塌的倾向);
2. **反思质量**:字数分布 + 质量门(pass/review/fail)比例 + 失败原因 Top N;
3. **一致性**:consistent / inconsistent / unknown 分布;
4. **Router 产出**:issues 总数与分布,零 issue 记录占比(过不了分流也是一种结论);
5. **与基线对比**:和 `答辩速查表.md` 的 25 条基线并排看,变化要能说出口。

用法
----
    # 分析一个批次(读 ledger.jsonl)
    python -m case01.tools.batch_analyze --batch 261003-124842-main3h

    # 直接扫 runs 目录下所有 run.json(不依赖批次台账)
    python -m case01.tools.batch_analyze --scan-runs --prefix batch-

    # 输出
    <batch>/analysis.json + analysis.md

口径
----
- **只统计真实存在的 run.json**;解析失败的单独计数,不混进分布。
- 反思字数按 `reflection.text` 长度;质量分取 `reflection.quality.score`。
- 台账行(`--batch`)是扁平摘要:字数/质量分/status/失败原因/issue 计数以
  `reflection_*`、`issues*` 为准,run.json 在场时仍以明细覆盖 —— 两条链
  (`--batch` 与 `--scan-runs`)在**进统计的字段**上必须给同一份 row。
  例外是台账独有字段 `seconds/attempt/model_actual/model_requested/seed`:
  一手 `run.json` 里没有这些键,`--scan-runs` 链上恒为空 ⇒ §5 的模型身份
  只有走 `--batch` 才取得到(走目录链会明说"未检出",不猜)。
- 基线数字来自 `results/analysis/答辩速查表.md`(25 条小镇面记录,2026-09-19~09-25)。
  这些记录**没有 manifest 键、也没有 rng.json**(2026-10-07 实测:目录里只有
  `run.json`/`run.json.bak`),模型身份没有任何一手字段;而它们的 T0 回答 24/25 是
  纯英文、反思却是中文(今日 4b 批的 T0 已是中文)⇒ "基线=4b"只是当期部署的推断。
  所以本节对比只作量级参照,不宣称显著性,也不宣称同档可比。
"""
import argparse
import glob
import json
import os
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
CASE01_DIR = os.path.dirname(HERE)
PROV_DIR = os.path.dirname(CASE01_DIR)
RUNS_DIR = os.path.join(CASE01_DIR, "runs")
BATCH_ROOT = os.path.join(PROV_DIR, "results", "analysis", "batch_runs")

# 答辩基线(25 条小镇面记录;记录内无模型字段,身份只能推断)——只作量级参照
BASELINE = {
    "n": 25,
    "branches": {"A": 2, "B": 16, "C": 7},
    "reflection_chars_mean": 2629,
    "issues_total": 109,
    "consistency": {"consistent": 18, "inconsistent": 3, "unknown": 4},
}


def _load_run(path: str) -> Optional[Dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _count(v) -> int:
    """列表给长度、整数直接给值。台账存的是计数,run.json 存的是列表——
    两种来源都要能读,否则分析器只能吃其中一种。"""
    if isinstance(v, (list, tuple)):
        return len(v)
    return v if isinstance(v, (int, float)) else 0


def _consistency_verdict(d: Dict) -> str:
    """取一致性判定,**两种形状都要吃**:run.json 里是 dict(带 verdict/reason),
    台账里是扁平字符串("consistent"/"unknown"/"")。

    2026-10-03 实测踩过:`_collect_from_ledger` 会把**台账行**当 run.json 喂给 `_row`
    (台账存 `consistency: "unknown"`,run.json 存 `consistency: {verdict: ...}`),
    于是 `(d.get("consistency") or {}).get("verdict")` 直接 AttributeError,
    **整份分析报告写不出来**。此前没暴露,是因为那会儿 consistency 恒为空串,
    `"" or {}` 兜住了 —— 一旦有人真的往记录里盖章,这个工具立刻死。
    """
    cs = d.get("consistency")
    if isinstance(cs, dict):
        return cs.get("verdict") or ""
    if isinstance(cs, str):
        return cs
    return ""


def _row(run_id: str, d: Dict, source: str = "ledger") -> Dict:
    """把一条 run.json 记录(或台账扁平摘要)归一成统计行。

    `source`(**2026-10-05 第七轮 N4/第九轮**):标明这条的来源是台账还是一手
    `run.json`。它必须**逐行**带而不是只在表头上,原因在体检 N4:台账缺段时补入的
    一手行与台账原有行在产物里**同形**,下游拿到 `per_run` 分不出哪几条的
    `seconds/attempt/model_actual` 是真的、哪几条是拿 run.json 造的(那些字段为空)。
    判"这条结论建立在什么数据上"必须能追到行级来源,否则补入行会被当成台账权威。
    取值:`"ledger"`(台账行) / `"primary"`(由一手 run.json 造的行)。
    """
    refl = d.get("reflection") or {}
    router = d.get("router") or {}
    quality = refl.get("quality") or {}
    post = router.get("postprocess") or {}
    issues = router.get("issues") or []
    reasons = quality.get("failure_reasons") or []
    row = {
        "run_id": run_id,
        "source": source,
        "branch": d.get("branch") or "",
        "consistency": _consistency_verdict(d),
        "refl_chars": _count(refl.get("text")) or len(refl.get("text") or ""),
        "refl_score": quality.get("score"),
        "refl_status": quality.get("status") or "",
        "refl_fail_reasons": reasons,
        "issues": _count(issues),
        "issues_final": post.get("final_issue_count", _count(issues)),
        "turns": _count(d.get("turns")),
        "events": _count(d.get("events")),
        # 失败/未执行的显式标记(2026-10-03):统计要能把它们摘出去,
        # 否则"没跑成"会被当成"跑出来但很差"混进均值。
        "router_status": router.get("status") or "",
        # 口径与 full_context._reflection_failed 保持一致:error 状态,或旧版
        # (2026-10-03 前)无 quality 块只剩占位文本,都算"没跑成";
        # status=fail 不算(那是质量门判真反思差)。
        "refl_error": ((quality.get("error") or "")
                       if quality.get("status") == "error"
                       else ("反思生成失败(旧版失败占位,无 quality 块)"
                             if (refl.get("text") or "").strip() in ("(反思生成失败)", "(失败)")
                             else "")),
        "router_error": (router.get("error") or "") if router.get("status") in ("error", "skipped") else "",
        # 台账独有字段(2026-10-07 补):`source` 那整段说明一直在讲"哪几条的
        # seconds/attempt/model_actual 是真的、哪几条是拿 run.json 造的",可这三个键
        # **从来没进过 row**(实测 21 份 analysis.json 的 per_run 里都没有)——行级来源
        # 因此无从判断,下游只能 KeyError。这里如实带上:一手行取不到就是 None/空,
        # 而那正是 "source=primary" 想表达的意思。
        # `model_actual` 是 batch_run 从响应里**检出**的服务端模型名(不是 --model 声明值),
        # §5 的"本批次用的什么模型"必须有真来源,以前那里写的是字面量 "qwen3:8b"。
        "seconds": d.get("seconds"),
        "attempt": d.get("attempt"),
        "model_actual": d.get("model_actual") or "",
        "model_requested": d.get("model_requested") or "",
        "seed": d.get("seed"),
    }
    # 台账行是**扁平摘要**(`reflection_chars/_score/_status/_failure_reasons`
    # 与 `issues/issues_final`,见 batch_run._digest),run.json 是嵌套结构。
    # 原先只吃嵌套侧,台账自带的这些字段全被丢掉、只能靠回读 run.json 补;
    # 一旦 run.json 不在(目录被清、换机分析、事后删档),这条记录就带着
    # refl_chars=0 / issues=0 溜进均值,报出来的是"模型写了一篇 0 字反思、
    # Router 一条 issue 都没挑",而不是"这条的明细读不到"。
    # 现在:嵌套侧给不出东西时以扁平台账字段为准。
    for key, flat_key in (("refl_chars", "reflection_chars"),
                          ("refl_score", "reflection_score"),
                          ("refl_status", "reflection_status"),
                          ("refl_fail_reasons", "reflection_failure_reasons"),
                          ("issues", "issues"),
                          ("issues_final", "issues_final")):
        if row[key] in (None, "", 0, []):
            v = d.get(flat_key)
            if v not in (None, ""):
                row[key] = v
    if not row["refl_error"] and row["refl_status"] == "error":
        row["refl_error"] = (";".join(str(x) for x in (row["refl_fail_reasons"] or []))
                             or "反思生成失败(台账只记了 status=error)")
    return row


def _is_failed(r: Dict) -> bool:
    """这条记录的反思或 Router 是不是"没跑成"(而非"跑出来质量差")。"""
    return bool(r.get("refl_error")) or (r.get("router_status") or "") in ("error", "skipped")


def _collect_from_runs(prefix: str = "") -> tuple:
    """扫 runs 目录。返回 (rows, 坏文件数)。"""
    rows, bad = [], 0
    for p in sorted(glob.glob(os.path.join(RUNS_DIR, "*", "run.json"))):
        run_id = os.path.basename(os.path.dirname(p))
        if prefix and not run_id.startswith(prefix):
            continue
        d = _load_run(p)
        if d is None:
            bad += 1
            continue
        rows.append(_row(run_id, d, source="primary"))
    return rows, bad


def _primary_ids(batch: str) -> List[str]:
    """枚举本批次**一手产物**的 run_id(扫 case01/runs/batch-<batch>-*/run.json)。

    2026-10-05 加(体检 G1):台账天生可能缺段(实测 h120seed 台账号 31 < 一手 35),
    只读台账会在缺段时**静默**产出"看起来完整"的表。这里给出独立的一手清单,
    供 `_collect_from_ledger` 对账用。批次为空/扫不到时返回空列表(调用方据此跳过对账)。
    """
    if not batch:
        return []
    pat = os.path.join(RUNS_DIR, "batch-{}-*".format(batch), "run.json")
    return sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(pat))


def _collect_from_ledger(batch: str, fill_from_primary: bool = False) -> tuple:
    """读批次台账。返回 (rows, 坏行数, gap)。

    有 run.json 的才算数(台账里失败行不掺进来)。

    `gap`(2026-10-05 加,体检 G1):把台账 id 集与**一手产物** id 集对账,不相等时
    给出 `{"n_primary": N, "missing": [一手有而台账无的 id], "extra": [台账有而一手无的 id]}`;
    两者一致或拿不到一手清单时为 `None`。此前这里是纯静默——缺 6 条也照出"完整"报告。

    `fill_from_primary`(2026-10-05 加):台账缺段时,把**一手有而台账无**的记录也用
    run.json 补进来(台账独有字段如 seconds/attempt 在这些行上留空)。默认**关**——
    默认行为必须是"如实报缺口",补齐要显式要求,免得把一次数据事故顺手抹平。

    `ledger_missing`(2026-10-05 加,体检 N5):**整份台账不存在**时不再静默返回空——
    那正是 261004-203137-h120b 的形态(台账永久丢失,29 条一手产物健在),旧写法会让
    `--batch --fill-from-primary` 报"0 条"而盘上明明有记录。这里改为:能拿到一手就照一手造行,
    并把 gap 标成 `"ledger_missing": True`,让报告知道这些行的台账来源字段天然为空。
    一手也扫不到才真的返回空(那种情况 `main` 会 `return 1`)。
    """
    d = os.path.join(BATCH_ROOT, batch)
    lp = os.path.join(d, "ledger.jsonl")
    if not os.path.exists(lp):
        primary = sorted(_primary_ids(batch))
        if not primary:
            return [], 0, None
        rows = [r for r in (_row(rid, _load_run(os.path.join(RUNS_DIR, rid, "run.json")),
                                source="primary")
                            for rid in primary) if r]
        gap = {"n_primary": len(primary), "missing": [], "extra": [], "filled": 0,
               "ledger_missing": True}
        return rows, 0, gap
    rows, bad = [], 0
    seen = set()
    with open(lp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            run_id = rec.get("run_id") or ""
            if not run_id or not rec.get("ok") or run_id in seen:
                continue          # 只留成功且未重复的
            seen.add(run_id)
            rows.append(_row(run_id, rec))
    # 台账摘要之外再回读 run.json 补全(与扫目录同口径)。
    # 2026-10-04 修:原先只在 `refl_status` 为空时回读,那是"台账没有质量字段"
    # 时的代理判断;一旦 _row 开始吃扁平字段,这个代理就永远为真、回读整段变成死代码,
    # 而 router.status(error/skipped)只有 run.json 里有 —— 不读就摘不出"Router 没跑成"。
    # 现在恒回读:文件在场就以明细为准,不在场就以台账摘要为准。
    #
    # source **保持 "ledger"**:这条的来源是台账,回读只是把台账行缺的字段填满,
    # 它仍然是一条台账行(seconds/attempt/model_actual 那些台账独有字段是真的)。
    # 只有下面 `missing` 那段由 run.json 从零造的行才算 "primary"。
    for r in rows:
        d2 = _load_run(os.path.join(RUNS_DIR, r["run_id"], "run.json"))
        if d2:
            full = _row(r["run_id"], d2, source="ledger")
            r.update({k: v for k, v in full.items() if v not in ("", None, [])})
            # `source` 不由上面那轮 update 决定(它靠"值非空"恰好覆盖,属于巧合而非契约):
            # 这一行的来源恒为台账,回读只填字段。
            r["source"] = "ledger"
    # 一手对账(2026-10-05,体检 G1)
    gap = None
    primary = set(_primary_ids(batch))
    if primary:
        missing = sorted(primary - seen)
        extra = sorted(seen - primary)
        if missing or extra:
            gap = {"n_primary": len(primary), "missing": missing, "extra": extra,
                   "filled": 0}
        if missing and fill_from_primary:
            # 补齐:缺的一手记录用 run.json 造行(台账独有字段在 _row 里天生为空)。
            for rid in missing:
                d2 = _load_run(os.path.join(RUNS_DIR, rid, "run.json"))
                if d2:
                    rows.append(_row(rid, d2, source="primary"))
                    gap["filled"] += 1
            rows.sort(key=lambda r: r["run_id"])
    return rows, bad, gap


def _dist(values: List, key=lambda x: x) -> Dict:
    out: Dict = {}
    for v in values:
        k = str(key(v))
        out[k] = out.get(k, 0) + 1
    return out


def _mean(vals) -> Optional[float]:
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 1) if vals else None


def _median(vals) -> Optional[float]:
    vals = sorted(v for v in vals if isinstance(v, (int, float)))
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else round((vals[mid - 1] + vals[mid]) / 2, 1)


def analyze(rows: List[Dict], bad: int, title: str, gap: Optional[Dict] = None) -> Dict:
    n = len(rows)
    # 失败/未执行的记录不进质量与产出统计(2026-10-03):它们的 0 分、0 issue
    # 不代表"质量差",而是"这一步没跑成"。混进均值会把结论带偏
    # (此前"反思生成失败"的占位文本就被当成一篇超短反思算进了字数均值)。
    # 摘出来单独报数 —— 不允许静默:条数与原因都写进结果。
    failed = [r for r in rows if _is_failed(r)]
    ok_rows = [r for r in rows if not _is_failed(r)]
    scores = [r["refl_score"] for r in ok_rows if isinstance(r["refl_score"], (int, float))]
    chars = [r["refl_chars"] for r in ok_rows]
    issues = [r["issues"] for r in ok_rows]

    fail_reasons: Dict = {}
    for r in rows:
        for reason in (r.get("refl_fail_reasons") or []):
            key = reason if isinstance(reason, str) else json.dumps(reason, ensure_ascii=False)
            fail_reasons[key] = fail_reasons.get(key, 0) + 1

    # 分支塌缩检测:最高占比 >60% 就明确标出来(这是要写进报告的判断,不是只给数字)
    br = _dist([r["branch"] or "(空)" for r in rows])
    top_branch, top_n = ("", 0)
    if br:
        top_branch, top_n = max(br.items(), key=lambda kv: kv[1])
    collapse = bool(n >= 10 and top_n / n > 0.60)

    # 质量门分布必须与同节的均值**同一总体**(2026-10-03 体检修):原来 status_dist
    # 取全量 rows、而字数/质量均值取 ok_rows,于是 pass 率的分母(43)比它旁边那句
    # "9 条已排除在上述均值之外"大了一圈,报告自相矛盾(261003-165042 报成 76.7%,
    # 真实值是 33/34=97.1%)。同一节里两个口径 = 读者必然读错。
    st = _dist([r["refl_status"] or "(无)" for r in ok_rows])
    cons = _dist([r["consistency"] or "(无)" for r in ok_rows])

    # 失败记录的单列:条数 + 逐条原因,让"没跑成"在报告里看得见
    failed_detail = []
    for r in failed:
        why = r.get("refl_error") or r.get("router_error") or "(未记录原因)"
        failed_detail.append({"run_id": r["run_id"], "reason": str(why)[:200]})

    return {
        "title": title,
        "n_runs": n,
        "n_unreadable": bad,
        # 一手对账(2026-10-05,体检 G1):台账与一手产物不等时非空,机器可读。
        # n_runs 是"本表实际统计了多少条"(以台账为准,补齐后含补入的一手行),
        # ledger_gap 说明台账原缺了几条;ledger_gap_resolved=True 表示缺口已用一手补齐
        # (此时 n_runs 已等于一手条数,不能只看 ledger_gap 判断表是否完整)。
        "ledger_gap": (gap or {}).get("missing") and len(gap["missing"]) or 0,
        "ledger_gap_resolved": bool(gap and gap.get("missing")
                                    and gap.get("filled") == len(gap["missing"])),
        # 整份台账不存在(体检 N5):上面两个字段会算成 0/False,那读起来像"台账与一手一致"。
        # 这个布尔量负责把那种形态单独标出来,别让它混进"无缺口"里。
        "ledger_missing": bool(gap and gap.get("ledger_missing")),
        "ledger_gap_detail": gap,
        # 模型身份(2026-10-07):`model_actual` 取台账里**检出**的服务端模型名,
        # 不是 `--model` 声明值,也不是 run.json 的 `manifest.judge_model`(那是客户端声明)。
        # 去重后是列表 —— 同一批里模型可能不止一个(换过模型、或检出为空)。
        "llm": {
            "model_actual": sorted({r["model_actual"] for r in rows
                                    if r.get("model_actual") and r["model_actual"] != "(未检出)"}),
            "model_requested": sorted({r["model_requested"] for r in rows
                                        if r.get("model_requested")
                                        and r["model_requested"] != "(默认)"}),
            "n_rows_with_model_actual": sum(1 for r in rows if r.get("model_actual")
                                            and r["model_actual"] != "(未检出)"),
        },
        "branches": br,
        "branch_top": {"branch": top_branch, "count": top_n,
                       "share": round(top_n / n, 3) if n else None,
                       "collapse_warning": collapse},
        "excluded_failed": {
            "n": len(failed),
            "n_scored": len(ok_rows),
            "note": ("反思或 Router 未跑成的记录已排除在质量/产出统计之外"
                     "(其 0 分与 0 issue 不代表质量)"),
            "records": failed_detail[:50],
        },
        "reflection": {
            "chars_mean": _mean(chars), "chars_median": _median(chars),
            "chars_min": min(chars) if chars else None,
            "chars_max": max(chars) if chars else None,
            "score_mean": _mean(scores), "score_median": _median(scores),
            "score_n": len(scores),
            "status_dist": st,
            "fail_reason_top": dict(sorted(fail_reasons.items(),
                                           key=lambda kv: -kv[1])[:8]),
        },
        "router": {
            "issues_total": sum(issues), "issues_mean": _mean(issues),
            "issues_median": _median(issues),
            "zero_issue_runs": sum(1 for c in issues if c == 0),
            "issues_dist": _dist(issues),
            # 分母必须写清(2026-10-05 第十轮4.1):`n` 是**参与 Router 统计的行数**,
            # 即 `len(ok_rows)` —— "没跑成"的样本已被剔除。原先它叫 `n`、旁边只有
            # 一个 `zero_issue_runs`,读起来像"该批次没有零问题样本",而真实含义是
            # "零问题的那几条没进分母"。实测 165042 批次:n=34 而样本 43——
            # 那 9 条 router error(占 21%)在专家表与聚合里**同时消失**。
            # 改名 `n_scored` + 显式给出被剔除的条数与状态分布,让分母无法被误读。
            "n_scored": len(issues),
            "n_excluded_not_scored": len(rows) - len(issues),
            "router_status_dist": _dist([r.get("router_status") or "ok" for r in rows]),
            # 兼容旧键:留 `n` 但语义写进注释,免得老脚本 KeyError。
            # 新读者请用 `n_scored`。
            "n": len(issues),
            # 自述字段(2026-10-06 补,第三方追问"4.7 的分母是谁"):把均值的分母**写进产物**,
            # 免得读者去读代码或默认成 `n_runs`。实测 261004-220441-h120seed:
            # issues_total=154 / n_scored=33 ⇒ 4.7;而 n_runs=35(2 条 router skipped 被剔除)。
            "issues_mean_over": "n_scored",
            "issues_mean_formula": "issues_total / n_scored",
        },
        "consistency": cons,
        "per_run": rows,
    }


def _md(a: Dict) -> str:
    L = [
        "# 批次数据分析 · {}".format(a["title"]),
        "",
        "- 有效记录:**{}** 条(另有 {} 个文件解析失败,未计入)".format(
            a["n_runs"], a["n_unreadable"]),
    ]
    # 一手对账告警(2026-10-05,体检 G1):台账缺段必须写在**报告顶部**,不能只在 JSON 里。
    # 两种情形措辞要分开 —— 补齐后本表已与一手一致,若还写"本表与一手不一致"就是诬告自己。
    gap = a.get("ledger_gap_detail")
    if gap:
        miss = gap.get("missing") or []
        extra = gap.get("extra") or []
        filled = gap.get("filled") or 0
        # 补全 = 一手有而台账无的每一条都补进来了。注意 `filled` 只是计数,
        # 用 `filled == len(miss)` 判断比切片清楚。
        resolved = bool(miss) and filled == len(miss)
        L.append("")
        if gap.get("ledger_missing"):
            # 整份台账不存在(体检 N5):措辞必须与"缺段"分开——这里不是少记,是全都没有,
            # 因此本表的台账独有字段(seconds/attempt/model_actual)全部为空。
            L.append("> ⚠ **本批次台账整份缺失**:目录 `ledger.jsonl` 不存在;"
                     "本表 **{}** 条全部由一手 `run.json` 重建,"
                     "台账独有字段(seconds/attempt/model_actual)在本表**全为空**。".format(
                         a["n_runs"]))
        elif resolved:
            L.append("> ⚠ **台账缺段(已用一手补齐)**:台账 `ledger.jsonl` 少记 **{}** 条,"
                     "已从一手 `run.json` 补入;本表 **{}** 条 == 一手条数。".format(
                         filled, a["n_runs"]))
        else:
            # 光说"一手 N 条、本表 M 条"在 N==M 时读起来像假警报,而真实的差**在侧别上**
            # (台账有、盘上没)。所以把差在哪一侧直接写进这句,而不是只给两个数。
            why = []
            if miss:
                why.append("台账少记 {} 条".format(len(miss)))
            if extra:
                why.append("台账有 {} 条的一手 run.json 已不在盘上".format(len(extra)))
            L.append("> ⚠ **本表与一手产物不一致**:一手 `run.json` **{}** 条,"
                     "本表基于 **{}** 条 —— {}。".format(
                         gap.get("n_primary"), a["n_runs"],
                         "、".join(why) or "两侧条数同但 id 集不同"))
        if miss:
            L.append("> 缺(一手有、台账无){} 条:{}{}".format(
                len(miss), ", ".join(miss[:12]), " …" if len(miss) > 12 else ""))
        if extra:
            L.append("> 多(台账有、一手无){} 条:{}{}".format(
                len(extra), ", ".join(extra[:12]), " …" if len(extra) > 12 else ""))
        if not resolved and not gap.get("ledger_missing"):
            L.append("> **以上分支分布仅基于 {} 条,不代表本批次全体。**".format(a["n_runs"]))
    L.extend([
        "",
        "## 1. 分支分布",
        "",
        "| 分支 | 条数 | 占比 |",
        "| --- | --- | --- |",
    ])
    tot = max(a["n_runs"], 1)
    for b, c in sorted(a["branches"].items(), key=lambda kv: -kv[1]):
        L.append("| {} | {} | {:.1%} |".format(b, c, c / tot))
    bt = a["branch_top"]
    L += ["", "**判定:{}**".format(
        "⚠️ 出现塌缩倾向——`{}` 占 {:.1%},单跑一条时分支不再由内容决定,"
        "批量统计分支占比意义有限,需改用 `--timeline` 强制均衡铺。"
        .format(bt["branch"], bt["share"]) if bt["collapse_warning"]
        else "分布未塌缩,分支由 LLM 判定自然产生。")]
    if a["branch_top"]["branch"]:
        L.append("（最高分支:{} · {} 条 · {:.1%}）".format(
            bt["branch"], bt["count"], bt["share"]))
    L += [
        "",
        "## 2. 反思质量",
        "",
        "- 字数:均值 {} · 中位 {} · 区间 {}–{}".format(
            a["reflection"]["chars_mean"], a["reflection"]["chars_median"],
            a["reflection"]["chars_min"], a["reflection"]["chars_max"]),
        "- 质量分:均值 {} · 中位 {}（n={}）".format(
            a["reflection"]["score_mean"], a["reflection"]["score_median"],
            a["reflection"]["score_n"]),
        "- 质量门:{}".format(", ".join(
            "{}={}".format(k, v)
            for k, v in sorted(a["reflection"]["status_dist"].items()))),
    ]
    ex = a.get("excluded_failed") or {}
    if ex.get("n"):
        L += [
            "",
            "**⚠️ 反思/Router 未跑成的记录:{} 条,已排除在上述均值之外**"
            "（计入统计的有效记录 {} 条）。".format(ex["n"], ex["n_scored"]),
            "",
            "它们的 0 分与 0 issue 不代表质量,而是这一步没执行 —— 混进均值会把"
            "结论带偏,故单列于此。逐条原因:",
            "",
            "| run | 原因 |",
            "| --- | --- |",
        ]
        for rec in ex.get("records", []):
            L.append("| {} | {} |".format(rec["run_id"], rec["reason"].replace("|", "\\|")))
    if a["reflection"]["fail_reason_top"]:
        L += ["", "**失败原因 Top N**", "", "| 原因 | 次数 |", "| --- | --- |"]
        for k, v in a["reflection"]["fail_reason_top"].items():
            L.append("| {} | {} |".format(k[:110], v))
    r = a["router"]
    L += [
        "",
        "## 3. Router 产出",
        "",
        "- issues:合计 {} · 均值 {} · 中位 {}".format(
            r["issues_total"], r["issues_mean"], r["issues_median"]),
        "- 均值的分母:**{} 条**（`issues_total / n_scored`；本批次总 {} 条，"
        "另有 {} 条 Router 未产出被剔除 — 见 `router_status_dist`）".format(
            r.get("n_scored"), a["n_runs"], r.get("n_excluded_not_scored")),
        "- 零 issue 记录:{} 条（占 {:.1%}，分母为参与统计的 {} 条）".format(
            r["zero_issue_runs"],
            r["zero_issue_runs"] / r["n"] if r["n"] else 0, r["n"]),
        "- 每条记录 issue 数分布:{}".format(
            ", ".join("{}条→{}次".format(k, v)
                      for k, v in sorted(r["issues_dist"].items()))),
        "",
        "## 4. 一致性",
        "",
        "| 判定 | 条数 |", "| --- | --- |",
    ]
    for k, v in sorted(a["consistency"].items(), key=lambda kv: -kv[1]):
        L.append("| {} | {} |".format(k, v))
    # §5 的"本批次用什么模型"以前是**字面量** "qwen3:8b"(实测 25 份产物里连 4b 批次
    # 都写着 8b,连带那句"不同模型只作量级参照"一起成了假话)。上一版改成取台账的
    # `model_actual`,但那是**"探针那一刻 Ollama 驻留的是谁"**,不是这条记录用的模型:
    # 2026-10-07 实测 thinkOn 批两条全请求 4b,第 1 条却检成 8b(上一批的 8b 还没卸载),
    # 于是标题写成"本批次(8b)"。标题取**请求值**(`--model`,整批一个值),
    # 检出值只在下面那行 ⚠ 里并排给;两个都取不到就明说"未检出",不替读者猜。
    llm = a.get("llm") or {}
    req = llm.get("model_requested") or []
    models = llm.get("model_actual") or []
    caveat = ("基线那 25 条**没有 manifest**,模型身份无从取证 ⇒ 不断言两边同档,"
              "两列只作量级参照,不宣称显著性。")
    if req:
        b_label = " / ".join(req)
    elif models:
        b_label = "{}(台账无请求值,只能用检出值)".format(" / ".join(models))
    else:
        b_label = "(台账未记模型名)"
        caveat = "本批次模型**未记** ⇒ 与基线是否同口径无从判断,本节对比**先别引用**。"
    L += [
        "",
        "## 5. 与答辩基线对比（25 条 · 模型未记档）",
        "",
        "> **本批次模型(请求值):** {} —— {}".format(b_label, caveat),
    ]
    # 检出≠请求时单独说一行:`model_actual` 来自"当时驻留的是谁"的探针,2026-10-07 实测
    # 261007-110702-seedOn8b 第 1 条请求 8b、检成 4b(第 2 条检成 8b),thinkOn 反向同理。
    # 不说,读者就会把检出值当成该条真正用过的模型。
    if req and models and set(models) != set(req):
        L.append("> ⚠ **驻留探针检出的模型 ≠ 请求模型**:请求 {} / 检出 {} —— `model_actual` 记的是"
                 "探针那一刻 Ollama 驻留的模型,不等于本条实际用的;逐条对照看行级 "
                 "`model_requested` 与 `model_actual`。".format(" / ".join(req),
                                                          " / ".join(models)))
    L += [
        "",
        "| 指标 | 基线(25 条·无 manifest) | 本批次(n={}) |".format(a["n_runs"]),
        "| --- | --- | --- |",
        "| 反思均字 | {} | {} |".format(
            BASELINE["reflection_chars_mean"], a["reflection"]["chars_mean"]),
        "| issues 总数 | {} | {} |".format(BASELINE["issues_total"],
                                          r["issues_total"]),
        "| issues/条 | {:.1f} | {} |".format(
            BASELINE["issues_total"] / BASELINE["n"], r["issues_mean"]),
        "| 分支 A/B/C | {} | {} |".format(
            "/".join(str(BASELINE["branches"].get(x, 0)) for x in "ABC"),
            "/".join(str(a["branches"].get(x, 0)) for x in "ABC")),
        "",
        "## 6. 结论",
        "",
    ]
    concl = []
    if a["reflection"]["status_dist"]:
        st = a["reflection"]["status_dist"]
        passed = st.get("pass", 0)
        # 分母 = 参与统计的记录数(status_dist 现在只数未跑成的,见上面的注释),
        # 与本节均值同口径,并把两个数都打出来,免得读者再猜分母是谁。
        denom = sum(st.values())
        concl.append("- 反思质量门 pass 率 {:.1%}（{}/{}，分母已摘除“没跑成”的记录）。".format(
            passed / denom if denom else 0, passed, denom))
    if r["issues_total"] == 0:
        concl.append("- **Router 零产出**:全批次没有拆出任何 issue,分流环节在"
                     "本批次未生效,需先排查再采信其它指标。")
    if bt["collapse_warning"]:
        concl.append("- **分支塌缩**:{} 占 {:.1%},自动判定模式下的分支分布"
                     "不可用于统计,后续批次应强制均衡铺。".format(
                         bt["branch"], bt["share"]))
    concl.append("- Router 每条产出 {} 个 issue，与基线 {:.1f} 个{}。".format(
        r["issues_mean"], BASELINE["issues_total"] / BASELINE["n"],
        "基本持平" if abs((r["issues_mean"] or 0)
                       - BASELINE["issues_total"] / BASELINE["n"]) < 1.0 else "有差距"))
    L.extend(concl)
    return "\n".join(L) + "\n"


def _write_atomic(path: str, text: str) -> None:
    """把 `text` 写进 `path`,**要么全写进去,要么原文件一字不动**(2026-10-05 第十一轮 N11-1)。

    为什么不能直接 `open(path, "w")`:那会**先 truncate 再逐块写**。写到一半
    (磁盘满、进程被杀、机器休眠)就留下一个**被截断的** analysis.json/json.load
    直接炸,而不存在的文件只会被当成"还没跑"——**截断文件比缺文件更难查**。

    做法:同目录临时文件写完 → `os.replace` 原子换名。同目录是硬要求
    (`os.replace` 跨文件系统会报错),临时文件用 `.` 前缀并在异常时清掉。
    """
    d = os.path.dirname(os.path.abspath(path)) or "."
    tmp = os.path.join(d, "." + os.path.basename(path) + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())          # 落盘再换名,否则崩溃后可能仍是旧内容
        os.replace(tmp, path)
    except BaseException:
        # 含 KeyboardInterrupt:换名失败时**必须**把临时文件收掉,否则
        # 下次跑会在目录里留下 .analysis.json.tmp 干扰人工排查。
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="批次数据分析 + 基线对比")
    ap.add_argument("--batch", default="", help="批次号(读其 ledger.jsonl)")
    ap.add_argument("--scan-runs", action="store_true",
                    help="直接扫 case01/runs 下所有 run.json")
    ap.add_argument("--prefix", default="", help="配合 --scan-runs 按 run_id 前缀过滤")
    ap.add_argument("--out", default="", help="输出目录(默认写批次目录)")
    ap.add_argument("--fill-from-primary", action="store_true",
                    help="台账缺段时用一手 run.json 补齐(默认只报缺口不补)")
    args = ap.parse_args(argv)

    if not args.batch and not args.scan_runs:
        ap.error("给 --batch <批次号> 或 --scan-runs")

    gap = None
    if args.batch:
        rows, bad, gap = _collect_from_ledger(args.batch, args.fill_from_primary)
        title = args.batch
    else:
        rows, bad = _collect_from_runs(args.prefix)
        title = "runs 扫描{}".format("/" + args.prefix if args.prefix else "")

    if not rows:
        # ⚠ 这句原先只说"确认 run.json 是否已落盘",而批次**整批失败**时 run.json 本来就不该有:
        # 台账旁边躺着 failures.jsonl,读的人却被告知去查产物(261007-163008-rep6h 就是这样,
        # 两条全死在 T0 超时,按旧提示会往"产物没落盘"的方向查,而真原因就在同一个目录里)。
        msg = "没有可用记录(bad={}）".format(bad)
        fails_p = os.path.join(BATCH_ROOT, args.batch, "failures.jsonl") if args.batch else ""
        if fails_p and os.path.isfile(fails_p):
            with open(fails_p, encoding="utf-8") as f:
                fails = [json.loads(x) for x in f if x.strip()]
            if fails:
                # 失败行的 schema 在 batch_run 里:原因躺 `log_tail`(traceback 尾),
                # 不是 `error`/`reason`(那两个键压根不存在,写成那样就又报"原因未记")。
                last = fails[-1]
                lines = [ln.strip() for ln in (last.get("log_tail") or "").splitlines() if ln.strip()]
                # `log_tail` 是 stdout/stderr 的尾巴,末行往往是台词正文而不是异常行,
                # 直接取末行会把"HCM 当前并不值得买入"当成失败原因(实测踩过)。
                why = next((ln for ln in reversed(lines)
                            if ("Error" in ln or "Exception" in ln or "error:" in ln)), "")
                if not why:
                    why = lines[-1][:200] if lines else "(log_tail 为空)"
                msg += "——台账 0 条成功、failures.jsonl 有 {} 条失败,最后一条 rc={} 用时 {}s,原因:{}".format(
                    len(fails), last.get("returncode"), last.get("seconds"), why[:200])
            else:
                msg += "——failures.jsonl 存在但是空的,确认批次是否真跑过"
        else:
            msg += "——确认 run.json 是否已落盘"
        print(msg)
        return 1

    a = analyze(rows, bad, title, gap)
    out_dir = args.out or (os.path.join(BATCH_ROOT, args.batch) if args.batch
                           else BATCH_ROOT)
    os.makedirs(out_dir, exist_ok=True)
    # 先把两份内容都算好再落盘:写第二份时失败,第一份**也还没被动过**
    # (各自的 _write_atomic 只会把同名旧文件换掉,不会留下半份新的)。
    p_json = os.path.join(out_dir, "analysis.json")
    p_md = os.path.join(out_dir, "analysis.md")
    _write_atomic(p_json, json.dumps(a, ensure_ascii=False, indent=2))
    _write_atomic(p_md, _md(a))

    print("有效 {} 条(坏 {} 个)→ {}".format(a["n_runs"], a["n_unreadable"],
                                        os.path.join(out_dir, "analysis.md")))
    if gap:
        miss = gap.get("missing") or []
        extra = gap.get("extra") or []
        filled = gap.get("filled") or 0
        if gap.get("ledger_missing"):
            # 整份台账不在,但一手全在:条数对得上,这里**不是**"与一手不一致"
            # (旧写法会打那一句,实测 261004-203137-h120b 报成"一手 29 条,本表 29 条;
            # 缺 0 条" —— 数字自相矛盾的假警报)。要说的是"这些行的台账字段天然为空"。
            print("⚠ 台账整份缺失:本表 {} 条全部由一手 run.json 重建,"
                  "seconds/attempt/model_actual 在本表全空".format(a["n_runs"]))
        elif miss or extra:
            tag = ("台账缺段(已用一手补齐)" if miss and filled == len(miss) and not extra
                   else "与一手不一致")
            print("⚠ {}:一手 {} 条,本表 {} 条;缺(台账少记){} 条:{};多(盘上无 run.json){} 条:{}{}"
                  "  {}".format(tag, gap["n_primary"], a["n_runs"], len(miss),
                                ", ".join(miss[:8]), " …" if len(miss) > 8 else "",
                                len(extra), ", ".join(extra[:8]),
                                " …" if len(extra) > 8 else ""))
    print("分支分布:{}".format(a["branches"]))
    print("反思均字 {} · 质量分均值 {} · issues 合计 {}".format(
        a["reflection"]["chars_mean"], a["reflection"]["score_mean"],
        a["router"]["issues_total"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
