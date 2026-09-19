# -*- coding: utf-8 -*-
"""厚 CLI:把 case01 的注入驱动与 mavis-vizkit 的实时可视化插件接起来,**一个界面**。

可视化实现已抽到独立包 mavis-vizkit;本命令只负责把 case01 侧的
roles / alias / scenario / 前端资源根 作为参数喂给它,并把 case01 自己的
**结果面板**(九块:概览/对话/检索/事件/状态/反思/问题分流/注入器/审计)
挂到**同一个服务**上——页面上是"实时小镇 / 结果记录"两个页签。
(独立的 5004 面板服务因此退役,见 case01/review_app.py 的 docstring。)

用法(在 provenance/provenance 下):
    python -m case01.vizkit.live_run --branch B --port 5010
    python -m case01.vizkit.live_run --review-only          # 不推演,只翻成品记录
然后浏览器打开 http://127.0.0.1:5010/ —— 左侧页签切"实时小镇 / 结果记录"。
跑完后默认再保持 60 秒(--hold)方便观察,随后关闭服务。
跑完会广播一条 `done`:此后(含保持期)打开页面的人会看到"已结束",
不会被空小镇误导——**不允许静默**。
"""
import argparse
import json
import os
import sys
import time

import mavis_vizkit
from ..injector.bridge import DEFAULT_ROLES, MavisBridge
from ..injector.nodes import default_nodes
from ..run_naming import live_run_id

# case01 业务侧的角色贴图别名与前端根(属于 case01,不属于 mavis-vizkit)
ROLE_TEXTURE_ALIAS = {"Investment AI": "AI Advisor", "Ethan Lin": "Mr. Zhou"}
_FRONTEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend")
SCENARIO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "injector", "scenario")


def build_service(host="127.0.0.1", port=5010, roles=None, run_id="",
                  scenario_dir="", with_review=True):
    """建**一个**服务:实时小镇 + case01 结果面板(九块)挂在同一个界面上。

    为什么要挂在一起(2026-09-19 用户拍板"只维护一个界面"):
    5010 原本只有小镇(过程),结果九块在另一个端口的面板服务(5004)上,
    于是人要看全貌得开两个地址。现在 5010 页面上多一个"结果记录"页签,
    同一个 FastAPI 应用上就挂着 `/review` 与 `/api/review/*`。

    分工:小镇由 mavis-vizkit 提供(它不认识 case01 的字段,只提供 extra_tabs 这个
    通用口子);九块由 case01 侧挂上去。5004 那个独立服务随之退役。
    """
    roles = list(roles or DEFAULT_ROLES)
    live = mavis_vizkit.create(
        "live", host=host, port=port, roles=roles,
        alias=ROLE_TEXTURE_ALIAS, scenario_dir=scenario_dir or SCENARIO,
        static_root=os.path.join(_FRONTEND, "static"),
        template_dir=os.path.join(_FRONTEND, "templates"),
        extra_tabs=[{"id": "review", "label": "结果记录", "url": "/review"}])
    if with_review:
        from ..review_app import attach_to
        # "本次实跑还没成品记录"的提示就靠这个:实跑跑完自动映射之后,下拉里才会出现它。
        attach_to(live.app, current_run_id=run_id,
                  note="实跑跑完会自动映射成成品记录")
    return live


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
    args = ap.parse_args(argv)

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    if len(roles) != 2:
        print("--roles 需要正好两个角色名")
        return 2

    scenario = args.scenario_dir or SCENARIO
    live = build_service(host=args.host, port=args.port, roles=roles,
                         run_id=args.run_id, scenario_dir=scenario)
    live.start()
    print("界面已启动: {}  (小镇 + 结果记录页签;Ctrl+C 结束)".format(live.url()))

    if args.review_only:
        # 不推演,只服务界面。告诉页面"没有在推演",别让人对着空小镇猜
        # (状态点会显示成"仅审阅模式")。这样它就是个可以随时开着的只读面。
        live.finish("review_only")
        print("仅审阅模式:没有在推演,小镇页签为空;结果记录页签可翻 case01/runs/")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("已停止")
            live.close()
            return 0

    nodes = default_nodes(args.branch, roles=list(roles))
    if args.nodes > 0:
        nodes = nodes[:args.nodes]
    # 每跑一次就该有一个能唯一定位的名字:**日期在最前**(便于按时间排序)、
    # 带 case 与引擎、实跑再带 HHMM(同一天跑多次也不撞)。名字里的日期是
    # **真实运行时间**;记录里的 start_date/end_date 是模拟剧情日期,不是一回事。
    run_id = args.run_id or live_run_id(args.branch)
    print("本次 run_id: {}".format(run_id))
    # 面板是建服务时挂上去的,那时 run_id 还没生成;这里补告一次,
    # 好让面板能写出"本次实跑 <run_id> 还没成品记录(跑完自动生成)"。
    from ..review_app import set_current_run
    set_current_run(run_id, "实跑跑完会自动映射成成品记录")
    bridge = MavisBridge(
        nodes=nodes, roles=roles, scenario_dir=scenario,
        run_id=run_id,
        dry_run=False, max_retries=args.max_retries, branch=args.branch,
        visualizers=[live],
    )
    exit_code = 0
    try:
        record = bridge.run()
        print("运行完成:", json.dumps(record.get("summary") or {}, ensure_ascii=False))
        if args.out:
            bridge.save(args.out)
            print("记录已保存 ->", args.out)
        # 跑完必须**明确告诉页面**(而不是留着服务静悄悄):此后连进来的人
        # 会收到 done,知道"推演结束、服务只是在保持",不会以为可视化坏了。
        live.finish("run_finished")
        if args.hold > 0:
            print("推演已结束,服务保持 {:.0f} 秒供观察(此刻打开页面会显示“已结束”)…"
                  .format(args.hold))
            time.sleep(args.hold)
    except KeyboardInterrupt:
        print("已中断")
        live.finish("interrupted")
        exit_code = 130
    except Exception as exc:  # noqa: BLE001 - 必须让页面也看到失败,不能只写在自己日志里
        print("运行失败: {}: {}".format(type(exc).__name__, exc))
        live.finish("error:{}".format(type(exc).__name__))
        exit_code = 1
    finally:
        bridge.close()
        print("服务已关闭")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())