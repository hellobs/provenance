# -*- coding: utf-8 -*-
"""demo 记录双份同步守卫:**服务读的那份**与**交付包里的那份**必须逐字节相同。

为什么要有它(2026-10-05 第六轮只读核查 P2-2):
    5010/5002 服务实际读取的是**仓内** `case01/runs/demo1015-*`;
    交给对方的 demo 包(`D:\\zzr\\demo-case01-20261015\\`,仓外)是它的副本。
    两处必须一致,否则"演示看到的"和"交付给对方的"是两份东西 ——
    而 `SHA256SUMS.txt` 只覆盖包内,对仓内那份**一无所知**,自证不了这一点。

    此前这条同步靠人记得。本脚本把它变成一条可复跑的命令(并接进测试面)。

**拷贝方向(2026-10-05 第九轮 N9-1 订正 —— 此前注释写反了)**:
    实际是**包内为原件、仓内为副本**,不是本docstring 原先写的"仓内→包"。
    证据两条,均可复核:
      ① `run_demo.sh` 把 `CASE01_RUNS_ROOT` 指向**包内** `runs/`,脚本全文
         **没有任何 cp/copy** —— 包内那四份是直接生成在包里的;
      ② mtime 时序:包内 13:42-13:54(四条依次),仓内 15:45:23/24/25(三连、
         间隔 1 秒 =典型 `cp -r`)。
    为什么要写清:方向含糊时,演示当天"按手册重跑 `run_demo.sh`"只会重新生成
    **包内**那一份,服务读的**仓内**那份一字不改 —— 现场看到旧记录、交给对方的
    说明是新记录,而本守卫要等有人手动跑它才会报。**所以那四条已冻结为固定产物,
    演示当天不重跑生成**(若真要重生成,必须同步本守卫并重跑质检,见手册)。

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


def check_zip(package: str, zip_path: str = "", require: bool = False) -> tuple:
    """校验**zip 交付件**与包目录一致(2026-10-05 第十二轮4.3)。

    为什么必须单独查:本脚本原先只比"仓内 vs 包目录",**对zip 一无所知**。
    而实际交付出去的是 zip,不是目录 —— 目录改了而 zip 没重打时,
    本守卫照样报"OK:逐字节相同",而对方拿到的 zip 里是**旧内容**。
    2026-10-05 实测踩到:`SHA256SUMS.txt` 在包里 20:09订正了拷贝方向,
    zip 仍是 18:32 那份,`.zip.sha256` 也匹配这个旧 zip ⇒
    **任何人校验 zip 都"通过"**,自洽得看不出错。

    查三件事(缺任一都不算通过):
      ① zip 内每个文件与包目录同名文件逐字节相同;
      ② zip 内文件集与目录文件集**完全相同**(多/少都算不一致 ——
         多出来的旧文件同样会误导对方);
      ③ `.zip.sha256` sidecar 匹配 zip 自身(它只证明 zip 没坏,不证明 zip 新)。

    默认:**zip 不在就返回"不适用"**(None)—— 开发期"包已生成、还没打包"是正常中间态,
    "没交付件 ≠ 不一致",别误报(这条语义有测试守着:
    `tests/test_demo_package_sync.py::test_zip_check_is_a_noop_when_nothing_is_packaged`)。
    `require=True`(CLI 的 `--require-zip`):**冻结/交付时必须打** —— 删了 zip 就是交付件缺失,
    不能因为"清单与目录自洽"而算通过。
    """
    zip_path = zip_path or (package.rstrip("\\/") + ".zip")
    if not os.path.isfile(zip_path):
        if require:
            return ["{}:冻结/交付要求 zip 存在,但它不在 —— 交付件缺失".format(
                os.path.basename(zip_path))]
        return None                      # 没打 zip ⇒ 本项不适用(见 docstring)
    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        stale = []
        for n in names:
            local = os.path.join(package, n.replace("/", os.sep))
            if not os.path.isfile(local):
                stale.append("{}:zip 内有、目录里没有".format(n))
                continue
            if hashlib.sha256(zf.read(n)).hexdigest() != _sha256(local):
                stale.append("{}:内容与目录不同".format(n))
    on_disk = set()
    for dp, _dirs, fs in os.walk(package):
        for f in fs:
            on_disk.add(os.path.relpath(os.path.join(dp, f), package
                        ).replace(os.sep, "/"))
    only_local = sorted(on_disk - set(names))
    sidecar = zip_path + ".sha256"
    side = None
    if os.path.isfile(sidecar):
        with open(sidecar, encoding="utf-8") as f:
            parts = f.read().split()
        side = parts[0] if parts else None
    problems = list(stale) + ["{}:目录里有、zip 内没有".format(n) for n in only_local]
    if side is not None and side != _sha256(zip_path):
        problems.append("{}.sha256 与 zip 不匹配".format(os.path.basename(zip_path)))
    elif side is None:
        problems.append("缺 {}.sha256(对方无法校验)".format(os.path.basename(zip_path)))
    return problems


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
    ap.add_argument("--require-zip", action="store_true",
                    help="冻结/交付时用:zip 不存在即判失败(默认:不适用,给开发期用)")
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
        print("\n⚠ demo 两份不同步 —— 交付给对方的与演示用的不是同一份。")
        print("   注意方向:**包内是原件**(run_demo.sh 直接生成在包里),"
              "仓内是副本;")
        print("   所以 `run_demo.sh` **不会**把包内更新到仓内 —— 重跑它只改包内那一份,"
              "服务读的仓内那份原地不动。")
        print("   正确处置:先确认哪边是新的,再单向 cp 到另一边,最后重跑本脚本。")
        print("   (仓内那份已被跟踪,改动会进 git status —— 提交前记得复核)")
        return 1
    print("OK: 仓内与包内逐字节相同。")
    # zip 是真正交付出去的那一份,目录一致**不代表** zip 一致(第十二轮 4.3)
    zip_problems = check_zip(args.package, require=args.require_zip)
    if zip_problems is None:
        print("zip: 不存在(尚未打包),本项不适用"
              "(冻结/交付时请加 --require-zip,那时它缺失就是失败)")
    elif not zip_problems:
        print("zip: 与包目录逐字节相同,sidecar 匹配")
    else:
        print("\n⚠ zip 与包目录不同步(共{} 处):".format(len(zip_problems)))
        for p in zip_problems:
            print("  [zip] {}".format(p))
        print("   处置:重打 zip 并重算 .zip.sha256。**包内任何文件变动"
              "(含 SHA256SUMS.txt 自身)都必须重打** ——")
        print("   sidecar 只证明 zip 没损坏,不证明 zip 是新的。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
