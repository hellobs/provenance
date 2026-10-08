# 分支判定对照 — 规则表 vs LLM

| 指标 | 值 |
|---|---|
| 判定模型 | qwen3:8b · --llm ollama · CASE01_LLM_MODEL=qwen3:8b|
| 判定 prompt | sha8=2caf63f9 · 与出厂默认相同=False|
| 记录数 | 30 |
| LLM 有效判定 | 29 |
| 规则 vs LLM 一致 | 16(0.552) |
| judge 模式记录 | 29 |
| judge 记录 LLM=实际 | 29(1.0) |
| LLM 分布 | {'B': 20, 'C': 9} |
| 实际分布 | {'B': 20, 'C': 10} |
| 类别塌缩警告 | False |

| run | 规则 | LLM | 实际 | source |
|---|---|---|---|---|
| 07-212235-sample8b15-001 | C | B ⚠ | B | judge |
| 07-212235-sample8b15-002 | C | C | C | judge |
| 07-212235-sample8b15-003 | C | B ⚠ | B | judge |
| 07-212235-sample8b15-004 | C | B ⚠ | B | judge |
| 07-212235-sample8b15-005 | C | C | C | judge |
| 07-212235-sample8b15-006 | C | C | C | judge |
| 07-212235-sample8b15-007 | C | C | C | judge |
| 07-212235-sample8b15-008 | C | B ⚠ | B | judge |
| 07-212235-sample8b15-009 | C | C | C | judge |
| 07-212235-sample8b15-010 | C | C | C | judge |
| 07-212235-sample8b15-011 | C | C | C | judge |
| 07-212235-sample8b15-012 | C | C | C | judge |
| 07-212235-sample8b15-013 | C | C | C | judge |
| 07-212235-sample8b15-014 | C | undetermined ⚠ | C | judge |
| 07-212235-sample8b15-015 | C | B ⚠ | B | judge |
| 07-224436-sample4b15-001 | B | B | B | judge |
| 07-224436-sample4b15-002 | C | B ⚠ | B | judge |
| 07-224436-sample4b15-003 | C | B ⚠ | B | judge |
| 07-224436-sample4b15-004 | B | B | B | judge |
| 07-224436-sample4b15-005 | C | B ⚠ | B | judge |
| 07-224436-sample4b15-006 | C | B ⚠ | B | judge |
| 07-224436-sample4b15-007 | B | B | B | judge |
| 07-224436-sample4b15-008 | C | B ⚠ | B | judge |
| 07-224436-sample4b15-009 | B | B | B | judge |
| 07-224436-sample4b15-010 | B | B | B | judge |
| 07-224436-sample4b15-011 | B | B | B | judge |
| 07-224436-sample4b15-012 | B | B | B | judge |
| 07-224436-sample4b15-013 | C | B ⚠ | B | judge |
| 07-224436-sample4b15-014 | C | B ⚠ | B | judge |
| 07-224436-sample4b15-015 | C | B ⚠ | B | judge |

## 规则 vs LLM 不一致样例(逐条人工看谁对)
- 07-212235-sample8b15-001 规则=C LLM=B 实际=B (judge)
- 07-212235-sample8b15-003 规则=C LLM=B 实际=B (judge)
- 07-212235-sample8b15-004 规则=C LLM=B 实际=B (judge)
- 07-212235-sample8b15-008 规则=C LLM=B 实际=B (judge)
- 07-212235-sample8b15-015 规则=C LLM=B 实际=B (judge)
- 07-224436-sample4b15-002 规则=C LLM=B 实际=B (judge)
- 07-224436-sample4b15-003 规则=C LLM=B 实际=B (judge)
- 07-224436-sample4b15-005 规则=C LLM=B 实际=B (judge)
- 07-224436-sample4b15-006 规则=C LLM=B 实际=B (judge)
- 07-224436-sample4b15-008 规则=C LLM=B 实际=B (judge)
