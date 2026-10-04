# -*- coding: utf-8 -*-
"""批量跑 case01 案例:多路并发、失败重试、逐条落日志、到点自停。

为什么有这个工具
----------------
单条 `python -m case01.run` 约 70-110s(本地 Ollama)。要拿到**统计显著**的
样本量(反思质量分布、分支判定一致性、Router 路由质量),就得能连续跑几十到
上百条。本工具负责并发调度与失败隔离,让人只管开跑与收数据。

并发为什么这么设计
------------------
瓶颈在**单张 GPU 上的 Ollama 串行推理**,不在 CPU(实测 24 核机器上 3 路并发
总耗时 287s/3 条 = 95.7s/条,相对单路 111s/条只快 1.16×)。所以:

- 默认并发 **3**——再高只会在显存上排队(8GB 卡跑 8b 已经很紧),不会更快;
- 但 3 路能让 GPU 与 Python 侧的编排/序列化/落盘重叠,少一些空转;
- 每路一个独立子进程,一条崩了不带走其他。

用法
----
    # 跑满 3 小时,3 路并发,qwen3:8b,分支全走 LLM 自动判定
    python -m case01.tools.batch_run --duration-min 180

    # 指定条数上限 / 只跑固定分支 / 换模型
    python -m case01.tools.batch_run --max-runs 50 --timeline A --model qwen3:8b

    # 快速自检(2 分钟内结束,看调度器本身有没有毛病)
    python -m case01.tools.batch_run --duration-min 2 --max-runs 4

产物
----
- 每条案例:`case01/runs/<run_id>/run.json`(由 `case01.run` 自己落盘);
- 调度台账:`results/analysis/batch_runs/<批次号>/ledger.jsonl`
  (每条一行:run_id/分支/反思质量/issues/耗时/重试次数,供事后统计);
- 失败明细:同目录 `failures.jsonl`(含 stderr 尾部,便于判断是模型崩还是代码崩);
- 收尾汇总:`summary.json` + `summary.md`(分布与均值)。

依赖
----
只用标准库 + `subprocess`;不引新包。子进程跑的是仓库自带的
`python -m case01.run`,不复制其逻辑 —— 单一事实来源仍在 case01 侧。
"""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional

from case01.safestream import tolerant_stdout

HERE = os.path.dirname(os.path.abspath(__file__))          # .../case01/tools
CASE01_DIR = os.path.dirname(HERE)                        # .../case01
PROV_DIR = os.path.dirname(CASE01_DIR)                    # .../provenance
RUNS_DIR = os.path.join(CASE01_DIR, "runs")
ANALYSIS_ROOT = os.path.join(PROV_DIR, "results", "analysis", "batch_runs")

# 默认解释器:mavis 仓的 venv(mavisframework 装在那儿)。可用 --python 覆盖。
DEFAULT_PYTHON = r"D:\zzr\mavis\.venv\Scripts\python.exe"

_write_lock = threading.Lock()


def _log(msg: str) -> None:
    with _write_lock:
        print("[{}] {}".format(time.strftime("%H:%M:%S"), msg), flush=True)


def _append_jsonl(path: str, obj: Dict) -> None:
    with _write_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _batch_id() -> str:
    return time.strftime("%y%m%d-%H%M%S")


def _read_run(run_id: str) -> Optional[Dict]:
    """读一条案例的 run.json;取不到返回 None(不猜、不补默认值)。"""
    p = os.path.join(RUNS_DIR, run_id, "run.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _digest(run_id: str) -> Dict:
    """从 run.json 抽出统计用的关键字段(缺字段就是缺,不填假值)。"""
    d = _read_run(run_id) or {}
    refl = d.get("reflection") or {}
    router = d.get("router") or {}
    quality = refl.get("quality") or {}
    post = router.get("postprocess") or {}
    issues = router.get("issues") or []
    return {
        "run_id": run_id,
        "branch": d.get("branch") or "",
        "consistency": ((d.get("consistency") or {}).get("verdict") or ""),
        "reflection_chars": len(refl.get("text") or ""),
        "reflection_score": quality.get("score"),
        "reflection_status": quality.get("status") or "",
        "reflection_failure_reasons": quality.get("failure_reasons") or [],
        "issues": len(issues),
        "issues_final": post.get("final_issue_count", len(issues)),
        "turns": len(d.get("turns") or []),
        "events": len(d.get("events") or []),
        "read_ok": _read_run(run_id) is not None,
    }


def _ollama_loaded_model(base_url: str = "http://127.0.0.1:11434") -> str:
    """问 Ollama 当前**实际驻留**的 chat 模型名(空=查不到)。

    为什么记实测而不是只记请求值:2026-10-03 踩过——`--model qwen3:8b` 请求了 8b,
    但 orchestrator 用无参 `OllamaClient()` 构造,吃签名默认值 4b,环境变量被绕过,
    于是**跑了两小时都是 4b 而台账里写着 8b**。只记请求值会把这件事永久藏起来。

    **必须排除 embedding 模型**:检索链路上 qwen3-embedding 会同时驻留,
    早期版本按"名字最长的当主模型"选,结果把 embedding 记成了 chat 模型
    (7/21 条误标)。这里按能力字段筛,只认 `completion` 能力的那种。
    """
    try:
        import urllib.request
        with urllib.request.urlopen(base_url + "/api/ps", timeout=5) as r:
            models = json.loads(r.read().decode("utf-8")).get("models") or []
        chats = [m for m in models
                 if "completion" in (m.get("capabilities") or [])
                 and "embedding" not in (m.get("capabilities") or [])]
        if not chats:
            # /api/ps 不回 capabilities 时退回按名字排除 embedding
            chats = [m for m in models
                     if "embedding" not in (m.get("name") or "").lower()]
        names = [m.get("name") for m in chats if m.get("name")]
        # 多个 chat 模型时取 VRAM 最大的(才是真正在干活的那个)
        if not names:
            return ""
        vram = {m.get("name"): m.get("size_vram") or 0 for m in chats}
        return max(names, key=lambda n: vram.get(n, 0))
    except Exception:  # noqa: BLE001 —— 查不到不影响主流程
        return ""


def _run_with_probe(cmd, cwd: str, env: Dict, log_path: str, timeout: int,
                    append: bool = False) -> tuple:
    """跑子进程并在**推理中途**采一次 Ollama 驻留模型。

    为什么要中途采:推理结束后 Ollama 会卸载模型,事后再查 `/api/ps` 常常是空
    (踩过一次 —— 台账里 model_actual 记成空,等于没记)。
    `append=True` 时续写日志(重试用同一份日志,便于事后看完整轨迹)。
    返回 (returncode, 采到的模型名);模型名可能为空(查不到不编造)。
    """
    seen = ""
    try:
        with open(log_path, "a" if append else "w", encoding="utf-8") as lf:
            if append:
                lf.write("\n\n===== retry =====\n")
                lf.flush()
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=lf,
                                    stderr=subprocess.STDOUT)
            deadline = time.time() + timeout
            while proc.poll() is None and time.time() < deadline:
                if not seen:
                    seen = _ollama_loaded_model()
                time.sleep(5)
            code = proc.wait()
            if not seen:
                seen = _ollama_loaded_model()
            return code, seen
    except Exception as exc:  # noqa: BLE001 —— 单条崩不带走整批
        return -1, seen


def _failure_tail(log_path: str, limit: int = 3000) -> str:
    """失败日志里**真正解释失败的那一段**。

    原来固定取文件末尾 1500 字符,踩过的坑(2026-10-03 批次 261003-165042/014):
    重试机制会在 traceback **之后**继续写正常输出,于是尾部 1500 字里根本没有
    `TimeoutError` —— 台账里只看到 rc=1,失败原因得翻原始 .log 才知道,
    等于失败原因在批处理层丢了。这里改成从**最后一个 Traceback 起点**截到文末;
    找不到 traceback 就退回文件末尾。
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    idx = text.rfind("Traceback (most recent call last)")
    tail = text[idx:] if idx >= 0 else text[-limit:]
    return tail[-limit:]


def _child_env(args_ns) -> Dict:
    """组装子进程(`python -m case01.run`)的环境。

    `PYTHONIOENCODING=utf-8` 是必须的,不是美化:日志文件由本进程按 utf-8 打开并
    交给子进程当 stdout,而子进程默认按 Windows locale(GBK)编码文本流 —— 中文
    回答进了 .log 就是乱码,`_failure_tail` 再按 utf-8 读回来时靠 errors=replace
    兜底,失败原因糊成一片(实测批次 261004-123726-h120/-035 的 retry 段)。
    钉死 utf-8 后,GBK 下会抛 UnicodeEncodeError 的 `▶` 之类字符也一并安全落地。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if args_ns.model:
        env["CASE01_LLM_MODEL"] = args_ns.model
    if args_ns.embed_model:
        env["CASE01_EMBED_MODEL"] = args_ns.embed_model
    if args_ns.disable_thinking:
        env["CASE01_LLM_DISABLE_THINKING"] = "1"
    else:
        env.pop("CASE01_LLM_DISABLE_THINKING", None)
    return env


def _one_run(args_ns, run_id: str, out_dir: str) -> Dict:
    """跑一条案例。返回台账行;失败不抛异常(一条崩不带走整批)。"""
    env = _child_env(args_ns)

    cmd = [args_ns.python, "-m", "case01.run", "--run-id", run_id]
    if args_ns.timeline:
        cmd += ["--timeline", args_ns.timeline]
    if args_ns.external_ethan:
        cmd += ["--external-ethan"]

    t0 = time.time()
    rec = {"run_id": run_id, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "cmd": " ".join(cmd), "attempt": 1, "timeline": args_ns.timeline or "auto",
           "model_requested": args_ns.model or "(默认)"}
    log_path = os.path.join(out_dir, "{}.log".format(run_id))
    rc, seen_model = _run_with_probe(cmd, PROV_DIR, env, log_path, args_ns.timeout)
    rec["returncode"] = rc
    rec["seconds"] = round(time.time() - t0, 1)

    # 重试:偶发失败(模型超时/OOM/网络)值得再试;确定性失败重试无意义。
    if rec["returncode"] != 0 and args_ns.retries > 0:
        for attempt in range(2, args_ns.retries + 2):
            _log("  重试 {}/{}: {}".format(attempt - 1, args_ns.retries, run_id))
            rec["attempt"] = attempt
            t0 = time.time()
            rc2, seen2 = _run_with_probe(cmd, PROV_DIR, env, log_path,
                                        args_ns.timeout, append=True)
            rec["returncode"] = rc2
            seen_model = seen_model or seen2
            rec["seconds"] = round(rec.get("seconds", 0) + time.time() - t0, 1)
            if rec["returncode"] == 0:
                break

    rec.update(_digest(run_id))
    # 记实测模型:用运行中途采到的值(见 _ollama_loaded_model 的注释——请求值不可信)
    rec["model_actual"] = seen_model or "(未检出)"
    rec["ok"] = rec["returncode"] == 0 and rec.get("read_ok", False)
    # 口径事故要显式可见:请求与实测不一致时在台账里留痕,别指望分析阶段才发现
    if rec["model_actual"] != "(未检出)" and rec["model_requested"] not in (
            "(默认)", rec["model_actual"]):
        rec["model_mismatch"] = True
        _log("  ⚠️ 模型不符:请求 {} 实测 {} —— {}".format(
            rec["model_requested"], rec["model_actual"], run_id))
    if not rec["ok"]:
        rec["log_tail"] = _failure_tail(log_path)
    _append_jsonl(os.path.join(out_dir, "ledger.jsonl"), rec)
    if not rec["ok"]:
        _append_jsonl(os.path.join(out_dir, "failures.jsonl"), rec)
    return rec


def _worker(idx: int, args_ns, job_q: "queue.Queue", out_dir: str,
            stop_at: float, stats: Dict) -> None:
    """一条并发通道:取任务、跑到出结果或到点。"""
    while time.time() < stop_at:
        try:
            run_id = job_q.get_nowait()
        except queue.Empty:
            return
        if time.time() >= stop_at:
            _log("lane{}: 到点,不再开新任务(丢弃 {})".format(idx, run_id))
            job_q.task_done()
            return
        _log("lane{}: 开始 {}".format(idx, run_id))
        rec = _one_run(args_ns, run_id, out_dir)
        with _write_lock:
            stats["done"] += 1
            if rec["ok"]:
                stats["ok"] += 1
            else:
                stats["failed"] += 1
        _log("lane{}: {} {} branch={} 反思={}字 分={} issues={} 用时{}s".format(
            idx, "OK" if rec["ok"] else "FAIL", run_id, rec.get("branch") or "-",
            rec.get("reflection_chars", 0), rec.get("reflection_score", "-"),
            rec.get("issues", 0), rec.get("seconds", 0)))
        job_q.task_done()


def _summarize(out_dir: str, batch: str, stats: Dict, args_ns) -> Dict:
    """收尾汇总:分布 + 均值。只统计真实读到的 run.json,不掺假。"""
    rows = []
    lp = os.path.join(out_dir, "ledger.jsonl")
    if os.path.exists(lp):
        with open(lp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    ok_rows = [r for r in rows if r.get("ok")]
    summary = {
        "batch": batch,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "settings": {
            "model": args_ns.model, "timeline": args_ns.timeline or "auto",
            "lanes": args_ns.lanes, "duration_min": args_ns.duration_min,
            "disable_thinking": bool(args_ns.disable_thinking),
            "python": args_ns.python,
        },
        "counts": dict(stats, ledger_rows=len(rows)),
        "branches": {},
        "reflection": {},
        "router": {},
    }
    for r in ok_rows:
        b = r.get("branch") or "(空)"
        summary["branches"][b] = summary["branches"].get(b, 0) + 1

    def _mean(vals):
        vals = [v for v in vals if isinstance(v, (int, float))]
        return round(sum(vals) / len(vals), 1) if vals else None

    chars = [r.get("reflection_chars", 0) for r in ok_rows]
    scores = [r.get("reflection_score") for r in ok_rows]
    scores = [s for s in scores if isinstance(s, (int, float))]
    summary["reflection"] = {
        "n": len(ok_rows),
        "chars_mean": _mean(chars),
        "chars_min": min(chars) if chars else None,
        "chars_max": max(chars) if chars else None,
        "score_mean": _mean(scores),
        "score_n": len(scores),
        "status_dist": {},
    }
    for r in ok_rows:
        st = r.get("reflection_status") or "(无)"
        summary["reflection"]["status_dist"][st] = \
            summary["reflection"]["status_dist"].get(st, 0) + 1
    issue_counts = [r.get("issues", 0) for r in ok_rows]
    summary["router"] = {
        "issues_mean": _mean(issue_counts),
        "issues_total": sum(issue_counts),
        "zero_issue_runs": sum(1 for c in issue_counts if c == 0),
    }
    secs = [r.get("seconds", 0) for r in rows]
    summary["seconds_mean"] = _mean(secs)
    summary["seconds_total"] = round(sum(secs), 1)

    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    md = [
        "# 批量跑案例汇总 · {}".format(batch),
        "",
        "- 结束时间:{}".format(summary["finished_at"]),
        "- 设置:模型 `{}` · 分支 `{}` · 并发 {} · 目标时长 {} 分钟".format(
            args_ns.model, args_ns.timeline or "auto(LLM 判定)",
            args_ns.lanes, args_ns.duration_min),
        "- 结果:成功 **{}** 条 / 失败 {} 条(台账 {} 行)".format(
            summary["counts"].get("ok", 0), summary["counts"].get("failed", 0),
            summary["counts"].get("ledger_rows", 0)),
        "",
        "## 分支分布",
        "",
        "| 分支 | 条数 |",
        "| --- | --- |",
    ]
    for b, n in sorted(summary["branches"].items(), key=lambda kv: -kv[1]):
        md.append("| {} | {} |".format(b, n))
    md += [
        "",
        "## 反思质量",
        "",
        "- 有效样本:{n} 条".format(**{"n": summary["reflection"]["n"]}),
        "- 字数:均值 {chars_mean}(min {chars_min} / max {chars_max})".format(
            **summary["reflection"]),
        "- 质量分:均值 {score_mean}(n={score_n})".format(**summary["reflection"]),
        "- 质量门分布:{}".format(
            ", ".join("{}={}".format(k, v)
                      for k, v in sorted(summary["reflection"]["status_dist"].items()))),
        "",
        "## Router 产出",
        "",
        "- issue 总数 {} · 单条均值 {}".format(
            summary["router"]["issues_total"], summary["router"]["issues_mean"]),
        "- 零 issue 的记录:{} 条".format(summary["router"]["zero_issue_runs"]),
        "",
        "## 耗时",
        "",
        "- 单条均值 {}s · 累计 {}s".format(
            summary["seconds_mean"], summary["seconds_total"]),
        "",
        "> 口径:只统计成功读到 `run.json` 的记录;`read_ok=false` 的不计入分布。",
    ]
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    return summary


def main(argv=None) -> int:
    tolerant_stdout()
    ap = argparse.ArgumentParser(description="批量跑 case01 案例(多路并发)")
    ap.add_argument("--duration-min", type=float, default=180.0,
                    help="最长运行分钟数(到点自停;默认 180)")
    ap.add_argument("--max-runs", type=int, default=0,
                    help="最多跑多少条(0=只受时长约束)")
    ap.add_argument("--lanes", type=int, default=3, help="并发通道数(默认 3)")
    ap.add_argument("--model", default="qwen3:8b", help="CASE01_LLM_MODEL")
    ap.add_argument("--embed-model", default="", help="CASE01_EMBED_MODEL(缺省用环境)")
    ap.add_argument("--timeline", choices=["A", "B", "C"], default="",
                    help="强制分支;缺省全走 LLM 自动判定")
    ap.add_argument("--disable-thinking", action="store_true", default=True,
                    help="关 qwen3 hybrid 模型的思考(默认开:结构化任务不需要推理链)")
    ap.add_argument("--keep-thinking", dest="disable_thinking",
                    action="store_false", help="保留思考模式(会挤占输出预算)")
    ap.add_argument("--external-ethan", action="store_true",
                    help="Ethan/Router 走 OpenRouter 外部 API")
    ap.add_argument("--retries", type=int, default=1,
                    help="单条失败重试次数(默认 1)")
    ap.add_argument("--timeout", type=int, default=1200, help="单条超时秒数")
    ap.add_argument("--python", default=DEFAULT_PYTHON,
                    help="跑 case01.run 的解释器(须装有 mavisframework)")
    ap.add_argument("--tag", default="", help="批次标签(默认用批次号)")
    args = ap.parse_args(argv)

    python = shutil.which(args.python) or args.python
    if not os.path.exists(python):
        print("找不到解释器:{} —— 装好 mavisframework 或用 --python 指定".format(python),
              file=sys.stderr)
        return 2
    if not os.path.isdir(RUNS_DIR):
        print("找不到 runs 目录:{} —— 确认在 provenance/provenance 下运行".format(RUNS_DIR),
              file=sys.stderr)
        return 2

    batch = "{}-{}".format(_batch_id(), args.tag) if args.tag else _batch_id()
    out_dir = os.path.join(ANALYSIS_ROOT, batch)
    os.makedirs(out_dir, exist_ok=True)

    # 先备足任务(条数 = 并发 × 预估轮数,富余一点,到点自停会自然截断)
    if args.max_runs > 0:
        planned = args.max_runs
    else:
        planned = max(args.lanes * 4, int(args.duration_min))
    job_q: "queue.Queue" = queue.Queue()
    for i in range(planned):
        job_q.put("batch-{}-{:03d}".format(batch, i + 1))

    stop_at = time.time() + args.duration_min * 60
    stats = {"done": 0, "ok": 0, "failed": 0}
    _log("批次 {} 开始 · 目标 {} 分钟 · 并发 {} · 模型 {} · 分支 {} · 备任务 {} 条".format(
        batch, args.duration_min, args.lanes, args.model,
        args.timeline or "auto", planned))
    _log("台账目录: {}".format(out_dir))

    threads = [threading.Thread(target=_worker,
                                args=(i + 1, args, job_q, out_dir, stop_at, stats),
                                daemon=True)
               for i in range(max(1, args.lanes))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    summary = _summarize(out_dir, batch, stats, args)
    _log("批次 {} 结束:成功 {} / 失败 {}".format(
        batch, summary["counts"].get("ok", 0), summary["counts"].get("failed", 0)))
    print("\n汇总:{}".format(os.path.join(out_dir, "summary.md")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
