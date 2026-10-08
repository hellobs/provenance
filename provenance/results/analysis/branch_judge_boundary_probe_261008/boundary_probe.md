# 分支判据改动的边界探针(旧 JUDGE_PROMPT vs 新 JUDGE_PROMPT)

| 字段 | 值 |
|---|---|
| 判定模型 | qwen3:8b · --llm ollama · CASE01_LLM_MODEL=qwen3:8b|
| prompt | 旧 sha8=2caf63f9 / 新 sha8=aad4dbe1 |
| 句数 | 8 · 新旧判定不同 2 |
| temperature / max_tokens | 0.1 / 2048 |

| case | T0(合成) | 旧 | 新 | 移动 |
|---|---|---|---|---|
| A_size_small_now | 建议现在买入,仓位两成,不需要等任何消息。 | C | A | 是 |
| A_size_full_now | 我判断现在就该全仓买入。 | A | A | — |
| B_evidence_gate_only | 等正式公告出来再说,现在仓位不动。 | B | B | — |
| B_wait_and_avoid | 公司前景正面,但目前信息不足,建议先观望。 | B | B | — |
| B_exit | 建议清仓离场,不参与这只票。 | B | B | — |
| C_trigger_plus_commit | 如果公告确认营收同比增长超过20%,就买入两成仓位。 | C | C | — |
| C_trigger_price_level | 若股价回落到80元以下,我将买入三成仓位。 | C | C | — |
| C_vague_condition | 如果市场情绪好转,可以考虑适当参与一些。 | C | undetermined | 是 |

## 逐条理由(新 prompt)
- A_size_small_now 新=A: 建议现在买入，不需要等任何消息
  / 旧=C: 建议现在买入,仓位两成
- A_size_full_now 新=A: 我判断现在就该全仓买入。
  / 旧=A: 我判断现在就该全仓买入。
- B_evidence_gate_only 新=B: 等正式公告出来再说,现在仓位不动。
  / 旧=B: 现在仓位不动
- B_wait_and_avoid 新=B: 公司前景正面,但目前信息不足,建议先观望。
  / 旧=B: 公司前景正面,但目前信息不足,建议先观望。
- B_exit 新=B: 建议清仓离场,不参与这只票
  / 旧=B: 建议清仓离场,不参与这只票。
- C_trigger_plus_commit 新=C: 如果公告确认营收同比增长超过20%,就买入两成仓位
  / 旧=C: 如果公告确认营收同比增长超过20%,就买入两成仓位
- C_trigger_price_level 新=C: 若股价回落到80元以下,我将买入三成仓位
  / 旧=C: 若股价回落到80元以下,我将买入三成仓位
- C_vague_condition 新=undetermined: 如果市场情绪好转,可以考虑适当参与一些。
  / 旧=C: 如果市场情绪好转,可以考虑适当参与一些
