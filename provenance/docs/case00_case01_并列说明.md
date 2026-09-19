# case00 / case01 两个案例的并列说明

> 2026-09-19。**case00 = 仓库原有的 6 角色投资咨询场景**（Provenance 原生，最一开始那套多 agent 设定）；
> **case01 = 作用于 mavis 的注入器**（2 角色、节点驱动的受控实验）。
> 两者并存、互不依赖。case00 已收成 first-class 的 `case00/` 容器并**冻结不维护**；
> **任何时刻只允许一个实时可视化在跑**（第三节列了五个服务的分工）。

## 一、case00 是什么

case00 原先没有单独的目录——它就是 provenance 平台原本就有的那套场景。2026-09-19 起它另有一个
first-class 容器 `case00/`（见下一小节），但容器只是**冻结留档**：实时运行读的仍是平台本体。
下面是构成 case00 的平台文件：

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

### 1.1 first-class 容器 `case00/`（2026-09-19 建，建成即冻结）

- `case00/scenario/`——场景快照 110 KB：6 个角色的 `agent.json` + `portrait.png` + `texture.png`、
  `maze.json`、`relationships.json`、`story.json`、`config.json`。这是建容器当时的**拷贝**，
  用于冻结留档；实时运行读的仍是平台本体（`frontend/static/assets/village/`、`scenarios/investment/`）。
- `case00/runs/`——只有说明：2.2 GB 存档（38 个 run，`stock-en8` 单个 1849 MB）留在
  `results/checkpoints/`，**不复制**，并写明不要删。
- `case00/serve.py`——**只读**服务，端口 **5003**：`/`（存档列表）、`/api/runs`、
  `/api/runs/{run_id}`（快照步数 + 最后一步的角色坐标与 value_tendency）、`/health`。
- `case00/README.md`——冻结声明与两条硬约定。

启动只读面：

```
.\.venv-live\Scripts\python.exe -m case00.serve --port 5003
```

## 二、case01 是什么，它的产物落在哪

case01 是"作用于 mavis 的一套事件参数约束"：2 个角色（`Investment AI` + `Ethan Lin`）、
节点驱动的受控实验；世界事实与节点节奏由注入器决定，mavis 只负责角色表达与交互。

- 场景与角色：`case01/injector/scenario/`（`config.json`、`maze.json`、
  `agents/{Investment AI, Ethan Lin}/agent.json`、`relationships.json`）
- 运行架构：`case01/injector/`（bridge / 节点 / 条件 / 记录映射）+ `case01/world/`
  （事实层、市场时间线、分支判定；纯净，只 import 标准库）
- 服务：只读契约 `case01/serve.py`（**5002**：`/api/runs`、`/api/runs/{run_id}`、
  `/api/runs/{run_id}/full-context`，另挂 `/viewer`）；实时可视化 `case01/vizkit/live_run.py`（**5010**）；
  成品记录的交互式审阅面板 `case01/review_app.py`（**5004**，见第四节的九块）

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

"同一时刻只允许一个实时可视化"是硬规则。先把五个服务分清：

- **实时面（跑模拟线程，只许起一个）**：case00 的 `live_fastapi.py`（**5001**）、
  case01 的 `case01/vizkit/live_run.py`（**5010**）。
- **只读面（不跑模拟，可以随时全开）**：case01 只读契约 `case01/serve.py`（**5002**）、
  case00 存档只读 `case00/serve.py`（**5003**）、case01 审阅面板 `case01/review_app.py`（**5004**）。

规则为什么是"只许一个实时"：

- 端口上不冲突——5001 与 5010 可以同时监听（实测同时起过）；
- 真正的瓶颈是**只有一个 Ollama**（`qwen3:4b-instruct-2507-q4_K_M`）：两套推演同时跑会把双方都拖慢；
  演示时也不该让两套同时在动；
- 所以：**任何时刻只让一个实时面在跑**；另一个可以只起只读面，或干脆不起。

切换：

- 让 case00 实时：起 5001（真模拟），并停掉 5010。
- 让 case01 实时：起 5010，5001 以 `--no-sim` 起（只服务 Web 层）或不启。
- 查端口对应的进程：`Get-NetTCPConnection -LocalPort 5001,5002,5003,5004,5010 -State Listen`；
  停止：`Stop-Process -Id <pid>`。

## 四、组件化与容器化现状

**已完成**：

- `packages/mavis-vizkit`（仓库根）：独立包（`pyproject.toml` + `src/` + `tests/`），
  可 `pip install -e`，4 个可视化插件经 entry point 注册（`console` / `report` / `town` / `live`）。
  它是两个案例都可复用的可视化层。
- mavis `mavisframework.plugin`（1.2.0 起）：框架级通用插件面，`Simulator(plugins=[...])` 挂载。
- **`case00/` 容器**（2026-09-19）：场景快照 + 存档索引说明 + 只读服务 5003 + 冻结声明。
  **建成即冻结，后续不维护。**
- **`case01/review_app.py`**（2026-09-19）：成品记录的交互式审阅面板（**5004**）。做法对齐
  `live/routes.py` 的 `_render_reflections_page`——自包含 HTML + 原生 JS + `fetch` 打自己的 JSON API，
  不依赖 Phaser、不需要构建步骤。分九块：概览 / 对话(turns) / 检索(retrievals) / 事件(events) /
  状态(state_history) / 反思(reflection) / 问题分流(router) / 注入器(injector) / 审计(audit)；
  六条记录（mavis 三条 + 旧引擎三条）都能选；旧引擎记录没有 `injector` 段，面板显示"无注入器记录"。

**还没组件化：case01 的注入器本体。** 它目前仍是 provenance 内的模块（`case01/injector/` + `case01/world/`）。
阶段 3 设计稿 `case01/docs/任务_阶段3_抽包设计稿_20260918.md` 已于 2026-09-19 评审通过，
目标包名 `packages/mavis-case01-injector`，但**代码一行都还没搬**。
设计稿已查明的两条硬事实：bridge 对 `mavisframework` 与 `mavis_vizkit` 是无条件硬依赖
（只有 `mavisframework.plugin` 是特性探测特例）；`case01/world/` 完全纯净。

**case00 不做组件化**：它是平台原生场景，容器只是冻结留档；`live_fastapi.py` 与前端仍是平台本体，
本次**没有**改动它们的引用路径。

## 五、已知取舍与待定

- case00 的 `scenario/` 是建容器当时的拷贝，会与平台本体随时间分叉。这是刻意的：
  case00 已冻结，快照要能独立说明"当时是什么样"。
- case00 **没有**做成 5002 那种机器可读契约：它的存档是每步快照（`simulate-*.json`），
  不是单文件 `run.json`，也没有 reflection/router；冻结之后不值得再定义契约。
- 审阅面板必须**写自己的 API**（`/api/review/*`），不能往 5002 上加 UI——
  5002 是冻结的对接契约，它的路径集合有漂移守卫盯着。
- 待定：case00 的存档（2.2 GB、38 个 run）是否挑几个代表性的瘦身留档？
  现在靠 `results/` 的 gitignore 保护，删掉无法从 git 恢复。
