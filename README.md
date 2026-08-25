# Provenance

[English](./README_en.md) | 简体中文

基于自研 [mavisframework](https://github.com/hellobs/mavis) 构建的多智能体仿真平台,
面向"AI 价值形成过程可解释、可治理"的演示(Global Trust Challenge)。应用场景为投资咨询(二级市场):
智能体在空间环境中自主决策、移动、对话,每一步可配置、可解释、可实时可视化。

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

## 2. 环境准备

需要 [uv](https://docs.astral.sh/uv/) 或 [conda](https://docs.conda.io/):

```bash
# uv
cd provenance
uv venv .venv --python 3.12
uv pip install -r requirements.txt

# conda
conda create -n provenance python=3.12
conda activate provenance
pip install -r requirements.txt
```

`requirements.txt` 依赖 `mavisframework==1.0.0`,安装前需先构建框架 wheel(见下一节)。

## 3. 安装框架依赖

```bash
# 方式 A:从源码构建 wheel 并安装(推荐,已验证稳定)
git clone git@github.com:hellobs/mavis.git ../mavis
cd ../mavis && uv build && uv pip install dist/mavisframework-1.0.0-py3-none-any.whl
cd ../provenance

# 方式 B:可编辑安装(开发框架时即时生效;注意框架 README 中记录的 import 异常)
# uv pip install -e ../mavis
```

## 4. 配置大模型(二选一)

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

## 5. 实时模拟

```bash
cd provenance/provenance
python live_fastapi.py --name sim-test --start "20250213-09:30" --stride 2 --step 0 --port 5001
```

浏览器打开 http://127.0.0.1:5001/

## 6. 角色配置

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

## 7. 运行参数

| 参数 | 说明 |
|---|---|
| `--name` | 模拟名称(唯一,存档按此分目录) |
| `--start` | 起始时间 |
| `--stride` | 每步游戏分钟数(2 较细腻) |
| `--step` | 步数,`0`=持续运行 |
| `--resume` | 从断点续跑 |
| `--port` | 服务端口 |

## 8. 说明

- 实时可视化通过 WebSocket(`/ws`)推送框架契约消息(agent/time/chat_line/snapshot);浏览器断线 3 秒后自动重连
- 实时服务由 mavisframework(Game + Simulator + LiveCompressor)驱动
- 决策导出:模拟过程自动生成 decisions.json(时间/角色/动作/涉他/重要性),供决策平台与专家界面使用
- 前端 Phaser 脚本:服务端优先使用本地 `frontend/static/vendor/phaser.min.js`(离线可用),不存在时回退 CDN。离线环境建议首次运行前下载 `https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.min.js`(约 1.3MB)放入该目录
- 界面/提示词本地化:修改框架的 `mavisframework/prompt/scratch.py` 与前端文案即可,逻辑无需改动

## 9. 修改地图

1. 参考原始 generative_agents 项目中 maze.py 的逻辑,修改现有代码以兼容 tiled 编辑器导出的 json/csv 数据文件
2. 参考现有 maze.json 格式,编写代码合并 tiled 导出的 maze_meta_info.json、collision_maze.csv、sector_maze.csv 等文件,为新地图生成 maze.json
3. 使用地图标注工具:https://github.com/jiejieje/tiled_to_maze.json

## 10. 参考资料

- 论文:[Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)
- 代码:[mavisframework(自研框架)](https://github.com/hellobs/mavis) / [Generative Agents(原始项目)](https://github.com/joonspk-research/generative_agents) / [wounderland](https://github.com/Archermmt/wounderland)
