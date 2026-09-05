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
└─ tests/             # 22 用例(纯逻辑,无网络)
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
- [ ] M4:仝牧 Governance Platform 对接(专家审核 Approve/Edit/Reject)
- [ ] M5:训练材料归集 + Run 回溯

## 依赖

- Ollama 本地:qwen3:4b-instruct-2507-q4_K_M(chat)、
  qwen3-embedding:0.6b-q8_0(检索)
- Ethan/Router 后续接外部 API(provider 接口预留)
- 真实公司 Financial Data(肖叶萱提供后替换 HCM 样例;字段格式不变)
