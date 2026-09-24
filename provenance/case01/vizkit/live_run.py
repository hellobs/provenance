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
from ..run_naming import live_run_id, unique_run_id

# case01 业务侧的角色贴图别名与前端根(属于 case01,不属于 mavis-vizkit)
ROLE_TEXTURE_ALIAS = {"Investment AI": "AI Advisor", "Ethan Lin": "Mr. Zhou"}
PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FRONTEND = os.path.join(PKG_ROOT, "frontend")
SCENARIO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "injector", "scenario")


def resolve_restart_choice(raw, current_branch="B"):
    """把 `POST /control/restart` 里的取值翻成 (分支, 模式);不认识的取值抛 ValueError。

    为什么单独一个函数而不是写死在回调里:取值口径要能被单测钉住(回调在 main() 的闭包里,
    起真服务才能测)。口径:
      - `A` / `B` / `C` → 预设分支(可控对照),mode=preset
      - 空 / `auto`     → 交给 AI 的 T0 回答判定,mode=judge(分支参数只作兜底)
      - 其它            → 拒绝。**不能**当 auto:调用方以为选了别的,实际跑的是 AI 判定。
    """
    choice = str(raw or "").strip().upper()
    if choice in ("A", "B", "C"):
        return choice, "preset"
    if choice in ("", "AUTO"):
        return current_branch, "judge"
    raise ValueError("不认识的 branch 取值 {!r}:只能是 A / B / C / auto".format(raw))


def build_service(host="127.0.0.1", port=5010, roles=None, run_id="",
                  scenario_dir="", with_review=True, on_restart=None, branch="B",
                  branch_mode="judge"):
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
        # 这一局的名字报给 /health:平台侧只嵌画布时靠它把画布与记录对上(2026-09-24)
        run_id=run_id,
        # ?embed=1:卡片里的 iframe 用压缩版式(去掉大标题与页边距),贴合 380px 宽的卡片
        extra_panels=[{"id": "review", "label": "结果记录",
                       "url": "/review?embed=1"}],
        on_restart=on_restart,
        # 重开时可以在页面上选:自动(由 AI 的 T0 回答判定,01 §六 的设计原意)或指定分支
        # ("auto" 是默认 —— 保留设计原意;选 A/B/C 则退回 preset 对照)
        restart_choices=[{"id": "branch",
                          "label": "分支",
                          "options": ["auto", "A", "B", "C"],
                          "value": "auto" if branch_mode == "judge" else branch}],
        # 顶栏外部工具链接:mavis 的角色/场景配置工具是**独立进程**(默认 8060),
        # 地址由 case01 侧给,vizkit 只负责渲染(它不认识"配置工具"这个词)
        extra_nav_links=[{"label": "配置工具 ↗",
                          "url": os.environ.get("MAVIS_CONFIG_TOOL_URL",
                                                "http://127.0.0.1:8060/"),
                          "title": "mavis 的角色/场景配置工具(独立进程;"
                                   "地址可用 MAVIS_CONFIG_TOOL_URL 覆盖)"}])
    if with_review:
        from ..review_app import attach_to
        # "本次实跑还没成品记录"的提示就靠这个:实跑跑完自动映射之后,下拉里才会出现它。
        attach_to(live.app, current_run_id=run_id,
                  note="实跑跑完会自动映射成成品记录")
    # 统一历史数据界面(/api/runs + /embed/explore)与 case00 同一个共享模块,
    # 于是 case01 实时面同样可开数据界面、同样能翻到 all 的历史。
    from live.history import router as history_router
    live.app.include_router(history_router)
    return live


def _map_run(run_id, branch, raw_out):
    """把这一局的原始记录映射成成品记录(**在本进程里顺序做**,不另起看护进程)。

    为什么改成顺序做:重开一局时如果每局都起一个独立的映射看护进程,连点几次就会
    堆起好几个映射都在抢同一个 Ollama —— 新一局的推演也跟着变慢
    (2026-09-19 实测:三次重开后三个映射进程同时跑)。顺序做就没有并发问题,
    而且映射完 `<run_id>/run.json` 一落盘,面板那边 `/api/review/live` 就会
    报 live=false + mapped=true,自动切到成品记录并弹绿条。
    映射失败**不静默**:打印退出码、输出尾部与手工重跑命令。
    """
    out = os.path.join("case01", "runs", run_id, "run.json")
    cmd = [sys.executable, "-m", "case01.injector.pipeline",
           "--branch", branch, "--run-id", run_id,
           "--from-record", os.path.abspath(raw_out), "--out", out, "--reflect"]
    print("  正在映射成品记录(约 1-2 分钟)…")
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=PKG_ROOT, capture_output=True, text=True)
    tail = (proc.stdout or "").strip()[-800:]
    if tail:
        print(tail)
    if proc.returncode != 0 or not os.path.exists(os.path.join(PKG_ROOT, out)):
        print("  [!] 映射失败(exit={}):可在 provenance/provenance 下手工重跑:\n      {}"
              .format(proc.returncode, " ".join(cmd)))
        if (proc.stderr or "").strip():
            print("      stderr 尾部:", proc.stderr.strip()[-400:])
        return False
    print("  成品记录已生成({:.0f}s) -> {}".format(time.time() - t0, out))
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="case01 单一界面:实时小镇 + 结果记录")
    ap.add_argument("--branch", default="B", choices=["A", "B", "C"],
                    help="兜底分支:judge 模式下只有在判不出来时才用它")
    ap.add_argument("--branch-mode", dest="branch_mode", default="judge",
                    choices=["judge", "preset"],
                    help="judge=先跑 T0 再由 AI 的回答判定分支(0904doc 01 §六 的设计原意,默认);"
                         "preset=分支由 --branch 指定(可控对照)")
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
    ap.add_argument("--exit-after-hold", dest="exit_after_hold", action="store_true",
                    help="保持期(--hold)结束后就退出(默认:只要页面上还有『重开一局』"
                         "按钮就一直等,免得按钮点了没反应)")
    ap.add_argument("--no-map", dest="no_map", action="store_true",
                    help="跑完不自动映射成成品记录(默认自动映射,由本进程顺序做)")
    args = ap.parse_args(argv)

    # 绑非本机地址必须显式声明:这些面没有任何鉴权(安全体检 2026-09-23)。
    from live.netguard import require_explicit_remote
    require_explicit_remote(args.host, where="5010 实时面")

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    if len(roles) != 2:
        print("--roles 需要正好两个角色名")
        return 2

    scenario = args.scenario_dir or SCENARIO
    # 页面上的"重开一局"按钮(用户要求:推演结束后决定要不要重开)。
    # 回调只做一件事:把重开请求(含页面上选的分支)记下来;真正的重开由下面的主循环执行
    # (不能在回调里直接跑推演,那是 HTTP 线程池的活)。
    restart_flag = threading.Event()
    restart_req = {"branch": ""}
    # 闭包里要读"当前分支":回调是 HTTP 线程池里跑的,不能直接用 args
    branch_now = [args.branch]

    def request_restart(payload=None):
        try:
            branch, mode = resolve_restart_choice((payload or {}).get("branch"),
                                                  current_branch=branch_now[0])
        except ValueError as exc:
            # 不认识的取值**不再静默当成 judge**(2026-09-24 第十轮体检):以前 {"branch":"D"}
            # 会被当成 auto,回一句"已受理:下一局由 AI 的 T0 回答判定分支" —— 调用方以为
            # 自己选了 D,实际跑的是 AI 判定,谁都不知道。现在明确拒绝并说明合法取值。
            print("重开请求被拒:{}".format(exc))
            return {"ok": False, "error": str(exc)}
        restart_req["branch"] = branch
        restart_req["mode"] = mode
        restart_flag.set()
        # **立刻**把"已结束"状态清掉:页面此刻正在刷新,不清的话连上来会收到
        # pending 里那条上一局的 done,变成"重开后又突然说推演结束"(用户实测反馈)。
        live.begin_run()
        # 同时把"实时记录"来源撤掉:此刻上一局的成品记录还在映射(~1-2 分钟),
        # 留着 provider 会让面板继续显示上一局的实时记录(7/7),看着像没重开。
        # 撤掉后面板会切到上一局的成品记录(或"尚未生成"提示);新一局开跑时再挂上。
        from ..review_app import clear_live, set_current_run
        clear_live()
        set_current_run("", "")
        print("收到重开请求:下一局 {};当前这一局跑完(或保持期结束)后立刻开".format(
            "由 AI 的 T0 回答判定分支" if mode == "judge" else "预设 {} 线".format(branch)))
        return {"ok": True,
                "detail": ("已受理:下一局由 AI 的 T0 回答判定分支" if mode == "judge"
                           else "已受理:下一局走 {} 线".format(branch)),
                "branch": branch, "mode": mode}

    live = build_service(host=args.host, port=args.port, roles=roles,
                         run_id=args.run_id, scenario_dir=scenario,
                         branch=args.branch, branch_mode=args.branch_mode,
                         on_restart=None if args.no_restart else request_restart)
    # 页面上有没有「重开一局」按钮(没注册回调就没有)
    can_restart = not args.no_restart
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
    branch = args.branch
    branch_mode = args.branch_mode
    try:
        while True:
            restart_flag.clear()
            # 分支方式由页面上的下拉决定(重开时可以换):auto=由 AI 的 T0 回答判定(默认),
            # A/B/C=预设(可控对照)。没选就沿用启动时的 --branch/--branch-mode。
            if restart_req.get("branch"):
                branch = restart_req["branch"]
                branch_mode = restart_req.get("mode") or branch_mode
                restart_req["branch"] = ""
                restart_req["mode"] = ""
            branch_now[0] = branch        # 回调里读"当前分支"用
            nodes = default_nodes(branch, roles=list(roles))
            debug_note = ""
            if args.nodes > 0:
                if args.nodes < len(nodes):
                    # 截断会把"末节点"变成最终反馈节点(1 节点跑时 T0 就是末节点,
                    # Ethan 张口就是"我没买…"),这种跑只适合自测 → 记录里必须标出来。
                    debug_note = ("--nodes {} 截断了节点序列({}→{}):末节点被当成最终反馈节点"
                                  .format(args.nodes, len(nodes), args.nodes))
                    print("**调试跑**:" + debug_note)
                nodes = nodes[:args.nodes]
            # 每跑一次就该有一个能唯一定位的名字:**日期在最前**(便于按时间排序)、
            # 带 case 与引擎、实跑再带 HHMM(同一天跑多次也不撞)。名字里的日期是
            # **真实运行时间**;记录里的 start_date/end_date 是模拟剧情日期,不是一回事。
            # judge 模式下真正的分支要等 T0 跑完才知道,所以名字里的分支先按兜底值,
            # 记录写完(raw 落盘)后由 pipeline 的 branch 覆盖 —— 提示会打出来。
            # --run-id 是调用方点名的名字(第一局),之后每一局现铸;同名会被 unique_run_id
            # 让开(否则同一分钟内重开一局会覆盖上一局的记录)。
            if args.run_id and not ran_once:
                run_id = args.run_id
            else:
                base = live_run_id("auto" if branch_mode == "judge" else branch)
                run_id = unique_run_id(base)
                if run_id != base:
                    print("名字 {} 已被占用(上一局),改用 {}".format(base, run_id))
            ran_once = True
            out = os.path.join("case01", "runs_injector", run_id, "raw.json")
            print("本次 run_id: {} (分支方式={} 兜底分支={})".format(run_id, branch_mode, branch))
            # 新的一局开始:把上一局的残留(已结束标记、积压事件、追赶快照)全清掉,
            # 否则刷新后的页面会收到上一局的 done / 旧位置。
            live.begin_run()
            # 面板是建服务时挂上去的,那时 run_id 还没生成;这里补告一次,
            # 好让面板能写出"本次实跑 <run_id> 还没成品记录(跑完自动生成)"。
            set_current_run(run_id, "实跑跑完会自动映射成成品记录")
            bridge = MavisBridge(
                nodes=nodes, roles=roles, scenario_dir=scenario,
                run_id=run_id,
                dry_run=False, max_retries=args.max_retries, branch=branch,
                branch_mode=branch_mode,
                debug_note=debug_note,
                visualizers=[live],
            )
            # 实时结果:面板每 2 秒来取一次"到目前为止的记录",用的映射与成品记录同一套,
            # 于是小镇一边动、右栏的对话/检索/事件/状态/审计一边长出来(用户要的"同步看全程")。
            # 跑完不立刻撤:成品记录还要等自动映射(~1-2 分钟),这期间面板继续显示完整过程。
            set_live_provider(lambda: bridge.run_record(), run_id=run_id,
                              total_nodes=len(nodes))
            try:
                record = bridge.run()
                # judge 模式:分支是 T0 跑完才定的;run_id 里那段分支名先按兜底值起,
                # 这里把**真实分支**打出来(记录里的 branch 以判定结果为准)。
                print("运行完成:", json.dumps(record.get("summary") or {}, ensure_ascii=False))
                print("  分支:{} (source={}) run_id={}".format(
                    record.get("branch"), record.get("branch_source"), run_id))
                bridge.save(out)
                print("记录已保存 ->", out)
                # 跑完必须**明确告诉页面**(而不是留着服务静悄悄):此后连进来的人
                # 会收到 done,知道"推演结束、服务只是在保持",不会以为可视化坏了。
                live.finish(record.get("finish_reason") or "run_finished")
                # 用户要求:结果出完了才给"重开一局"按钮 —— 所以映射(结果落盘)之后
                # 才把 restart_ready 置位;映射失败也要置位(否则人卡在这里没法重开),
                # 但失败原因会在 stdout 与页面提示里写明。
                try:
                    if record.get("branch") == "undetermined":
                        print("  Judge could not determine A/B/C; mapping skipped. "
                              "Select a branch in the UI and restart.")
                    elif not args.no_map:
                        # 用**判定后的**真实分支做映射(judge 模式下 branch 参数只是兜底)
                        _map_run(run_id, record.get("branch") or branch, out)
                    else:
                        print("  (--no-map:跳过映射;手工命令见 docs)")
                finally:
                    live.set_restart_ready(True)
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
                # **不要**在这里关掉可视化插件:它是跨局共享的 HTTP 服务,
                # 关掉等于把整个界面弄没(实测:重开一局后 5010 不再监听)。
                # 真正的关闭放到进程退出时(finally 外层)。
                bridge.close(keep_visualizers=True)
            if not restart_flag.is_set():
                # 保持期到了,但**页面上还挂着「重开一局」按钮**:这时如果直接退出,
                # 按钮就变成谎言 —— 点了没反应(用户反馈"重开一局这个功能有问题"的最常见来源)。
                # 所以默认**不退出**,继续等服务被 Ctrl+C;要旧行为请显式 --exit-after-hold。
                if can_restart and not args.exit_after_hold:
                    print("保持期结束,但页面上仍有「重开一局」按钮:"
                          "服务继续等(要退出请 Ctrl+C,或启动时加 --exit-after-hold)")
                    restart_flag.clear()
                    try:
                        while not restart_flag.wait(timeout=3600):
                            print("仍在等待「重开一局」…(服务在线,记录已生成)")
                    except KeyboardInterrupt:
                        print("已中断")
                        break
                    print("收到重开请求,开始新的一局")
                else:
                    break
            print("重开:开始新的一局(分支 {})".format(branch))
    finally:
        live.close()      # 进程要退出了:现在才关服务本身
        clear_live()      # 别让面板一直显示一个已经不动的"实时"
        print("服务已关闭")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
