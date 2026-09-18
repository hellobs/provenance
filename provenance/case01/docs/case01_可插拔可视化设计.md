# Case 01 可插拔可视化设计

> 版本：v0.1（2026-09-17）· 更新（2026-09-18）。
> 范围：`packages/mavis-vizkit`（独立包，本文件原说的是 `case01/vizkit/` 与 `case01/viz.py`，
> 可视实现已在 2026-09-18 抽到独立包 `mavis_vizkit`；`case01/vizkit/` 只留 `live_run.py` 薄 CLI，
> `case01/viz.py` 属已归档旧引擎）。
> 目标：**一份数据、多种画法**——引擎/注入层只产生事件，可视化后端按插件接入。

## 1. 为什么要可插拔

同一份 run 材料，不同场景要不同画法：

- 评审/审查要**看得清判断与后果**（图表 + 文本 + 风险标记）；
- 演示要**有小场景的实时观感**（斯坦福小镇风格：角色走动、对话气泡、状态曲线）；
- 平台要**嵌进去做专家审核**（任务队列、Approve/Edit/Reject、Full Context）。

这些不该各自去改引擎。因此把"产生事件"与"怎么画"分开：前者在引擎/注入层，后者全部是插件。

## 2. 统一事件契约

契约直接采用 mavis `runtime.protocol` 的键名（前端 / Unity / 平台都按它解析），
不引入第二套格式：

- `{"type":"init","agents":[...],"time":...}`
- `{"type":"time","time":...}`
- `{"type":"agent","name","coord","path","action","location","currently","time"}`
- `{"type":"chat_line","speaker","text"}`
- `{"type":"story","id","event_type","content","targets","time"}`
- `{"type":"snapshot","agents":{name:{...}},"time"}`

两条来源都归一到这套事件：

- **在线**：mavis `Simulator` 的 `on_agent / on_step / on_story` 回调，加上 `agent_core.chat_callback`
  的逐句对话（provenance live 也是这么接的）；
- **离线**：一份 run 记录 → `events_from_record()`。同时兼容两种布局：
  bridge 原始记录（顶层 `nodes`）与映射后的 `run.json`（节点在 `injector.nodes`）。

## 3. 插件机制

（可视化实现已抽到 `packages/mavis-vizkit`，以下均为 `mavis_vizkit` 的接口；case01 侧
`case01/vizkit/__init__.py` 是只做 re-export 的兼容壳。）

- `mavis_vizkit.Visualizer`：基类，`on_event(evt)` 处理事件流，`on_record(rec)` 可直接吃整份记录。
- `mavis_vizkit.register(name, factory)` / `create(name, **kw)` / `names()`：注册表。
- `mavis_vizkit.Fanout`：把事件广播给多个插件；**单个插件抛错会被隔离**，不影响引擎与其它插件。
- `mavis_vizkit.replay`：回放驱动（可加 pacing），也能把事件写成 JSONL 交给外部播放器/服务。

内置四个插件：

1. `console`：收集/打印事件（默认，CI 与调试用）。
2. `report`：把整份记录渲染成审查页，输出静态 HTML。单页渲染回调（`page_fn`）与总览
   回调（`index_fn`）由调用方注入，本插件不绑定任何业务渲染器（不再复用什么 `case01.viz`）。
3. `town`：把事件翻译成**现有 Phaser 前端**可消费的消息，并处理：
   - 角色贴图别名（`alias`）：由调用方传入（case01 的两个角色映射到小镇素材池里的已有
     贴图，不新增美术资源）；缺省会报错，不内置 `ROLE_TEXTURE_ALIAS` 之类的默认映射；
   - 坐标回退：老记录没有逐节点坐标时，用 `scenario_dir`（调用方给）里场景配置的初始坐标占位；
   - 可选 JSONL 落盘，供离线播放/调试。
4. `live`：**实时**推送（见 §6）——在本机起一个只读服务，直接复用现成小镇前端页面，
   运行中把事件边产生边广播给所有客户端。

## 4. 注入层怎么接

`MavisBridge(..., visualizers=[town, report])`（`town` / `report` 是**插件实例**，不是名字字符串）：

- 装配时把 `on_agent / on_step / on_story` 接到 mavis，并设置逐句对话回调；
- 每个 agent 步骤都会把 `coord / action / location / currently` **落进节点记录**（`nodes[].agents`），
  因此小镇风格视图与后续任何位置相关视图都有数据；
- 跑完调用 `bridge.close()` 让插件收尾（刷盘/关连接）。

**带必传配置的插件只能用实例、不能用名字创建**：`town` 需要 `alias` / `scenario_dir`，
`report` 需要 `page_fn` / `index_fn`。若写 `visualizers=["town", "report"]`，装配时会走
`create("town")` / `create("report")`，缺参直接抛 ValueError。正确写法是先 `create(...)` 拿到
实例再放进列表，如 `visualizers=[create("town", alias=..., scenario_dir=...), report_inst]`。

不传 `visualizers` 时，行为与之前完全一致（不产生事件、不引入任何可视化依赖）。

## 5. 现在能做什么 / 还差什么

已可用：

- 实时（小镇风格，推荐）：`python -m case01.vizkit.live_run --branch B --port 5010`
  → 浏览器打开 `http://127.0.0.1:5010/`，运行过程中角色移动、对话气泡、状态面板**实时**出现。
  这就是原来的 Phaser 前端（`frontend/templates/index.html` + `main_script.html`），**前端零改动**。
  （`case01.vizkit.live_run` 是薄 CLI：roles / alias / scenario / 前端资源根由它传给 `mavis_vizkit` 的 live 插件。）
- 审查页（离线）：report 插件需要调用方注入单页渲染回调（`page_fn`）与总览回调（`index_fn`），
  无法只给一份 run 记录就自动生成——需由 case01 侧提供渲染回调后实例化（不再使用已归档的 `python -m case01.viz`）。
- 回放文件（离线）：`python -m mavis_vizkit.replay --record <run.json> --town out.jsonl
  --alias "Investment AI=AI Advisor,Ethan Lin=Mr. Zhou" --scenario-dir <case01 场景目录>`
  （town 插件需要 `alias` 与 `scenario-dir`，缺省会报错；这两个参数放不进 `--record`，必须显式传）。

## 6. 实时插件（live）如何工作

- uvicorn 跑在守护线程；引擎线程每产生一个事件就调 `on_event()`，
  经 `asyncio.run_coroutine_threadsafe` 投递到事件循环广播给所有 WS 客户端；
- 客户端连上先收 `init`（含角色与贴图别名），随后立即收到当前 `snapshot` 与积压事件；
- 每步结束补发一份 `snapshot`（含两角色的 coord/action/location），因此刷新页面也能立刻有画面；
- 每 5 秒一次 `ping`（与既有前端心跳一致）；
- **角色贴图别名**：把 `/static/assets/village/agents/<角色>/` 挂载到已有角色目录
  （case01 的 Investment AI→AI Advisor、Ethan Lin→Mr. Zhou）。别名表在 case01 侧由
  `live_run` 注入（传给 `mavis_vizkit` 的 live/town 插件），插件不内置这份映射，不需要新增美术资源；
- 服务未启动时事件进缓冲（`pending`），便于单测与无头运行，不丢语义。

实测（2026-09-18，`--branch B --nodes 1`）：WS 客户端在运行过程中陆续收到
`ping@15.1s`、`chat_line@15.3/17.7/20.0s`、`agent@24.1/24.4s`、`time@24.4s`、`snapshot@24.4s`，
随后每 5 秒 ping —— 属**实时**推送，不是跑完再回放。

还差 / 可继续做的：

- 真实坐标：`nodes[].agents` 是本次新增的字段，旧 demo 靠场景初始坐标回退；重跑一次即可带上真实走动轨迹。
- 前端角色数为 2：地图与素材沿用小镇版本，若要专门的两人工位场景，只需替换 `scenario/` 与 tilemap，插件不用改。
- 平台侧专家审核界面（任务队列/Approve-Edit-Reject/冲突/归池）仍属平台范围，接口沿用 5002 三端点 + full-context。
