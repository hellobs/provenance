# 业务层:投资场景(investment)

业务层 = 框架的"输入",换业务只改这里,框架层零改动。

## 目录说明

```
scenarios/investment/
├── relationships.json   # 角色关系(邻接表):谁找谁、何时、频率
├── story.json           # 剧情事件(危机/冲突注入):时间/类型/影响/期望行为
├── agents/              # (可选)角色 agent.json 独立副本
│                        #   当前复用 frontend/static/assets/village/agents/
└── scene/               # (可选)场景 maze.json 独立副本
                         #   当前复用 frontend/static/assets/village/maze.json
```

制度层约束不在此目录:期望目标权重在 `provenance/governance.json`
(治理面板实时可调,IVD 机制见引擎 README §7)。

## 当前角色(6)

| 角色 | 类型 | 职位 | 关系 |
|---|---|---|---|
| AI投顾助手 | ai_tool | AI 投资顾问 | 服务用户 + 合规披露(制度内建,出厂=约束) |
| 沈砚之 | user | 首席投资顾问 | 老周咨询对象、林晚晴汇报对象 |
| 苏清越 | user | 量化交易分析师 | 与陈慕白数据协作 |
| 陈慕白 | user | 行业研究员 | 与苏清越交叉验证 |
| 林晚晴 | user | 风控合规专员 | 向沈砚之提交风险评级 |
| 老周 | user | 资深散户投资者 | 主动找沈砚之咨询 |

角色有 `initial_tendency`(人物初始底色,agent.json),制度约束
(governance.json)决定"该关心哪些价值",体验内化出 value_tendency。

## 剧情事件(story.json)

| id | 时间 | 类型 | 影响 | 目的 |
|---|---|---|---|---|
| s-001 | 10:00 | 市场波动 | all | 监管要求评估风险 → 全员转向风控 |
| s-002 | 15:00 | 客户投诉 | 老周、沈砚之 | 解释决策依据 + 复核风险披露 |
| s-003 | 10:10 | 客户要求 | AI投顾助手 | 高风险高收益产品 → 服务 vs 合规的价值冲突 |

剧情事件注入高重要性记忆,牵引角色行动——也是 IVD 演示中
"环境/剧情改变价值取向"的驱动源。

## 关系配置如何生效(规划)

1. `relationships.json` → 注入相关角色 daily_plan("下午3点找沈砚之")→ 空间共现
2. 可选强约束:`frequency=high` 的关系,对话决策直接放行(必聊)

## 剧情配置如何生效(规划)

`story.json` → 指定模拟时间往环境注入事件 → agent 感知 → 触发反应(评估/开会/对话)
