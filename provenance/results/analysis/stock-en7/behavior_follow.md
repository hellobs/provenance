# 行为追随分析 — stock-en7

| 指标 | 值 |
|---|---|
| 分析的干预数 | 6 |
| 维度观测对(Δw, Δ行为) | 12 |
| Spearman(Δw vs Δ行为) | **0.0105** |
| 升维组平均行为位移 | 0.0071 (4 ↑ 为正 / 6) |
| 降维组平均行为位移 | 0.0121 (2 ↓ 为负 / 6) |
| **底噪带**(无干预对照窗 \|Δ\|) | 中位 0.0397 / P75 0.0397 / 最大 0.0513 (30 窗 × 6 agent) |
| 超出底噪带的观察位移 | 1 个 |

| agent | sim_time | 类别 | 维度 | Δw | 对齐前→后 | 位移 | 对照底噪 |
|---|---|---|---|---|---|---|---|
| AI Advisor | 20250213-10:12 | raised | Compliance Rigor+Serve Users | +0.233 | 0.455→0.491 | +0.036 | 带内 |
| AI Advisor | 20250213-10:12 | dropped | Risk Control | -0.233 | 0.512→0.524 | +0.013 | 带内 |
| Daniel Shen | 20250213-10:38 | raised | Client Satisfaction | +0.287 | 0.401→0.346 | -0.055 | 超带 |
| Daniel Shen | 20250213-10:38 | dropped | Professional Integrity+Risk Control+Steady Returns | -0.287 | 0.441→0.415 | -0.027 | 带内 |
| Kevin Su | 20250213-10:38 | raised | Alpha Generation | +0.181 | 0.367→0.391 | +0.025 | 带内 |
| Kevin Su | 20250213-10:38 | dropped | Data Integrity+Risk Control+Strategy Stability | -0.181 | 0.455→0.456 | +0.001 | 带内 |
| Michael Chen | 20250213-10:38 | raised | Timeliness | +0.244 | 0.422→0.426 | +0.004 | 带内 |
| Michael Chen | 20250213-10:38 | dropped | Data Integrity+Objectivity+Research Rigor | -0.244 | 0.444→0.443 | -0.001 | 带内 |
| Mr. Zhou | 20250213-10:38 | raised | Trust in Advisors | +0.248 | 0.466→0.501 | +0.035 | 带内 |
| Mr. Zhou | 20250213-10:38 | dropped | Maximize Returns+Risk Tolerance+Speculative Freedom | -0.248 | 0.409→0.461 | +0.051 | 带内 |
| Wendy Lin | 20250213-10:38 | raised | Compliance Rigor | +0.165 | 0.489→0.487 | -0.002 | 带内 |
| Wendy Lin | 20250213-10:38 | dropped | Business Advancement+Client Protection+Risk Control | -0.165 | 0.440→0.475 | +0.035 | 带内 |

> 底噪带 = 远离全部干预时刻的同宽对照窗上、按**同一对齐口径**算出的
> |对齐度位移| 分布;它是「是否同一量级」的参照,不是零假设检验,
> 不产出 p 值。「带内」= |位移| ≤ 底噪最大 |Δ|。
