# Case 01 可插拔可视化设计

> 版本：v0.1（2026-09-17）· 范围：`case01/vizkit/` 与 `case01/viz.py`
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

- `vizkit.Visualizer`：基类，`on_event(evt)` 处理事件流，`on_record(rec)` 可直接吃整份记录。
- `vizkit.register(name, factory)` / `create(name, **kw)` / `names()`：注册表。
- `vizkit.Fanout`：把事件广播给多个插件；**单个插件抛错会被隔离**，不影响引擎与其它插件。
- `vizkit.replay`：回放驱动（可加 pacing），也能把事件写成 JSONL 交给外部播放器/服务。

内置三个插件：

1. `console`：收集/打印事件（默认，CI 与调试用）。
2. `report`：把整份记录渲染成审查页（复用 `case01.viz`），输出静态 HTML。
3. `town`：把事件翻译成**现有 Phaser 前端**可消费的消息，并处理：
   - 角色贴图别名（`ROLE_TEXTURE_ALIAS`）：case01 的两个角色映射到小镇素材池里的已有贴图，
     不新增美术资源；
   - 坐标回退：老记录没有逐节点坐标时，用场景配置的初始坐标占位；
   - 可选 JSONL 落盘，供实时回放服务逐条推送。

## 4. 注入层怎么接

`MavisBridge(..., visualizers=["town", "report"])`：

- 装配时把 `on_agent / on_step / on_story` 接到 mavis，并设置逐句对话回调；
- 每个 agent 步骤都会把 `coord / action / location / currently` **落进节点记录**（`nodes[].agents`），
  因此小镇风格视图与后续任何位置相关视图都有数据；
- 跑完调用 `bridge.close()` 让插件收尾（刷盘/关连接）。

不传 `visualizers` 时，行为与之前完全一致（不产生事件、不引入任何可视化依赖）。

## 5. 现在能做什么 / 还差什么

已可用：

- 审查页：`python -m case01.viz`（或 report 插件）→ `runs_html/viz/*.html`；
- 回放文件：`python -m case01.vizkit.replay --record <run.json> --town out.jsonl`
  → 已验证 demo-C-mavis 产出 36 条消息（init 1 / time 7 / story 13 / chat_line 8 / snapshot 7）。

还差一步（下一步做）：

- **回放服务**：把 JSONL/记录按时间逐条推给现有小镇前端（WS `/ws`），并做两件事：
  1. 前端只需 2 个角色，需要按别名贴图与初始位置渲染；
  2. 与 `5001`/`5002` 的既有服务并存（回放服务独立端口，只读记录，不改引擎）。
- 老记录缺坐标：`agents` 字段是新加的，旧 demo 靠场景初始坐标回退；重跑一次即可带上真实坐标。
