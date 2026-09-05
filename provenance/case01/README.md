# case01 — GTC Case 01 执行引擎(provenance 仓内子包)

GTC Case 01(0904doc)的运行引擎:节点驱动的受控实验,
非 MAVIS 连续仿真。Ethan Lin(普通投资者)× Investment AI(本地 Ollama
+ Financial Data 检索)→ Branch(A/B/C)→ Timeline 推进 → 最终反馈
→ Reflection → Router → 专家审核(后置里程碑)。

## 结构

```
case01/
├─ world/             # 纯逻辑,无 LLM 依赖
│  ├─ state.py        #   World/System:日期推进/事件释放/信息权限/Ethan 状态
│  ├─ timelines.py    #   Timeline A/B 剧本(03 文档数据化)
│  └─ branch.py       #   Branch 判定:LLM judge(主)+ 规则版(no-llm 降级)
├─ agents/
│  ├─ llm.py          #   轻量 Ollama client(chat + embedding)
│  ├─ financial.py    #   Financial Data 加载/向量检索(保留来源元数据)
│  ├─ investment_ai.py#   Investment AI(06 system prompt + 检索注入)
│  └─ ethan.py        #   Ethan(状态注入 + 冲突重生成)
├─ data/
│  ├─ make_hcm_sample.py  # 从 0904doc 生成 HCM(虚构)样例
│  └─ financial/hcm/docs.json
├─ runs/<run_id>/     # 每次 Run 的完整记录(JSONL)
├─ run.py             # CLI: python -m case01.run
└─ tests/             # 单元测试(纯逻辑 / stub LLM,无网络)
```

## 用法

```bash
# 跑一次 T0(全中文;Ethan 问 → AI 检索+答 → LLM 判 Branch)
python -m case01.run --run-id my-run

# 强制 Branch 推进测试(跳过判定)
python -m case01.run --timeline A

# 不调 LLM(测全链路,规则判定)
python -m case01.run --no-llm
```

## M1-M2 完成度(2026-09)

- [x] World 状态机(日期跳跃推进、事件按剧本释放、信息权限:AI 只见
      已释放公开事件;个人后果默认隐藏、披露节点才开放)
- [x] Timeline A/B 剧本数据化(03 文档)
- [x] Branch 判定:LLM 独立 judge(A/B/C+理由);规则版 no-llm 降级
- [x] Branch C 条件化方案解析(LLM → 仓位 fraction/等待条件)
- [x] Investment AI:Ollama + Financial Data 向量检索,结果保留来源/
      类型/时间/依赖线索(识别 MarketScope 二次传播),source_stats 审计
- [x] Ethan:状态注入 + 状态冲突重生成 + 最终反馈披露个人后果
- [x] **M2 完整 Run 编排**(orchestrator):T0 → Branch → 逐节点推进
      (事件释放+账面更新,中间无对话) → 09-15 最终反馈 → 完整记录
      (events/state_history/final_feedback/audit)
- [x] runs/<run_id>/ JSONL + run.json 落盘;no-llm 全链路 A/B/C 测试
- [x] **M3:Reflection + Router**——Run 结束后后台触发 8 维反思(本地 qwen3
      同源,结构化自述材料,信息边界不泄露 Branch/Timeline);Router 独立模型
      拆分问题→专业领域/风险/路由理由(JSON 结构化);渲染页"七 Reflection/
      八 Router"章节
- [x] **① T0 多轮(06 §3.1)**:AI 若追问收入/风险承受/资金用途等隐私 →
      Ethan 自然拒答 → AI 带完整对话历史给出最终结论(`t0_rounds` 记录);
      Ethan 首轮不再主动预告隐私立场
- [x] **② Branch C 逐节点条件监测(01 §六 / 03 §八)**:条件化方案解析出
      `trigger`(keyword / price_below / price_above)+ `buy_fraction`;
      时间线推进时逐节点求值(否定公告句不误触发),满足即按 T0 方案买入、
      09-07 与 A 同规则退出;`condition_monitor` 与 `condition_check`
      audit 落盘;C 最终反馈按实际执行结果生成
- [x] **③ Branch C 个人后果(方案 A)**:见下《设计决策》第 1 条
- [x] Router 输出解析容错(剥代码围栏;整体失败按顶层对象恢复;
      输出约束:内容禁英文双引号)——实测 demo-3 由 0 条恢复到 7 条
- [ ] M4:Governance Platform(Tongmu 团队)对接(专家审核 Approve/Edit/Reject)
- [ ] M5:训练材料归集 + Run 回溯

## 与 0904 文档的设计决策与假设(2026-09-05)

以下为 03/06 文档未明确处,Leo 侧实现所采用的设计,需陈总确认或后续
文档化:

1. **Branch C 个人后果 = 方案 A(复用 A 线资金用途背景,按结果派生)**。
   06 §5.3 的 C 模板含 `{final_personal_consequence}` 槽位,但 03/06 从未
   给出 C 的具体后果内容。2026-09-05 沟通确定:与 03 §五 Branch A 共用
   "Ethan 原计划在未来半年内将约 20 万用作小型创业启动资金"这一既有资金
   规划背景,后果随实际执行结果自动派生(`orchestrator.c_personal_consequence`):
   C 建仓并亏损 → 启动资金按实际结果减少、计划压缩/推迟;从未建仓 →
   资金原样保留、计划未受影响。**不新增剧情**;若后续获得 C 专属文案,
   替换 `HIDDEN_CONTEXT_A/B` 同款注入位置即可。
2. **C 建仓后的退出时点**:03 只写了 A 于 09-07 按 $27.40 退出(第四节),
   未写 C 何时退出。实现采用与 A 相同的退出节点(09-07,按 $27.40 全部
   退出),使 C 的"后果"与 A 可比;若文档规定 C 应持有至期末,改动
   `EXIT_DATE_A/EXIT_PRICE_A` 的应用范围即可。
3. **C 条件触发的买入价**:取"条件满足当日收盘价"(Timeline A 的 price 事件)。
4. **T0 多轮触发率观察**:三轮真机 demo(2026-09-05)中 Investment AI
   均未追问隐私 → 均走单轮(不是缺陷,是模型实际行为);多轮路径由
   stub 测试覆盖,真实触发率待更多轮次统计。
5. **Router 输出格式约束**:字段内容内不得出现英文双引号、不得使用
   Markdown 代码围栏包裹 JSON(见 `reflection.ROUTER_JSON_HINT`);解析器
   对历史畸形输出做围栏剥离 + 顶层对象容错恢复。
6. **演示 run(不入库)**:`runs/` 与 `runs_html/` 为 gitignored 生成物;
   2026-09-05 真机三线 demo-1(B,自动判定)/ demo-2(A,强制)/
   demo-3(C,强制,条件监测全程未触发)齐备 Reflection + Router 分流。

## 依赖

- Ollama 本地:qwen3:4b-instruct-2507-q4_K_M(chat)、
  qwen3-embedding:0.6b-q8_0(检索)
- Ethan/Router 后续接外部 API(provider 接口预留)
- 真实公司 Financial Data(肖叶萱提供后替换 HCM 样例;字段格式不变)
