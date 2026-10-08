#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重打交付包 zip + 写 sidecar(冻结流程第 1 步,一条命令)。

用法(在仓根):
    python tools/make_demo_zip.py                 # 包目录默认 D:\\zzr\\demo-case01-20261015
    python tools/make_demo_zip.py --package <目录> --out <zip 路径>
    python tools/make_demo_zip.py --dry-run       # 只列将要打包的条目,不写盘

为什么要有它(2026-10-06):冻结流程写的是"重打 zip → 校验 → 记哈希",但仓里**没有打包工具** ——
`zipfile` 只出现在校验侧,上一次的 zip 是手敲出来的。没有这一步,"一页纸"就落不了地。

与既有 zip 对齐的三件事(照 `D:\\zzr\\demo-case01-20261015.zip` 实测):
  * 条目名用**正斜杠**、**不带顶层目录**、**不写目录条目**(21 条带 `/`、2 条在根、0 条目录);
  * 用 `zipfile.write()` ⇒ 保留源文件 mtime(实测条目时间戳 = 源文件时间);
  * sidecar 格式 `<sha256>  <zip 文件名>`(两个空格、只写文件名)。

打包本身**不做校验**:打完必须再跑
    python provenance/tools/verify_demo_sync.py --require-zip
它才是"仓内 ↔ 包内 ↔ zip ↔ sidecar"四者一致的判据。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import zipfile


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_package() -> str:
    """与 verify_demo_sync 同一口径:默认取与仓根并列的 demo 包目录。

    那句"单一来源"此前是假的(2026-10-08 实测):`verify_demo_sync` 在 `provenance/tools/`,
    本文件在仓根 `tools/`,而旧实现插的是**自己所在目录** ⇒ import 必失败,永远走 except 里
    的第二份推导。两份当时算出同一个值所以没有数值后果,但默认包目录是交付链上的参数,
    改一处忘另一处就会漂移。现在真的只有一个来源,导不到就当场抛 —— 不在交付工具里留兜底。
    """
    sys.path.insert(0, os.path.join(REPO_ROOT, "provenance", "tools"))
    from verify_demo_sync import DEFAULT_PKG
    return DEFAULT_PKG


def collect(package: str, out_zip: str, out_side: str):
    """递归收集要打包的文件;排除 zip/sidecar 自身(否则每次重打都把自己装进去)。"""
    items = []
    for dirpath, dirs, files in os.walk(package):
        dirs.sort()
        for fn in sorted(files):
            full = os.path.join(dirpath, fn)
            if os.path.abspath(full) in (os.path.abspath(out_zip), os.path.abspath(out_side)):
                continue
            arc = os.path.relpath(full, package).replace(os.sep, "/")
            items.append((full, arc))
    return items


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="重打交付包 zip 并写 .zip.sha256")
    ap.add_argument("--package", default=default_package(),
                    help="交付包目录(默认与仓并列的 demo-case01-20261015)")
    ap.add_argument("--out", default="", help="zip 输出路径(默认 <package>.zip)")
    ap.add_argument("--dry-run", action="store_true", help="只列条目,不写盘")
    args = ap.parse_args(argv)

    package = os.path.abspath(args.package)
    if not os.path.isdir(package):
        print("[失败] 包目录不存在:{}\n  新克隆上没有交付包是正常的;要造它先跑包内 run_demo.sh。"
              .format(package))
        return 2
    out_zip = os.path.abspath(args.out) if args.out else package.rstrip("\\/") + ".zip"
    out_side = out_zip + ".sha256"
    items = collect(package, out_zip, out_side)
    if not items:
        print("[失败] 包目录里一个文件都没有:{} —— 不生产空 zip(那看起来像成功)".format(package))
        return 2
    print("包目录:{}".format(package))
    print("条目 {} 个:".format(len(items)))
    for _full, arc in items:
        print("   " + arc)
    if args.dry_run:
        print("\n--dry-run:未写盘。去掉它即打包到 {}".format(out_zip))
        return 0

    old = sha256(out_zip) if os.path.isfile(out_zip) else ""
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for full, arc in items:
            zf.write(full, arcname=arc)
    new = sha256(out_zip)
    with open(out_side, "w", encoding="utf-8", newline="\n") as f:
        f.write("{}  {}\n".format(new, os.path.basename(out_zip)))
    print("\n已写 {} ({} 条目)".format(out_zip, len(items)))
    print("已写 {} ".format(out_side))
    print("sha256 {}{}".format(new, "" if not old else ("(与上一份{} )".format(
        "相同" if old == new else "**不同** —— 内容确实变了,交付前确认这次改动是有意的"))))
    print("\n**下一步(必做)**:python provenance/tools/verify_demo_sync.py --require-zip")
    print("  它才判「仓内 ↔ 包内 ↔ zip ↔ sidecar」四者一致;本工具只管打包。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
