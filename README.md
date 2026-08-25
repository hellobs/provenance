# Provenance — 生成式智能体仿真平台

基于自研 [mavisframework](https://github.com/hellobs/mavis) 框架构建的多智能体仿真平台。
面向"AI 价值形成过程可解释、可治理"的演示(Global Trust Challenge),场景为投资咨询(二级市场):
智能体在空间里自主决策、移动、对话,每一步可配置、可解释、可实时可视化。

## 架构

```
Provenance(平台,本仓库)
├── provenance/          # 平台本体
│   ├── live_fastapi.py  # ★ 实时模拟 + 可视化(FastAPI + WebSocket,唯一入口)
│   ├── frontend/        # 可视化前端(Phaser + 贴图池 agents_pool/)
│   ├── scenarios/       # 业务场景配置(investment: 角色/关系/剧情)
│   ├── data/            # 配置与提示词
│   └── results/         # 存档与决策留痕(decisions.json)
└── 依赖 mavisframework  # 框架(独立仓库 hellobs/mavis,以 wheel 安装)
```

**平台与框架分离**:框架(mavisframework)独立维护于 [hellobs/mavis](https://github.com/hellobs/mavis),
平台通过 `requirements.txt` 中的 `mavisframework==1.0.0` 依赖它。角色配置工具(config_tool)也属框架仓库。

## 快速开始

### 1. 环境准备

需要 [uv](https://docs.astral.sh/uv/) 或 [conda](https://docs.conda.io/):

```bash
# 用 uv(更快)
cd provenance
uv venv .venv --python 3.12
uv pip install -r requirements.txt

# 或使用 conda
conda create -n provenance python=3.12
conda activate provenance
pip install -r requirements.txt
```

> `requirements.txt` 依赖 `mavisframework==1.0.0`。安装前需先构建框架 wheel(见下节)。

### 2. 安装框架依赖(mavisframework)

框架在独立仓库,先克隆/构建:

```bash
# 方式 A:从源码构建 wheel 并安装(推荐,已验证稳定)
git clone git@github.com:hellobs/mavis.git ../mavis
cd ../mavis && uv build && uv pip install dist/mavisframework-1.0.0-py3-none-any.whl
cd ../provenance

# 方式 B:可编辑安装(开发框架时即时生效;注意当前环境 editable 有 import 怪癖,见框架 README)
# uv pip install -e ../mavis
```

### 3. 配置大模型(二选一)

- **本地 Ollama**(免费,推荐开发调试):安装 [Ollama](https://ollama.com/) 并拉取模型
  ```bash
  ollama pull qwen3:4b-instruct-2507-q4_K_M
  ollama pull qwen3-embedding:0.6b-q8_0
  ```
  无需改配置(默认就是 Ollama)。
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

### 4. 实时观看(FastAPI + WebSocket)

```bash
cd provenance/provenance
python live_fastapi.py --name sim-test --start "20250213-09:30" --stride 2 --step 0 --port 5001
```

浏览器打开 http://127.0.0.1:5001/

### 5. 角色配置(config_tool,属框架仓库)

角色/关系/剧情通过网页表单配置(免手写 JSON)。工具在 mavis 仓库:

```bash
cd ../mavis/config_tool
python app.py
```

浏览器打开 http://127.0.0.1:5002/

- `/` — 角色配置表单:填写角色信息,生成标准 JSON(自动校验,成功后清除草稿)
- `/relationships` — 关系录入(追加到 relationships.json)
- `/story` — 剧情录入(追加到 story.json)
- `/agents` — 已配置角色列表
- 字段清单见 `../mavis/config_tool/角色字段清单.md`

> config_tool 产物默认写入本平台的 `provenance/frontend/static/assets/village/agents/` 与
> `provenance/scenarios/`(通过环境变量 `MAVIS_ASSETS_ROOT` / `MAVIS_SCENARIOS_DIR` 可覆盖)。
> 新增角色后,重启仿真服务器(5001)即可让新角色进入模拟。

## 常用参数

| 参数 | 说明 |
|---|---|
| `--name` | 模拟名称(唯一,存档按此分目录) |
| `--start` | 起始时间 |
| `--stride` | 每步游戏分钟数(2 较细腻) |
| `--step` | 步数,`0`=持续运行 |
| `--resume` | 从断点续跑 |
| `--port` | 服务端口 |

## 说明

- 实时可视化走 WebSocket(`/ws`),推送框架契约消息(agent/time/chat_line/snapshot);浏览器断线 3s 后自动重连
- 实时服务由 mavisframework(Game + Simulator + LiveCompressor)驱动
- 决策导出:模拟过程自动生成 decisions.json(时间/角色/动作/涉他/重要性),供决策平台与专家界面
- 前端 Phaser 脚本:服务端优先用 `frontend/static/vendor/phaser.min.js`(本地化,断网可用),不存在时回退 CDN;离线环境下建议下载 phaser.min.js 放入该目录
  - **首次运行前**(可选但推荐):在浏览器打开 `https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.min.js`(约 1.3MB),右键另存为 `frontend/static/vendor/phaser.min.js`。之后无需外网即可显示画面
- 换用英文界面/提示词:改框架的 `mavisframework/prompt/scratch.py` 与前端文案即可,逻辑无需改动

## 修改地图

创建新地图,有以下几种方案:

1. 参考原始 generative_agents 项目中 maze.py 的逻辑,修改现有代码,以便兼容 tiled 编辑器导出的 json 和 csv 数据文件;
2. 参考现有的 maze.json 格式,编写代码用于合并 tiled 编辑器导出的 maze_meta_info.json、collision_maze.csv、sector_maze.csv 等文件,为新地图生成 maze.json。
3. `jiejieje` 开发了一款地图标注工具: https://github.com/jiejieje/tiled_to_maze.json

## 参考资料

### 论文

[Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)

### 代码

- [mavisframework(自研框架)](https://github.com/hellobs/mavis)
- [Generative Agents(原始项目)](https://github.com/joonspk-research/generative_agents)
- [wounderland](https://github.com/Archermmt/wounderland)
