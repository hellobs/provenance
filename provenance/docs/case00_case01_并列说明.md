# case00 / case01 两个案例的并列说明

> 2026-09-19。**case00 = 仓库原有的 6 角色投资咨询场景**（Provenance 原生，最一开始那套多 agent 设定）；
> **case01 = 作用于 mavis 的注入器**（2 角色、节点驱动的受控实验）。
> 两者并存、互不依赖；同一时刻只把其中一个做成实时可视化（见第三节）。

## 一、case00 是什么

case00 没有单独的目录——它就是 provenance 平台原本就有的那套场景，由下面这些文件共同构成：

- 场景设定：`scenarios/investment/`——`relationships.json`（12 条角色关系）、
  `story.json`（17 个剧情事件 `s-001`…`s-017`）、`README.md`
- 制度约束：`governance.json`——6 个角色各自的 3~4 维价值权重
- 角色底色与贴图：`frontend/static/assets/village/agents/`（6 个目录，各含 `agent.json` + 行走图）
- 地图：`frontend/static/assets/village/maze.json`
- 前端（权威实现）：`frontend/templates/main_script.html`（Phaser：消息解析、移动队列、倾向曲线、治理面板）
- 服务：`live_fastapi.py` + `live/`，端口 **5001**；对外面为 `/`（小镇实时）、`/embed/scene`、
  `/embed/goals`、`/embed/explain`、`/api/goals`、`/api/export-chart`
- 运行存档：`results/checkpoints/<run-name>/`（已有 `gtc-demo`、`gtc-demo10`、`gtc-demo14` 等）、`results/compressed/`

六个角色（`governance.json` 与贴图目录一致）：
`AI Advisor`（`ai_tool`，制度内建）、`Daniel Shen`、`Kevin Su`、`Michael Chen`、`Wendy Lin`、`Mr. Zhou`。

启动（真实 LLM，走本地 Ollama）：

```
cd D:\zzr\provenance\provenance
.\.venv-live\Scripts\python.exe live_fastapi.py --name demo --start 20250213-09:30 --stride 2 --step 0 --port 5001
```

只想看 Web 层（小镇页与三个 `/embed` 面板）、不跑模拟线程：把 `--step 0` 换成 `--no-sim`。

## 二、case01 是什么，它的产物落在哪

case01 是"作用于 mavis 的一套事件参数约束"：2 个角色（`Investment AI` + `Ethan Lin`）、
节点驱动的受控实验；世界事实与节点节奏由注入器决定，mavis 只负责角色表达与交互。

- 场景与角色：`case01/injector/scenario/`（`config.json`、`maze.json`、
  `agents/{Investment AI, Ethan Lin}/agent.json`、`relationships.json`）
- 运行架构：`case01/injector/`（bridge / 节点 / 条件 / 记录映射）+ `case01/world/`
  （事实层、市场时间线、分支判定；纯净，只 import 标准库）
- 服务：只读契约 `case01/serve.py`（**5002**：`/api/runs`、`/api/runs/{run_id}`、
  `/api/runs/{run_id}/full-context`，另挂 `/viewer`）；实时可视化 `case01/vizkit/live_run.py`（**5010**）

产物按"加工程度"分四层落盘，都在 `case01/` 下：

- **成品三线**（平台契约读的就是这里）：`runs/demo-A-mavis/run.json`、`runs/demo-B-mavis/`、
  `runs/demo-C-mavis/`，各约 56 KB，含 turns / retrievals / events / reflection / router / injector
- **旧引擎对照**（archive 基线 `d41cdec` 的产物，保留不删）：`runs/demo-1|2|3/`
  （`run.json` + `turns.jsonl` + `retrievals.jsonl` + `branch.json`）
- **注入器原始与映射**：`runs_injector/`——`def-B/{raw.json,mapped.json}`（默认剧本）、
  `live-B/{raw.json,record-B-mavis.json}`（真实跑一次）、`smoke/`、`isolation/probe-B.json`
- **静态审查页**：`runs_html/`（index + 6 个 demo 页 + viz 子目录）

一次新跑的固定链路：`live_run.py` 出 `raw.json` → `case01.injector.pipeline --from-record <raw> --reflect`
出 case01 兼容的 `run.json`（补齐 Reflection 与 Router）→ 5002 自动发现新的 `run_id`。

## 三、两个案例的关系与"同一时刻只起一个实时可视化"

关系：

- **互不依赖**。5001（case00）与 5002 / 5010（case01）是三个独立进程；
  case01 的联调不依赖 5001，5001 也不需要 case01。仓库里早已这么记
  （`case01/docs/对接确认单_引擎侧答复_20260905.md`：provenance 旧场景服务 5001，与 Case 01 无关）。
- case01 复用 mavis 与 mavis-vizkit；case00 走 provenance 自己的 `live/` 编排，用同一套 Phaser 前端。
- 二者**共用同一个本地 Ollama**（`qwen3:4b-instruct-2507-q4_K_M`）。

"同一时刻只起一个实时可视化"的真实约束：

- 端口上不冲突——5001 与 5010 可以同时监听（实测同时起过）；
- 真正的瓶颈是**只有一个 Ollama**：两套推演同时跑会把双方都拖慢；演示时也不该让两套同时在动；
- 因此定为：**同一时刻只让一个案例跑模拟线程**。另一个可以照常起 Web 层（`--no-sim`）供看页面，或干脆不起。

切换：

- 让 case00 实时：起 5001（真模拟），并停掉 5010。
- 让 case01 实时：起 5010，5001 以 `--no-sim` 起或不启。
- 查端口对应的进程：`Get-NetTCPConnection -LocalPort 5001,5002,5010 -State Listen`；
  停止：`Stop-Process -Id <pid>`。

## 四、组件化现状

**已组件化**：

- `packages/mavis-vizkit`（仓库根）：独立包（`pyproject.toml` + `src/` + `tests/`），
  可 `pip install -e`，4 个可视化插件经 entry point 注册（`console` / `report` / `town` / `live`）。
  它是两个案例都可复用的可视化层。
- mavis `mavisframework.plugin`（1.2.0 起）：框架级通用插件面，`Simulator(plugins=[...])` 挂载。

**还没组件化：case01 的注入器本体。** 它目前仍是 provenance 内的模块（`case01/injector/` + `case01/world/`）。
阶段 3 设计稿 `case01/docs/任务_阶段3_抽包设计稿_20260918.md` 已于 2026-09-19 评审通过，
目标包名 `packages/mavis-case01-injector`，但**代码一行都还没搬**。
设计稿已查明的两条硬事实：bridge 对 `mavisframework` 与 `mavis_vizkit` 是无条件硬依赖
（只有 `mavisframework.plugin` 是特性探测特例）；`case01/world/` 完全纯净。

**case00 没有组件化计划**：它是平台原生场景，`live_fastapi.py` 与前端都是平台本体的一部分。

## 五、待定（需要拍板，不能顺手做）

case00 现在是"平台原生的一套散落文件"，没有自己的目录。是否要把它也收成一个 first-class 的
`case00/` 容器（自己的场景目录、自己的 runs 目录、自己的只读服务），与 case01 对称？
这一步会动到 `live_fastapi.py` 与前端引用，属于结构改动，先定再做。
