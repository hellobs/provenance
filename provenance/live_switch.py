# -*- coding: utf-8 -*-
"""实时面开关:机械保证任何时刻只有**一个**实时可视化在跑。

为什么要它
----------
实时面有两个**实现**,属于两个不同的 case:

- case00(原初 6 角色投资咨询小镇):`live_fastapi.py`,端口 **5001**
- case01(2 角色、节点驱动的注入器推演):`case01/vizkit/live_run.py`,端口 **5010**

两个 case 各自需要实时面,但**任何时刻只允许起一个**:两者共用同一个本地 Ollama,
同时跑会把双方都拖慢,演示时屏幕上也不该有两套在动。

只读面(5002 契约 / 5003 case00 存档)**不跑模拟,不受此限**,本脚本不碰。
**case01 的 5010 现在同时是"单一界面"**(小镇 + 结果记录九块,`--review-only`
就是只翻结果不推演)——2026-09-19 起独立的 5004 面板服务已退役。

本脚本要防的两件事(都是实测踩过的)
---------------------------------
1. **同时起两个**:`--start` 会先停掉另一个 case 的实时面。
2. **同一个 case 起两份**:如果目标端口已被本 case 的旧实例占着,新进程会
   `[Errno 10048] 端口已被占用` **绑不上端口,却照样在后台跑推演**——于是变成两份在跑、
   而且外面完全看不出第二份存在。所以 `--start` 也要先停掉**本 case** 的旧实例,
   并在起完后**校验端口真的绑上了**;没绑上就把新进程收掉并报错,绝不留游离实例。

因此进程发现**不能只看端口**:游离实例不监听任何端口,只能按命令行认。
`--status` 会顺带报出匹配到的进程数,数量 >1 就是有游离实例。
状态还区分"在推演 / 已跑完在保持 / 仅审阅(`--review-only`)"——靠 5010 的
`/health` 里的 `finished`/`finish_reason`,不再靠猜。

给平台侧的口径:平台**只需要一个地址**——`http://<host>:5010/`
(case01 单一界面:实时小镇 + 右栏"结果记录"卡片)。只看结果就引
`http://<host>:5010/embed/review`,只看小镇 `http://<host>:5010/embed/scene`。
平台不必知道 case00 的 5001,也不必知道当前在跑哪个 case。

用法(在 `provenance/provenance` 下执行)
--------------------------------------
    python live_switch.py --status
    python live_switch.py --start case01 [--nodes 2] [--hold 1800] [--branch B] [--no-map]
    python live_switch.py --start case01 --review-only     # 不推演,只翻成品记录
    python live_switch.py --start case00 [--stride 2]
    python live_switch.py --stop case01 | case00 | all

`--start case01` **默认跑完自动映射**(看护进程等 `raw.json` 写好,再跑 pipeline 生成
`case01/runs/<run_id>/run.json`)。理由见"不允许静默"那条铁律:实跑成功了却没有成品记录,
面板上就是一片空白,而没人会知道为什么。`--no-map` 可关掉,手工映射的命令仍会打印出来。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from case01.run_naming import live_run_id

HERE = os.path.dirname(os.path.abspath(__file__))

LIVE = {"case00": 5001, "case01": 5010}
LIVE_NAME = {"case00": "原初 6 角色小镇(live_fastapi.py)",
             "case01": "注入器推演(vizkit town)"}
# 命令行特征:按它认进程(端口判断不到游离实例)
CMD_NEEDLE = {"case00": "live_fastapi.py", "case01": "vizkit.live_run"}
READONLY = {5002: "case01 只读契约", 5003: "case00 存档只读"}
LOG_DIR = os.path.join(os.environ.get("TEMP", HERE), "dsh_srv")


# ---------------------------------------------------------------------------
# 进程/端口发现
# ---------------------------------------------------------------------------
def _pids_by_port(port):
    """Windows:从 netstat 拿监听该端口的 PID(不依赖 psutil)。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return set()
    pat = re.compile(r"^\s*TCP\s+\S+:%d\s+\S+\s+LISTENING\s+(\d+)\s*$" % port, re.M)
    return {int(m) for m in pat.findall(out)}


def _procs_by_cmdline(needle):
    """按命令行找 python 进程,返回 {pid: ppid}。**必须用这个**,否则游离实例看不见。

    uv 建的 venv 里 `python.exe` 是 trampoline,会再拉起真正的解释器,
    于是**一个逻辑实例对应父子两个 PID**。带上 ppid 才能数清"几个实例"
    (否则会把 1 个实例误报成 2 个)。
    """
    # 注意:这里用字符串拼接而不是 % 或 .format——
    # 脚本里有 'python%'(会被 % 当成格式符)和 '{0},{1}'(会被 .format 吃掉)。
    script = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
              "Where-Object { $_.CommandLine -like '*" + needle + "*' } | "
              "ForEach-Object { '{0},{1}' -f $_.ProcessId, $_.ParentProcessId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, timeout=25).stdout
    except Exception:  # noqa: BLE001
        return {}
    return {int(a): int(b) for a, b in re.findall(r"(\d+),(\d+)", out)}


def live_procs(case):
    """该 case 实时面的进程 {pid: ppid}(含不监听端口的游离实例)。"""
    procs = _procs_by_cmdline(CMD_NEEDLE[case])
    for pid in _pids_by_port(LIVE[case]):
        procs.setdefault(pid, 0)
    return procs


def live_pids(case):
    return set(live_procs(case))


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
            "n_inst": n_instances(procs), "listening": bool(_pids_by_port(port)),
            "simulating": None, "detail": ""}
    if not info["listening"]:
        info["detail"] = "未监听端口"
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
            info["detail"] = "端口在听,但 /health 取不到"
    else:
        g = _http_json("http://127.0.0.1:%d/api/goals" % port)
        if g:
            n = len(g.get("tendency") or {})
            info["simulating"] = bool(n)
            info["detail"] = "倾向数据 {} 个角色(--no-sim 时为 0 = 只是 Web 层)".format(n)
        else:
            info["detail"] = "端口在听,但 /api/goals 取不到"
    return info


def _state(i):
    """一行状态。只报能从外部确证的东西:

    - case00(5001):用 /api/goals 的倾向角色数判断,可靠(--no-sim 时为 0);
    - case01(5010):用 /health 的 finished/finish_reason 分清
      "在推演" / "已跑完在保持" / "仅审阅(--review-only,不推演)"。
      (2026-09-19 加了 finished 字段;在那之前分不清"还在跑"和"跑完在保持"。)
    """
    if not i["listening"]:
        return "未启动"
    if i["case"] == "case00":
        return "在推演" if i["simulating"] else "在听(未在推演:--no-sim)"
    reason = i.get("finish_reason", "")
    if i["simulating"]:
        return "在推演"
    if reason == "review_only":
        return "仅审阅(未在推演)"
    return "在听(已跑完)"


def status():
    print("实时面(同时只允许一个在跑):")
    for case in ("case00", "case01"):
        i = probe(case)
        warn = "  [!] 有 {} 个实例(含游离)".format(i["n_inst"]) if i["n_inst"] > 1 else ""
        print("  {:<6} :{:<5} {:<22} {}{}".format(case, i["port"], _state(i), i["detail"], warn))
    print("只读面(不跑模拟,可随时全开):")
    for port, what in sorted(READONLY.items()):
        pids = sorted(_pids_by_port(port))
        print("  :{:<5} {:<24} {}".format(port, what,
              "listening pid={}".format(pids[0]) if pids else "down"))
    print("\n平台侧只需要一个地址: http://<host>:5010/ (case01 单一界面:小镇 + 结果记录)")
    print("  只看结果 http://<host>:5010/embed/review ｜ 只看小镇 http://<host>:5010/embed/scene")


# ---------------------------------------------------------------------------
# 停 / 起
# ---------------------------------------------------------------------------
def stop(case, quiet=False):
    pids = live_pids(case)
    if not pids:
        if not quiet:
            print("  {} :{} 本来就没在跑".format(case, LIVE[case]))
        return 0
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=20)
    # 等端口真的释放(否则紧接着的 --start 会绑不上)
    for _ in range(20):
        time.sleep(0.5)
        if not live_pids(case):
            break
    if not quiet:
        print("  已停 {} :{} (pid={})".format(case, LIVE[case], ",".join(map(str, sorted(pids)))))
    return len(pids)


def _is_review_only(case):
    """该 case 当前在听的那个实例是不是"仅审阅"(不推演)。

    靠 5010 的 /health(实时插件报 finished/finish_reason)判断,不靠猜命令行。
    """
    i = probe(case)
    return bool(i["listening"]) and not i["simulating"] \
        and i.get("finish_reason") == "review_only"


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
    if case == "case01" and review_only:
        # 仅审阅:不推演、不占 Ollama,所以**不必**停另一个 case 的实时面
        # (它俩不会互相拖慢)。只清本端口上的旧实例。
        print("仅审阅模式(--review-only):不跑推演,只服务界面;不会停 case00 的实时面。")
        stop(case)
    else:
        print("按「同时只允许一个实时面」的规则——先停另一个,再停本 case 的旧实例(防游离):")
        if case == "case00" and _is_review_only("case01"):
            # case01 只是"仅审阅"(不推演),不该被停掉:它不抢 Ollama,
            # 而且那是看结果记录的唯一界面。
            print("  case01 :5010 只是「仅审阅」(未在推演),保留不动。")
        else:
            stop(other)
        stop(case)
    if live_pids(case) or (not (case == "case01" and review_only) and live_pids(other)):
        print("  [!] 仍有实时进程没清干净,继续起会得到两个实例;先手工处理:")
        for c in ("case00", "case01"):
            print("    {}: {}".format(c, sorted(live_pids(c))))
        return 1

    os.makedirs(LOG_DIR, exist_ok=True)
    run_id, out = "", ""
    if case == "case01":
        if review_only:
            cmd = [sys.executable, "-m", "case01.vizkit.live_run", "--review-only",
                   "--port", str(LIVE["case01"])]
            run_id = ""
        else:
            # 每跑一次就该有一个记录,而且名字带**真实日期时刻**:这里统一生成,
            # 落盘路径也按它派生,并把后续映射命令打出来——免得手抄长命令时把名字写岔
            # (名字写岔会让 5002 与结果面板对同一条记录显示两个名字,实测踩过)。
            run_id = args.run_id or live_run_id(args.branch)
            out = args.out or os.path.join("case01", "runs_injector", run_id, "raw.json")
            cmd = [sys.executable, "-m", "case01.vizkit.live_run", "--branch", args.branch,
                   "--port", str(LIVE["case01"]), "--hold", str(args.hold),
                   "--run-id", run_id, "--out", out]
            if args.nodes:
                cmd += ["--nodes", str(args.nodes)]
    else:
        cmd = [sys.executable, "live_fastapi.py", "--name", args.name, "--start", args.sim_start,
               "--stride", str(args.stride), "--port", str(LIVE["case00"])]
        cmd += ["--no-sim"] if args.no_sim else ["--step", "0"]

    log = os.path.join(LOG_DIR, "live_%s.out" % case)
    err = os.path.join(LOG_DIR, "live_%s.err" % case)
    # 输出重定向到文件,不用管道(管道会随父进程退出把子进程带走)
    with open(log, "ab") as fo, open(err, "ab") as fe:
        subprocess.Popen(cmd, cwd=HERE, stdout=fo, stderr=fe,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    print("  已起 {} :{}  -> {}".format(case, LIVE[case], " ".join(cmd)))
    print("  日志:{}".format(log))
    mapper_log = ""
    if run_id:
        print("  本次 run_id: {}   (名字里的日期是真实运行时间,记录里的 start/end date 是模拟剧情日期)".format(run_id))
        print("  原始记录将落盘到: {}".format(out))
        final = os.path.join("case01", "runs", run_id, "run.json")
        print("  跑完自动映射成成品记录: {}".format(final))
        print("    (手动等价命令: python -m case01.injector.pipeline --branch {} --run-id {}"
              " --from-record {} --reflect --out {})".format(args.branch, run_id, out, final))

    # 校验:进程活着 **且端口真绑上了**。只等端口不够——绑不上时进程还会继续跑模拟。
    bound = False
    for _ in range(24):
        time.sleep(1.5)
        if probe(case)["listening"]:
            bound = True
            break
    if not bound:
        print("\n  [失败] 起来后端口没绑上(常见原因:端口被别的进程占着)。")
        print("    正在收掉这个游离实例,避免留下'看不见的第二个推演'……")
        stop(case, quiet=True)
        tail = ""
        try:
            with open(err, "r", encoding="utf-8", errors="replace") as f:
                tail = f.read()[-600:]
        except Exception:  # noqa: BLE001
            pass
        if tail.strip():
            print("    stderr 尾部:\n" + tail.strip())
        return 1

    if run_id and not args.no_map:
        # 映射现在由实时面进程**自己顺序做**(case01/vizkit/live_run.py 的 _map_run):
        # 重开一局时再另起看护进程会并发抢同一个 Ollama。这里只提示一句。
        print("  跑完由实时面自己顺序映射(重开一局也会映射),不发看护进程")

    print("\n现状:")
    for c in ("case00", "case01"):
        j = probe(c)
        extra = "  实例数={}".format(j["n_inst"]) if j["n_inst"] else ""
        print("  {:<6} :{:<5} {}{}".format(c, j["port"], _state(j), extra))
    print("\n平台入口(一个): http://<host>:5010/  ｜ 只看结果 .../embed/review ｜ 只看小镇 .../embed/scene")
    return 0


def main():
    ap = argparse.ArgumentParser(description="实时面开关:保证同时只有一个实时可视化在跑")
    ap.add_argument("--status", action="store_true", help="只看现状,不动任何进程")
    ap.add_argument("--start", choices=["case00", "case01"], help="起某个 case 的实时面(先停另一个与本 case 旧实例)")
    ap.add_argument("--stop", choices=["case00", "case01", "all"], help="停实时面(不碰只读面)")
    ap.add_argument("--nodes", type=int, default=0, help="case01:只跑前 N 个节点(0=全部)")
    ap.add_argument("--hold", type=float, default=1800, help="case01:跑完保持服务的秒数")
    ap.add_argument("--branch", default="B", help="case01:分支 A/B/C")
    ap.add_argument("--out", default="", help="case01:原始记录落盘路径(默认按 run_id 派生到 case01/runs_injector/<run_id>/raw.json)")
    ap.add_argument("--run-id", default="", help="case01:显式指定 run_id(默认 <YYMMDD>-live-case01-mavis-<分支>-<HHMM>)")
    ap.add_argument("--name", default="demo", help="case00:模拟名")
    ap.add_argument("--sim-start", dest="sim_start", default="20250213-09:30",
                    help="case00:模拟起始时间(与 --start 的 case 选择不是一回事)")
    ap.add_argument("--stride", type=int, default=2, help="case00:步长(分钟)")
    ap.add_argument("--no-sim", action="store_true",
                    help="case00:只服务 Web 层不跑模拟(不算实时面,但仍占端口)")
    ap.add_argument("--no-map", dest="no_map", action="store_true",
                    help="case01:跑完不自动映射成成品记录(默认自动映射)")
    ap.add_argument("--review-only", dest="review_only", action="store_true",
                    help="case01:不推演,只把界面(小镇 + 结果记录)起来;可随时开着翻记录")
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
    sys.exit(main())
