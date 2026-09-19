# -*- coding: utf-8 -*-
"""厚 CLI:把 case01 的注入驱动与 mavis-vizkit 的实时可视化插件接起来。

可视化实现已抽到独立包 mavis-vizkit;本命令只负责把 case01 侧的
roles / alias / scenario / 前端资源根 作为参数喂给它,不再含渲染逻辑。

用法(在 provenance/provenance 下):
    python -m case01.vizkit.live_run --branch B --port 5010
然后浏览器打开 http://127.0.0.1:5010/ —— 运行过程中角色移动/对话会实时出现。
跑完后默认再保持 60 秒(--hold)方便观察,随后关闭服务。
"""
import argparse
import json
import os
import sys
import time

import mavis_vizkit
from ..injector.bridge import DEFAULT_ROLES, MavisBridge
from ..injector.nodes import default_nodes
from ..run_naming import run_id_for

# case01 业务侧的角色贴图别名与前端根(属于 case01,不属于 mavis-vizkit)
ROLE_TEXTURE_ALIAS = {"Investment AI": "AI Advisor", "Ethan Lin": "Mr. Zhou"}
_FRONTEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend")
SCENARIO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "injector", "scenario")


def main(argv=None):
    ap = argparse.ArgumentParser(description="case01 实时可视化(小镇风格)")
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
    args = ap.parse_args(argv)

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    if len(roles) != 2:
        print("--roles 需要正好两个角色名")
        return 2

    scenario = args.scenario_dir or SCENARIO
    live = mavis_vizkit.create(
        "live", host=args.host, port=args.port, roles=list(roles),
        alias=ROLE_TEXTURE_ALIAS, scenario_dir=scenario,
        static_root=os.path.join(_FRONTEND, "static"),
        template_dir=os.path.join(_FRONTEND, "templates"))
    live.start()
    print("实时可视化已启动: {}  (Ctrl+C 结束)".format(live.url()))

    nodes = default_nodes(args.branch, roles=list(roles))
    if args.nodes > 0:
        nodes = nodes[:args.nodes]
    # 每跑一次就该有一个能唯一定位的名字,而且名字里带**真实运行日期时刻**——
    # 旧默认 "live-<分支>" 不带时间,同一分支跑第二次就分不清哪条是哪次了
    # (2026-09-19 指出)。注意:名字里的日期是真实运行时间,记录里的
    # start_date/end_date 是模拟剧情日期,两者不是一回事。
    run_id = args.run_id or run_id_for(args.branch)
    print("本次 run_id: {}".format(run_id))
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
        if args.hold > 0:
            print("保持服务 {:.0f} 秒供观察…".format(args.hold))
            time.sleep(args.hold)
    except KeyboardInterrupt:
        print("已中断")
        exit_code = 130
    finally:
        bridge.close()
        print("服务已关闭")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())