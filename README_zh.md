<h1 align="center">Provenance</h1>

> **状态**:现行
> **说明**:provenance 平台总览(中文);本仓入口

**AI 价值形成过程，可观察、可治理、可审计。**

**本平台基于 [mavisframework](https://github.com/hellobs/mavis) v1.3.4 构建**（自研生成式智能体仿真框架，独立发版）。应用场景为投资咨询（二级市场）：智能体在空间环境中基于情境作判断、移动、对话，每一步可配置、可解释、可实时可视化。

[![based on mavisframework](https://img.shields.io/badge/based%20on-mavisframework%201.3.4%20%C2%B7%20engine-7c3aed?style=flat-square&labelColor=1f2328)](https://github.com/hellobs/mavis) [![License](https://img.shields.io/badge/license-Apache--2.0-3b82f6?style=flat-square&labelColor=1f2328)](LICENSE) [![Tests](https://img.shields.io/badge/tests-passing-2ea043?style=flat-square&labelColor=1f2328)](tests) [![Python](https://img.shields.io/badge/python-%E2%89%A5%203.12-3776ab?style=flat-square&labelColor=1f2328)](requirements.txt) [![Live](https://img.shields.io/badge/live-127.0.0.1%3A5010-009688?style=flat-square&labelColor=1f2328)](provenance/docs/平台对接契约_5010唯一入口.md) [![Scenarios](https://img.shields.io/badge/cases-3%20declared-f59e0b?style=flat-square&labelColor=1f2328)](provenance/cases)

[English](./README.md) | **简体中文**

---

> **摘要**
>
> 本平台是一个多智能体仿真平台，应用场景为投资咨询（二级市场）：智能体在空间环境中基于情境作判断、移动、对话，每一步可配置、可解释、可实时可视化。平台面向 Global Trust Challenge 的*过程对齐*叙事——**AI 的价值形成过程可被观察、可被治理、可被审计**。
>
> 平台与框架分离：[mavisframework](https://github.com/hellobs/mavis) 独立维护、独立发版（v1.3.4），本仓通过 `requirements.txt` 中的 `mavisframework>=1.2.0,<2.0.0` 依赖它。**制度约束不进提示词**——专家调整约束只加权*后果反馈*，倾向需经后续体验才逐步收敛（滞后收敛 = 内化发生的可观测证据）。
>
> → [0\. 现状速览](#0-现状速览) ｜ [7\. IVD 治理平台](#7-ivd-治理平台) ｜ [专家导览](provenance/docs/架构总览与对接指南.md)

**范围界定**

| 本平台覆盖 | 本平台不覆盖 |
|---|---|
| 实时推演与可视化；5010 唯一对接入口（实时面 + 数据面） IVD 治理：约束编辑、倾向曲线、干预审计、三层解释 专家干预可插拔（`InterventionStrategy` 注册表） 只读发现面：专家导览、平台契约、字段读法 嵌入面 `/embed/*`（iframe 免 CORS） | 换业务场景的开箱即用（换场景 = 写 `cases/*/scenario.yaml` + 引擎） 对外服务的安全加固（**无鉴权**，默认只绑本机） 真实市场模型（后果反馈是 embedding 相似度的轻量替代） 研究结论的最终裁定（AI 只负责机械正确性） |

**接手/对接请先读 [provenance/docs/架构总览与对接指南.md](provenance/docs/架构总览与对接指南.md)** —— 一眼看清哪些文档是现行口径、哪些已被取代。交给治理平台的唯一口径见 [平台对接契约_5010唯一入口.md](provenance/docs/平台对接契约_5010唯一入口.md)。

---

## 目录

* [0\. 现状速览](#0-现状速览)
* [1\. 架构](#1-架构)
* [2\. 环境准备与框架安装](#2-环境准备与框架安装)
* [3\. 配置大模型](#3-配置大模型)
* [4\. 实时模拟](#4-实时模拟)
* [5\. 角色配置](#5-角色配置)
* [6\. 运行参数](#6-运行参数)
* [7\. IVD 治理平台](#7-ivd-治理平台)
* [8\. 部署与嵌入](#8-部署与嵌入)
* [9\. 说明](#9-说明)
* [10\. 修改地图](#10-修改地图)
* [11\. 参考资料](#11-参考资料)
* [12\. 安全与暴露面](#12-安全与暴露面)
* [13\. 校验与交付](#13-校验与交付)

---

## 0. 现状速览

（2026-10-06 核对）

**入口:各一条命令**（下面 §2、§4 的手工步骤就是它们替你做的事）:

- **装一台新机**:`setup.cmd`（Windows 双击）/ `./setup.sh` / `python tools/setup_all.py`
  —— 克隆或构建引擎、建 `provenance/.venv-live`、按固定顺序装依赖、拉 Ollama 模型,
  最后自检。`--check` 是只读预检(不装任何东西)。重复跑是安全的:已完成的步骤会跳过并说明。
- **把各个面起起来**:`serve.cmd` / `./serve.sh` / `python tools/serve_all.py`
  —— 起 5010(默认 case01 只翻记录)+ 5002 + 5003 + 5020,逐个健康自检并打印地址。
  `--status` 只看不动;`--stop` **只停本工具起过的**那些(同时核对自己的 pid 记录与目标命令行);
  `--all` 连 8060 写面一起起;`--full` 让 5010 真跑一局,再带 `--seed <种子> --run-id <名字>`
  就是"一条命令 + 一个种子"的可复现起法(这三个参数只在 `--full` 那条分支有消费者,少了
  `--full` 会在起跑前直接拒,不静默丢掉)。
- **打包 / 校验交付件**:`tools/make_demo_zip.py`(重打)+
  `provenance/tools/verify_demo_sync.py --require-zip`(四者逐字节比对)—— 见 §13。

**分层**：场景声明（数据）→ 引擎（`case_engine/`，可注册可替换）→ 案例
（`case01/`、`case00/`）→ 内核（`mavisframework`，独立发版，v1.3.4）→ 呈现
（`packages/mavis-vizkit`，mavis 插件）。

**干预可插拔**：三个专家写端点走 `InterventionStrategy` 注册表
（`live/interventions.py`；内置 `goals`/`undo`/`mark`/`corrective_feedback`
——纠正回流：专家纠正文本写入 agent 记忆流）。新增干预 = 一个子类 + 一行
`register()`，自动经 `POST /api/intervention/{strategy_id}` 暴露，清单见
`GET /api/interventions`。旧路径（`/api/goals` 等）保留为薄壳。

**分支判定 LLM 化**：25 条 T0 回答实测——关键词规则表与实际线一致仅 1/25
（长回答必命中条件词），LLM 判定（GLM-4.7-flash，关思考）与 judge 模式记录
一致 15/16（93.8%）。规则表保留为离线降级；评估工具
`case01/tools/branch_judge_eval.py`（报告：`results/analysis/branch_judge_eval/`）。

**服务面（本机）**：

- `5010` 唯一对接入口（实时面 + 数据面：聚合 `/api/runs`、专家安全
  `/api/run-detail/...`、六个 `/embed/*` 面）
- `5002` case01 只读契约 · `5003` case00 存档只读 · `5020` 分阶段像素场景
  · `8060` 配置工具（`5004` 多场景面板已退役 —— 仅评审面板调试用）

**文档**：入口是 `provenance/docs/架构总览与对接指南.md`（状态一览）；平台侧契约是
`provenance/docs/平台对接契约_5010唯一入口.md`。

## 1. 架构

```
Provenance(平台,本仓库)
├── provenance/          # 平台本体
│   ├── live_fastapi.py  # 实时模拟 + 可视化(FastAPI + WebSocket,唯一入口)
│   ├── case_engine/     # 引擎层:场景声明 → 可注册可替换的运行方式
│   ├── cases/           # 场景声明(数据):case00_village / case01_stock / case02_minimal
│   │                    #   运行素材随各 case 自带(`case00/scenario/`、
│   │                    #   `case01/injector/scenario/`);旧的 `scenarios/` 一层已归并掉
│   ├── case01/          # 案例:投资咨询(二级市场)基线
│   ├── case00/          # 案例:村庄(已冻结,作对照与展示)
│   ├── live/            # 实时面:干预策略/网络守卫等
│   ├── frontend/        # 可视化前端(Phaser + 贴图池 agents_pool/)
│   ├── data/            # 配置与提示词
│   └── results/         # 分析报表与决策留痕(decisions.json)
├── data/                # 跑出来的数据在**仓根**这一层:case01/runs(成品记录)、case01/raw、
│                        #   case00/state、ledgers。**不入库**——新克隆这里什么都没有;
│                        #   5010 / 5002 读的是 `data/case01/runs/<run_id>/run.json`
├── handbook/            # 框架接入/配置指南(仓根 `docs/` 已于 2026-10-08 改名 handbook/)
├── packages/            # 本地包: mavis-vizkit(呈现) / mavis-case01-injector(注入器)
└── 依赖 mavisframework  # 框架(独立仓库 hellobs/mavis,以 wheel 安装)
```

平台与框架分离:mavisframework 独立维护于
[hellobs/mavis](https://github.com/hellobs/mavis),平台通过
`requirements.txt` 中的 `mavisframework>=1.2.0,<2.0.0` 依赖它。角色配置工具
(config_tool)亦属框架仓库。

## 2. 环境准备与框架安装

**前置条件**:Python ≥ 3.12;[Git](https://git-scm.com/) 在 `PATH` 上(安装器用它克隆引擎,
缺了会直接说清楚);以及 [uv](https://docs.astral.sh/uv/) 或 [conda](https://docs.conda.io/),
纯 `pip` 同样可用。安装器构建/挑选的引擎 wheel **与引擎仓声明的版本一致** ——
若 `../mavis/dist` 里只有旧版本,它会重建,而不是悄悄装上旧引擎。

**一条命令(推荐)。** 在仓根执行:

```bash
# Windows:双击 setup.cmd        (命令行里也可以:setup.cmd --check)
./setup.sh                               # macOS / Linux
python tools/setup_all.py                # 任意平台 —— 同一个东西
```

它会替你做完下面六步然后自检:克隆/构建引擎、建环境、**按顺序**装齐依赖、拉 Ollama 模型、
打印下一步。重复跑安全 —— 已完成的步骤会跳过并说明。只读预检(不装东西、只告诉你缺什么):

```bash
python tools/setup_all.py --check
```

值得知道的旗标:`--yes`(不再问)、`--skip-models`(跳过约 8 GB 模型下载)、
`--editable-engine`(改用 `-e ../mavis` 就地安装)、`--venv-name`、`--models a,b`、
`--install-ollama`、`--api-key sk-...`。

下面是它自动化的手工步骤 —— 留作参考与排障。按顺序执行:

平台依赖框架 `mavisframework>=1.2.0,<2.0.0`(不在 PyPI,需从源码构建)。不要低于 1.2.0:
1.0.0 早于 case01 需要的三处注入钩子(external_state / interaction_request /
role_directive),用钩子构造 Simulator 会直接 TypeError;1.1.0 早于通用插件面
(mavisframework.plugin、Simulator(plugins=)、agent_core.subscribe_chat_line)。
CI 会先用 `tools/check_engine_baseline.py` 断言插件面存在,再跑测试。按顺序执行:

```bash
# 2.1 克隆框架仓库并构建 wheel(装进平台环境)
# 推荐 HTTPS(无需 SSH 密钥);若已配置 SSH 密钥也可用 SSH 方式
git clone https://github.com/hellobs/mavis.git ../mavis
# 或: git clone git@github.com:hellobs/mavis.git ../mavis
cd ../mavis
uv build                              # 生成 dist/mavisframework-1.3.4-py3-none-any.whl
#   pip 用户(没装 uv):pip install build && python -m build --wheel
cd ../provenance

# 2.2 创建环境(uv 或 conda;Python 3.12)
uv venv .venv --python 3.12
#   conda 用户:conda create -n provenance python=3.12 && conda activate provenance

# 2.3 按顺序装依赖(**顺序不能反**;三条都做完才算装好)
#     a) 框架(2.1 构建的 wheel;开发期也可 `pip install -e ../mavis` 可编辑安装)
uv pip install ../mavis/dist/mavisframework-1.3.4-py3-none-any.whl
#     b) 本仓的两个**本地包**(不在 PyPI;漏这步,下一步会报 "No matching distribution found")
uv pip install -e packages/mavis-vizkit -e packages/mavis-case01-injector
#     c) 其余运行依赖 + 测试框架(requirements.txt **不含** pytest)
uv pip install -r requirements.txt pytest
```

> 需要 [uv](https://docs.astral.sh/uv/) 或 [conda](https://docs.conda.io/);`uv pip` 换成 `pip` 同样适用。
>
> **自检**(仓库根有 `pytest.ini` 设了 `pythonpath=provenance`,所以从仓库根或 `provenance/` 目录跑都行):
> ```bash
> pytest tests                                          # 外层:接口/页面/守卫
> pytest provenance/case_engine/tests provenance/case01/tests
> ```
>
> **新克隆的仓库里没有运行数据。** `data/case01/runs/` 与
> `data/case00/state/` 是有意不入库的(体量大,且曾被误提交过),
> 所以 5010 实时面启动时是空的,少数依赖证据的测试会 **skip 而不是跑** ——
> 这是预期行为,不是环境没配好。自己产出数据:`python -m case01.run --run-id <id>`
> (每条约 1-2 分钟),或批量 `python -m case01.tools.batch_run --duration-min 180
> --lanes 3 --model qwen3:8b`。缺数据时测试会说明它要哪个路径
> (例如 `tests/test_metric_semantics.py` 指向 `data/case00/state`)——
> 用你自己跑出来的数据喂它,或把项目提供的 demo 包解压进 `data/case01/runs/`
> (见 `provenance/docs/架构总览与对接指南.md` —— 它在**包内** `provenance/docs/`,
> 不是仓根的 `handbook/`)。
>
> **一条命令都还没跑就想看到记录**:本仓入库了两条 fixture 记录,把读侧指过去即可
> (环境变量必须是**绝对路径**):
> `CASE01_RUNS_ROOT=<仓根>/provenance/case01/tests/fixtures/records`。
> 干净检出上实测:`/api/runs` 报 `count=2`,两条的详情与 full-context 都能出。
> 不设它也**不是故障**——面板照样 200、只是列表为空(无 traceback)。
>
> **提交信息守卫**:`.githooks/commit-msg` 入库了但**默认不生效**,每个克隆要开一次:
> `git config core.hooksPath .githooks`。(本仓口径:文档与提交信息只写角色词
> —— 平台侧/需求方/实现侧/研究侧 —— 不写真实人名。)

## 3. 配置大模型(二选一)

- **本地 Ollama**(免费,推荐开发调试):安装 [Ollama](https://ollama.com/) 并拉取模型

  ```bash
  ollama pull qwen3:4b-instruct-2507-q4_K_M
  ollama pull qwen3-embedding:0.6b-q8_0
  ```

  无需改配置(默认即为 Ollama)。

- **OpenRouter 等外部 API(需 key)**:最省事的是一条命令(写入 + 联网自检,key 不打印):

  ```bash
  python provenance/tools/setup_api.py --key sk-xxxx
  python provenance/tools/setup_api.py --show      # 只看当前 key 从哪来
  ```

  也可以照仓库根的 `.env.example` 复制成 `.env` 并填 `OPENROUTER_API_KEY=...` ——
  `.env` 会被**自动读取**(不覆盖系统环境变量),不必懂"环境变量怎么设";
  `.env` 与 `.secrets.json` 都已 gitignore。解析顺序:`环境变量 → .env → .secrets.json`
  (仓库根 / 包根)。

## 4. 实时模拟

```bash
cd provenance/provenance
python live_switch.py --start case01        # 5010 是唯一实时入口(case00/case01 互斥共用)
# 只看界面、不跑推演:python live_switch.py --start case00 --no-sim
```

浏览器打开 http://127.0.0.1:5010/ 。只读面 `python -m case00.serve --port 5003` /
`python -m case01.serve --port 5002`;配置工具在本仓 `provenance/config_tool`:
`cd provenance/config_tool && python app.py`(8060),默认直接发现同仓平台目录,无需设环境变量。

**专家/评审看结果**先读 `provenance/docs/架构总览与对接指南.md`:
结论边界、面归属(哪些页面只在 case01/case00 面)、两个只读数据口、字段读法、
两张人工标注表怎么用,以及一份十五分钟走查。

## 5. 角色配置

角色/关系/剧情通过网页表单配置(免手写 JSON)。工具位于本仓库:

```bash
cd provenance/config_tool
python app.py
```

浏览器打开 http://127.0.0.1:8060/

- `/` — 角色配置表单:填写角色信息,生成标准 JSON(自动校验,成功后清除草稿)
- `/relationships` — 关系录入(追加到 relationships.json)
- `/story` — 剧情录入(追加到 story.json)
- `/agents` — 已配置角色列表

字段清单见 `provenance/config_tool/角色字段清单.md`。config_tool 产物默认写入本平台的
`provenance/frontend/static/assets/village/agents/` 与 **case 自己的 `scenario/`**
(可通过环境变量 `MAVIS_ASSETS_ROOT` 覆盖;`MAVIS_SCENARIOS_DIR` **已废弃且被忽略**)。
新增角色后,重启仿真服务器(5010)即可让新角色进入模拟。

## 6. 运行参数

| 参数 | 说明 |
|---|---|
| `--name` | 模拟名称(唯一,存档按此分目录) |
| `--start` | 起始时间 |
| `--stride` | 每步游戏分钟数(2 较细腻) |
| `--step` | 步数,`0`=持续运行 |
| `--resume` | 从断点续跑 |
| `--port` | 服务端口 |

## 7. IVD 治理平台

本平台是 IVD"过程对齐"叙事的参考实现:AI 价值形成可被观察、可治理、可审计。

### 7.1 制度层(governance.json)

专家设定的约束/期望存放于 `provenance/governance.json`(不在 AI 本体中)。每个角色对应一个 `{目标: 权重}` 向量,总和为 1。目标名采用**行为绑定**设计(语义可区分,embedding 反馈才能分辨行动——如 "Risk Control" 对应压力测试、"Data Rigor" 对应交叉核验):

```json
{ "roles": { "AI投顾助手": { "Serve Users": 0.35, "Compliance Rigor": 0.3, "Risk Control": 0.2, "Data Rigor": 0.15 } } }
```

每个角色的 `agent.json` 同时携带 `initial_tendency`(人物底色,与约束略有偏移)。`--resume` 续跑时,`value_tendency` 与体验计数从检查点恢复,倾向曲线跨重启连续。

约束不进入提示词;它只加权后果反馈,因此专家调整约束后,倾向需经后续体验才逐步收敛(滞后收敛 = 内化证据)。

### 7.2 治理面板(实时调整)

浏览器右侧面板供专家:

- **查看**各角色的价值倾向(内化结果,只读):实时曲线,每个约束目标一条线,并叠加**分段阶梯虚线**(约束期望,在每次干预时刻跳变)与干预竖线;
- **调整**约束权重(滑条,强制总和为 1;**松开滑条才提交**,拖动过程不产生干预记录);
- **导出**倾向曲线 PNG:走后端 `GET /api/export-chart?agent=...`(matplotlib 渲染,含分段约束虚线、紧凑底部图例)。

### 7.3 审计链

- `interventions.json`:每次专家干预的记录(`time/sim_time/agent/old_constraints/new_constraints/operator`);
- `decisions.json`:逐步决策流,含 `goal_alignment`(即时对齐)与 `value_tendency`(累积倾向);
- 倾向曲线本身:干预与倾向收敛之间的滞后,是内化发生的可观测证据。

### 7.4 机制概要

行动 → 与**行为绑定目标**的 embedding 相似度 → 相对占比 × 权重 → 滑动窗口 → 倾向(与人物底色混合)→ 提示词 → 行动。目标名刻意设计为语义可区分(见 7.1);剧情事件与角色日程轮换行为,保持曲线活跃而非平坦。形式化见运行方式 README 第 7 节。

### 7.5 可解释性面板(`/api/explain`)

`GET /api/explain?agent=<角色名>` 返回三层解释,回答"该角色的价值倾向为什么是当前值":

1. **构成分解** — `倾向 = α×人物底色 + (1−α)×体验窗口均值`(逐目标,含 α 与累计体验次数);
2. **窗口明细** — 最近体验逐条展示(行动描述、逐目标对齐度、反馈值),解释哪些行动把倾向拉向哪;
3. **干预因果链** — 每次专家干预的约束跳变、干预前/后 2 小时倾向、量化迁移量(内化滞后证据)。

前端通过每个角色的"解释倾向成因"按钮展示。

## 8. 部署与嵌入

### 8.1 运行依赖

| 组件 | 说明 |
|---|---|
| Python 3.12 + venv | `pip install -r requirements.txt` + 构建/安装 mavis wheel |
| 大模型 | 本地 Ollama(qwen3-instruct + qwen3-embedding)**或** OpenAI 兼容 API(data/config.json 配置,见第 3 节) |
| 前端资源 | 已本地化(`static/vendor/`:phaser/jquery/bootstrap)——无 CDN 依赖,可离线/内网部署 |

### 8.2 启动服务

```bash
# 在 provenance/provenance 目录下
python live_fastapi.py --name stock-en6 --resume --step 0 --port 5010
# 全新模拟:去掉 --resume(从配置日期开始);--step 0 = 无限运行
```

对外部平台嵌入时,建议用反向代理(nginx/caddy)提供 HTTPS。服务自包含(FastAPI + WS + 静态资源),无需构建步骤。

### 8.3 嵌入外部平台(iframe)

服务提供**嵌入专用路由**——复用同一 WebSocket/数据,隐藏无关 UI 的精简页。任意 Web 平台(如治理看板)用 `<iframe>` 引用即可;iframe 内的页面自行连接自己的 WS,无需配置 CORS。

| 路由 | 内容 |
|---|---|
| `/embed/scene` | 仅 Phaser 场景(无浮动面板)——用于"仿真画面"位置 |
| `/embed/goals` | 仅治理面板(滑条 + 倾向曲线 + 解释按钮) |
| `/embed/explain` | 治理面板并自动展开解释面板 |
| `/embed/timeline` | 干预时间线面板(全角色、整页)——用于"审计留痕"位置 |

示例(React/Next.js):

```jsx
<iframe src="https://sim.example.com/embed/scene" style={{width:'100%',height:'480px',border:0}} />
<iframe src="https://sim.example.com/embed/goals" style={{width:'380px',height:'70vh',border:0}} />
```

部署形态:provenance 独立域名运行,宿主平台 iframe 嵌入。两套代码库相互独立,共享同一实时模拟。

## 9. 说明

- 实时可视化通过 WebSocket(`/ws`)推送框架契约消息(agent/time/chat_line/snapshot);客户端看门狗自动恢复死连接。服务端**独立心跳任务**每 5 秒无条件发送(asyncio 定时,不依赖队列空——建日程等纯 LLM 空档期也可靠),客户端 20 秒无消息判定断开 + 回焦检查
- 实时服务由 mavisframework(Game + Simulator + LiveCompressor)驱动
- **API**:
  - `GET /api/goals` — 约束/倾向/干预(按 `simulation` 字段仅返回当前模拟)/角色类型/embedding 健康度
  - `POST /api/goals` — 专家改约束 → 写 governance.json + interventions.json 审计(带 `simulation` 标记);拒绝数字/零权重垃圾目标;权重和须为 1
  - `POST /api/undo-intervention` — 按 agent + sim_time + 记录时间回滚某次干预到其 `old_constraints`;追加一条 `operator=undo` 审计并把原记录标 `revoked` —— 历史只增不删,只作修订
  - `GET /api/timeline` — 全角色干预时间线(按 sim_time 排序):每条含 old→new 约束、note、operator 与倾向迁移量(与 `/api/explain` 同一窗口算法);revoked/undo 事件带标记供前端区分
  - `GET /api/export-chart?agent=<名>` — matplotlib 倾向曲线 PNG
  - `GET /api/explain?agent=<名>` — 可解释性面板:倾向构成分解(α 混合)/体验窗口明细(行动·对齐度·反馈)/干预因果链(每次干预后的倾向迁移量)。checkpoint 序列加载按目录 mtime 缓存
- 决策导出:模拟过程自动生成 decisions.json(时间/角色/动作/涉他/重要性),供决策平台与专家界面使用
- **测试**:`tests/test_live_api.py`(pytest,假 server 注入,无需真实模拟/LLM)覆盖 goals 读写、干预跨模拟隔离、explain 三层、export 错误处理;运行方式测试在 mavis 仓库 `tests/`
- 前端 Phaser 脚本:服务端优先使用本地 `frontend/static/vendor/phaser.min.js`(离线可用),不存在时回退 CDN。离线环境建议首次运行前下载 `https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.min.js`(约 1.3MB)放入该目录
- 界面/提示词本地化:修改框架的 `mavisframework/prompt/scratch.py` 与前端文案即可,逻辑无需改动
- **角色/场景配置工具**:角色、关系、剧情事件通过本仓 `provenance/config_tool/` 的表单式工具生成(独立进程,端口 **8060**)。产物落三处(仓里**没有** `scenarios/` 这一层,它已于 2026-10-08 归并):角色 → `provenance/frontend/static/assets/village/agents/<角色>/agent.json`;关系/剧情/制度层权重 → 各 case 自己的运行期素材目录(`provenance/case00/scenario/` 或 `provenance/case01/injector/scenario/`);场景声明 → `provenance/cases/<case_id>/scenario.yaml`

## 10. 修改地图

1. 参考原始 generative_agents 项目中 maze.py 的逻辑,修改现有代码以兼容 tiled 编辑器导出的 json/csv 数据文件
2. 参考现有 maze.json 格式,编写代码合并 tiled 导出的 maze_meta_info.json、collision_maze.csv、sector_maze.csv 等文件,为新地图生成 maze.json
3. **推荐**:使用随仓库携带的转换工具 `tools/tilemap_to_maze.py`(纯 CLI、无外部依赖)——直接把 Tiled 的 `.tmx`/`.json` 地图转换为 `maze.json`(详见 `tools/tilemap_to_maze_README.md`)。旧 GUI 工具见 https://github.com/jiejieje/tiled_to_maze.json

## 11. 参考资料

- 论文:[Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)
- 代码:[mavisframework(自研框架)](https://github.com/hellobs/mavis) / [Generative Agents(原始项目)](https://github.com/joonspk-research/generative_agents) / [wounderland](https://github.com/Archermmt/wounderland)
- 地图工具:仓库自带 `tools/tilemap_to_maze.py` / [tiled_to_maze(旧 GUI)](https://github.com/jiejieje/tiled_to_maze.json)

## 12. 安全与暴露面

（2026-09-23）

**这些服务没有任何鉴权**:能连上端口的人可以读走全部成品记录;5010 还能改治理约束、
撤销干预、标记反思、重开一局(共 **4 个**写端点);8060(配置工具)还能改场景、删角色、执行一次运行。所以:

- **默认只绑本机**(`127.0.0.1`)。要绑非本机地址必须显式声明 `LIVE_ALLOW_REMOTE=1`,
  否则进程**拒绝启动**并把暴露面逐条列出来(不是打一句 warning 就放行)。
  **所有已交付的入口都执行这条**(5020 布景播放器是最后一个接上的,2026-10-06),
  所以误写 `--host 0.0.0.0` 会**响亮地失败**,而不是悄悄把端口开出去。
- **跨源白名单默认只给本机来源**(未设 `EMBED_ALLOW_ORIGINS` 时)。平台侧跨源取数请显式设
  `EMBED_ALLOW_ORIGINS=https://<平台域名>`;**iframe 嵌入本身不走 CORS,不受影响**。
- **跨机对接优先"不要暴露端口"**:同机部署、只读反向代理(只代理 `/embed/*` 与 `GET /api/*`)、
  或 SSH 隧道把 5010 转给对方。确需直连时至少做到:白名单 + 防火墙按来源 IP 放行,
  并且**绝不要把 8060 放出去**。
- 密钥:OpenRouter key 在 `case01/.secrets.json`(已 gitignore,未入库);实测 5000+ 产物与日志文件里
  没有密钥痕迹。路径穿越守卫见 `live/netguard.py` 与 mavis 的 `config_tool/_safe_agent_name()`。

代码位置:`live/netguard.py`(绑定与白名单策略)、`tests/test_netguard.py`(行为断言)。
对接细节见 `provenance/docs/平台对接契约_5010唯一入口.md`,体检结论见 `核验主张与证据说明书.md`**（提交件，不随仓发布；副本在 `GTC/archive/`）**。

## 13. 校验与交付

三件事都由机器可查 —— 别靠记忆:

```bash
python tools/setup_all.py --check                          # ① 环境(只读)
python tools/serve_all.py --status                         # ② 各面在不在(端口 + HTTP)
python tools/serve_all.py --stop                           #    只停本工具起过的那些
python provenance/tools/verify_demo_sync.py --require-zip   # ③ 交付件一致性(四者逐字节)
```

`--require-zip` 是关键:不加它,**压缩包缺失**会被报成"不适用",于是"删掉包"看起来像通过。
重打是一条命令(`python tools/make_demo_zip.py`,先加 `--dry-run` 可以只看条目);
按顺序的交付清单在 `10月15日演示_运行手册.md`**（提交件，不随仓发布；副本在 `GTC/archive/`）**。

**克隆下来没有的东西**:`data/case01/runs/` 与 `data/case00/state/`
是**故意 gitignore 的**(体积大,且曾经误提交过),所以本仓带的是**派生分析**
(`provenance/results/analysis/`,已入库),**不含其背后的原始记录**。
因此依赖原始记录的主张**无法只靠一次克隆复算** —— 这条边界连同"复现三档口径"写在
`核验主张与证据说明书.md`**（提交件，不随仓发布；副本在 `GTC/archive/`）** 里。

## 许可证

Apache License 2.0，见 [LICENSE](LICENSE)。
