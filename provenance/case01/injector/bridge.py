# -*- coding: utf-8 -*-
"""MavisBridge:用 case01 的节点序列驱动 mavis 侧的两个角色。

契约(见设计说明 §3-§6):
- 事实由 case01 world 层维护,本桥只把"已释放"的信息转成 mavis 的 story 事件;
- 临时状态通过 `Simulator(external_state=...)` 每步注入,不写记忆;
- 关键交互通过 `Simulator(interaction_request=...)` 请求,内容仍由 LLM 生成;
- 1 个节点 = 1 步;关键节点若未发生交互,在节点内重试(上限 max_retries)。

dry_run=True 时完全不 import mavisframework,只产出与真实运行同构的记录,
供联调、CI 与字段等价性对比使用。
"""
import json
import os
from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

from .nodes import NodeSpec

DEFAULT_ROLES: Tuple[str, str] = ("Investment AI", "Ethan Lin")


class MavisBridge:
    """节点序列 -> mavis 驱动 + 记录。"""

    def __init__(
        self,
        nodes: List[NodeSpec],
        roles: Tuple[str, str] = DEFAULT_ROLES,
        scenario_dir: str = "",
        run_id: str = "injector-run",
        max_retries: int = 5,
        dry_run: bool = True,
    ):
        self.nodes = list(nodes or [])
        self.roles = tuple(roles)
        self.scenario_dir = scenario_dir
        self.run_id = run_id
        self.max_retries = int(max_retries)
        self.dry_run = bool(dry_run)

        # mavis 侧对象(dry_run 时为 None)
        self.game = None
        self.simulator = None
        self.config: dict = {}

        # 当前节点的注入内容(供 external_state / interaction_request 回调读取)
        self._current_node: Optional[NodeSpec] = None
        self._current_context: Dict[str, dict] = {}
        self._current_requests: List[dict] = []

        # 记录
        self.records: List[dict] = []

    # ------------------------------------------------------------------
    # mavis 侧回调(两个通用入口的实参)
    # ------------------------------------------------------------------
    def external_state(self, name: str, step: int, sim_time: str, game) -> dict:
        """步级临时状态回调:返回该角色本节点的临时状态(不写记忆)。"""
        return dict(self._current_context.get(name, {}))

    def interaction_request(self, step: int, sim_time: str, game) -> List[dict]:
        """交互请求回调:返回本节点要发起的交互列表。"""
        return [dict(r) for r in self._current_requests]

    # ------------------------------------------------------------------
    # 驱动
    # ------------------------------------------------------------------
    def activate(self, node: NodeSpec) -> None:
        """把某个节点设为"当前节点",使其注入内容对两个回调可见。"""
        self._current_node = node
        self._current_context = {r: dict(node.context.get(r, {})) for r in self.roles}
        self._current_requests = [dict(r) for r in node.interactions]

    def run(self) -> dict:
        """按节点推进,返回本次运行的记录。"""
        if not self.dry_run:
            self._build_mavis()
        for idx, node in enumerate(self.nodes, start=1):
            self.activate(node)
            self._apply_world(node)

            retries = 0
            started = self._step_once(node)
            if node.require_interaction and not started:
                while retries < self.max_retries and not started:
                    retries += 1
                    started = self._step_once(node)

            self.records.append({
                "node_id": node.node_id,
                "date": node.date,
                "step": idx,
                "released_events": [e.get("id") for e in node.events],
                "context": {k: dict(v) for k, v in self._current_context.items()},
                "interactions": [dict(r) for r in self._current_requests],
                "interaction_started": bool(started),
                "retries": retries,
                "world": dict(node.world),
            })
        return self.run_record()

    def run_record(self) -> dict:
        """本次运行的记录(schema 版本化,便于与 case01 run.json 对齐)。"""
        return {
            "schema_version": "injector-0.1",
            "run_id": self.run_id,
            "mode": "dry-run" if self.dry_run else "mavis",
            "roles": list(self.roles),
            "scenario_dir": self.scenario_dir,
            "nodes": list(self.records),
            "summary": {
                "node_count": len(self.records),
                "interaction_started": sum(1 for r in self.records if r["interaction_started"]),
                "retries": sum(r["retries"] for r in self.records),
            },
            # 与 case01 run.json 的字段级对齐属于阶段 3 的验收项,此处显式标注未完成
            "case01_run_compatible": False,
        }

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.run_record(), f, ensure_ascii=False, indent=2)
        return path

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _apply_world(self, node: NodeSpec) -> None:
        """把该节点的世界事实写入(真实运行时写 case01 world 状态;dry-run 只记录)。"""
        if self.dry_run:
            return
        # TODO(阶段 2):接入 case01.world.state 的日期推进与事件释放,
        # 并把"已释放"的事件转成 story 事件写入 self._current_requests/事件池。
        raise NotImplementedError("真实运行的世界推进尚未接入,见执行计划阶段 2")

    def _step_once(self, node: NodeSpec) -> bool:
        """推进 1 步;返回本节点是否发生了被请求的交互。"""
        if self.dry_run:
            # dry-run:关键节点视为已发生,非关键节点视为未请求
            return bool(node.require_interaction or node.interactions)
        self.simulator.simulate(
            self.game, self.config, step=1, stride=0,
            start_step=len(self.records), checkpoints_folder="",
        )
        if not self.simulator.interactions:
            return False
        return bool(self.simulator.interactions[-1].get("started"))

    def _build_mavis(self) -> None:
        """构造 mavis 侧对象(懒加载,默认关闭时不 import mavisframework)。

        阶段 2 待办:
        1. 为两个角色准备 mavis 场景配置(agent.json / relationships / maze / storage);
        2. load_config 后建 Game(不挂 governance)与 Simulator(传入两个回调);
        3. 注册自定义条件类型(case01_node)用于"到点必发"的 story 事件。
        """
        if not self.scenario_dir:
            raise RuntimeError("dry_run=False 需要 --scenario-dir(含 mavis 场景配置的目录)")
        raise NotImplementedError(
            "mavis 场景装配为阶段 2 任务:需要两个角色的 mavis 配置与地图,"
            "见执行计划阶段 2 与设计说明 §3"
        )
