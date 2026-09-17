# -*- coding: utf-8 -*-
"""case01 记录的过程审查页渲染器(零依赖,只读 run.json)。

与 render.py 的区别:面向"判断→后果→反思→专家审核"的审查视图,而不是叙事稿。
逐条 run 生成一页,另出总览页(三条线并列)。

输出目录:case01/runs_html/viz/
用法:
    python -m case01.viz                      # 渲染 runs/ 下全部
    python -m case01.viz demo-A-mavis demo-B-mavis
"""
import html as _html
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs_html", "viz")

RISK_COLOR = {"high": "#c0392b", "medium": "#d68910", "low": "#1e8449"}
BRANCH_COLOR = {"A": "#c0392b", "B": "#1f6feb", "C": "#7d3c98"}


# ---------------------------------------------------------------------------
# 数据提取
# ---------------------------------------------------------------------------
def load_run(run_id: str, runs_dir: str = RUNS_DIR) -> dict:
    with open(os.path.join(runs_dir, run_id, "run.json"), encoding="utf-8") as f:
        return json.load(f)


def price_series(rec: dict) -> List[Tuple[str, float]]:
    """从事件里抽价格序列(按日期升序)。"""
    out: List[Tuple[str, float]] = []
    for ev in rec.get("events") or []:
        price = ev.get("price_usd")
        if price is None:
            continue
        out.append((str(ev.get("date", "")), float(price)))
    return sorted(out, key=lambda p: p[0])


def cash_series(rec: dict) -> List[Tuple[str, float]]:
    out: List[Tuple[str, float]] = []
    for s in rec.get("state_history") or []:
        st = s.get("state") or {}
        try:
            out.append((str(s.get("date", "")), float(st.get("cash_rmb") or 0.0)))
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# SVG 小图(无第三方库)
# ---------------------------------------------------------------------------
def _svg_line(points: List[Tuple[str, float]], color: str = "#1f6feb",
              width: int = 720, height: int = 220, label: str = "",
              extra_lines: Optional[List[Tuple[List[Tuple[str, float]], str, str]]] = None,
              fmt: str = "{:.2f}") -> str:
    """折线图。extra_lines: [(series, color, name)] 叠加线。"""
    all_series = [(points, color, label)] + [
        (pts, c, n) for pts, c, n in (extra_lines or [])
    ]
    flat = [p for pts, _c, _n in all_series for _d, p in pts]
    if not flat:
        return "<p class='muted'>（无数据）</p>"
    lo, hi = min(flat), max(flat)
    pad = (hi - lo) * 0.12 or 1.0
    lo, hi = lo - pad, hi + pad
    left, right, top, bottom = 52, 16, 14, 30
    w, h = width - left - right, height - top - bottom
    dates = sorted({d for pts, _c, _n in all_series for d, _p in pts})
    dx = {d: i for i, d in enumerate(dates)}
    nx = len(dates) - 1 or 1

    def sx(d: str) -> float:
        return left + (dx[d] / nx) * w

    def sy(v: float) -> float:
        return top + h - ((v - lo) / (hi - lo)) * h

    parts = ["<svg viewBox='0 0 {} {}' class='chart' role='img'>".format(width, height)]
    for i in range(5):
        y = top + h * i / 4
        val = hi - (hi - lo) * i / 4
        parts.append("<line x1='{}' y1='{:.1f}' x2='{}' y2='{:.1f}' class='grid'/>".format(
            left, y, width - right, y))
        parts.append("<text x='{}' y='{:.1f}' class='ax' text-anchor='end'>{}</text>".format(
            left - 6, y + 4, _html.escape(fmt.format(val))))
    for d in dates:
        parts.append("<text x='{:.1f}' y='{}' class='ax' text-anchor='middle'>{}</text>".format(
            sx(d), height - 8, _html.escape(d[5:])))
    for pts, c, name in all_series:
        if not pts:
            continue
        poly = " ".join("{:.1f},{:.1f}".format(sx(d), sy(v)) for d, v in pts)
        parts.append("<polyline points='{}' fill='none' stroke='{}' stroke-width='2'/>".format(poly, c))
        for d, v in pts:
            parts.append("<circle cx='{:.1f}' cy='{:.1f}' r='3' fill='{}'><title>{} {} {}</title></circle>".format(
                sx(d), sy(v), c, _html.escape(d), _html.escape(name or label), _html.escape(fmt.format(v))))
    legend = " · ".join(
        "<span style='color:{}'>{}</span>".format(c, _html.escape(n or "数值"))
        for pts, c, n in all_series if pts)
    parts.append("</svg>")
    parts.append("<div class='legend'>{}</div>".format(legend))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
def _issues_html(rec: dict) -> str:
    issues = (rec.get("router") or {}).get("issues") or []
    if not issues:
        return "<p class='muted'>Router 未拆出问题（或未运行 Reflection/Router）。</p>"
    out = []
    for it in issues:
        risk = str(it.get("risk", "") or "").lower()
        color = RISK_COLOR.get(risk, "#666")
        out.append(
            "<div class='card'>"
            "<div class='card-head'><span class='badge' style='background:{}'>{}</span>"
            "<span class='field'>{}</span></div>"
            "<div class='summary'>{}</div>"
            "<div class='reason muted'>路由理由：{}</div>"
            "</div>".format(
                color, _html.escape(risk or "n/a"),
                _html.escape(str(it.get("field", ""))),
                _html.escape(str(it.get("summary", ""))),
                _html.escape(str(it.get("routing_reason", "")))))
    return "\n".join(out)


def _turns_html(rec: dict) -> str:
    turns = rec.get("turns") or []
    if not turns:
        return "<p class='muted'>（无对话记录）</p>"
    out = []
    for t in turns:
        who = str(t.get("speaker", ""))
        cls = "ai" if who == "ai" else "ethan"
        name = "Investment AI" if who == "ai" else "Ethan Lin"
        out.append("<div class='turn {}'><div class='who'>{} <span class='date'>{}</span></div>"
                   "<div class='say'>{}</div></div>".format(
                       cls, _html.escape(name), _html.escape(str(t.get("date", ""))),
                       _html.escape(str(t.get("text", "")))))
    return "\n".join(out)


def _events_html(rec: dict) -> str:
    rows = []
    for ev in rec.get("events") or []:
        price = ev.get("price_usd")
        rows.append("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _html.escape(str(ev.get("date", ""))),
            _html.escape(str(ev.get("kind", ""))),
            _html.escape(str(ev.get("summary", ""))[:160]),
            "" if price is None else _html.escape("${:.2f}".format(float(price)))))
    if not rows:
        return "<p class='muted'>（无事件）</p>"
    return ("<table><thead><tr><th>日期</th><th>类型</th><th>摘要</th><th>价格</th></tr></thead>"
            "<tbody>{}</tbody></table>".format("".join(rows)))


def _states_html(rec: dict) -> str:
    rows = []
    for s in rec.get("state_history") or []:
        st = s.get("state") or {}
        rows.append("<tr><td>{}</td><td>{:,.0f}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _html.escape(str(s.get("date", ""))),
            float(st.get("cash_rmb") or 0.0),
            "持有" if st.get("hcm_shares") else "未持有",
            "" if st.get("entry_price_usd") is None else _html.escape("${}".format(st["entry_price_usd"])),
            "" if st.get("exit_price_usd") is None else _html.escape("${}".format(st["exit_price_usd"]))))
    if not rows:
        return "<p class='muted'>（无状态快照）</p>"
    return ("<table><thead><tr><th>日期</th><th>现金(RMB)</th><th>持仓</th><th>入场价</th><th>退出价</th>"
            "</tr></thead><tbody>{}</tbody></table>".format("".join(rows)))


def _condition_html(rec: dict) -> str:
    items = rec.get("condition_monitor") or []
    if not items:
        return "<p class='muted'>（非 Branch C，或未产生条件监测记录）</p>"
    out = []
    for it in items:
        fired = bool(it.get("fired"))
        out.append("<li class='{}'><b>{}</b> fired={} close={}</li>".format(
            "fired" if fired else "notfired",
            _html.escape(str(it.get("date", ""))),
            "是" if fired else "否",
            _html.escape(str(it.get("close_usd", "")))))
    return "<ul class='cond'>{}</ul>".format("".join(out))


def _audit_html(rec: dict) -> str:
    rows = []
    for a in rec.get("audit") or []:
        label = a.get("action", "")
        detail = {k: v for k, v in a.items() if k not in ("t", "action")}
        rows.append("<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _html.escape(str(a.get("t", ""))), _html.escape(str(label)),
            _html.escape(json.dumps(detail, ensure_ascii=False)[:180])))
    if not rows:
        return "<p class='muted'>（无审计记录）</p>"
    return ("<table><thead><tr><th>时间</th><th>动作</th><th>详情</th></tr></thead>"
            "<tbody>{}</tbody></table>".format("".join(rows)))


def page_html(rec: dict) -> str:
    rid = str(rec.get("run_id", ""))
    branch = str(rec.get("branch", "") or "")
    color = BRANCH_COLOR.get(branch, "#333")
    gaps = (rec.get("compat") or {}).get("gaps") or []
    fb = rec.get("final_feedback") or {}
    ref = (rec.get("reflection") or {}).get("text") or ""
    prices = price_series(rec)
    cash = cash_series(rec)
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>Case01 审查视图 · {rid}</title>
<style>
 body{{font-family:-apple-system,'Segoe UI','Noto Sans SC',sans-serif;margin:0;background:#f6f7f9;color:#1c1e21;line-height:1.7}}
 .wrap{{max-width:960px;margin:0 auto;padding:28px 20px 60px}}
 h1{{font-size:1.4em;margin:0 0 4px}} h2{{font-size:1.05em;margin:28px 0 10px;border-left:4px solid {color};padding-left:8px}}
 .muted{{color:#6b7280}} .meta{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px;margin:14px 0}}
 .chart{{width:100%;height:auto;background:#fff;border:1px solid #e5e7eb;border-radius:10px}}
 .grid{{stroke:#eef0f3}} .ax{{font-size:11px;fill:#6b7280}}
 .legend{{font-size:12px;margin:6px 0 0}}
 .card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px;margin:8px 0}}
 .card-head{{display:flex;gap:8px;align-items:center}}
 .badge{{color:#fff;border-radius:6px;padding:1px 8px;font-size:12px}} .field{{font-size:12px;color:#374151}}
 .summary{{margin-top:6px}} .reason{{font-size:12px}}
 .turn{{border-radius:10px;padding:8px 12px;margin:8px 0;background:#fff;border:1px solid #e5e7eb}}
 .turn.ai{{border-left:4px solid {color}}} .turn.ethan{{border-left:4px solid #64748b}}
 .who{{font-size:12px;color:#374151}} .date{{color:#9ca3af}}
 table{{width:100%;border-collapse:collapse;background:#fff;font-size:13px}}
 th,td{{border-bottom:1px solid #eef0f3;padding:6px 8px;text-align:left;vertical-align:top}}
 details{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px;margin:8px 0}}
 ul.cond{{list-style:none;padding-left:0}} ul.cond li{{padding:4px 8px;border-radius:6px;margin:4px 0;background:#fff;border:1px solid #e5e7eb}}
 ul.cond li.fired{{border-color:#c0392b}} a{{color:#1f6feb}}
</style></head><body><div class="wrap">
<h1>Case 01 审查视图 · {rid}</h1>
<div class="muted">Branch <b style="color:{color}">{branch}</b> · {start} → {end} · {nturns} 轮对话 · {nstates} 条状态快照 · {nissues} 个待审问题</div>
<div class="meta"><b>最终反馈（Ethan 陈述）</b><div>{fbe}</div>
<div style="margin-top:6px"><b>Investment AI 回应</b><div>{fba}</div></div></div>

<h2>市场与判断：价格轨迹</h2>
{price_svg}

<h2>后果：现金轨迹</h2>
{cash_svg}

<h2>逐日状态</h2>
{states}

<h2>公开事件（按日期释放）</h2>
{events}

<h2>对话全文</h2>
{turns}

<h2>Branch C 条件监测</h2>
{cond}

<h2>反思全文（{reflen} 字）</h2>
<details><summary>展开反思</summary><div style="white-space:pre-wrap">{reflection}</div></details>

<h2>Router 拆出的待审问题</h2>
{issues}

<h2>审计链</h2>
<details><summary>展开 {naudit} 条审计记录</summary>{audit}</details>

<h2>数据边界</h2>
<ul>{gaps}</ul>
<div class="muted" style="margin-top:20px"><a href="index.html">← 返回总览</a></div>
</div></body></html>""".format(
        rid=_html.escape(rid), color=color, branch=_html.escape(branch or "—"),
        start=_html.escape(str(rec.get("start_date", ""))), end=_html.escape(str(rec.get("end_date", ""))),
        nturns=len(rec.get("turns") or []), nstates=len(rec.get("state_history") or []),
        nissues=len((rec.get("router") or {}).get("issues") or []),
        fbe=_html.escape(str(fb.get("ethan", "") or "")),
        fba=_html.escape(str(fb.get("ai", "") or "")),
        price_svg=_svg_line(prices, color=color, label="HCM 收盘价(USD)", fmt="${:.2f}"),
        cash_svg=_svg_line(cash, color="#1e8449", label="可用现金(RMB)", fmt="{:,.0f}"),
        states=_states_html(rec), events=_events_html(rec), turns=_turns_html(rec),
        cond=_condition_html(rec), reflen=len(ref),
        reflection=_html.escape(ref) or "（未运行 Reflection）",
        issues=_issues_html(rec), naudit=len(rec.get("audit") or []), audit=_audit_html(rec),
        gaps="".join("<li>{}</li>".format(_html.escape(str(g))) for g in gaps) or "<li>（无标注）</li>")


def index_html(recs: List[dict]) -> str:
    """总览页:三条线的价格并列 + 关键结果对照。"""
    series = []
    for rec in recs:
        b = str(rec.get("branch", "") or "")
        series.append((price_series(rec), BRANCH_COLOR.get(b, "#333"),
                       "{} · {}".format(b or "?", rec.get("run_id", ""))))
    first = series[0][0] if series else []
    rest = series[1:] if len(series) > 1 else []
    rows = []
    for rec in recs:
        st = (rec.get("state_history") or [{}])[-1].get("state") or {}
        rows.append("<tr><td><a href='{rid}.html'>{rid}</a></td><td>{br}</td><td>{dates}</td>"
                    "<td>{turns}</td><td>{cash}</td><td>{hold}</td><td>{issues}</td></tr>".format(
                        rid=_html.escape(str(rec.get("run_id", ""))),
                        br=_html.escape(str(rec.get("branch", ""))),
                        dates="{} → {}".format(rec.get("start_date", ""), rec.get("end_date", "")),
                        turns=len(rec.get("turns") or []),
                        cash=("{:,.0f}".format(float(st.get("cash_rmb") or 0.0))),
                        hold=("持有" if st.get("hcm_shares") else ("已退出" if st.get("exited") else "未持有")),
                        issues=len((rec.get("router") or {}).get("issues") or [])))
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>Case01 审查视图 · 总览</title>
<style>body{{font-family:-apple-system,'Segoe UI','Noto Sans SC',sans-serif;background:#f6f7f9;margin:0;color:#1c1e21}}
.wrap{{max-width:960px;margin:0 auto;padding:28px 20px 60px}} h1{{font-size:1.4em}}
.chart{{width:100%;height:auto;background:#fff;border:1px solid #e5e7eb;border-radius:10px}}
.grid{{stroke:#eef0f3}} .ax{{font-size:11px;fill:#6b7280}} .legend{{font-size:12px;margin:6px 0}}
table{{width:100%;border-collapse:collapse;background:#fff;font-size:13px}} th,td{{border-bottom:1px solid #eef0f3;padding:6px 8px;text-align:left}}
a{{color:#1f6feb}}</style></head><body><div class="wrap">
<h1>Case 01 审查视图 · 总览</h1>
<p class="legend">三条线价格轨迹并列（C 与 A 共用市场时间线）</p>
{chart}
<h2>关键结果对照</h2>
<table><thead><tr><th>记录</th><th>Branch</th><th>区间</th><th>对话轮数</th><th>末态现金</th><th>持仓</th><th>待审问题</th></tr></thead>
<tbody>{rows}</tbody></table>
</div></body></html>""".format(
        chart=_svg_line(first, color=series[0][1] if series else "#333",
                        label=series[0][2] if series else "", fmt="${:.2f}", extra_lines=rest),
        rows="".join(rows))


def main():
    targets = sys.argv[1:] or sorted(
        d for d in os.listdir(RUNS_DIR) if os.path.isdir(os.path.join(RUNS_DIR, d)))
    os.makedirs(OUT_DIR, exist_ok=True)
    recs = []
    for rid in targets:
        try:
            rec = load_run(rid)
        except Exception as e:
            print("skip {}: {}".format(rid, e))
            continue
        recs.append(rec)
        with open(os.path.join(OUT_DIR, rid + ".html"), "w", encoding="utf-8") as f:
            f.write(page_html(rec))
        print("rendered -> {}".format(os.path.join(OUT_DIR, rid + ".html")))
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html(recs))
    print("index -> {}".format(os.path.join(OUT_DIR, "index.html")))


if __name__ == "__main__":
    main()
