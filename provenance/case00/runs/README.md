# case00 的运行存档（不复制，仅索引）

case00 的运行存档体积很大——`results/checkpoints/` 下 **38 个 run、合计约 2.2 GB**
（`stock-en8` 单个就 1849 MB）。**所以这里不放副本**，只放这份说明；真身在原处：

```
provenance/provenance/results/checkpoints/<run-name>/
```

一个 run 目录里通常是：

- `simulate-YYYYMMDD-HHMM.json` —— 每步一个完整快照（角色坐标/状态/倾向）
- `decisions.json` —— 决策留痕
- `conversation.json` —— 对话留痕

按规模看，最完整、最常被引用的几个是 `gtc-demo`（23 MB）、`gtc-demo14`（6 MB）、
`gtc-demo13`、`gtc-demo2`、`stock-en9`、`invest-align`、`invest-i18n`；
`stock-en5/6/7/8` 是英文场景的大存档，占了绝大多数体积。

查看方式：起只读服务 `python -m case00.serve --port 5003`，然后
`GET /api/runs` 拿索引、`GET /api/runs/{run_id}` 拿某个 run 的快照步数与最后一步的角色状态。

**这些存档不要删**：case00 已冻结，它们是唯一的历史证据；且 `results/` 已在 gitignore 内，
删掉无法从 git 恢复。
