# case01 侧触点白名单(mavis 接入面)

> 2026-09-18 建立。目的:把"case01 允许碰 mavis 的哪些东西"写死,
> 让越界在 review 时可见,而不是靠记忆。
> 对应 mavis 侧文档:`D:\zzr\mavis\docs\tutorial-extension.md`(扩展面),
> 契约测试:`D:\zzr\mavis\tests\test_extension_surface.py`。

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
  `external_state=`、`interaction_request=`、`on_agent=`、`on_step=`、`on_story=`、
  `max_workers=`、`export_decisions=`
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

- `mavisframework.core.agent_core.chat_callback`(实时可视化逐句回调)

## 三、当前触点清单(实际在用,含越界项)

`case01/injector/bridge.py`

- 217 `game.reset_game()` —— 白名单内,但**语义上是个坑**:provider 在 `Agent.reset()`
  里惰性创建,构造完不调一次就报"缺少可用 LLM"。已在 `tutorial-extension.md` §5 记为
  已知行为。
- 231–233 `agent_core.chat_callback = ...` —— 白名单内的进程级钩子;
  注意它是**全局单例**,同进程只能有一个消费者(与 provenance 实时服务共用时要协调)。
- 248 `simulator.story = [...]` —— 白名单内(只放当前节点事件,隔离验证的基础)。
- 279–282 `game.agents` / `game.get_agent(...).coord` —— 白名单内(只读)。
- 286 `agent.schedule.daily_schedule` —— **越界(半公开)**:读了 `schedule` 子对象的
  内部字段,只为判断"日程是否已生成"。
- 293 `agent.move(...)`、287 `agent.make_schedule()` —— 白名单内。
- 297 `agent.path = []` —— **越界(内部状态直写)**:mavis 的对话前置条件看运行时
  `path`,清空路径只能用这种方式。
- 373/378 `simulator.interactions` —— 白名单内(读交互审计,判断关键交互是否发生)。
- 374 `simulator.simulate(...)` —— 白名单内(case01 自己控制轮次与 stride)。

`case01/tools/isolation_probe.py`(仅验证工具,不参与正式运行)

- 67–77 `agent.associate.memory` / `associate._index.find_node(...)` —— **越界(私有)**:
  为了拿"全量事件记忆"(公开的 `retrieve_events()` 会被 `retention=8` 截断),
  探针直接读了联想记忆的索引。
- 96 `agent.associate.retrieve_events(text=...)` —— 白名单内的公开读侧。

`case01/injector/conditions.py`

- 15–20 `Simulator.register_condition("case01_node")` —— 白名单内;框架不带业务条件类型,
  由 case01 在自己进程里注册。

## 四、越界项与收编计划

两处越界都不改语义、只在 case01 侧,先按"记录 + 注释"处理,不急着推给 mavis:

1. `agent.schedule.daily_schedule`(bridge 286):改为**不读内部字段**——
   直接调 `agent.make_schedule()` 让 mavis 自己判断是否需要生成(重复调用应由框架幂等),
   或改用公开的日程读取方法(若后续补上)。
2. `agent.path = []`(bridge 297):与 `agent.move(coord, [])` 语义重叠,先确认
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
  前端按字符串用会炸(已在 `vizkit.events.as_text()` 收敛)。记录里也按文本存。
- **23:00 后不发起对话**:必须显式注入按首节点日期起算的 `Timer`,否则运行成败取决于真实时间。

## 六、怎么快速核对有没有新增越界

```
# 看 case01 碰 mavis 的全部位置(应只出现在 bridge / conditions / 探针 三处)
rg -n "mavisframework|agent\.|game\.|simulator\." provenance/case01/injector --glob "*.py"

# 看框架有没有被污染(应无输出)
cd D:/zzr/mavis && git grep -in "case01\|case 01\|ethan\|investment ai\|hcm" -- mavisframework

# 契约有没有被破坏
cd D:/zzr/mavis && python -m pytest tests/test_extension_surface.py -q
```

新增了白名单以外的触点,就在本文件第三节补一行,并说明为什么绕不开。
