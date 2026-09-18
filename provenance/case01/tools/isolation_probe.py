# -*- coding: utf-8 -*-
"""隔离验证实测:未释放事件在节点前"检索不到"(执行计划阶段 3 第 2 项)。

做的事:跑一条真实的节点序列(默认 B 线全程),每个节点开始前对两个角色做两件事——
  1. 快照记忆:列出此刻记忆里所有"剧情注入"条目(应为"已释放事件"的并集),
     并直接检查"后续节点的事件原文"有没有一条已经在记忆里(主判据,预期 0 条);
  2. 主动检索:用**后续所有节点**的事件原文当检索词去检索记忆,看返回的条目里
     有没有真的包含未释放事件原文(预期 0 条)。
     注意 mavis 的检索是 top-k 相似度、没有相关度阈值,所以"有返回"不等于"泄漏",
     只有命中文本里真的出现未释放事件原文才算泄漏。

`--full-pool` 为对抗模式:把整条时间线的事件全部塞进 simulator.story,只靠
`case01_node` 条件放行当前节点。默认开启——隔离结论要在"池子里什么都有"的前提下
才站得住。

用法(在 provenance/provenance 下):
    python -m case01.tools.isolation_probe --branch B --out case01/runs_injector/isolation/probe-B.json
"""
import argparse
import json
import os
import sys
import time

from ..injector.bridge import DEFAULT_ROLES, MavisBridge
from ..injector.nodes import default_nodes

SCENARIO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "injector", "scenario")


class ProbeBridge(MavisBridge):
    """带记忆审计的桥:每次外部状态注入前,记录一次记忆快照与检索结果。"""

    def __init__(self, *args, full_pool=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.full_pool = bool(full_pool)
        self.probe: dict = {"per_node": [], "queries": []}
        self._step_index = 0

    # ---------------------------------------------------------------- 钩子
    def _apply_world(self, node):
        super()._apply_world(node)
        if self.full_pool and not self.dry_run and self.simulator is not None:
            # 对抗模式:池子里放全量事件,释放完全交给 case01_node 条件
            self.simulator.story = [
                self._as_story_event(ev, n) for n in self._all_nodes for ev in n.events]

    def external_state(self, name, step: int, sim_time: str, game) -> dict:
        # 该钩子在 _inject_story 之后触发 → 此刻记忆里应当已有本节点释放的事件
        try:
            self._audit(name, step, sim_time)
        except Exception as e:      # 审计失败不影响真实运行
            if self.game is not None:
                self.game.logger.warning("isolation probe failed: {}".format(e))
        return super().external_state(name, step, sim_time, game)

    # ---------------------------------------------------------------- 审计
    @staticmethod
    def _all_event_nodes(agent):
        """全量事件记忆。

        不能用 `retrieve_events()`:它按 `retention`(默认 8 条)截断,
        窗口外的剧情条目会被漏掉,审计就不是全量。这里直接按记忆索引取。
        失败时退回 `retrieve_events()`(窗口视图,弱一些但不会中断审计)。

        [越界] `associate.memory` 与 `associate._index` 是私有成员,不属于 mavis 的
        公开扩展面(见 `docs/case01_触点白名单.md` 第三、四节)。仅本验证工具使用,
        不参与正式运行;收编计划:向 mavis 提一个通用的"导出全部事件记忆"只读方法,
        或在公开检索侧复现同样结论(当前结论已有公开侧证据:60 次主动检索 0 泄漏)。
        """
        assoc = agent.associate
        try:
            ids = list((assoc.memory or {}).get("event") or [])   # [越界] 私有
        except Exception:
            return list(assoc.retrieve_events() or [])
        if not ids:
            return list(assoc.retrieve_events() or [])
        out = []
        for node_id in ids:
            try:
                # [越界] _index 私有;见本方法 docstring 的收编计划
                out.append(assoc.to_concept(assoc._index.find_node(node_id)))
            except Exception:
                continue
        return out

    def _story_memory(self, name: str):
        """该角色记忆里所有剧情注入条目(describe 文本,全量)。"""
        agent = self.game.get_agent(name)
        items = []
        for concept in self._all_event_nodes(agent):
            event = getattr(concept, "event", None)
            if event is None or getattr(event, "subject", "") != "环境":
                continue
            items.append(str(getattr(concept, "describe", "") or str(concept)))
        return items

    def _retrieve(self, name: str, text: str):
        agent = self.game.get_agent(name)
        hits = []
        for concept in agent.associate.retrieve_events(text=text) or []:
            event = getattr(concept, "event", None)
            if event is None or getattr(event, "subject", "") != "环境":
                continue
            hits.append(str(getattr(concept, "describe", "") or str(concept)))
        return hits

    def _audit(self, name: str, step: int, sim_time: str) -> None:
        node = self._current_node
        if node is None:
            return
        index = next(i for i, n in enumerate(self.nodes) if n is node)
        released = [ev["content"] for n in self.nodes[:index + 1] for ev in n.events]
        future = [ev["content"] for n in self.nodes[index + 1:] for ev in n.events]

        memory = self._story_memory(name)
        foreign = [d for d in memory
                   if not any(c and c in d for c in released)]

        # 主判据:后续节点的事件原文有没有一条已经躺在记忆里
        future_in_memory = [c for c in future if c and any(c in d for d in memory)]

        snapshot = {
            "node_id": node.node_id,
            "step": step,
            "sim_time": sim_time,
            "role": name,
            "memory_count": len(memory),
            "memory": memory,
            "foreign": foreign,
            "future_in_memory": future_in_memory,
        }
        self.probe["per_node"].append(snapshot)

        for content in future:
            if not content:
                continue
            hits = self._retrieve(name, content)
            # 检索是 top-k 相似度,没有相关度阈值 → 永远会返回"最像的已释放记忆"。
            # 因此只有命中的文本真的包含某条**未释放**事件原文,才算泄漏。
            leaked = any(content in h and not any(r and r in h for r in released)
                         for h in hits)
            self.probe["queries"].append({
                "node_id": node.node_id,
                "role": name,
                "query": content,
                "hits": hits,
                "leaked": bool(leaked),
            })


def summarise(probe: dict) -> dict:
    snapshots = probe["per_node"]
    queries = probe["queries"]
    return {
        "snapshots": len(snapshots),
        "foreign_total": sum(len(s["foreign"]) for s in snapshots),
        "future_in_memory_total": sum(len(s["future_in_memory"]) for s in snapshots),
        "queries": len(queries),
        "leaked_queries": [q for q in queries if q["leaked"]],
        "memory_equals_released": all(not s["foreign"] for s in snapshots),
        "future_never_retrievable": all(not q["leaked"] for q in queries),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="隔离验证实测(未释放事件检索不到)")
    ap.add_argument("--branch", default="B", choices=["A", "B", "C"])
    ap.add_argument("--roles", default=",".join(DEFAULT_ROLES))
    ap.add_argument("--scenario-dir", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--nodes", type=int, default=0, help="只跑前 N 个节点(0=全程)")
    ap.add_argument("--max-retries", type=int, default=3)
    ap.add_argument("--no-full-pool", action="store_true",
                    help="关闭对抗模式(池子里只放当前节点的事件)")
    ap.add_argument("--out", default="", help="审计结果落盘路径")
    args = ap.parse_args(argv)

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    if len(roles) != 2:
        print("--roles 需要正好两个角色名")
        return 2

    scenario = args.scenario_dir or SCENARIO
    all_nodes = default_nodes(args.branch, roles=list(roles))
    nodes = all_nodes[:args.nodes] if args.nodes > 0 else all_nodes

    bridge = ProbeBridge(
        nodes=nodes, roles=roles, scenario_dir=scenario,
        run_id=args.run_id or "isolation-{}".format(args.branch),
        dry_run=False, max_retries=args.max_retries, branch=args.branch,
        full_pool=not args.no_full_pool,
    )
    bridge._all_nodes = all_nodes
    t0 = time.time()
    record = bridge.run()
    summary = summarise(bridge.probe)

    out = {
        "schema_version": "isolation-probe-0.1",
        "branch": args.branch,
        "roles": list(roles),
        "full_pool": not args.no_full_pool,
        "node_count": len(nodes),
        "elapsed_s": round(time.time() - t0, 1),
        "probe": bridge.probe,
        "summary": summary,
        "run_summary": record.get("summary") or {},
        "pass": bool(summary["memory_equals_released"]
                     and summary["future_never_retrievable"]),
    }
    if args.out:
        path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("审计结果 ->", path)
    print(json.dumps({k: v for k, v in summary.items() if k != "leaked_queries"},
                     ensure_ascii=False))
    print("结论:", "通过" if out["pass"] else "不通过")
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
