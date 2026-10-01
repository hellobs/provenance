# 分支判定对照 — 规则表 vs LLM

| 指标 | 值 |
|---|---|
| 记录数 | 25 |
| LLM 有效判定 | 25 |
| 规则 vs LLM 一致 | 18(0.72) |
| judge 模式记录 | 16 |
| judge 记录 LLM=实际 | 5(0.312) |
| LLM 分布 | {'C': 17, 'B': 8} |
| 实际分布 | {'A': 2, 'B': 16, 'C': 7} |
| 类别塌缩警告 | False |

| run | 规则 | LLM | 实际 | source |
|---|---|---|---|---|
| live-case01-mavis-A-1720 | C | C | A | preset |
| live-case01-mavis-B-1926 | C | C | B | judge |
| live-case01-mavis-B-2125 | C | C | B | judge |
| live-case01-mavis-B-2131 | C | C | B | judge |
| live-case01-mavis-B-2139 | C | C | B | judge |
| live-case01-mavis-B-2151 | C | B ⚠ | B | judge |
| live-case01-mavis-B-2158 | C | C | B | judge |
| live-case01-mavis-B-2237 | C | C | B | judge |
| e-case01-mavis-auto-2253 | C | C | B | judge |
| live-case01-mavis-C-1443 | C | B ⚠ | C | preset |
| live-case01-mavis-C-1452 | C | C | C | preset |
| live-case01-mavis-C-1630 | C | C | C | preset |
| live-case01-mavis-C-1642 | C | C | C | preset |
| e-case01-mavis-auto-1320 | C | B ⚠ | B | judge |
| e-case01-mavis-auto-1351 | C | C | B | judge |
| e-case01-mavis-auto-1702 | C | C | B | judge |
| e-case01-mavis-auto-1859 | C | C | C | judge |
| e-case01-mavis-auto-2011 | C | C | B | judge |
| e-case01-mavis-auto-2210 | C | C | B | judge |
| live-case01-mavis-A-0958 | B | B | A | preset |
| e-case01-mavis-auto-0950 | C | B ⚠ | B | judge |
| e-case01-mavis-auto-1820 | C | B ⚠ | B | judge |
| live-case01-mavis-C-1609 | C | B ⚠ | C | preset |
| live-case01-mavis-C-1630 | C | C | C | preset |
| live-case01-mavis-B-0850 | C | B ⚠ | B | preset |

## 规则 vs LLM 不一致样例(逐条人工看谁对)
- live-case01-mavis-B-2151 规则=C LLM=B 实际=B (judge)
- live-case01-mavis-C-1443 规则=C LLM=B 实际=C (preset)
- e-case01-mavis-auto-1320 规则=C LLM=B 实际=B (judge)
- e-case01-mavis-auto-0950 规则=C LLM=B 实际=B (judge)
- e-case01-mavis-auto-1820 规则=C LLM=B 实际=B (judge)
- live-case01-mavis-C-1609 规则=C LLM=B 实际=C (preset)
- live-case01-mavis-B-0850 规则=C LLM=B 实际=B (preset)
