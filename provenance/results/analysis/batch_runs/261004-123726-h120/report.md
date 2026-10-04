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

## 八、体检补记(批次收口后的只读延伸,15:3x)

以下由本批暴露的两个疑点展开,全部只读排查;已修项给了 commit 归属,未修项如实留着。

**1. `manifest` 缺失的根因确认:批量生产路径压根不经过 manifest 代码。**
生产链是 `case01/tools/batch_run.py:226` 起子进程 → `case01/run.py:96` → `case01/orchestrator.py`,落盘字段全集由 `RunRecorder`(orchestrator:106-116)初始化再补 condition_monitor/reflection/router/audit/consistency —— **orchestrator 全文对 "manifest" 零提及(含注释,大小写不敏感 grep 无匹配)**。挂 manifest 的是另一条链:`packages/mavis-case01-injector/src/mavis_case01_injector/pipeline.py:144-148`(`collect_run_meta` + `attach_manifest`),所以只有走 injector/映射的产物有这一段。13 个 REQUIRED_KEYS(manifest.py:42-56)对批量记录一个都不生效。
6 个**静默退化点**(缺字段不报错、也不告警):`live/history.py:260-285` 的 safe 视图白名单本就不含 manifest;`live/history.py:221-245` 的质检只看 consistency/reflection,从不看 `manifest_warnings`,且 import 失败整块 except 吞成 `unverified`;`case01/tools/reflection_audit.py:62-66` 的注释声称"截断落进 manifest_warnings",实际用文本启发式判截断、根本不读 manifest;`batch_run.py` 的统计对缺失不告警;`pipeline.py:248`(rerun_router_only)对无 manifest 的旧记录写回时不补挂;orchestrator:468 走 quick_scan 时只 print,不像 pipeline:190-193 那样记进 `manifest_warnings`。**结论:平台侧看不见"截断/判官降级"这类警告,不是概率问题,是链路没接。** 未修。

**2. 分支 A=0 要拆成两件事看,其中一件是确凿缺陷(已修)。**
- 规则判定路径(`case01/world/branch.py` shim → `mavis_case01_injector/world/branch.py:226-251`,由 `orchestrator.py:275` 在 no-llm/rules 模式调用)**结构上不可能判出 A**:`classify()` 只有 NO_BUY/REFUSE→B、CONDITIONAL/ANTI_ALLIN→C,兜底 `return "C"`,`route()` 里那段 `if b == "A"` 是死代码。实证:三条明确的"满仓买入/直接 all in"文本全部被判成 C。→ 已修(新增 ALL_IN 词表并把 A 判在 CONDITIONAL 之后,带任何对冲词仍不给 A;`case01/tests/test_world.py` 加 4 条守卫,含"A 可达"回归)。
- LLM 判定路径上 A=0 仍属**素材侧事实**:216 条留有 `raw_outputs` 的记录里,把所有 attempt 的原始输出抽出 `"branch":"X"` 得到 B 168 / C 38,**模型自己在任何一次尝试中都没吐过 A**;本批 39 条 stance 只有 wait 24 / conditional 15,`buy_now` 与 `unclear` 均 0,说明不是判官失败被兜底。历史 2 条 A 记录(`260919-live-case01-mavis-A-1720`、`260922-live-case01-mavis-A-0958`)都是 `--timeline A` 人工强制,`judge="preset"`、quick_scan 还判它们 inconsistent。
- 仍缺的证据:判官**输入侧**的召回率(这 39 条 T0 回答全文里到底有没有"立即/重仓买入"措辞)。`case01/tools/branch_judge_eval.py` 能做,但它要调 LLM 且往 `results/analysis/branch_judge_eval/` 写产物,本轮按只读约束没跑。

**3. 同类编码缺陷在另外 3 个调用点(已修)。**
本仓约定一律 `-X utf8`,而 `subprocess.run(..., text=True)` 此时按严格 UTF-8 解码:netstat/未钉 `PYTHONIOENCODING` 的自家子进程写的都是 GBK 字节,读取线程抛异常后 `communicate()` 交回 **stdout=None**(本机实测复现)。命中 `case01/tools/map_after_run.py` 的端口探活(旧写法会把 `re.search(pat, None)` 炸成 TypeError,看护进程直接崩)与映射取输出(整段诊断被 `(proc.stdout or "")` 静默吞光)、`case01/vizkit/live_run.py:132` 同一姿势。已统一走 `case01/safestream.py` 的 `run_text()` / `utf8_env()` / `tolerant_stdout()` 单一实现;`live_switch.py` 当年修的那份局部实现保持不动(它已在文档里记账)。

**4. 台账之外还丢过多少条,没有账。**
`case01/runs/` 共 254 个目录,其中 **12 个没有 run.json**(建了目录、记录没落盘)。只有 `batch-261003-165042-014` 在批次台账里留下 `ok=false / rc=1` 的对应行,其余 11 个(261003 三批的 lane 目录 + `v8b-1436` + `vrfy8b-1434`)**不在任何批次台账中**,今天已经说不出它们当时为什么没写成。`list_runs` 靠 `os.path.exists` 过滤跳过,所以这 12 个目录既不进统计也不报错 —— 行为正确,但"丢过 11 条"这件事目前无处可查。

**5. 平台索引口径:文件侧重算与批前基线对不上,未解释。**
只读函数口径(`list_runs`,不起服务):全库 242 条 → `/api/runs` 默认(隐藏 questionable/debug/deprecated)应返回 **213**,excluded **29**(questionable 28 + deprecated 1),`include_questionable=1` 全量 **242**。批前基线记的是 count=234 / excluded=38 / 全量 272 —— **差 30 条**。已知存放点最多凑出 25(归档 `runs_archive_20260919` 13 条 + 半成品目录 12 个),仍差 5 条;基线那两个数字来自 12:33 那轮的服务侧聚合,本轮按约束不起服务,因此**无法复现也无法证伪**,原样留此,不并入本批结论。

**6. 红线复核**:`interventions.json` 仍 34 条(可比 12 条),本轮修的是判定链路与编码链路,**没有**新增任何内化证据;可主张范围与第一节相同(状态级内化、反思层证据),行为级改变依旧不可主张。

*补记口径:新增测试合计 `case01/tests/test_stdout_encoding_guard.py` 16 条 + `test_world.py` 4 条;`case01/tests` 全量 513 passed,injector 包 4 passed,`tools/doc_audit.py --check` 待更新 0(只读)。*

*口径说明(第一至七节):统计源为 `results/analysis/batch_runs/261004-123726-h120/ledger.jsonl` 与 `case01/runs/<run_id>/run.json`,全部只读;批前基线 = 242 条全库 − 本批 39 条 = 203 条,与 12:33 实测的 97/25/80/1 完全吻合。分析用的临时统计脚本未落仓库,算完即删,`git status` 除本批次目录外干净。*

