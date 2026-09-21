# case01 injector

> **状态**:现行
> **最后核对**:2026-09-21
> **说明**:注入器说明
把 case01 的节点剧本作为"mavis 上的一套事件参数约束"注入。设计依据见
`../docs/case01_over_mavis_设计说明.md`，执行步骤见 `../docs/case01_over_mavis_执行计划.md`。

## 现状（2026-09-16）

已完成：

- 节点序列生成：`nodes.py`（timeline 按日聚节点；关键节点标记必须发生交互并附 focus）。
- mavis 装配：`bridge.py` 的 `_build_mavis`（复用通用注入钩子，不挂 governance）；
  装配后显式 `Game.reset_game()` 初始化 LLM provider。
- 到点必发：`conditions.py` 用 `Simulator.register_condition` 注册 `case01_node`（不改框架主逻辑）。
- 关键交互：`Simulator(interaction_request=...)` + 节点内重试；真实 Ollama 已验证
  （单节点约 2–3 分钟，重试 1 次内成功）。
- 字段对齐：`record.py` 把运行记录映射成 case01 `run.json` 兼容结构，顶层键齐全，
  与真实 `runs/demo-3/run.json` 交叉校验通过。
- 完整流水线：`pipeline.py`（可接 case01 的 Reflection/Router）。
- 场景：`scenario/`（两个角色 + 复用投资地图），说明见该目录 README。

未完成：

- `state_history`：尚未接入 case01 的 world 状态机（逐日资金/持仓），当前留空并在
  `compat.gaps` 标注。
- 整条线（6 节点）的耗时与记录完整性仍在实测。
- CI 未覆盖 case01 测试（阶段 0 已决定不做）。

## 用法

```bash
# dry-run：不加载 mavis，输出 case01 兼容记录（CI/联调）
python -m case01.injector.pipeline --branch B --dry-run --out runs_injector/B.json

# 真实运行（需本地 Ollama；可选接 Reflection/Router）
python -m case01.injector.pipeline --branch B --reflect --out runs_injector/B.json

# 只驱动节点、不接反思（单节点调试）
python -m case01.injector.pipeline --branch A --out /tmp/A.json
```

## mavis 侧依赖的三个通用能力

都是纯新增、默认关闭，不传时行为与之前完全一致（见 mavis 分支
`feat/generic-injection-hooks`）：

- `Simulator(external_state=fn)` / `Agent.set_step_context()`：步级临时状态，进提示词、不写记忆。
- `Simulator(interaction_request=fn)` / `Agent.request_interaction()`：请求一次交互，跳过冷却与概率门。
- agent 配置 `role_directive`：角色指令，非空时进提示词。

## 记录与兼容

`record.py` 输出 case01 `run.json` 的顶层键（`turns` / `events` / `retrievals` /
`final_feedback` / `audit` 等），并新增两段：

- `injector`：原始节点记录（含对话、临时状态、交互与重试）。
- `compat`：`level`（当前为 `schema`）、`missing_keys`、`gaps`（哪些字段是留空而非真值）、
  `reflection_attached`。

取不到真值的字段一律留空并写进 `gaps`，不伪造。
