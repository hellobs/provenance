# case01 — GTC Case 01 执行引擎(provenance 仓内子包)

GTC Case 01(0904doc)的运行引擎:节点驱动的受控实验,
非 MAVIS 连续仿真。Ethan Lin(普通投资者)× Investment AI(本地 Ollama
+ Financial Data 检索)→ Branch(A/B/C)→ Timeline 推进 → 最终反馈
→ Reflection → Router → 专家审核(后置里程碑,平台侧)。

## 角色与联系人

- **维护与实现**:ZZR(case01 引擎;supervised by Tongmu)。
- **引擎侧职责对接**:Leo(0904 文档名义任务对象;接口语义问题由其确认)。
- **Platform / HCI 侧**:Tongmu 团队(含 ZZR;负责 Expert Review Task、
  专家审核界面、Full Context 展示与平台持久化)。
- **研究设计基准**:0904doc(01/03/04/05/06,2026-09-04)。

对接契约与分工详见 `docs/Governance平台对接说明.md` 与
`docs/对接启动_给Tongmu_20260905.md`;与 0904 文档的设计决策见文末章节。

## 结构

```
case01/
├─ world/             # 纯逻辑,无 LLM 依赖
│  ├─ state.py        #   World/System:日期推进/事件释放/信息权限/Ethan 状态
│  ├─ timelines.py    #   Timeline A/B 剧本(03 文档数据化)
│  └─ branch.py       #   Branch 判定(LLM judge + 规则版)+ C 条件触发解析/求值
├─ agents/
│  ├─ llm.py          #   Ollama / OpenRouter 轻量 client(chat/embed/native_chat)
│  ├─ secrets.py      #   OpenRouter key 安全读取(.secrets.json,不入 git)
│  ├─ financial.py    #   Financial Data 加载/向量检索(来源元数据,防未来泄露)
│  ├─ investment_ai.py#   Investment AI(06 system prompt + 检索注入 + 对话历史)
│  └─ ethan.py        #   Ethan(状态注入 + 冲突重生成)
├─ data/financial/hcm/docs.json   # HCM(虚构)样例资料
├─ orchestrator.py    # 完整 Run 编排(T0 多轮 → Branch → 节点推进 → 反馈 → 反思)
├─ reflection.py      # Reflection(本地同源)+ Router(问题拆分/路由)+ 容错解析
├─ full_context.py    # 专家"查看完整记录"自然语言全文(04 六.3)
├─ serve.py           # 只读数据 API(FastAPI,供 Governance Platform)
├─ render.py          # run → 学术排版 HTML(折叠式演示页,输出 runs_html/)
├─ run.py             # CLI: python -m case01.run
├─ docs/              # 对接契约与说明(OpenAPI / 对接说明 / 启动包)
├─ runs/<run_id>/     # 每次 Run 的完整记录(gitignored)
└─ tests/             # 单元测试(纯逻辑 / stub LLM,无网络;119 用例)
```

## 用法

```bash
# 完整 Run(自动判定 Branch;Investment AI 本地,Reflection 本地)
python -m case01.run --run-id my-run

# Ethan/Router 走外部 API(OpenRouter minimax;需 .secrets.json 或环境变量)
python -m case01.run --run-id my-run --external-ethan

# 强制 Branch(A/B/C),便于对齐三线演示
python -m case01.run --timeline A --run-id demo-a

# 不调 LLM(规则判定,测全链路)
python -m case01.run --no-llm

# 对已有 run 补 Reflection + Router(不重跑对话)
python -m case01.run --reflect-only --run-id <run_id> --external-ethan

# 只读数据 API(供 Governance Platform)
python -m uvicorn case01.serve:app --port 5002   # 需在 provenance 包根目录

# 渲染演示页(case01/runs_html/)
python -m case01.render
```

## 完成度(2026-09)

- [x] World 状态机(日期跳跃推进、事件按剧本释放、信息权限:AI 只见
      已释放公开事件;个人后果默认隐藏、披露节点才开放)
- [x] Timeline A/B 剧本数据化(03 文档)
- [x] Branch 判定:LLM 独立 judge(A/B/C+理由);规则版 no-llm 降级
- [x] Branch C 条件化方案解析(LLM → 仓位/等待条件 → trigger + buy_fraction)
- [x] Investment AI:Ollama + Financial Data 向量检索,来源/类型/时间/
      依赖线索与 source_stats 审计;检索严格防未来信息泄露
- [x] Ethan:状态注入 + 状态冲突重生成 + 最终反馈披露个人后果
- [x] **M2 完整 Run 编排**(orchestrator):T0 → Branch → 逐节点推进
      → 09-15 最终反馈 → 完整记录(events/state_history/final_feedback/audit)
- [x] **M3:Reflection + Router**——Run 结束后后台触发 8 维反思(本地 qwen3
      同源,信息边界不泄露 Branch/Timeline);Router 独立模型拆分问题→
      专业领域/风险/路由理由;解析容错(剥围栏 + 顶层对象恢复)
- [x] **① T0 多轮(06 §3.1)**:AI 若以真疑问/请求语气追问隐私 → Ethan 自然
      拒答 → AI 带完整对话历史给出最终结论(`t0_rounds` 记录);陈述句
      不误判(强/弱信号拆分)
- [x] **② Branch C 逐节点条件监测(01 §六 / 03 §八)**:时间线推进时逐节点
      求值(否定公告句如"无新公告"不误触发),满足即按 T0 方案买入、
      09-07 与 A 同规则退出;`condition_monitor` 与 `condition_check`
      audit 落盘;C 最终反馈按实际执行结果生成
- [x] **③ Branch C 个人后果(方案 A)**:见文末《设计决策》第 1 条
- [x] **只读数据服务 + Full Context**(M4 中引擎职责部分):serve.py 三端点
      (/api/runs、/api/runs/{run_id}、/api/runs/{run_id}/full-context);
      full_context.py 自然语言全文,不含实验元信息;OpenAPI 契约与
      对接文档见 docs/
- [x] 真机三线演示 demo-1/2/3(2026-09-05,Reflection + Router 齐备),
      真机暴露并修复两处判定 bug(否定词表、隐私追问误报)
- [ ] M4(平台侧):Expert Review Task / 审核状态机 / 冲突 / HCI,由
      Tongmu 团队按 docs/ 契约实现
- [ ] M5:训练材料归集 + Run 回溯(平台侧)

## 与 0904 文档的设计决策与假设(2026-09-05)

以下为 03/06 文档未明确处,case01 引擎实现所采用的设计(实现与仓库
维护:ZZR;supervised by Tongmu),需研究侧确认或后续文档化:

1. **Branch C 个人后果 = 方案 A(复用 A 线资金用途背景,按结果派生)**。
   06 §5.3 的 C 模板含 `{final_personal_consequence}` 槽位,但 03/06 从未
   给出 C 的具体后果内容。2026-09-05 沟通确定:与 03 §五 Branch A 共用
   "Ethan 原计划在未来半年内将约 20 万用作小型创业启动资金"这一既有资金
   规划背景,后果随实际执行结果自动派生(`orchestrator.c_personal_consequence`):
   C 建仓并亏损 → 启动资金按实际结果减少、计划压缩/推迟;从未建仓 →
   资金原样保留、计划未受影响。**不新增剧情**;若后续获得 C 专属文案,
   替换注入位置即可。
2. **C 建仓后的退出时点**:03 只写了 A 于 09-07 按 $27.40 退出(第四节),
   未写 C 何时退出。实现采用与 A 相同的退出节点(09-07,按 $27.40 全部
   退出),使 C 的"后果"与 A 可比;若文档规定 C 应持有至期末,改动
   退出规则的应用范围即可。
3. **C 条件触发的买入价**:取"条件满足当日收盘价"(Timeline A 的 price 事件)。
4. **T0 多轮触发率观察**:真机 demo 中 Investment AI 通常不问隐私 → 单轮;
   曾出现一次陈述句被误判为追问(已作为 bug 修复:仅真疑问/请求语气触发)。
   多轮路径由 stub 测试覆盖,真实触发率待更多轮次统计。
5. **Router 输出格式约束**:字段内容内不得出现英文双引号、不得使用
   Markdown 代码围栏包裹 JSON(见 `reflection.ROUTER_JSON_HINT`);解析器
   对历史畸形输出做围栏剥离 + 顶层对象容错恢复。
6. **演示 run(不入库)**:`runs/` 与 `runs_html/` 为 gitignored 生成物;
   2026-09-05 真机三线 demo-1(B,自动判定)/ demo-2(A,强制)/
   demo-3(C,强制;条件监测全程未触发、未买入,个人后果按方案 A 派生)
   齐备 Reflection + Router 分流。

## 依赖

- Ollama 本地:qwen3:4b-instruct-2507-q4_K_M(chat)、
  qwen3-embedding:0.6b-q8_0(检索);Reflection 与 Investment AI 必须本地
- Ethan / Router 经 `--external-ethan` 走 OpenRouter(minimax/minimax-m3:free),
  key 存 `case01/.secrets.json`(gitignored)或环境变量 `OPENROUTER_API_KEY`
- 真实公司 Financial Data(10 家,研究侧提供后替换/扩充 HCM 样例;字段格式不变)
