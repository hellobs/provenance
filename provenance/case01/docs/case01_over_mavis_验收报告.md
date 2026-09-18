# Case01 over MAVIS · 阶段 3 验收报告

> 日期：2026-09-16 · 复核更新：2026-09-18（第 1/2/3/5 节补端到端与隔离实测）
> 分支：`feat/case01-injector`（引擎侧）/ `feat/generic-injection-hooks`（mavis 侧）
> 依据：`case01_over_mavis_设计说明.md` §8、`case01_over_mavis_执行计划.md` 阶段 3

## 1. 字段级对比（通过）

- 映射层 `record.py` 输出 case01 `run.json` 的全部顶层键；`compat.missing_keys` 为空。
- 与仓库中真实 `runs/demo-3/run.json` 做顶层键交叉校验，测试通过（demo 不入库时自动跳过）。
- 修正版整跑（B 线 7 节点，真实 Ollama）实测记录：`turns` 5、`events` 12、`state_history` 7、`audit` 22、`reflection` 5154–5590 字、Router 拆出问题 5–7 个。
- **端到端复核（2026-09-18）**：把 B 线实跑记录直接走 `pipeline --from-record --reflect`
  （原始记录 → 映射 → Reflection/Router）得到 17 个顶层键、`compat.gaps` 与
  `compat.missing_keys` 均为空、8 turns / 12 events / 7 retrievals / 22 audit /
  reflection 5167 字 / Router 4 个问题。Router 问题字段与旧引擎逐键一致
  （`id` / `summary` / `field` / `risk` / `routing_reason`）。
- **同日修正一处映射偏差**：`branch_action.timeline` 原来照抄分支号，而 Branch C 走的是
  Timeline A（旧 `demo-3` 实测 `timeline="A"`，`BRANCH_TO_TIMELINE={A:A,B:B,C:A}`）。
  已改为按该映射取值并补测试；B 线不受影响。
- `final_feedback.date` = 2026-09-15，与 01/03 文档一致（此前错放到 09-11，已修）。
- 新增段 `injector`（原始节点记录）与 `compat`（兼容等级、缺口、是否已挂反思）不影响平台既有解析。

## 2. 隔离验证（通过，2026-09-18 补齐实测）

三层取证，互不依赖：

1. **池子层**（`case01/tests/test_isolation.py`）：桥每次只把"当前节点"的事件放进
   `simulator.story`，池子里永远不会出现别的节点的事件（也不可能混进别节点的内容）。
2. **判定层**（同上）：即使把**整条时间线**的事件全塞进池子，`case01_node` 条件也只放行
   当前节点；已过去的节点不会补发，未到的节点一条都不注入；同一事件只触发一次。
3. **检索层**（真实运行实测，`case01/tools/isolation_probe.py`）：B 线全程 + 对抗模式
   （全量事件进池），每个节点、每个角色做记忆审计：
   - 记忆快照取**全量**事件记忆（不走 `retrieve_events()` 的 retention 截断）。
     实测各节点剧情条目数为 2 / 4 / 6 / 8 / 10 / 12 / 12，与"已释放事件累计数"逐一相等；
     `foreign`（不属于任何已释放事件的条目）合计 **0** 条。
   - 用**后续所有节点**的事件原文当检索词主动检索记忆，共 **60 次查询，0 次命中未释放内容**
     （`future_in_memory_total = 0`）。
   - 说明：mavis 检索是 top-k 相似度、没有相关度阈值，所以"有返回"不等于"泄漏"；
     判据是返回文本里是否真的出现未释放事件原文。第一版脚本按"有返回即泄漏"判定，
     得到 60/60 假阳性，已修正判据后重跑。
   - 产物：`runs_injector/isolation/probe-B.json`（该目录已自忽略，不入库）。
     该次运行本身 7 节点、2 次关键交互、0 重试，节点耗时和 256.7 秒。

## 3. 自发行为统计（已记录）

- B 线每一轮整跑都出现 **1 次非请求的自发对话**，出现节点不固定：
  2026-09-16 的三轮里 2 次在 09-03（node-4），2026-09-18 的两次实跑都在 08-31（node-3）。
- 两次关键交互（T0 咨询、09-15 最终反馈）在 2026-09-16 之后的所有整跑里 **100% 发生**，
  2026-09-16 的三轮里 T0 有 1 次重试 1 步，2026-09-17 起（时钟修复后）各轮均为 **0 重试**。
- 结论：与既定取舍一致——接受自发行为、不改 mavis 语义。

## 4. mavis 改动审查（通过）

- diff 范围：4 个文件、+380/−6（`agent_core.py` 81、`prompt/scratch.py` 28、`runtime/simulator.py` 58、新增测试 219）。
- 三处能力均为纯新增、默认关闭、语义中立：外部状态入口、交互请求入口、`role_directive` 字段。
- mavis 全量 **96 例通过**（2026-09-18；其中 5 例是 Location 转移默认关闭的回归测试）。
  停手规则复核：三处钩子仍是唯一的 case01 侧诉求；Location 转移是同一次提交里一起带进来的
  演示能力，`64780ac` 把它的默认值由 True 改成 False（显式配置才生效），属于**回到
  "纯新增、默认关闭"边界**，不是新语义——且 `origin/main` 上本就没有这段代码，
  对既有使用者零影响。
- 版本口径：mavis main 已 bump 到 `1.1.0` 并重建制品，provenance pin 同步（见执行计划阶段 4 第 5 项）。

## 5. 成本实测

> 两个口径要分清，二者不矛盾：**整跑墙钟**是整个进程从启动到反思结束的耗时
> （含场景构建、mavis provider 初始化、映射、Reflection+Router）；
> `injector.summary.elapsed_s` 只是**各节点步进耗时之和**（不含上面这些）。
> 第 8 节三线记录里 `elapsed_s` 为 302.6 / 286.4 / 302.8 秒，配上构建与反思
> （约 15–30 秒 + 89–108 秒）即得到文档里 437 / 390 / 417 秒的整跑墙钟。

- 定版整跑（B 线 7 节点，时钟修复后）：**558 秒**；两次关键交互 **零重试**（修复前需要 1 次重试）。
- 单节点耗时：T0 61s、08-28 54s、08-31 73s、09-03 103s、09-07 45s、09-11 58s、09-15 最终反馈 114s。
- 单次 Reflection + Router：80 秒（反思 4906 字、Router 拆出 5 个问题）。
- 2026-09-18 复跑两次 B 线（同一份代码、同一台机器、Ollama 单实例）：
  节点耗时和 253.8 秒与 350.5 秒（后者最终节点 138.8 秒，因与另一个进程抢 LLM 而变慢）；
  两次都是 7 节点、2 次关键交互、0 重试。隔离探针那次节点耗时和 256.7 秒。
  → 结论：**节点耗时和随机器负载在 250–560 秒之间浮动**，引用绝对秒数时必须写明口径与当次环境。
- 成本主因是 mavis 每步的角色日程与行动生成，与注入机制无关。
- 产物：`runs_injector/def-B/raw.json`（原始记录）与 `mapped.json`（case01 兼容 + 反思）——该目录已自忽略,不入库。

## 5.1 端到端跑出的三个真实缺陷（均已修复）

1. **最终反馈日期错误**：节点序列只到 timeline 末条事件（B 线 09-11），使"最终反馈"错放；01/03 规定固定 09-15。已补空节点并标为必须交互。
2. **强制交互会被"在移动/日程未初始化"挡住**：只改 config 无效，mavis 的前置条件看运行时状态。已改为运行时钉定（同格 + 清 path + 必要时补日程）。
3. **模拟时钟取的是墙钟**：`Game` 默认 `Timer()` 用 `datetime.now()`，而 mavis 规定 23:00 后不发起对话——导致"真实运行成败取决于实际时间"（23 点后所有探针 `started=False`）。已显式注入按首节点日期 09:30 起算的 Timer，节点间按 stride 推进。

修复后验证（墙钟已过 23:00，仍成功）：最终节点 `started=True retries=0`，Ethan 按规定汇报：
"I didn't buy HCM. The market stayed stable and HCM rose about 40% from its T0 price. I still have RMB 200,000 in cash.
I missed my friend's co-investment opportunity because it required at least RMB 250,000 …"（与 03 §七 B 线叙事一致，含个人后果）。

## 6. 已知缺口（未伪装为完成）

1. ~~Branch C 的"实际仓位"未实现~~ —— **已补齐(2026-09-17)**：`worldfacts.set_c_plan`
   注入 T0 条件化方案，`apply_node` 按 action 执行 `buy_now`（分支设定后立即建仓）或
   `wait`（逐节点监测触发后建仓）；`bridge._install_c_plan` 在 T0 落地后用
   `ConditionPlanParser` 解析 Investment AI 的答案注入事实层。真实 C 线整跑已验证：
   方案解析成功（`action=wait, buy_fraction=0.2`,keyword 触发）,Timeline A 否定句
   全部不触发 → 未建仓、现金 20 万不变（符合 C 线"订单落空→谨慎不买"剧情）。
2. `condition_monitor` 仅 Branch C 使用；真实 C 线运行已在 `condition_monitor` 落
   逐节点 fired=false 记录。
3. 记录未包含模型身份（后端/权重/dtype/device）——阶段 0 已决定不做该组修复。
4. CI 仍不覆盖 case01 测试（阶段 0 决定不做）。

## 7. 结论

阶段 3 四项验收全部通过，可进入阶段 4（替换、归档、demo 重录、文档同步）。
进入阶段 4 前需要确认两件事：是否现在替换已冻结的旧引擎；Branch C 仓位是否要在替换前补齐。
（说明：Branch C 仓位已于 2026-09-17 补齐并补测试，见第 8 节。）

## 8. 三线定版重跑（2026-09-17，Branch C 修复后的代码）

用修好的代码把 A/B/C 三条线各跑一遍，产物覆盖写入 `case01/runs/demo-{A,B,C}-mavis/run.json`，
serve 只读接口与 full-context 均已复核（full-context 10245–10506 字）。
本节整跑墙钟与记录内 `injector.summary.elapsed_s` 的口径差异见第 5 节开头。

- **A 线（积极买入）**：整跑 437 秒（其中 Reflection+Router 108 秒）；7 节点、2 次关键交互、0 次重试；
  turns 9、state_history 7、Router 问题 4 个；末态持有 HCM、现金约 125,177（与 03 §四 约 61% 一致）。
  最终反馈实录："I bought HCM at USD 45.2 on August 27 … sold at USD 27.4. The market never confirmed any order."
- **B 线（建议回避）**：整跑 390 秒（Reflection+Router 89 秒）；7 节点、2 次关键交互、0 次重试；
  turns 8、state_history 7、问题 4 个；全程未建仓、现金 200,000；最终反馈含"错过 25 万门槛合投机会"。
- **C 线（条件化等待）**：整跑 417 秒（Reflection+Router 97 秒）；7 节点、2 次关键交互、**1 次重试**；
  turns 8、state_history 7、问题 5 个；解析出的 T0 方案为 `action=wait, buy_fraction=0.2`
  （keyword 触发），`condition_monitor` 逐节点 3 次 `fired=false` → 未建仓、现金 200,000
  （符合 03 §八：C 走 Timeline A，条件未出现即不建仓）。

三条线的差异点全部落在"判断与后果"上（A 建仓亏损 / B 未建仓错失 / C 条件未触发维持现金），
说明节点驱动 + 事实层 + 反思链路对不同分支都能产出可比较材料。

## 9. 数据一致性复核（2026-09-18）

对 `runs/demo-{A,B,C}-mavis/run.json` 做独立的只读复核，四条维度全部通过：

1. **顶键完整性**：三条线均含 17 个顶层键，`compat.missing_keys` 均为空；十条必需键
   （run_id/start_date/end_date/branch/branch_action/turns/retrievals/events/
   state_history/final_feedback）齐全。
2. **branch 内部口径**：
   - state_history 日期逐条单调（08-27 → 09-15），符合时间线推进。
   - A 末态 `exited=True`、持有 HCM；B/C 全程未持有、现金 200,000；
     C 的 `condition_monitor` 全程 fired=false（符合同日第 8 节记录）。
3. **财务守恒（数学精确验证）**：A 线初始满仓 0.95@45.20，持有期 `cash_rmb=10,000`
   = 200,000×(1−0.95) 即未投入的 5%；09-07 按 27.40 退出后
   `cash = 10,000 + 190,000×(27.40/45.20) = 125,176.99`，**与记录逐分吻合**
   （`abs(expected−recorded)<0.01`）。B/C 无操作现金恒为 200,000，无跳变。
4. **跨记录交叉**：mavis 三线末账与旧引擎对应线（A=2、B=3、C=1）完全同口径
   （125,176.99 / 200,000 / 200,000），数值一致，无回归。

结论：三条 demo 顶层结构、时间线、分支仓位规则、财务记账全部自洽，且与旧引擎账目对齐。
复核所用临时脚本不入库，结论已并入本报告。
