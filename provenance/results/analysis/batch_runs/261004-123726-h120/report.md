# 批次 261004-123726-h120 分析报告(中文)

- 批次:`261004-123726-h120` · 台账目录 `results/analysis/batch_runs/261004-123726-h120/`
- 窗口:2026-10-04 12:37:26 启动 → 14:39:37 收口(墙钟 122 分 11 秒,最后一条在途记录 039 于 14:39:37 落盘)
- 设置:`qwen3:8b` · 思考关闭 · 分支 auto(LLM 判定) · 并发 3 · 目标 120 分钟
- 状态:**已收口**(`summary.md` 已生成,tasklist 无 batch_run 进程残留)。本报告为收口后终版,非阶段报告。

## 一、结论

1. **39 / 39 成功,0 失败,0 条 `read_ok=false`。** 单条耗时均值 558.8s、中位 525.1s、最长 950.1s、最短 375.0s,累计 21794.3s(÷3 lane ≈ 121 分钟,与 120 分钟窗口吻合);最长 950.1s 距 1200s 单条超时线还有 250s,**本批没有任何一条被超时打断**。
2. **台账口径 39/39 自洽**:`model_requested` 与 `model_actual` 全部等于 `qwen3:8b`,不符 **0 条**。"请求 8b 实际跑 4b"的老坑在本批未复现。
3. **立场判官在生产路径上首次生效,这是本批最硬的证据:39 条新记录 `consistency.method` 全为 `llm_stance`(39/39),`quick_scan` 为 0;`branch_source` 全为 `judge`(39/39)。** 对照:本批之前落盘的 203 条成品 `llm_stance` 为 **0 条**、`quick_scan` 为 **203 条**。修复(commits ccc8b70 / dbda3d2)不再是测试用例里的断言,而是有了生产落盘样本。
4. **质量分布:ok 36 / questionable 3 / unverified 0 / debug 0 / deprecated 0。** 批前 203 条里 unverified 占 80 条(39.4%),本批 0 条 —— 一致性判定从"旧记录没戳所以判不了"变成"每条都给出了立场判定"。
5. **但分支分布仍然塌缩:A 0 / B 23 / C 16。** A 为 0 不是新问题(批前 203 条只有 2 条 A),但本批 39 条一条 A 都没有,叠加 B 占 59%,往 B 塌缩的风险**依旧成立且未被本批改善**。

## 二、批次概况与耗时

| 项 | 本批 |
| --- | --- |
| 台账行数 / 成功 / 失败 | 39 / 39 / 0 |
| 墙钟窗口 | 12:37:26 → 14:39:37(122.2 分钟) |
| 吞吐 | 39 条 ÷ 120 分钟 ≈ 19.5 条/小时(并发 3) |
| 单条耗时 均值 / 中位 / 最短 / 最长 | 558.8s / 525.1s / 375.0s / 950.1s |
| 累计机时 | 21794.3s(≈6.05 机时) |
| 重试(attempt>1) | 1 条:`-035`(attempt 2,rc 0,见"异常与事故") |
| returncode≠0 | 0 条 |
| 触发 1200s 超时 | 0 条 |

三条 lane 均匀收尾,最后 12 分钟(14:26–14:39:37)是"到点只停取新任务、在途跑完才退出"的预期行为,实际结束比 14:37 晚 2 分 37 秒。

## 三、质量分布

口径:逐条用 `case01.full_context.quality_of(run.json)` 判定,只读。

| quality | 本批 | 批前 203 条 |
| --- | --- | --- |
| ok | **36** | 97 |
| questionable | **3** | 25 |
| unverified | **0** | 80 |
| debug | 0 | 0 |
| deprecated | 0 | 1 |
| 反思未生成(占位) | **0** | 3(全部被 questionable 隔离) |

本批 3 条 questionable 的成因是 **`consistency.verdict = inconsistent`(跑得差),不是"没跑成"**:三条反思正文分别是 1392 / 1422 / 1630 字,均为真文本,质量门也正常打分。**按约定这 3 条不摘**,整块保留供专家复核;`quality_of` 把它们标 questionable 只是给平台的筛选提示,不构成"证据作废"。

反思产出与 Router:

| 指标 | 本批 |
| --- | --- |
| 反思字数 均值 / 中位 / 最短 / 最长 | 1810.1 / 1817 / 1227 / 2438 |
| 反思 < 300 字 | **0 条**(逐条核对台账与 run.json 正文,一致) |
| 质量分均值 | 94.7(n=39) |
| 质量门 | pass 27 / review 12 |
| review 主因 | "未提出可执行的后续改进动作" 11;“缺少实质维度:行动与后果、结果与判断过程、进一步帮助” 1;“未明确区分最终结果与当时判断过程” 1 |
| Router issues 合计 / 均值 | 179 / 4.6(区间 3–5) |
| 零 issue 记录 | **0 条** |

## 四、一致性与立场判官生效证据

| 项 | 本批 39 条 | 批前 203 条 |
| --- | --- | --- |
| `consistency.method = llm_stance` | **39** | **0** |
| `consistency.method = quick_scan` | 0 | 203 |
| `branch_source = judge` | 39 | — |
| verdict consistent / inconsistent | 36 / 3 | — |
| `disagreement = true` | **3** | — |

本批是该修复的**第一批生产证据**:39 条全部走立场判官,`quick_scan` 没有一条漏进新记录,**不是回归**。

`disagreement=true` 的 3 条是两判据打架(立场判官 vs quick_scan),按研究信号处理,不是 bug:

- `-016`(B 线):立场判官判 `conditional` → 期望 C,记录是 B;quick_scan 判 consistent("B 线与 AI 的谨慎立场一致")。
- `-025`(C 线):立场判官判 `wait` → 期望 B,记录是 C;quick_scan 判 consistent。
- `-039`(C 线):立场判官判 `wait` → 期望 B,记录是 C;quick_scan 判 consistent。

三条形态一致:AI 的表态是"观望 + 有条件才动"的混合体,quick_scan 只看关键词命中就放过,立场判官要求"主立场"与分支线型对齐,于是更严。**这说明新判据确实在改变结论(3/39 ≈ 7.7% 的翻案率),而不是复述旧口径。**

`max_tokens` 截断:**本批无法核对,原因是字段不存在而非无截断。** 这 39 条 `case01/runs/<id>/run.json` 里没有 `manifest` 段(键集只有 `audit/branch/branch_action/condition_monitor/consistency/events/final_feedback/reflection/retrievals/router/run_id/start_date/state_history/turns`),因此 `manifest.manifest_warnings` 无从读取。不能把它写成"0 条截断"。Router 侧可间接观察:39 条全部解析出 3–5 条 issues、零 issue 记录 0 条,没有出现"截断后 JSON 不完整 → 解析 0 条"的典型征兆,但这只是旁证,不等于截断已验证。

## 五、与基线并排对比

批前全量基线为 2026-10-04 12:33 实测;答辩基线 25 条;同口径上一批 `261003-192735`(同设置:qwen3:8b、并发 3、120 分钟)。

| 指标 | 答辩基线(25 条) | 上一批 261003-192735(43 条) | **本批 h120(39 条)** | 批前全量(203 条) | 批后全量(242 条) |
| --- | --- | --- | --- | --- | --- |
| 分支 A | 2 | 0 | **0** | 2 | 2 |
| 分支 B | 16 | 33 | **23** | 157 | 180 |
| 分支 C | 7 | 10 | **16** | 29 | 45 |
| undetermined | — | 0 | **0** | 15 | 15 |
| 反思均值(字) | 2629 | 1732.0 | **1810.1** | — | — |
| Router issues 合计 | 109 | 192 | **179** | — | — |
| 单条均值 issues | ≈4.4 | 4.47 | **4.6** | — | — |
| 单条耗时均值 | — | 523.0s | **558.8s** | — | — |
| quality:ok | — | 29 | **36** | 97 | 133 |
| quality:questionable | — | 0 | **3** | 25 | 28 |
| quality:unverified | — | 14 | **0** | 80 | 80 |
| `llm_stance` 条数 | — | 0 | **39** | 0 | 39 |

索引口径(`list_runs`,只读函数调用,未起服务):批后全库 242 条,`quality=ok` 133 条,`exclude_questionable=True` 返回 133 条。批前基线给出的 `/api/runs` 聚合 count=234 / excluded=38 / include_questionable=1 时 272 是服务侧口径(含未落盘为 run.json 的条目),本批未起服务,不与该数字强行对齐,只报文件侧实测。

三点要并排看的事实:

- **A 分支依旧是 0**,且和上一批一样塌向 B;反思均值 1810 字明显低于答辩基线 2629 字(样本量换短文本,不是新退化)。
- **unverified 归零是本批唯一结构性变化**:上一批 43 条还有 14 条 unverified,本批 39 条 0 条 —— 与立场判官生效是同一件事的两面。
- **Router 密度稳定**:4.4 → 4.47 → 4.6 条/记录,跨三批基本不变。

## 六、异常与事故

1. **1 条子进程 GBK 编码崩溃后重试成功(`-035`,attempt 2)。** 该 lane 日志首次尝试以 `UnicodeEncodeError: 'gbk' codec can't encode character '\u25b6' in position 361` 终止,栈顶在 `case01/orchestrator.py:242 → log(answer + "\n")`。根因:batch_run 用 `encoding="utf-8"` 打开日志文件,但子进程(`python -m case01.run`)自己的 stdout 仍按 Windows locale(GBK)编码,模型输出含 `▶` 就抛异常、整条 run 死在 T0;主日志对应 `[14:26:07] 重试 1/1: batch-...-035`,重试时模型没再吐 `▶`,attempt 2 正常跑完并落盘(840.1s,ok)。commit dbda3d2 修的是 batch_run **读外部命令输出**的 GBK 解码面,**子进程写 stdout 的编码面仍未覆盖**:`case01/tools/batch_run.py` 组装子进程 env 时没有设 `PYTHONIOENCODING`,命令行也没带 `-X utf8`。同一日志的重试段还能看到大面积 mojibake(正文按 GBK 写进 utf-8 文件再被按 utf-8 读),属于同一处的表现。**本批影响面:1/39 条多花了约 14 分钟重跑,没有丢数据。**
2. **`manifest` 段在本批 run.json 缺失**(见第四节),`manifest_warnings` / max_tokens 截断口径**未验证**,不能报"无截断"。
3. **环境核对(只读)**:Ollama 在线(`http://127.0.0.1:11434/api/ps` 返回 200,`models` 为空 = 无常驻模型,批次结束后已卸载,正常);D 盘余量 119G(已用 79%),无磁盘压力;未启动 5010/5002/8060 任何服务,体检与统计全程只读。
4. **无失败率高、无 0 ok 事故**:39 条全成功,台账与 run.json 逐条对得上,反思字数在台账与 run.json 正文两处口径一致(39/39)。
5. **待解释的空白(不粉饰)**:分支 A 为 0 的成因本批数据无法定位 —— 39 条立场标签只出现 `conditional` / `wait` 两类形态,没有出现"坚决买入"型表态,这既可能是场景/素材本身的分布如此,也可能是 auto 判定的期望线型映射对 A 过严,现有字段不足以区分,留待专家复核。

## 七、下一步建议

1. **补子进程 stdout 编码**:在 `case01/tools/batch_run.py` 组装子进程 env 处加 `PYTHONIOENCODING=utf-8`(或子命令统一带 `-X utf8`),并对 `case01/orchestrator.py` 的 `log()` 走容错编码,让 `▶` 这类字符不再单条报废一次重试机会。这是本次唯一的代码级事故,优先级最高。
2. **把 `manifest` 段落进 `case01/run` 的 run.json**(与 injector pipeline 的落盘对齐),否则 `manifest_warnings` / 截断口径在生产样本上永远读不到,而这是"截断是否影响 Router"的唯一直接证据。
3. **专门验一次分支 A 的可达性**:构造或挑几条 AI 明确表态"立即买入"的素材跑 `--timeline auto`,确认判据映射不是结构性排除 A;在此之前,报告与答辩里**不要**把 A=0 解释成"当事人行为如此"。
4. **科研口径保持红线**:本批 39 条补的是**量与质量分布**,可作为"状态级内化 + 反思层证据"的样本池;`interventions.json` 仍只有 34 条(可比 12 条),**批量样本增多不等于内化证据变多**,2 小时窗内行动文本位移与底噪同量级,**行为级改变不可主张**。
5. **可直接入库的产物**:本批 questionable 3 条(016/025/039)连同 `[两法分歧]` 的 reason 原文整块保留给专家复核,不摘除;`analysis.json` / `analysis.md` / `summary.*` / 台账与逐条日志已在批次目录,本报告为 `report.md`。

---

*口径说明:统计源为 `results/analysis/batch_runs/261004-123726-h120/ledger.jsonl` 与 `case01/runs/<run_id>/run.json`,全部只读;批前基线 = 242 条全库 − 本批 39 条 = 203 条,与 12:33 实测的 97/25/80/1 完全吻合。分析用的临时统计脚本未落仓库,算完即删,`git status` 除本批次目录外干净。*
