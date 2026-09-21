# case01 injector 用的 mavis 场景（生成物）

> **状态**:现行
> **最后核对**:2026-09-21
> **说明**:注入器场景(生成物)说明
> **最后核对**:2026-09-21
> **说明**:注入器场景(生成物)说明

按 case01 的角色设定生成的最小场景：两个角色（Investment AI / Ethan Lin）、复用现有投资场景的地图。
要换布局或换角色，替换本目录内容即可，桥接代码不用改。

## 结构

- `config.json`：agent_base（感知/日程/LLM/对话参数）。默认 `associate.embedding.provider=simple`
  （不引入 llama-index），`poignancy_max` 设得很大以抑制阶段性反思（少花 LLM 调用）。
- `agents/<角色名>/agent.json`：角色设定，含 `role_directive`（角色指令，进入提示词）。
- `relationships.json`、`maze.json`：关系与地图。
- `story.json` 不由文件提供：injector 按节点生成并注入，事件带 `condition.type=case01_node`。

## 两个角色的差异

- Investment AI：`role_type=ai_tool`、本地 Ollama；`role_directive` 要求长回答（证据、来源独立性、不确定性、改判条件）。
- Ethan Lin：`role_type=user`；默认与 agent_base 同后端。若要让他走外部 API，用环境变量注入，不要写进仓库：
  设置 `CASE01_ETHAN_BASE_URL`、`CASE01_ETHAN_MODEL`、`CASE01_ETHAN_API_KEY`，桥接会在装配时写入他的 `think.llm`。
  `role_directive` 要求短问句、不主动披露隐私。

## 校验与运行

- 场景可按 mavis validator 校验（`ScenarioConfig(validate=True)` 的字段要求已满足）。
- 存档默认落在本目录下的 `checkpoints/`（已 gitignore），不会污染仓库根目录。
