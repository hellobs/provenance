#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""数据归类搬迁:把"跑出来的存档"从代码目录挪到仓根 `data/`。

    python tools/migrate_data_layout.py                # 只看(dry-run,默认)
    python tools/migrate_data_layout.py --apply        # 真搬
    python tools/migrate_data_layout.py --only state,injector --apply   # 只搬这几组(分步搬/续搬)
    python tools/migrate_data_layout.py --undo <台账>  # 按台账搬回去

分组名(`--only` 用):
    runs      case01/runs(成品记录;**正在被 5010 读时别搬**)
    injector  case01/runs_injector(拆成 raw/ 与 runtime/)
    state     case01/injector/scenario/checkpoints(引擎现场存档)
    archive   case01/runs_archive_20260919
    html      case01/runs_html
    shells    两个空目录(case01/results、仓根 results)

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
import re
import shutil
import subprocess
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

# ---------------------------------------------------------------------------
# case00 存档(第二批):`provenance/results/checkpoints/` 里**混着三类东西**,搬之前必须分类,
# 否则测试残留会跟着进"数据家":
#   a) 跨局登记簿(根下的 `*.json` / `*.jsonl`)→ data/ledgers/
#   b) 真存档(有现场数据的模拟)→ data/case00/state/<模拟名>/
#   c) 测试/探针残留 → data/_quarantine/case00/<名字>/(**不进** case00/state;
#      隔离而不是删,台账留痕,确认后可一把删)
# 判据(c):名字带测试语义,或整个目录里一个文件都没有(只剩空壳)。
# ---------------------------------------------------------------------------
CASE00_SRC = "provenance/results/checkpoints"
CASE00_DEST = "data/case00/state"
LEDGER_DEST = "data/ledgers"
QUARANTINE = "data/_quarantine/case00"
_TEST_NAME_RE = re.compile(r"(^_|test|visual-path|^stage$|^hc$|mazecheck|ivd-|fresh-check)",
                           re.IGNORECASE)
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


def build_plan(groups=None):
    """返回 (moves, drops, problems)。moves 每项 (old_abs, new_abs)。

    `groups` 为 None 表示全搬;否则只搬选中的组(`--only`)。
    """
    want = None if not groups else {g.strip() for g in groups if g.strip()}
    moves, drops, problems = [], [], []

    def on(name):
        return want is None or name in want

    for name, (old_rel, new_rel) in (("runs", WHOLE[0]), ("state", WHOLE[1]),
                                     ("archive", WHOLE[2]), ("html", WHOLE[3])):
        if not on(name):
            continue
        old, new = os.path.join(REPO, *old_rel.split("/")), os.path.join(REPO, *new_rel.split("/"))
        if not os.path.isdir(old):
            continue
        files = list(walk_files(old))
        if not files:
            continue                      # 空壳:没什么可搬(搬完的收尾由 prune_empty 负责)
        if os.path.exists(new):
            problems.append("目标已存在且老位置**还有 {} 个文件**,需人工确认:{}".format(
                len(files), new_rel))
            continue
        for f in files:
            moves.append((f, os.path.join(new, os.path.relpath(f, old))))
    if on("case00"):
        src = os.path.join(REPO, *CASE00_SRC.split("/"))
        if os.path.isdir(src):
            for name in sorted(os.listdir(src)):
                p = os.path.join(src, name)
                if os.path.isfile(p):                    # (a) 跨局登记簿 → data/ledgers/
                    # 带 `.bak` 的是**备份**不是现行账本 → 进 ledgers/_bak/(同一族,但一眼看得出不是活的)
                    sub = "_bak" if ".bak" in name else ""
                    dest = os.path.join(REPO, *LEDGER_DEST.split("/"), sub, name) if sub \
                        else os.path.join(REPO, *LEDGER_DEST.split("/"), name)
                    if os.path.exists(dest):
                        try:
                            same = (os.path.getsize(p) == os.path.getsize(dest)
                                    and sha256(p) == sha256(dest))
                        except OSError:
                            same = False
                        if same:
                            continue
                        problems.append("登记簿目标已存在且内容不同,拒绝覆盖:{}".format(name))
                        continue
                    moves.append((p, dest))
                    continue
                files = list(walk_files(p))
                # (c) 测试/探针残留:名字带测试语义,或整个目录一个文件都没有 → 隔离
                test_like = bool(_TEST_NAME_RE.search(name)) or not files
                root_rel = QUARANTINE if test_like else CASE00_DEST
                dest_root = os.path.join(REPO, *root_rel.split("/"), name)
                for f in files:
                    new = os.path.join(dest_root, os.path.relpath(f, p))
                    if os.path.exists(new):
                        continue                              # 已搬过:跳过(可重复跑)
                    moves.append((f, new))
    for old_root_rel, rec_rel, rest_rel in SPLIT:
        if not on("injector"):
            continue
        old_root = os.path.join(REPO, *old_root_rel.split("/"))
        if not os.path.isdir(old_root):
            continue
        for f in walk_files(old_root):
            rel = os.path.relpath(f, old_root)
            parts = rel.split(os.sep)
            rid = parts[0] if len(parts) > 1 else "_loose"
            tail = os.path.join(*parts[1:]) if len(parts) > 1 else parts[0]
            dest_root_rel = rec_rel if os.path.basename(f) in RECORD_NAMES else rest_rel
            new = os.path.join(REPO, *dest_root_rel.split("/"), rid, tail)
            # 拆分组也要做"拒绝覆盖":这里最容易出现"搬过一次、之后又新写了同一条"的情形
            # (runs_injector 是活的写入目标),静默覆盖会吃掉新记录。
            if os.path.exists(new):
                try:
                    same = (os.path.getsize(f) == os.path.getsize(new)
                            and sha256(f) == sha256(new))
                except OSError:
                    same = False
                if same:
                    continue              # 已经搬过且内容一致:跳过(可重复跑)
                problems.append("目标已存在且内容不同,拒绝覆盖:{}".format(
                    os.path.relpath(new, REPO).replace("\\", "/")))
                continue
            moves.append((f, new))
    for rel in DROP_IF_EMPTY:
        if not on("shells"):
            continue
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


def prune_empty(root: str):
    """把 root 下变空的目录自底向上删掉(只删**空**的),返回被删的相对路径列表。

    为什么需要:整棵搬走文件后,老位置会留下一串空目录。留着它们有两个坏处 ——
    ① "哪个才是真的"又变模糊;② `data_root()` 的存在性判据会被空壳满足。
    """
    gone = []
    if not os.path.isdir(root):
        return gone
    for dp, dn, fn in os.walk(root, topdown=False):
        if dp == root:
            continue
        if not os.listdir(dp):
            try:
                os.rmdir(dp)
                gone.append(dp)
            except OSError:
                pass
    if os.path.isdir(root) and not os.listdir(root):
        try:
            os.rmdir(root)
            gone.append(root)
        except OSError:
            pass
    return gone


def do_move(moves, drops, apply_: bool, prune_roots=()):
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
        for root in prune_roots:
            for gone in prune_empty(root):
                dropped.append(os.path.relpath(gone, REPO).replace("\\", "/") + "  (搬空的壳)")
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


def who_may_be_reading() -> list:
    """尽力找出"可能正在读记录根"的进程(只报名字,不动手)。

    为什么只警告不阻断:搬运是用户显式要求的动作;**但要出声** ——
    5010 的审查面在启动时就把记录根记在内存里,搬走会让**已在跑的进程**读不到记录
    (重启即恢复)。2026-10-08 实测:另一个会话的两个 `live_run --review-only` 就是这样。
    """
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                 "ForEach-Object { $_.ProcessId.ToString() + ' ' + $_.CommandLine }"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30).stdout or ""
        else:
            out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace",
                                 timeout=30).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return []
    hits = []
    for line in out.splitlines():
        if re.search(r"live_run|live_fastapi|case01\.serve|case00\.serve|review_app|"
                     r"serve_all|stage_run", line):
            hits.append(line.strip()[:120])
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据归类搬迁(case01 存档 → 仓根 data/)")
    ap.add_argument("--apply", action="store_true", help="真搬(默认只 dry-run)")
    ap.add_argument("--undo", default="", help="按台账搬回去")
    ap.add_argument("--only", default="",
                    help="只搬这几组(逗号分隔):runs,injector,state,archive,html,shells")
    args = ap.parse_args(argv)

    if args.undo:
        n, bad = do_undo(args.undo, args.apply)
        print("可搬回 {} 个文件{}".format(n, "" if args.apply else "(dry-run)"))
        for b in bad:
            print("  [!] " + b)
        return 1 if bad else 0

    groups = [g for g in args.only.replace(",", " ").split() if g]
    want_runs = (not groups) or ("runs" in groups)      # 搬 runs 才需要"没人在读"的窗口
    moves, drops, problems = build_plan(groups)
    print("仓根:{}".format(REPO))
    if groups:
        print("只搬这几组:{}".format(", ".join(groups)))
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
    if want_runs:
        busy = who_may_be_reading()
        if busy:
            print("⚠ 检测到 {} 个可能正在读记录/服务的进程(搬 runs 会让它们读不到记录,重启即恢复):"
                  .format(len(busy)))
            for b in busy[:5]:
                print("   " + b)
            print("   建议:先按各自的停法停掉,搬完再起。")
    long_ones = [n for _, n in moves if len(n) > LONG_PATH]
    if long_ones:
        print("⚠ 搬家后路径超过 {} 字符的 {} 个(Windows 上可能打不开):".format(LONG_PATH, len(long_ones)))
        for n in long_ones[:3]:
            print("   " + os.path.relpath(n, REPO).replace("\\", "/"))
    if not args.apply:
        print("\n--dry-run:未写盘。加 --apply 执行。")
        return 0
    entries, dropped, failed, ledger = do_move(moves, drops, True, prune_roots=[
        os.path.join(REPO, *r.split("/")) for r in
        [w[0] for w in WHOLE] + [s[0] for s in SPLIT] + [CASE00_SRC]])
    print("\n已搬 {} 个文件;台账:{}".format(len(entries), ledger))
    for rel, why in failed:
        print("  [!] {} {}".format(rel, why))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
