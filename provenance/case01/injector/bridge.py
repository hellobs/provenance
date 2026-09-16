# -*- coding: utf-8 -*-
"""MavisBridge:用 case01 的节点序列驱动 mavis 侧的两个角色。

契约（见设计说明 §3–§6）:
- 事实由 case01 的 world 层维护,本桥只把"已释放"的信息转成 mavis 的 story 事件;
- 临时状态通过 `Simulator(external_state=...)` 每步注入,不写记忆;
- 关键交互通过 `Simulator(interaction_request=...)` 请求,内容仍由 LLM 生成;
- 1 个节点 = 1 步;关键节点若未发生交互,在节点内重试(上限 max_retries)。

dry_run=True 时完全不 import mavisframework（dry-run 与 CI 使用）。
"""
import datetime
import json
import os
import time
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
        anchor_coord: Optional[List[int]] = None,
        branch: str = "A",
        use_case01_facts: bool = True,
        meeting_coord: Optional[List[int]] = None,
    ):
        self.nodes = list(nodes or [])
        self.roles = tuple(roles)
        self.scenario_dir = scenario_dir
        self.run_id = run_id
        self.max_retries = int(max_retries)
        self.dry_run = bool(dry_run)
        self.anchor_coord = list(anchor_coord) if anchor_coord else None
        self.branch = branch
        self.use_case01_facts = bool(use_case01_facts)
        # 必须交互的节点:把两个角色钉到同一格并清空路径（mavis 要求同址且静止才可能对话）
        self.meeting_coord = list(meeting_coord) if meeting_coord else None

        # mavis 侧对象（dry_run 时为 None）
        self.game = None
        self.simulator = None
        self.config: dict = {}
        # 事实层（case01 World 的注入侧封装,dry_run 时不建）
        self.facts = None
        self._node_facts: Dict[str, dict] = {}
        self._world_audit: List[dict] = []
        # 当前节点 id（供 case01_node 条件读取）
        self._node_state: Dict[str, str] = {"id": ""}

        # 当前节点的注入内容（供两个回调读取）
        self._current_node: Optional[NodeSpec] = None
        self._current_context: Dict[str, dict] = {}
        self._current_requests: List[dict] = []

        # 记录
        self.records: List[dict] = []

    # ------------------------------------------------------------------
    # mavis 侧回调（两个通用入口的实参）
    # ------------------------------------------------------------------
    def external_state(self, name: str, step: int, sim_time: str, game) -> dict:
        """步级临时状态回调:返回该角色本节点的临时状态（不写记忆）。"""
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
        # 交互主题同时写入双方步级状态,确保话题进入 LLM 上下文
        for req in self._current_requests:
            src = req.get("from")
            dst = req.get("to")
            focus = str(req.get("focus", "") or "").strip()
            if not focus:
                continue
            if src in self._current_context:
                self._current_context[src].setdefault("current task", focus)
            if dst in self._current_context:
                self._current_context[dst].setdefault("user request", focus)

    def run(self) -> dict:
        """按节点推进,返回本次运行的记录。"""
        if not self.dry_run:
            self._build_mavis()
        for idx, node in enumerate(self.nodes, start=1):
            self.activate(node)
            self._apply_world(node)
            dialogue_before = self._dialogue_count()
            node_t0 = time.time()

            retries = 0
            started = self._step_once(node, step_index=idx - 1,
                                      stride=self._stride_to_next(idx - 1))
            if node.require_interaction and not started:
                while retries < self.max_retries and not started:
                    retries += 1
                    started = self._step_once(node, step_index=idx - 1, stride=0)

            self.records.append({
                "node_id": node.node_id,
                "date": node.date,
                "step": idx,
                "released_events": [e.get("id") for e in node.events],
                "events": [dict(e, date=node.date) for e in node.events],
                "context": {k: dict(v) for k, v in self._current_context.items()},
                "interactions": [dict(r) for r in self._current_requests],
                "interaction_started": bool(started),
                "retries": retries,
                "world": dict(node.world),
                "world_state": (self._node_facts.get(node.node_id) or {}).get("state"),
                "dialogue": self._dialogue_tail(dialogue_before),
                "elapsed_s": round(time.time() - node_t0, 1),
            })
        return self.run_record()

    def run_record(self) -> dict:
        """本次运行的记录（schema 版本化,便于与 case01 run.json 对齐）。"""
        return {
            "schema_version": "injector-0.1",
            "run_id": self.run_id,
            "mode": "dry-run" if self.dry_run else "mavis",
            "branch": self.branch,
            "roles": list(self.roles),
            "scenario_dir": self.scenario_dir,
            "nodes": list(self.records),
            "world_audit": list(self._world_audit),
            "condition_monitor": list(self.facts.condition_monitor) if self.facts else [],
            "summary": {
                "node_count": len(self.records),
                "interaction_started": sum(1 for r in self.records if r["interaction_started"]),
                "retries": sum(r["retries"] for r in self.records),
                "elapsed_s": round(sum(r.get("elapsed_s", 0) or 0 for r in self.records), 1),
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
    # mavis 装配与推进
    # ------------------------------------------------------------------
    def _build_mavis(self) -> None:
        """构造 mavis 侧对象（懒加载,默认关闭时不 import mavisframework）。"""
        if not self.scenario_dir:
            raise RuntimeError("dry_run=False 需要 --scenario-dir(含 mavis 场景配置的目录)")
        scenario = os.path.abspath(self.scenario_dir)
        config_path = os.path.join(scenario, "config.json")
        if not os.path.exists(config_path):
            raise RuntimeError("场景缺少 config.json: {}".format(config_path))

        from mavisframework.config.loader import load_config
        from mavisframework.core.timer import Timer
        from mavisframework.runtime.game import Game
        from mavisframework.runtime.simulator import Simulator

        from .conditions import install_case01_node_condition

        config = load_config(
            start_time=self._start_time(), stride=0, agents=list(self.roles),
            config_path=config_path, assets_root=scenario,
        )
        self._apply_ethan_provider(config)
        # 存档目录默认落在场景内,避免污染仓库根目录
        os.environ.setdefault("MAVIS_CHECKPOINTS_ROOT", os.path.join(scenario, "checkpoints"))
        install_case01_node_condition(self._node_state)

        self.config = config
        # 模拟时钟必须显式注入:Game 默认用墙钟(Timer() 取 datetime.now()),
        # 而 mavis 规定 23:00 后不发起对话 → 真实运行会随实际时间成败。
        timer = Timer(self._start_time())
        # case01 不挂 governance/consequence:不启用倾向与后果反馈机制
        self.game = Game(self.run_id, scenario, config, {},
                         timer=timer, governance=None, consequence_fn=None)
        # mavis 的 LLM provider 在 Agent.reset() 里惰性创建,必须显式初始化一次
        self.game.reset_game()
        self.simulator = Simulator(
            max_workers=max(1, len(self.roles)),
            export_decisions=False,
            external_state=self.external_state,
            interaction_request=self.interaction_request,
        )
        if self.use_case01_facts:
            from .worldfacts import Case01Facts

            self.facts = Case01Facts(self.branch, run_id=self.run_id)

    def _apply_world(self, node: NodeSpec) -> None:
        """设置当前节点:节点 id、本节点要释放的 story 事件、（可选）角色位置。"""
        if self.dry_run:
            return
        if self.simulator is None:
            raise RuntimeError("mavis 尚未装配(_build_mavis 未执行)")
        self._node_state["id"] = node.node_id
        # 只释放本节点的事件:未释放事件不进入任何角色上下文
        self.simulator.story = [self._as_story_event(ev, node) for ev in node.events]
        # 事实层推进(日期/价格/公开事件/Ethan 状态),与 case01 同口径
        if self.facts is not None:
            self._node_facts[node.node_id] = self.facts.apply_node(node)
            self._world_audit = self.facts.audit()
            # 最终反馈节点:把分支专属的个人处境注入发起方(镜像 03 §五/§七)
            if node is self.nodes[-1]:
                context = self.facts.final_feedback_context()
                for req in self._current_requests:
                    src = req.get("from")
                    if src in self._current_context:
                        self._current_context[src].setdefault("personal situation", context)
        if self.anchor_coord:
            for name in self.roles:
                agent_cfg = self.config.get("agents", {}).get(name)
                if agent_cfg is not None:
                    agent_cfg["coord"] = list(self.anchor_coord)
                    agent_cfg["path"] = []
        # 必须交互的节点:强制同址 + 静止,否则强制交互会被"在移动/不同处/无行动"挡住
        if node.require_interaction and node.interactions:
            self._pin_for_interaction(node)

    def _pin_for_interaction(self, node: NodeSpec) -> None:
        """强制交互前把两个角色放到同一格、清空路径、必要时补日程。

        mavis 的对话前置条件看的是运行时状态（agent.path / agent.action / daily_schedule）,
        只改 config 不够;这里用既有公开方法（move / make_schedule）把状态摆好,不改框架。
        """
        coord = self.meeting_coord
        if coord is None and self.roles:
            first = self.roles[0]
            if first in (self.game.agents or {}):
                coord = list(self.game.get_agent(first).coord)
        for name in self.roles:
            agent = (self.game.agents or {}).get(name)
            if agent is None:
                continue
            try:
                if len(getattr(agent.schedule, "daily_schedule", []) or []) < 1:
                    agent.make_schedule()
            except Exception as e:      # 日程生成失败不阻塞(下一步会再试)
                self.game.logger.warning(
                    "pin: make_schedule failed for {}: {}".format(name, e))
            if coord is not None:
                try:
                    agent.move(list(coord), [])
                except Exception as e:
                    self.game.logger.warning(
                        "pin: move failed for {}: {}".format(name, e))
            agent.path = []
            cfg = self.config.get("agents", {}).get(name)
            if cfg is not None and coord is not None:
                cfg["coord"] = list(coord)
                cfg["path"] = []

    def _step_once(self, node: NodeSpec, step_index: int = 0, stride: int = 0) -> bool:
        """推进 1 步;返回本节点是否发生了被请求的交互。"""
        if self.dry_run:
            return bool(node.require_interaction or node.interactions)
        self.simulator.interactions.clear()
        self.simulator.simulate(
            self.game, self.config, step=1, stride=stride,
            start_step=step_index, checkpoints_folder="",
        )
        return any(r.get("started") for r in self.simulator.interactions)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _dialogue_count(self) -> int:
        """当前已记录的对话块数量（用于取本步新增部分）。"""
        conv = getattr(self.game, "conversation", None) if self.game is not None else None
        if not conv:
            return 0
        total = 0
        for entries in conv.values():
            total += len(entries) if isinstance(entries, list) else 1
        return total

    def _dialogue_tail(self, start_count: int) -> List[dict]:
        """本步新增的对话块（dry-run 时为空）。"""
        conv = getattr(self.game, "conversation", None) if self.game is not None else None
        if not conv:
            return []
        blocks: List[dict] = []
        for entries in conv.values():
            if isinstance(entries, list):
                blocks.extend(entries)
            else:
                blocks.append(entries)
        return blocks[int(start_count):]

    def _start_time(self) -> str:
        date = self.nodes[0].date if self.nodes else "2026-08-27"
        return date.replace("-", "") + "-09:30"

    def _stride_to_next(self, idx: int) -> int:
        """当前节点 -> 下一节点 的模拟分钟数（用于 mavis 时间推进）。"""
        if idx + 1 >= len(self.nodes):
            return 0
        fmt = "%Y-%m-%d %H:%M"
        try:
            cur = datetime.datetime.strptime(self.nodes[idx].date + " 09:30", fmt)
            nxt = datetime.datetime.strptime(self.nodes[idx + 1].date + " 09:30", fmt)
        except ValueError:
            return 0
        return max(0, int((nxt - cur).total_seconds() // 60))

    @staticmethod
    def _as_story_event(ev: dict, node: NodeSpec) -> dict:
        """节点事件 -> mavis story 事件（带 case01_node 条件,到点必发）。"""
        return {
            "id": ev.get("id") or "{}-ev".format(node.node_id),
            "time": ev.get("time", "09:30"),
            "event_type": ev.get("event_type", "market"),
            "content": ev.get("content", ""),
            "targets": list(ev.get("targets") or ["all"]),
            "importance": int(ev.get("importance", 6) or 6),
            "condition": {"type": "case01_node", "node_id": node.node_id},
        }

    def _apply_ethan_provider(self, config: dict) -> None:
        """Ethan 走外部 API 时,从环境变量注入（密钥不进仓库、不进记录）。"""
        base_url = os.environ.get("CASE01_ETHAN_BASE_URL", "").strip()
        model = os.environ.get("CASE01_ETHAN_MODEL", "").strip()
        if not (base_url and model):
            return
        target = config.get("agents", {}).get(self.roles[1])
        if target is None:
            return
        target.setdefault("think", {})["llm"] = {
            "provider": "openai",
            "model": model,
            "base_url": base_url,
            "api_key": os.environ.get("CASE01_ETHAN_API_KEY", "").strip(),
        }
