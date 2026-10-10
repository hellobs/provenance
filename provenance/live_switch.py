# -*- coding: utf-8 -*-
"""实时面开关:机械保证任何时刻只有**一个**实时可视化在跑。

为什么要它
----------
实时面有两个**实现**,属于两个不同的 case,但**都跑在同一个 5010 端口**:

- case00(原初 6 角色投资咨询小镇):`live_fastapi.py`
- case01(2 角色、节点驱动的注入器推演):`case01/vizkit/live_run.py`

**5010 是平台唯一实时入口**:前一个 case(即 config_tool 运行组合选中的那个)在此
服务。两 case **互为互斥**:`--start` 会先停另一个 case 的实时面;因为两者共用同一个
本地 Ollama,同时跑会把双方都拖慢,演示时屏幕上也不该有两套在动。

只读面(5002 契约 / 5003 case00 存档)**不跑模拟,不受此限**,本脚本不碰。
`case01` 的 `--review-only` 就是只翻结果不推演(停掉当前在跑的那个也一样的逻辑)。
2026-09-19 起独立的 5004 面板服务已退役。

本脚本要防的两件事(都是实测踩过的)
---------------------------------
1. **同时起两个**:`--start` 会先停掉另一个 case 的实时面。
2. **同一个 case 起两份**:如果目标端口已被本 case 的旧实例占着,新进程会
   `[Errno 10048] 端口已被占用` **绑不上端口,却照样在后台跑推演**——于是变成两份在跑、
   而且外面完全看不出第二份存在。所以 `--start` 也要先停掉**本 case** 的旧实例,
   并在起完后**校验端口真的绑上了**;没绑上就把新进程收掉并报错,绝不留游离实例。

因此进程发现**不能只看端口**:游离实例不监听任何端口,只能按命令行认。
而且因为两 case **共用 5010**,端口**不归属任何单一 case**——靠端口判断"哪个 case
在跑"会把另一个 case 的进程误判进自己头上(实测会误杀),所以**本 case 的进程必须
只用命令行特征(`live_fastapi.py` / `vizkit.live_run`)找**;5010 只用来确认"谁把
端口绑上了"。
`--status` 会顺带报出匹配到的进程数,数量 >1 就是有游离实例。
状态还区分"在推演 / 已跑完在保持 / 仅审阅(`--review-only`)":case01 靠 5010 的
`/health` 里的 `finished`/`finish_reason`,case00 靠 `/api/goals` 的倾向角色数。

给平台侧的口径:平台**只需要一个地址**——`http://<host>:5010/`(单一入口,当前选中的 case
于此服务;case00 首页=中央小镇场景 + 右栏治理约束面板 + 底部干预时间轴,case01 首页=实时小镇 +
右栏"结果记录"卡片)。只看结果就引 case01 的 `http://<host>:5010/embed/review`。
平台不必知道当前在跑哪个 case、也不必知道各 case 的独立端口。

用法(在 `provenance/provenance` 下执行)
--------------------------------------
    python live_switch.py --status
    python live_switch.py --start case01 [--nodes 2] [--hold 1800] [--no-map]
    python live_switch.py --start case01 --review-only     # 不推演,只翻成品记录
    python live_switch.py --start case00 [--stride 2]
    python live_switch.py --stop case01 | case00 | all

`--start case01` **默认跑完自动映射**(`case01/vizkit/live_run.py` 在跑完当刻生成
`data/case01/runs/<run_id>/run.json`)。理由见"不允许静默"那条铁律:实跑成功了却没有成品记录,
面板上就是一片空白,而没人会知道为什么。`--no-map` 可关掉,手工映射的命令仍会打印出来。
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

from case01.run_naming import live_run_id
from case01.safestream import tolerant_stdout
from case_engine.paths import data_root

HERE = os.path.dirname(os.path.abspath(__file__))
# 进程/端口发现与终止在两个平台上用的工具和版式完全不同(netstat 版式、powershell vs
# ps、taskkill vs signal),所以按平台分叉而不是猜 —— 与 tools/serve_all.py 同一个 IS_WIN。
IS_WIN = os.name == "nt"

LIVE = {"case00": 5010, "case01": 5010}  # 5010 是唯一实时入口,两 case 互斥共用
LIVE_NAME = {"case00": "Original six-agent town (live_fastapi.py)",
             "case01": "Injector simulation (vizkit town)"}
# 命令行特征:按它认进程(端口判断不到游离实例)
CMD_NEEDLE = {"case00": "live_fastapi.py", "case01": "vizkit.live_run"}
READONLY = {5002: "case01 read-only contract", 5003: "case00 read-only archive"}
LOG_DIR = os.path.join(os.environ.get("TEMP") or tempfile.gettempdir(), "dsh_srv")


def records_root():
    """成品记录根 —— 必须与 `case01/vizkit/live_run.py` 的映射目标同一算法。

    2026-10-08 归类搬迁把记录的家从 `case01/runs` 挪到仓根 `data/case01/runs`。这里原先
    写死老根,而老根搬迁后**根本不存在**,后果不是报错而是静默:
    ① `_warn_overwrite` 查的是不存在的目录 ⇒ 沿用同一个 `--run-id` 重跑时"覆盖写"预警
    不再出声,上一局产物被原地盖掉而界面无异常(正是它要防的那条铁律);
    ② 打印出来的手动映射命令把人往老根引,照着敲会在老根**再造一份**记录,而面板读的是新根。
    """
    return data_root("case01.records", env_var="CASE01_RUNS_ROOT")


# ---------------------------------------------------------------------------
# 进程/端口发现
# ---------------------------------------------------------------------------
def _run_stdout(cmd, timeout):
    """跑外部命令并**保证返回字符串**(取不到就空串,绝不返回 None)。

    为什么要显式指定 encoding/errors:中文 Windows 上 netstat/PowerShell 的输出是
    GBK(codepage 936),而本脚本常以 `-X utf8` 运行 —— text=True 的严格 utf-8 解码
    会在读取线程里抛 UnicodeDecodeError,`communicate()` 于是把 stdout 交回 **None**,
    调用方再拿它做正则匹配就 TypeError(2026-10-04 实测:`--start` 服务**已经起来了**,
    自检却崩在 `_pids_by_port`,退出码非 0,看起来像"没起来")。
    errors="replace" 保留了 PID/端口这些 ASCII 字符,中文表头换成 U+FFFD 不影响判断。
    """
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout).stdout
    except Exception:  # noqa: BLE001
        return ""
    return out or ""


def _attribution_tools_missing():
    """POSIX 上"谁在听 5010 / 谁的命令行里有 live_fastapi.py"要靠 ss+netstat+ps 问出来。

    为什么要把"问不到"和"没有"分开说:Ubuntu 精简镜像与 `python:*-slim` 里
    net-tools/procps/iproute2 常常一个都没装(容器里连 `ps` 都可能没有)。这时本开关
    打印的 `down` / `未启动` / `本来就没在跑` 只代表**查不到进程**,不代表真没在跑 ——
    照着它再起一个,就会撞在已被占用的 5010 上(表现是"端口没绑上"自杀,像服务的锅)。
    Windows 侧走 netstat/powershell,必然有,直接返回空。
    """
    if IS_WIN:
        return []
    missing = []
    if not any(shutil.which(t) for t in ("ss", "netstat")):
        missing.append("ss/netstat (port owner)")
    if not shutil.which("ps"):
        missing.append("ps (process command line)")
    return missing


def _pids_by_port(port):
    """监听该端口的 PID 集合(不依赖 psutil),按平台选工具与版式。"""
    if IS_WIN:
        return _parse_netstat_windows(_run_stdout(["netstat", "-ano", "-p", "TCP"], 10), port)
    pids = _parse_ss(_run_stdout(["ss", "-H", "-tlnp"], 10), port)
    if pids:
        return pids
    # macOS 没有 ss、Linux 的 netstat 还常被砍掉 PID 列 ⇒ 再问 lsof(与 serve_all.port_owner 同命令)
    pids = _parse_lsof(_run_stdout(["lsof", "-ti", "tcp:%d" % port, "-s", "TCP:LISTEN"], 10))
    if pids:
        return pids
    # iproute2 没装(Ubuntu 最小镜像常见)时退 netstat;三者都拿不到就是空集 —— 
    # 与 Windows 侧同语义,调用方按"没在跑"处理,不猜(缺工具本身由 _attribution_tools_missing 出声)。
    return _parse_netstat_posix(_run_stdout(["netstat", "-tlnp"], 10), port)


def _parse_lsof(out):
    """`lsof -ti tcp:5010 -s TCP:LISTEN` 只吐 PID,一行一个。

    判据是**整行只有一个数字**:`-t` 的输出本就如此,而按"行里任意一串数字"抽会把别的
    版式(如 netstat 的 Recv-Q/Send-Q、TIME_WAIT 行的 0)当成 PID,蒙出一个不存在的进程。
    """
    return {int(line) for line in out.splitlines() if line.strip().isdigit()}


def _parse_netstat_windows(out, port):
    pat = re.compile(r"^\s*TCP\s+\S+:%d\s+\S+\s+LISTENING\s+(\d+)\s*$" % port, re.M)
    return {int(m) for m in pat.findall(out)}


def _parse_ss(out, port):
    """`ss -H -tlnp` 的版式(iproute2;`-H` 去表头,列序固定):
        LISTEN 0 128 127.0.0.1:5010 0.0.0.0:* users:(("python3",pid=1234,fd=6))
    只认**本地地址列**正好是该端口的 LISTEN 行 —— 对端地址里出现同端口不算。
    """
    pids = set()
    for line in out.splitlines():
        cols = line.split()
        if len(cols) < 5 or cols[0] != "LISTEN":
            continue
        if not cols[3].endswith(":%d" % port):
            continue
        pids.update(int(m) for m in re.findall(r"\bpid=(\d+)", line))
    return pids


def _parse_netstat_posix(out, port):
    """`netstat -tlnp` (net-tools) 的版式:
        tcp        0      0 127.0.0.1:5010      0.0.0.0:*         LISTEN      1234/python3
    列序 Proto/Recv-Q/Send-Q/Local/Foreign/State/PID-Program ⇒ 状态在第 6 列。
    """
    pids = set()
    for line in out.splitlines():
        cols = line.split()
        if len(cols) < 7 or not cols[0].startswith("tcp"):
            continue
        if cols[5] != "LISTEN" or not cols[3].endswith(":%d" % port):
            continue
        m = re.match(r"(\d+)/", cols[6])
        if m:
            pids.add(int(m.group(1)))
    return pids


def _parse_ps(out, needle):
    """`ps -eo pid=,ppid=,args=` 的行 → {pid: ppid},只留命令行含 needle 的。

    带上 ppid 的理由与 Windows 侧一致:uv 建的 venv 里解释器可能是 trampoline,
    一个逻辑实例对应父子两个 PID,只数 PID 会把 1 份报成 2 份。
    """
    procs = {}
    for line in out.splitlines():
        cols = line.split(None, 2)
        if len(cols) < 3:
            continue
        try:
            pid, ppid = int(cols[0]), int(cols[1])
        except ValueError:
            continue
        args = cols[2]
        if needle not in args or args.startswith("ps "):
            continue
        procs[pid] = ppid
    return procs


def _procs_by_cmdline(needle):
    """按命令行找 python 进程,返回 {pid: ppid}。**必须用这个**,否则游离实例看不见。

    uv 建的 venv 里 `python.exe` 是 trampoline,会再拉起真正的解释器,
    于是**一个逻辑实例对应父子两个 PID**。带上 ppid 才能数清"几个实例"
    (否则会把 1 个实例误报成 2 个)。
    """
    if not IS_WIN:
        return _parse_ps(_run_stdout(["ps", "-eo", "pid=,ppid=,args="], 20), needle)
    # 注意:这里用字符串拼接而不是 % 或 .format——
    # 脚本里有 'python%'(会被 % 当成格式符)和 '{0},{1}'(会被 .format 吃掉)。
    script = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
              "Where-Object { $_.CommandLine -like '*" + needle + "*' } | "
              "ForEach-Object { '{0},{1}' -f $_.ProcessId, $_.ParentProcessId }")
    out = _run_stdout(["powershell", "-NoProfile", "-Command", script], 25)
    return {int(a): int(b) for a, b in re.findall(r"(\d+),(\d+)", out)}


def live_procs(case):
    """该 case 实时面的进程 {pid: ppid}(含不监听端口的游离实例)。

    **只按命令行认**:两 case 共用 5010,端口不归属单 case,若把端口 pid 加进来会把
    另一个 case 的进程算到自己头上(停时误杀)。游离实例不监听端口,也正需要靠命令行补。
    """
    return _procs_by_cmdline(CMD_NEEDLE[case])


def live_pids(case):
    return set(live_procs(case))


def _port_bound_by(case):
    """5010 上是否有**本 case** 的进程在听(两 case 共用端口,须两端交集才算)。"""
    return bool(set(live_pids(case)) & _pids_by_port(LIVE[case]))


def n_instances(procs):
    """逻辑实例数 = 匹配进程里"父进程不在集合内"的那些(把 trampoline 子进程折掉)。"""
    pids = set(procs)
    return len([p for p in pids if procs.get(p) not in pids])


def _http_json(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def probe(case):
    port = LIVE[case]
    procs = live_procs(case)
    info = {"case": case, "port": port, "name": LIVE_NAME[case], "pids": sorted(procs),
            "n_inst": n_instances(procs), "listening": _port_bound_by(case),
            "simulating": None, "detail": ""}
    if not info["listening"]:
        info["detail"] = "Port is not listening"
        return info
    if case == "case01":
        h = _http_json("http://127.0.0.1:%d/health" % port)
        if h:
            # finished / finish_reason 由实时插件报出,于是能分清三种状态:
            # 在推演 / 已跑完在保持 / 仅审阅(--review-only,只服务界面不推演)。
            reason = h.get("finish_reason", "")
            info["finish_reason"] = reason
            info["simulating"] = not h.get("finished", False)
            info["detail"] = "roles={} pending={} clients={} finished={}{}".format(
                h.get("roles"), h.get("pending"), h.get("clients"),
                h.get("finished"), ("(%s)" % reason) if reason else "")
        else:
            info["detail"] = "Port is listening, but /health is unavailable"
    else:
        g = _http_json("http://127.0.0.1:%d/api/goals" % port)
        if g:
            n = len(g.get("tendency") or {})
            info["simulating"] = bool(n)
            info["detail"] = "倾向数据 {} 个角色(--no-sim 时为 0 = 只是 Web 层)".format(n)
        else:
            info["detail"] = "Port is listening, but /api/goals is unavailable"
    return info


def _state(i):
    """一行状态。只报能从外部确证的东西:

    - case00(5010):用 /api/goals 的倾向角色数判断,可靠(--no-sim 时为 0);
    - case01(5010):用 /health 的 finished/finish_reason 分清
      "在推演" / "已跑完在保持" / "仅审阅(--review-only,不推演)"。
      (2026-09-19 加了 finished 字段;在那之前分不清"还在跑"和"跑完在保持"。)
    """
    if not i["listening"]:
        return "Not running"
    if i["case"] == "case00":
        return "Simulating" if i["simulating"] else "Listening (no simulation: --no-sim)"
    reason = i.get("finish_reason", "")
    if i["simulating"]:
        return "Simulating"
    if reason == "review_only":
        return "Review only (no simulation)"
    return "Listening (run completed)"


def status():
    print("Live services (only one may run at a time):")
    for case in ("case00", "case01"):
        i = probe(case)
        warn = "  [!] {} instances (including detached)".format(i["n_inst"]) if i["n_inst"] > 1 else ""
        print("  {:<6} :{:<5} {:<22} {}{}".format(case, i["port"], _state(i), i["detail"], warn))
    print("Read-only services (no simulation):")
    for port, what in sorted(READONLY.items()):
        pids = sorted(_pids_by_port(port))
        print("  :{:<5} {:<24} {}".format(port, what,
              "listening pid={}".format(pids[0]) if pids else "down"))
    print("\nPlatform entry point: http://<host>:5010/ (the selected case is served here)")
    print("  case00: home = town + governance panel + intervention timeline | scene /embed/scene | governance /embed/goals")
    print("  case01: home = town + results | results /embed/review | scene /embed/scene")


# ---------------------------------------------------------------------------
# 停 / 起
# ---------------------------------------------------------------------------
def _terminate(pid, force=False):
    """停一个进程:Windows 走 `taskkill /F`(原本就是强杀),POSIX 走 SIGTERM/SIGKILL。

    POSIX 侧不能照抄 `/F`:直接 SIGKILL 会让 uvicorn 没机会收尾,而 5010 在跑批时
    还要把当前那局的记录落完 —— 先 SIGTERM,`stop()` 等不到才升级(见调用处)。
    进程已经自己退了(竞态)不算错,`ProcessLookupError` 直接吞。
    """
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=20)
        else:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass


def stop(case, quiet=False):
    pids = live_pids(case)
    if not pids:
        if not quiet:
            print("  {} :{} was not running".format(case, LIVE[case]))
        return 0
    for pid in pids:
        _terminate(pid)
    # 等端口真的释放(否则紧接着的 --start 会绑不上)
    for _ in range(20):
        time.sleep(0.5)
        if not live_pids(case):
            break
    else:
        # SIGTERM 没收回来的(卡在退出路径上),再强一次 —— 对齐 Windows 侧 taskkill /F
        for pid in sorted(live_pids(case)):
            _terminate(pid, force=True)
    if not quiet:
        print("  Stopped {} :{} (pid={})".format(case, LIVE[case], ",".join(map(str, sorted(pids)))))
    return len(pids)


def _warn_overwrite(explicit_run_id, run_id, out_rel):
    """沿用同一个 `--run-id` 起面会把上一条记录**原地盖掉**(raw 与成品都是覆盖写,不报错)。

    演示日这条最容易踩:彩排那条若与现场那条同名,彩排的产物就在跑完那一刻没了,而界面上
    看不出任何异常(2026-10-07 实测被坑:拿"文件在不在"当"跑完没跑完"的判据,读到的正是上一局
    的旧文件,于是紧接着的 `--stop` 把自己那局杀在 20 秒处)。只在**显式给了 run_id** 时提示——
    自动现铸的名字按设计就不会撞,报了只是噪音。
    """
    if not explicit_run_id:
        return
    for p in (os.path.join(HERE, out_rel),
              os.path.join(records_root(), run_id, "run.json")):
        if os.path.isfile(p):
            print("  Warning: {} already exists. Reusing run_id={} will overwrite that record. "
                  "Choose another --run-id or move the {} directory first.".format(
                      p, run_id, os.path.dirname(p)))
            return


def _start_mapper(run_id, args, raw_out):
    """【已停用】原来看护进程的事,现在由实时面进程自己顺序做。

    保留函数只为说明历史:`case01/vizkit/live_run.py` 的 `_map_run()` 在跑完当刻
    顺序映射(重开一局也映射),不再另起进程 —— 否则连点"重开"会堆起多个映射
    同时抢同一个 Ollama(2026-09-19 实测)。
    `case01/tools/map_after_run.py` 仍留着,供手工/离线映射用。
    """
    return ""


def start(case, args):
    other = "case01" if case == "case00" else "case00"
    review_only = bool(getattr(args, "review_only", False))
    # 绑非本机必须先声明,而且要**先于停旧实例**判断:否则用户敲错一个 host,
    # 旧服务先被杀掉、然后才报"拒绝绑定",等于把在跑的面弄没了(安全体检 2026-09-23)。
    from live.netguard import require_explicit_remote
    require_explicit_remote(args.host, where="5010 live service")
    if review_only:
        print("Review-only mode (--review-only): serving the interface without running a simulation.")
    else:
        print("Only one live service may run at a time. Stopping the other case and older instances:")
    # 两 case **共用 5010(唯一入口)**:无论对方是推演还是仅审阅,都得先停——
    # 否则对方占着 5010,新进程绑不上端口(而 injector 游离实例会照跑推演,
    # 外面却看不出第二份存在)。
    stop(other)
    stop(case)
    if live_pids(case) or live_pids(other):
        print("  [!] Live processes remain. Resolve them before starting another instance:")
        for c in ("case00", "case01"):
            print("    {}: {}".format(c, sorted(live_pids(c))))
        return 1

    # 绑非本机地址必须显式声明(安全体检 2026-09-23):5010 有 3 个写端点、全栈无鉴权。
    # (上面 start() 里已经判过,这里再判一次只是防有人直接调这个函数。)
    from live.netguard import exposure_lines, require_explicit_remote
    require_explicit_remote(args.host, where="5010 live service")

    os.makedirs(LOG_DIR, exist_ok=True)
    run_id, out = "", ""
    if case == "case01":
        if review_only:
            cmd = [sys.executable, "-m", "case01.vizkit.live_run", "--review-only",
                   "--host", args.host,
                   "--port", str(LIVE["case01"])]
            run_id = ""
        else:
            # 每跑一次就该有一个记录,而且名字带**真实日期时刻**:这里统一生成,
            # 落盘路径也按它派生,并把后续映射命令打出来——免得手抄长命令时把名字写岔
            # (名字写岔会让 5002 与结果面板对同一条记录显示两个名字,实测踩过)。
            # 分支要等 T0 跑完才判出来,名字里就不该写一个假的分支字母(用 auto);
            # 重开一局时循环里现铸的名字也走同一套口径(见 live_run.py)。
            run_id = args.run_id or live_run_id("auto")
            # 2026-10-08:原始记录的家挪到仓根 `data/case01/raw/`(搬迁期新优先、老回落并出声)。
            out = args.out or os.path.join(data_root("case01.raw"), run_id, "raw.json")
            _warn_overwrite(args.run_id, run_id, out)
            cmd = [sys.executable, "-m", "case01.vizkit.live_run",
                   "--host", args.host,
                   "--port", str(LIVE["case01"]), "--hold", str(args.hold),
                   "--run-id", run_id, "--out", out]
            if str(getattr(args, "seed", "") or "").strip():
                cmd += ["--seed", str(args.seed).strip()]
            if args.nodes:
                cmd += ["--nodes", str(args.nodes)]
            # --no-map 必须**传给子进程**(2026-10-06 修):此前它只影响下面那句提示,
            # 子进程仍照默认自动映射 —— 选项写着"不自动映射",实际照映射。
            if args.no_map:
                cmd += ["--no-map"]
    else:
        if str(getattr(args, "seed", "") or "").strip():
            # 对外口径是"只有一个种子",所以种子**接不住的那一面**必须自己说清楚,
            # 不能让人以为 case00 也被固定了(help 里写的是 `case01:`)。
            print("  Warning: --seed applies only to case01; case00 remains nondeterministic.")
        cmd = [sys.executable, "live_fastapi.py", "--name", args.name, "--start", args.sim_start,
               "--stride", str(args.stride), "--port", str(LIVE["case00"]),
               "--host", args.host]
        cmd += ["--no-sim"] if args.no_sim else ["--step", "0"]

    log = os.path.join(LOG_DIR, "live_%s.out" % case)
    err = os.path.join(LOG_DIR, "live_%s.err" % case)
    # 输出重定向到文件,不用管道(管道会随父进程退出把子进程带走)。
    # **启动前截断**(2026-10-06 修):此前是 `"ab"` 追加,而下面报错时读文件尾部 ——
    # 于是**任何一次历史 bind 冲突都会永久显示成"当次失败原因"**(本机 err 当时全文就一行旧 10048),
    # 让人以为这次也失败了。旧日志不是当次证据。与 `tools/serve_all.py` 的口径一致。
    with open(log, "wb") as fo, open(err, "wb") as fe:
        # POSIX 用 `start_new_session`(与 tools/serve_all.py 已验的写法同口径):不加这句,
        # 子进程留在本开关的进程组里,开关退出后终端的一次 Ctrl+C 会连带把 5010 打死 ——
        # 而 5010 的设计是"跑完还在保持,供人翻记录"。Windows 侧走 DETACHED_PROCESS。
        kwargs = {} if IS_WIN else {"start_new_session": True, "stdin": subprocess.DEVNULL}
        # 子进程必须**用 UTF-8 写 stdout**:Windows 上 Python 默认按 GBK 写,
        # 于是 live_*.out 落的是 GBK,而 --follow 按 UTF-8 读 ⇒ 满屏乱码
        # (用户 2026-10-10 贴的 "[case01.llm] !! �˵�ܾ�" 就是这个)。
        # 只影响日志编码,不改任何行为。
        child_env = dict(os.environ)
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env.setdefault("PYTHONUTF8", "1")
        # 留着句柄:_follow 要用它判断"服务是不是已经退出去了"
        # (2026-10-10:原先丢弃返回值,于是服务崩了之后跟随循环会一直静静挂着)
        child_proc = subprocess.Popen(
            cmd, cwd=HERE, stdout=fo, stderr=fe, env=child_env,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0), **kwargs)
    print("  Started {} :{} -> {}".format(case, LIVE[case], " ".join(cmd)))
    print("  Log: {}".format(log))
    if args.host not in ("127.0.0.1", "localhost", ""):
        # 跨机对接:把所有候选地址印出来(别只印路由那一个,也别让人手抄)
        for line in exposure_lines(args.host, LIVE[case]):
            print("  " + line)
        ips = _all_lan_ips()
        print("  Bound to {}. Addresses reachable from other hosts (non-loopback IPv4):".format(args.host))
        if ips:
            for i, ip in enumerate(ips):
                tail = "   <- default route" if i == 0 else ""
                print("    http://{}:{}/{}{}".format(
                    ip, LIVE[case], tail, _reachable(ip, LIVE[case])))
        else:
            print("    (No address detected; inspect with {})".format(
                "ipconfig" if IS_WIN else "ip addr"))
        print("  If other hosts cannot connect, check the firewall ({}).".format(
            "Windows may classify a new network as Public" if IS_WIN
            else "check `ufw status`/firewalld and any container port mapping"))
    mapper_log = ""
    if run_id:
        print("  Run ID: {} (ID date is the actual run time; record dates are simulation dates)".format(run_id))
        print("  Raw record path: {}".format(out))
        final = os.path.join(records_root(), run_id, "run.json")
        print("  Final record will be generated at: {}".format(final))
        print("    (Equivalent manual command: python -m case01.injector.pipeline --run-id {}"
              " --from-record {} --reflect --out {})".format(run_id, out, final))

    # 校验:进程活着 **且端口真绑上了**。只等端口不够——绑不上时进程还会继续跑模拟。
    bound = False
    for _ in range(24):
        time.sleep(1.5)
        if probe(case)["listening"]:
            bound = True
            break
    if not bound:
        # 探活不可靠时**不照着它杀服务**(2026-10-06 修,第三方只读复核的两条合并):
        #   ① 判据(命令行 PID ∩ netstat PID)在 uv trampoline → conda python 这条链上偏脆,
        #      真判不出来时,自动 `stop` 会把一个**真在跑**的服务收掉 —— 探针 bug 升级成事故;
        #   ② 失败信息要可诊断:把"两侧各看到什么"打出来,而不是只给一句"没绑上"。
        ours = live_pids(case)
        port_pids = _pids_by_port(LIVE[case])
        print("\n  [FAIL] Service started but could not bind its port (another process may own it).")
        print("     Check: {} case processes / :{} has {} listening PIDs / {} in common".format(
            len(ours), LIVE[case], len(port_pids), len(set(ours) & port_pids)))
        if port_pids:
            print("     Warning: :{} is listening (PID {}), but ownership is unclear; leaving it running.".format(
                LIVE[case], ", ".join(str(x) for x in sorted(port_pids))))
            print("        Check HTTP first: {} -s http://127.0.0.1:{}/health".format(
                "curl.exe" if IS_WIN else "curl", LIVE[case]))
            print("        To stop it: `python live_switch.py --stop {}` or {}".format(
                case, "Stop-Process -Id <pid>" if IS_WIN else "kill <pid>"))
        else:
            print("     Stopping this detached instance to prevent a second hidden simulation...")
            stop(case, quiet=True)
        tail = ""
        try:
            with open(err, "r", encoding="utf-8", errors="replace") as f:
                tail = f.read()[-600:]
        except Exception:  # noqa: BLE001
            pass
        if tail.strip():
            print("    Recent stderr:\n" + tail.strip())
        return 1

    if run_id and not args.no_map:
        # 映射现在由实时面进程**自己顺序做**(case01/vizkit/live_run.py 的 _map_run):
        # 重开一局时再另起看护进程会并发抢同一个 Ollama。这里只提示一句。
        print("  The live service will map the record after the run, including restarted runs.")

    print("\nCurrent status:")
    for c in ("case00", "case01"):
        j = probe(c)
        extra = "  instances={}".format(j["n_inst"]) if j["n_inst"] else ""
        print("  {:<6} :{:<5} {}{}".format(c, j["port"], _state(j), extra))
    print("\nPlatform entry point: http://<host>:5010/ (the selected case is served here)")
    if not args.no_follow:
        # 2026-10-10 用户反馈"都按任意键继续了,为什么后续还会有日志打印出来":
        # 服务是 DETACHED 起的、输出重定向到日志文件,所以**启动器退出后窗口就是死壳**,
        # 而日志还在那个文件里继续长 —— 设计如此,但没告诉人在哪儿看。
        # 这里默认跟读日志(像 tail -f);Ctrl+C 只停跟随,**不杀服务**。
        print("\n---- Following log (Ctrl+C stops following; the service keeps running) ----")
        try:
            _follow(log, err, child_proc)
        except KeyboardInterrupt:
            print("\n[Stopped following] Service is still running: `python live_switch.py --stop {}`".format(case))
    return 0


# 这些行在窗口里**必须扎眼** —— 服务进程自己的 stdout 是重定向进日志文件的
# (不是终端),所以染色只能做在"跟随日志读出来再写控制台"这一环
# (2026-10-10 用户要求"报错那行染红")。
_HOT = re.compile(
    r"(运行失败|服务已退出|Run failed|Service exited|"
    r"Traceback \(most recent call last\)|"
    r"\[case01\.llm\] !!|\[case01\] \[warn\]|\[FAIL\]|\bERROR\b)")


def _highlight(chunk):
    """给错误行染红。**不是终端就不染** —— 免得把 ANSI 写进别的地方。"""
    try:
        if not sys.stdout.isatty():
            return chunk
    except Exception:  # noqa: BLE001
        return chunk
    out = []
    for line in chunk.splitlines(True):
        if _HOT.search(line):
            out.append("\x1b[1;31m" + line.rstrip("\n") + "\x1b[0m\n")
        else:
            out.append(line)
    return "".join(out)


def _follow(log, err, child=None, interval=1.0):
    """跟读实时面日志。文件还没生成就先等;被截断/轮转则重开。

    `child` 是实时面那个子进程的句柄。**它退出时要说一句再收工** ——
    否则服务崩了之后日志文件还在,这个循环会一直静静地挂着,窗口显示完错误文本
    就再无动静,看的人不知道"是崩了还是在跑"(2026-10-10 用户要求补这个缺口)。
    """
    import time as _t
    # 日志是 UTF-8,Windows 控制台默认 GBK —— 直接 write 会把中文打成乱码
    # (2026-10-10 用户贴的日志全是"�?)。先把 stdout 切到 UTF-8 并容错。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 老 Python/被重定向时跳过即可
            pass
    handles, pos = {}, {}
    for path in (log, err):
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
            handles[path] = fh
            pos[path] = fh.seek(0, 2)          # 从末尾开始,不打已经写过的历史
        except Exception:  # noqa: BLE001 - 文件还没生成就等下一轮
            pass
    while True:
        _t.sleep(interval)
        if child is not None and child.poll() is not None:
            # 把可能还没读到的最后一段刷出来,再说"退出了",然后收工。
            for path, fh in list(handles.items()):
                try:
                    fh.seek(pos[path])
                    tail = fh.read()
                    if tail:
                        pos[path] = fh.tell()
                        sys.stdout.write(_highlight(tail))
                        sys.stdout.flush()
                except Exception:  # noqa: BLE001
                    pass
            sys.stdout.write(_highlight(
                "Service exited (return code {}): see the last few red lines above; "
                "this run either failed to start or has finished.\n".format(child.returncode)))
            sys.stdout.flush()
            return
        for path, fh in list(handles.items()):
            try:
                fh.seek(pos[path])
                chunk = fh.read()
                if chunk:
                    pos[path] = fh.tell()
                    sys.stdout.write(_highlight(chunk))
                    sys.stdout.flush()
            except Exception:  # noqa: BLE001
                try:
                    fh.close()
                except Exception:
                    pass
                handles.pop(path, None)
        for path in (log, err):
            if path not in handles and os.path.exists(path):
                try:
                    fh = open(path, "r", encoding="utf-8", errors="replace")
                    handles[path] = fh
                    pos[path] = fh.seek(0, 2)
                except Exception:  # noqa: BLE001
                    pass


def _lan_ip() -> str:
    """**默认路由会用的**本机地址。做法:UDP socket 连一个不可路由地址,再读
    `getsockname()` —— 只问内核路由表,一个包都不发(不会真的去连 10.255.255.255)。
    注意它只给出"出门走哪张网卡"的那一个地址,机器上其它地址它看不见。
    """
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def _reachable(ip: str, port: int) -> str:
    """本机自测:这个地址的这个端口,从本机连得上吗。

    网卡没插/没连上、或被防火墙拦,都会不通 —— 所以不能只把地址列出来就当成"能用"。
    """
    import socket

    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((ip, port))
        return " · 本机自测通"
    except OSError:
        return " · 本机自测不通(网卡没连上或被防火墙拦)"
    finally:
        s.close()


def _all_lan_ips() -> list:
    """本机所有非回环 IPv4,**尽力而为**;第一个是默认路由那个。

    为什么不能只印一个:stdlib 的 `getaddrinfo(gethostname())` 与 UDP 技巧都只给
    "主机名/路由表对应的那一个"。实测这台机器上有两个可用地址
    (WLAN 的 10.195.104.175、以太网手配的 116.56.156.64),只印路由那个,可能刚好
    不是平台侧能到的那一个。Windows 上用 `Get-NetIPAddress` 补齐;取不到就只回一个,
    绝不因为"列不全"而报错。
    """
    ips = []
    routed = _lan_ip()
    if routed:
        ips.append(routed)
    # 列不全只影响提示,不该让起服务失败:helper 出错就回空串/空表,循环自然跳过
    if IS_WIN:
        raw = _run_stdout(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-NetIPAddress -AddressFamily IPv4).IPAddress"], 10).splitlines()
        cands = [s.strip() for s in raw]
    else:
        # Linux/macOS(iproute2):`2: eth0 inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0`
        # ⇒ 第 3 列是 inet、第 4 列是地址,去掉 /掩码。没装 iproute2 时退 `hostname -I`。
        cands = []
        for cols in (l.split() for l in _run_stdout(["ip", "-4", "-o", "addr", "show"], 10).splitlines()):
            if len(cols) >= 4 and cols[2] == "inet":
                cands.append(cols[3].split("/")[0])
        if not cands:
            cands = _run_stdout(["hostname", "-I"], 10).split()
    for ip in cands:
        ip = ip.strip()
        if ip and ip not in ips and not ip.startswith(("127.", "169.254.")):
            ips.append(ip)
    return ips


def main():
    # 本文件满屏是 ⚠/▶ 这类 GBK 编不了的字,而 Windows 控制台默认就是 cp936:
    # 不加这句,**预警本身会把开关打死** —— `--start case01 --run-id <已有名字>` 的
    # 覆盖写预警发生在 `stop(other)`/`stop(case)` 之后,抛 UnicodeEncodeError 就等于
    # 把两个实时面都停了却没起新的(正是本文件要防的"先弄没在跑的面")。
    tolerant_stdout()
    missing = _attribution_tools_missing()
    if missing:
        # 先出声再干活:否则 --start 会按"没在跑"的假前提往下走(见 _attribution_tools_missing)
        print("[live_switch] Warning: missing {}. Any 'down' status may mean process discovery failed. "
              "Install the required tools: apt-get install iproute2 net-tools procps".format(", ".join(missing)))
    ap = argparse.ArgumentParser(description="Live service switch: allow only one simulation interface at a time")
    ap.add_argument("--status", action="store_true", help="Show current status without changing processes")
    ap.add_argument("--start", choices=["case00", "case01"], help="Start a case after stopping the other case and older instances")
    ap.add_argument("--stop", choices=["case00", "case01", "all"], help="Stop live services without affecting read-only services")
    ap.add_argument("--nodes", type=int, default=0, help="case01: run the first N nodes (0 = all)")
    # 平台侧对接要用:默认只绑本机;给 0.0.0.0(或 LIVE_HOST=0.0.0.0)才允许跨机访问。
    ap.add_argument("--host", default=os.environ.get("LIVE_HOST", "127.0.0.1"),
                    help="Bind address; default 127.0.0.1. Use 0.0.0.0 for access from another host")
    ap.add_argument("--hold", type=float, default=1800, help="case01: seconds to keep the service running after the simulation")
    ap.add_argument("--out", default="", help="case01: raw record path (default: data/case01/raw/<run_id>/raw.json)")
    ap.add_argument("--run-id", default="", help="case01: explicit run ID (default: <YYMMDD>-live-case01-mavis-<branch>-<HHMM>)")
    ap.add_argument("--seed", default="",
                    help="case01: integer seed for model sampling and town dynamics; also accepts CASE01_SEED")
    ap.add_argument("--name", default="demo", help="case00: simulation name")
    ap.add_argument("--sim-start", dest="sim_start", default="20250213-09:30",
                    help="case00: simulation start time")
    ap.add_argument("--stride", type=int, default=2, help="case00: time step in minutes")
    ap.add_argument("--no-sim", action="store_true",
                    help="case00: serve the web interface without running a simulation")
    ap.add_argument("--no-follow", dest="no_follow", action="store_true",
                    help="Do not follow the log after startup (default: follow; Ctrl+C only stops following)")
    ap.add_argument("--no-map", dest="no_map", action="store_true",
                    help="case01: skip automatic final-record generation")
    ap.add_argument("--review-only", dest="review_only", action="store_true",
                    help="case01: serve the town and results interface without running a simulation")
    args = ap.parse_args()

    if args.start:
        return start(args.start, args)
    if args.stop:
        if args.stop == "all":
            for c in ("case00", "case01"):
                stop(c)
        else:
            stop(args.stop)
        print()
    status()
    return 0


if __name__ == "__main__":
    from live.english_output import install_english_output
    install_english_output()
    sys.exit(main())
