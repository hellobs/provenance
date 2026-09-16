# -*- coding: utf-8 -*-
"""完整流水线:节点序列 -> mavis 驱动 -> case01 兼容记录 ->（可选）Reflection/Router。

用法（真实运行,需 Ollama）:
    python -m case01.injector.pipeline --branch B --reflect --out runs_injector/B.json

dry-run 不加载 mavis,只产出同构记录（CI/联调用）。
"""
import argparse
import json
import os
import sys
from typing import Optional, Tuple

from .bridge import DEFAULT_ROLES, MavisBridge
from .nodes import default_nodes
from .record import to_case01_record

DEFAULT_SCENARIO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenario")


def _attach_reflection(record: dict, llm=None, router_llm=None,
                       external_router: bool = False) -> dict:
    """在映射后的记录上补 Reflection 与 Router（复用 case01 现有实现）。"""
    from ..agents.llm import OllamaClient, OpenRouterClient
    from ..reflection import run_reflection, run_router

    local = llm or OllamaClient()
    router = router_llm or (OpenRouterClient() if external_router else local)
    ref = run_reflection(local, record)
    record["reflection"] = {"material": ref["material"], "text": ref["text"]}
    rout = run_router(router, ref["text"])
    record["router"] = {"raw": rout["raw"], "issues": rout["issues"]}
    # 这两个字段已补齐,从 gaps 里移除
    gaps = [g for g in record.get("compat", {}).get("gaps", [])
            if not g.startswith("reflection/router")]
    record.setdefault("compat", {})["gaps"] = gaps
    record["compat"]["reflection_attached"] = True
    return record


def run_pipeline(branch: str = "B", scenario_dir: str = "", run_id: str = "",
                 roles: Tuple[str, ...] = DEFAULT_ROLES, dry_run: bool = False,
                 max_retries: int = 2, reflect: bool = False,
                 external_router: bool = False, llm=None, router_llm=None,
                 out_path: str = "") -> dict:
    """跑一条完整流水线,返回 case01 兼容记录。"""
    scenario_dir = scenario_dir or DEFAULT_SCENARIO
    run_id = run_id or "injector-{}".format(branch)
    nodes = default_nodes(branch, roles=list(roles))
    bridge = MavisBridge(nodes=nodes, roles=roles, scenario_dir=scenario_dir,
                         run_id=run_id, max_retries=max_retries, dry_run=dry_run,
                         branch=branch)
    raw = bridge.run()
    record = to_case01_record(raw, branch=branch)
    record.setdefault("compat", {})["reflection_attached"] = False

    if reflect:
        _attach_reflection(record, llm=llm, router_llm=router_llm,
                           external_router=external_router)

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    return record


def main():
    ap = argparse.ArgumentParser(description="case01 injector 完整流水线")
    ap.add_argument("--branch", default="B", choices=["A", "B", "C"])
    ap.add_argument("--roles", default="Investment AI,Ethan Lin")
    ap.add_argument("--scenario-dir", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reflect", action="store_true",
                    help="运行后调用 case01 的 Reflection + Router（需本地 LLM）")
    ap.add_argument("--external-router", action="store_true",
                    help="Router 用外部 API（需 OPENROUTER_API_KEY）")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    if len(roles) != 2:
        print("--roles 需要正好两个角色名")
        sys.exit(2)

    record = run_pipeline(
        branch=args.branch, scenario_dir=args.scenario_dir, run_id=args.run_id,
        roles=roles, dry_run=args.dry_run, max_retries=args.max_retries,
        reflect=args.reflect, external_router=args.external_router, out_path=args.out,
    )
    if args.out:
        print("saved ->", args.out)
    print(json.dumps(record.get("summary") or {}, ensure_ascii=False))
    print("turns:", len(record.get("turns") or []),
          "events:", len(record.get("events") or []),
          "issues:", len((record.get("router") or {}).get("issues") or []))
    return 0


if __name__ == "__main__":
    sys.exit(main())
