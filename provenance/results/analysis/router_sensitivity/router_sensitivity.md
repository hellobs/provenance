# Router 敏感性 — 同一反思 × 双模型

| 反思来源 | 本地条数 | 外部条数 | 跨模型重合 | 本地风险分布 | 外部风险分布 |
|---|---|---|---|---|---|
| 260924-live-case01-mavis-C-1630 | [4] | [5] | 0.2667 | {"high": 2, "medium": 2} | {"medium": 3, "low": 2} |
| 260924-live-case01-mavis-C-1609 | [3] | [3] | 0.1557 | {"high": 1, "medium": 2} | {"medium": 2, "low": 1} |
| 260922-live-case01-mavis-auto-1820 | [4] | [6] | 0.1175 | {"high": 1, "medium": 3} | {"high": 2, "medium": 3, "low": 1} |
| 260922-live-case01-mavis-auto-0950 | [5] | [None] | 0.0 | {"high": 3, "medium": 2} | {} |
| 260922-live-case01-mavis-A-0958 | [5] | [None] | 0.0 | {"high": 3, "medium": 2} | {} |

> 重合度 = 双方 issue summary 的字符 bigram Jaccard 最大匹配均值,粗粒度词汇重合;原始输出在同名 JSON 里,可人工复核。
