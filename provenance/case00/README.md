# case00 — 原初的 6 角色投资咨询场景（已冻结）

> 2026-09-19 建容器并**冻结**：case00 后续不再维护，只保留可看、可复现。
> 与 case01 的关系见 `../docs/case00_case01_并列说明.md`。

## 这是什么

case00 是 provenance 平台**最一开始那套多 agent 设定**：6 个角色在投资咨询中心里
按制度约束与剧情事件连续仿真，实时展示每个角色的价值倾向如何演变。

六个角色：`AI Advisor`（`ai_tool`，制度内建）、`Daniel Shen`、`Kevin Su`、
`Michael Chen`、`Wendy Lin`、`Mr. Zhou`。

## 容器里有什么

- `scenario/` —— 场景快照（110 KB）：6 个角色的 `agent.json` + `portrait.png` + `texture.png`、
  `maze.json`（地图）、`relationships.json`（12 条关系）、`story.json`（17 个剧情事件）、
  `config.json`（mavis 的 agent/LLM 基础配置）。
  这是**建容器当时的拷贝**，用于冻结留档；实时运行读的仍是平台本体
  （`frontend/static/assets/village/agents/` 与 `scenarios/investment/`）。
- `runs/` —— 运行存档**索引说明**。2.2 GB 的存档留在 `results/checkpoints/`，不复制，见 `runs/README.md`。
- `serve.py` —— **只读**服务（端口 **5003**）：`/`（存档列表）、`/api/runs`、`/api/runs/{run_id}`、`/health`。

## 怎么跑

实时可视化（这是 case00 的"实时"入口，**5001**）：

```
cd D:\zzr\provenance\provenance
.\.venv-live\Scripts\python.exe live_fastapi.py --name demo --start 20250213-09:30 --stride 2 --step 0 --port 5001
```

只读存档浏览（**5003**）：

```
.\.venv-live\Scripts\python.exe -m case00.serve --port 5003
```

## 两条硬约定

1. **同一时刻只允许一个实时可视化。** case00 的实时（5001）与 case01 的实时（5010）
   共用同一个本地 Ollama，同时跑会把双方都拖慢。只读面（5002 / 5003）不跑模拟，
   不受此限，可以随时开着。（case01 的结果面板挂在 5010 上，`--review-only` 起它不推演、
   也不算实时面；独立的 5004 已退役。）
2. **case00 不再维护。** 不新增功能、不改场景设定；除修正性改动外不接受开发。
   要做新的实验，走 case01（或在 case01 的插件面上再挂新案例）。
