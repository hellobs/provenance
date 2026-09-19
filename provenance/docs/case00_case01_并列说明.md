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
  `/api/runs/{run_id}/full-context`，另挂 `/viewer`）；**单一界面** `case01/vizkit/live_run.py`
  （**5010**：实时小镇 + 成品记录九块，页面上一组页签切换；见第四节）

产物按"加工程度"分四层落盘，都在 `case01/` 下：

- **成品三线**（平台契约读的就是这里）：`runs/260917-demo-case01-mavis-A/run.json`、
  `-B/`、`-C/`，各约 56 KB，含 turns / retrievals / events / reflection / router / injector
- **旧引擎对照**（archive 基线 `d41cdec` 的产物，保留不删）：
  `runs/260905-demo-case01-old-{A,B,C}/`（`run.json` + `turns.jsonl` + `retrievals.jsonl` + `branch.json`）
- **注入器原始与映射**：`runs_injector/`——`def-B/{raw.json,mapped.json}`（默认剧本）、
  `live-B-<日期>/raw.json`（真实跑一次的原始记录）、`smoke/`、`isolation/probe-B.json`
- **静态审查页**：`runs_html/`（index + 6 个 demo 页 + viz 子目录）

**记录命名（2026-09-19 定稿）**：`<YYMMDD>-<kind>-<case>-<engine>-<branch>[-HHMM]`。
样本如 `260905-demo-case01-old-B`（旧引擎）与 `260917-demo-case01-mavis-A`（mavis），
实跑如 `260919-live-case01-mavis-B-1424`。**日期在最前**（便于按时间排序）、
**带 `case`**（现在 case00 / case01 并存）、**带 `engine`**（`old` / `mavis`，一眼分出对照）。
规则有三条：

- **目录名必须等于记录里的 `run_id`**。不一致的后果实测过：5002 契约按记录里的 `run_id` 列，
  结果面板按目录名列，同一条记录会显示成两个名字。
- **样本名一经铸定就不再变**（平台通知 `通知平台侧_换样本_20260917.md` 里点名引用它们）；
  只有实跑记录每次新铸一个带时刻的名字（同一天跑多次也不撞）。
- **两种日期别混**：名字里的日期是**真实运行时间**；记录里的 `start_date` → `end_date`
  （08-27 → 09-15）是**模拟剧情日期**，属于案例内部时间线。实跑记录里**没有**墙上时钟字段，
  所以真实运行时间只存在于名字里——这也是名字必须带时间的根本原因。

一次新跑的固定链路：`live_run.py` 出 `raw.json` → 看护进程自动跑
`case01.injector.pipeline --from-record <raw> --reflect` 出 case01 兼容的 `run.json`
（补齐 Reflection 与 Router）→ 5002 与 5010 的结果页签都自动发现新的 `run_id`。
`live_switch.py --start case01` 会把这条链路的命令连同生成好的 `run_id` 一起打印出来。

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
  case00 存档只读 `case00/serve.py`（**5003**）。
- **5010 现在身兼两职**（2026-09-19 用户拍板"只维护一个界面"）：它既是 case01 的实时面，
  也把"成品记录九块"挂在同一个服务上（`/review` 页与 `/api/review/*`，首页一个页签切换）。
  `--review-only` 是它的**不推演**模式——只服务界面、不占 Ollama，属于只读用途，
  可以随时开着翻记录；独立的 5004 面板服务已退役。

规则为什么是"只许一个实时"：

- 端口上不冲突——5001 与 5010 可以同时监听（实测同时起过）；
- 真正的瓶颈是**只有一个 Ollama**（`qwen3:4b-instruct-2507-q4_K_M`）：两套推演同时跑会把双方都拖慢；
  演示时也不该让两套同时在动；
- 所以：**任何时刻只让一个实时面在跑**；另一个可以只起只读面，或干脆不起。

**为什么会有两个实时面，而规则只说"一个"**：这两个实时面是**两个 case 各自的实现**
（case00 是 6 角色小镇，case01 是 2 角色注入器推演），不是两个窗口、也不是重复建设。
"只有一个"约束的是**同时运行的实例数**，不是"只允许存在一个实现"。
两个都跑会抢同一个本地 Ollama，屏幕上也不该有两套在动，所以要机械保证。

切换用 `live_switch.py`（在 `provenance/provenance` 下执行），它把规则从约定做成保证：

```
python live_switch.py --status                    # 只看现状,不动任何进程
python live_switch.py --start case01 --nodes 2    # 起 case01(自动先停 case00 与本 case 旧实例)
python live_switch.py --start case01 --review-only # 不推演,只把"小镇+结果记录"界面起来
python live_switch.py --start case00              # 切到 case00 的 6 角色小镇
python live_switch.py --stop all                  # 全停(不碰只读面)
```

`--status` 会区分 case01 的三种状态：**在推演 / 在听(已跑完) / 仅审阅(未在推演)**
（靠 5010 `/health` 里的 `finished`/`finish_reason`，不靠猜）。`--start case00` 不会
误停一个只处于"仅审阅"的 5010——它没在推演，不抢 Ollama。

它要防两件事，**都是实测踩过的**：

1. 同时起两个 case；
2. **同一个 case 起两份**——目标端口被旧实例占着时，新进程会报
   `[Errno 10048] 端口已被占用`、**绑不上端口却照样在后台跑推演**，外面完全看不出
   第二份存在。所以发现进程**不能只看端口**，要按命令行认；又因为 uv venv 的
   `python.exe` 是 trampoline（一个实例父子两个 PID），`--status` 按 ppid 折算实例数，
   >1 就报「[!] 有 N 个实例」。`--start` 起完会校验端口真的绑上了，没绑上就把那个新进程收掉并报错，
   **绝不留游离实例**。

## 四、组件化与容器化现状

**已完成**：

- `packages/mavis-vizkit`（仓库根）：独立包（`pyproject.toml` + `src/` + `tests/`），
  可 `pip install -e`，4 个可视化插件经 entry point 注册（`console` / `report` / `town` / `live`）。
  它是两个案例都可复用的可视化层。
- mavis `mavisframework.plugin`（1.2.0 起）：框架级通用插件面，`Simulator(plugins=[...])` 挂载。
- **`case00/` 容器**（2026-09-19）：场景快照 + 存档索引说明 + 只读服务 5003 + 冻结声明。
  **建成即冻结，后续不维护。**
- **`case01/review_app.py`**（2026-09-19 建，同日晚改为**挂件**）：成品记录的结果面板。
  做法对齐 `live/routes.py` 的 `_render_reflections_page`——自包含 HTML + 原生 JS +
  `fetch` 打自己的 JSON API，不依赖 Phaser、不需要构建步骤。分九块：概览 / 对话(turns) /
  检索(retrievals) / 事件(events) / 状态(state_history) / 反思(reflection) /
  问题分流(router) / 注入器(injector) / 审计(audit)；六条记录（mavis 三条 + 旧引擎三条）都能选；
  旧引擎记录没有 `injector` 段，面板显示"无注入器记录"。
  **它不再是独立服务**：路由放在 `APIRouter` 里，由 5010 的实时面 `include_router` 挂上去
  （`attach_to()`），所以界面只有一个（小镇 / 结果记录 两个页签）。`--port 5004` 的独立跑法
  只留作排障。
- **`case01/tools/review_panel_probe.js`**（2026-09-19）：审阅面板的**无头自查**——把页面里的
  `<script>` 抽出来、用最小 DOM stub 在 Node 里真跑一遍九个 pane 的渲染函数（默认把
  `/api/review/runs` 里每条记录都跑一遍），抓"pane 抛异常"与"输出里出现 undefined /
  `[object Object]` / NaN"。**没有浏览器时这是唯一能抓 JS 运行期错误的手段**（`node --check`
  只查语法）。用法见文件头；需要面板服务在跑。
- **两种记录形态必须都认**：mavis 的检索是 `date/mode/injected[]`，旧引擎是
  `current_date/hits[]/source_stats`。首版只认前者，把旧记录的命中明细**整个丢了**
  （修复后 `260905-demo-case01-old-C` 的检索块从 1174 字符涨到 4764）。同理旧记录没有 `injector`/`summary`/`compat`，
  面板要分段渲染并明说"该记录早于 mavis 路径"，**不能**渲染成 `mode= / 节点 0 个`。
  另外默认选中的必须是成品三线（mavis），不能落在旧引擎对照记录上。

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
- 结果面板必须**写自己的 API**（`/api/review/*`），不能往 5002 上加 UI——
  5002 是冻结的对接契约，它的路径集合有漂移守卫盯着。面板自己的健康检查也因此放在
  `/api/review/health`，避开实时面的 `/health`。
- 待定：case00 的存档（2.2 GB、38 个 run）是否挑几个代表性的瘦身留档？
  现在靠 `results/` 的 gitignore 保护，删掉无法从 git 恢复。

## 六、给平台的 iframe 接入面（一个界面）

要求是：**既要有 Phaser 实时小镇窗口，又要有结果窗口，且都能 iframe 插进治理平台**。
2026-09-19 又加了一条：**只维护一个界面**（人不用开两个地址；`/combined` 那种"两窗一页"
因为要自己嵌自己而撤掉了）。

**平台只需要一个地址**：

```
http://<host>:5010/          ← 单一界面:首页就是"实时小镇 / 结果记录"两个页签
```

它既是 case01 的实时面，也把结果九块挂在同一个服务上；整页可再被 iframe。平台**不必知道**
5001 的存在，也不必知道当前在跑哪个 case——那由运维侧用 `live_switch.py` 决定
（`--start case01 --review-only` 可以只把这个界面起来而不推演）。

**实时小镇（Phaser；嵌的是场景，不含浮动面板、也不含页签）**

- case01：`http://<host>:5010/embed/scene`
- case00：`http://<host>:5001/embed/scene`
- 两者都有等价写法：`/?embed=scene`（页签**只在**独立首页出现，嵌入面不带）
- 场景页的 WebSocket 是 `"ws://" + location.host + "/ws"`，**按 location 拼**，
  所以嵌到哪个端口都连得对（iframe 不改变它自己的 location）。

**结果窗口（case01 成品记录九块）**

- 完整页：`http://<host>:5010/review`（与首页那个页签是同一份页面）
- 嵌入面：`http://<host>:5010/embed/review`（压缩版式：去掉大标题与页边距，贴合 iframe 尺寸）
- 深链：`?run=260917-demo-case01-mavis-A&tab=router`——指定默认记录与页签。
  页签 id：`overview` / `turns` / `retrievals` / `events` / `states` / `reflection` /
  `router` / `injector` / `audit`
- `?embed=1` 与 `/embed/review` 等价
- /**已退役**：`http://<host>:5004/*`（含 `/combined`）。平台侧若还配着 5004，改成上面的 5010。

**四条注意**

- 上述面都**不带** `X-Frame-Options` 与 CSP——测试里钉死了。带上它们，iframe 就白做。
- 实时面只有 5001 / 5010 两个，**任何时刻只应起一个**；只读面（5002 / 5003）可随时全开。
- 平台若只想看"结果"，引 `5010/embed/review` 即可；这台机器上用
  `live_switch.py --start case01 --review-only` 起界面，**不必**推演、也不占 Ollama。
- 5010 既是实时面也是结果面板的宿主，所以它由运维侧起/停；平台只负责引地址。
