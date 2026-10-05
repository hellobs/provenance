# -*- coding: utf-8 -*-
"""demo 记录双份同步守卫:**服务读的那份**与**交付包里的那份**必须逐字节相同。

为什么要有它(2026-10-05 第六轮只读核查 P2-2):
    5010/5002 服务实际读取的是**仓内** `case01/runs/demo1015-*`;
    而交给对方的 demo 包(`D:\\zzr\\demo-case01-20261015\\`,仓外)是**手工拷贝**的。
    两处必须一致,否则"演示看到的"和"交付给对方的"是两份东西 ——
    而 `SHA256SUMS.txt` 只覆盖包内,对仓内那份**一无所知**,自证不了这一点。

    此前这条同步靠人记得。本脚本把它变成一条可复跑的命令(并接进测试面)。

用法
----
    python -m tools.verify_demo_sync                      # 用默认包路径
    python -m tools.verify_demo_sync --package <目录>      # 指定包根

退出码:0 = 全同(或缺包目录,视为"没交付包可查");非 0 = 有差异/缺文件。
"""
import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(HERE)                      # provenance/provenance
REPO_ROOT = os.path.dirname(PKG_ROOT)
DEFAULT_PKG = os.path.join(os.path.dirname(REPO_ROOT),
                           "demo-case01-20261015")     # 仓外,默认位置

RUNS = ("demo1015-A", "demo1015-B", "demo1015-C", "demo1015-auto")
FILES = ("run.json", "turns.jsonl", "retrievals.jsonl", "branch.json")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def compare(package: str, repo_runs: str = "") -> tuple:
    """返回 (missing, diff, n_same)。missing/diff 为 (run, file, 说明) 列表。

    `repo_runs` 默认取仓内 `case01/runs`。**可覆盖**是为了让守卫自测能
    把两侧都指向 tmp 目录 —— 否则这条测试会隐式依赖"仓内正好有 demo 记录",
    而 `case01/runs/` 是 gitignored 的(CI 干净检出上必然没有),
    守卫自测就会在 CI 上红(2026-10-05 实测踩过:CI run 37297898731)。
    """
    in_repo = repo_runs or os.path.join(PKG_ROOT, "case01", "runs")
    pkg_runs = os.path.join(package, "runs")
    missing, diff, same = [], [], 0
    for run in RUNS:
        for name in FILES:
            a = os.path.join(in_repo, run, name)
            b = os.path.join(pkg_runs, run, name)
            if not os.path.isfile(b):
                missing.append((run, name, "包内缺文件:{}".format(b)))
                continue
            if not os.path.isfile(a):
                missing.append((run, name, "仓内缺文件:{}".format(a)))
                continue
            if _sha256(a) == _sha256(b):
                same += 1
            else:
                diff.append((run, name, "两边字节不同"))
    return missing, diff, same


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="demo 双份记录同步守卫")
    ap.add_argument("--package", default=DEFAULT_PKG,
                    help="交付包根目录(默认 {}）".format(DEFAULT_PKG))
    args = ap.parse_args(argv)

    if not os.path.isdir(args.package):
        print("包目录不存在(视为未交付): {}".format(args.package))
        return 0        # 没包可查 ≠ 不一致
    missing, diff, same = compare(args.package)
    total = len(RUNS) * len(FILES)
    print("包根: {}".format(args.package))
    print("一致 {}/{}".format(same, total))
    for run, name, why in missing:
        print("  [缺] {}/{}: {}".format(run, name, why))
    for run, name, why in diff:
        print("  [异] {}/{}: {}".format(run, name, why))
    if missing or diff:
        print("\n⚠ demo 两份不同步 —— 交付给对方的与演示用的不是同一份。"
              "重跑 run_demo.sh 拷贝,或核对哪边是新的。")
        return 1
    print("OK: 仓内与包内逐字节相同。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
