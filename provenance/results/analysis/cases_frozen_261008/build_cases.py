# -*- coding: utf-8 -*-
"""冻结 10-07 两批的 30 条 T0,给分支判据对照(#56)当同一份语料用。

用法(在 provenance/ 目录下):
    python results/analysis/cases_frozen_261008/build_cases.py
    python -m case01.tools.branch_judge_eval --llm ollama \
        --cases-json results/analysis/cases_frozen_261008/cases.json \
        --out-root results/analysis/<新目录>
    同一份语料上跑旧判据要显式喂文本:--prompt-file <旧 JUDGE_PROMPT>
"""
import glob, json, os, sys

BATCHES = ("batch-261007-212235-sample8b15-", "batch-261007-224436-sample4b15-")
HERE = os.path.dirname(os.path.abspath(__file__))
CK = os.path.abspath(os.path.join(HERE, "..", "..", ".."))   # provenance/provenance
# 10-08 归类搬迁后成品记录在仓根 data/case01/runs;写死老路径会扫到 0 条而 cases.json
# 照样生成(只是 n=0),下一次复算就拿着空语料当"同一份文本"。脚本形态运行时
# sys.path[0] 是本目录,所以先把包根插进去再走 data_root。
sys.path.insert(0, CK)
from case_engine.paths import data_root  # noqa: E402

RUNS = data_root("case01.records", env_var="CASE01_RUNS_ROOT")

rows = []
for p in sorted(glob.glob(os.path.join(RUNS, "*", "run.json"))):
    rid = os.path.basename(os.path.dirname(p))
    if not rid.startswith(BATCHES):
        continue
    d = json.load(open(p, encoding="utf-8"))
    t0 = next((t.get("text", "") for t in d.get("turns", [])
               if t.get("speaker") in ("ai", "investment_ai")), "")
    if not t0.strip():
        continue
    rows.append({"run_id": d.get("run_id") or rid, "t0": t0,
                 "actual": (d.get("branch") or "").upper(),
                 "source": (d.get("branch_action") or {}).get("source", "")})

out = {
    "meta": {
        "what": "冻结 T0 语料:#56 判据改动前后必须打同一份文本,否则差多少行说不清是谁移动的",
        "built_from": list(BATCHES),
        "n": len(rows),
        "actual_distribution": {k: sum(1 for r in rows if r["actual"] == k)
                               for k in sorted({r["actual"] for r in rows})},
        "note_actual": ("actual = 记录落盘时的分支,由**该批自己的模型 + 旧判据(sha8 2caf63f9)"
                        "在钉了种子的运行时**判出;它不是人工金标,也不能当作"
                        "\"同模型新旧对照\"的另一臂(那一臂见 branch_judge_oldprompt_samecases_261008/)"),
        "how": "python results/analysis/cases_frozen_261008/build_cases.py",
    },
    "rows": rows,
}
json.dump(out, open(os.path.join(HERE, "cases.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("rows:", len(rows), out["meta"]["actual_distribution"])
