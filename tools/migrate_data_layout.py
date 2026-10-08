#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""数据归类搬迁:把"跑出来的存档"从代码目录挪到仓根 `data/`。

    python tools/migrate_data_layout.py                # 只看(dry-run,默认)
    python tools/migrate_data_layout.py --apply        # 真搬
    python tools/migrate_data_layout.py --undo <台账>  # 按台账搬回去

为什么要有它(2026-10-08):仓里同一个概念有过**三个落点** ——
`results/checkpoints/`(case00 沙盒存档 + 跨局登记簿)、
`case01/injector/scenario/checkpoints/`(case01 引擎状态)、
`case01/results/checkpoints/`(空壳);而 case01 还把成品/原始记录放在自己目录下。
决定:**案例目录里不留存档**,统一到仓根 `data/`;读侧由 `case_engine.paths.data_root()`
做"新位置优先、老位置回落并出声"(见 `provenance/docs/仓库布局说明.md`)。

搬迁映射(逐条与 `case_engine.paths.DATA_KINDS` 对齐):

    case01/runs/**                        → data/case01/runs/**
    case01/runs_injector/<局>/raw.json    → data/case01/raw/<局>/raw.json
    case01/runs_injector/<局>/<其它>      → data/case01/runtime/<局>/<其它>   ← 拆开:日志/pid/探针残留
    case01/injector/scenario/checkpoints/** → data/case01/state/**
    case01/runs_archive_20260919/**       → data/case01/archive/20260919/**
    case01/runs_html/**                   → data/case01/html/**
    case01/results/**                     → 删除(空壳,记进台账)
    results/**                            → 删除(仓根空目录,记进台账)

安全设计:
  * **默认 dry-run**;写盘必须显式 `--apply`;
  * 目标已存在同名文件且内容不同 → **拒绝**,绝不静默覆盖("不静默"铁律);
  * 先整棵 `os.rename`(同盘瞬时),拆分的那些按文件搬;每个文件都进台账(旧路径/新路径/字节/sha256);
  * `--undo` 按台账搬回,并校验 sha256;
  * Windows 长路径:搬家后路径超过 250 字符 → 报告(不阻断,但要点出来)。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO, "provenance")
DATA = os.path.join(REPO, "data")

# 整棵搬: (老相对仓根, 新相对仓根)
WHOLE = [
    ("provenance/case01/runs", "data/case01/runs"),
    ("provenance/case01/injector/scenario/checkpoints", "data/case01/state"),
    ("provenance/case01/runs_archive_20260919", "data/case01/archive/20260919"),
    ("provenance/case01/runs_html", "data/case01/html"),
]
# 只删(空壳/空目录): 相对仓根 —— 删之前会核对"确实没有文件"
DROP_IF_EMPTY = ["provenance/case01/results", "results"]
# 拆分搬: 老根 → (记录去哪, 其余去哪)
SPLIT = [("provenance/case01/runs_injector", "data/case01/raw", "data/case01/runtime")]
RECORD_NAMES = {"raw.json"}          # 只有它算"记录";其余(日志/pid/探针/修复副本)算运行残渣

LONG_PATH = 250


def sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_files(base: str):
    for dp, _dn, fn in os.walk(base):
        for f in fn:
            yield os.path.join(dp, f)


def build_plan():
    """返回 (moves, drops, problems)。moves 每项 (old_abs, new_abs)。"""
    moves, drops, problems = [], [], []
    for old_rel, new_rel in WHOLE:
        old, new = os.path.join(REPO, *old_rel.split("/")), os.path.join(REPO, *new_rel.split("/"))
        if not os.path.isdir(old):
            continue
        if os.path.exists(new):
            problems.append("目标已存在,需人工确认:{}".format(new_rel))
            continue
        for f in walk_files(old):
            moves.append((f, os.path.join(new, os.path.relpath(f, old))))
    for old_root_rel, rec_rel, rest_rel in SPLIT:
        old_root = os.path.join(REPO, *old_root_rel.split("/"))
        if not os.path.isdir(old_root):
            continue
        for f in walk_files(old_root):
            rel = os.path.relpath(f, old_root)
            parts = rel.split(os.sep)
            rid = parts[0] if len(parts) > 1 else "_loose"
            tail = os.path.join(*parts[1:]) if len(parts) > 1 else parts[0]
            dest_root_rel = rec_rel if os.path.basename(f) in RECORD_NAMES else rest_rel
            moves.append((f, os.path.join(REPO, *dest_root_rel.split("/"), rid, tail)))
    for rel in DROP_IF_EMPTY:
        p = os.path.join(REPO, *rel.split("/"))
        if not os.path.isdir(p):
            continue
        left = list(walk_files(p))
        if left:
            problems.append("本该是空的,实际有 {} 个文件,不动:{}".format(len(left), rel))
        else:
            drops.append(p)
    return moves, drops, problems


def check_targets(moves, apply_: bool):
    """目标冲突检查:同名文件已存在且内容不同 → 拒绝。"""
    clash = []
    for old, new in moves:
        if os.path.exists(new):
            try:
                same = os.path.getsize(old) == os.path.getsize(new) and sha256(old) == sha256(new)
            except OSError:
                same = False
            if not same:
                clash.append(new)
    return clash


def do_move(moves, drops, apply_: bool):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    ledger_path = os.path.join(DATA, "MIGRATION_{}.json".format(stamp))
    entries, failed = [], []
    for old, new in moves:
        rel_old = os.path.relpath(old, REPO)
        rel_new = os.path.relpath(new, REPO)
        try:
            size = os.path.getsize(old)
            digest = sha256(old)
        except OSError as e:
            failed.append((rel_old, "读不到:{}".format(e)))
            continue
        entries.append({"old": rel_old.replace("\\", "/"), "new": rel_new.replace("\\", "/"),
                        "size": size, "sha256": digest})
        if not apply_:
            continue
        try:
            os.makedirs(os.path.dirname(new), exist_ok=True)
            os.replace(old, new) if os.path.exists(new) else os.rename(old, new)
        except OSError as e:
            failed.append((rel_old, "搬迁失败:{}".format(e)))
    dropped = []
    for p in drops:
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        dropped.append(rel)
        if apply_:
            shutil.rmtree(p, ignore_errors=True)
    if apply_:
        os.makedirs(DATA, exist_ok=True)
        with open(ledger_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"stamp": stamp, "moves": entries, "dropped": dropped,
                       "failed": failed}, f, ensure_ascii=False, indent=2)
    return entries, dropped, failed, ledger_path


def do_undo(ledger_path: str, apply_: bool):
    with open(ledger_path, encoding="utf-8") as f:
        led = json.load(f)
    back, bad = 0, []
    for e in led.get("moves", []):
        cur = os.path.join(REPO, *e["new"].split("/"))
        orig = os.path.join(REPO, *e["old"].split("/"))
        if not os.path.isfile(cur):
            bad.append("台账里的新位置不见了:{}".format(e["new"]))
            continue
        if sha256(cur) != e["sha256"]:
            bad.append("内容与台账不符,拒绝搬回:{}".format(e["new"]))
            continue
        if not apply_:
            back += 1
            continue
        os.makedirs(os.path.dirname(orig), exist_ok=True)
        os.rename(cur, orig)
        back += 1
    return back, bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据归类搬迁(case01 存档 → 仓根 data/)")
    ap.add_argument("--apply", action="store_true", help="真搬(默认只 dry-run)")
    ap.add_argument("--undo", default="", help="按台账搬回去")
    args = ap.parse_args(argv)

    if args.undo:
        n, bad = do_undo(args.undo, args.apply)
        print("可搬回 {} 个文件{}".format(n, "" if args.apply else "(dry-run)"))
        for b in bad:
            print("  [!] " + b)
        return 1 if bad else 0

    moves, drops, problems = build_plan()
    print("仓根:{}".format(REPO))
    print("计划:搬 {} 个文件,删 {} 个空目录".format(len(moves), len(drops)))
    total = sum(os.path.getsize(o) for o, _ in moves if os.path.isfile(o))
    print("合计 {:.1f} MB".format(total / 1e6))
    roots = {}
    for old, new in moves:
        key = os.path.relpath(new, REPO).replace("\\", "/").split("/")[:3]
        roots["/".join(key)] = roots.get("/".join(key), 0) + 1
    for k in sorted(roots):
        print("   {:<34} {} 个文件".format(k, roots[k]))
    if drops:
        print("删除(核对过确实没有文件):")
        for d in drops:
            print("   " + os.path.relpath(d, REPO).replace("\\", "/"))
    for p in problems:
        print("  [!!] " + p)
    clash = check_targets(moves, args.apply)
    if clash:
        print("目标冲突 {} 处(拒绝覆盖,先人工处理):".format(len(clash)))
        for c in clash[:10]:
            print("   " + os.path.relpath(c, REPO).replace("\\", "/"))
        return 2
    long_ones = [n for _, n in moves if len(n) > LONG_PATH]
    if long_ones:
        print("⚠ 搬家后路径超过 {} 字符的 {} 个(Windows 上可能打不开):".format(LONG_PATH, len(long_ones)))
        for n in long_ones[:3]:
            print("   " + os.path.relpath(n, REPO).replace("\\", "/"))
    if not args.apply:
        print("\n--dry-run:未写盘。加 --apply 执行。")
        return 0
    entries, dropped, failed, ledger = do_move(moves, drops, True)
    print("\n已搬 {} 个文件;台账:{}".format(len(entries), ledger))
    for rel, why in failed:
        print("  [!] {} {}".format(rel, why))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
