# GTC Case 01 → Governance Platform 对接说明(供仝牧团队)

> 配套文件:`case01_api.openapi.yaml`(机器可读接口契约)。
> 上游设计依据:`GTC/0904doc/04_GTC_Reflection_Governance_Platform_技术集成流程.txt`
> 与 `05_GTC_HCI_增量需求说明_仝牧老师.txt`。本文只讲"Leo 这边提供什么、
> 仝牧这边需要实现什么、两边怎么对数据",不重复全文。

## 0. 一句话分工

**Leo(case01)提供只读数据:** 已完成 Run 的索引、结构化治理数据
(Raw Reflection / Router 拆分 / Audit 链)、以及给专家看的 **Full Context
自然语言全文**。
**仝牧(Governance Platform)负责:** Expert Review Task 的建单、专家池与
动态分配、2 人首轮 → 冲突追加 3 人 → 5 人多数决、Approve / Edit / Reject、
状态机与持久化、HCI 展示、训练材料池归集、Audit Log。
case01 不写平台,平台不写 case01;两边通过只读 HTTP 接口单向取数。

## 1. 服务信息

| 项 | 值 |
| --- | --- |
| 服务 | `provenance/case01/serve.py`(FastAPI,只读) |
| 地址 | `http://127.0.0.1:5002` |
| 启动 | `uvicorn case01.serve:app --host 127.0.0.1 --port 5002`(在 `D:\zzr\provenance\provenance` 下) |
| 数据根 | 默认 `case01/runs/`;可用环境变量 `CASE01_RUNS_ROOT` 覆盖 |
| OpenAPI | `GET /openapi.json`(交互文档在 `GET /docs`) |
| CORS | 开发期全放开(GET only),平台接入后建议收紧域名 |
| 鉴权 | 本阶段无;服务仅监听 127.0.0.1,不做写操作 |

一次 Run 的产物就是一个 `runs/<run_id>/` 目录,内含 `run.json`
(全部结构化记录 + 生成后的 Reflection / Router 结果)等文件。
**专家端永远不直接读这些文件/JSON**,一律经由 API。

## 2. 三个只读端点

### 2.1 `GET /api/runs` —— Run 索引

平台侧用来发现"有哪些新 Run 可以建任务"。

响应:`{"runs": [...], "count": n}`,每条含:

| 字段 | 含义 | 给专家看? |
| --- | --- | --- |
| `run_id` | 运行标识(如 `m2-ext-C2`) | 可作为内部编号 |
| `start_date` / `end_date` | 模拟起止日期 | 可显示 |
| `branch` / `branch_summary` | A/B/C 及自然语言摘要 | **否**(实验元信息,仅内部关联用) |
| `n_turns` / `n_retrievals` | 对话轮数 / 检索次数 | 可显示 |
| `has_reflection` | Reflection 是否已生成 | 是(决定能否建审核任务) |
| `router_issue_count` | Router 拆出的问题数 | 是 |

### 2.2 `GET /api/runs/{run_id}` —— Run 详情(结构化治理数据)

平台侧**建 Expert Review Task 的唯一数据源**。响应含:

- `run_id`、日期、`branch`/`branch_summary`(同上,内部用);
- `reflection`: `{generated, text}` —— Raw Reflection 原文(**原始版,
  平台必须原样保留,任何专家修改不得覆盖它**);
- `router`: `{ran, issues:[…]}` —— Router 拆分结果。每个 issue:
  `summary`(问题摘要,自然语言)、`field`(专业类别,用于匹配专家池)、
  `risk`(Low / Medium / High)、`routing_reason`(路由理由);
- `audit`: 该 Run 世界侧动作时间线(释放事件 / 买卖 / 状态推进),
  每条含 `t`(日期)、`action`、`kind`、`summary` 等——可作追溯链素材;
- 另含 `n_turns`、`n_events`、`final_feedback_date` 等计数/线索字段。

> **建单规则建议(对齐 04 五.3):** 一条 Reflection 若拆出 N 个 issue,
> 平台建 N 条 Expert Review Task,共享同一 `run_id` + 同一条 Reflection;
> 不重复生成 Reflection。若 `router.ran == false` 或 issues 为空,
> 原始 Reflection 仍应保留并进入人工审核流程(04 五.3 末句)。

### 2.3 `GET /api/runs/{run_id}/full-context` —— 专家"View Full Context"

响应:`{"run_id": …, "format": "text/plain; charset=utf-8", "full_context": "…"}`。
`full_context` 是**按 04 六.3 / 05 三 要求组装的自然语言完整记录**,段落为:

1. 案例设定(角色 / 资金 / 咨询对象);
2. T0 咨询对话(Ethan 与 Investment AI 原文);
3. Investment AI 检索到的信息(来源 / 类型 / 时间 / 二次转述统计);
4. 市场时间线与当事人状态(逐日事件 + 持仓 / 现金 / 盈亏自然语言);
5. 最终反馈(Ethan 陈述 + Investment AI 回应);
6. Investment AI 的完整原始 Reflection(如已生成);
7. 反思中识别出的待审核问题(如已生成)。

**信息边界(已由 case01 保证):** 全文不含 Branch A/B/C 标签、Branch 判定
方式、Future Timeline 预设、未披露的 Ethan 隐藏信息等实验元信息,也
不以 JSON / 字段 / event object 形式出现。平台展示时不要再叠加这些
内部字段。

## 3. 平台侧需要实现的 Expert Review 流程(状态机)

按 04 七 / 八、05 四实现;case01 只提供问题与原文,流程与界面全在平台:

```
Reflection + Router issues(经 2.2 取数)
   │  按 issue.field 匹配专家池,动态/随机分配 2 位不同专家
   ▼
pending → assigned(2/2 已审)
   │  2 人结论互相排斥(同一问题+同一专业) → disputed
   ▼
disputed → 追加 3 位未参与专家(共 5) → 多数意见 = 最终结论
   │  少数意见保留
   ▼
final → 训练材料池(完整治理记录,见 04 九)
```

- 每个 issue 的专家意见 = Approve(反思核心成立)/ Edit(有价值但有遗漏或
  表述问题,只改自己负责的问题片段)/ Reject(核心逻辑不成立)。
- **原始 Reflection 永不覆盖**;Edit 产生的是该 issue 的 reviewed/edited 版本。
- 同一 Reflection 多个 issue 属于不同专业时,各专业分别首轮 2 人;
  专业间意见差异**不视为冲突**。
- 训练材料保留完整关联:Run / 原始 Reflection / Router 结果 / 全部专家
  意见与修改 / 冲突过程与少数意见 / Audit Trail,不同状态内容明确标记、
  不得混同(04 九)。

## 4. HCI 最小实现清单(对应 05)

1. **任务队列卡片**:未点开时显示 专业类别 + 问题摘要 + 风险等级 + 状态;
2. **任务详情**:默认只显示该 issue 的摘要 / 专业 / 风险 / 路由理由 +
   相关 Reflection 片段 + Approve / Edit / Reject 操作(Edit 只改该片段);
3. **View Full Context**:调用 2.3,用普通文本/段落呈现;
4. **冲突过程视图**:disputed → 追加 3 人 → 多数/少数意见并列;
5. **追溯时间线**:按 Run / Reflection 查看 生成 → 路由 → 分配 → 审核 →
   修改 → 冲突 → 最终结论 → 归池 各节点(时间、责任人、动作、结果);
6. **训练材料状态**:该 Reflection / issue 是否已进入材料池。

## 5. 使用示例

```bash
# 1) 发现可建任务的 Run
curl http://127.0.0.1:5002/api/runs

# 2) 取某个 Run 的 Reflection + Router issues,逐 issue 建 Task
curl http://127.0.0.1:5002/api/runs/m2-ext-C2

# 3) 专家点"View Full Context"
curl http://127.0.0.1:5002/api/runs/m2-ext-C2/full-context
```

## 6. 数据更新方式

case01 侧每完成一个完整 Run(含 Reflection / Router)才落盘,且为只读
服务、**不推送事件**。平台建议:在需要时(手动刷新 / 定时轮询)
调 `GET /api/runs` 对比 `run_id` 集合,发现新 Run 后经 2.2 建任务。
审核过程中的一切写入(状态、专家意见、冲突、归池)都在平台自己的
存储里完成,无需回写 case01。

## 7. 对接验收点

- [ ] 平台能列出 Runs,并能区分"已可审核(has_reflection)"与"尚未";
- [ ] 平台能按 issue 建 Task 并保留 Raw Reflection 原文与 run 关联;
- [ ] 专家详情页默认展示 issue 摘要而非整篇 Reflection;
- [ ] View Full Context 走 `/full-context`,展示为自然语言、无实验元信息;
- [ ] Approve / Edit / Reject 落库,Edit 不覆盖原始 Reflection;
- [ ] 同问题同专业 2 人互斥 → Disputed → 追加 3 人 → 多数/少数意见保留;
- [ ] 关键节点有时间、责任人、动作、结果,可按 Run / Reflection 回溯;
- [ ] 最终结论与材料池状态可查询。

有任何字段/语义问题,直接找 Leo 沟通,不要自行补剧情或改 case01 产物。
