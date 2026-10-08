# -*- coding: utf-8 -*-
"""唯一移动那一行(sample8b15-014):同一条 T0、同一模型、同一 prompt 连判 4 次。
区分"新判据把它救回来了"和"旧判据那一跑本来就是抖动"。"""
import hashlib, json, os, sys
sys.path.insert(0, ".")
from case01.agents.llm import local_client_from_env
from mavis_case01_injector.world.branch import JUDGE_PROMPT as NEW, LLMBranchJudge

OLD = open("_old_judge_prompt.txt", encoding="utf-8").read()
rows = json.load(open("results/analysis/branch_judge_newprompt_261008/branch_judge_eval.json",
                      encoding="utf-8"))["rows"]
target = next(r for r in rows if r["run_id"].endswith("sample8b15-014"))
out = {"run_id": target["run_id"], "actual": target["actual"],
       "old_prompt_sha8": hashlib.sha256(OLD.encode()).hexdigest()[:8],
       "new_prompt_sha8": hashlib.sha256(NEW.encode()).hexdigest()[:8],
       "judge_model": getattr(local_client_from_env(), "chat_model", None),
       "model_env_requested": os.environ.get("CASE01_LLM_MODEL") or "(未设)",
       "temperature": 0.1, "max_tokens": 2048, "repeats": 4, "runs": []}
for name, prompt in (("old", OLD), ("new", NEW)):
    for i in range(out["repeats"]):
        b, info = LLMBranchJudge(local_client_from_env(), max_tokens=2048,
                                 prompt=prompt).judge(target["t0"])
        out["runs"].append({"prompt": name, "i": i + 1, "branch": b,
                            "attempts": info.get("attempts"),
                            "reason": (info.get("reason") or "")[:180],
                            "raw_tail": (info.get("raw_outputs") or [""])[-1][-220:]})
        print(name, i + 1, b, flush=True)
d = os.path.join("results", "analysis", "branch_judge_repeat014_261008")
os.makedirs(d, exist_ok=True)
json.dump(out, open(os.path.join(d, "repeat.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
with open(os.path.join(d, "repeat.md"), "w", encoding="utf-8") as f:
    f.write("# 唯一移动那一行重判 4 次(同模型同温度)\n\n")
    f.write("- run_id:{} actual={}\n- 判定模型:{} · prompt 旧 sha8={} 新 sha8={}\n"
            "- temperature={} max_tokens={} 每条 {}\n\n".format(
                out["run_id"], out["actual"], out["judge_model"], out["old_prompt_sha8"],
                out["new_prompt_sha8"], out["temperature"], out["max_tokens"], out["repeats"]))
    for r in out["runs"]:
        f.write("- {} 第{}次:{}(attempts={}){}\n".format(
            r["prompt"], r["i"], r["branch"], r["attempts"],
            " reason:" + r["reason"] if r["reason"] else ""))
print(json.dumps([{k: r[k] for k in ("prompt", "branch", "attempts")} for r in out["runs"]],
                 ensure_ascii=False))
