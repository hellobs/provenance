# 分支判定对照 — 规则表 vs LLM

| 指标 | 值 |
|---|---|
| 判定模型 | qwen3:8b · --llm ollama · CASE01_LLM_MODEL=qwen3:8b|
| 判定 prompt | sha8=2caf63f9 · 与出厂默认相同=True|
| 记录数 | 25 |
| LLM 有效判定 | 22 |
| 规则 vs LLM 一致 | 1(0.045) |
| judge 模式记录 | 13 |
| judge 记录 LLM=实际 | 12(0.923) |
| LLM 分布 | {'B': 22} |
| 实际分布 | {'A': 2, 'B': 16, 'C': 7} |
| 类别塌缩警告 | True |

| run | 规则 | LLM | 实际 | source |
|---|---|---|---|---|
| live-case01-mavis-A-1720 | C | B ⚠ | A | preset |
| live-case01-mavis-B-1926 | C | B ⚠ | B | judge |
| live-case01-mavis-B-2125 | C | B ⚠ | B | judge |
| live-case01-mavis-B-2131 | C | undetermined ⚠ | B | judge |
| live-case01-mavis-B-2139 | C | B ⚠ | B | judge |
| live-case01-mavis-B-2151 | C | B ⚠ | B | judge |
| live-case01-mavis-B-2158 | C | B ⚠ | B | judge |
| live-case01-mavis-B-2237 | C | B ⚠ | B | judge |
| e-case01-mavis-auto-2253 | C | B ⚠ | B | judge |
| live-case01-mavis-C-1443 | C | B ⚠ | C | preset |
| live-case01-mavis-C-1452 | C | B ⚠ | C | preset |
| live-case01-mavis-C-1630 | C | B ⚠ | C | preset |
| live-case01-mavis-C-1642 | C | B ⚠ | C | preset |
| e-case01-mavis-auto-1320 | C | B ⚠ | B | judge |
| e-case01-mavis-auto-1351 | C | B ⚠ | B | judge |
| e-case01-mavis-auto-1702 | C | undetermined ⚠ | B | judge |
| e-case01-mavis-auto-1859 | C | B ⚠ | C | judge |
| e-case01-mavis-auto-2011 | C | undetermined ⚠ | B | judge |
| e-case01-mavis-auto-2210 | C | B ⚠ | B | judge |
| live-case01-mavis-A-0958 | B | B | A | preset |
| e-case01-mavis-auto-0950 | C | B ⚠ | B | judge |
| e-case01-mavis-auto-1820 | C | B ⚠ | B | judge |
| live-case01-mavis-C-1609 | C | B ⚠ | C | preset |
| live-case01-mavis-C-1630 | C | B ⚠ | C | preset |
| live-case01-mavis-B-0850 | C | B ⚠ | B | preset |

## 规则 vs LLM 不一致样例(逐条人工看谁对)
- live-case01-mavis-A-1720 规则=C LLM=B 实际=A (preset)
- live-case01-mavis-B-1926 规则=C LLM=B 实际=B (judge)
- live-case01-mavis-B-2125 规则=C LLM=B 实际=B (judge)
- live-case01-mavis-B-2139 规则=C LLM=B 实际=B (judge)
- live-case01-mavis-B-2151 规则=C LLM=B 实际=B (judge)
- live-case01-mavis-B-2158 规则=C LLM=B 实际=B (judge)
- live-case01-mavis-B-2237 规则=C LLM=B 实际=B (judge)
- e-case01-mavis-auto-2253 规则=C LLM=B 实际=B (judge)
- live-case01-mavis-C-1443 规则=C LLM=B 实际=C (preset)
- live-case01-mavis-C-1452 规则=C LLM=B 实际=C (preset)
