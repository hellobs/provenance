# Case01 over MAVIS · 阶段 3 验收报告

> 日期：2026-09-16 · 分支：`feat/case01-injector`（引擎侧）/ `feat/generic-injection-hooks`（mavis 侧）
> 依据：`case01_over_mavis_设计说明.md` §8、`case01_over_mavis_执行计划.md` 阶段 3

## 1. 字段级对比（通过）

- 映射层 `record.py` 输出 case01 `run.json` 的全部顶层键；`compat.missing_keys` 为空。
- 与仓库中真实 `runs/demo-3/run.json` 做顶层键交叉校验，测试通过（demo 不入库时自动跳过）。
- 修正版整跑（B 线 7 节点，真实 Ollama）实测记录：`turns` 5、`events` 12、`state_history` 7、`audit` 22、`reflection` 5154–5590 字、Router 拆出问题 5–7 个。
- `final_feedback.date` = 2026-09-15，与 01/03 文档一致（此前错放到 09-11，已修）。
- 新增段 `injector`（原始节点记录）与 `compat`（兼容等级、缺口、是否已挂反思）不影响平台既有解析。

## 2. 隔离验证（通过）

- 机制：事件按节点释放，未释放事件不进入任何角色上下文。
- 测试：切到下一节点后，上一节点的 story 事件不再命中（`case01_node` 条件按节点 id 判定）。
- 注意：这是机制级验证；"角色记忆里检索不到未来信息"的实测还需在真实运行中抓一次检索记录。

## 3. 自发行为统计（已记录）

- 三次 B 线整跑中，**2 次在 09-03 节点出现非请求的自发对话**（B2 无、B3 有、首次 B 有），全部记录在案。
- 两次关键交互（T0 咨询、09-15 最终反馈）在三次整跑中 **100% 发生**，其中 T0 有一次重试 1 步。
- 结论：与既定取舍一致——接受自发行为、不改 mavis 语义。

## 4. mavis 改动审查（通过）

- diff 范围：4 个文件、+380/−6（`agent_core.py` 81、`prompt/scratch.py` 28、`runtime/simulator.py` 58、新增测试 219）。
- 三处能力均为纯新增、默认关闭、语义中立：外部状态入口、交互请求入口、`role_directive` 字段。
- mavis 全量 91 例通过；未触发停手规则（没有改动任何现有行为语义）。

## 5. 成本实测

- 定版整跑（B 线 7 节点，时钟修复后）：**558 秒**；两次关键交互 **零重试**（修复前需要 1 次重试）。
- 单节点耗时：T0 61s、08-28 54s、08-31 73s、09-03 103s、09-07 45s、09-11 58s、09-15 最终反馈 114s。
- 单次 Reflection + Router：80 秒（反思 4906 字、Router 拆出 5 个问题）。
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
