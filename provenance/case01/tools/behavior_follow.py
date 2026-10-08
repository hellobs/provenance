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
- **底噪对照**(2026-10-05 归入代码,此前只活在文档里、无法复算):
  在没有干预发生的时段上,取同宽度的相邻两窗,用**同一对齐口径**算一个"位移",
  得到行为对齐度的自然波动分布 —— 它是"观察位移是否可区分于噪声"的参照系;
- 主统计量:跨全部干预/维度的 **Δweight 与 Δalignment 的相关**(Spearman),
  以及升维组/降维组的平均位移 —— 双向选择性(升的↑降的↓)才支持"行为追随治理";
  判读时必须与同一批数据的 `noise_floor` 对照。

诚实边界:
- goal_alignment 是 mavis 自带的语义打分器,与倾向更新共用同一 embedding 模型 ——
  这是"同一把尺子量两处",不是独立裁判;结论定位为"行为与内化状态同向",
  不宣称"独立验证";
- 行动文本是"计划做什么",不是实际对话;对话层证据留给 M4 专家链路;
- 底噪对照只**归一化参照系**,不是零假设检验:对照窗的维度集与干预窗不同,
  样本量也小(见 `noise_floor.n_windows`),只用于"是不是同一量级"的判断,
  不产出 p 值。
"""
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

CK = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
# 与 tools/deep_check.py:113 同一自救写法:脚本形态(`python case01/tools/behavior_follow.py`)
# 的 sys.path[0] 是脚本自己那层,而 case_engine 没装进任何 venv ⇒ 不补这句就 import 不到解析口。
# 此前这里用 try/except 兜老根,于是换个敲法就换一套数:老 `results/checkpoints` 现在只剩
# 一个空壳 `stage/`(0 文件),真存档在 `data/case00/state`(41 条)与 `data/ledgers`(4 条),
# 脚本形态会安静地出一份"0 条样本"的报表(2026-10-08 晚实测)。解析不到就当场抛。
if CK not in sys.path:
    sys.path.insert(0, CK)
from case_engine.paths import data_root as _data_root   # noqa: E402
CHECKPOINTS = _data_root("case00.state", env_var="CASE00_CHECKPOINTS_ROOT")
TABLEDGER = _data_root("ledgers")                        # 跨局登记簿单独一层
# 分析产物根(报表,入库):与存档不在同一层,用包根显式拼,不要 `..`。
ANALYSIS_ROOT = os.path.join(CK, "results", "analysis")

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


def _probe_ollama(base_url: str = "http://127.0.0.1:11434",
                  timeout: float = 2.0) -> bool:
    """Ollama 可达性预检(2 秒 socket):死后端不再空转 GoalScorer 的重试策略
    (embed 失败不进缓存,30 条文本 × 3 重试 × 60s 超时 = 分钟级,实测 616s)。"""
    import socket
    from urllib.parse import urlparse
    try:
        u = urlparse(base_url)
        host = u.hostname or "127.0.0.1"
        port = u.port or 80
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


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
        return {"agent": intervention.get("agent", ""), "sim_time": t,
                "skip_reason": "窗口内无行动文本"}

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
    warnings: List[str] = []
    scorer_failed = False
    if dropped:
        dims = sorted(dropped)
        b_txt = [e["action"] for e in _sample(before, sample)]
        a_txt = [e["action"] for e in _sample(after, sample)]
        if scorer is None and not _probe_ollama():
            # 死后端预检:不进 GoalScorer 的重试循环(embed 失败不进缓存,
            # 全部文本逐个空转 = 分钟级,2026-09-25 实测 616s)
            scorer_failed = True
            warnings.append("Ollama 不可达,降维维度({})的离线补算跳过:"
                            "本条结果只含升维维度,不完整".format("+".join(dims)))
        else:
            a_b = _offline_alignment(b_txt, dims, scorer)
            a_a = _offline_alignment(a_txt, dims, scorer)
            if a_b is not None and a_a is not None:
                rows.append({"kind": "dropped", "dims": dims,
                             "dw": round(sum(-dropped[d] for d in dims), 4),
                             "align_before": round(a_b, 4),
                             "align_after": round(a_a, 4),
                             "shift": round(a_a - a_b, 4)})
            else:
                scorer_failed = True   # 打分器失败(embed 不可得),不是"没有行动"
                warnings.append("离线打分器失败,降维维度({})的补算跳过:结果不完整"
                                .format("+".join(dims)))
    if not rows:
        return {"agent": intervention.get("agent", ""), "sim_time": t,
                "skip_reason": ("对齐度不可得:离线打分器失败(检查 Ollama/embedding)"
                                if scorer_failed else "窗口内无可用的对齐记录"),
                "warnings": warnings}
    result = {"agent": intervention.get("agent", ""), "sim_time": t,
              "note": intervention.get("note", ""), "dims": rows}
    if warnings:
        result["warnings"] = warnings    # 部分结果必须亮牌,不静默
    return result


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


# ---------------------------------------------------------------------------
# 底噪对照(2026-10-05 入库:此前 0.0187 等数字是手工算的,无法复算)
# ---------------------------------------------------------------------------

DEFAULT_NOISE_WINDOWS = 20   # 目标对照窗数(与 2026-09-25 手工口径一致)


def _interval_of(events: List[dict]) -> Optional[Tuple[float, float]]:
    """事件时间跨度 [min, max](绝对分钟);不足两点返回 None。"""
    ts = [v for v in (_abs_min(e.get("time", "")) for e in events) if v is not None]
    return (min(ts), max(ts)) if len(ts) >= 2 else None


def _reference_windows(events: List[dict], interventions: List[dict],
                       window_min: float, margin_min: float) -> List[float]:
    """挑出**远离全部干预**的窗心时刻(等间隔),返回绝对分钟列表。

    规则:窗心 c 的两侧窗 [c-w, c) / (c, c+w] 必须落在事件跨度内,且与任一干预
    时刻的距离 > w + margin —— 保证对照窗既不含干预,也不贴着干预的余波。
    """
    span = _interval_of(events)
    if span is None:
        return []
    lo, hi = span
    t0, t1 = lo + window_min, hi - window_min          # 窗心可取范围
    if t1 <= t0:
        return []
    blocked = [v for v in (_abs_min(iv.get("sim_time", "")) for iv in interventions)
               if v is not None]
    guard = window_min + margin_min

    def _clear(c: float) -> bool:
        return all(abs(c - b) > guard for b in blocked)

    # 先按固定步长扫,再在可行区间里等间隔取,尽量凑到目标窗数
    step = max(1, int(window_min))
    cands = [c for c in range(int(t0), int(t1) + 1, step) if _clear(float(c))]
    if not cands and t1 - t0 <= 4 * step:
        # 步长太粗导致全被挡:仅在跨度不大时逐分钟回退(避免长轨迹上的百万级循环)
        cands = [c for c in range(int(t0), int(t1) + 1) if _clear(float(c))]
    if not cands:
        return []
    if len(cands) <= DEFAULT_NOISE_WINDOWS:
        return [float(c) for c in cands]
    step = len(cands) / DEFAULT_NOISE_WINDOWS
    return [float(cands[int(i * step)]) for i in range(DEFAULT_NOISE_WINDOWS)]


def _noise_shift(events: List[dict], dims: List[str], c: float,
                 window_min: float, sample: int, scorer) -> Optional[float]:
    """在窗心 c 处按同一口径算一次"伪位移":对齐度 after − before。"""
    before, after = split_window(events, _min_to_time(c), window_min)
    if not before or not after:
        return None
    b_txt = [e.get("action", "") for e in _sample(before, sample)]
    a_txt = [e.get("action", "") for e in _sample(after, sample)]
    # 对照窗不预设升/降维,统一用离线打分器(与降维分支同口径),
    # 避免"对照用记录值、干预用离线值"的口径混用
    a_b = _offline_alignment(b_txt, dims, scorer)
    a_a = _offline_alignment(a_txt, dims, scorer)
    if a_b is None or a_a is None:
        return None
    return round(a_a - a_b, 4)


def _min_to_time(v: float) -> str:
    """绝对分钟 → 'YYYYMMDD-HH:MM'(注意内部编码是 日期*1440+分钟)。"""
    date, rem = divmod(int(v), 1440)
    h, m = divmod(rem, 60)
    return "{:08d}-{:02d}:{:02d}".format(date, h, m)


def noise_floor(events: List[dict], interventions: List[dict], dims: List[str],
                window_min: float = DEFAULT_WINDOW_MIN,
                sample: int = DEFAULT_SAMPLE, scorer=None,
                margin_min: float = 0.0) -> dict:
    """无干预时段的行为对齐度自然波动(底噪带)。

    与 `analyse_intervention` 用**同一对齐口径**、同一窗宽,唯一区别是窗心
    远离所有干预时刻。返回分布统计量,供"观察位移是否可区分于噪声"对照。
    """
    if not dims:
        return {"n_windows": 0, "note": "无可对照维度"}
    if scorer is None and not _probe_ollama():
        return {"n_windows": 0,
                "note": "Ollama 不可达,底噪对照跳过(检查本地 embedding 服务)"}
    centers = _reference_windows(events, interventions, window_min, margin_min)
    if not centers:
        return {"n_windows": 0,
                "note": "无满足条件的对照窗(事件跨度不足或全被干预遮挡)"}
    shifts = []
    for c in centers:
        v = _noise_shift(events, dims, c, window_min, sample, scorer)
        if v is not None:
            shifts.append(v)
    if not shifts:
        return {"n_windows": 0, "note": "对照窗内无可评判的行动文本"}
    absv = sorted(abs(x) for x in shifts)
    signed = sorted(shifts)
    n = len(absv)
    return {
        "n_windows": n,
        "window_min": window_min,
        "median_signed": round(signed[n // 2], 4),
        "median_abs": round(absv[n // 2], 4),
        "p75_abs": round(absv[min(n - 1, int(n * 0.75))], 4),
        "max_abs": round(absv[-1], 4),
        "dims": sorted(dims),
    }


def compare_to_noise(observed_shift: float, nf: dict) -> Optional[str]:
    """把一个观察位移放到底噪带里判读:'in_band' / 'above_band'。"""
    if not nf or not nf.get("n_windows"):
        return None
    return "above_band" if abs(observed_shift) > nf["max_abs"] else "in_band"


def analyse_simulation(decisions_path: str, interventions: List[dict],
                       window_min: float = DEFAULT_WINDOW_MIN,
                       sample: int = DEFAULT_SAMPLE, scorer=None,
                       with_noise_floor: bool = True) -> dict:
    """一个模拟的行为追随汇总(含底噪对照)。"""
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
            # analyse_intervention 用带 skip_reason 的字典表示"没分析成",
            # 它与真正的结果行**不同构**(无 dims)。此前不分流会让下游
            # 拿 r["dims"] 直接 KeyError(2026-10-05 demo 上暴露)。
            if r and "dims" in r:
                rows.append(r)
            elif r:
                skipped.append({"agent": agent, "sim_time": iv.get("sim_time", ""),
                                "reason": r.get("skip_reason", "未分析"),
                                **({"warnings": r["warnings"]} if r.get("warnings") else {})})
            else:
                skipped.append({"agent": agent, "sim_time": iv.get("sim_time", ""),
                                "reason": "约束无变化(无升维也无降维)"})
    pairs = [(d["dw"], d["shift"]) for row in rows for d in row["dims"]]
    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    raised = [(d["shift"], row["agent"]) for row in rows for d in row["dims"]
              if d["kind"] == "raised"]
    dropped = [(d["shift"], row["agent"]) for row in rows for d in row["dims"]
               if d["kind"] == "dropped"]

    def _mean(v):
        return round(sum(v) / len(v), 4) if v else None

    summary = {
        "n_interventions": len(rows),
        "n_dim_pairs": len(pairs),
        "spearman_dw_vs_shift": _spearman(xs, ys),
        "mean_shift_raised": _mean([v for v, _ in raised]),
        "mean_shift_dropped": _mean([v for v, _ in dropped]),
        "n_raised_positive": sum(1 for v, _ in raised if v > 0),
        "n_raised": len(raised),
        "n_dropped_negative": sum(1 for v, _ in dropped if v < 0),
        "n_dropped": len(dropped),
        "n_skipped": len(skipped),
    }

    # 底噪对照:按 agent 各算一份(每个 agent 的干预时刻不同),再并出全局带
    noise = {}
    if with_noise_floor:
        for agent in sorted(by_agent):
            events = load_events(decisions_path, agent)
            if not events:
                continue
            dims = sorted({d for row in rows if row["agent"] == agent
                           for dd in row["dims"] for d in dd["dims"]})
            if not dims:
                continue
            nf = noise_floor(events, by_agent[agent], dims,
                             window_min=window_min, sample=sample, scorer=scorer)
            if nf.get("n_windows"):
                noise[agent] = nf
        merged = _merge_noise(noise)
        if merged:
            summary["noise_floor"] = merged
            above = [d for row in rows for d in row["dims"]
                     if compare_to_noise(d["shift"], merged) == "above_band"]
            summary["n_shifts_above_noise_band"] = len(above)
    return {"summary": summary, "interventions": rows, "skipped": skipped,
            "noise_floor_by_agent": noise}


def _merge_noise(by_agent: Dict[str, dict]) -> Optional[dict]:
    """各 agent 的底噪带并成一条(取最宽的 max_abs 与各分位中位),供全局判读。"""
    nfs = [v for v in by_agent.values() if v.get("n_windows")]
    if not nfs:
        return None

    def _med(key):
        vs = sorted(nf[key] for nf in nfs if nf.get(key) is not None)
        return round(vs[len(vs) // 2], 4) if vs else None

    return {
        "n_windows": sum(nf["n_windows"] for nf in nfs),
        "n_agents": len(nfs),
        "median_abs": _med("median_abs"),
        "p75_abs": _med("p75_abs"),
        "max_abs": round(max(nf["max_abs"] for nf in nfs), 4),
        "window_min": nfs[0].get("window_min"),
    }


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
             "| 升维组平均行为位移 | {} ({} {} 为正 / {}) |".format(
                 s["mean_shift_raised"], s["n_raised_positive"], "↑", s["n_raised"]),
             "| 降维组平均行为位移 | {} ({} {} 为负 / {}) |".format(
                 s["mean_shift_dropped"], s["n_dropped_negative"], "↓", s["n_dropped"])]
    nf = s.get("noise_floor")
    if nf:
        lines.append(
            "| **底噪带**(无干预对照窗 \\|Δ\\|) | 中位 {} / P75 {} / 最大 {} "
            "({} 窗 × {} agent) |".format(
                nf["median_abs"], nf["p75_abs"], nf["max_abs"],
                nf["n_windows"], nf["n_agents"]))
        lines.append("| 超出底噪带的观察位移 | {} 个 |".format(
            s.get("n_shifts_above_noise_band", 0)))
    lines += ["", "| agent | sim_time | 类别 | 维度 | Δw | 对齐前→后 | 位移 | 对照底噪 |",
              "|---|---|---|---|---|---|---|---|"]
    for r in result["interventions"]:
        if "dims" not in r:      # skip_reason 行(打分器失败/窗口无行动):如实列出
            lines.append("| {} | {} | 跳过 | {} | — | — | — | — |".format(
                r.get("agent", ""), r.get("sim_time", ""), r.get("skip_reason", "")))
            continue
        for d in r["dims"]:
            verdict = compare_to_noise(d["shift"], nf) if nf else None
            tag = {"above_band": "超带", "in_band": "带内"}.get(verdict, "—")
            lines.append("| {} | {} | {} | {} | {:+.3f} | {:.3f}→{:.3f} | {:+.3f} | {} |".format(
                r["agent"], r["sim_time"], d["kind"], "+".join(d["dims"]),
                d["dw"], d["align_before"], d["align_after"], d["shift"], tag))
    if result["skipped"]:
        lines += ["", "跳过:"] + [
            "- {} @ {} : {}".format(x.get("agent", ""), x.get("sim_time", ""), x.get("reason", ""))
            for x in result["skipped"]]
    if nf:
        lines += ["", "> 底噪带 = 远离全部干预时刻的同宽对照窗上、按**同一对齐口径**算出的",
                  "> |对齐度位移| 分布;它是「是否同一量级」的参照,不是零假设检验,",
                  "> 不产出 p 值。「带内」= |位移| ≤ 底噪最大 |Δ|。"]
    return "\n".join(lines) + "\n"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="行为追随分析(约束调整→行动内容偏移)")
    ap.add_argument("--sims", default="stock-en7,stock-en8,demo-0921-222949")
    # 2026-10-08:登记簿与模拟存档不是同一层了(前者 data/ledgers,后者 data/case00/state)
    ap.add_argument("--interventions", default=os.path.join(TABLEDGER, "interventions.json"))
    ap.add_argument("--window", type=float, default=DEFAULT_WINDOW_MIN)
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    ap.add_argument("--out-root", default=ANALYSIS_ROOT)
    ap.add_argument("--no-noise-floor", action="store_true",
                    help="跳过底噪对照(默认开启;对照窗需本地 embedding)")
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
                                    window_min=args.window, sample=args.sample,
                                    with_noise_floor=not args.no_noise_floor)
        out_dir = os.path.join(args.out_root, sim)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "behavior_follow.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        with open(os.path.join(out_dir, "behavior_follow.md"), "w", encoding="utf-8") as f:
            f.write(_md(result, sim))
        s = result["summary"]
        nf = s.get("noise_floor")
        print("[{}] 干预{} 条, Δw–Δ行为 Spearman={}, 升维 {} {}/{}, 降维 {} {}/{}{}".format(
            sim, s["n_interventions"], s["spearman_dw_vs_shift"],
            s["mean_shift_raised"], s["n_raised_positive"], s["n_raised"],
            s["mean_shift_dropped"], s["n_dropped_negative"], s["n_dropped"],
            ", 底噪中位 {}/P75 {}/最大 {} ({} 窗)".format(
                nf["median_abs"], nf["p75_abs"], nf["max_abs"], nf["n_windows"])
            if nf else ", 底噪未算"))
        ok += 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
