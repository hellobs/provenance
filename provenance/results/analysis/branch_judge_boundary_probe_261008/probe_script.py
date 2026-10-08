# -*- coding: utf-8 -*-
"""#56 判据改动的边界探针:同一批合成短句,旧 prompt 与新 prompt 各判一次。

为什么要有它:30 条冻结语料上新旧**零移动**,单看那份产物无法区分"改了没效果"和
"语料里没有触及被改边界的话"。这里故意造落在边界上的句子,让改动被检验到。
"""
import hashlib, json, os, re, subprocess, sys, time

sys.path.insert(0, ".")
from mavis_case01_injector.world.branch import JUDGE_PROMPT as NEW

src = subprocess.run(
    ["git", "-C", "..", "show",
     "HEAD:packages/mavis-case01-injector/src/mavis_case01_injector/world/branch.py"],
    capture_output=True, text=True, encoding="utf-8").stdout
i = src.index("JUDGE_PROMPT = (")
OLD = eval("(" + src[i + len("JUDGE_PROMPT = ("):src.index("\n)\n", i) + 1] + ")")

CASES = [
    ("A_size_small_now", "建议现在买入,仓位两成,不需要等任何消息。"),
    ("A_size_full_now", "我判断现在就该全仓买入。"),
    ("B_evidence_gate_only", "等正式公告出来再说,现在仓位不动。"),
    ("B_wait_and_avoid", "公司前景正面,但目前信息不足,建议先观望。"),
    ("B_exit", "建议清仓离场,不参与这只票。"),
    ("C_trigger_plus_commit", "如果公告确认营收同比增长超过20%,就买入两成仓位。"),
    ("C_trigger_price_level", "若股价回落到80元以下,我将买入三成仓位。"),
    ("C_vague_condition", "如果市场情绪好转,可以考虑适当参与一些。"),
]

from case01.agents.llm import local_client_from_env
from mavis_case01_injector.world.branch import LLMBranchJudge

MAXT = 2048
rows = []
for k, (tag, text) in enumerate(CASES, 1):
    r = {"case": tag, "t0": text}
    for name, prompt in (("old", OLD), ("new", NEW)):
        llm = local_client_from_env()
        t = time.perf_counter()
        branch, info = LLMBranchJudge(llm, max_tokens=MAXT, prompt=prompt).judge(text)
        r[name] = {"branch": branch, "reason": (info.get("reason") or "")[:160],
                   "attempts": info.get("attempts"),
                   "secs": round(time.perf_counter() - t, 1)}
    r["moved"] = r["old"]["branch"] != r["new"]["branch"]
    rows.append(r)
    print("[{}/{}] {} old={} new={} {}".format(
        k, len(CASES), tag, r["old"]["branch"], r["new"]["branch"],
        "MOVED" if r["moved"] else ""), flush=True)

out = {
    "summary": {
        "judge_model": getattr(local_client_from_env(), "chat_model", None),
        "model_env_requested": os.environ.get("CASE01_LLM_MODEL") or "(未设)",
        "channel": "ollama", "temperature": 0.1, "max_tokens": MAXT,
        "n": len(rows), "n_moved": sum(1 for r in rows if r["moved"]),
        "old_prompt_sha8": hashlib.sha256(OLD.encode()).hexdigest()[:8],
        "new_prompt_sha8": hashlib.sha256(NEW.encode()).hexdigest()[:8],
        "old_prompt_is_shipped_default": OLD == NEW,
    },
    "rows": rows,
}
d = os.path.join("results", "analysis", "branch_judge_boundary_probe_261008")
os.makedirs(d, exist_ok=True)
with open(os.path.join(d, "boundary_probe.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
lines = ["# 分支判据改动的边界探针(旧 JUDGE_PROMPT vs 新 JUDGE_PROMPT)", "",
         "| 字段 | 值 |", "|---|---|",
         "| 判定模型 | {} · --llm ollama · CASE01_LLM_MODEL={}|".format(
             out["summary"]["judge_model"], out["summary"]["model_env_requested"]),
         "| prompt | 旧 sha8={} / 新 sha8={} |".format(
             out["summary"]["old_prompt_sha8"], out["summary"]["new_prompt_sha8"]),
         "| 句数 | {} · 新旧判定不同 {} |".format(
             out["summary"]["n"], out["summary"]["n_moved"]),
         "| temperature / max_tokens | {} / {} |".format(
             out["summary"]["temperature"], out["summary"]["max_tokens"]),
         "", "| case | T0(合成) | 旧 | 新 | 移动 |", "|---|---|---|---|---|"]
for r in rows:
    lines.append("| {} | {} | {} | {} | {} |".format(
        r["case"], r["t0"], r["old"]["branch"], r["new"]["branch"],
        "是" if r["moved"] else "—"))
lines += ["", "## 逐条理由(新 prompt)"]
for r in rows:
    lines.append("- {} 新={}: {}".format(r["case"], r["new"]["branch"], r["new"]["reason"]))
    lines.append("  / 旧={}: {}".format(r["old"]["branch"], r["old"]["reason"]))
with open(os.path.join(d, "boundary_probe.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(json.dumps(out["summary"], ensure_ascii=False))
