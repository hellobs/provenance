# -*- coding: utf-8 -*-
"""行为追随分析:治理约束调整后,Agent 的**行动内容**是否朝新价值方向偏移?

这是"内化"证据链的关键一环:倾向权重位移(见 internalization_metrics)只是内部
状态的代理指标;权重动了之后,**行为是否真的变**,才决定"内化"这个词能否成立。

方法(2026-09-24 与用户商定):
- 每条干预把约束维度分为**升维**(new_w > old_w)与**降维**(new_w < old_w 或被移除);
- 行为度量 = 行动文本对维度的语义对齐度(GoalScorer:cosine(embed(action),
  embed(维度名)),**独立于权重**,见 agent_core.goal_alignment docstring);
- 升维维度:干预前后都在约束里,直接用决策事件里已记录的 goal_alignment;
- 降维维度:干预后从约束里消失,记录里没有 —— 用同一 GoalScorer 对前后窗口的
  行动文本**离线补算**(本地 Ollama embedding,抽样封顶控成本);
- 主统计量:跨全部干预/维度的 **Δweight 与 Δalignment 的相关**(Spearman),
  以及升维组/降维组的平均位移 —— 双向选择性(升的↑降的↓)才支持"行为追随治理"。

诚实边界:
- goal_alignment 是 mavis 自带的语义打分器,与倾向更新共用同一 embedding 模型 ——
  这是"同一把尺子量两处",不是独立裁判;结论定位为"行为与内化状态同向",
  不宣称"独立验证";
- 行动文本是"计划做什么",不是实际对话;对话层证据留给 M4 专家链路。
"""
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

CK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
CHECKPOINTS = os.path.join(CK, "results", "checkpoints")

DEFAULT_WINDOW_MIN = 120.0
DEFAULT_SAMPLE = 30          # 离线补算时每侧最多抽多少条行动文本
SCORER_MODEL = "qwen3-embedding:0.6b-q8_0"


# ---------------------------------------------------------------------------
# 数据装载(纯函数)
# ---------------------------------------------------------------------------

def load_events(decisions_path: str, agent: str) -> List[dict]:
    """取某 agent 的决策事件(按时间升序),只留有行动文本的。"""
    with open(decisions_path, encoding="utf-8") as f:
        d = json.load(f)
    ev = [e for e in d.get("events", []) if e.get("agent") == agent
          and (e.get("action") or "").strip()]
    ev.sort(key=lambda e: e.get("time", ""))
    return ev


def split_window(events: List[dict], t: str, window_min: float) -> Tuple[List[dict], List[dict]]:
    """事件按 [T-w, T) 与 (T, T+w] 切两窗(绝对分钟,跨午夜单调)。"""
    t0 = _abs_min(t)
    if t0 is None:
        return [], []
    before, after = [], []
    for e in events:
        v = _abs_min(e.get("time", ""))
        if v is None:
            continue
        if t0 - window_min <= v < t0:
            before.append(e)
        elif t0 < v <= t0 + window_min:
            after.append(e)
    return before, after


def _abs_min(s: str) -> Optional[float]:
    try:
        date, hm = s.split("-")
        h, m = hm.split(":")
        return int(date) * 1440 + int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _sample(events: List[dict], cap: int) -> List[dict]:
    """窗口内均匀抽样(cap=0 表示全取),确定性(等间隔)。"""
    if cap <= 0 or len(events) <= cap:
        return list(events)
    step = len(events) / cap
    return [events[int(i * step)] for i in range(cap)]


def _weight_deltas(old_c: Dict[str, float], new_c: Dict[str, float]) -> Tuple[Dict[str, float], Dict[str, float]]:
    """(升维 {d: new_w}, 降维 {d: old_w - new_w_implicit(0)})。"""
    raised, dropped = {}, {}
    for d, nw in new_c.items():
        ow = old_c.get(d, 0.0)
        if nw > ow:
            raised[d] = nw
    for d, ow in old_c.items():
        nw = new_c.get(d, 0.0)
        if nw < ow:
            dropped[d] = ow - nw
    return raised, dropped


# ---------------------------------------------------------------------------
# 对齐度量
# ---------------------------------------------------------------------------

def _recorded_alignment(events: List[dict], dims: List[str]) -> Optional[float]:
    """事件里已记录的 goal_alignment 对若干维度的均值;无数据返回 None。"""
    vals = []
    for e in events:
        ga = e.get("goal_alignment") or {}
        for d in dims:
            v = ga.get(d)
            if isinstance(v, (int, float)):
                vals.append(float(v))
    return sum(vals) / len(vals) if vals else None


def _offline_alignment(actions: List[str], dims: List[str],
                       scorer=None) -> Optional[float]:
    """离线补算:同一 GoalScorer 对行动文本 × 维度名的 cosine 均值。"""
    if not actions or not dims:
        return None
    if scorer is None:
        from mavisframework.runtime.goal_scorer import GoalScorer
        scorer = GoalScorer(model=SCORER_MODEL)
    vals = []
    for a in actions:
        for d in dims:
            v = scorer.alignment(a, {d: 1.0}).get(d)
            if v is not None:
                vals.append(v)
    return sum(vals) / len(vals) if vals else None


def analyse_intervention(events: List[dict], intervention: dict,
                         window_min: float = DEFAULT_WINDOW_MIN,
                         sample: int = DEFAULT_SAMPLE,
                         scorer=None) -> Optional[dict]:
    """单条干预的行为追随分析;任一侧窗口无行动返回 None。"""
    t = intervention.get("sim_time", "")
    old_c = {str(k): float(v) for k, v in (intervention.get("old_constraints") or {}).items()
             if isinstance(v, (int, float))}
    new_c = {str(k): float(v) for k, v in (intervention.get("new_constraints") or {}).items()
             if isinstance(v, (int, float))}
    raised, dropped = _weight_deltas(old_c, new_c)
    if not raised and not dropped:
        return None
    before, after = split_window(events, t, window_min)
    if not before or not after:
        return None

    rows = []
    # 升维:记录值(前后都在约束里,打分口径一致)
    if raised:
        dims = sorted(raised)
        a_b = _recorded_alignment(before, dims)
        a_a = _recorded_alignment(after, dims)
        if a_b is not None and a_a is not None:
            rows.append({"kind": "raised", "dims": dims,
                         "dw": round(sum(new_c[d] - old_c.get(d, 0.0) for d in dims), 4),
                         "align_before": round(a_b, 4), "align_after": round(a_a, 4),
                         "shift": round(a_a - a_b, 4)})
    # 降维:干预后不在约束里,离线补算(两侧同口径离线,避免记录/补算口径混用)
    if dropped:
        dims = sorted(dropped)
        b_txt = [e["action"] for e in _sample(before, sample)]
        a_txt = [e["action"] for e in _sample(after, sample)]
        a_b = _offline_alignment(b_txt, dims, scorer)
        a_a = _offline_alignment(a_txt, dims, scorer)
        if a_b is not None and a_a is not None:
            rows.append({"kind": "dropped", "dims": dims,
                         "dw": round(sum(-dropped[d] for d in dims), 4),
                         "align_before": round(a_b, 4), "align_after": round(a_a, 4),
                         "shift": round(a_a - a_b, 4)})
    if not rows:
        return None
    return {"agent": intervention.get("agent", ""), "sim_time": t,
            "note": intervention.get("note", ""), "dims": rows}


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    """Spearman 秩相关(并列取平均秩);n<3 返回 None。"""
    n = len(xs)
    if n < 3:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 4) if den else None


def analyse_simulation(decisions_path: str, interventions: List[dict],
                       window_min: float = DEFAULT_WINDOW_MIN,
                       sample: int = DEFAULT_SAMPLE, scorer=None) -> dict:
    """一个模拟的行为追随汇总。"""
    by_agent: Dict[str, List[dict]] = {}
    for iv in interventions:
        by_agent.setdefault(iv.get("agent", ""), []).append(iv)
    rows, skipped = [], []
    for agent, ivs in sorted(by_agent.items()):
        events = load_events(decisions_path, agent)
        if not events:
            skipped.append({"agent": agent, "reason": "无决策事件"})
            continue
        for iv in ivs:
            r = analyse_intervention(events, iv, window_min=window_min,
                                     sample=sample, scorer=scorer)
            if r:
                rows.append(r)
            else:
                skipped.append({"agent": agent, "sim_time": iv.get("sim_time", ""),
                                "reason": "窗口内无行动文本"})
    pairs = [(d["dw"], d["shift"]) for row in rows for d in row["dims"]]
    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    raised = [d["shift"] for row in rows for d in row["dims"] if d["kind"] == "raised"]
    dropped = [d["shift"] for row in rows for d in row["dims"] if d["kind"] == "dropped"]

    def _mean(v):
        return round(sum(v) / len(v), 4) if v else None

    summary = {
        "n_interventions": len(rows),
        "n_dim_pairs": len(pairs),
        "spearman_dw_vs_shift": _spearman(xs, ys),
        "mean_shift_raised": _mean(raised),
        "mean_shift_dropped": _mean(dropped),
        "n_raised_positive": sum(1 for v in raised if v > 0),
        "n_raised": len(raised),
        "n_dropped_negative": sum(1 for v in dropped if v < 0),
        "n_dropped": len(dropped),
        "n_skipped": len(skipped),
    }
    return {"summary": summary, "interventions": rows, "skipped": skipped}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _md(result: dict, sim: str) -> str:
    s = result["summary"]
    lines = ["# 行为追随分析 — {}".format(sim), "",
             "| 指标 | 值 |", "|---|---|",
             "| 分析的干预数 | {} |".format(s["n_interventions"]),
             "| 维度观测对(Δw, Δ行为) | {} |".format(s["n_dim_pairs"]),
             "| Spearman(Δw vs Δ行为) | **{}** |".format(s["spearman_dw_vs_shift"]),
             "| 升维组平均行为位移 | {} ({} {}/{} 为正) |".format(
                 s["mean_shift_raised"], s["n_raised_positive"], "↑", s["n_raised"]),
             "| 降维组平均行为位移 | {} ({} {}/{} 为负) |".format(
                 s["mean_shift_dropped"], s["n_dropped_negative"], "↓", s["n_dropped"]),
             "", "| agent | sim_time | 类别 | 维度 | Δw | 对齐前→后 | 位移 |",
             "|---|---|---|---|---|---|---|"]
    for r in result["interventions"]:
        for d in r["dims"]:
            lines.append("| {} | {} | {} | {} | {:+.3f} | {:.3f}→{:.3f} | {:+.3f} |".format(
                r["agent"], r["sim_time"], d["kind"], "+".join(d["dims"]),
                d["dw"], d["align_before"], d["align_after"], d["shift"]))
    if result["skipped"]:
        lines += ["", "跳过:"] + [
            "- {} @ {} : {}".format(x.get("agent", ""), x.get("sim_time", ""), x.get("reason", ""))
            for x in result["skipped"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="行为追随分析(约束调整→行动内容偏移)")
    ap.add_argument("--sims", default="stock-en7,stock-en8,demo-0921-222949")
    ap.add_argument("--interventions", default=os.path.join(CHECKPOINTS, "interventions.json"))
    ap.add_argument("--window", type=float, default=DEFAULT_WINDOW_MIN)
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    ap.add_argument("--out-root", default=os.path.join(CHECKPOINTS, "..", "analysis"))
    args = ap.parse_args()

    with open(args.interventions, encoding="utf-8") as f:
        iv_all = json.load(f)
    by_sim: Dict[str, List[dict]] = {}
    for x in iv_all:
        if x.get("simulation"):
            by_sim.setdefault(x["simulation"], []).append(x)

    ok = 0
    for sim in [s.strip() for s in args.sims.split(",") if s.strip()]:
        dp = os.path.join(CHECKPOINTS, sim, "decisions.json")
        if not os.path.isfile(dp):
            print("[!] 无 decisions.json: {}".format(sim))
            continue
        result = analyse_simulation(dp, by_sim.get(sim, []),
                                    window_min=args.window, sample=args.sample)
        out_dir = os.path.join(args.out_root, sim)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "behavior_follow.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        with open(os.path.join(out_dir, "behavior_follow.md"), "w", encoding="utf-8") as f:
            f.write(_md(result, sim))
        s = result["summary"]
        print("[{}] 干预{} 条, Δw–Δ行为 Spearman={}, 升维 {} {}/{}, 降维 {} {}/{}".format(
            sim, s["n_interventions"], s["spearman_dw_vs_shift"],
            s["mean_shift_raised"], s["n_raised_positive"], s["n_raised"],
            s["mean_shift_dropped"], s["n_dropped_negative"], s["n_dropped"]))
        ok += 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
