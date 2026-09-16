# -*- coding: utf-8 -*-
"""injector CLI(骨架)。

用法:
    python -m case01.injector.run_injector --timeline B --dry-run
    python -m case01.injector.run_injector --timeline B --scenario-dir <mavis 场景目录>
"""
import argparse
import json
import sys

from .bridge import MavisBridge
from .nodes import default_nodes


def main():
    ap = argparse.ArgumentParser(description="case01 injector(dry-run / mavis 驱动)")
    ap.add_argument("--timeline", default="A", choices=["A", "B", "C"],
                    help="使用哪条 timeline 生成节点(B/C 见 case01.world.timelines)")
    ap.add_argument("--roles", default="Investment AI,Ethan Lin",
                    help="两个角色名,逗号分隔")
    ap.add_argument("--scenario-dir", default="",
                    help="mavis 场景目录;dry-run 时不需要")
    ap.add_argument("--run-id", default="injector-dry")
    ap.add_argument("--max-retries", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true",
                    help="不加载 mavis,只产出同构记录")
    ap.add_argument("--out", default="",
                    help="记录输出路径(默认打印到 stdout)")
    args = ap.parse_args()

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    if len(roles) != 2:
        print("--roles 需要正好两个角色名")
        sys.exit(2)

    nodes = default_nodes(args.timeline, roles=list(roles))
    bridge = MavisBridge(
        nodes=nodes, roles=roles, scenario_dir=args.scenario_dir,
        run_id=args.run_id, max_retries=args.max_retries, dry_run=args.dry_run,
    )
    record = bridge.run()

    if args.out:
        bridge.save(args.out)
        print("saved ->", args.out)
    else:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
