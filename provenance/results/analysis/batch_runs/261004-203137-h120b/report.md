# case01 批次 `261004-203137-h120b` 阶段报告：批次于 22:0x 手动停止，共完成 29 条（台账已被 22:29 的拉取清掉）

- 报告时间:2026-10-04(仅写盘,未提交、未推送)
- 数据源:`case01/runs/batch-261004-203137-h120b-001..029/run.json`(逐条实读,29/29 全在、编号连续)
- 辅助源:主日志 `C:\Users\rui\AppData\Local\Temp\qoder_probe\batch_h120b.log`(57 行,止于 21:52:48)、29 份 lane 日志
- **台账 `ledger.jsonl` 不存在**(22:29 一次带清理的拉取扫掉,非跑批故障),所有统计改从 run.json 取,字段来源变化在第三节逐项标注
- 解释器:`D:\zzr\mavis\.venv\Scripts\python.exe -X utf8`,全部命令在 `D:\zzr\provenance\provenance` 下执行;统计阶段一律只读

---

## 一、结论(先说数字)

1. **29/29 条成品完整落盘,0 条 FAIL。** 主日志 26 条 `OK` 行(001–026)+ 尾部 3 条(027–029)在父进程被杀后自己跑完并落盘。"跑得成"的判据本批改用三条硬指标:run.json 可解析、`reflection.text` 非占位、`reflection.quality.status` 不为 `error` —— 29 条全过。
2. **单条耗时均值 558.8s、中位 552.6s、最短 400.1s、最长 750.1s(n=26)。** 027–029 的"用时"字段**缺失**(台账没了、父进程没写出 OK 行),只能用主日志"开始"时刻减 `manifest.created_at` 推上界:631s / 630s / 541s,都落在本批 400–750s 区间内。
3. **`manifest` 段 29/29 进了 run.json(批前 243 条只有 6 条),`manifest_warnings` 非空 0 条。** 写侧接上了,这是本批最硬的一条生产证据。
4. **但截断口径拿到了负面证据:lane 日志里有 12 次 `max_tokens` 截断、涉及 8 条记录(003/004/006/009/013/016/019/022),这 8 条的 `manifest_warnings` 全是空的。** 其中两条被截断直接判废:-009 分支判官 3 次全截断 → `branch=undetermined`;-013 立场判官 3 次全截断 → `verdict=unknown`。截断在生产路径上仍然是"只 print、不落盘"。
5. **质量:ok 19 / questionable 9 / unverified 1(0 条"没跑成");一致性 llm_stance 29/29、quick_scan 0 条;两判据分歧 `disagreement=true` 9 条;分支 A 0 / B 23 / C 5 / undetermined 1。** 分歧率 31% 是 h120 批(3/39=7.7%)的 4 倍,这是研究信号,不是 bug。

**科研口径**:本批支持"状态级内化 + 反思层证据"的既有主张;**不支持行为级改变的主张**(2h 窗内行动文本位移与底噪同量级)。分支 A 仍为 0,现有字段分不出"素材本无坚决买入表态"与"auto 判据对 A 过严",**不许解释成当事人行为如此**。批量样本增多 ≠ 内化证据变多(`interventions.json` 仍只有 34 条、可比 12 条)。

---

## 二、批次概况、耗时与停止经过

### 2.1 启动参数(主日志第 1 行原文)

```
[20:31:37] 批次 261004-203137-h120b 开始 · 目标 120.0 分钟 · 并发 3 · 模型 qwen3:8b · 分支 auto · 备任务 120 条
```

命令:`python -m case01.tools.batch_run --duration-min 120 --lanes 3 --model qwen3:8b --tag h120b`。启动时 GPU 97% 占用、qwen3:8b 驻留 5.58GB。实际运行 20:31:37 → 最后一条成品落盘 22:02:38,约 91 分钟,完成 29 条(计划 120 条任务池未取完,不是队列空)。

### 2.2 手动停止的经过(不是自然收口)

- 2026-10-04 约 22:00–22:05,人为终止批次:**只杀了 `batch_run` 父进程,没有用 `/T` 连带杀子进程**。
- 证据:主日志最后一行是 `[21:52:48] lane3: 开始 batch-...-029`,此后父进程再没写出任何一行(既无 OK 也无汇总);而 -027/-028/-029 三个目录里 `run.json`/`branch.json`/`turns.jsonl`/`retrievals.jsonl` 四件齐全,lane 日志 mtime 分别是 22:02、22:02、22:01 —— 三条在途子进程跑完并落盘了。
- 后果:这 3 条**没有 `seconds`、没有 `attempt`、没有 OK 行**,耗时与分支只能从 run.json/branch.json 读(见 2.4)。
- 停止动机:把运行窗口让给带固定种子的新一批 `261004-220441-h120seed`(22:04:41 起跑)。那是另一个定时任务的范围,**本报告一条都不计入**。
- 时间重叠的事实核正:尾部 3 条落盘于 22:01:49–22:02:38,**早于** 新一批 22:04:41 起跑,三者推断耗时 541–631s 也在本批正常区间内 —— 所以"新批抢显存导致尾部异常耗时"这个担心在本批**没有观测到**,不写成结论。

### 2.3 台账被 22:29 的拉取清掉:影响面与重算路径

- 现状:`results/analysis/batch_runs/261004-203137-h120b/` 里只剩 29 份 lane `.log`(被**仓根** `.gitignore:62` 的 `*.log` 规则挡住,`git check-ignore -v` 实测确认),`ledger.jsonl` 不存在;`summary.md`/`summary.json` 本来就不会有(父进程先被杀,`_summarize` 没跑到)。
- 原因(只读核实):`git reflog` HEAD@{0} = `dd0a91e HEAD@{0}: pull --no-commit origin main: Fast-forward`。那次清理只扫"未跟踪且未被忽略"的文件;`provenance/case01/runs/` 被 `case01/.gitignore` 的 `runs/` 规则忽略,**所以 29 条成品全数幸存**。`git stash list` 为空,台账取不回来。
- 影响面(照实测说,不外推):现在同目录族里 `261004-123726-h120/ledger.jsonl`(39 行)与 `261004-220441-h120seed/ledger.jsonl`(15 行)**都还在**,本批是唯一确认丢失台账的那个。此前"两批台账同批被清"的说法在本轮实测中**没有复现**(h120seed 的台账可能是清理之后才由在跑的批次新建的,无法用现有证据区分)。
- **数据本体 29 条完整**,编号 001–029 连续,2026-10-04 22:40 复核一致。台账缺失是**记录载体的事故**,不是跑批失败。
- 重算路径:本轮用临时只读脚本(放在仓外 Temp 目录,算完即删)逐条读 `case01/runs/<id>/run.json`,经 `case01.full_context.quality_of` 判质量。
- **字段来源换了(必须点名)**:
  - `model_requested` / `model_actual` / `model_mismatch` / `seconds` / `attempt` / `returncode` / `log_tail` **只存在于台账**(`batch_run.py:238-271`),run.json 里没有对应键。本批实测:29 条 run.json 里含 "model" 的键只有 `manifest.judge_model`。
  - 因此本报告用 **`manifest.judge_model`(29/29 = `qwen3:8b`)** 替代模型核对项,用 **主日志"开始/OK"时间戳差** 替代 `seconds`,用 **lane 日志的 `===== retry =====` 分隔符 + 主日志"重试"行** 替代 `attempt`。
  - 代价:`model_actual`(运行中从 Ollama `/api/ps` 实测采到的驻留模型,`batch_run.py:261`)这类"实测"证据**永久丢失**了 —— lane 日志不打印它,现在再查 `/api/ps` 只能反映当前状态(实测此刻仅 `qwen3:8b` 5.58GB 在驻留),不能回填本批。

### 2.4 逐条实测表(只读脚本输出,`用时` 列 `-` = 字段缺失)

| run | branch | 一致性 verdict | method | 分歧 | quality | 反思门 | 反思字数 | issues | 用时(s) | git | manifest 键数 | seed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -001 | B | consistent | llm_stance | – | ok | pass | 2079 | 5 | 465.5 | a8c3b7d | 19 | 无此键 |
| -002 | B | consistent | llm_stance | – | ok | pass | 1358 | 5 | 695.5 | a8c3b7d | 19 | 无此键 |
| -003 | B | consistent | llm_stance | – | ok | pass | 1512 | 5 | 490.5 | a8c3b7d | 19 | 无此键 |
| -004 | C | inconsistent | llm_stance | DIS | questionable | review | 1586 | 4 | 580.1 | a8c3b7d | 19 | 无此键 |
| -005 | B | consistent | llm_stance | – | ok | review | 2059 | 5 | 560.1 | a8c3b7d | 19 | 无此键 |
| -006 | C | consistent | llm_stance | – | ok | pass | 1671 | 5 | 750.1 | a8c3b7d | 19 | 无此键 |
| -007 | B | consistent | llm_stance | – | ok | pass | 1409 | 5 | 410.0 | a8c3b7d | 19 | 无此键 |
| -008 | B | inconsistent | llm_stance | DIS | questionable | pass | 1464 | 5 | 400.1 | a8c3b7d | 19 | 无此键 |
| -009 | undetermined | inconsistent | llm_stance | – | questionable | review | 1336 | 5 | 530.1 | a8c3b7d | 19 | 无此键 |
| -010 | B | consistent | llm_stance | – | ok | pass | 1679 | 3 | 500.1 | a8c3b7d | 19 | 无此键 |
| -011 | B | inconsistent | llm_stance | DIS | questionable | pass | 2213 | 4 | 530.1 | a8c3b7d | 19 | 无此键 |
| -012 | B | consistent | llm_stance | – | ok | pass | 1727 | 4 | 540.1 | a8c3b7d | 19 | 无此键 |
| -013 | B | unknown | llm_stance | DIS | unverified | pass | 2170 | 4 | 570.1 | a8c3b7d | 19 | 无此键 |
| -014 | B | consistent | llm_stance | – | ok | pass | 2661 | 5 | 515.1 | a8c3b7d | 19 | 无此键 |
| -015 | C | consistent | llm_stance | – | ok | review | 2370 | 5 | 615.1 | a8c3b7d | 19 | 无此键 |
| -016 | B | inconsistent | llm_stance | DIS | questionable | pass | 1510 | 4 | 610.1 | a8c3b7d | 19 | 无此键 |
| -017 | B | inconsistent | llm_stance | DIS | questionable | pass | 1568 | 5 | 555.1 | a8c3b7d | 19 | 无此键 |
| -018 | B | consistent | llm_stance | – | ok | pass | 1937 | 4 | 460.1 | a8c3b7d | 19 | 无此键 |
| -019 | C | inconsistent | llm_stance | DIS | questionable | pass | 2036 | 4 | 460.1 | a8c3b7d | 19 | 无此键 |
| -020 | C | consistent | llm_stance | – | ok | pass | 2070 | 4 | 680.1 | a8c3b7d | 19 | 无此键 |
| -021 | B | consistent | llm_stance | – | ok | pass | 1631 | 4 | 695.1 | a8c3b7d | 19 | 无此键 |
| -022 | B | consistent | llm_stance | – | ok | pass | 2202 | 5 | 725.1 | a8c3b7d | 19 | 无此键 |
| -023 | B | consistent | llm_stance | – | ok | review | 1979 | 4 | 485.1 | a8c3b7d | 19 | 无此键 |
| -024 | B | consistent | llm_stance | – | ok | pass | 2060 | 5 | 575.1 | a8c3b7d | **20** | null |
| -025 | B | consistent | llm_stance | – | ok | pass | 1839 | 4 | 550.1 | a8c3b7d | **20** | null |
| -026 | B | consistent | llm_stance | – | ok | pass | 1811 | 5 | 580.1 | **d49be63** | 20 | null |
| -027 | B | inconsistent | llm_stance | DIS | questionable | pass | 1400 | 4 | **-** | d49be63 | 20 | null |
| -028 | B | consistent | llm_stance | – | ok | pass | 2222 | 4 | **-** | d49be63 | 20 | null |
| -029 | B | inconsistent | llm_stance | DIS | questionable | pass | 1497 | 4 | **-** | d49be63 | 20 | null |

027–029 的推断上界(主日志"开始"→ `manifest.created_at`):-027 = 21:52:03→22:02:34 = 631s;-028 = 21:52:08→22:02:38 = 630s;-029 = 21:52:48→22:01:49 = 541s。这三个数**不是 `seconds` 字段**,不进均值/中位统计。

---

## 三、四项修复在生产样本上的证据(15:20 之后新落的)

### 3.1 `manifest` 段进本批 run.json —— 生效(写侧)

- **29/29 条带 `manifest` 块**,对照批前 243 条只有 6 条(旧记录根本没这个块);生产路径(`case01.run`/orchestrator)是 commit `f942a0c` 才挂上的,本批是它落地后第一个 2h 级样本。
- **`manifest_warnings` 非空:0/29 条。** 逐条读原文,29 条全是 `[]`,`manifest_view` 状态全 `clean`(无 `absent`/`noted`/`error`)。
- 清单字段实测恒定量(29/29):`manifest_version=injector-manifest-0.1`、`engine_id=case01-run`、`judge=local`、`judge_backend_reason=class OllamaClient`、`git_commit_source=git rev-parse HEAD`、`scenario_sha256=a98e17486e265d4b…`、`financial_data_version=b45bf31cacda…`。
- **口径收紧:只有写侧接上才算证据,不能说"整条链路已接上"。** 读侧目前只到"质检透出标签"(commit `d45fcc6`,`full_context.manifest_view` + `live/history.py:245`),`manifest_warnings` 非空**不改 quality、不进默认排除集**;而"截断该不该进 manifest"这一步在生产路径还没接(见 3.2)。

### 3.2 `max_tokens` 截断口径 —— 读得到 manifest,但**这条口径仍然核不了,而且拿到了反例**

- manifest 段本批可读(29/29),可 **manifest 里压根没有截断这一栏**:键集只有 19/20 键,不含 `truncated`/`max_tokens`。全仓 grep 确认:截断写进 `manifest_warnings` 的唯一代码在**注入器 pipeline**(`packages/mavis-case01-injector/.../pipeline.py:54-60`,句子"LLM 输出被 max_tokens 截断 N 次(反思/路由可能不完整)");`case01.run` → `orchestrator` 这条生产落盘路径**不汇总 `client.truncations`**,它只在 `orchestrator.py:500` 写 quick_scan 那一句。
- 于是本批出现可直接点名的静默:**lane 日志实读 12 次截断、8 条记录命中,而这 8 条的 `manifest_warnings` 全为 `[]`。**

| run | lane 日志行号 | 截断次数 | max_tokens | 归属判定(按日志段落位置) | 落盘后果 |
|---|---|---|---|---|---|
| -003 | 134 | 1 | 512 | 立场判官(Router 之后、落盘之前) | 后续尝试成功,`verdict=consistent` |
| -004 | 47 | 1 | 512 | 分支判官(`=== Branch` 之前) | `branch_attempts=2`,第二次成功 → C |
| -006 | 31 | 1 | 800 | C 线 plan 解析 | `c_plan attempts=2`,第二次成功 |
| -009 | 30,31,32 | 3 | 512 | 分支判官,三次全截断 | `branch=undetermined`、`source=judge-failed`,原文 "Judge returned no valid A/B/C after 3 attempts" |
| -013 | 117,118,119 | 3 | 512 | 立场判官,三次全截断 | `verdict=unknown`、`stance=unclear`、reason 带 3 × `ValueError: Stance output is not valid JSON` |
| -016 | 132 | 1 | 512 | 立场判官 | 成功,`verdict=inconsistent` |
| -019 | 43 | 1 | 800 | C 线 plan 解析 | `c_plan attempts=2`,第二次成功 |
| -022 | 64 | 1 | 512 | 分支判官 | `branch_attempts=2`,第二次成功 → B |

- **不得写成"0 条截断"**:事实是"截断发生了 12 次、manifest 没记"。本批两条最有代价的降级(-009 判废、-013 判不了)在 run.json 里只能从 `consistency.reason`/`branch_action.reason` 的错误文本反推,清单层面零留痕。
- 旁证(不是直接证据):文件侧启发式 `python -m case01.tools.reflection_audit --runs-dir case01/runs`(只读,未加 `--clean/--write`)对全库 293 条报"疑似截断 0";本批 29 条反思正文末句都有句末标点,即**反思正文本身没被切断** —— 被切断的是判定类调用(分支/立场/plan),反思正文与判定是两回事。
- 结论:上一批(h120,39 条)"截断未验证"的口径**本批仍然成立**,但性质变了 —— 从"读不到 manifest 所以无从查"变成"**manifest 读得到、里面确实没有这一栏、而日志证明截断真发生了**"。

### 3.3 `▶`(U+25B6)不再打死一条跑 —— 无回归,但**本批没命中该字符路径**

- `UnicodeEncodeError` / `gbk` / `▶` 在主日志与 29 份 lane 日志中**出现 0 次**;29 条 run.json + 29 份 lane 日志全文扫描,`U+25B6 ▶`、`U+25B8 ▸`、`U+2705`、`U+274C` 均 0 次。
- 整轮重试(`attempt>1`)**0 条**:主日志无 `重试 k/n` 行,lane 日志无 `===== retry =====` 分隔符(该分隔符由 `batch_run.py:164` 在重试时追加写同一份 lane 日志,父进程被杀后仍可事后核对 —— 29 份 lane 日志都是单段连续输出,027–029 也一样)。
- 修复确实在生产路径上:`case01/tools/batch_run.py:54` import `utf8_env`,`:209` 起子进程时钉 `PYTHONIOENCODING=utf-8`(commit `1e5c79a`)。
- **诚实口径:上一批 -035 那个具体触发字符在本批 29 条输出里一次都没出现过,所以只能说"无编码错误回归",不能说"`▶` 崩溃路径已被生产样本验证修好"。**
- 注意区分层级:判定层**内部**重试(`branch_action.attempts`、`c_plan.attempts`,同一轮进程内的格式重试)本批有 -004=2、-022=2、-009=3、-006=2、-019=2 —— 这**不是**整轮重跑,与 `attempt>1` 两回事。

### 3.4 quick_scan 降级留痕自洽 —— **无样本可核**,等价证据是"本批没发生降级"

- `consistency.method` 分布:**llm_stance 29 / quick_scan 0**。commit `dae4934` 要求的"quick_scan 记录的 `manifest_warnings` 必须同句写明立场判官未启用",在本批**没有一条可核对的记录**。
- 等价旁证:29/29 `judge_backend_reason = class OllamaClient`、`judge = local`,即判官后端全程在场,降级条件从未触发。
- 因此不得把本批写成"降级留痕已验证"。已验证的是:**降级没发生**;留痕自洽性需要一个 judge 缺失的批次才能验。**没有一条静默降级记录**是本批的事实,而不是"检查通过"。

---

## 四、批内跨 commit 与两种清单形状

批次 20:31 起跑,期间落了新 commit `d49be63`(给 manifest 加一栏新配置 + 给 LLM 客户端加透传口子,默认不启用)。**逐条实读 `git_commit` 与 manifest 键集**(不按时间点猜,三条轴不同步):

| 组 | manifest 键数 | `git_commit` | `seed` 栏 | 条数 | run 编号 |
|---|---|---|---|---|---|
| ① | 19 | a8c3b7d | 该栏不存在 | **23** | -001 … -023 |
| ② | 20 | a8c3b7d | `null` | **2** | -024, -025 |
| ③ | 20 | d49be63 | `null` | **4** | -026 … -029 |

- 键集差异**只有一栏**:新增的 `seed`(第 20 键)。组② 的 `git_commit` 仍是 a8c3b7d 但键集已变 —— 代码 21:35 就被新起的子进程用上了,commit 21:47 才落盘,这正是"三条轴不同步"的实测形状,不能按时间点划界。
- **新栏逐条统计:29 条里 0 条非 null**(23 条无该键 + 6 条 null)。本批既没带命令行参数也没设环境变量,与该提交"未显式给值时请求体逐字节同改动前"的说明相符;**没有异常要点名。**
- **配置一致性核对(①②③ 三组逐一比对,全部相同)**:`judge_model` = qwen3:8b(29/29)、`temperature` = {judge 0.1, reflection 0.4, router 0.2}(29/29)、`scenario_sha256` = a98e17486e26…(29/29)、`prompt_versions` 五元组 {reflection aff9f218adaa, router 22bbd108c157, branch_judge 2caf63f9b246, c_plan b4d33ff4388d, scenario_branch_judge 073d1ae96c29}(29/29)、`branch_mode`=judge、`financial_data_version`、`judge_prompt_version` 同样 29/29 一致。
- 结论:**跨 commit 只改变代码与清单形状,不是两种配置**;没有需要报红的 scenario/提示词版本差异。
- **不得越口径**:这个透传口子不等于"逐字重生成已具备"。清单注释(`packages/.../manifest.py:27-29`)自己写明:`seed=null` = 没固定,有值也只买到有限的可复现性;对外口径见 `docs/复现说明_三档口径.md` §一/§五,**第三档(逐字重生成)仍然不能写进评审材料**。

---

## 五、质量分布(逐条用 `case01.full_context.quality_of` 判,29/29 实跑该函数)

| quality | 条数 | run 编号 |
|---|---|---|
| ok | **19** | 001,002,003,005,006,007,010,012,014,015,018,020,021,022,023,024,025,026,028 |
| questionable | **9** | 004,008,**009**,011,016,017,019,027,029 |
| unverified | **1** | **013** |
| deprecated / debug | 0 | — |

- **"没跑成"的隔离结果:0 条。** 29 条 `reflection.quality.status` 只有 `pass`(24)/`review`(5),**没有 `error`**,文本里没有 `(反思生成失败)`/`(失败)` 占位 → `_reflection_failed` 全返回空串。
- **"跑得差"不摘**:本批 `quality.status` 也没有 `fail`(最低分 83,-023),所以既没有"隔离出的没跑成",也没有"摘掉的差反思"。9 条 questionable 全部是**一致性判据不一致**(-009 另有 `source=judge-failed`),整块留给专家复核。
- 反思门比例:pass 24 / review 5(=17%);分数分布 100×11、95×12、90×4、85×1(-004)、83×1(-023)。
- 反思字数(n=29):均值 **1829.5**、中位 **1811.0**、最短 **1336**(-009)、最长 **2661**(-014)。**<300 字的记录:0 条**(故无需逐条列)。
- Router issues 合计 **131**(29 条,均值 4.52;5 条的 17 条、4 条的 10 条、3 条的 2 条),**零 issue 记录 0 条**。主日志 OK 行只覆盖 001–026,那 26 条合计 119 —— 两个数不是口径冲突,是覆盖范围差 3 条,以 run.json 的 131 为准。

---

## 六、一致性与分歧

- `consistency.method`:**llm_stance 29 / quick_scan 0**(批前全量是 quick_scan 203 / llm_stance 40,本批是纯 llm_stance 样本)。
- `verdict` 三态:**consistent 19 / inconsistent 9 / unknown 1**。
- 立场分布:`wait` 19、`conditional` 9、`unclear` 1(-013)。quick_scan 侧:`consistent` 24、`unknown` 5。
- `branch_source`(取 `consistency.branch_source`,与 `branch_action.source` 29 条完全一致):**judge 28 / judge-failed 1**(-009)。批前全量是 preset 9 / judge 234;本批 0 条 preset。
- **`disagreement = true`:9 条 —— -004、-008、-011、-013、-016、-017、-019、-027、-029。** 形状高度一致:9 条里 quick_scan 全部判 `consistent`,而立场判官给出不同结论:
  - 6 条 `branch=B / stance=conditional`(008,011,016,017,027,029)—— 判官认为"有条件参与"该走 C,分支却记 B;
  - 2 条 `branch=C / stance=wait`(004,019)—— 判官认为"该等"该走 B,分支却记 C;
  - 1 条 `-013` `stance=unclear`(立场判官 3 次 JSON 解析失败)vs quick_scan=consistent。
- 这是**研究信号,不是 bug**:两判据打架率在 2h 窗内升到 31%(9/29),而分歧方向偏向"判官读出的立场比关键词快筛更条件化"。样本只有 29 条、同一 scenario、同一提示词,尚不足以判断是判官对 B/C 边界过严还是 T0 表述本身条件化,不下结论。
- 分支分布:**A 0 / B 23 / C 5 / undetermined 1**(A 为 0 的解释边界见第一节口径与第八节)。

---

## 七、与三条基线并排对比

| 指标 | 批前全量(2026-10-04 20:29 实测,243 条) | 上一批 `261004-123726-h120`(39 条) | **本批 `261004-203137-h120b`(29 条)** | 答辩基线(25 条) |
|---|---|---|---|---|
| 完成/成功 | —(存量) | 39/39 成功 | **29/29 落盘,0 FAIL**(26 条有 OK 行 + 3 条父进程死后自跑完) | — |
| 单条耗时均值 | — | 558.8s | **558.8s(n=26;027–029 用时字段缺失)** | — |
| 耗时中位/最短/最长 | — | — | **552.6 / 400.1 / 750.1s** | — |
| quality ok | 134 | 36 | **19** | — |
| quality questionable | 28 | 3 | **9** | — |
| quality unverified | 80 | 0 | **1** | — |
| quality deprecated | 1 | 0 | **0** | — |
| consistency.method | quick_scan 203 / llm_stance 40 | llm_stance 39/39 | **llm_stance 29/29 / quick_scan 0** | — |
| branch_source | preset 9 / judge 234 | judge | **judge 28 / judge-failed 1** | — |
| disagreement | — | 3(7.7%) | **9(31.0%)** | — |
| 分支 A/B/C | 2 / 181 / 45(+undetermined 15) | 0 / 23 / 16 | **0 / 23 / 5(+undetermined 1)** | 2 / 16 / 7 |
| 反思字数均值 | — | 1810.1 字 | **1829.5 字** | 2629 字 |
| Router issues 合计/均值 | — | 179 / 4.59 | **131 / 4.52** | 109 / 4.36 |
| 带 manifest 段 | 6/243 | 0/39 | **29/29** | 0/25 |
| manifest_warnings 非空 | 1 | 0(无法读) | **0(可读,已确认)** | — |
| max_tokens 截断口径 | 不可核 | **未验证**(run.json 无 manifest) | **仍不可核,且有反例:日志 12 次截断 / 8 条记录,manifest 全 clean** | — |

三个要提醒的比较点:

1. **本批均值 558.8s 与上一批完全相同是巧合**(样本 n=26 vs n=39,本批最短 400.1s / 最长 750.1s,离散并不小)。不要因为数字相等就说"耗时已稳定"。
2. **questionable 比例明显高于上一批(9/29=31% vs 3/39=7.7%)**,几乎全部来自"两判据分歧 + 判官判 inconsistent"这一条路径,其中 -027/-029 是父进程被杀后跑完的两条。这不是失败率上升(0 条没跑成),而是**一致性判据在 auto/judge 模式下更严**,留给专家复核的块变大了。
3. **反思字数与 Router 产出与上一批同量级,但比答辩基线(25 条 2629 字)短约 30%。** 现有字段给不出"变短是因为提示词还是模型/思考开关"的区分,不解释。

---

## 八、异常与事故

按"事故先于数据"的顺序列,每条都给可核对的位置。

1. **台账整份丢失(事故,非跑批故障)。** `results/analysis/batch_runs/261004-203137-h120b/ledger.jsonl` 被 22:29 的带清理拉取扫掉(reflog `pull --no-commit origin main: Fast-forward`),`git stash list` 空,不可恢复。丢失内容:`seconds`/`attempt`/`returncode`/`model_actual`/`model_mismatch`/`log_tail` 六类字段,影响 29 条中的全部记录(其中 26 条的主日志 OK 行可部分替代,3 条不能替代)。**数据本体 29 条完整,统计以 run.json 为唯一权威。**
2. **`case01.tools.batch_analyze --batch` 在本批不可用,且是静默不可用。** 代码事实:`_collect_from_ledger`(`case01/tools/batch_analyze.py:175-176`)在 `ledger.jsonl` 不存在时 `return [], 0`,**不报错、不告警**,随后 `:452-454` 会往批次目录写 `analysis.json`/`analysis.md`。本轮**没有执行它**,以免生成一份"0 条记录"的伪分析、也以免在批次目录多写文件(交付只要求 `report.md`)。这是"台账缺失"的下游静默点,建议按第十节修。
3. **`max_tokens` 截断在生产路径静默(第 3.2 节)。** 12 次截断、8 条记录、`manifest_warnings` 全 `[]`;代价最重的是 -009(分支判官 3 次全截断 → `undetermined`)与 -013(立场判官 3 次全截断 → `unknown`)。截断计数只在 lane 日志里,而 lane 日志被 `.gitignore` 的 `*.log` 忽略 —— **本批唯一直接证据本身随时可以再被一次清理弄丢。**
4. **父进程被单杀(未带 `/T`)导致台账不完整。** 027–029 三条"跑成了但没有台账行",`summary.md`/`summary.json` 也没生成。这种"数据在、账不在"的形状,以后每次单杀父进程都会重现。
5. **判定层重试共 7 次事件,分 5 条记录:** 分支判官 `branch_action.attempts` —— -004=2、-022=2、-009=3;C 线 plan `attempts` —— -006=2、-019=2。前四条最终都判出了有效结果,只有 -009 停在 `judge-failed`。**整轮重跑 0 次**(与上一批 -035 那次 `UnicodeEncodeError` 白跑形成对照)。
6. **服务侧口径与文件侧口径是两件事(现成例子)。** 22:35 实测 5010/5002/8060 **都没在监听**(`netstat` 95 条 LISTENING 里只有 Ollama 11434),本轮报告期间也未尝试拉起或重启;而同一时刻 `case01/runs/…` 的 29 条文件产物完好。**"端口没人听"不影响文件侧统计的可信度,反之亦然** —— 平台侧读不到记录时,先看端口再看文件。
7. **不属于本批的运行已排除。** 报告期间另一个会话的 `case01.run --seed 1234 --run-id seedrun-1` 与 `261004-220441-h120seed` 批次在本仓之外/在途,**一条都没计入本统计,也没读它们的半写文件**;未杀任何进程(实测此刻已无 `case01`/`batch_run` 相关 python 进程存活)。当前 Ollama `/api/ps` 仅 `qwen3:8b` 5578204118 字节在驻留,这与本批启动时观测的 5.58GB 一致,但**它是当前状态,不能当本批的 `model_actual` 回填**。
8. **`git_commit` 与代码生效时刻不同步(第 4 节)。** -024/-025 的清单是 20 键但 `git_commit` 仍写 a8c3b7d,因为新代码 21:35 已被子进程使用、commit 21:47 才落盘。**清单里的 commit 是"当时 HEAD",不是"当时生效的代码"** —— 以后跨 commit 批次要用键集而不是 `git_commit` 分组。
9. **未解释项(原样列出,不美化)**:(a) -009 的分支判官三次 512 截断为什么集中在同一条(同一条 T0 文本、同一提示词,前 23 条只有 -004 截断 1 次)—— 现有字段只给了 `finish_reason`,没给提示词长度,无法判断;(b) -013 立场判官三次截断后 reason 里是 `ValueError: Stance output is not valid JSON` 而不是 `unclear`,与 -009 的 `Judge returned no valid A/B/C after 3 attempts` 用词不同,两个失败路径的文案没有统一;(c) 反思正文最短 -009=1336 字与最长 -014=2661 字差近一倍,原因未查。

---

## 九、下一步建议

1. **台账是未跟踪文件,批次在途时不要用带清理的拉取。** 本轮实测的直接后果:一份 `pull --no-commit` + 清理把 29 条记录的 `seconds`/`attempt`/`model_actual` 全部抹掉,而 lane 日志因 `.gitignore` 幸存 —— 幸存者恰好是"被忽略的"那部分。批次在途时优先 `git fetch` + `git merge --no-clean`,或把台账目录列入忽略规则。
2. **给台账换载体或加补写。** 两种可行做法:(a) `batch_run` 每条落盘后把关键字段也写进 `case01/runs/<id>/run.json`(它已被 `runs/` 规则忽略,清理扫不到,且天然与数据同生命周期);(b) 保留 `ledger.jsonl` 但在批次结束时用只读方式从 run.json 重建一次,并把"台账行数 ≠ run.json 条数"当硬错误告警。
3. **把截断接进生产路径的 manifest(第 3.2 节)。** 让 `orchestrator` 落盘前汇总 `client.truncations`(与注入器 `pipeline.py:54-60` 同一句话),把本批这 12 次截断变成 run.json 里可读的 `manifest_warnings`。同时把判定类调用的 512/800 上限问题一并处理 —— **-009、-013 两条判废就是 512 上限吃光输出**,不是模型不会答。
4. **把 `batch_analyze` 的"台账不存在"从静默返回改成报错或明确降级提示**(并在输出里写明"0 条来自台账,改用 X")。本轮它一旦真跑就会写出 0 条的 `analysis.md`,比没跑更糟。
5. **补一个 quick_scan 降级样本**(第 3.4 节)。本批 0 条 quick_scan,`dae4934` 的留痕自洽仍未拿到生产证据 —— 需要一个 judge 缺失/后端为 rules 的批次来验,或在小样本 smoke 里显式造一条。
6. **下一批(带固定种子)要留的核对项**:逐条读 `manifest.seed` 是否真为固定值、`seed` 非 null 时请求体是否带 `seed`、以及"有 seed 也只买到有限可复现性"的对外口径别越线(第三档仍不能写进评审材料)。
7. **A 分支为 0 的两种解释继续不可分**。要拆开,得在记录里留"分支判官对 A 的拒绝理由原文"或"素材里坚决买入表态的命中数";现有 `branch_action.reason` 只有最终判定理由。在拆开之前,对外口径维持"不主张行为级改变"。

---

*产物状态:本报告与 29 条批次产物**都在工作树里,未 `git add`、未 commit、未 push**(按要求;且另一合作者正在同一仓库提交)。临时统计脚本放在仓外 Temp 目录,已删除;`reflection_audit` 与所有 git 命令均为只读形态(`--runs-dir` / `log` / `reflog` / `stash list`,未用 `--clean`/`--write`/`--banner`)。*
