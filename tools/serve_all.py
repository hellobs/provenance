#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""一条命令起/查/停 provenance 的各个面。

    python tools/serve_all.py                # 起:5010(case01 只读)+5002+5003+5020
    python tools/serve_all.py --all          # 再加 8060 配置工具(写面,无鉴权)
    python tools/serve_all.py --status       # 只查:端口在听吗 / HTTP 通吗
    python tools/serve_all.py --stop         # 停掉**本脚本起的**那些(按 pid 文件,不误杀)
    python tools/serve_all.py --only 5010,5020
    python tools/serve_all.py --case case00  # 5010 换成 case00
    python tools/serve_all.py --full         # 5010 真跑推演(需 Ollama/GPU);默认 --review-only

Windows 可双击 `serve.cmd`,macOS/Linux 用 `./serve.sh`。

为什么要入库(2026-10-06):这套东西原先只活在仓外(`D:\zzr\start_servers.ps1` +
`D:\zzr\_srv\svc_50xx.cmd`),不在版本库里、写死本机解释器路径 —— 演示机若是新克隆就没有它,
文件一丢下面这五个坑就得重新踩一遍。本文件把它们连同命令一起搬进仓。

**五个已经踩过的坑(不要"简化"掉)**:
  1. 本机同时有 `http_proxy` 与 `HTTP_PROXY`(大小写重复)。Windows 环境变量不区分大小写,
     而 .NET 的 ProcessStartInfo 会因重复键抛 "item with the same key already added",表现为
     **进程起不来而日志是空的**。这里给子进程一份清掉代理变量的环境(要保留用 --keep-proxy)。
  2. 传参**必须用 argv 列表**,不能拼成一个字符串。旧脚本里 PowerShell 的 -ArgumentList
     不替含空格元素加引号,`--roles "AI Advisor,Daniel Shen,..."` 被按空格拆开 ⇒ python 报
     unrecognized arguments。Python 传 list 天然没这问题。
  3. **不要用 `timeout /t` 等待**:Git Bash 的 GNU timeout 在 PATH 上盖住了 Windows 的 timeout.exe,
     它把 `/t` 当非法时间区间,脚本"等"了 0 秒就往下走(静默不等待)。这里用 time.sleep。
  4. **5020 必须带 `--loop`**(或脚本文本里 `loop: true`),否则 `stage_run.py` 放完就 break、
     服务随之退出。本文件显式带 --loop。
  5. **`stage_run` docstring 里那个 `stage_village.json` 不存在**(旧脚本的注释记着这件事)。
     2026-10-06 已把 docstring 指到真实存在且已入库的 `case00/stage/village.json`(loop=true),
     所以本文件用**仓内**脚本,不再依赖仓外的 `_stage_village_demo.json`。
  6. **有些面会 re-exec 成子进程 —— 我们 spawn 的 pid 不是持有端口的那个**(2026-10-06 实测:
     父进程是仓内 venv 的 python,真正 LISTEN 的是它拉起的另一个解释器,下面还挂着第三代)。
     所以 pid 文件里记的是**端口持有者**(启动后实测),`--stop` 也按它 + `/T` 整树杀;
     照 spawn pid 停会"报告已停、端口还在听"。
     同理:**Windows 上不要用 `os.kill(pid, 0)` 探测存活** —— Python 在 Windows 把任意信号
     实现成 TerminateProcess,`os.kill(pid, 0)` 会**真的把进程杀掉**;这里用 `tasklist` 探活。

另外两条纪律:①5010 是**唯一实时入口且两个 case 互斥**,由 `live_switch.py` 管,本文件不自己绑;
②`--stop` 只杀 pid 文件里记着的进程 —— 别人的进程一律不碰(旧脚本那句 `Stop-Process -Name python`
是全杀,这里不学)。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

IS_WIN = os.name == "nt"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)                      # 仓根
PKG_DIR = os.path.join(REPO_ROOT, "provenance")        # 服务的工作目录
LOG_DIR = os.path.join(REPO_ROOT, "logs", "serve")
DEFAULT_LOG_DIR = LOG_DIR          # main() 里用这个名字做 argparse 默认值:
                                   # 若直接用 LOG_DIR,它会出现在 `global` 声明之前 → SyntaxError
PID_FILE = os.path.join(LOG_DIR, "pids.json")

# 面定义:port → (启动参数, 健康检查 URL, 说明, 是否写面)
ROLES_5010 = "AI Advisor,Daniel Shen,Kevin Su,Michael Chen,Mr. Zhou,Wendy Lin"


def faces(case: str, full: bool, host: str, hold: float):
    live = ["live_switch.py", "--start", case, "--host", host]
    if case == "case01":
        live += ["--hold", str(int(hold))]
        if not full:
            live += ["--review-only"]      # 默认只翻记录:不占 GPU、演示更稳
    else:
        if not full:
            live += ["--no-sim"]           # case00:不起推演,只服务界面
    return {
        5010: (live, "http://127.0.0.1:5010/health",
               "实时面({} —— 唯一实时入口,两 case 互斥)".format(case), False),
        5002: (["-m", "case01.serve", "--port", "5002"], "http://127.0.0.1:5002/api/runs",
               "case01 只读契约", False),
        5003: (["-m", "case00.serve", "--port", "5003"], "http://127.0.0.1:5003/api/runs",
               "case00 存档只读", False),
        8060: (["config_tool/app.py"], "http://127.0.0.1:8060/engines",
               "配置工具(**写面,无鉴权**)", True),
        5020: (["-m", "case01.vizkit.stage_run",
                "--scenario-dir", "case00/scenario",
                "--roles", ROLES_5010,
                "--script", "case00/stage/village.json",
                "--port", "5020", "--loop"],
               "http://127.0.0.1:5020/embed/scene", "布景播放器(三级兜底第二级)", False),
    }


# ---------------------------------------------------------------------------
def say(msg=""):
    print(msg, flush=True)


def child_env(keep_proxy: bool):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"       # 中文日志别在 GBK 上炸(与 safestream 同口径)
    env["PYTHONUTF8"] = "1"
    if not keep_proxy:
        for k in list(env):
            if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
                env.pop(k, None)            # 坑 1
    return env


def pick_python(explicit: str) -> str:
    if explicit:
        return explicit
    env = os.environ.get("CASE01_SERVE_PYTHON", "").strip()
    if env:
        return env
    tail = os.path.join("Scripts", "python.exe") if IS_WIN else os.path.join("bin", "python")
    for c in (os.path.join(PKG_DIR, ".venv-live", tail),
              os.path.join(PKG_DIR, ".venv", tail),
              os.path.join(REPO_ROOT, ".venv-live", tail),
              os.path.join(REPO_ROOT, ".venv", tail)):
        if os.path.isfile(c):
            return c
    return sys.executable


def port_listening(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3.5) as r:
            return 200 <= r.status < 400
    except urllib.error.HTTPError as e:
        return 200 <= e.code < 400
    except (urllib.error.URLError, OSError):
        return False


def load_pids() -> dict:
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            return {int(k): int(v) for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


def save_pids(d: dict) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in d.items()}, f, indent=2)


def port_owner(port: int) -> int:
    """谁在听这个端口(Windows: netstat -ano;mac/Linux: lsof)。

    为什么需要:有的面会 re-exec 成子进程(坑 6),我们 spawn 的那个 pid 并不是 LISTEN 的那个。
    ASCII 的 PID 不会被 GBK/UTF-8 差异影响,但仍显式给 encoding(与 live_switch._run_stdout 同口径)。
    """
    try:
        if IS_WIN:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=25).stdout or ""
            m = re.findall(r"^\s*TCP\s+\S+:%d\s+\S+\s+LISTENING\s+(\d+)\s*$" % port, out, re.M)
            return int(m[0]) if m else 0
        out = subprocess.run(["lsof", "-ti", "tcp:%d" % port, "-s", "TCP:LISTEN"],
                             capture_output=True, text=True, timeout=25).stdout or ""
        for tok in out.split():
            if tok.strip().isdigit():
                return int(tok)
    except (OSError, subprocess.SubprocessError):
        pass
    return 0


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WIN:
        # 坑 6:Windows 上 os.kill(pid, 0) 会真的终止进程,不能用它探活
        try:
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=25).stdout or ""
            return str(pid) in out
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
def do_status(ports, spec) -> int:
    say("端口      状态         HTTP      说明")
    bad = 0
    for p in sorted(ports):
        _cmd, url, label, _w = spec[p]
        lis = port_listening(p)
        ok = http_ok(url) if lis else False
        say("  {:<6}  {:<11}  {:<9}  {}".format(
            p, "在听" if lis else "没在听", "通" if ok else "—", label))
        if not lis:
            bad += 1
    return 1 if bad else 0


def do_start(args, ports, spec, py) -> int:
    os.makedirs(LOG_DIR, exist_ok=True)
    pids = load_pids()
    started, skipped = [], []

    # 5010 交给 live_switch 自己管互斥:先让它停掉当前在跑的那个 case
    if 5010 in ports:
        try:
            subprocess.run([py, "-X", "utf8", "live_switch.py", "--stop", "all"],
                           cwd=PKG_DIR, env=child_env(args.keep_proxy),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.SubprocessError):
            say("[提示] live_switch --stop all 没跑成(可能本来就没在跑),继续")

    for p in sorted(ports):
        cmd, url, label, _is_write = spec[p]
        if port_listening(p):
            skipped.append((p, "端口已在听(不是本脚本起的,不动它)"))
            continue
        out = os.path.join(LOG_DIR, "{}.out.log".format(p))
        err = os.path.join(LOG_DIR, "{}.err.log".format(p))
        for f in (out, err):
            # 坑:旧日志会把上一次的 traceback 留下来,让人以为新起的失败了
            try:
                os.remove(f)
            except OSError:
                pass
        argv = [py, "-X", "utf8"] + cmd        # 坑 2:argv 列表,不拼字符串
        flags = 0
        kwargs = {}
        if IS_WIN:
            flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            kwargs["start_new_session"] = True
        with open(out, "wb") as fo, open(err, "wb") as fe:
            proc = subprocess.Popen(argv, cwd=PKG_DIR, env=child_env(args.keep_proxy),
                                    stdout=fo, stderr=fe, stdin=subprocess.DEVNULL,
                                    creationflags=flags, **kwargs)
        pids[p] = proc.pid
        started.append((p, proc.pid, label))
        say("[起] {} pid={}  {}".format(p, proc.pid, label))
    save_pids(pids)

    if not started:
        say("没有新起任何面。")
    if args.wait > 0:
        say("等待 {} 秒让它起来(不用 timeout /t:坑 3)…".format(args.wait))
        time.sleep(args.wait)

    # 坑 6:把 pid 换成**端口持有者**。实测有些面会 re-exec 成子进程,
    # 我们 spawn 的是父进程,照它停会"报告已停、端口还在听"。
    for p, spawn_pid, label in started:
        owner = port_owner(p)
        if owner and owner != spawn_pid:
            say("[注意] {} 实际由 pid={} 持有(spawn 的 pid={} 是父进程,该面会 re-exec) —— "
                "pid 文件记持有者".format(p, owner, spawn_pid))
            pids[p] = owner
        elif not owner:
            say("[注意] {} 还没在听;pid 文件仍记 spawn pid={}".format(p, spawn_pid))
    save_pids(pids)

    say("")
    say("面      状态      地址")
    bad = 0
    for p in sorted(ports):
        _cmd, url, label, _w = spec[p]
        ok = http_ok(url)
        say("  {:<5}  {:<8}  {}".format(p, "OK" if ok else "失败", url))
        if not ok:
            bad += 1
            say("          日志:{}".format(os.path.join(LOG_DIR, "{}.err.log".format(p))))
    for p, why in skipped:
        say("  {:<5}  跳过     {}".format(p, why))
    say("")
    if bad == 0:
        say("全部就绪。入口:http://127.0.0.1:5010/  停:`python tools/serve_all.py --stop`")
        return 0
    say("{} 个面没起来 —— 看上面日志路径(不静默)。".format(bad))
    return 1


def do_stop(ports, spec, py) -> int:
    pids = load_pids()
    if not pids:
        say("pid 文件里没有记录({})。看门狗/live_switch 自己起的请用它们自己的停法:".format(PID_FILE))
        say("  cd provenance && python live_switch.py --stop all")
        return 0
    left = {}
    for p in sorted(pids):
        rec = pids[p]
        if p not in ports:
            left[p] = rec
            continue
        owner = port_owner(p)          # 坑 6:以端口持有者为准
        target = owner or rec
        if owner and rec and owner != rec:
            say("[注意] {} 的持有者是 pid={}(记录里是 {}),按持有者停".format(p, owner, rec))
        if not target or not alive(target):
            say("[已不在] {} pid={}".format(p, target or rec))
            continue
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(target), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            else:
                os.killpg(os.getpgid(target), signal.SIGTERM)
        except (OSError, subprocess.SubprocessError) as e:
            say("[失败] 停 {} pid={}: {}".format(p, target, e))
            left[p] = target
            continue
        say("[停] {} pid={}".format(p, target))
    # 5010 额外让 live_switch 收尾(它自己管互斥与子进程)
    if 5010 in ports:
        try:
            subprocess.run([py, "-X", "utf8", "live_switch.py", "--stop", "all"],
                           cwd=PKG_DIR, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
    time.sleep(1.5)
    for p in sorted(ports):
        if port_listening(p):
            say("[仍在听] {} —— 可能不是本脚本起的,不误杀;要停用 `live_switch.py --stop all` "
                "或看门狗自己的停法".format(p))
    save_pids(left)
    return 0


def main() -> int:
    global LOG_DIR, PID_FILE          # 必须在用这两个名字之前声明(否则 SyntaxError)
    ap = argparse.ArgumentParser(description="起/查/停 provenance 各面",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true", help="只查,不动进程")
    ap.add_argument("--stop", action="store_true", help="停掉本脚本起的那些(按 pid 文件)")
    ap.add_argument("--all", action="store_true", help="连 8060 配置工具(写面)一起起")
    ap.add_argument("--only", default="", help="只操作这些端口,逗号分隔(如 5010,5020)")
    ap.add_argument("--case", choices=["case01", "case00"], default="case01", help="5010 跑哪个 case")
    ap.add_argument("--full", action="store_true",
                    help="5010 真起推演(需要 Ollama/GPU);默认 --review-only 只翻记录")
    ap.add_argument("--host", default="127.0.0.1", help="5010 绑定地址(非本机地址需显式声明)")
    ap.add_argument("--hold", type=float, default=3600, help="case01 跑完保持服务的秒数")
    ap.add_argument("--wait", type=float, default=15, help="起完后等待并自检的秒数(0=不等)")
    ap.add_argument("--python", default="", help="跑服务的解释器(默认自动探测仓内 venv)")
    ap.add_argument("--keep-proxy", action="store_true", help="保留代理环境变量给子进程(默认清掉)")
    ap.add_argument("--log-dir", default=DEFAULT_LOG_DIR,
                    help="日志目录(默认 {} 已 gitignore)".format(DEFAULT_LOG_DIR))
    args = ap.parse_args()
    global LOG_DIR, PID_FILE
    LOG_DIR = os.path.abspath(args.log_dir)
    PID_FILE = os.path.join(LOG_DIR, "pids.json")

    spec = faces(args.case, args.full, args.host, args.hold)
    default_ports = [5010, 5002, 5003, 5020] + ([8060] if args.all else [])
    if args.only:
        want = {int(x) for x in re.split(r"[,\s]+", args.only.strip()) if x}
        unknown = want - set(spec)
        if unknown:
            say("未知端口:{};可用:{}".format(sorted(unknown), sorted(spec)))
            return 2
        ports = sorted(want)
    else:
        ports = default_ports

    if args.stop:
        return do_stop(ports, spec, pick_python(args.python))
    py = pick_python(args.python)
    if args.status:
        return do_status(ports, spec)
    say("解释器:{}".format(py))
    say("工作目录:{}".format(PKG_DIR))
    say("日志目录:{}".format(LOG_DIR))
    if not os.path.isfile(py):
        say("[失败] 解释器不存在:{} —— 先跑 `python tools/setup_all.py`".format(py))
        return 2
    rc = subprocess.run([py, "-c", "import fastapi, uvicorn"], capture_output=True)
    if rc.returncode != 0:
        say("[失败] 该解释器里没有 fastapi/uvicorn —— 先跑 `python tools/setup_all.py`")
        return 2
    if 5010 in ports and args.case == "case01" and not args.full:
        say("5010:case01 **只翻记录**(--review-only,不占 GPU);要真跑加 --full")
    if 8060 in ports:
        say("注意:8060 是**写面且无鉴权**,只在需要改配置时开。")
    return do_start(args, ports, spec, py)


if __name__ == "__main__":
    sys.exit(main())
