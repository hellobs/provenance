# -*- coding: utf-8 -*-
"""场景引擎 CLI(引擎通用,Phase 4 收尾)。

命令行消费 `scenarios.discover`/`by_id` 的公开入口 —— 与 `serve` 互补(serve 走
网络,CLI 走进程内)。纯引擎词,不含任何场景业务词。

用法:
  python -m case_engine list                 # 列出所有已发现场景
  python -m case_engine show <case_id>       # 查看单场景声明
  python -m case_engine --cases-dir <路径> list   # 指定 cases 根目录
"""
import argparse
from typing import Optional

from case_engine.config import load_yaml
from case_engine.engines import describe
from case_engine.scenarios import by_id, default_cases_root, discover


def _do_list(root: str) -> None:
    infos = discover(root)
    print("cases_root: {}".format(root))
    for s in infos:
        flag = "  [!] {}".format(s.problem) if s.problem else ""
        print("{:20} engine={:<16} roles={:<3} {}{}".format(
            s.case_id, s.engine or "?", s.n_roles, s.name or "", flag))
    print("共 {} 个场景".format(len(infos)))


def _do_show(root: str, case_id: str) -> int:
    info = by_id(root, case_id)
    if info is None:
        print("场景不存在: {}".format(case_id))
        return 2
    if info.problem:
        print("场景无法加载: {}".format(info.problem))
        return 1
    cfg = load_yaml(info.path)
    print("case_id: {}".format(info.case_id))
    print("name:    {}".format(info.name))
    print("engine:  {} — {}".format(info.engine, describe(info.engine)["name"]))
    print("roles[{}]:".format(len(cfg.roles)))
    for r in cfg.roles:
        print("  - {}  ({} / {})".format(r.id, r.type, r.llm))
    fields = ", ".join(sorted(cfg.state_schema)) if cfg.state_schema else "-"
    print("state_schema[{}]: {}".format(len(cfg.state_schema), fields))
    print("path:    {}".format(info.path))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m case_engine",
        description="场景引擎 CLI:发现与查看声明式场景(scenario.yaml)",
    )
    parser.add_argument("--cases-dir", default=None,
                        help="cases 根目录(默认自动定位 provenance/provenance/cases)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="列出所有已发现场景")
    ps = sub.add_parser("show", help="查看单场景声明")
    ps.add_argument("case_id", help="场景 id(即 case 目录名)")
    args = parser.parse_args(argv)

    root = args.cases_dir or default_cases_root()
    if args.cmd == "list":
        _do_list(root)
        return 0
    return _do_show(root, args.case_id)


if __name__ == "__main__":
    raise SystemExit(main())