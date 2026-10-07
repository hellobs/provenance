# 分支判定对照 — 规则表 vs LLM

| 指标 | 值 |
|---|---|
| 判定模型 | qwen3:4b-instruct-2507-q4_K_M · --llm ollama · CASE01_LLM_MODEL=qwen3:4b-instruct-2507-q4_K_M|
| 判定 prompt | sha8=2caf63f9 · 与出厂默认相同=True|
| 记录数 | 30 |
| LLM 有效判定 | 30 |
| 规则 vs LLM 一致 | 16(0.533) |
| judge 模式记录 | 30 |
| judge 记录 LLM=实际 | 29(0.967) |
| LLM 分布 | {'B': 21, 'C': 9} |
| 实际分布 | {'B': 20, 'C': 10} |
| 类别塌缩警告 | False |

| run | 规则 | LLM | 实际 | source |
|---|---|---|---|---|
| 261007-142834-sb20b2-004 | C | B ⚠ | B | judge |
| 06-214428-seedbase12-001 | B | B | B | judge |
| -261007-130642-sb20b-011 | B | B | B | judge |
| 06-223822-seedbase24-003 | C | B ⚠ | B | judge |
| 261007-142834-sb20b2-010 | B | B | B | judge |
| 261007-142834-sb20b2-018 | C | B ⚠ | B | judge |
| -261007-130642-sb20b-008 | B | B | B | judge |
| 1007-095428-rep8same-001 | B | B | B | judge |
| 61007-111345-thinkOn-001 | B | B | B | judge |
| h-261007-101434-sb16-005 | B | B | B | judge |
| 1004-220441-h120seed-006 | C | C | C | judge |
| 1004-220441-h120seed-014 | C | C | C | judge |
| 1004-220441-h120seed-020 | C | C | C | judge |
| 1004-220441-h120seed-026 | C | C | C | judge |
| 261006-153009-r2h06b-020 | C | C | C | judge |
| 1007-110702-seedOn8b-002 | C | C | C | judge |
| 261006-153009-r2h06b-001 | C | C | C | judge |
| 261006-153009-r2h06b-006 | C | C | C | judge |
| 261006-153009-r2h06b-034 | C | C | C | judge |
| -261006-142519-r2h06-001 | C | B ⚠ | C | judge |
| -261004-203137-h120b-023 | C | B ⚠ | B | judge |
| 261006-153009-r2h06b-013 | C | B ⚠ | B | judge |
| -261004-203137-h120b-012 | C | B ⚠ | B | judge |
| -261004-203137-h120b-016 | C | B ⚠ | B | judge |
| -261004-203137-h120b-017 | C | B ⚠ | B | judge |
| -261004-203137-h120b-001 | C | B ⚠ | B | judge |
| -261004-203137-h120b-022 | C | B ⚠ | B | judge |
| -261004-203137-h120b-002 | C | B ⚠ | B | judge |
| -261004-203137-h120b-026 | C | B ⚠ | B | judge |
| -261004-203137-h120b-003 | C | B ⚠ | B | judge |

## 规则 vs LLM 不一致样例(逐条人工看谁对)
- 261007-142834-sb20b2-004 规则=C LLM=B 实际=B (judge)
- 06-223822-seedbase24-003 规则=C LLM=B 实际=B (judge)
- 261007-142834-sb20b2-018 规则=C LLM=B 实际=B (judge)
- -261006-142519-r2h06-001 规则=C LLM=B 实际=C (judge)
- -261004-203137-h120b-023 规则=C LLM=B 实际=B (judge)
- 261006-153009-r2h06b-013 规则=C LLM=B 实际=B (judge)
- -261004-203137-h120b-012 规则=C LLM=B 实际=B (judge)
- -261004-203137-h120b-016 规则=C LLM=B 实际=B (judge)
- -261004-203137-h120b-017 规则=C LLM=B 实际=B (judge)
- -261004-203137-h120b-001 规则=C LLM=B 实际=B (judge)
