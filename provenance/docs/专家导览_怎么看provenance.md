# 专家导览:怎么看 provenance

> **状态**:现行
> **最后核对**:2026-09-27
> **说明**:**给 GTC 专家的查看导览**:结论边界/面归属/只读口/字段读法/标注表用法/十五分钟走查(2026-09-27 起)
这份文档不讲实现细节,只回答一个外来读者最需要的三个问题:结论边界在哪里、
东西在哪个地址、材料怎么读。实现侧口径(平台契约、分层、信息边界)见
《平台对接契约_5010唯一入口》《架构总览与对接指南》;本文只做"领路"。

## 一、先记住三条口径红线

第一条,也是最重要的一条:**我们只主张"状态级内化 + 反思证据",不主张行为级改变。**
倾向权重在干预后的位移是实测的(见第八节),但"权重变了以后行为跟着变"目前**未观测**:
两小时窗内行动文本的位移与底噪同量级。所以看到任何"行为已经变了"的读法,都超出了证据。
这条边界来自实测而非保守措辞,见 `case01/docs/内化与敏感性_定量分析_20260924.md`。

第二条,**平台侧职责不在本仓实现**。评审流程(审批/驳回、冲突升级到五次多数决、
训练材料归集、Audit Trail)属治理平台侧;实现侧只留接口与数据形状,没有对应页面。
在界面上找不到这些功能不是缺陷,是分工。

第三条,**数据只读**。专家意见与标记是追加记录,不覆盖原始数据;原始 Reflection
不得被平台或专家改写。

## 二、把人送到正确页面上(启动与地址)

最省事的方式是只看成品、不跑推演(不占本地大模型):

```bash
cd provenance/provenance
python live_switch.py --start case01 --review-only
```

然后打开 `http://127.0.0.1:5010/embed/review`。5010 是唯一实时入口,case00 与 case01
互斥共用;专家要看的是 case01(受控实验)这一支。

这里有一个**面归属**的实测事实,先记住免得扑空:同一个 5010 端口,起哪个 case
决定哪些页面存在。`/embed/review`(结果记录九块)与 `/embed/demo-parent` 只有
**case01 面**才有;`/embed/goals`、`/embed/timeline`、`/embed/explain` 只有
**case00 面**才有。实测:起 case00 时访问 `/embed/review` 会直接 404。
两个面都有的是:`/`、`/embed/scene`、`/embed/explore`(数据界面)、`/api/runs`、
`/api/run-detail/{source}/{run_id}`、`/health`。

另外,结果面的深链是 `?run=<run_id>&tab=<页签>`;页签 id 为
`overview` / `turns` / `retrievals` / `events` / `states` / `reflection` /
`router` / `injector` / `audit`。`run` 不存在时页面上会红字写明并回落到默认记录,
不静默;`tab` 非法给黄条说明。5010 是否还在推演、是否已跑完,以 `/health` 的
`finished` / `finish_reason` 为准,不要靠画面猜。

## 三、页面上有什么

`/embed/review` 是**专家视图**,一条成品记录分九块:概览、对话(turns)、检索
(retrievals,该角色当时想起了什么)、事件(events,剧情事件流水)、状态
(states,资金/持仓/买卖价逐日快照)、反思(reflection)、问题分流(router,
从反思里拆出的带风险的行为与判断)、审计(audit),以及注入器(injector)页签
(仅本仓复核面可见,见下)。

给专家看的这一面已经做了信息边界处理:**不显示** `branch`、分支判定方式、
分支来源、T0 立场一致性,也没有"注入器"页签与原始 JSON 链接;索引里
`questionable` / `debug` / `deprecated` 的记录**默认不列**,但页面上会写明
"已隐藏 N 条",不静默。专家的 `View Full Context` 走自然语言全文(这不是 JSON,
也不含任何实验元信息,有守卫测试钉死)。

`/embed/explore` 是数据界面(左侧分组历史 run、右侧摘要与详情),纯数据浏览,
**不依赖当前是否在跑**,所以任何一面都能开它。它的取数就是下一节的两个只读口。

## 四、只要数据、不要界面:两个只读口

平台或脚本自己取数,只用这两个口:

`GET /api/runs` 是聚合索引:case00 痕迹、case01 成品、压缩成品归一化成同一组摘要字段。
查询参数有 `include_questionable`、`limit`、`offset`、`source`(取值
`review` / `checkpoint` / `compressed`);响应里 `count` 是本页条数、`total` 是过滤后总数。
**不认识的参数不静默吞掉**,会回在 `ignored_params` 里。

`GET /api/run-detail/{source}/{run_id}` 取单条详情,`source` 是白名单枚举,写别的直接 404。
它**默认返回专家安全视图**:实测该响应的 `view` 为 `expert-safe`,数据段只含
`run_id`、剧情起止日期、`turns`、`retrievals`、`events`、`state_history`、
`final_feedback`、`audit`、`condition_monitor`、`reflection`、`router`、`compat`、`summary`;
`injector`、`branch_action`、`consistency`、`quality`、`branch` 都不在其中。
内部排查要原文时显式加 `?raw=1`(`view` 变为 `raw`,此时才带 `injector`)——
请**不要**把 raw 视图喂给专家页。

还有一条更接近"读故事"的入口:5002 只读面的 `GET /api/runs/{id}/full-context`
返回自然语言全文(实测不含 `branch_action` / `injector` / `consistency` 这些实验元信息)。
它的说明见《case01 侧 5002 冻结契约》,对接入口已统一到 5010,但这条全文入口仍然可用。

内部面 `/api/review/*` 是本仓复核用的,不要拿来当对接面。

## 五、字段怎么读

`quality` 是质检口径,取值 `ok` / `questionable` / `debug` / `deprecated` / `unverified`。
两点容易误读:`deprecated` 表示记录被显式标废弃(例如"标 A 的原文实为 B 判据"),
**它优先于其它判断**——一条自洽的废弃样本同样是废的;`questionable` 表示预设分支与
投资 AI 在 T0 的立场自相矛盾,或者**判定失败**(judge 三次都判不出分支 → run 停在 T0、
没有时间线)。而 `unverified` 只是"判不了"(旧记录缺一致性戳、case00 痕迹、压缩成品),
**判不了不等于有问题**。

`branch_source` 取值 `preset`(运行参数指定)/ `judge`(由 T0 回答判定)/
`judge-failed`(判定失败、停在 T0)。受控实验目前默认走 `judge`,也就是"分支由受判主体
在 T0 的回答判定";`preset` 保留为对照条件——**这个默认口径仍待研究侧确认**,确认前
请按字段实际取值理解数据。

时间字段要分清:`start_date` / `end_date` 是**模拟剧情日期**(如 2026-08-27),
不是这次跑的真实时间;`run_id` 里的 `<YYMMDD>-<HHMM>` 是生成那台机器的**本地墙钟**;
只有 `manifest.created_at` 与标记的 `marked_at` 带时区。

主键与取值:Router issue 的 `id` 是 **run 内序号**(`issue-1`、`issue-2`……),跨 run 会重名,
所以建单或做幂等要用 **`(run_id, issue_id)`** 复合主键;`risk` 取值是小写
`low` / `medium` / `high`。

## 六、专家自己的动作:标记反思

人机协同闭环的专家动作是**标记一条反思**:`verdict` 取 `correct`(核心成立)、
`incorrect`(核心逻辑不成立)、`partial`(有遗漏或表述问题),后两者附一段纠正文本。
入口是 `POST /api/reflections/mark`(统一口为 `POST /api/intervention/mark`);
落盘在 `results/checkpoints/reflection_marks.json`(加锁 + 原子写),
并同步导出 `.jsonl`,每行含一条原始记录与一个 LoRA 训练样本(SFT 指令样本 + DPO 偏好对)。

需要留意当前的实现边界:现成的**标记面板页 `/embed/reflections` 与反思列表口
`GET /api/reflections` 目前只在 case00 面提供**;case01 面提供的是标记接口本身
(`/api/reflections/mark`)与只读的反思页签。也就是说,在 case01 面看反思是现成的,
若要当场打标,目前要么切到 case00 面,要么直接调接口。

这条闭环的数据目前是空的:管道、校验门、去重与统计都已就绪,**真实标记 0 条**——
闸门在专家这一侧。管道说明见 `case01/docs/LoRA预准备_20260924.md`。

## 七、分析素材怎么用

第一批定量证据都在 `results/analysis/` 与 `case01/docs/`,不需要跑模拟就能读:

- `results/analysis/<sim>/internalization.json` / `.md`:每条干预的响应延迟、保持性、
  过冲幅度(12 条干预、11 条朝新约束为正、7 条过冲后收敛);
- `results/analysis/<sim>/behavior_follow.json` / `.md`:权重位移与行为位移的相关,
  以及底噪对照——**第一个如实结果为"未观测到行为级效应"的那份材料**;
- `results/analysis/router_sensitivity/`:同一反思换模型的敏感性(条数稳定、风险校准不同);
- `results/analysis/router_review/router_review_sheet.csv`:Router issue 的人工标注表,
  每条填三列——`field_ok`(路由到的问题域是否合适)、`expected_risk`(专家认定的风险等级)、
  `summary_ok`(概述是否为行为陈述句且忠于原文);**表内第二行是填法说明**(以 `#` 开头,
  评分时会被自动跳过,不是数据行);填完用
  `python -m case01.tools.router_review --score <填好的.csv>` 得到
  **路由一致率 / 行为句率 / 风险校准率**三个指标。
  ⚠ **行数会随跑随增,以上不给具体数字**,要当前条数就重新导出后数:
  `python -m case01.tools.router_review --export`。表里除真 issue 行外还有
  **"Router 未产出"的占位行**(summary 写明"Router 未产出"),它们不在评分分母内,
  也**不要读成"没有问题"**。占位行有两类来源,看 `risk_note` 列区分:
  ① `router.status=error` / `=skipped`(Router 明确跑失败或被跳过);
  ② **第四态**(2026-10-06 补):`router.status=(缺键);reflection.quality.status=…`
  —— 旧记录连 `status` 键都没有,而**反思质量门没过**(`error`/`fail`/`review`)。
  实测全库只此一条 `p8b-1234`:`router.raw='[]'`、反思仅 67 字、`quality.status=fail`;
  ⚠ 所以"0 issue"单独看**分不清"干净"还是"没跑成"**:`status` 正常且 0 issue 的记录
  是**真干净、不占行**;反过来,**表里没出现某条 ≠ 那条没问题** —— 两件事都要看 `risk_note`;
- `results/analysis/reflection_review/reflection_review_sheet.csv`:反思的质量标注表,
  标 `dim1..dim8`(八维是否实质覆盖)、`relevance`(切题度)、`fidelity`(1 = 未编造事实)、
  `style_ok`(1 = 无客套/追问/emoji);同样用 `--export` / `--score` 两个子命令,
  **第二行是八维定义说明**(`#` 开头,评分自动跳过)。行数同样以现导出的表为准。

配套三份文档:`内化与敏感性_定量分析_20260924.md`(证据全貌与答辩口径)、
`转接_给下一棒_20260926.md`(当前基线与四个方向)、`LoRA预准备_20260924.md`(管道与运行手册)。
待修问题清单在 `case01/docs/问题清单_20260925.md`(六条的当前状态见文首)。

## 八、第一次走查的十五分钟

1. 起 5010 的 case01 只读面,打开 `/embed/review`;先看 `reflection` 与 `router` 两块——
   专家要判的就是这两块的内容;
2. 挑一条记录点 `View Full Context`,读一遍自然语言全文;
3. 切到 `/embed/explore` 看看历史 run 的整体分布,再用
   `/api/runs?include_questionable=1` 对照默认视图,理解"被隐藏的那几条是什么样";
4. 用 `/api/run-detail/review/<run_id>` 拿一次专家安全视图,确认它没有实验元信息
   (想看差异再 `?raw=1` 比一次);
5. 打开 `router_review_sheet.csv` 试着标十条,跑一次 `--score`,感受三个指标的定义;
6. 打开 `/review/expert`(专家审核面板的**参照实现**):左边一条"问题＋专业"一份意见,
   右边是 approve/edit/reject 三个按钮 + 自由文本框。提交后响应里的 `training_effect`
   会当场说出这份意见进训练集取的是哪份文本、被哪道校验门拦了 ——
   想理解"专家的文本最后变成什么"就看这一步。字段口径在
   `docs/给平台侧_专家意见字段与训练线映射.md`。界面正式由治理平台实现。

## 九、最容易误读的五件事

- 默认视图"少给了记录"不等于"没有这条记录"——以响应里的 `excluded` 为准;
- 剧情日期不是运行时间,`run_id` 里的墙钟才是;跨机比较只能用带时区的字段;
- 状态级位移不等于行为改变(见第一节红线);
- `unverified` 不是"有问题",只是"判不了";`deprecated` 才是一票否决;
- 页面上看不到平台侧流程(审批、冲突多数决、训练材料归集)是分工,不是缺失。