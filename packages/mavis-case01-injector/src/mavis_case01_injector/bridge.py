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
from typing import Any, Dict, List, Optional, Tuple

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


def _red_text(text):
    """染成红色亮字;stdout 被重定向进日志文件时**不上色**(见 provider 侧同名函数)。"""
    import sys as _sys
    try:
        if not _sys.stdout.isatty():
            return text
    except Exception:  # noqa: BLE001
        return text
    return "\x1b[1;31m" + text + "\x1b[0m"


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
        use_case01_facts: bool = True,
        meeting_coord: Optional[List[int]] = None,
        c_plan_llm: Optional[object] = None,
        visualizers: Optional[List[object]] = None,
        c_plan_file: str = "",
        debug_note: str = "",
        node_cap: int = 0,
        judge_llm: Optional[object] = None,
        backend_kind: str = "",
        think_workers: int = 0,
        language: str = "en",
    ):
        self.nodes = list(nodes or [])
        # 调用方给的序列长度就是它要的步数(冒烟/收时间窗用的截断也在这里)。
        # judge 模式下换时间线时要按它重新截,否则"只跑前 N 个节点"会被换回完整序列。
        #
        # ⚠ 2026-10-10 修:剔除预设分支后,判定前的种子节点改成 **只排 T0 那天**
        # (`default_nodes("undetermined")`),于是 `len(self.nodes)` 只有 1~2 —— 而
        # 判定后 `_decide_branch_from_t0` 会用 `self._node_cap` 把换回来的完整时间线
        # **砍到只剩 1~2 个节点**,表现为"推演半分钟就结束、0/2 节点"。
        # 所以:**显式传 node_cap 才截断**;0 = 不截断(默认)。
        self._node_cap = max(0, int(node_cap))
        self.roles = tuple(roles)
        # 并行思考线程数。0 = 按角色数(今天的行为)。
        # 为什么这跟"可复现"有关:引擎那 15 个 `random.*` 走的是**进程全局** RNG,
        # 多个 agent 同时 think 时,数流是固定的(种子链已钉),但**谁拿到哪个数**
        # 取决于线程调度 ⇒ 位置/目的地/retry_prob 会在角色之间串味。
        # 设成 1 才让"同一个种子"落到同一个角色的同一次抽样上。
        self.think_workers = max(0, int(think_workers or 0))
        self.scenario_dir = scenario_dir
        from .language import check_language
        self.language = check_language(language)
        self.run_id = run_id
        self.max_retries = int(max_retries)
        self.dry_run = bool(dry_run)
        self.anchor_coord = list(anchor_coord) if anchor_coord else None
        # 分支**只能由 T0 回答判定**得出(2026-10-10 用户:"绝对不允许预设,预设的板块
        # 全部剔除")。判定之前没有分支 —— 不给默认值、不接受调用方传入。
        self.branch = "undetermined"
        # 判定后端(**只用于运行清单,不改变判定行为**):真跑时 judge 走本地 Ollama;
        # 显式传 judge_llm/backend_kind 时按它记(测试替身、外部 API、规则判定都能如实落账)。
        self.judge_llm = judge_llm
        self.backend_kind = backend_kind or ""
        # 运行清单轻量部分的缓存(键 = 决定它的那几个量),避免每步重算
        self._manifest_meta_cache: Optional[tuple] = None
        # T0 跑完之前**不能**自称已判定 —— 否则记录会显示一个来路不明的分支来源。
        # 空串 = 待判定;判定成功后恒为 "judge"(失败为 "judge-failed")。
        self.branch_source = ""
        self.judge_info: Dict[str, object] = {}
        # Ethan 这一局实际用的后端(api(...) / local(...));写进 run_record,事后可查
        self.ethan_backend = "unknown"
        self.finish_reason = ""
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
        self._background_provider = None
        self._background_provider_loaded = False
        self._current_retrievals: List[dict] = []

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
        self._current_retrievals = []
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
        self._retrieve_background(node)

    def _retrieve_background(self, node: NodeSpec) -> None:
        """Retrieve at consultation nodes; only the assistant sees the passages."""
        if not self._background_provider_loaded:
            from ._providers import background_retrieval_provider
            self._background_provider = background_retrieval_provider(
                embed_fn=None if self.dry_run else getattr(self.judge_llm, "embed", None),
                use_default_embed=not self.dry_run)
            self._background_provider_loaded = True
        provider = self._background_provider
        if provider is None:
            return
        assistant = self.roles[0]
        for request in self._current_requests:
            query = str(request.get("focus") or "").strip()
            if request.get("to") != assistant or not query:
                continue
            provider.retrieve(query, node.date, background_only=True)
            audit = dict(provider.last_retrieval, mode="background_retrieval",
                         query_source="node_focus", recipient=assistant)
            self._current_retrievals.append(audit)
            context = self._current_context[assistant]
            text = audit["context"]
            context["Financial Data background"] = (
                context.get("Financial Data background", "") + "\n" + text).strip()

    def run(self) -> dict:
        """按节点推进,返回本次运行的记录。

        **分支只能由判定得出**(2026-10-10 用户:"绝对不允许预设,预设的板块全部剔除"):
        先跑 T0(咨询当天),用 Investment AI 在 T0 的实际回答判定本次进入 A/B/C,
        再按该分支的时间线跑完其余节点(市场世界仍不由模型临时生成)。
        没有判定就没有分支:判不出来时 `finish_reason="branch_undetermined"`,
        不给默认值、不静默挑一条。
        `dry_run` 下除非注入了 `judge_llm`(测试替身),否则同样判不了 → 停在 T0。
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
                "background_retrievals": list(self._current_retrievals),
            })

            # 分支判定:T0 落地后立刻判定,然后换成该分支的后续节点重排。
            # 没有判定就没有分支 —— dry_run 且没注入 judge_llm 时不判定(不调真 LLM),
            # 分支保持 undetermined,下面按既有语义收尾。
            if step_index == 0 and (self.judge_llm is not None or not self.dry_run):
                self._decide_branch_from_t0(self.records[-1], t0_node=node)
                nodes = list(self.nodes)
                if self.branch == "undetermined":
                    self.finish_reason = "branch_undetermined"
                    break
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
        from mavis_case01_injector.llm import local_client_from_env

        # LLMBranchJudge owns all three attempts; avoid multiplying retries
        # inside the transport client.
        client = local_client_from_env(retries=1)
        # 把**真正拿去判定的那个客户端**记回 self.judge_llm:清单的 seed /
        # judge_model / temperature 全是从它身上读的。此前起面路径不注入 judge_llm,
        # 于是这三项落的是常量默认值(记录里 `seed: null`,而请求体其实带着种子)
        # —— 2026-10-06 小镇三条实测皆如此。声明式探测的口径不变:只认客户端自报。
        if self.judge_llm is None:
            self.judge_llm = client
        return client

    def _decide_branch_from_t0(self, rec: dict, t0_node: NodeSpec) -> str:
        """用 Investment AI 在 T0 的实际回答判定分支(01 §六),并据此重排后续节点与事实层。

        If classification fails, stop after T0 and require manual selection or rerun.
        """
        answer = self._extract_role_answer(rec, self.roles[0])
        if not answer:
            self.branch = "undetermined"
            self.branch_source = "judge-failed"
            self.judge_info = {"detected": "undetermined",
                               "reason": "T0 has no Investment AI answer",
                               "attempts": 0, "raw_outputs": []}
            self.nodes = [t0_node]
            self._warn("judge: T0 has no Investment AI answer; timeline stopped")
            return self.branch
        from mavis_case01_injector.world.branch import LLMBranchJudge

        llm = self._judge_client()
        try:
            detected, info = LLMBranchJudge(llm, language=self.language).judge(answer)
        except Exception as e:  # noqa: BLE001 - 判定失败也要留痕,不静默
            detected = "undetermined"
            info = {"branch": detected, "reason": "judge failed: {}".format(e),
                    "attempts": 3, "raw_outputs": []}
        if detected == "undetermined":
            self.branch = detected
            self.branch_source = "judge-failed"
            self.judge_info = dict(info, detected=detected)
            self.nodes = [t0_node]
            self._warn("judge: {}; timeline stopped for manual selection or rerun".format(
                info.get("reason", "undetermined")))
            return self.branch
        self.branch = detected
        self.branch_source = "judge"
        self.judge_info = {"detected": detected, "reason": info.get("reason", ""),
                           "judge": info.get("judge", "llm"),
                           "answer_head": answer[:200],
                           # 重试与原始输出(2026-09-25 第十四轮体检):判定**成功**时
                           # 以前只留最终结果,把 `attempts`/`raw_outputs` 丢掉了 ——
                           # 而失败路径反而留着。判错时最需要的恰恰是"试了几次、
                           # 模型原样回了什么"(LLMBranchJudge 本来就返回了这两个字段)。
                           # 实验自变量(走哪条线)的判定要能事后复核,不能只留一句理由。
                           "attempts": info.get("attempts", 1),
                           "raw_outputs": list(info.get("raw_outputs") or [])}
        # 后续节点换成该分支的时间线(T0 已经跑过,从第 2 个节点接着跑)
        rebuilt = [t0_node] + self._nodes_for(detected)[1:]
        # 换完必须按调用方给的长度重新截,否则 `--nodes N`(冒烟/收时间窗那条)
        # 在默认 judge 模式下被这句话整个换回完整序列 —— 2026-10-06 实测:按 4 个
        # 节点提交,记录里是 7 个节点、~397 秒,而 live_run 还打印了"已截断 7→4"。
        cap = self._node_cap
        self.nodes = rebuilt[:cap] if 0 < cap < len(rebuilt) else rebuilt
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

        key = (self.branch, self.branch_source, self.language,
               repr(self.judge_info))
        cached = self._manifest_meta_cache
        if cached and cached[0] == key:
            return dict(cached[1])
        raw = {"branch": self.branch,
               "language": self.language,
               "branch_source": self.branch_source,
            "ethan_backend": self.ethan_backend, "judge_info": dict(self.judge_info),
               "mode": "dry-run" if self.dry_run else "mavis"}
        # 小镇这一局的对话/反思走的是挂在各 agent 上的 `Case01SafeProvider`,把它们
        # 一起交给清单:否则清单只看得见判定那一个客户端 —— 起面实测过两次后果:
        # `manifest.seed=null`(而每个请求体都带着 seed)、`truncations=0`(而 stdout
        # 明明打过头截断)。与批路径同一口径(`orchestrator` 就是这样收 llms 的)。
        town: Dict[str, Any] = {}
        game = getattr(self, "game", None)
        for _name, _agent in (getattr(game, "agents", None) or {}).items():
            _llm = getattr(_agent, "_llm", None)
            if _llm is not None:
                town["小镇 {}".format(_name)] = _llm
        meta = collect_run_meta(raw,
                                judge_llm=self.judge_llm,
                                backend_kind=self.backend_kind,
                                llms=town or None)
        # 兜底值统计(2026-10-10 用户:"还有哪些地方出现 failsafe!!!"):
        # mavis 的 prompt 库里有 20+ 处 failsafe,命中时会返回**看起来正常的一句话**
        # ("X 说的话没有得到回应" / "X 进行了一次对话"),落进产物就等于凭空造话。
        # 这里按调用点把命中次数汇总进清单 —— 兜底不再是看不见的事。
        _fs = {}
        for _c in list(town.values()) + ([self.judge_llm] if self.judge_llm else []):
            for _k, _v in (getattr(_c, "summary", None) or {}).items():
                if isinstance(_v, list) and len(_v) >= 3 and _v[2]:
                    _fs[_k] = _fs.get(_k, 0) + int(_v[2])
        meta["failsafe_by_caller"] = _fs
        meta["failsafe_total"] = sum(_fs.values())
        if _fs:
            msg = ("本次运行有 {} 次调用落到兜底值(不是模型说的): {}".format(
                meta["failsafe_total"],
                ", ".join("{}={}".format(k, v) for k, v in sorted(_fs.items()))))
            meta.setdefault("warnings", []).append(msg)
            print("[case01] [warn] " + msg, flush=True)
        self._manifest_meta_cache = (key, dict(meta))
        return meta

    def manifest_meta(self) -> dict:
        """给映射路径(pipeline)用的同一份轻量元信息(单一来源)。"""
        return self._manifest_meta()

    def run_record(self) -> dict:
        """本次运行的记录（schema 版本化,便于与 case01 run.json 对齐）。"""
        return {
            "schema_version": "injector-0.1",
            "language": self.language,
            "run_id": self.run_id,
            "mode": "dry-run" if self.dry_run else "mavis",
            "branch": self.branch,
            # 分支从哪来:恒为 judge(由 T0 回答判定,01 §六 的设计原意)/ judge-failed。
            # **没有 preset** —— 2026-10-10 用户要求剔除全部预设分支的口子。
            "branch_source": self.branch_source,
            "ethan_backend": self.ethan_backend,
            "judge_info": dict(self.judge_info),
            "finish_reason": self.finish_reason,
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
        from mavis_case01_injector.atomicio import write_json_atomic
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
        from .language import apply_role_text
        apply_role_text(config, self.roles, self.language)
        self._apply_local_provider(config)
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
        self._install_safe_mavis_providers()
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
            max_workers=self.think_workers or max(1, len(self.roles)),
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
        """强制交互前:清空路径、必要时补日程 —— **默认不再把两人钉到同一格**。

        为什么不再钉同址(2026-10-10):`meeting_coord` 未显式给出时,原实现把两个角色
        一起 `move` 到 `roles[0]` 所在的格子,于是场景配置里的初始距离
        (`injector/scenario/agents/<role>/agent.json` 的 `coord`)在第一个交互节点就被抹掉 ——
        画面上两人从一开始就是叠在一起的,而且相向而行时互相推,
        出现"一个人一直在原地踏步/反过来走"。

        查实 mavis 的对话前置条件(`Agent._chat_with_locked`)只要求:
          ①双方已生成 daily_schedule;②`_skip_react` 为假(非睡眠 / 非"待开始");
          ③`other.path` 为空;④当前不在"对话"事件里;⑤冷却(forced 时跳过)。
        **没有一条与"是否同一格"有关** —— 同址从来不是必要条件,原注释把
        "运行时状态"读窄了。真正要摆的只有日程与空路径,下面照旧做。
        调用方显式传了 `meeting_coord`(就是要"在某处碰面")时,仍然照它 move。

        越界声明（见 `docs/case01_触点白名单.md` 第三、四节）:下面两处碰了 mavis 的
        半公开/内部状态,暂留并已登记收编计划:
        - 读 `agent.schedule.daily_schedule` 只为判断"日程是否已生成";
        - 直写 `agent.path = []`,因为清空运行时路径没有公开入口。
        两处都只影响 case01 自己的进程,不改 mavis 语义;收编放到 mavis 下次动扩展面时。
        """
        coord = self.meeting_coord      # 显式指定碰面位置时才动位置
        for name in self.roles:
            agent = (self.game.agents or {}).get(name)
            if agent is None:
                continue
            try:
                # [越界·半公开] schedule.daily_schedule 是子对象内部字段。
                # 归属演示/编排层(见 docs/case01_触点白名单.md §〇):只为"节点时刻
                # 需要一段对话"而保证日程已生成;只影响是否生成日程,不改 agent 决定。
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
            # mavis 的对话前置条件看运行时 path,清空才可能触发交互;
            # 这项只影响"能否触发对话",不经手 agent 的台词。
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

    def _blocked_by_agents(self, name: str) -> set:
        """当前**别的角色**正站着的格子,给可视路径当临时障碍。

        2026-10-10 用户指出:"走不到对应的位置因为有人挡住,BFS 这里换条路不就好了吗"。
        查实:引擎自己的 `Agent.find_path` 早就把"别人站的格子"当临时障碍了
        (`mavisframework/core/agent_core.py` 的 `_blocked_by_agents` / `_next_step_blocked`),
        但 case01 实时面走的是本模块的 `_visual_path` —— 它原来是**不带 blocked 的裸 BFS**,
        于是前端拿到的路径会笔直撞进人身上(实测两人同向走同一段时,后面那个一路顶住前面
        那个的背,表现为"一直在走却被挡住")。这里补齐同一口径:绕开别人,而不是硬顶。
        """
        blocked = set()
        for other, coord in (self._last_visual_coord or {}).items():
            if other != name and coord:
                blocked.add(tuple(coord))
        return blocked

    def _visual_path(self, name, dst):
        """给前端的**可视路径**:从上一次画到的格子走到目标格子,走迷宫的正交路线。

        为什么要单独算:mavis 侧 pin 之后 `agent.path` 被清空(不 pin 时也常为空),
        前端拿不到路径就只能沿直线滑过去 —— 于是**斜着穿格**(2026-09-19 用户实测反馈
        "有的AI走斜线,而不是横着竖着走的")。这里用迷宫 BFS 补一条正交路径,
        只影响画面,不改 mavis 语义(交互仍按"空路径"判定)。

        绕人:把别的角色当前站的格子当临时障碍(见 `_blocked_by_agents`)。
        全被挡死时退回不带 blocked 的 BFS —— 与引擎 `Agent.find_path` 同一套兜底,
        宁可挤过去也别僵住。

        返回值(=给前端的走法):
          `[]`   无需移动(已经在目标格 / 还没有起点 / 拿不到迷宫);
          `path` 一条正交路径(逐格);
          `None` **真的走不过去** —— 调用方据此让角色**停在原地**
                (2026-10-10 用户:"要是实在过不去,BFS 都给出无解了那就应该要停下!")。
                此前这里返回 None 后前端会退化成"沿直线滑到目标格",那会穿墙;
                而且 `src` 还没算出来就先把 `_last_visual_coord` 记成了目标格 ——
                一次走不通,后面每一步的起点都是错的。现在**走得到才更新**起点。
        """
        src = self._last_visual_coord.get(name)
        if not dst:
            return []
        if not src or list(src) == list(dst):
            self._last_visual_coord[name] = list(dst)
            return []
        try:
            agents = getattr(self.game, "agents", None) or {}
            agent = agents.get(name)
            if agent is None and hasattr(self.game, "get_agent"):
                agent = self.game.get_agent(name)
            maze = getattr(agent, "maze", None) if agent is not None else None
            if maze is None:
                return []
            blocked = self._blocked_by_agents(name)
            path = maze.find_path(list(src), list(dst), blocked=blocked) if blocked else None
            if not path:
                # 没绕开(全被堵死,或绕路反而算不出):退回裸 BFS
                path = maze.find_path(list(src), list(dst))
            if path:
                self._last_visual_coord[name] = list(dst)   # 走得到才更新"当前所在格"
                return [[int(c[0]), int(c[1])] for c in path]
            # 真的无解:不动 `_last_visual_coord`(起点保持原样),返回 None 让调用方停下。
            # 必须留痕但不重复刷同一对格子。
            key = (name, tuple(src), tuple(dst))
            if key not in self._visual_path_warned and self.game is not None:
                self._visual_path_warned.add(key)
                self.game.logger.warning(
                    "visual path: {} 从 {} 到 {} 无解(BFS 绕不开),停在原地".format(
                        name, list(src), list(dst)))
            return None
        except Exception as e:  # noqa: BLE001 - 画不出来不该打断推演,但要说一声
            if self.game is not None:
                self.game.logger.warning(
                    "visual path failed for {} {}->{}: {}".format(name, src, dst, e))
            return []

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
            path = self._visual_path(name, coord)
            if path is None:
                # BFS 无解 ⇒ **停在原地**(2026-10-10 用户要求)。把 coord 换成
                # "最后一次真正走到的那一格",前端 moveAgent 就地认为已到达,
                # 一步也不会走 —— 此前这里退化成沿直线滑过去,会穿墙。
                held = self._last_visual_coord.get(name)
                if held:
                    coord = list(held)
                path = []
            else:
                path = path or []
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
            from mavis_case01_injector.world.branch import ConditionPlanParser

            llm = self.c_plan_llm
            if llm is None:
                from mavis_case01_injector.llm import local_client_from_env

                llm = local_client_from_env()
            try:
                plan = ConditionPlanParser(llm, language=self.language).parse(ai_answer)
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
        """Ethan 走外部 API 时,从环境变量注入（密钥不进仓库、不进记录）。

        **硬失败,绝不悄悄退回本地模型**(2026-10-10 用户要求)。以前这里是
        "配置不全 ⇒ 直接 return",于是 Ethan 落回 mavis 的默认本地 provider ——
        台词还是本地模型写的,但从产物上完全看不出来。
        现在:
          · 只配了一半(BASE_URL / MODEL 其一)⇒ 报错停这一局(是配漏了,不是选择本地);
          · 配全了但起面探测不通 ⇒ 报错停这一局,**不**退回本地;
          · 一个都没配 ⇒ 仍按"就是要本地"处理,但把实际后端写进 run_record,
            事后一眼能看出 Ethan 是谁写的。
        两种写法(10-10 统一成一张后端表,见 `llm.ROUTER_PROVIDERS`):
          · `CASE01_ETHAN_PROVIDER=deepseek|bigmodel|openrouter|vllm` —— 端点/模型/key
            都按那张表取(`--show` 打得出来),要换的只有一家时不必再抄三行;
            `CASE01_ETHAN_BASE_URL`/`_MODEL`/`_API_KEY` 仍可逐项覆盖,优先级最高。
          · 只给那三个 `CASE01_ETHAN_*` 而不给 provider —— 老写法,行为不变。
        """
        base_url = os.environ.get("CASE01_ETHAN_BASE_URL", "").strip()
        model = os.environ.get("CASE01_ETHAN_MODEL", "").strip()
        key = os.environ.get("CASE01_ETHAN_API_KEY", "").strip()
        provider = os.environ.get("CASE01_ETHAN_PROVIDER", "").strip().lower()
        if provider:
            from .llm import LOCAL_ROUTER_PROVIDERS, provider_endpoint, provider_key
            if provider in LOCAL_ROUTER_PROVIDERS:
                # 显式点名"这次 Ethan 就要本地":与"没配"区分,但走的是同一条如实记录
                self.ethan_backend = "local(CASE01_ETHAN_PROVIDER={})".format(provider)
                return
            # router_store=False:`.secrets.json` 那两个 router_* 覆盖键是给 N7 的,
            # 不该决定 Ethan 的台词由谁写(否则"给 Router 换个端点"会连 N3 一起换掉)。
            base_url, model = provider_endpoint(provider, base_url, model,
                                                router_store=False)
            key = key or provider_key(provider)
            if not key:
                raise RuntimeError(
                    "CASE01_ETHAN_PROVIDER={} 但取不到 key:设 CASE01_ETHAN_API_KEY,"
                    "或用 tools/setup_api.py --router {} --key <key> 把它写进 "
                    ".secrets.json(已 gitignore;不打印 key)".format(provider, provider))
        if not (base_url or model):
            self.ethan_backend = "local(未配 CASE01_ETHAN_* ⇒ Ethan 走本地模型)"
            return
        if not (base_url and model):
            missing = "CASE01_ETHAN_MODEL" if model else "CASE01_ETHAN_BASE_URL"
            raise RuntimeError(
                "Ethan 的外部 API 只配了一半(缺 {}):配漏了不等于选择本地。"
                "要么两个都配齐,要么都不配;或只点 CASE01_ETHAN_PROVIDER=<后端名>,"
                "端点与模型名按那张表取。".format(missing))
        # 起面探测:**重试几次**;仍不通则**报警但继续**。
        # 为什么不再直接杀服务(2026-10-10 用户"为什么现在启动不了 5010 了"):
        # 一次探测失败就把整局连同 5010 一起关掉,等于"网络抖一下 → 整个平台打不开",
        # 代价远大于收益。真正的错误信息由**第一次真实调用**给出(比我的探测准)。
        # ⚠ 底线不变:绝不悄悄退回本地模型 —— 探测不通照样注入外部 provider,
        # 调用失败会大声报错,不会换成 local。
        if not self._remote_reachable(base_url):
            print(_red_text(
                "[case01] [warn] Ethan 的外部 API({})起面探测不通;"
                "仍然使用外部 provider(绝不退回本地),真实错误会在第一次调用时出现。"
                "若网络不通请检查本机出网/代理。".format(base_url)), flush=True)
        target = config.get("agents", {}).get(self.roles[1])
        if target is None:
            raise RuntimeError("场景里找不到第二个角色,无法给它配外部 API")
        target.setdefault("think", {})["llm"] = {
            "provider": "openai",
            "model": model,
            "base_url": base_url,
            "api_key": key,
        }
        self.ethan_backend = "api({}@{})".format(model, base_url)

    @staticmethod
    def _remote_reachable(base_url: str, timeout: float = 8.0, retries: int = 3) -> bool:
        """起面**探测**外部 API 是否可达(不发真实请求,只连握手)。

        为什么要在起面就探:Ethan 的台词是实验自变量,写它的模型必须可查。
        放到第一次调用才发现连不上,那一局已经跑掉一大半了,只会留下半截记录。
        """
        import urllib.request
        import urllib.error
        url = base_url.rstrip("/") + "/models"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        import time as _t
        for attempt in range(1, max(1, retries) + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout):
                    return True
            except urllib.error.HTTPError:
            # 有响应也算"可达"(401/404 说明服务在,只是这个路径/密钥不对 —— 同样该硬失败,
            # 但那属于"调不通",不是"连不上")
                return True
            except Exception:
                if attempt < max(1, retries):
                    _t.sleep(2.0)      # 网络抖动是常见的,别一次就判死刑
        return False

    def _apply_local_provider(self, config: dict) -> None:
        """Optionally switch the local backend; preserve scenario defaults otherwise."""
        provider = os.environ.get("CASE01_LLM_PROVIDER", "").strip().lower()
        if not provider:
            return
        if provider not in ("ollama", "vllm"):
            raise ValueError("CASE01_LLM_PROVIDER must be ollama or vllm")
        base_url = os.environ.get("CASE01_LLM_BASE_URL", "").strip()
        model = os.environ.get("CASE01_LLM_MODEL", "").strip()
        if not (base_url and model):
            raise ValueError("CASE01_LLM_BASE_URL and CASE01_LLM_MODEL are required")
        if provider == "ollama" and not base_url.rstrip("/").endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"
        llm = dict(config.get("agent_base", {}).get("think", {}).get("llm", {}))
        # vLLM is OpenAI-compatible, so upstream Mavis needs no vLLM-specific provider.
        mavis_provider = "openai" if provider == "vllm" else "ollama"
        llm.update({"provider": mavis_provider, "base_url": base_url, "model": model,
                    "api_key": os.environ.get("CASE01_LLM_API_KEY", "").strip()})
        config.setdefault("agent_base", {}).setdefault("think", {})["llm"] = llm

    def _install_safe_mavis_providers(self) -> None:
        """Install case-local validation without modifying upstream Mavis."""
        from mavis_case01_injector._providers import import_mavis_provider
        _mp = import_mavis_provider()
        Case01SafeProvider = getattr(_mp, "Case01SafeProvider", None) if _mp else None
        if Case01SafeProvider is None:      # 裸包:不包 case01 安全层,原样使用
            return

        for agent in self.game.agents.values():
            # Preserve explicitly injected providers used by tests or extensions.
            if type(agent._llm).__module__ != "mavisframework.runtime.llm_providers":
                continue
            agent._llm = Case01SafeProvider(agent.think_config["llm"])
