"""倾向曲线 PNG 渲染(matplotlib 后端,替代前端 canvas 导出)。"""
import io
import os

from live.state import BASE_DIR, load_tendency_series, log


def render_tendency_png(ckpt_dir: str, agent: str,
                        constraints: dict, my_ivs: list, cur_sim: str):
    """渲染指定角色的价值倾向演变 PNG。

    数据源:checkpoint simulate-*.json 的 value_tendency 序列 + governance.json(约束)
    + interventions.json(干预)。约束按干预时间画分段阶梯虚线。
    返回 PNG 二进制;数据不足时返回 None。
    """
    import datetime as _dt

    if not ckpt_dir or not os.path.isdir(ckpt_dir):
        return None

    series = load_tendency_series(ckpt_dir, agent)
    if not series:
        return None

    # 约束(当前值)与干预(分段阶梯)
    gov_path = os.path.join(BASE_DIR, "governance.json")
    cons = {}
    if os.path.exists(gov_path):
        try:
            cons = __import__("json").load(open(gov_path, encoding="utf-8")).get("roles", {}).get(agent, {})
        except Exception as e:
            log.warning("export-chart 读取 governance.json 失败: {}".format(e))
            cons = {}
    my_ivs = sorted(
        [x for x in my_ivs if x.get("agent") == agent and x.get("simulation") == cur_sim],
        key=lambda x: str(x.get("sim_time", "")),
    )

    # ---- matplotlib 渲染 ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from matplotlib.ticker import FuncFormatter, FixedLocator
        from matplotlib.font_manager import FontProperties
    except Exception as e:
        log.warning("matplotlib 不可用: {}".format(e))
        return None

    try:
        FONT = FontProperties(fname=r"C:\Windows\Fonts\msyh.ttc")
    except Exception:
        FONT = None

    times = [t for t, _ in series]
    t0, t1 = times[0], times[-1]
    goal_names = sorted(cons.keys()) or sorted(series[0][1].keys())
    COLORS = ["#2d6cdf", "#e07b39", "#2f9e44", "#c92a2a", "#9c36b5", "#0b7285"]
    color_of = {g: COLORS[i % len(COLORS)] for i, g in enumerate(goal_names)}

    fig, ax = plt.subplots(figsize=(11, 4.6))
    # 倾向实线
    for g in goal_names:
        ts, vs = [], []
        for t, vt in series:
            if g in vt:
                ts.append(t)
                vs.append(vt[g])
        if ts:
            ax.plot(ts, vs, color=color_of[g], linewidth=2.2, label="{} 倾向".format(g))

    # 约束分段阶梯虚线:起始段=最早图内干预前的 old(干预前的制度期望),
    # 之后每个干预时刻跳到 new;无图内干预时=当前 governance。
    # 注意:interventions.json 全局共享(跨模拟),只取图内时间范围的干预参与阶梯。
    steps = []          # (datetime, constraints)
    in_range_ivs = []   # 图内干预(用于起始约束选择)
    for iv in my_ivs:
        try:
            ivt = _dt.datetime.strptime(str(iv.get("sim_time", "")), "%Y%m%d-%H:%M")
        except (ValueError, TypeError) as e:
            log.warning("export-chart 干预时间非法,跳过(agent={}, sim_time={}): {}".format(
                agent, iv.get("sim_time"), e))
            continue
        steps.append((ivt, dict(iv.get("new_constraints") or cons)))
        if t0 <= ivt <= t1:
            in_range_ivs.append(iv)
    # 起始约束:图内有干预 → 最早图内干预的 old_constraints(干预前);
    # 图内无干预 → 当前 governance(图外旧干预不属于本模拟,不得污染)
    start_cons = dict(cons)
    if in_range_ivs:
        first_iv = in_range_ivs[0]
        oldc = first_iv.get("old_constraints")
        if isinstance(oldc, dict) and oldc:
            oldc_clean = {k: v for k, v in oldc.items() if not str(k)[0].isdigit()}
            start_cons = {
                k: v for k, v in {**cons, **oldc_clean}.items() if k in cons
            }
    for g in goal_names:
        segs = []
        for ivt, newc in steps:
            if g in newc:
                segs.append((ivt, newc[g]))
        prev_t, prev_v = t0, start_cons.get(g, 0)
        for ivt, v in segs:
            if ivt <= t0 or ivt > t1:
                continue
            if ivt > prev_t:
                ax.hlines(prev_v, prev_t, ivt, color=color_of[g], linestyle="--",
                          linewidth=1.4, alpha=0.7)
            prev_t, prev_v = ivt, v
        ax.hlines(prev_v, prev_t, t1, color=color_of[g], linestyle="--", linewidth=1.4, alpha=0.7)
    # 干预竖线
    for iv in my_ivs:
        try:
            ivt = _dt.datetime.strptime(str(iv.get("sim_time", "")), "%Y%m%d-%H:%M")
        except (ValueError, TypeError) as e:
            log.warning("export-chart 干预时间非法,跳过干预竖线(agent={}, sim_time={}): {}".format(
                agent, iv.get("sim_time"), e))
            continue
        if t0 <= ivt <= t1:
            ax.axvline(ivt, color="#e07b39", linewidth=1.6, linestyle=":", alpha=0.8)

    ax.set_title("{} — 价值倾向演变(实线=内化, 虚线=约束期望, 橙线=干预)".format(agent),
                 fontproperties=FONT, fontsize=13)
    ax.set_ylim(0, 1.0)
    _time_abbr = lambda _x: _x.strftime("%m/%d %H:%M")
    ax.grid(True, alpha=0.3)
    if goal_names:
        import matplotlib.patches as mpatches
        handles = [mpatches.Patch(color=color_of[g], label=g) for g in goal_names]
        ax.legend(handles=handles, prop=FONT, fontsize=8, loc="lower center",
                  ncol=len(goal_names), frameon=True, bbox_to_anchor=(0.5, -0.32))
    # X 轴时间刻度:按跨度选小时步长(约 6 个刻度),避免长跨度标签挤在一起;
    # 格式定制——00:00(跨天处)标日期 MM/DD,其余标 HH:MM,贴合前端时间轴风格。
    # 为保证跨天时确有 00:00 刻度(午夜即日期分隔),用 FixedLocator 在
    # HourLocator 生成的整点刻度之上补插所有图内午夜,去重后固定刻度。
    span_h = max((t1 - t0).total_seconds() / 3600.0, 0.1)
    step = max(1, int(round(span_h / 6.0)))
    _ticks = sorted(set(
        list(mdates.HourLocator(interval=step).tick_values(t0, t1))
        + list(mdates.HourLocator(byhour=0).tick_values(t0, t1))  # 图内每个 00:00
    ))
    ax.xaxis.set_major_locator(FixedLocator(_ticks))

    def _time_fmt(n, pos=None):
        d = mdates.num2date(n)
        if d.hour == 0 and d.minute == 0:
            # 跨天处:日期与时间上下两行堆叠,给出日期上下文又省横向宽度,避免和相邻刻度挤在一起
            return "{}\n{}".format(d.strftime("%m/%d"), d.strftime("%H:%M"))
        return d.strftime("%H:%M")

    ax.xaxis.set_major_formatter(FuncFormatter(_time_fmt))
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout()
    # 标题携带日期区间,补足 X 轴只显时间时的日期上下文
    ax.set_title("{} — 价值倾向演变(实线=内化, 虚线=约束期望, 橙线=干预)　{} ~ {}".format(
        agent, _time_abbr(t0), _time_abbr(t1)),
        fontproperties=FONT, fontsize=13)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
