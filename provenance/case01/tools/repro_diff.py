# -*- coding: utf-8 -*-
"""两条 case01 记录的**可复现性比对** —— 交付/评审时答"这两条是不是同一次跑出来的"。

用法(在 provenance/provenance 下):

    python -m case01.tools.repro_diff <runA> <runB>
    python -m case01.tools.repro_diff --runs-dir <根> <runA> <runB>
    python -m case01.tools.repro_diff <runA> <runB> --json     # 机器可读

退出码:0=排除易变字段后一致(可复现);1=有实质差异;2=读不到记录。

为什么要这个工具(2026-10-05):
    验收"可复现"时,人手写 json.load + != 比对,**几乎必然踩两个坑**:
      1. 忘了排除 `run_id` —— 两次跑用不同 id 是常态,不排就等于"永远不可复现";
      2. 忘了排除 `manifest.created_at` / 本机绝对路径 —— wall-clock 与机器路径
         本来就该不同,不排就是一堆假阳性。
    手工比对时我本人就漏过 `run_id`。把排除清单**写进工具**,
    再让 `test_reproducibility.py` 钉住这份清单与实际 manifest 对齐,
    就不必每次靠人记。

比对口径(与《复现说明_三档口径.md》§四一致):
    - 排除:顶层 `run_id`;`manifest` 里的 `created_at`/`scenario_path`/
      `financial_data_dir`/`git_commit`/`git_commit_source`(后两个跨 commit 本就会变,
      是否算"可复现"取决于你是否在比同一 commit —— 所以**默认排除但单独报出来**);
    - 其余字段逐字节比对(JSON 序列化 sort_keys)。

    注意:本工具只比**产物**。它说"一致"= 两条记录在排除上述字段后逐字节相同;
    它**不**证明"换个机器也能跑出同样结果"(那需要 ③ 逐字重生成,现在做不到)。
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from case01.orchestrator import RUNS_ROOT  # noqa: E402

# 顶层:每条记录自己的 id 当然不同,不参与比对。
TOP_VOLATILE = ("run_id",)

# manifest 内:时间与本机路径类字段,同一输入两次跑也不该相同。
MANIFEST_VOLATILE = (
    "created_at",          # wall-clock 写入时间
    "scenario_path",       # 本机绝对路径(跨机必然不同)
    "financial_data_dir",  # 本机绝对路径
)

# manifest 内:跨 commit 会变。默认排除,但**单独报出来** ——
# 因为"同一 commit 复现"是更强的主张,想比的人应该能看见它们到底变没变。
MANIFEST_REPORT_ONLY = (
    "git_commit",
    "git_commit_source",
)


def _load(runs_dir, run_id):
    path = os.path.join(runs_dir, run_id, "run.json")
    if not os.path.isfile(path):
        return None, path
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh), path


def _strip(rec):
    """剥掉易变字段;返回 (可比对的口径, 被剥掉的键清单)。"""
    stripped_keys = []
    out = {}
    for k, v in rec.items():
        if k in TOP_VOLATILE:
            stripped_keys.append(k)
            continue
        if k == "manifest" and isinstance(v, dict):
            m = {}
            for mk, mv in v.items():
                if mk in MANIFEST_VOLATILE or mk in MANIFEST_REPORT_ONLY:
                    stripped_keys.append("manifest." + mk)
                    continue
                m[mk] = mv
            out[k] = m
            continue
        out[k] = v
    return out, sorted(stripped_keys)


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _diff_keys(a, b, prefix=""):
    """逐层找出取值不同的键路径。"""
    diffs = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                diffs.append(prefix + k)
                continue
            diffs.extend(_diff_keys(a[k], b[k], prefix + k + "."))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(prefix + "[len]")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diffs.extend(_diff_keys(x, y, "{}[{}].".format(prefix, i)))
    else:
        if _canon(a) != _canon(b):
            diffs.append(prefix.rstrip("."))
    return diffs


def _git_commits(a, b):
    """取两条记录 manifest 里的 git_commit(若有),供"是否同 commit"提示。"""
    return ((a.get("manifest") or {}).get("git_commit", ""),
            (b.get("manifest") or {}).get("git_commit", ""))


def compare(rec_a, rec_b):
    """返回比对结果 dict(纯函数,便于测试)。"""
    ca, ka = _strip(rec_a)
    cb, kb = _strip(rec_b)
    diffs = _diff_keys(ca, cb)
    gc_a, gc_b = _git_commits(rec_a, rec_b)
    return {
        "equal": not diffs,
        "diff_keys": diffs,
        "stripped_keys_a": ka,
        "stripped_keys_b": kb,
        "sha256_a": __import__("hashlib").sha256(_canon(ca).encode()).hexdigest(),
        "sha256_b": __import__("hashlib").sha256(_canon(cb).encode()).hexdigest(),
        "git_commit_a": gc_a,
        "git_commit_b": gc_b,
        "same_commit": bool(gc_a) and gc_a == gc_b,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="两条 case01 记录的可复现性比对")
    ap.add_argument("run_a")
    ap.add_argument("run_b")
    ap.add_argument("--runs-dir", default="", help="记录根;缺省用 CASE01_RUNS_ROOT/默认根")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    runs_dir = args.runs_dir or RUNS_ROOT()
    a, pa = _load(runs_dir, args.run_a)
    b, pb = _load(runs_dir, args.run_b)
    if a is None:
        print("读不到记录:", pa)
        return 2
    if b is None:
        print("读不到记录:", pb)
        return 2

    res = compare(a, b)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["equal"] else 1

    print("比对根:", runs_dir)
    print("  A =", args.run_a)
    print("  B =", args.run_b)
    print()
    print("已排除(易变字段):", ", ".join(res["stripped_keys_a"]) or "(无)")
    if res["git_commit_a"] or res["git_commit_b"]:
        print("git_commit: A={} B={} {}".format(
            res["git_commit_a"][:12] or "-", res["git_commit_b"][:12] or "-",
            "(同 commit)" if res["same_commit"] else "(不同 commit —— 默认已排除该字段)"))
    print()
    if res["equal"]:
        print("结论: 排除易变字段后**逐字节一致** → 这两条可判为同一次可复现产出。")
        print("      归一化 sha256 =", res["sha256_a"])
        return 0
    print("结论: 存在**实质差异** → 不可判为可复现。差异键 {} 处:".format(
        len(res["diff_keys"])))
    for k in res["diff_keys"][:40]:
        print("   -", k)
    if len(res["diff_keys"]) > 40:
        print("   ...(还有 {} 处)".format(len(res["diff_keys"]) - 40))
    return 1


if __name__ == "__main__":
    sys.exit(main())
