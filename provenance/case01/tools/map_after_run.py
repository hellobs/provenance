# -*- coding: utf-8 -*-
"""实跑跑完**自动**把原始记录映射成成品记录(免得"跑完了但面板里什么都没有")。

为什么要有它
-----------
`--start case01` 只负责起实时推演并落一份 `raw.json`;要在审阅面板(5004)/
只读契约(5002)里看到这条记录,还得再跑一次
`python -m case01.injector.pipeline ... --from-record <raw> --reflect --out <run.json>`。
这一步以前是**手工**的,忘了跑的结果就是:实跑明明成功了,但两个面上什么都没有——
属于"静默后果",按本工作区的铁律不允许。

做法
----
`live_switch.py --start case01` 在实时面起好之后,再起一个**脱离的看护进程**
(本模块),它:

1. 轮询等 `raw.json` 出现(推演跑完 bridge.save 才会写);
2. 等文件大小连续若干次不变(防读到写了一半的 JSON);
3. 跑 pipeline 映射成 `case01/runs/<run_id>/run.json`;
4. 校验成品文件真的存在,成功/失败都打日志、都留痕。

它**不改**任何已有记录,也可以随时手工重复执行(幂等:重跑覆盖同一路径)。

额外的一条"不静默"保护:如果实时面已经退出(端口不再监听)且 `raw.json`
始终没出现,说明那次实跑**没写成记录**(崩了/被中断),这时立刻报错退出,
而不是傻等到超时。

用法(在 `provenance/provenance` 下)
------------------------------------
    python -m case01.tools.map_after_run --run-id <run_id> --branch B \
        --raw case01/runs_injector/<run_id>/raw.json --port 5010
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(os.path.dirname(HERE))          # provenance/provenance
RUNS_DIR = os.path.join(PKG_ROOT, "case01", "runs")


def log(msg: str) -> None:
    """带时间戳打印并立即刷盘(会被 live_switch 重定向到日志文件)。"""
    print("[{}] {}".format(datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def _port_listening(port: int) -> bool:
    """Windows:netstat 看该端口是否还有 LISTENING。取不到就当作 True(保守)。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return True
    return bool(re.search(r"^\s*TCP\s+\S+:%d\s+\S+\s+LISTENING\s+\d+\s*$" % port,
                          out, re.M))


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def _looks_like_json(path: str) -> bool:
    """能解析成 JSON 才算写完整(大小不变也可能是写坏了)。"""
    try:
        with open(path, encoding="utf-8") as f:
            json.load(f)
        return True
    except Exception:  # noqa: BLE001
        return False


def wait_for_raw(raw: str, port: int, poll: float, stable: int, timeout: float) -> str:
    """等 raw.json 写完。返回 'ready' / 'no-record' / 'timeout'。"""
    deadline = time.time() + timeout
    last_size, same = -2, 0
    gone_polls = 0
    while time.time() < deadline:
        size = _size(raw)
        if size > 0:
            # 大小连续 stable 次不变 + 能解析成 JSON → 认定写完
            if size == last_size:
                same += 1
            else:
                same = 0
            last_size = size
            if same >= stable and _looks_like_json(raw):
                return "ready"
        else:
            if not _port_listening(port):
                gone_polls += 1
                if gone_polls >= 3:
                    return "no-record"
            else:
                gone_polls = 0
        time.sleep(poll)
    return "timeout"


def run_mapping(run_id: str, branch: str, raw: str, out: str, reflect: bool) -> int:
    cmd = [sys.executable, "-m", "case01.injector.pipeline",
           "--branch", branch, "--run-id", run_id,
           "--from-record", raw, "--out", out]
    if reflect:
        cmd.append("--reflect")
    log("映射命令: " + " ".join(cmd))
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=PKG_ROOT, capture_output=True, text=True)
    dt = time.time() - t0
    tail = (proc.stdout or "").strip()
    if tail:
        log("映射输出(尾部 1500 字符):\n" + tail[-1500:])
    if (proc.stderr or "").strip():
        log("映射 stderr(尾部 1500 字符):\n" + proc.stderr.strip()[-1500:])
    if proc.returncode != 0:
        log("✗ 映射失败(exit={}, 耗时 {:.0f}s)。原始记录仍在:{}\n"
            "  可手工重跑: python -m case01.injector.pipeline --branch {} --run-id {} "
            "--from-record {} --reflect --out {}".format(
                proc.returncode, dt, raw, branch, run_id, raw, out))
        return proc.returncode
    if not os.path.exists(out):
        log("✗ 映射命令返回 0,但成品文件不存在:{}".format(out))
        return 1
    log("✓ 成品记录已生成({:.0f}s):{}".format(dt, out))
    log("  审阅面板: http://127.0.0.1:5004/   (刷新后在下拉里选 {})".format(run_id))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="实跑跑完自动映射成成品记录")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--branch", default="B", choices=["A", "B", "C"])
    ap.add_argument("--raw", required=True, help="实时面写出的原始记录路径")
    ap.add_argument("--out", default="", help="成品记录路径(默认 case01/runs/<run_id>/run.json)")
    ap.add_argument("--port", type=int, default=5010, help="实时面端口(用于判断它是否还在)")
    ap.add_argument("--poll", type=float, default=10.0, help="轮询间隔秒")
    ap.add_argument("--stable", type=int, default=3,
                    help="文件大小连续多少次不变才认为写完(默认 3 次)")
    ap.add_argument("--timeout", type=float, default=7200.0, help="最多等多久(秒)")
    ap.add_argument("--no-reflect", dest="reflect", action="store_false",
                    help="映射时不跑反思(默认跑)")
    args = ap.parse_args(argv)

    raw = os.path.abspath(args.raw)
    out = os.path.abspath(args.out) if args.out else os.path.join(RUNS_DIR, args.run_id, "run.json")
    log("看护启动:run_id={} branch={}".format(args.run_id, args.branch))
    log("  等原始记录:{}".format(raw))

    state = wait_for_raw(raw, args.port, args.poll, args.stable, args.timeout)
    if state == "no-record":
        log("✗ 实时面已经退出,但原始记录一直没出现:{}".format(raw))
        log("  说明那次实跑**没写成记录**(崩了或被中断)。请看实时面日志:{}".format(
            os.path.join(os.environ.get("TEMP", ""), "dsh_srv", "live_case01.out")))
        return 2
    if state == "timeout":
        log("✗ 等了 {:.0f} 秒仍没等到写好的原始记录:{}".format(args.timeout, raw))
        return 3

    log("原始记录已就绪({} 字节),开始映射".format(_size(raw)))
    return run_mapping(args.run_id, args.branch, raw, out, args.reflect)


if __name__ == "__main__":
    sys.exit(main())
