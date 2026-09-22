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
        branch_mode: str = "preset",
        c_plan_file: str = "",
        debug_note: str = "",
        judge_llm: Optional[object] = None,
        backend_kind: str = "",
    ):
        self.nodes = list(nodes or [])
        self.roles = tuple(roles)
        self.scenario_dir = scenario_dir
        self.run_id = run_id
        self.max_retries = int(max_retries)
        self.dry_run = bool(dry_run)
        self.anchor_coord = list(anchor_coord) if anchor_coord else None
        self.branch = branch
        # 分支来源:preset(运行参数) / judge(由 T0 回答判定,01 §六 的设计原意)
        if branch_mode not in ("preset", "judge"):
            raise ValueError("branch_mode 只能是 preset 或 judge,收到 {!r}".format(branch_mode))
        self.branch_mode = branch_mode
        # 判定后端(**只用于运行清单,不改变判定行为**):真跑时 judge 走本地 Ollama;
        # 显式传 judge_llm/backend_kind 时按它记(测试替身、外部 API、规则判定都能如实落账)。
        self.judge_llm = judge_llm
        self.backend_kind = backend_kind or ""
        # 运行清单轻量部分的缓存(键 = 决定它的那四个量),避免每步重算
        self._manifest_meta_cache: Optional[tuple] = None
        # judge 模式下 T0 跑完之前**不能**自称 preset —— 否则映射出来的记录会显示
        # "分支来源=实验设计预设",看着像 preset 跑(用户实测反馈)。空串 = 待判定。
        self.branch_source = "preset" if branch_mode == "preset" else ""
        self.judge_info: Dict[str, str] = {}
        self.use_case01_facts = bool(use_case01_facts)
        # 必须交互的节点:把两个角色钉到同一格并清空路径（mavis 要求同址且静止才可能对话）
        self.meeting_coord = list(meeting_coord) if meeting_coord else None
        # Branch C 方案解析用的 LLM:可注入(如 引擎侧 的 HF 权重客户端),缺省回退本地 Ollama
        self.c_plan_llm = c_plan_llm
        # C 线方案也可以来自文件(演示/联调:模型在传闻级证据下总是"等正式确认",
        # 而 A 线市场根本没有订单确认事件 → 条件永不触发。用文件能稳定演示
        # "条件触发→建仓→亏损→反思"整条链,记录里会标 source=manual)
        self.c_plan_file = c_plan_file or ""
        # 调试跑说明(--nodes 截断节点序列等)。**必须进记录**:截断会让"末节点"
        # 变成最终反馈节点(1 节点跑时 T0 就是末节点 → Ethan 张口就是最终反馈),
        # 这种记录不该被当成正式样本(2026-09-19 实测 B-1652/B-1654 就是这样来的)。
        self.debug_note = debug_note or ""

        # 可插拔可视化:引擎只产生事件,插件自己决定怎么画(见 vizkit/)
        self._agent_trace: Dict[int, Dict[str, dict]] = {}
        # 前端"上一次画到哪个格子"——用来给 pin 之类没有路径的移动补一条正交可视路径
        # (见 _visual_path;不补的话前端会沿直线斜穿格子)。
        # 初值 = 场景配置里的初始坐标 = 前端把角色摆在的位置,否则**第一段**仍是斜线。
        self._last_visual_coord: Dict[str, list] = self._seed_visual_coords()
        # 已经报过"这对格子不可达"的组合(避免每步刷同一行日志)
        self._visual_path_warned: set = set()
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
        """按节点推进,返回本次运行的记录。

        `branch_mode="judge"` 时**保留 0904doc 01 §六 的设计原意**:先跑 T0(咨询当天),
        用 Investment AI 在 T0 的实际回答判定本次进入 A/B/C,再按该分支的预设时间线跑完
        其余节点(市场世界仍不由模型临时生成)。`branch_mode="preset"` 则是可控对照:
        分支由运行参数指定,T0 不参与判定。
        """
        if not self.dry_run:
            self._build_mavis()
        nodes = list(self.nodes)
        idx = 0
        while idx < len(nodes):
            node = nodes[idx]
            step_index = idx          # 0-based,与原来 enumerate(start=1) 的 idx-1 等价
            idx += 1
            self.activate(node)
            self._apply_world(node)
            dialogue_before = self._dialogue_count()
            node_t0 = time.time()

            retries = 0
            started = self._step_once(node, step_index=step_index,
                                      stride=self._stride_to_next(step_index))
            if node.require_interaction and not started:
                while retries < self.max_retries and not started:
                    retries += 1
                    started = self._step_once(node, step_index=step_index, stride=0)

            self.records.append({
                "node_id": node.node_id,
                "date": node.date,
                "step": step_index + 1,
                "released_events": [e.get("id") for e in node.events],
                "events": [dict(e, date=node.date) for e in node.events],
                "context": {k: dict(v) for k, v in self._current_context.items()},
                "interactions": [dict(r) for r in self._current_requests],
                "interaction_started": bool(started),
                "retries": retries,
                "world": dict(node.world),
                "world_state": (self._node_facts.get(node.node_id) or {}).get("state"),
                "dialogue": self._dialogue_tail(dialogue_before),
                "agents": dict(self._agent_trace.get(step_index + 1, {})),
                "elapsed_s": round(time.time() - node_t0, 1),
            })

            # judge 模式:第一个节点(T0)落地后立刻判定分支,然后换成该分支的后续节点重排。
            if (self.branch_mode == "judge" and step_index == 0 and not self.dry_run):
                self._decide_branch_from_t0(self.records[-1], t0_node=node)
                nodes = list(self.nodes)
            # Branch C:分支确定后,用 Investment AI 的 T0 答案解析条件化方案并注入事实层
            # (buy_now 立即建仓 / wait 留待后续节点监测触发)。
            if self.branch == "C" and not self.dry_run \
                    and self.facts is not None and step_index == 0:
                self._install_c_plan(self.records[-1], node)
        return self.run_record()

    def _judge_client(self):
        """分支判定用的 LLM 客户端。

        优先级:显式注入的 judge_llm → c_plan_llm(引擎侧权重客户端/测试替身)
        → 本地 OllamaClient。**行为与既有实现等价**(原来就是 c_plan_llm or OllamaClient);
        多出来的 judge_llm 是给"运行清单要如实记判定后端"用的。
        """
        if self.judge_llm is not None:
            return self.judge_llm
        if self.c_plan_llm is not None:
            return self.c_plan_llm
        from ..agents.llm import OllamaClient

        return OllamaClient()

    def _decide_branch_from_t0(self, rec: dict, t0_node: NodeSpec) -> str:
        """用 Investment AI 在 T0 的实际回答判定分支(01 §六),并据此重排后续节点与事实层。

        判不出来(没有 T0 对话)时**不静默**:记一条警告、保留原分支,并把
        `branch_source` 标成 `preset-fallback` —— 记录里能看出来这次没判成。
        """
        answer = self._extract_role_answer(rec, self.roles[0])
        if not answer:
            self.branch_source = "preset-fallback"
            self.judge_info = {"detected": "", "reason": "T0 没有 AI 回答,无法判定"}
            self._warn("judge: T0 没有 Investment AI 的回答,分支退回预设 {}".format(self.branch))
            return self.branch
        from ..world.branch import LLMBranchJudge

        llm = self._judge_client()
        try:
            detected, info = LLMBranchJudge(llm).judge(answer)
        except Exception as e:  # noqa: BLE001 - 判定失败也要留痕,不静默
            self.branch_source = "preset-fallback"
            self.judge_info = {"detected": "", "reason": "judge 失败: {}".format(e)}
            self._warn("judge 调用失败({}),分支退回预设 {}".format(e, self.branch))
            return self.branch
        self.branch = detected
        self.branch_source = "judge"
        self.judge_info = {"detected": detected, "reason": info.get("reason", ""),
                           "judge": info.get("judge", "llm"),
                           "answer_head": answer[:200]}
        # 后续节点换成该分支的时间线(T0 已经跑过,从第 2 个节点接着跑)
        self.nodes = [t0_node] + self._nodes_for(detected)[1:]
        # 事实层也得换成该分支的市场世界,并把它推进到 T0(与刚跑完的那一步对齐)
        if self.use_case01_facts:
            from .worldfacts import Case01Facts

            facts = Case01Facts(detected, run_id=self.run_id)
            facts.apply_node(t0_node)
            self.facts = facts
            self._node_facts[t0_node.node_id] = {"state": facts.state_snapshot()}
            self._world_audit = facts.audit()
            rec["world_state"] = facts.state_snapshot()
        self._warn("judge: T0 判定为 {} 线({});后续按 Timeline {} 跑".format(
            detected, self.judge_info["reason"], "B" if detected == "B" else "A"))
        return detected

    def _nodes_for(self, branch: str) -> List[NodeSpec]:
        """某个分支的完整节点序列(judge 模式判定后重排用)。"""
        from .nodes import default_nodes

        return default_nodes(branch, roles=list(self.roles))

    def _warn(self, msg: str) -> None:
        """警告:优先走 mavis 日志,没有 game 时打 stdout(**不许静默**)。"""
        if self.game is not None and getattr(self.game, "logger", None) is not None:
            self.game.logger.warning(msg)
        else:
            print("[bridge] " + msg, flush=True)

    def _manifest_meta(self) -> dict:
        """运行清单里"这次运行是什么"的**轻量**部分(不含哈希/时间/git)。

        直接用自身状态构造,不经过 `run_record()` —— 而 `run_record()` 会调本方法,
        走 `run_record()` 会自引用递归。
        结果按"决定它的四个量"缓存:`run_record()` 在实时面是每 2 秒被拉一次的
        (见 vizkit/live_run.set_live_provider),不该每拉一次就重算一遍。
        """
        from .manifest import collect_run_meta

        key = (self.branch, self.branch_mode, self.branch_source,
               repr(self.judge_info))
        cached = self._manifest_meta_cache
        if cached and cached[0] == key:
            return dict(cached[1])
        raw = {"branch": self.branch, "branch_mode": self.branch_mode,
               "branch_source": self.branch_source, "judge_info": dict(self.judge_info),
               "mode": "dry-run" if self.dry_run else "mavis"}
        meta = collect_run_meta(raw, branch_mode=self.branch_mode,
                                judge_llm=self.judge_llm,
                                backend_kind=self.backend_kind)
        self._manifest_meta_cache = (key, dict(meta))
        return meta

    def manifest_meta(self) -> dict:
        """给映射路径(pipeline)用的同一份轻量元信息(单一来源)。"""
        return self._manifest_meta()

    def run_record(self) -> dict:
        """本次运行的记录（schema 版本化,便于与 case01 run.json 对齐）。"""
        return {
            "schema_version": "injector-0.1",
            "run_id": self.run_id,
            "mode": "dry-run" if self.dry_run else "mavis",
            "branch": self.branch,
            # 分支从哪来:preset=运行参数指定;judge=由 T0 回答判定(01 §六 的设计原意);
            # preset-fallback=判定失败退回预设(记录里必须看得出来)
            "branch_mode": self.branch_mode,
            "branch_source": self.branch_source,
            "judge_info": dict(self.judge_info),
            # 调试跑标记(空串=正式跑);映射进成品记录的 debug 字段
            "debug": self.debug_note,
            # 运行清单的"轻量"一半(判定后端/分支方式/模型/温度):给映射路径做单一来源,
            # 免得映射器重新猜一遍这次判定到底走的是哪个后端。完整 manifest(含 git/
            # 内容哈希/时间)在落盘时由 manifest.build_manifest 现算,不在这里做。
            "manifest_meta": self.manifest_meta(),
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
        """落盘本次运行的原始记录(含完整运行清单),原子写。

        原子写的原因:原始记录是成品记录的输入,半截 raw.json 会被后续映射当成
        一条完整记录读进去(见 case01/atomicio.py 的模块说明)。
        """
        from ..atomicio import write_json_atomic
        from .manifest import attach_manifest

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        rec = self.run_record()
        meta = rec.get("manifest_meta") or self._manifest_meta()
        attach_manifest(rec, meta)
        write_json_atomic(path, rec)
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

        # assets_root 必须是"相对根"——mavis 契约写明它会拼到 Game.static_root 下。
        # 这里传 "" 而不是 scenario:绝对路径在 POSIX 上会被 mavis 的 _resolve_assets_root
        # (内部 os.path.join(*p.split("/"))) 吃掉前导斜杠、变成相对路径,再被
        # Game.load_static 拼一次 static_root,得到"场景目录+场景目录/maze.json"的翻倍路径。
        # Windows 上绝对路径不含正斜杠、原样通过,所以这个错在开发机上永远看不见
        # (2026-09-18 合并进 main 后首次 CI 红的根因)。static_root 已经是场景目录。
        config = load_config(
            start_time=self._start_time(), stride=0, agents=list(self.roles),
            config_path=config_path, assets_root="",
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
                # [越界·半公开] schedule.daily_schedule 是子对象内部字段。
                # 归属演示/编排层(见 docs/case01_触点白名单.md §〇):只为"节点时刻
                # 需要一段对话"而钉角色同址;只影响是否生成日程,不改 agent 决定。
                # make_schedule() 幂等,重复调用由框架自行判断,无需读内部字段——
                # 但这里需在"未生成"时才调,保留读取以最小化对既有行为的影响。
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
            # [越界·内部状态] 清空运行时路径:没有公开入口,只能直写。
            # 同上属演示/编排层:mavis 的对话前置条件看运行时 path,同址且空路径
            # 才可能触发交互;这项只影响"能否触发对话",不经手 agent 的台词。
            agent.path = []
            cfg = self.config.get("agents", {}).get(name)
            if cfg is not None and coord is not None:
                cfg["coord"] = list(coord)
                cfg["path"] = []

    # ------------------------------------------------------------------
    # 可插拔可视化接线(引擎只产生事件,插件自己画)
    # ------------------------------------------------------------------
    def _build_fanout(self, visualizers):
        """visualizers:[插件实例] / ["town","report","console"] / None(不接)。

        插件故障**不允许静默**:某个插件抛异常时记一行警告(带 traceback),
        否则表现就是"页面/文件里什么都没有,日志里也什么都没有"。
        """
        if not visualizers:
            return None
        from mavis_vizkit import Fanout, create

        instances = [create(v) if isinstance(v, str) else v for v in visualizers]

        def _on_error(name, exc):
            msg = "可视化插件 {} 出错(已隔离): {}: {}".format(
                name, type(exc).__name__, exc)
            game = getattr(self, "game", None)
            if game is not None and getattr(game, "logger", None) is not None:
                game.logger.warning(msg)
            else:
                print("[vizkit] " + msg, flush=True)

        return Fanout(instances, on_error=_on_error)

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

    def _seed_visual_coords(self) -> Dict[str, list]:
        """可视路径的起点 = 场景配置里的初始格子(与前端渲染的初始位置同源)。"""
        try:
            from mavis_vizkit.plugins.town import scenario_coords
            return {k: list(v) for k, v in
                    scenario_coords(self.scenario_dir, self.roles).items()}
        except Exception as e:  # noqa: BLE001 - 拿不到就退化成"第一段走直线",但留痕
            print("[case01] 初始坐标取不到,第一段移动可能仍是直线: {}".format(e), flush=True)
            return {}

    def _visual_path(self, name, dst):
        """给前端的**可视路径**:从上一次画到的格子走到目标格子,走迷宫的正交路线。

        为什么要单独算:mavis 侧 pin 之后 `agent.path` 被清空(同址且静止才可能触发交互),
        前端拿不到路径就只能沿直线滑过去 —— 于是**斜着穿格**(2026-09-19 用户实测反馈
        "有的AI走斜线,而不是横着竖着走的")。这里用迷宫 BFS 补一条正交路径,
        只影响画面,不改 mavis 语义(交互仍按"同址 + 空路径"判定)。

        寻不到路(不可达/同格)返回 None,由调用方回退成直线 —— 但会留一行日志,不静默。
        """
        src = self._last_visual_coord.get(name)
        if dst:
            self._last_visual_coord[name] = list(dst)
        if not src or not dst or list(src) == list(dst):
            return None
        try:
            agents = getattr(self.game, "agents", None) or {}
            agent = agents.get(name)
            if agent is None and hasattr(self.game, "get_agent"):
                agent = self.game.get_agent(name)
            maze = getattr(agent, "maze", None) if agent is not None else None
            if maze is None:
                return None
            path = maze.find_path(list(src), list(dst))
            if path:
                return [[int(c[0]), int(c[1])] for c in path]
            # 不可达:前端只能直线走过去(可能穿墙)——必须留痕,但不重复刷同一对格子
            key = (name, tuple(src), tuple(dst))
            if key not in self._visual_path_warned and self.game is not None:
                self._visual_path_warned.add(key)
                self.game.logger.warning(
                    "visual path: {} 从 {} 到 {} 不可达,前端只能直线走过去".format(
                        name, list(src), list(dst)))
            return None
        except Exception as e:  # noqa: BLE001 - 画不出来不该打断推演,但要说一声
            if self.game is not None:
                self.game.logger.warning(
                    "visual path failed for {} {}->{}: {}".format(name, src, dst, e))
            return None

    def _emit_agent(self, name, state, sim_time):
        """可视化侧:发一条 agent 事件给 fanout(适配器转发;不需要 step)。"""
        if self._fanout is None:
            return
        from mavis_vizkit.events import agent_event, as_text

        state = state or {}
        action = as_text(state.get("action"))
        location = as_text(state.get("location"))
        currently = as_text(state.get("currently"))
        coord = state.get("coord")
        # mavis 自带路径(pin 之外的自然行走)优先用;没有就用可视路径补一条正交的,
        # 否则前端会沿直线斜穿格子。
        path = state.get("path") or []
        if not path:
            path = self._visual_path(name, coord) or []
        else:
            if coord:
                self._last_visual_coord[name] = list(coord)
        self._fanout.emit(agent_event(
            name, coord, action, location, currently,
            path, sim_time, self._role_type(name)))

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

    def close(self, keep_visualizers: bool = False) -> None:
        """收尾:关闭可视化插件,并按所走的路径退订对话接线。

        keep_visualizers=True 时**不关**可视化插件 —— 给"一局跑完接着重开一局"用:
        插件(live 的 HTTP 服务)是跨局共享的,关掉它等于把整个界面弄没
        (2026-09-19 实测:重开一局后 5010 不再监听,页面直接消失)。
        """
        if self._fanout is not None and not keep_visualizers:
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

        `c_plan_file` 给出时**用文件里的方案**(演示/联调用),并在记录里标
        `source="manual"` —— 不许静默:看记录的人必须知道这个方案不是模型给的。
        """
        plan: dict = {}
        if self.c_plan_file:
            try:
                with open(self.c_plan_file, encoding="utf-8") as f:
                    plan = json.load(f)
                plan.setdefault("source", "manual")
                if self.facts is not None:
                    self.facts.set_c_plan(plan)
                    rec["world_state"] = self.facts.state_snapshot()
                    self._node_facts[node.node_id] = {"state": self.facts.state_snapshot()}
                rec["c_plan"] = plan
                self._c_plan = plan
                self._warn("C 方案来自文件 {} (source=manual)".format(self.c_plan_file))
                return
            except Exception as e:  # noqa: BLE001 - 读不了就退回解析,但要说出来
                self._warn("c_plan_file 读不了({}),回退到解析 T0 回答".format(e))
        ai_answer = self._extract_role_answer(rec, self.roles[0])
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
