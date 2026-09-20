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

import case_engine.engines as eng
from case_engine.config import load_yaml
from case_engine.engines import describe, build_for, supported_by
from case_engine.scenarios import by_id, default_cases_root, discover


def _load_scenario(root: str, case_id: str):
    info = by_id(root, case_id)
    if info is None:
        print("场景不存在: {}".format(case_id))
        return None, 2
    if info.problem:
        print("场景无法加载: {}".format(info.problem))
        return None, 1
    return load_yaml(info.path), 0


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


def _do_run(root: str, case_id: str, engine_id: str, input_text: str) -> int:
    """用选定引擎跑一份场景(自由搭配核心的可见演示)。

    选引擎 = build_for(scenario, requested):`--engine` 覆盖场景推荐引擎。
    experiment-eval 已接免 LLM 最小线(分支/一致性,产出结果);sandbox-value
    需 mavis 桥仍未接线 —— 如实报告,绝不假装产出。
    """
    scenario, rc = _load_scenario(root, case_id)
    if scenario is None:
        return rc
    strat = build_for(scenario, requested=engine_id)
    ok = strat.supports(scenario)
    print("case_id:  {}".format(case_id))
    print("engine:   {} (显式覆盖: {})".format(strat.engine_id, bool(engine_id)))
    print("supports: {}".format("✔ 可跑" if ok else "✘ 缺本引擎所需输入"))
    try:
        out = strat.run(scenario, input_text=input_text)
        print("run:      完成 ({})".format(out.get("run_type", "?")))
        print("branch:   {}".format(out.get("branch", "")))
        cs = out.get("consistency") or {}
        print("consist:  {} — {}".format(cs.get("verdict", ""), cs.get("reason", "")))
        if out.get("artifact"):
            print("artifact: {}".format(out["artifact"]))
        return 0
    except NotImplementedError as exc:
        print("run:      未接线 —— {}".format(exc))
        return 0 if ok else 4
    except ValueError as exc:
        print("run:      无法执行 —— {}".format(exc))
        return 4


def _do_engines(root: str, case_id: str) -> int:
    """列出全部已注册引擎;给 case-id 则显示该场景的兼容矩阵(自由搭配一览)。"""
    print("{:16} {:12} {}".format("engine", "id", "name"))
    for eid, meta in sorted(eng.ENGINES.items()):
        print("{:16} {:12} {}".format("", eid, meta["name"]))
    if case_id:
        scenario, rc = _load_scenario(root, case_id)
        if scenario is None:
            return rc
        ok_ids = set(supported_by(scenario))
        print("\n场景 {} 的引擎兼容矩阵:".format(case_id))
        for eid, meta in sorted(eng.ENGINES.items()):
            tag = "✔" if eid in ok_ids else "✘"
            print("  {}  {}  {}".format(eid, tag, meta["name"]))
    print("\n共 {} 个引擎;用 run --engine 可自由搭配".format(len(eng.ENGINES)))
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
    pr = sub.add_parser("run", help="用选定引擎跑一份场景(可 --engine/--input)")
    pr.add_argument("case_id", help="场景 id(即 case 目录名)")
    pr.add_argument("--engine", default="", help="指定引擎(默认用场景推荐的 meta.engine)")
    pr.add_argument("--input", default="", help="受判的对象回答(experiment-eval 需要)")
    pe = sub.add_parser("engines", help="列出全部已注册引擎(及可选兼容矩阵)")
    pe.add_argument("--case-id", default="", help="若给,显示该场景的引擎兼容矩阵")
    args = parser.parse_args(argv)

    root = args.cases_dir or default_cases_root()
    if args.cmd == "list":
        _do_list(root)
        return 0
    if args.cmd == "show":
        return _do_show(root, args.case_id)
    if args.cmd == "run":
        return _do_run(root, args.case_id, args.engine, args.input)
    return _do_engines(root, args.case_id)


if __name__ == "__main__":
    raise SystemExit(main())