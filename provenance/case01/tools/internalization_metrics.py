# -*- coding: utf-8 -*-
"""内化定量分析:干预前后价值倾向位移 vs 对照维度(IVD 的定量证据)。

核心问题:专家调整治理约束(governance)后,Agent 的内化价值倾向(value_tendency)
是否**朝新目标移动**,且移动幅度大于未被干预触及的维度(对照)?

方法(2026-09-24 与用户商定,先做小样本):
- 对每条干预记录 (agent, sim_time, old→new constraints):
  - before = 该时刻之前最后一个 checkpoint 的 value_tendency;
  - after  = 时间窗 (T, T+window_min] 内最后一个 checkpoint 的 value_tendency;
  - 干预维度集 S = new_constraints 的维度,目标取 new;对照维度 = 其余维度,目标取 old;
  - 每维位移朝向量 = gap_before - gap_after(gap = |tendency - target|;
    正值 = 朝目标移动);
  - 主统计量:mean_displacement(S) − mean_displacement(control),差值 > 0 且
    S 侧均值为正,才支持"干预被内化"。
- 输出 JSON + Markdown 到 results/analysis/<sim>/internalization.{json,md}。

纯逻辑可单测:checkpoint 解析与统计分离,不依赖真实数据。
"""
import glob
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

CK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "..", "results", "checkpoints")

DEFAULT_WINDOW_MIN = 60.0


# ---------------------------------------------------------------------------
# checkpoint 解析(纯函数)
# ---------------------------------------------------------------------------

def load_trajectory(ck_dir: str, agent: str) -> List[dict]:
    """读一个模拟的 checkpoint 序列,返回 [{sim_time, step, tendency:{dim:w}}](按时间升序)。"""
    out = []
    for p in sorted(glob.glob(os.path.join(ck_dir, "simulate-*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue          # 坏快照跳过,不中断整条轨迹
        st = ((d.get("agents") or {}).get(agent) or {}).get("status") or {}
        vt = st.get("value_tendency")
        if not isinstance(vt, dict) or not vt:
            continue
        out.append({"sim_time": str(d.get("time") or ""),
                    "step": d.get("step"),
                    "tendency": {str(k): float(v) for k, v in vt.items()
                                 if isinstance(v, (int, float))}})
    out.sort(key=lambda x: x["sim_time"])
    return out


def _parse_t(s: str) -> Optional[float]:
    """'20250213-12:02' → 绝对分钟(日期×1440 + 当日分钟),跨午夜单调;解析失败 None。"""
    try:
        date, hm = s.split("-")
        h, m = hm.split(":")
        return int(date) * 1440 + int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def snapshot_at(traj: List[dict], t: str, before: bool = True) -> Optional[dict]:
    """sim_time=t 之前(before=True,取**最近的过去**)/之后(最早的未来)的快照;无则 None。"""
    tv = _parse_t(t)
    if tv is None:
        return None
    cands = []
    for x in traj:
        xv = _parse_t(x["sim_time"])
        if xv is None:
            continue
        if (xv <= tv) if before else (xv > tv):
            cands.append((xv, x))
    if not cands:
        return None
    return (max if before else min)(cands, key=lambda p: p[0])[1]


def snapshots_in_window(traj: List[dict], t: str, window_min: float) -> List[dict]:
    tv = _parse_t(t)
    if tv is None:
        return []
    return [x for x in traj
            if (v := _parse_t(x["sim_time"])) is not None and tv < v <= tv + window_min]


# ---------------------------------------------------------------------------
# 位移统计(纯函数,可单测)
# ---------------------------------------------------------------------------

def displacement(tendency_before: Dict[str, float], tendency_after: Dict[str, float],
                 target: Dict[str, float]) -> Dict[str, float]:
    """每维 gap_before − gap_after;>0 = 朝 target 移动。只统计三方都有的维度。"""
    out = {}
    for d, tgt in target.items():
        if d in tendency_before and d in tendency_after:
            out[d] = (abs(tendency_before[d] - tgt)
                      - abs(tendency_after[d] - tgt))
    return out


def analyse_intervention(traj: List[dict], intervention: dict,
                         window_min: float = DEFAULT_WINDOW_MIN) -> Optional[dict]:
    """单条干预的内化位移分析;轨迹不足(before/after 任一缺失)返回 None。"""
    t = intervention.get("sim_time", "")
    before_s = snapshot_at(traj, t, before=True)
    if before_s is None:
        return None
    window = snapshots_in_window(traj, t, window_min)
    after_s = window[-1] if window else before_s   # 窗内无快照 = 干预后无观测,位移记 0
    tb, ta = before_s["tendency"], after_s["tendency"]

    new_c = {str(k): float(v) for k, v in (intervention.get("new_constraints") or {}).items()
             if isinstance(v, (int, float))}
    old_c = {str(k): float(v) for k, v in (intervention.get("old_constraints") or {}).items()
             if isinstance(v, (int, float))}
    treated = {d: new_c[d] for d in new_c if d in tb and d in ta}
    control = {d: old_c[d] for d in old_c if d not in new_c and d in tb and d in ta}
    if not treated and not control:
        return None

    d_tr = displacement(tb, ta, treated)
    d_ct = displacement(tb, ta, control)
    mean_tr = sum(d_tr.values()) / len(d_tr) if d_tr else 0.0
    mean_ct = sum(d_ct.values()) / len(d_ct) if d_ct else 0.0

    def _tv(v: Dict[str, float], tgt: Dict[str, float]) -> float:
        common = [d for d in tgt if d in v]
        return sum(abs(v[d] - tgt[d]) for d in common) / (2 * len(common)) if common else None

    return {
        "agent": intervention.get("agent", ""),
        "sim_time": t,
        "window_min": window_min,
        "note": intervention.get("note", ""),
        "before_step": before_s.get("step"), "after_step": after_s.get("step"),
        "treated_dims": sorted(treated),
        "control_dims": sorted(control),
        "displacement_treated": {k: round(v, 4) for k, v in d_tr.items()},
        "displacement_control": {k: round(v, 4) for k, v in d_ct.items()},
        "mean_displacement_treated": round(mean_tr, 4),
        "mean_displacement_control": round(mean_ct, 4),
        "internalization_gap": round(mean_tr - mean_ct, 4),
        "tv_to_new_before": round(_tv(tb, new_c), 4) if new_c and _tv(tb, new_c) is not None else None,
        "tv_to_new_after": round(_tv(ta, new_c), 4) if new_c and _tv(ta, new_c) is not None else None,
    }


def analyse_simulation(ck_dir: str, interventions: List[dict],
                       window_min: float = DEFAULT_WINDOW_MIN) -> dict:
    """一个模拟的内化分析汇总。"""
    by_agent: Dict[str, List[dict]] = {}
    for iv in interventions:
        by_agent.setdefault(iv.get("agent", ""), []).append(iv)
    rows, skipped = [], []
    for agent, ivs in sorted(by_agent.items()):
        traj = load_trajectory(ck_dir, agent)
        if not traj:
            skipped.append({"agent": agent, "reason": "无含 value_tendency 的 checkpoint"})
            continue
        for iv in ivs:
            r = analyse_intervention(traj, iv, window_min=window_min)
            (rows if r else skipped).append(r or {"agent": agent,
                                                  "sim_time": iv.get("sim_time", ""),
                                                  "reason": "干预时刻无前置快照"})
    treated = [r for r in rows if "internalization_gap" in r]
    summary = {
        "n_interventions_analysed": len(treated),
        "n_skipped": len(skipped),
        "mean_displacement_treated": round(
            sum(r["mean_displacement_treated"] for r in treated) / len(treated), 4) if treated else None,
        "mean_displacement_control": round(
            sum(r["mean_displacement_control"] for r in treated) / len(treated), 4) if treated else None,
        "internalization_gap": round(
            sum(r["internalization_gap"] for r in treated) / len(treated), 4) if treated else None,
        "n_positive_gap": sum(1 for r in treated if r["internalization_gap"] > 0),
    }
    return {"summary": summary, "interventions": rows, "skipped": skipped}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _md(result: dict, sim: str) -> str:
    s = result["summary"]
    lines = ["# 内化定量分析 — {}".format(sim), "",
             "| 指标 | 值 |", "|---|---|",
             "| 分析的干预数 | {} |".format(s["n_interventions_analysed"]),
             "| 干预维度平均朝目标位移 | {} |".format(s["mean_displacement_treated"]),
             "| 对照维度平均位移 | {} |".format(s["mean_displacement_control"]),
             "| 内化差值(干预 − 对照) | **{}** |".format(s["internalization_gap"]),
             "| 差值为正的干预数 | {} / {} |".format(s["n_positive_gap"], s["n_interventions_analysed"]),
             "", "| agent | sim_time | 干预维度位移均值 | 对照位移均值 | 差值 |",
             "|---|---|---|---|---|"]
    for r in result["interventions"]:
        if "internalization_gap" not in r:
            continue
        lines.append("| {} | {} | {} | {} | {} |".format(
            r["agent"], r["sim_time"], r["mean_displacement_treated"],
            r["mean_displacement_control"], r["internalization_gap"]))
    if result["skipped"]:
        lines += ["", "跳过:"]
        lines += ["- {} @ {} : {}".format(x.get("agent", ""), x.get("sim_time", ""),
                                          x.get("reason", "")) for x in result["skipped"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="内化定量分析(干预前后位移 vs 对照维度)")
    ap.add_argument("--checkpoints", default="", help="results/checkpoints 下的模拟目录名或绝对路径")
    ap.add_argument("--interventions", default=os.path.join(CK_DIR, "interventions.json"))
    ap.add_argument("--window", type=float, default=DEFAULT_WINDOW_MIN,
                    help="干预后观测窗(分钟,按 sim_time)")
    ap.add_argument("--all", action="store_true", help="跑有干预记录的全部模拟")
    ap.add_argument("--out-root", default=os.path.join(CK_DIR, "..", "analysis"))
    args = ap.parse_args()

    with open(args.interventions, encoding="utf-8") as f:
        iv_all = json.load(f)
    by_sim: Dict[str, List[dict]] = {}
    for x in iv_all:
        if x.get("simulation"):
            by_sim.setdefault(x["simulation"], []).append(x)

    sims = sorted(by_sim) if args.all else ([args.checkpoints] if args.checkpoints else [])
    if not sims:
        print("无模拟可选:--all 或 --checkpoints <目录名>;有干预记录的模拟: {}".format(sorted(by_sim)))
        return 2
    ok = 0
    for sim in sims:
        ck = sim if os.path.isdir(sim) else os.path.join(CK_DIR, sim)
        if not os.path.isdir(ck):
            print("[!] checkpoint 目录不存在: {}".format(ck))
            continue
        result = analyse_simulation(ck, by_sim[sim], window_min=args.window)
        out_dir = os.path.join(args.out_root, sim)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "internalization.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        with open(os.path.join(out_dir, "internalization.md"), "w", encoding="utf-8") as f:
            f.write(_md(result, sim))
        print("[{}] 分析 {} 条干预,内化差值 {}".format(
            sim, result["summary"]["n_interventions_analysed"],
            result["summary"]["internalization_gap"]))
        print("    -> {}\\internalization.{{json,md}}".format(out_dir))
        ok += 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
