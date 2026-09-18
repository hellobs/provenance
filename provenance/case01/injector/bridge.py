# -*- coding: utf-8 -*-
"""MavisBridge:用 case01 的节点序列驱动 mavis 侧的两个角色。

契约（见设计说明 §3–§6）:
- 事实由 case01 的 world 层维护,本桥只把"已释放"的信息转成 mavis 的 story 事件;
- 临时状态通过 `Simulator(external_state=...)` 每步注入,不写记忆;
- 关键交互通过 `Simulator(interaction_request=...)` 请求,内容仍由 LLM 生成;
- 1 个节点 = 1 步;关键节点若未发生交互,在节点内重试(上限 max_retries)。

dry_run=True 时完全不 import mavisframework（dry-run 与 CI 使用）。

接入边界:case01 只依赖 mavis 的**公开扩展面**(见 mavis `docs/tutorial-extension.md`),
越界处在本文件里都有 `[越界]` 注释并登记在 `docs/case01_触点白名单.md`;
mavis 侧对应的契约测试是 `tests/test_extension_surface.py`。
新增需求按白名单第一节的顺序办:配置 → 既有扩展点 → case01 自己解决 → 才提新扩展点。
"""
import datetime
import json
import os
import time
from typing import Dict, List, Optional, Tuple

from .nodes import NodeSpec

DEFAULT_ROLES: Tuple[str, str] = ("Investment AI", "Ethan Lin")


def _plugin_surface_available() -> bool:
    """特性探测:mavis 是否具备通用插件面(纯新增,main 上还没有)。

    provenance 的 CI 从 GitHub mavis 的 main 装框架,main 上没有
    `mavisframework.plugin` 与 `Simulator(plugins=)`。这里一次性探测
    "插件面三个要件"都在,才让 bridge 走插件面;否则回退到旧回调写法。
    """
    try:
        import inspect

        import mavisframework.plugin  # noqa: F401
        from mavisframework.core import agent_core
        from mavisframework.runtime.simulator import Simulator

        return (
            hasattr(agent_core, "subscribe_chat_line")
            and "plugins" in inspect.signature(Simulator.__init__).parameters
        )
    except Exception:
        return False


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
        c_plan_llm: Optional[object] = None,
        visualizers: Optional[List[object]] = None,
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
        # Branch C 方案解析用的 LLM:可注入(如 Leo 的 HF 权重客户端),缺省回退本地 Ollama
        self.c_plan_llm = c_plan_llm

        # 可插拔可视化:引擎只产生事件,插件自己决定怎么画(见 vizkit/)
        self._agent_trace: Dict[int, Dict[str, dict]] = {}
        self._fanout = self._build_fanout(visualizers)
        # 插件面迁移状态(默认关闭,由 _build_mavis 决定):
        # _plugin_mode=True 时走 mavis 插件面且不再覆盖全局 chat_callback;
        # _set_chat_callback 只在回退路径(无插件面)设置过全局钩子时置 True。
        self._plugin_mode = False
        self._set_chat_callback = False
        self._adapter = None

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
        # Branch C 的条件化方案(在 T0 节点落地后解析,见 _install_c_plan)
        self._c_plan: Optional[dict] = None

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
                "agents": dict(self._agent_trace.get(idx, {})),
                "elapsed_s": round(time.time() - node_t0, 1),
            })
            # Branch C:首个节点(T0)落地后,用 Investment AI 的答案解析条件化方案并
            # 注入事实层(buy_now 立即建仓 / wait 留待后续节点监测触发)。
            if self.branch == "C" and not self.dry_run \
                    and self.facts is not None and node is self.nodes[0]:
                self._install_c_plan(self.records[-1], node)
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
            "c_plan": self._c_plan,
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
        # (已知行为,见 mavis docs/tutorial-extension.md §5;不是绕框架)
        self.game.reset_game()
        self._plugin_mode = _plugin_surface_available()
        # mavis 插件面存在时,可视化事件经薄适配器(case01 侧的 Plugin 子类)转发给
        # vizkit Fanout;对话逐句也不再用"覆盖全局 chat_callback"的方式接入
        # (由 Simulator 自动订阅对话总线 → 适配器 → fanout),消除同进程互相顶掉。
        adapter = None
        if self._fanout is not None and self._plugin_mode:
            from .viz_plugin import VizForwarder

            adapter = VizForwarder(self)
            self._adapter = adapter
        sim_kwargs = dict(
            max_workers=max(1, len(self.roles)),
            export_decisions=False,
            external_state=self.external_state,
            interaction_request=self.interaction_request,
            on_agent=self._viz_on_agent,     # 记录侧总走这里(_agent_trace 需要 step)
            on_step=self._viz_on_step,       # time/snapshot(config 自带 step)
        )
        if adapter is not None:
            sim_kwargs["plugins"] = [adapter]
        if self._fanout is not None and not self._plugin_mode:
            # 回退:无插件面时 story 事件只能走 on_story 回调
            sim_kwargs["on_story"] = self._viz_on_story
        self.simulator = Simulator(**sim_kwargs)
        if self._fanout is not None:
            from mavis_vizkit.events import init_event

            # init 事件不在插件总线上(由消费方自行构造),这里直接发给 fanout
            self._fanout.emit(init_event(list(self.roles)))
            if not self._plugin_mode:
                # 回退:无插件面时对话逐句只能走全局钩子(旧写法);
                # 运行结束由 close() 把它清回 None,避免污染同进程其它消费者。
                import mavisframework.core.agent_core as _fw_agent

                if _fw_agent.chat_callback is None:
                    self._set_chat_callback = True
                    _fw_agent.chat_callback = self._viz_on_chat_line
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

        越界声明（见 `docs/case01_触点白名单.md` 第三、四节）:下面两处碰了 mavis 的
        半公开/内部状态,暂留并已登记收编计划:
        - 读 `agent.schedule.daily_schedule` 只为判断"日程是否已生成";
        - 直写 `agent.path = []`,因为清空运行时路径没有公开入口。
        两处都只影响 case01 自己的进程,不改 mavis 语义;收编放到 mavis 下次动扩展面时。
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
                # [越界·半公开] schedule.daily_schedule 是子对象内部字段
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
            # [越界·内部状态] 清空运行时路径:没有公开入口,只能直写
            agent.path = []
            cfg = self.config.get("agents", {}).get(name)
            if cfg is not None and coord is not None:
                cfg["coord"] = list(coord)
                cfg["path"] = []

    # ------------------------------------------------------------------
    # 可插拔可视化接线(引擎只产生事件,插件自己画)
    # ------------------------------------------------------------------
    @staticmethod
    def _build_fanout(visualizers):
        """visualizers:[插件实例] / ["town","report","console"] / None(不接)。"""
        if not visualizers:
            return None
        from mavis_vizkit import Fanout, create

        instances = [create(v) if isinstance(v, str) else v for v in visualizers]
        return Fanout(instances, on_error=lambda name, exc: None)

    def _trace_agent(self, name, state, step, sim_time):
        """记录侧:把本步节点的 agent 状态落进 _agent_trace(两种模式都必须)。"""
        from mavis_vizkit.events import as_text

        state = state or {}
        action = as_text(state.get("action"))
        location = as_text(state.get("location"))
        currently = as_text(state.get("currently"))
        self._agent_trace.setdefault(int(step), {})[name] = {
            "coord": list(state.get("coord") or []),
            "action": action,
            "location": location,
            "currently": currently,
        }

    def _role_type(self, name: str) -> str:
        agent = (self.game.agents or {}).get(name) if self.game is not None else None
        return getattr(agent, "role_type", "user") or "user"

    def _emit_agent(self, name, state, sim_time):
        """可视化侧:发一条 agent 事件给 fanout(适配器转发;不需要 step)。"""
        if self._fanout is None:
            return
        from mavis_vizkit.events import agent_event, as_text

        state = state or {}
        action = as_text(state.get("action"))
        location = as_text(state.get("location"))
        currently = as_text(state.get("currently"))
        self._fanout.emit(agent_event(
            name, state.get("coord"), action, location, currently,
            state.get("path"), sim_time, self._role_type(name)))

    def _viz_on_agent(self, name, state, step, sim_time):
        """on_agent 回调:记录侧总走这里;_plugin_mode 下可视化事件由适配器转发。"""
        self._trace_agent(name, state, step, sim_time)
        if not self._plugin_mode:
            self._emit_agent(name, state, sim_time)

    def _viz_on_step(self, config):
        if self._fanout is not None:
            from mavis_vizkit.events import snapshot_event, time_event

            config = config or {}
            step = int(config.get("step") or 0)
            sim_time = str(config.get("time", ""))
            self._fanout.emit(time_event(sim_time, step))
            # 每步补一份快照:实时前端可用它刷新全量状态,新连入的客户端也能立刻有画面
            trace = self._agent_trace.get(step) or {}
            if trace:
                self._fanout.emit(snapshot_event(
                    {name: dict(st) for name, st in trace.items()}, sim_time))

    def _viz_on_chat_line(self, speaker, text):
        if self._fanout is not None:
            from mavis_vizkit.events import chat_event

            self._fanout.emit(chat_event(speaker, text))

    def _viz_on_story(self, ev):
        if self._fanout is not None:
            from mavis_vizkit.events import story_event

            self._fanout.emit(story_event(dict(ev or {})))

    def close(self) -> None:
        """收尾:关闭可视化插件,并按所走的路径退订对话接线。"""
        if self._fanout is not None:
            self._fanout.close()
        if self.simulator is None:
            return
        if self._plugin_mode:
            # 插件面路径:退订 Simulator 自动挂到 agent_core 的对话订阅
            self.simulator.plugin_teardown()
        elif self._set_chat_callback:
            # 回退路径:清掉我们设置过的全局 chat_callback,避免污染同进程其它消费者。
            # 注意:按方法相等(==)比较,而非 is——每次属性访问 `self._viz_on_chat_line`
            # 都会新建一个绑定期程对象,`is` 永远为假,会导致这里清不掉。
            import mavisframework.core.agent_core as _fw_agent

            if _fw_agent.chat_callback == self._viz_on_chat_line:
                _fw_agent.chat_callback = None
            self._set_chat_callback = False

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

    def _install_c_plan(self, rec: dict, node: NodeSpec) -> None:
        """从 T0 节点的对话里取出 Investment AI 的答案,解析成条件化方案并注入事实层。

        只在该节点落地后调用(此时 T0 对话已生成、facts 已推进到 T0 当天)。
        buy_now 会立即建仓 → 重取 T0 节点的状态快照;wait 的方案由后续 apply_node 监测。
        """
        ai_answer = self._extract_role_answer(rec, self.roles[0])
        plan: dict = {}
        if ai_answer and self.facts is not None:
            from ..world.branch import ConditionPlanParser

            llm = self.c_plan_llm
            if llm is None:
                from ..agents.llm import OllamaClient

                llm = OllamaClient()
            try:
                plan = ConditionPlanParser(llm).parse(ai_answer)
                plan.setdefault("source", "T0")
            except Exception as e:
                if self.game is not None:
                    self.game.logger.warning(
                        "C plan parse failed at {}: {}".format(
                            node.node_id, e))
                plan = {"action": "wait", "fraction": 0.0, "buy_fraction": 0.0,
                        "condition": "(parse-failed)",
                        "trigger": {"type": "none", "value": None,
                                    "keywords": []},
                        "judge": "llm-plan-error"}
            self.facts.set_c_plan(plan)
            rec["world_state"] = self.facts.state_snapshot()
            self._node_facts[node.node_id] = {"state": self.facts.state_snapshot()}
        rec["c_plan"] = plan or None
        self._c_plan = plan or None

    @staticmethod
    def _extract_role_answer(rec: dict, role: str) -> str:
        """从一条节点记录的 dialogue 里取某角色最后一条发言文本。"""
        answers: List[str] = []
        for block in rec.get("dialogue") or []:
            if not isinstance(block, dict):
                continue
            for lines in block.values():
                for line in lines or []:
                    if (isinstance(line, (list, tuple)) and len(line) == 2
                            and str(line[0]) == role):
                        answers.append(str(line[1]))
        return answers[-1] if answers else ""

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
