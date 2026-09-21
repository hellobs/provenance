# case01 侧触点白名单(mavis 接入面)

> **状态**:现行
> **最后核对**:2026-09-21
> **说明**:case01 侧触碰 mavis 半公开面的白名单,越界要有登记
> 2026-09-18 建立。目的:把"case01 允许碰 mavis 的哪些东西"写死,
> 让越界在 review 时可见,而不是靠记忆。
> 对应 mavis 侧文档:`D:\zzr\mavis\docs\tutorial-extension.md`(扩展面),
> 契约测试:`D:\zzr\mavis\tests\test_extension_surface.py`。
>
> 2026-09-21 补充 §二之"导演面 vs 自治面的语义边界"。

## 〇、导演面 vs 自治面:interaction_request 的语义边界(2026-09-21)

**一句话边界**:agent 的"话"永远自治(LLM 生成);外部只能在**演示/编排层**
指定"哪些节点需要发生一段交互、话题是什么(focus)",不能替代 agent 的决定或台词。

**为什么立这条**:case01 有一个正式 Run 路径(`case01/orchestrator.py`,纯流程编排,
零 mavis、零 interaction_request——陈总场景说明要的结构化边界节点 T0/最终反馈 就落在
这里)和一个实时可视化演示路径(`case01/injector/bridge.py`,复现正式 Run 的样子)。
只有**演示路径**为了让"节点时刻有段对话"好看,借用了 mavis 的
`Simulator(interaction_request=)` 触发交互——它是一个**迷你导演器**。

**与 IVD 主张的关系(重要)**:IVD 的核心承诺是"价值判断是 AI 内部的、涌现的;
行为不可控 OK,形成过程必须可审计"。**forced 交互不在 IVD 明文中被等价禁止**,
但它本质是"外部指定谁和谁、在何时、谈什么主题",方向上是排斥 agent 自主的。
IVD 的初心仍保存在正式 Run 路径里(那里没有任何导演);演示路径的导演器是
**可视化复现的工程折衷,不属于 case01 实验本身的心智主张**。因此:

1. `interaction_request` 是**可选、显式 opt-in** 的演示能力,不是 agent 自治的默认;
   不传它时框架行为不变(agent 仍自己决定是否/与谁/谈什么)。
2. 它的职权只到"**话题由外部定**";对话**内容仍由 LLM 生成**,外部不替换 agent 的话。
3. 不能把这条扩展到"直接改 agent 的状态/决定/后果"——那是被禁止的另一类。
4. 正式 Run 记录(`orchestrator` 产出)不含导演操作;演示记录(`bridge` 产出)
   在 `interactions` 里可见是否由节点 `require_interaction` 触发,看记录的人可区分。

## 一、原则

mavis 保持纯洁(零业务词汇、新增能力默认关闭、不改既有语义),case01 享受这块
**已经存在的**面,不靠"再加一个钩子"来扩展空间。遇到新需求按下面的顺序办:

1. 配置能不能解决?(agent.json / maze.json / story 事件 / 参数)
2. 上面白名单里的既有扩展点能不能解决?
3. **case01 自己能不能解决?**(多数情况可以——实验节奏、事实层、事件释放、
   记录映射都在 injector 侧;随机数用全局 `random.seed()` 收敛;不需要动 mavis)
4. 都不行,才考虑向 mavis 提一个通用扩展点(纯新增、默认关闭、语义中立、带单测)。
5. **需要改 mavis 现有行为语义 → 不做**,case01 绕路或降级。

## 二、白名单(允许依赖)

配置层

- `load_config(start_time=, stride=, agents=, config_path=, assets_root=)`
- 角色 `agent.json` 字段:`role_directive`、`coord`、`path`、`think.llm`、
  `transfer.enabled`(默认 False,case01 不开启)
- 环境变量:`MAVIS_CHECKPOINTS_ROOT`、`MAVIS_ASSETS_ROOT`、`MAVIS_CONFIG_PATH`

容器层

- `Game(name, static_root, config, conversation, timer=Timer(...), governance=, consequence_fn=)`
- `Game.agents`(读)、`Game.get_agent(name)`、`Game.reset_game()`、`Game.logger`

调度层

- `Simulator(...)` 全部构造参数,其中 case01 实际用到:
  `external_state=`、`interaction_request=`(**演示/编排层,见 §〇语义边界**)、`on_agent=`、
  `on_step=`、`on_story=`、`max_workers=`、`export_decisions=`、`plugins=`(插件面存在时,把
  `VizForwarder` 适配器挂进 Simulator 插件管理)
- `Simulator.plugin_teardown()`(插件面路径的收尾:`close()` 用它退订对话订阅并 teardown)
- `Simulator.simulate(game, config, step, stride=, start_step=)`
- `Simulator.story`(写:只放当前节点事件)、`Simulator.interactions`(读:交互审计)
- `Simulator.register_condition("<type>")` 与 `Simulator.CONDITION_CHECKERS`

角色层

- `agent.coord`、`agent.path`(写)、`agent.action`、`agent.status`
- `agent.set_step_context()` / `agent.step_context()`
- `agent.request_interaction()`
- `agent.move(coord, path)`、`agent.make_schedule()`、`agent.find_path(agents)`
- `agent.to_dict()`
- `agent.inject_story_event()` / `agent.recent_story_events()`
- `agent.is_awake()`、`agent.llm_available()`

进程级

- `mavisframework.core.agent_core.chat_callback`(实时可视化逐句回调;**仅插件面不存在时**
  的回退路径才直接赋值,见第三节;插件面存在时不再覆盖它)
- `mavisframework.plugin.Plugin`(适配器 `case01/injector/viz_plugin.py` 继承它;
  **可选依赖**,特性探测不到就不加载,见第三节)

## 三、当前触点清单(实际在用,含越界项)

> 行号核对日期:2026-09-18。bridge.py 因阶段 1/2/3 变长,行号会随文件变长漂移;
> 若下方行号与代码对不上,说明本清单已过期,需重新机械核对一遍再更新核对日期。

`case01/injector/bridge.py`

- 252 `_plugin_surface_available()` —— bridge 自己的特性探测;要求 mavis 三个要件齐备
  (`mavisframework.plugin` 可导入 + `agent_core.subscribe_chat_line` + `Simulator` 签名含
  `plugins=`),否则 case01 走回退路径。只读不动框架。
- 251 `game.reset_game()` —— 白名单内,但**语义上是个坑**:provider 在 `Agent.reset()`
  里惰性创建,构造完不调一次就报"缺少可用 LLM"。已在 `tutorial-extension.md` §5 记为
  已知行为。
- 288 `agent_core.chat_callback = ...` —— **仅回退路径**(插件面不存在,即 mavis main)
  才直接赋值全局钩子;`close()`(458/459)`==` 比较后清回。插件面存在时对话经
  `Simulator(plugins=[VizForwarder])` 自动订阅走插件总线,**不覆盖**全局 `chat_callback`,
  多消费者经 `agent_core.subscribe_chat_line` 共存。
- 271/275 `Simulator(..., plugins=[adapter])` —— 插件面路径:把 case01 侧适配器
  `viz_plugin.VizForwarder` 挂进 Simulator 插件管理,由 simulate 首次触发 setup/订阅。
- 451 `simulator.plugin_teardown()` —— `close()` 插件面收尾:退订对话订阅 + teardown 适配器。
- 302 `simulator.story = [...]` —— 白名单内(只放当前节点事件,隔离验证的基础)。
- 340 `game.get_agent(...).coord` —— 白名单内(只读)。
- 347 `agent.schedule.daily_schedule` —— **越界(半公开)**:读了 `schedule` 子对象的
  内部字段,只为判断"日程是否已生成"。
- 348 `agent.make_schedule()`、354 `agent.move(...)` —— 白名单内。
- 359 `agent.path = []` —— **越界(内部状态直写)**:mavis 的对话前置条件看运行时
  `path`,清空路径只能用这种方式。
- 466/467 `simulator.interactions` / `simulator.simulate(...)` —— 白名单内(读交互审计,
  判断关键交互是否发生;case01 自己控制轮次与 stride)。

`case01/injector/conditions.py`

- 15/17 `Simulator.register_condition("case01_node")`(import 在 15,注册在 17) —— 白名单内;
  框架不带业务条件类型,由 case01 在自己进程里注册。

`case01/tools/isolation_probe.py`(仅验证工具,不参与正式运行)

- 74 `agent.associate.memory` / 83 `associate._index.find_node(...)` —— **越界(私有)**:
  为了拿"全量事件记忆"(公开的 `retrieve_events()` 会被 `retention=8` 截断),
  探针直接读了联想记忆的索引。
- 102 `agent.associate.retrieve_events(text=...)` —— 白名单内的公开读侧。

测试专用触点(仅测试,不参与正式运行)

- `case01/tests/test_viz_plugin_wiring.py` 里的
  `agent_core.subscribe_chat_line` / `agent_core.unsubscribe_chat_line` /
  `agent_core._emit_chat_line` —— 只有测试文件直接调用,正式运行不经它们;
  `_emit_chat_line` 是 mavis 侧的单一分发点,验证"多消费者共存、单订阅者抛错隔离、
  退订干净、真实订阅链路"时使用。生产路径下,case01 的对话订阅由 Simulator
  自动挂到 `agent_core`,case01 侧代码不直接调这三个。

可视化(`packages/mavis-vizkit`)

- **不在 mavis 触点里,也不是 mavis 的消费者**:它只消费"事件字典"的键名(契约见其 README),
  运行时零 import mavisframework。case01 通过 `case01/vizkit/live_run.py`(薄 CLI,业务别名
  与前端根留在 case01 侧)与 `bridge.py` 把 roles / alias / scenario / 前端资源根喂给
  `mavis_vizkit.create("live", ...)`。它的契约落在 `mavisframework.runtime.protocol` 的键名,
  靠"协议漂移测试"(测试期 import 框架断言键名仍在)兜住,而不是运行时耦合。

## 四、越界项与收编计划

两处越界都不改语义、只在 case01 侧,先按"记录 + 注释"处理,不急着推给 mavis:

1. `agent.schedule.daily_schedule`(bridge 347):改为**不读内部字段**——
   直接调 `agent.make_schedule()` 让 mavis 自己判断是否需要生成(重复调用应由框架幂等),
   或改用公开的日程读取方法(若后续补上)。
2. `agent.path = []`(bridge 359):与 `agent.move(coord, [])` 语义重叠,先确认
   `move(coord, [])` 是否已经清空路径;若已清空就删掉这行。
3. 探针的 `associate.memory` / `_index`:属于**验证工具**的取证需要。要么向 mavis 提一个
   通用只读方法(例如"导出全部事件记忆"),要么在文档里写明"探针允许越界,但结论必须
   能被公开检索侧复现"(现在的结论确实有公开侧证据:60 次主动检索 0 泄漏)。

收编时机:mavis 下一次动扩展面时一起做,避免为这点小事单独改框架。

## 五、如实记录的限制

- **运行不可逐样本复现**:mavis 用全局 `random` 且无 seed 参数,`Agent.think` 走线程池,
  跨角色消费随机顺序不确定。case01 侧 `random.seed(n)` 只能收敛,不能保证。
  对外不要说"可复现",只能说"同配置下稳定产出同量级结果"。
- **`agent.action` 是 dict**:`AgentState.action` 落库是 `action.to_dict()`,
  前端按字符串用会炸(已在 `mavis_vizkit.events.as_text()` 收敛)。记录里也按文本存。
- **23:00 后不发起对话**:必须显式注入按首节点日期起算的 `Timer`,否则运行成败取决于真实时间。

## 六、怎么快速核对有没有新增越界

```
# 真 import mavis 的位置(应只出现在 bridge / viz_plugin / conditions 三个文件,
# 都在 injector/ 下;肉眼核对 import 后的调用是否符合白名单)
rg -n "import mavisframework|from mavisframework" provenance/case01/injector provenance/case01/tools --glob "*.py"

# 触及 mavis API 的全部引用点(按白名单逐条比对;注意探针在 tools/ 下,不在 injector/)
rg -n "agent\.|game\.|simulator\.|\.associate" provenance/case01/injector provenance/case01/tools --glob "*.py"

# 看框架有没有被污染(应无输出)
cd D:/zzr/mavis && git grep -in "case01\|case 01\|ethan\|investment ai\|hcm" -- mavisframework

# 契约有没有被破坏
cd D:/zzr/mavis && python -m pytest tests/test_extension_surface.py -q
```

两条 rg 的命令命中里,哪些是**真触点**、哪些只是**文档串/注释提及**(非触点,不必算越界):

- 真触点(会 import 或调用 mavis):
  1. `injector/bridge.py` —— 特性探测 + 懒加载构造 Game/Simulator/agent_core(第三节已逐行列);
  2. `injector/viz_plugin.py` —— 接入插件面:继承 `mavisframework.plugin.Plugin`(仅插件面存在时);
  3. `injector/conditions.py` —— 注册 `Simulator.register_condition("case01_node")`(仅真实运行时);
  4. `tools/isolation_probe.py`(探针,在 `tools/` 下,不是 injector/)—— 不直接 import
     mavisframework,而是操作注入进来的 `game.simulator/agent` 对象(**越界**取证,见第三节)。
- 仅是文档串/注释提及(非触点):`injector/__init__.py:11`、`bridge.py:10/218` 的 docstring、
  `viz_plugin.py:8`、`conditions.py:5` 的注释里出现 "mavisframework" 字样,描述依赖关系但
  自身不调用框架。

新增了白名单以外的触点,就在本文件第三节补一行,并说明为什么绕不开。
