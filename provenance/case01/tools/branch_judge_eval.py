# -*- coding: utf-8 -*-
"""分支判定对照评估:关键词规则 vs LLM 判定 vs 实际走的线。

回答的问题(2026-09-27 用户立项"LLM 判定分支,你来做"):
- 成品记录的 T0 AI 回答,规则表(scenario.yaml 词表)和 LLM 判定
  各判到哪个分支?与记录实际走的线一致吗?
- judge 模式的记录:actual = LLM 判定结果,可量"判定准确率";
  preset 模式的记录:actual = 操作者预设(可能与 AI 立场矛盾,那是另一回事)——
  对这类记录,有意义的指标是 **规则 vs LLM 的一致性**。

默认语料是 `case01/runs/*/run.json` 里有 T0 回答的那些。2026-10-07 前只认
speaker="ai" 的小镇面记录,而批次面的 speaker 是 "investment_ai",于是 490 多条
批次记录被静默排除在对照之外;现在两种都收。要固定语料复测用 `--cases-json`。

用法:
  python -m case01.tools.branch_judge_eval --llm glm      # BigModel 免费档(默认)
  python -m case01.tools.branch_judge_eval --llm ollama   # 本地 Ollama(取 CASE01_LLM_MODEL,未设则 4b)
  python -m case01.tools.branch_judge_eval --llm vllm     # 本地 Qwen3-8B/vLLM
  python -m case01.tools.branch_judge_eval --llm glm --tune-prompt  # 见 --tune-prompt 说明

产物:results/analysis/branch_judge_eval/{json,md}
每份产物的 summary 里带 `judge_model`/`prompt_sha8` —— 换模型或换 prompt 复跑时,
对照的是哪一跑全靠这两个字段,目录名不算记录。
"""
import json
import os
import sys
import time
from collections import Counter
from typing import Dict, List

CK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
RUNS = os.path.join(CK, "case01", "runs")
SCENARIO = os.path.join(CK, "cases", "case01_stock", "scenario.yaml")
OUT_ROOT = os.path.join(CK, "results", "analysis", "branch_judge_eval")

BIGMODEL_URL = "https://open.bigmodel.cn/api/paas/v4"
BIGMODEL_MODEL = "glm-4.7-flash"

from mavis_case01_injector.world.branch import JUDGE_PROMPT as ACTION_FIRST_PROMPT


class _Retry429:
    """GLM 免费档限流退避(429 → 指数等待重试)。"""

    def __init__(self, inner):
        self._inner = inner

    def chat(self, messages, temperature=0.7, max_tokens=1024, **kw):
        last = None
        for attempt in range(5):
            try:
                return self._inner.chat(messages, temperature=temperature,
                                        max_tokens=max_tokens, **kw)
            except RuntimeError as e:
                last = e
                if "429" in str(e):
                    wait = 20 * (attempt + 1)
                    print("    429 限流,等 {}s".format(wait), flush=True)
                    time.sleep(wait)
                    continue
                raise
        raise last


def make_llm(which: str, max_tokens: int):
    """按 --llm 构造判定客户端(免费通道;绝不碰付费模型)。

    glm:thinking 关闭(判定任务不需要深推理——开着时 3072 token 全烧在
    reasoning 上,content 为空 → undetermined;关闭后 content 直出)。
    """
    if which == "glm":
        from case01.agents.llm import OpenRouterClient
        key = os.environ.get("BIGMODEL_API_KEY", "").strip()
        if not key:
            raise SystemExit("缺 BIGMODEL_API_KEY 环境变量(BigModel 平台 key)")
        inner = OpenRouterClient(
            model=BIGMODEL_MODEL, base_url=BIGMODEL_URL, api_key=key,
            extra_body={"thinking": {"type": "disabled"}})
        return _Retry429(inner)
    if which == "ollama":
        # 走 local_client_from_env() 而不是无参 OllamaClient():
        # 后者吃签名默认模型(4b)并**绕过** CASE01_LLM_MODEL / CASE01_LLM_SEED,
        # 于是"按环境变量评测 8b"实际跑的是 4b,且结果不可复现(2026-10-05 修,
        # 与 orchestrator 同一形态的坑 —— 那里 2026-10-03 已修过一次)。
        from case01.agents.llm import local_client_from_env
        return local_client_from_env()
    if which == "vllm":
        from case01.agents.llm import VLLMClient
        return VLLMClient(
            base_url=os.environ.get(
                "CASE01_LLM_BASE_URL", "http://127.0.0.1:8101/v1"),
            chat_model=os.environ.get("CASE01_LLM_MODEL", "qwen3-8b"),
            # 分支判定只需短 JSON；关闭 Qwen3 thinking 可显著降低延迟，
            # 且避免推理 token 把结构化正文挤出 max_tokens。
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
    raise SystemExit("未知 --llm: {}".format(which))


def load_cases() -> List[dict]:
    """25 条成品记录 → {run_id, t0_answer, actual_branch, source}。"""
    out = []
    for p in sorted(glob_run_files()):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        t0 = next((t.get("text", "") for t in d.get("turns", [])
                   if t.get("speaker") in ("ai", "investment_ai")), "")
        if not t0.strip():
            continue
        out.append({
            "run_id": d.get("run_id") or os.path.basename(os.path.dirname(p)),
            "t0": t0,
            "actual": (d.get("branch") or "").upper(),
            "source": (d.get("branch_action") or {}).get("source", ""),
        })
    return out


def load_cases_from_report(path: str) -> List[dict]:
    """从既有评测报告恢复语料。

    历史 run 通常不入 Git；报告中的 rows 保留了 T0、实际分支和来源，适合在
    同一冻结语料上复测新模型。这里只读取输入/金标字段，不沿用旧模型判定。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for row in data.get("rows") or []:
        t0 = str(row.get("t0") or "").strip()
        actual = str(row.get("actual") or "").upper()
        if t0 and actual in ("A", "B", "C"):
            out.append({
                "run_id": row.get("run_id") or "",
                "t0": t0,
                "actual": actual,
                "source": row.get("source") or "",
            })
    return out


def glob_run_files():
    import glob
    return sorted(glob.glob(os.path.join(RUNS, "*", "run.json")))


def rules_verdict(t0: str) -> str:
    """规则表判定(与 case01 管线同源:包版 RuleBranchRouter,词表内置)。"""
    from mavis_case01_injector.world.branch import RuleBranchRouter
    return RuleBranchRouter().classify(t0)


def llm_verdict(llm, t0: str, max_tokens: int, prompt: str = ""):
    from mavis_case01_injector.world.branch import LLMBranchJudge
    judge = LLMBranchJudge(llm, max_tokens=max_tokens, prompt=prompt) if prompt \
        else LLMBranchJudge(llm, max_tokens=max_tokens)
    branch, info = judge.judge(t0)
    return branch, info


def evaluate(cases: List[dict], llm, max_tokens: int, prompt: str = "") -> List[dict]:
    rows = []
    for i, c in enumerate(cases):
        rv = rules_verdict(c["t0"])
        t0 = time.perf_counter()
        try:
            lv, info = llm_verdict(llm, c["t0"], max_tokens, prompt=prompt)
            err = ""
        except Exception as exc:  # noqa: BLE001 —— 单条失败留痕继续
            lv, info, err = "error", {}, "{}: {}".format(type(exc).__name__, exc)
        rows.append({**c, "rules": rv, "llm": lv,
                     "llm_attempts": info.get("attempts"),
                     "llm_reason": (info.get("reason") or "")[:120],
                     "rules_eq_llm": rv == lv,
                     "llm_eq_actual": lv == c["actual"] if lv not in ("error", "undetermined") else None,
                     "rules_eq_actual": rv == c["actual"],
                     "secs": round(time.perf_counter() - t0, 1)})
        print("[{}/{}] {} rules={} llm={} actual={}({}){}".format(
            i + 1, len(cases), c["run_id"][-14:], rv, lv, c["actual"],
            c["source"], " ERR:" + err if err else ""), flush=True)
    return rows


def summarize(rows: List[dict]) -> dict:
    judged = [r for r in rows if r["llm"] not in ("error", "undetermined")]
    judge_src = [r for r in judged if r["source"] == "judge"]
    predicted = Counter(r["llm"] for r in judged)
    actual = Counter(r["actual"] for r in rows if r.get("actual"))
    dominant_rate = (max(predicted.values()) / len(judged)) if judged else 0.0
    return {
        "n": len(rows),
        "llm_ok": len(judged),
        "rules_llm_agree": sum(1 for r in judged if r["rules_eq_llm"]),
        "rules_llm_agree_rate": round(
            sum(1 for r in judged if r["rules_eq_llm"]) / len(judged), 3) if judged else None,
        "judge_records": len(judge_src),
        "judge_llm_eq_actual": sum(1 for r in judge_src if r["llm_eq_actual"]),
        "judge_llm_eq_actual_rate": round(
            sum(1 for r in judge_src if r["llm_eq_actual"]) / len(judge_src), 3) if judge_src else None,
        "prediction_distribution": dict(predicted),
        "actual_distribution": dict(actual),
        "collapse_warning": bool(judged and dominant_rate >= 0.9),
        "mismatch_examples": [
            {"run_id": r["run_id"], "rules": r["rules"], "llm": r["llm"],
             "actual": r["actual"], "source": r["source"]}
            for r in judged if not r["rules_eq_llm"]][:10],
    }


def _judge_identity(llm, channel: str, prompt: str) -> dict:
    """这份对照是"谁、用哪版 prompt"判的 —— 必须落进产物。

    C/B 分布本身就是被判定模型的属性(同一份冻结 T0 语料:4b 判 25 条全 B,
    qwen3:8b 判 C=17/B=8),而三份既有产物里没有任何模型字段,只有目录名在说模型。
    """
    import hashlib
    import inspect

    while isinstance(llm, _Retry429):
        llm = llm._inner
    from mavis_case01_injector.world.branch import LLMBranchJudge
    default_prompt = inspect.signature(
        LLMBranchJudge.__init__).parameters["prompt"].default
    effective = prompt or default_prompt
    return {
        "llm_channel": channel,
        "judge_model": getattr(llm, "chat_model", None) or "(未检出)",
        # ollama/vllm 通道的模型来自环境变量;没设就是客户端签名里的默认值
        "model_env_requested": os.environ.get("CASE01_LLM_MODEL") or "(未设)",
        "prompt_sha8": hashlib.sha256(effective.encode("utf-8")).hexdigest()[:8],
        "prompt_is_shipped_default": effective == default_prompt,
    }


def _md(rows: List[dict], s: dict) -> str:
    lines = ["# 分支判定对照 — 规则表 vs LLM", "",
             "| 指标 | 值 |", "|---|---|",
             "| 判定模型 | {} · --llm {} · CASE01_LLM_MODEL={}|".format(
                 s["judge_model"], s["llm_channel"], s["model_env_requested"]),
             "| 判定 prompt | sha8={} · 与出厂默认相同={}|".format(
                 s["prompt_sha8"], s["prompt_is_shipped_default"]),
             "| 记录数 | {} |".format(s["n"]),
             "| LLM 有效判定 | {} |".format(s["llm_ok"]),
             "| 规则 vs LLM 一致 | {}({}) |".format(
                 s["rules_llm_agree"], s["rules_llm_agree_rate"]),
             "| judge 模式记录 | {} |".format(s["judge_records"]),
             "| judge 记录 LLM=实际 | {}({}) |".format(
                 s["judge_llm_eq_actual"], s["judge_llm_eq_actual_rate"]),
             "| LLM 分布 | {} |".format(s["prediction_distribution"]),
             "| 实际分布 | {} |".format(s["actual_distribution"]),
             "| 类别塌缩警告 | {} |".format(s["collapse_warning"]),
             "", "| run | 规则 | LLM | 实际 | source |",
             "|---|---|---|---|---|"]
    for r in rows:
        mark = "" if r["rules_eq_llm"] else " ⚠"
        lines.append("| {} | {} | {}{} | {} | {} |".format(
            r["run_id"][-24:], r["rules"], r["llm"], mark, r["actual"], r["source"]))
    if s["mismatch_examples"]:
        lines += ["", "## 规则 vs LLM 不一致样例(逐条人工看谁对)"]
        for m in s["mismatch_examples"]:
            lines.append("- {} 规则={} LLM={} 实际={} ({})".format(
                m["run_id"][-24:], m["rules"], m["llm"], m["actual"], m["source"]))
    return "\n".join(lines) + "\n"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="分支判定对照:规则表 vs LLM")
    ap.add_argument("--llm", default="glm", choices=["glm", "ollama", "vllm"])
    ap.add_argument("--max-tokens", type=int, default=3072,
                    help="判定输出上限(推理模型要给足,默认 3072)")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条(0=全部)")
    ap.add_argument("--cases-json", default="",
                    help="从既有 branch_judge_eval.json 读取冻结 T0 语料")
    ap.add_argument("--tune-prompt", action="store_true",
                    help="用 ACTION_FIRST_PROMPT 复跑;2026-10-07 实测它和出厂 JUDGE_PROMPT "
                         "是同一个字符串,所以现在加了也是 no-op(会出声),等真有候选 prompt 再用")
    ap.add_argument("--out-root", default=OUT_ROOT)
    args = ap.parse_args()

    cases = load_cases_from_report(args.cases_json) if args.cases_json else load_cases()
    if args.limit:
        cases = cases[:args.limit]
    if not cases:
        print("没有可评估的记录")
        return 2
    llm = make_llm(args.llm, args.max_tokens)
    prompt = ACTION_FIRST_PROMPT if args.tune_prompt else ""
    rows = evaluate(cases, llm, args.max_tokens, prompt=prompt)
    s = summarize(rows)
    s.update(_judge_identity(llm, args.llm, prompt))
    if args.tune_prompt and s["prompt_is_shipped_default"]:
        print("[!] --tune-prompt 传进去的文本和出厂 JUDGE_PROMPT 逐字相同"
              "(sha8={}),这一跑和默认跑没有区别。".format(s["prompt_sha8"]), flush=True)
    os.makedirs(args.out_root, exist_ok=True)
    with open(os.path.join(args.out_root, "branch_judge_eval.json"), "w",
              encoding="utf-8") as f:
        json.dump({"summary": s, "rows": rows}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.out_root, "branch_judge_eval.md"), "w",
              encoding="utf-8") as f:
        f.write(_md(rows, s))
    print(json.dumps(s, ensure_ascii=False, indent=2))
    print("-> {}\\branch_judge_eval.{{json,md}}".format(args.out_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
