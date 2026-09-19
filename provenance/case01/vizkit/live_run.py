# -*- coding: utf-8 -*-
"""厚 CLI:把 case01 的注入驱动与 mavis-vizkit 的实时可视化插件接起来,**一个界面**。

可视化实现已抽到独立包 mavis-vizkit;本命令只负责把 case01 侧的
roles / alias / scenario / 前端资源根 作为参数喂给它,并把 case01 自己的
**结果面板**(九块:概览/对话/检索/事件/状态/反思/问题分流/注入器/审计)
挂到**同一个服务**上——页面右栏里是"结果记录"卡片(与 Chat Log 同款,可折叠)。
(独立的 5004 面板服务因此退役,见 case01/review_app.py 的 docstring。)

用法(在 provenance/provenance 下):
    python -m case01.vizkit.live_run --branch B --port 5010
    python -m case01.vizkit.live_run --review-only          # 不推演,只翻成品记录
然后浏览器打开 http://127.0.0.1:5010/ —— 左边小镇实时动,右栏是对话与"结果记录"卡片
(点卡片头部可收起/展开,点"单独打开 ↗"整页看)。
跑完后默认再保持 60 秒(--hold)方便观察,随后关闭服务。
跑完会广播一条 `done`:此后(含保持期)打开页面的人会看到"已结束",
不会被空小镇误导——**不允许静默**。

推演结束后,页面左上角状态块里会出现一个**"重开一局"小按钮**(用户要求:结束后决定要不要重开)。
点了就打 `POST /control/restart`:本进程不等保持期结束,立刻用同样设置开新的一局
(run_id 重新铸、新 raw.json、自动起本局的映射看护),页面自己刷新。
`--no-restart` 可以关掉这个按钮。
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time

import mavis_vizkit
from ..injector.bridge import DEFAULT_ROLES, MavisBridge
from ..injector.nodes import default_nodes
from ..run_naming import live_run_id

# case01 业务侧的角色贴图别名与前端根(属于 case01,不属于 mavis-vizkit)
ROLE_TEXTURE_ALIAS = {"Investment AI": "AI Advisor", "Ethan Lin": "Mr. Zhou"}
PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FRONTEND = os.path.join(PKG_ROOT, "frontend")
SCENARIO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "injector", "scenario")


def build_service(host="127.0.0.1", port=5010, roles=None, run_id="",
                  scenario_dir="", with_review=True, on_restart=None):
    """建**一个**服务:实时小镇 + case01 结果面板(九块)。

    为什么要挂在一起(2026-09-19 用户拍板"只维护一个界面"):
    5010 原本只有小镇(过程),结果九块在另一个端口的面板服务(5004)上,
    于是人要看全貌得开两个地址。现在九块作为**右栏里一张可折叠的卡片**
    (与 Chat Log 同款)嵌在同一个页面上,小镇照旧在旁边实时动。

    分工:小镇与"卡片"由 mavis-vizkit 提供(它不认识 case01 的字段,只提供
    extra_panels 这个通用口子);九块由 case01 侧挂上去。5004 那个独立服务随之退役。
    """
    roles = list(roles or DEFAULT_ROLES)
    live = mavis_vizkit.create(
        "live", host=host, port=port, roles=roles,
        alias=ROLE_TEXTURE_ALIAS, scenario_dir=scenario_dir or SCENARIO,
        static_root=os.path.join(_FRONTEND, "static"),
        template_dir=os.path.join(_FRONTEND, "templates"),
        # ?embed=1:卡片里的 iframe 用压缩版式(去掉大标题与页边距),贴合 380px 宽的卡片
        extra_panels=[{"id": "review", "label": "结果记录",
                       "url": "/review?embed=1"}],
        on_restart=on_restart)
    if with_review:
        from ..review_app import attach_to
        # "本次实跑还没成品记录"的提示就靠这个:实跑跑完自动映射之后,下拉里才会出现它。
        attach_to(live.app, current_run_id=run_id,
                  note="实跑跑完会自动映射成成品记录")
    return live


def _spawn_mapper(run_id, branch, raw_out):
    """给这一局的原始记录起一个脱离的看护进程(和 live_switch 用的是同一个工具)。

    为什么要它:重开一局之后,`live_switch` 当初只给**第一局**起了看护进程,
    不补的话第二局跑完就没有成品记录 —— 面板上"这片空白没人解释"的老问题会回来。
    """
    log_dir = os.path.join(os.environ.get("TEMP", os.path.dirname(raw_out)), "dsh_srv")
    os.makedirs(log_dir, exist_ok=True)
    log = os.path.join(log_dir, "live_case01.map.log")
    cmd = [sys.executable, "-m", "case01.tools.map_after_run",
           "--run-id", run_id, "--branch", branch,
           "--raw", os.path.abspath(raw_out), "--port", "0"]
    try:
        with open(log, "ab") as f:
            subprocess.Popen(cmd, cwd=PKG_ROOT, stdout=f, stderr=f,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        print("  看护进程已起(本局映射): {}".format(run_id))
    except Exception as exc:  # noqa: BLE001 - 起不来要说清楚
        print("  [!] 本局的映射看护进程没起来({}: {}),跑完请手工映射".format(
            type(exc).__name__, exc))


def main(argv=None):
    ap = argparse.ArgumentParser(description="case01 单一界面:实时小镇 + 结果记录")
    ap.add_argument("--branch", default="B", choices=["A", "B", "C"])
    ap.add_argument("--roles", default=",".join(DEFAULT_ROLES))
    ap.add_argument("--scenario-dir", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--port", type=int, default=5010)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--max-retries", type=int, default=3)
    ap.add_argument("--hold", type=float, default=60.0, help="运行结束后保持服务的秒数")
    ap.add_argument("--nodes", type=int, default=0, help="只跑前 N 个节点(0=全部;冒烟用)")
    ap.add_argument("--out", default="", help="可选:同时保存运行记录 JSON")
    ap.add_argument("--review-only", dest="review_only", action="store_true",
                    help="只服务界面(小镇 + 结果记录),不跑推演;用于随时翻成品记录")
    ap.add_argument("--no-restart", dest="no_restart", action="store_true",
                    help="不提供页面上的『重开一局』按钮(默认提供)")
    args = ap.parse_args(argv)

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    if len(roles) != 2:
        print("--roles 需要正好两个角色名")
        return 2

    scenario = args.scenario_dir or SCENARIO
    # 页面上的"重开一局"按钮(用户要求:推演结束后决定要不要重开)。
    # 回调只做一件事:把重开请求记下来;真正的重开由下面的主循环执行
    # (不能在回调里直接跑推演,那是 HTTP 线程池的活)。
    restart_flag = threading.Event()

    def request_restart():
        restart_flag.set()
        print("收到重开请求:当前这一局跑完(或保持期结束)后立刻开新的一局")
        return {"ok": True, "detail": "已受理:这一局结束后自动开新的一局"}

    live = build_service(host=args.host, port=args.port, roles=roles,
                         run_id=args.run_id, scenario_dir=scenario,
                         on_restart=None if args.no_restart else request_restart)
    live.start()
    print("界面已启动: {}  (小镇 + 右栏“结果记录”卡片;Ctrl+C 结束)".format(live.url()))

    if args.review_only:
        # 不推演,只服务界面。告诉页面"没有在推演",别让人对着空小镇猜
        # (状态点会显示成"仅审阅模式")。这样它就是个可以随时开着的只读面。
        live.finish("review_only")
        print("仅审阅模式:没有在推演,小镇为空;右栏“结果记录”卡片可翻 case01/runs/")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("已停止")
            live.close()
            return 0

    from ..review_app import clear_live, set_current_run, set_live_provider

    exit_code = 0
    ran_once = False
    try:
        while True:
            restart_flag.clear()
            nodes = default_nodes(args.branch, roles=list(roles))
            if args.nodes > 0:
                nodes = nodes[:args.nodes]
            # 每跑一次就该有一个能唯一定位的名字:**日期在最前**(便于按时间排序)、
            # 带 case 与引擎、实跑再带 HHMM(同一天跑多次也不撞)。名字里的日期是
            # **真实运行时间**;记录里的 start_date/end_date 是模拟剧情日期,不是一回事。
            run_id = args.run_id if (args.run_id and not ran_once) else live_run_id(args.branch)
            ran_once = True
            out = args.out or os.path.join("case01", "runs_injector", run_id, "raw.json")
            print("本次 run_id: {}".format(run_id))
            # 面板是建服务时挂上去的,那时 run_id 还没生成;这里补告一次,
            # 好让面板能写出"本次实跑 <run_id> 还没成品记录(跑完自动生成)"。
            set_current_run(run_id, "实跑跑完会自动映射成成品记录")
            bridge = MavisBridge(
                nodes=nodes, roles=roles, scenario_dir=scenario,
                run_id=run_id,
                dry_run=False, max_retries=args.max_retries, branch=args.branch,
                visualizers=[live],
            )
            # 实时结果:面板每 2 秒来取一次"到目前为止的记录",用的映射与成品记录同一套,
            # 于是小镇一边动、右栏的对话/检索/事件/状态/审计一边长出来(用户要的"同步看全程")。
            # 跑完不立刻撤:成品记录还要等自动映射(~1-2 分钟),这期间面板继续显示完整过程。
            set_live_provider(lambda: bridge.run_record(), run_id=run_id,
                              total_nodes=len(nodes))
            try:
                record = bridge.run()
                print("运行完成:", json.dumps(record.get("summary") or {}, ensure_ascii=False))
                bridge.save(out)
                print("记录已保存 ->", out)
                # 跑完必须**明确告诉页面**(而不是留着服务静悄悄):此后连进来的人
                # 会收到 done,知道"推演结束、服务只是在保持",不会以为可视化坏了。
                live.finish("run_finished")
                _spawn_mapper(run_id, args.branch, out)
                if args.hold > 0:
                    print("推演已结束,服务保持 {:.0f} 秒(此刻页面显示“已结束”,"
                          "并给出“重开一局”按钮)…".format(args.hold))
                    # 保持期内**等重开请求**:收到就立刻开新的一局,不用等满
                    if restart_flag.wait(timeout=args.hold):
                        print("保持期内收到重开请求,立即开新的一局")
            except KeyboardInterrupt:
                print("已中断")
                live.finish("interrupted")
                exit_code = 130
                break
            except Exception as exc:  # noqa: BLE001 - 页面也必须看到失败
                print("运行失败: {}: {}".format(type(exc).__name__, exc))
                live.finish("error:{}".format(type(exc).__name__))
                exit_code = 1
                break
            finally:
                bridge.close()
            if not restart_flag.is_set():
                break
            print("重开:开始新的一局(分支 {} 不变)".format(args.branch))
    finally:
        clear_live()      # 进程要退出了,别让面板一直显示一个已经不动的"实时"
        print("服务已关闭")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())