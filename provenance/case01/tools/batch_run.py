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
    python -m case01.tools.batch_run --max-runs 50 --model qwen3:8b

    # 快速自检(2 分钟内结束,看调度器本身有没有毛病)
    python -m case01.tools.batch_run --duration-min 2 --max-runs 4

    # 要**多样本**:逐条一个种子(第 i 条 = base+i,记在每条台账行的 seed 栏)
    python -m case01.tools.batch_run --max-runs 12 --lanes 1 --seed-base 20261101
    # 为什么不是"同一种子多跑几条":批/单条路径上没有世界流的消费者(全仓唯一的
    # random.* 就是 rngchain.py:174 那句播种,15 个取数点都在 mavisframework 里,
    # 只有小镇面跑得到)⇒ 同 master + 同一服务端状态 = 同一份内容。
    # 2026-10-06 实测:12 条同 master、单 lane,内容只有 2 种(空载 1 条 + 暖 11 条全等)。

产物
----
- 每条案例:`case01/runs/<run_id>/run.json`(由 `case01.run` 自己落盘);
- 调度台账:`results/analysis/batch_runs/<批次号>/ledger.jsonl`
  (每条一行:run_id/分支/反思质量/issues/耗时/重试次数/**这一条用的种子**,供事后统计;
   **例外**:收尾真的越过界限弃等某条 lane 时,那条不会有台账行 —— 去 failures 里看);
- 失败明细:同目录 `failures.jsonl`(含 stderr 尾部,便于判断是模型崩还是代码崩;
   越过界限弃等的在飞条目也在这一份里,带 `abandoned:true` 与 `lane`,没有 `log_tail`);
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
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional

from case01.safestream import tolerant_stdout, utf8_env

HERE = os.path.dirname(os.path.abspath(__file__))          # .../case01/tools
CASE01_DIR = os.path.dirname(HERE)                        # .../case01
PROV_DIR = os.path.dirname(CASE01_DIR)                    # .../provenance
# 成品记录根只有一个解析口(下面两行)。这里**不留 except 兜底、也不留老根字面量**:
# 老位置当晚已被删,兜到它等于安静地读一个空目录;而本文件在脚本形态下早在文件头
# `from case01.safestream` 那行就起不来(2026-10-08 实测 ModuleNotFoundError、rc=1),
# 所以原先那段 `except Exception: pass` 从来不可达 —— 留着只会被下一个人当成"老位置还在"的证据。
# 要独立跑就用 `-m case01.tools.batch_run`。
from case_engine.paths import data_root as _data_root    # noqa: E402 —— 与文件头 case01.* 同一可导前提
RUNS_DIR = _data_root("case01.records", env_var="CASE01_RUNS_ROOT")
ANALYSIS_ROOT = os.path.join(PROV_DIR, "results", "analysis", "batch_runs")
_SEQ_RE = re.compile(r"-(\d{3,})$")            # run_id 末尾的 -001…-NNN(--seed-base 用)

def _detect_python() -> str:
    """挑跑 `case01.run` 的解释器:环境变量 → 仓内 venv → 并列的 mavis venv → 当前解释器。

    为什么不写死(2026-10-06 修):此处原本硬编码 `D:\\zzr\\mavis\\.venv\\Scripts\\python.exe`,
    换机器/换 venv 名就跑不起来 —— 这正是新人第一次用批量工具会撞的墙。
    顺序:`CASE01_BATCH_PYTHON` → `provenance/.venv-live` → `provenance/.venv`
    → 仓根同名两个 → 与仓并列的 `../mavis/.venv` → `sys.executable`。
    选中的路径会打印出来(不静默),也可用 `--python` 覆盖。
    """
    env = os.environ.get("CASE01_BATCH_PYTHON", "").strip()
    if env:
        return env
    repo = os.path.dirname(PROV_DIR)                     # 仓根
    tail = os.path.join("Scripts", "python.exe") if os.name == "nt" else os.path.join("bin", "python")
    for c in (os.path.join(PROV_DIR, ".venv-live", tail),
              os.path.join(PROV_DIR, ".venv", tail),
              os.path.join(repo, ".venv-live", tail),
              os.path.join(repo, ".venv", tail),
              os.path.join(os.path.dirname(repo), "mavis", ".venv", tail)):
        if os.path.isfile(c):
            return c
    return sys.executable


# 默认解释器见 _detect_python()(可用 --python 或 CASE01_BATCH_PYTHON 覆盖)
DEFAULT_PYTHON = _detect_python()

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
            timed_out = False
            while proc.poll() is None and time.time() < deadline:
                if not seen:
                    seen = _ollama_loaded_model()
                time.sleep(5)
            if proc.poll() is None:
                # 到点仍在跑:必须**真的终止**,否则一条卡死就让整批陪跑
                # (2026-10-03 h120seed 那次 3 条 lane 卡 8h25m;此前这里是直接
                #  proc.wait() —— 轮询退出后仍无限等待,deadline 形同虚设)。
                timed_out = True
                proc.terminate()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()          # Windows 上走 TerminateProcess
                    proc.wait(timeout=10)
                lf.write("\n\n===== TIMEOUT after {}s, process terminated =====\n"
                         .format(timeout))
                lf.flush()
            code = proc.wait()
            if not seen:
                seen = _ollama_loaded_model()
            if timed_out:
                # 用非零 rc 让上层按失败记账,并把"超时"写进日志(见 _failure_tail)
                return (code if code not in (0, None) else -9), seen
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


def _child_env(args_ns, run_seed=None) -> Dict:
    """组装子进程(`python -m case01.run`)的环境。

    `PYTHONIOENCODING=utf-8` 是必须的,不是美化:日志文件由本进程按 utf-8 打开并
    交给子进程当 stdout,而子进程默认按 Windows locale(GBK)编码文本流 —— 中文
    回答进了 .log 就是乱码,`_failure_tail` 再按 utf-8 读回来时靠 errors=replace
    兜底,失败原因糊成一片(实测批次 261004-123726-h120/-035 的 retry 段)。
    钉死 utf-8 后,GBK 下会抛 UnicodeEncodeError 的 `▶` 之类字符也一并安全落地。
    """
    env = utf8_env()
    if args_ns.model:
        env["CASE01_LLM_MODEL"] = args_ns.model
    if args_ns.embed_model:
        env["CASE01_EMBED_MODEL"] = args_ns.embed_model
    if args_ns.disable_thinking:
        env["CASE01_LLM_DISABLE_THINKING"] = "1"
    else:
        env.pop("CASE01_LLM_DISABLE_THINKING", None)
    # 采样种子:`--seed` 是整批同一个值;`--seed-base` 是逐条一个值(见 `run_seed`)。
    # 缺省两者都不写这一栏 = 每条不固定(请求体一个 seed 字节都不许多发,既有语料的语义前提)。
    # 以前这里写过一句"逐条不同种子会让'这批跑的是什么设定'变成 39 个答案"——
    # 那句话的前提是"清单只有一栏可对照",而台账行现在自己记 `seed`(:288),
    # 所以逐条不同种子是可以被回答的:**这批 = base+序号,每条记在自己的台账行里**。
    seed = getattr(args_ns, "seed", None) if run_seed is None else run_seed
    if seed is not None and str(seed).strip() != "":
        env["CASE01_LLM_SEED"] = str(seed).strip()
    return env


def run_seed(args_ns, run_id: str = ""):
    """这一条用哪个采样种子(None = 不固定)。

    - `--seed N`:整批同一个值(既有语义,一字不动)。
    - `--seed-base N`:第 i 条 = `N + i`,i 取自 run_id 末尾的 `-001`…`-NNN`
      (`:536` 起批时按序号铸名字,lane 乱序消费不影响序号;重试沿用同一个 run_id
      ⇒ **同一条的重试必然同一个种子**,不然重试会把"偶发失败"换成"换了设定",
      两件事混在一次运行里)。
    - 两个都没给:返回 None,子进程不写 seed。
    `--seed` 与 `--seed-base` 互斥在 `main()` 里就拦掉,到这里不会两个都有值。
    """
    # 只有 None/空串算"没给";0 是合法种子值(`str(0 or "")` 会变空串,所以不能这么写)
    base = "" if getattr(args_ns, "seed_base", None) is None \
        else str(args_ns.seed_base).strip()
    if base:
        tail = _SEQ_RE.search(str(run_id or ""))
        if tail is None:
            raise ValueError("--seed-base 需要 run_id 末尾带序号(-001 这种),"
                             "取不到序号就不猜:{}".format(run_id))
        return int(base) + int(tail.group(1))
    seed = "" if getattr(args_ns, "seed", None) is None else str(args_ns.seed).strip()
    return int(seed) if seed else None


def _one_run(args_ns, run_id: str, out_dir: str) -> Dict:
    """跑一条案例。返回台账行;失败不抛异常(一条崩不带走整批)。"""
    seed = run_seed(args_ns, run_id)
    env = _child_env(args_ns, seed)

    cmd = [args_ns.python, "-m", "case01.run", "--run-id", run_id]
    if args_ns.external_ethan:
        cmd += ["--external-ethan"]

    t0 = time.time()
    # `seed` 记在**行里**而不是只靠 env:`cmd` 字段里永远不会有种子(它是 env 传的),
    # 以前只能回答"整批一个值",`--seed-base` 之后每条不同 —— 不记就等于
    # "这批 12 条各用了什么种子"在台账里查不回来(2026-10-06 加)。空串 = 这条没固定。
    rec = {"run_id": run_id, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "cmd": " ".join(cmd), "attempt": 1,
           "model_requested": args_ns.model or "(默认)",
           "seed": "" if seed is None else seed}
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
            stop_at: float, stats: Dict, inflight: Dict = None) -> None:
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
        # 开跑就把这一条的种子打出来:`--seed-base` 时人要能当场看见"第 3 条用的是哪个数",
        # 而不是跑完再去翻台账(种子本身走 env、不在 cmd 里,stdout 是唯一的过程侧可见面)。
        _seed = run_seed(args_ns, run_id)
        _log("lane{}: 开始 {}{}".format(
            idx, run_id, "" if _seed is None else " (种子 {})".format(_seed)))
        # 在飞记录:收尾放弃等待时,"哪几条正在跑"必须是查得出的,不能只说"有 lane 没退出"
        if inflight is not None:
            with _write_lock:
                inflight[idx] = run_id
        try:
            rec = _one_run(args_ns, run_id, out_dir)
        finally:
            if inflight is not None:
                with _write_lock:
                    inflight.pop(idx, None)
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
            "model": args_ns.model,
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
    # 耗时的总体是**台账全部行(含失败尝试)**,与上面分布类的总体不同:
    # 失败那条同样烧了 GPU 时间,把它剔掉等于把成本藏起来。但差异必须写明 ——
    # 结尾那句"只统计成功读到 run.json 的记录"以前也把这里一起盖掉了
    # (261003-165042:44 行的耗时均值 vs 43 条的质量均值,同一个文件两个分母)。
    summary["seconds_n"] = len(secs)

    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    md = [
        "# 批量跑案例汇总 · {}".format(batch),
        "",
        "- 结束时间:{}".format(summary["finished_at"]),
        "- 设置:模型 `{}` · 并发 {} · 目标时长 {} 分钟".format(
            args_ns.model, args_ns.lanes, args_ns.duration_min),
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
        "- 单条均值 {}s · 累计 {}s(总体 = 台账全部 {} 行,含失败尝试:失败也烧时间)".format(
            summary["seconds_mean"], summary["seconds_total"], summary["seconds_n"]),
        "",
        "> 口径:分支/反思/Router 三节只统计成功读到 `run.json` 的记录"
        "(`read_ok=false` 与失败行不计入);耗时一节统计台账全部行。",
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
    ap.add_argument("--seed", default="",
                    help="采样种子(整数,整批同一个值);缺省不固定 = 现有非确定行为")
    ap.add_argument("--seed-base", dest="seed_base", default="",
                    help="逐条种子的基数(整数):第 i 条用 base+i,记在该条台账行的 seed 栏;"
                         "与 --seed 互斥。要多样本用这个 —— 同一种子多跑几条只会得到同一份内容"
                         "(2026-10-06 实测:12 条同 master 单 lane,内容 2 种,冷/暖各一)")
    args = ap.parse_args(argv)

    from case01.agents.llm import parse_seed
    try:
        args.seed = parse_seed(args.seed)
        args.seed_base = parse_seed(args.seed_base)
    except ValueError as e:
        # 在起跑前就拒,不烧掉一整批才发现种子没生效
        print(str(e), file=sys.stderr)
        return 2
    if args.seed is not None and args.seed_base is not None:
        print("--seed 与 --seed-base 互斥:前者是整批一个值,后者是逐条 base+序号。",
              file=sys.stderr)
        return 2

    python = shutil.which(args.python) or args.python
    if not os.path.exists(python):
        print("找不到解释器:{} —— 装好 mavisframework 或用 --python 指定".format(python),
              file=sys.stderr)
        return 2
    # 选中的解释器要看得见:自动探测错了(例如挑到系统 python 而它没装 mavisframework)
    # 时,只会在每条 run 里失败,不打印就很难定位。
    print("解释器:{}".format(python), flush=True)
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
    inflight: Dict = {}
    # 起跑行就把种子形态说全:批的"设定"要能被一句话回答(整批一个值 / base+序号 / 不固定),
    # 逐条的具体值另记在每条台账行的 seed 栏里。
    if args.seed_base is not None:
        _seed_desc = "逐条 {}+序号".format(args.seed_base)
    elif args.seed is not None:
        _seed_desc = str(args.seed)
    else:
        _seed_desc = "不固定"
    _log("批次 {} 开始 · 目标 {} 分钟 · 并发 {} · 模型 {} · 备任务 {} 条 · 种子 {}".format(
        batch, args.duration_min, args.lanes, args.model, planned, _seed_desc))
    _log("台账目录: {}".format(out_dir))

    threads = [threading.Thread(target=_worker,
                                args=(i + 1, args, job_q, out_dir, stop_at, stats,
                                      inflight),
                                daemon=True)
               for i in range(max(1, args.lanes))]
    for t in threads:
        t.start()
    # 收尾有界等待(2026-10-05 第六轮 M7,回应体检 §七.4「join 无超时」)。
    # 每条子进程自身有 --timeout(见 _run_with_probe),但**线程退出**此前是无限
    # `t.join()`:若某条卡在 kill 不掉的子进程或收尾 I/O 上,整批会一直挂着,
    # 到点自停的语义等于失效(2026-10-03 h120seed 的 8h25m 就是这一类)。
    # 界要锚在 stop_at(lane 不再开新任务的时刻),不是批次起点:到点时在跑的那条
    # 必然在 budget 内自己退出。锚在起点会把"最后几条正在正常收尾"当成卡死放弃
    # ——2026-10-06 批2 实测:台账 35 行而磁盘 38 条,缺的 -036/-037/-038 正是
    # 17:22–17:29 刚领到任务、17:31:12 被父进程弃等的那三条(daemon 线程随主进程退出
    # 被杀,子进程却跑完了并写出 run.json ⇒ 磁盘比台账多,而 summary 报 failed=0)。
    budget = int(args.timeout) * (int(args.retries) + 1) + 60
    deadline = max(time.time(), stop_at) + budget
    for t in threads:
        t.join(timeout=max(1, deadline - time.time()))
    stuck = [t for t in threads if t.is_alive()]
    if stuck:
        with _write_lock:
            left = sorted(inflight.items())
        for lane_idx, rid in left:
            _append_jsonl(os.path.join(out_dir, "failures.jsonl"), {
                "run_id": rid, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "lane": lane_idx, "ok": False, "abandoned": True,
                "returncode": None, "seconds": None,
                "note": "批次收尾放弃等待时该条仍在跑;它之后落的 run.json 不会进台账"})
        _log("⚠️ {} 条 lane 越过收尾界限 {}s 未退出;在飞 {} 条{}已写入 failures.jsonl,"
             "按其已落盘的台账出汇总(台账会少这么多条,磁盘上的 run.json 不会)".format(
                 len(stuck), budget, len(left),
                 "" if left else "(取不到 run_id:卡在收尾 I/O)"))

    summary = _summarize(out_dir, batch, stats, args)
    _log("批次 {} 结束:成功 {} / 失败 {}".format(
        batch, summary["counts"].get("ok", 0), summary["counts"].get("failed", 0)))
    print("\n汇总:{}".format(os.path.join(out_dir, "summary.md")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
