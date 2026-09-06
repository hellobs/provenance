# Expert Review 数据结构草案 v0(平台侧,待审改)

> 范围:Governance Platform 中 Expert Review Task、专家意见、冲突处理、
> 训练材料归集与追溯审计的数据结构。引擎侧(5002)只读,不参与本结构。
> 对齐:04 §四(优先复用 Role/Task/Decision/Audit 结构、最小扩展)、
> 04 §七/八(审核操作、冲突规则)、04 §九(归集)、04 §十(审计可追溯)。
> 状态:草案,由 ZZR 定稿;供 Tongmu 与 Leo 复核。

## 0. 设计约束

1. 原始 Reflection 永不覆盖:专家修改只产生该 issue 的 reviewed/edited 片段。
2. 状态可区分:原始内容、专家修改、最终结论三者不混同(04 §九)。
3. 全过程可追溯:每个关键节点记录时间、主体、动作、结果(04 §十)。
4. 不同专业意见不视为冲突;同一 issue + 同一专业的互斥结论才触发冲突。
5. 引擎侧字段(接口响应)只读引用,平台不写回引擎。

## 1. 实体与字段

### 1.1 expert(专家)
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string(PK) | 平台账号或内部标识 |
| display_name | string | 展示名(可用匿名/编号) |
| fields | string[] | 可匹配的专业类别(与 router issue.field 匹配) |
| active | bool | 是否进入"当前可用专家池" |
| created_at / updated_at | datetime | |

分配规则(04 §八):按 task.field 匹配 active 专家,动态/随机取;同一 task 同一轮不重复取同一人。

### 1.2 expert_task(Task,一个 Router issue 一条)
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string(PK) | 平台自增或 UUID |
| run_id | string | 关联引擎 Run(外键语义:runs/<run_id>) |
| issue_key | string | 引擎侧 issue 标识(issue-1..N) |
| field | string | 专业类别(建单时快照自 router.issues[].field) |
| risk | enum(low/medium/high) | 快照自 router.issues[].risk |
| summary | text | 快照自 router.issues[].summary |
| routing_reason | text | 快照自 router.issues[].routing_reason |
| reflection_text | text | 原始 Reflection 全文快照(平台保存,永不覆盖) |
| reflection_anchor | string | 可选:引擎侧定位片段(待 Leo 确认#4;未定前可空) |
| status | enum(见 2) | |
| round | int | 当前审核轮(1=首轮 2 人;2=冲突追加 3 人) |
| final_verdict | enum(approved/edited/rejected) | 定稿后写入 |
| final_verdict_basis | text | 多数结论摘要 |
| created_at / updated_at | datetime | |

### 1.3 review_assignment(专家分配记录)
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string(PK) | |
| task_id | FK→expert_task | |
| expert_id | FK→expert | |
| round | int | 1 或 2 |
| assigned_at | datetime | |
| state | enum(assigned/submitted/skipped) | 已提交后为 submitted |

### 1.4 review_opinion(专家意见,一条 = 一位专家一轮的意见)
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string(PK) | |
| task_id | FK | |
| expert_id | FK | |
| round | int | |
| verdict | enum(approve/edit/reject) | 04 §七 审核操作 |
| edited_segment | text(可空) | verdict=edit 时,专家修改的该 issue 片段 |
| comment | text | 意见说明(必填建议) |
| created_at | datetime | |

约束:同一 (task_id, expert_id, round) 唯一;Edit 的 edited_segment 独立保存,
与 reflection_text 并存,不覆盖原始。

### 1.5 training_pool_entry(训练材料归集,04 §九)
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string(PK) | |
| run_id | string | |
| reflection_id/task_ids | string[] | 关联的 Reflection 与全部 Task |
| entry_status | enum(ready/pooled/archived) | 是否已进入材料池 |
| payload_ref | json | 指向聚合产物(见 5) |
| pooled_at | datetime | |

### 1.6 governance_audit(追溯审计,04 §十)
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string(PK) | |
| run_id | string | 便于按 Run 回溯 |
| task_id | FK(可空) | |
| ts | datetime | |
| actor_type | enum(system/router/expert/admin) | |
| actor_id | string | 执行主体 |
| action | string | 如 task_created/assigned/submitted/disputed/finalized/pooled |
| detail | json | 动作结果快照(如多数/少数意见) |

## 2. Task 状态机

```
pending(建单)
  → assigned(首轮 2 人已分配,round=1)
      → 2 人意见一致(或均非互斥)→ finalized(按多数/一致)
      → 2 人同专业互斥结论 → disputed(round=2)
           → 追加 3 位新专家(round=2,共 5 意见)
           → 多数意见 → finalized(少数意见保留在 review_opinion)
finalized 后:可被标记进入训练材料池(pooled)
```

状态枚举: pending / assigned / disputed / finalized / pooled / archived。
互斥判定(04 §八.3):同一 task 同一专业,2 位首轮专家意见"互相排斥"——
approve 与 reject 互斥;approve 与 edit、edit 与 reject 是否视为冲突由
定稿口径决定(建议:approve×reject 必冲突;edit 与其他结论视为可协调,
仅记录差异,不自动触发追加,可在草案中单独标注)。

## 3. 引擎侧字段 → 平台字段映射(建单用)

| 引擎 5002 响应 | 平台落库 | 说明 |
| --- | --- | --- |
| run_id | expert_task.run_id | 每条 issue 一条 Task |
| router.issues[].id(issue-N) | expert_task.issue_key | 同 run 内稳定 |
| router.issues[].field | expert_task.field | 匹配专家池 |
| router.issues[].risk | expert_task.risk | |
| router.issues[].summary | expert_task.summary | 队列卡片主文本 |
| router.issues[].routing_reason | expert_task.routing_reason | |
| reflection.text | expert_task.reflection_text | 原文快照,永不覆盖 |
| reflection.text 片段定位 | reflection_anchor | 待 Leo 确认(见待定项 4) |
| audit[] | (追溯展示素材) | 平台不落,展示时调 5002 |

## 4. 冲突处理的数据表达

- 触发:某 task 首轮 2 条 review_opinion 互斥 → status=disputed,round=2。
- 追加:按 field 匹配再分配 3 位新专家(round=2),写入 review_assignment。
- 结论:round=2 共 5 条意见(含首轮 2 条),多数 verdict → final_verdict;
  少数意见的 review_opinion 行不删除,随 final_verdict_basis 一并可查。
- 不同专业 issue 之间:各自独立 task,不互相触发冲突。

## 5. 训练材料聚合产物(04 §九,归池时生成)

一份 JSON/文档,包含:run 的 experience 索引(指向引擎 run.json)、
原始 Reflection、Router issues、全部 review_opinion(approve/edit/reject)、
edited_segment、冲突过程与多数/少数结论、governance_audit 摘要;
各来源字段带来源标签(original/edited/final),不混同。

## 6. 待定项(需 ZZR 定稿或 Leo 回复)

1. ID 策略:expert_task.id 用平台自增,还是 run_id+issue_key 复合?
   (复合可省映射,但冲突追加/历史重审会改键,建议平台自增 + 复合唯一约束)
2. edit 的存储:edited_segment 放 review_opinion 内联列,还是独立 edit 表?
3. 专家实体:复用平台现有账号表,还是本结构内独立 expert?
4. reflection_anchor:引擎是否提供片段定位(Leo 待确认 #4);未定前留空,
   详情页按 summary 就近截取展示。
5. 状态字段是否保留 history 展开表(governance_audit 是否足以回溯)?
6. 归池触发:该 run 全部 task 达到 finalized 后由系统聚合(建议),或人工。

## 7. 第一里程碑最小集(建议先落)

expert_task + review_assignment + review_opinion + governance_audit,
覆盖 pending→assigned→(approve/edit/reject)→finalized 单线;冲突、归池
在二期补表。此最小集足以支撑 05 第 1-2 项 HCI(队列 + 单专家审核闭环)。
