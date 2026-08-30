# Provenance

[English](./README.md) | 简体中文

基于自研 [mavisframework](https://github.com/hellobs/mavis) 构建的多智能体仿真平台,
面向"AI 价值形成过程可解释、可治理"的演示(Global Trust Challenge)。应用场景为投资咨询(二级市场):
智能体在空间环境中基于情境作判断、移动、对话,每一步可配置、可解释、可实时可视化。

## 1. 架构

```
Provenance(平台,本仓库)
├── provenance/          # 平台本体
│   ├── live_fastapi.py  # 实时模拟 + 可视化(FastAPI + WebSocket,唯一入口)
│   ├── frontend/        # 可视化前端(Phaser + 贴图池 agents_pool/)
│   ├── scenarios/       # 业务场景配置(investment: 角色/关系/剧情)
│   ├── data/            # 配置与提示词
│   └── results/         # 存档与决策留痕(decisions.json)
└── 依赖 mavisframework  # 框架(独立仓库 hellobs/mavis,以 wheel 安装)
```

平台与框架分离:mavisframework 独立维护于
[hellobs/mavis](https://github.com/hellobs/mavis),平台通过
`requirements.txt` 中的 `mavisframework==1.0.0` 依赖它。角色配置工具
(config_tool)亦属框架仓库。

## 2. 环境准备与框架安装

平台依赖框架 `mavisframework==1.0.0`(不在 PyPI,需从源码构建)。按顺序执行:

```bash
# 2.1 克隆框架仓库并构建 wheel(装进平台环境)
# 推荐 HTTPS(无需 SSH 密钥);若已配置 SSH 密钥也可用 SSH 方式
git clone https://github.com/hellobs/mavis.git ../mavis
# 或: git clone git@github.com:hellobs/mavis.git ../mavis
cd ../mavis
uv build                              # 生成 dist/mavisframework-1.0.0-py3-none-any.whl
cd ../provenance

# 2.2 创建环境并安装依赖(uv 或 conda)
# uv
uv venv .venv --python 3.12
uv pip install ../mavis/dist/mavisframework-1.0.0-py3-none-any.whl
uv pip install -r requirements.txt

# conda
conda create -n provenance python=3.12
conda activate provenance
pip install ../mavis/dist/mavisframework-1.0.0-py3-none-any.whl
pip install -r requirements.txt
```

> 需要 [uv](https://docs.astral.sh/uv/) 或 [conda](https://docs.conda.io/)。
>
> **开发/协作期可改用可编辑安装**(改框架代码即时生效,框架更新后 `git pull` 即可,无需重装):
> ```bash
> uv pip install -e ../mavis   # 或 pip install -e ../mavis
> ```
> 可编辑安装与 wheel 安装二选一,均可正常使用(详见框架 README 的版本管理章节)。

## 3. 配置大模型(二选一)

- **本地 Ollama**(免费,推荐开发调试):安装 [Ollama](https://ollama.com/) 并拉取模型

  ```bash
  ollama pull qwen3:4b-instruct-2507-q4_K_M
  ollama pull qwen3-embedding:0.6b-q8_0
  ```

  无需改配置(默认即为 Ollama)。

- **DeepSeek API**:在 `provenance/.env` 中配置

  ```
  LLM_API_KEY=你的key
  ```

  并编辑 `provenance/data/config.json` 的 `agent.think.llm`:

  ```json
  "llm": {
    "provider": "openai",
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": ""
  }
  ```

## 4. 实时模拟

```bash
cd provenance/provenance
python live_fastapi.py --name sim-test --start "20250213-09:30" --stride 2 --step 0 --port 5001
```

浏览器打开 http://127.0.0.1:5001/

## 5. 角色配置

角色/关系/剧情通过网页表单配置(免手写 JSON)。工具位于框架仓库:

```bash
cd ../mavis/config_tool
python app.py
```

浏览器打开 http://127.0.0.1:5002/

- `/` — 角色配置表单:填写角色信息,生成标准 JSON(自动校验,成功后清除草稿)
- `/relationships` — 关系录入(追加到 relationships.json)
- `/story` — 剧情录入(追加到 story.json)
- `/agents` — 已配置角色列表

字段清单见 `../mavis/config_tool/角色字段清单.md`。config_tool 产物默认写入本平台的
`provenance/frontend/static/assets/village/agents/` 与 `provenance/scenarios/`
(可通过环境变量 `MAVIS_ASSETS_ROOT` / `MAVIS_SCENARIOS_DIR` 覆盖)。
新增角色后,重启仿真服务器(5001)即可让新角色进入模拟。

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

行动 → 与**行为绑定目标**的 embedding 相似度 → 相对占比 × 权重 → 滑动窗口 → 倾向(与人物底色混合)→ 提示词 → 行动。目标名刻意设计为语义可区分(见 7.1);剧情事件与角色日程轮换行为,保持曲线活跃而非平坦。形式化见引擎 README 第 7 节。

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
python live_fastapi.py --name stock-en6 --resume --step 0 --port 5001
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

示例(React/Next.js):

```jsx
<iframe src="https://sim.example.com/embed/scene" style={{width:'100%',height:'480px',border:0}} />
<iframe src="https://sim.example.com/embed/goals" style={{width:'380px',height:'70vh',border:0}} />
```

部署形态:provenance 独立域名运行,宿主平台 iframe 嵌入。两套代码库相互独立,共享同一实时模拟。

## 9. 说明

- 实时可视化通过 WebSocket(`/ws`)推送框架契约消息(agent/time/chat_line/snapshot);客户端看门狗自动恢复死连接(服务端每 5 秒心跳、20 秒无消息判定断开、回焦检查)
- 实时服务由 mavisframework(Game + Simulator + LiveCompressor)驱动
- **API**:`GET /api/goals`(约束/倾向/干预/角色类型/embedding 健康度)、`POST /api/goals`(专家改约束 → 写 governance.json + interventions.json 审计,拒绝数字/零权重垃圾目标)、`GET /api/export-chart?agent=<名>`(matplotlib PNG)
- 决策导出:模拟过程自动生成 decisions.json(时间/角色/动作/涉他/重要性),供决策平台与专家界面使用
- 前端 Phaser 脚本:服务端优先使用本地 `frontend/static/vendor/phaser.min.js`(离线可用),不存在时回退 CDN。离线环境建议首次运行前下载 `https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.min.js`(约 1.3MB)放入该目录
- 界面/提示词本地化:修改框架的 `mavisframework/prompt/scratch.py` 与前端文案即可,逻辑无需改动

## 10. 修改地图

1. 参考原始 generative_agents 项目中 maze.py 的逻辑,修改现有代码以兼容 tiled 编辑器导出的 json/csv 数据文件
2. 参考现有 maze.json 格式,编写代码合并 tiled 导出的 maze_meta_info.json、collision_maze.csv、sector_maze.csv 等文件,为新地图生成 maze.json
3. 使用地图标注工具:https://github.com/jiejieje/tiled_to_maze.json

## 11. 参考资料

- 论文:[Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)
- 代码:[mavisframework(自研框架)](https://github.com/hellobs/mavis) / [Generative Agents(原始项目)](https://github.com/joonspk-research/generative_agents) / [wounderland](https://github.com/Archermmt/wounderland)

