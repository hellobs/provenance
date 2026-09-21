# Case 01 · 框架层级图（LLM 接入节点与不确定性来源）

> **状态**:参考
> **最后核对**:2026-09-21
> **说明**:框架层级与 LLM 接入节点;仍有效
> **最后核对**:2026-09-21
> **说明**:框架层级与 LLM 接入节点;仍有效
> **最后核对**:2026-09-21
> **说明**:框架层级与 LLM 接入节点;仍有效
> **最后核对**:2026-09-21
> **说明**:框架层级与 LLM 接入节点;仍有效

> 对象：GTC Case 01 受控单案例实验执行引擎（`case01/`）。
> 目的：对照 2026-09-08 会议 TongMu 要求，梳理所有大模型接入节点、模型选择、输入输出与不确定性来源，作为"可解释性 / 可追溯"的评估基准。
> 版本：2026-09-17（补 mavis 路径节点 N9/N10 与 mavis 各角色 LLM 映射；
> 2026-09-17 起 mavis+injector 为 case01 当前运行路径，旧引擎 N1–N8 供归档回溯，见 `case01/README.md` 归档注记）。

---

## 1. 整体运行时层级

```
┌─────────────────────────────────────────────────────────────────────┐
│  case01 引擎（程序决定事实，LLM 只负责"表达"）                        │
│                                                                     │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │ World/System │   │ Orchestrator │   │ Reflection/Router（事后） │  │
│  │ 纯逻辑、无LLM │   │  编排循环     │   │  反思 → 拆分问题 → 路由专家  │  │
│  └──────┬──────┘   └──────┬───────┘   └────────────┬─────────────┘  │
│         │                │                         │                │
│         │   时间/分支/信息可见性由程序控制，节点时间轴固定              │
└─────────┼────────────────┼─────────────────────────┼────────────────┘
          │                │                         │
          ▼                ▼                         ▼
   ◀──── LLM 接入层（见下表，按调用方区分） ────▶   外部：OpenRouter / 未来浩泽
   ◀──── 本地：Ollama（Investment AI 硬性本地）──▶
```

## 2. LLM 接入节点清单

| # | 节点（调用方） | 用途 | 模型 / Provider | 默认 | 温度 | 输入 | 输出 |
|---|---|---|---|---|---|---|---|
| N1 | **Investment AI 回答**（`agents/investment_ai.py`） | T0 咨询 + 最终反馈回应 | 本地 Ollama `qwen3:4b-instruct-2507-q4_K_M` | **硬性本地，不可切外部** | 0.5 | 检索到的 Financial Data + 对话历史 | 投资建议文本 |
| N2 | **Financial Data 检索（embedding）**（`agents/financial.py`） | 向量检索 + 防未来信息泄露 | 本地 Ollama `qwen3-embedding:0.6b-q8_0` | 本地 | — | query + 截止当前日期 | 命中文档 + 来源独立性统计 |
| N3 | **Ethan**（`agents/ethan.py`） | 扮演普通投资者 | 本地 Ollama；**可切换** OpenRouter | 本地 | 0.8 | 可见状态 + 公开事件 | 自然语言表达（含冲突重生成 ≤3 次） |
| N4 | **Branch Judge**（`world/branch.py`） | T0 回答 → A/B/C | 默认本地；**可切换** OpenRouter（用 `router_llm`） | 本地 | 0.1 | Investment AI 的 T0 回答 | `{branch, reason}` |
| N5 | **ConditionPlanParser**（`world/branch.py`） | C 建议 → 可执行仓位 | 默认本地；可切换 OpenRouter（用 `router_llm`） | 本地 | 0.1 | 条件化建议文本 | `{action, fraction, trigger, ...}` |
| N6 | **Reflection（8 维反思）**（`reflection.py`） | Run 后的经历回顾 | **本地 Ollama，与 Investment AI 同源**（0904 硬性：本地、同源，不走外部） | 本地 | 0.4 | Investment AI 有权知道的完整经历 | 8 维中文反思文本 |
| N7 | **Router（问题拆分 / 路由）**（`reflection.py`） | Reflection → 若干待审问题 | **推荐外部 OpenRouter**（独立模型，与反思同模型无意义）；默认可回退本地 | 本地（默认）/ 外部（推荐） | — | Reflection 文本 | `issues[]: {摘要, 专业类别, 风险等级, 路由理由}` |
| N8 | **（规划中）浩泽大模型 API** | 会议：AI 助手将接入浩泽大模型 | 外部 API | — | — | 同 N1/N3/N7 按需替换 | — |

> 说明：`orchestrator.run_case01` 里 `ethan_llm` / `router_llm` 缺省都指向本地 `llm`（Ollama）；`run.py --external-ethan` 可将 N3 / N4 / N5 / N7 切到 OpenRouter。Reflection（N6）始终本地、同源。

## 2.1 mavis 路径的 LLM 接入节点（2026-09-17 起当前运行架构）

> 当前 case01 经 `case01/injector/` 作用于 mavis 体系运行（见 `case01_over_mavis_设计说明.md`）。
> mavis 只负责角色表达与交互；世界事实由 injector 复用 case01 `world/` 决定。下表是
> mavis 路径下的 LLM 接入面，接续 N8 编号。

| # | 节点（调用方） | 用途 | 模型 / Provider | 默认 | 温度 | 输入 | 输出 |
|---|---|---|---|---|---|---|---|
| N9 | **Ethan（mavis Agent，外部 API 可选）**（`bridge._apply_ethan_provider`） | 扮演普通投资者，受 injector 外部状态 / 交互请求驱动 | 本地 Ollama；**经环境变量 `CASE01_ETHAN_*` 可切外部 OpenAI 兼容 API** | 本地 | 角色配置 | injector 注入的临时状态 + 已释放 story 事件 + `personal situation`（最终反馈轮） | Ethan 自然语言表达 |
| N10 | **Investment AI（mavis Agent，本地）**（injector scenario agent 配置） | T0 咨询 + 最终反馈回应（B 线角色担当） | 本地 Ollama `qwen3:4b-instruct-2507-q4_K_M` | **硬性本地，不可切外部** | 角色配置 | Financial Data（story 事件写入记忆）+ 注入上下文 | 投资建议文本 |
| N11 | **ConditionPlanParser（mavis 路径动态化）**（`bridge._install_c_plan` + `world/branch.ConditionPlanParser`） | T0 后用 Investment AI 的回答解析 C 条件化仓位方案 | 默认本地（复用 `agent.llm`）；复用 N5 相同的 `ConditionPlanParser` | 本地 | 0.1 | Investment AI 的 T0 回答 | `{action, fraction, buy_fraction, condition, trigger, judge, source}` |

> mavis 路径下，N1/N2/N3（旧引擎的 `investment_ai.py` / `financial.py` / `ethan.py`）不再承担
> 运行时对话与检索：对话由 mavis Agent 表达（N9/N10），检索语义由"按节点释放的 story 事件"
> 承担（injector 记录 `retrievals.mode=injection`）。Reflection（N6）/ Router（N7）在 mavis
> 路径由 `pipeline.py --reflect` 在映射记录上复用 `case01.reflection` 补齐。
> 临时状态经 mavis 通用外部状态入口注入（不写记忆），关键交互经通用交互请求入口发起
> （跳过冷却与概率门，内容仍由 LLM 生成）；两者都是 mavis 侧纯新增、默认关闭的能力。

## 3. Provider 与依赖

| Provider | 地址 / 标识 | 依赖 | 失败表现 |
|---|---|---|---|
| Ollama（本地） | `http://127.0.0.1:11434` | Ollama 服务在线、模型已拉取 | `is_available()` 为假；调用抛错 |
| OpenRouter（外部） | `https://openrouter.ai/api/v1`，模型 `minimax/minimax-m3:free` | API Key（env `OPENROUTER_API_KEY` 或 `case01/.secrets.json`） | key 缺失 → 构造即 `RuntimeError`；网络/时延→超时重试 |
| 浩泽 API（规划） | TBD | 待接入 / 替换 N1、N3、N7 任意节点 | TBD |

## 4. 不确定性来源（按影响排序）

| # | 来源 | 涉及节点 | 说明 / 已设兜底 |
|---|---|---|---|
| U1 | **本地 Ollama 可用性** | 全部本地节点 | 若 Ollama 未启动 → 整链不可用；`is_available()` 可预检；未内置自动降级到云端（09 规定 AI 本地） |
| U2 | **模型生成随机性** | N3/Ethan（0.8） | 表达可能漂移；靠 **状态感知冲突检测 + 重生成 ≤3** 兜底（`agents/ethan.py` CONFLICT_RULES） |
| U3 | **分支判定漂移** | N4/Judge（0.1） | 低温压低随机；但 LLM 判定对长文/复述用户话术可能误判 → 提供 `RuleBranchRouter` 关键词降级（已知局限） |
| U4 | **条件/触发解析弱** | N5/N4 | C 条件由自然语言推导机检触发（价格/关键词正则），歧义时 `trigger=none`（永不触发）——防误触发，但可能漏触发 |
| U5 | **长上下文截断** | N6/Reflection | 反思材料 12k+ 字符 → 需 `num_ctx=32768`；用 `native_chat` 放大上下文，超时放宽到 600s（4b 慢） |
| U6 | **外部网络时延 / Key** | N7/Router、N3/N4/N5 外部态 | 超时指数退避重试；key 缺失显式报错（不静默） |
| U7 | **OpenRouter 免费模型的稳定性** | N7/Router | `:free` 限额/波动；后续可替换为付费或浩泽模型 |
| U8 | **（未来）浩泽 API** | 待定节点 | 接入后需按节点更新此表与层级图 |
| U9 | **mavis 自发行为** | N9/N10（mavis Agent） | mavis 允许角色自发对话/走动，与注入的请求并行；已接受并记录（验收报告 `自发行为统计`），不抑制 |
| U10 | **强制交互依赖运行时状态** | N9/N10 | mavis 对话前置条件看运行时状态（同址/静止/日程/冷却）；injector 在关键节点运行时钉定（`_pin_for_interaction`）+ 节点内重试兜底 |
| U11 | **mavis 路径 C 方案解析** | N11 | 解析基于 LLM 回答，失败/歧义时回退 `action=wait,fraction=0`（不建仓、不误买）；英文关键词与中文事件摘要匹配靠 `_EN_KW_TO_CN` 映射 + 否定句判定，必要时 `trigger=none`（永不触发） |

## 5. 降级 / 审计路径（非 LLM）

- `run.py --no-llm`：全链路固定文本，测字段与流程，不调 LLM。
- `RuleBranchRouter`：branch 关键词规则（降级，不用于正式结论）。
- 审计闭环：所有 LLM 输出均伴随**程序方状态快照**（`state_history` / `audit` / `retrievals`），保证"模型表达"与"程序真相"可对照，即使模型不可复现，事实链仍可追溯。

## 6. 给平台的对接影响

- 契约（`serve.py` / `case01_api.openapi.yaml`）只暴露**只读**结果（runs、runs/{id}、full-context），**不暴露** N1–N8 内部调用细节；专家端看到的是反思+问题，而非模型内部。
- mavis 路径（N9–N11）同样不影响平台契约：平台看到的是映射后的 case01 run.json（相同顶层键），mavis 的 LLM 表达细节不出现在专家端。`demo-A/B/C-mavis` 三条新样本已落盘 `case01/runs/`，供平台换样（见 `通知平台侧_换样本_20260917.md`）。
- 若平台要展示"大模型性能指标"（统一后台第二模块），建议以 `run.json` 内的 `retrievals` + Router `issues` 元数据为入口，不要读取引擎内部。