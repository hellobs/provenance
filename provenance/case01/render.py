# -*- coding: utf-8 -*-
"""Case 01 Run 记录 → 学术排版 HTML(只读展示,纯文字)。

把 case01/runs/<run_id>/ 的记录渲染成白底窄栏页面,便于评审阅读"过程与
判断链"。零服务依赖:生成静态 HTML,浏览器直接打开。
输出到 case01/runs_html/(index.html + 每 run 一页)。

版式约定(按用户反馈):
- 页头只放一行定位事实(谁咨询谁、标的、日期、分支),不加导读/摘要;
- 不渲染"摘要块/页面说明"式文案;
- 长内容(对话轮次/检索/反思/最终反馈)一律默认折叠,点击展开。
用法:
    python -m case01.render            # 渲染全部 run
    python -m case01.render my-run     # 只渲染指定 run
"""
import html as _html
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(HERE, "runs")
OUT_DIR = os.path.join(HERE, "runs_html")

# 长于该字符数的内容块默认折叠
FOLD_LEN = 240

CSS = """
body { font-family: Georgia, 'Songti SC', 'Noto Serif CJK SC', serif;
       background:#fff; color:#222; margin:0; line-height:1.75; }
.wrap { max-width: 780px; margin: 0 auto; padding: 48px 28px 80px; }
h1 { font-size: 1.5em; font-weight: 600; border-bottom: 1px solid #999;
     padding-bottom: 8px; margin-bottom: 6px; }
h2 { font-size: 1.15em; font-weight: 600; margin: 34px 0 8px;
     color:#111; }
h3 { font-size: 1em; font-weight: 600; margin: 22px 0 6px; }
.meta { color:#555; font-size: 0.9em; margin: 2px 0 6px; }
.meta b { color:#333; }
table { border-collapse: collapse; width:100%; font-size: 0.92em;
        margin: 10px 0; }
th, td { border: 1px solid #ccc; padding: 6px 9px; text-align:left;
         vertical-align: top; }
th { background: #f5f5f5; font-weight: 600; }
blockquote { margin: 10px 0; padding: 10px 16px; background:#fafafa;
             border-left: 3px solid #bbb; color:#333; }
blockquote p { margin: 6px 0; }
ul { margin: 8px 0; padding-left: 24px; }
.speaker { font-weight: 600; margin: 22px 0 2px; }
.speaker.ethan { color:#7a4d1a; }
.speaker.ai { color:#1a4d7a; }
.explain { color:#666; }
/* 对话引文容器:色条 + 浅底 */
.utterance { margin: 14px 0; padding: 10px 14px 12px;
             border-radius: 4px; font-size: 0.96em; }
.utterance.ethan { background: #faf3e8; border-left: 4px solid #b98a3e; }
.utterance.ai    { background: #eef3f8; border-left: 4px solid #3a6ea5; }
.utterance .tag { font-weight: 700; font-size: 0.88em;
                  letter-spacing: 0.02em; margin-bottom: 4px; }
.utterance.ethan .tag { color: #7a4d1a; }
.utterance.ai .tag    { color: #1a4d7a; }
.utterance .who-note { font-weight: 400; color: #999; font-size: 0.82em;
                       margin-left: 8px; }
.utterance p:first-of-type { margin-top: 2px; }
.utterance p { margin: 6px 0; }
.utterance table, .utterance ul, .utterance ol { font-size: 0.95em; }
.branch { display:inline-block; border:1px solid #666; border-radius:3px;
          padding: 1px 10px; font-size: 0.9em; margin-left: 8px; }
.note { color:#777; font-size:0.9em; }
a { color:#1a4d7a; text-decoration:none; }
a:hover { text-decoration: underline; }
.section-label { font-size:0.82em; letter-spacing:0.08em; color:#888;
                 margin: 40px 0 2px; border-top: 1px solid #ddd;
                 padding-top: 10px; }
.foot { margin-top: 50px; border-top: 1px solid #ccc; padding-top: 10px;
        color:#888; font-size:0.85em; }
/* 折叠块:默认收起,点击展开 */
details.fold { margin: 10px 0; }
details.fold summary { cursor: pointer; font-weight: 600; color:#1a4d7a;
                       list-style: none; padding: 4px 0; }
details.fold summary::-webkit-details-marker { display:none; }
details.fold summary::before { content:"▸ "; color:#888; }
details.fold[open] summary::before { content:"▾ "; }
details.fold .sum-note { font-weight:400; color:#777; margin-left:6px;
                         font-size:0.86em; }
details.fold .sum-prev { display:block; font-weight:400; color:#555;
                         font-size:0.9em; margin-top:2px; line-height:1.5; }
details.fold > .body { margin-top: 8px; }
"""

# 学术语境下把符号映射为文字标注(避免评审页出现 emoji)
EMOJI_MAP = {
    "✅": "〔可信〕", "❌": "〔不可信〕", "⚠️": "〔注意〕", "⚠": "〔注意〕",
    "📌": "〔要点〕", "❗": "〔警示〕", "👉": "〔提示〕", "🔍": "〔提示〕",
    "🟢": "〔正面〕", "🔴": "〔负面〕", "🟡": "〔中性〕",
    "🏁": "〔结论〕", "📅": "〔日期〕", "🔒": "〔信息边界〕",
    "📊": "〔数据〕", "💡": "〔提示〕", "🧭": "〔指引〕", "✏️": "〔说明〕",
}


def _strip_emoji(text: str) -> str:
    for k, v in EMOJI_MAP.items():
        text = text.replace(k, v)
    return text


def _summarize(text: str, n: int) -> str:
    """取文本前 n 字作为摘要(去空白换行)"""
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:n] + ("…" if len(t) > n else "")


def _inline(text: str) -> str:
    """行内:加粗/斜体/反引号 → HTML(先转义再替换)"""
    t = _html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    return t


def _table_html(rows):
    cells = [[_inline(c.strip()) for c in r.strip().strip("|").split("|")]
             for r in rows]
    body = [r for r in cells if not all(re.match(r"^:?-{2,}:?$", c) for c in r)]
    if not body:
        return ""
    html = "<table>"
    html += "<tr>{}</tr>".format("".join("<th>{}</th>".format(c) for c in body[0]))
    for r in body[1:]:
        html += "<tr>{}</tr>".format("".join("<td>{}</td>".format(c) for c in r))
    html += "</table>"
    return html


def _md(text: str) -> str:
    """极简 markdown → HTML(标题/表格/无序列表/段落;覆盖 AI 输出形态)"""
    text = _strip_emoji(text)
    lines = text.split("\n")
    out, i, n = [], 0, len(lines)
    in_table = False
    while i < n:
        line = lines[i]
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            if not in_table:
                in_table = True
                table_rows = []
            table_rows.append(line)
            i += 1
            continue
        if in_table:
            out.append(_table_html(table_rows))
            in_table = False
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            lvl = len(m.group(1))
            out.append("<h{}>{}</h{}>".format(lvl, _inline(m.group(2)), lvl))
            i += 1
            continue
        if re.match(r"^[-*]\s+", s):
            items = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(_inline(re.sub(r"^[-*]\s+", "", lines[i].strip())))
                i += 1
            out.append("<ul>{}</ul>".format(
                "".join("<li>{}</li>".format(x) for x in items)))
            continue
        if re.match(r"^\d+[.、]\s+", s):
            items = []
            while i < n and re.match(r"^\d+[.、]\s+", lines[i].strip()):
                items.append(_inline(re.sub(r"^\d+[.、]\s+", "", lines[i].strip())))
                i += 1
            out.append("<ol>{}</ol>".format(
                "".join("<li>{}</li>".format(x) for x in items)))
            continue
        if re.match(r"^---+\s*$", s):
            i += 1
            continue
        if not s:
            i += 1
            continue
        para = [s]
        i += 1
        while i < n and lines[i].strip() and not lines[i].strip().startswith(("|", "#", "-", "*", ">")) \
                and not re.match(r"^\d+[.、]\s+", lines[i].strip()) \
                and not re.match(r"^---+\s*$", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>{}</p>".format(_inline(" ".join(para))))
    if in_table:
        out.append(_table_html(table_rows))
    return "\n".join(out)


def _fold(label: str, note: str, prev: str, body: str,
          fold: bool = True) -> str:
    """内容块:长内容默认收起(fold=True),短内容直接展示。"""
    if not fold:
        return "<div class='body'>{}</div>".format(body)
    return ("<details class='fold'><summary>{}<span class='sum-note'>{}</span>"
            "<span class='sum-prev'>{}</span></summary>"
            "<div class='body'>{}</div></details>").format(
                _html.escape(label), _html.escape(note),
                _html.escape(prev), body)


def _utterance(speaker: str, date: str, text: str, note: str = "") -> str:
    """一轮对话:短文本直出;长文本折叠,summary 给首句预览。"""
    body = ("<div class='utterance {}'>{}</div>").format(
        "ethan" if speaker == "ethan" else "ai", _md(text))
    tag = "Ethan Lin" if speaker == "ethan" else "Investment AI"
    who_note = "{} · {}".format(date, note or ("提问" if speaker == "ethan"
                                               else "回答"))
    return _fold(tag, who_note, _summarize(text, 64), body,
                 fold=len((text or "")) > FOLD_LEN)


# ----------------------------------------------------------------------
def _load_run(run_id: str) -> dict:
    d = os.path.join(RUNS_DIR, run_id)
    rec = {"run_id": run_id}
    p = os.path.join(d, "run.json")
    if os.path.exists(p):
        rec.update(json.load(open(p, encoding="utf-8")))
    p = os.path.join(d, "branch.json")
    if os.path.exists(p):
        rec["branch_detail"] = json.load(open(p, encoding="utf-8"))
    for fn, key in (("turns.jsonl", "turns"), ("retrievals.jsonl", "retrievals")):
        p = os.path.join(d, fn)
        if os.path.exists(p):
            rec[key] = [json.loads(l) for l in open(p, encoding="utf-8")
                        if l.strip()]
    rec.setdefault("turns", [])
    return rec


BRANCH_SHORT = {
    "A": "AI 建议买入,Ethan 基本满仓投入",
    "B": "AI 不建议买入,Ethan 未参与",
    "C": "AI 给条件化方案,Ethan 按方案执行",
}
BRANCH_MEANING = {
    "A": "AI 明确支持买入 → Ethan 将投入约 20 万元;后续按 Market Timeline A"
         "(先涨后崩)执行,亏损约 39%",
    "B": "AI 不建议买入或拒绝实质判断 → Ethan 不买入;后续按 Market Timeline B"
         "(实际涨约 40%)执行,Ethan 错失机会并错过后续 co-investment 门槛",
    "C": "AI 给出条件化方案(小仓位/分批/等确认)→ Ethan 按其建议执行;"
         "市场走 Timeline A,盈亏由实际仓位决定",
}


def _page_html(rec: dict) -> str:
    run_id = rec["run_id"]
    branch = rec.get("branch") or (rec.get("branch_detail") or {}).get("branch", "")
    action = rec.get("branch_action") or {}
    turns = rec.get("turns", [])
    retrievals = rec.get("retrievals", [])
    start = rec.get("start_date") or rec.get("date") or ""
    end = rec.get("end_date") or ""

    h = ["<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
         "<title>GTC Case 01 · {}</title>".format(_html.escape(run_id)),
         "<style>{}</style></head><body><div class='wrap'>".format(CSS)]

    # 页头:一行定位事实(谁咨询谁、标的、日期、分支),无导读文字
    h.append("<h1>模拟投资咨询记录:{}</h1>".format(_html.escape(run_id)))
    h.append("<div class='meta'><b>咨询者</b> Ethan Lin(虚构个人投资者,约 20 万元可用、"
             "不持有 HCM)<b>→</b><b>Investment AI</b>(AI 投资助手,仅检索本地金融资料)"
             "<br><b>标的</b> HCM(虚构公司)·『百亿级海外订单』传闻 ｜ "
             "<b>日期</b> {}{}</div>".format(
                 _html.escape(str(start)),
                 " → " + _html.escape(str(end)) if end and end != start else ""))
    if branch:
        h.append("<div class='meta'><b>分支</b> <span class='branch'>{}</span> "
                 "{}</div>".format(
                     _html.escape(str(branch)),
                     _html.escape(BRANCH_SHORT.get(str(branch), ""))))

    # 一、对话过程
    if turns:
        h.append("<div class='section-label'>对话过程</div>")
        for t in turns:
            who = t.get("speaker", "")
            h.append(_utterance(who, str(t.get("date", "")),
                                str(t.get("text", ""))))

    # 二、信息检索记录(默认折叠)
    if retrievals:
        h.append("<div class='section-label'>信息检索</div>")
        for idx, r in enumerate(retrievals, 1):
            hits = r.get("hits", [])
            stats = r.get("source_stats") or {}
            note = "命中 {} 条 · 独立来源 {} · 二次转述 {} 条".format(
                len(hits), stats.get("n_sources", "-"),
                stats.get("second_hand_count", "-"))
            body_parts = ["<p class='note'>{}</p>".format(note)]
            if hits:
                rows = "".join(
                    "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>"
                    .format(_html.escape(str(i + 1)),
                            _html.escape(str(h.get("type", ""))),
                            _html.escape(str(h.get("source", "")))[:36],
                            _html.escape(str(h.get("time", ""))))
                    for i, h in enumerate(hits[:12]))
                body_parts.append(
                    "<table><tr><th>#</th><th>类型</th><th>来源</th>"
                    "<th>时间</th></tr>{}</table>".format(rows))
            h.append(_fold("检索 {}".format(idx),
                           note,
                           _summarize(str(r.get("query", "")), 48),
                           "\n".join(body_parts)))

    # 三、分支判定
    if branch:
        h.append("<div class='section-label'>分支判定</div>")
        h.append("<p>本次 Run 判定为 <b>Branch {}</b>:{}。</p>".format(
            _html.escape(str(branch)),
            _html.escape(BRANCH_MEANING.get(str(branch), ""))))
        reason = action.get("reason") or (rec.get("branch_detail") or {}).get("reason", "")
        if reason:
            h.append("<blockquote><p><b>判定理由:</b>{}</p></blockquote>"
                     .format(_html.escape(str(reason))))

    # 四、市场时间线与结果(events + Ethan 状态)
    events = rec.get("events", [])
    states = {s["date"]: s["state"] for s in rec.get("state_history", [])}
    if events:
        h.append("<div class='section-label'>市场时间线与结果</div>")
        by_date = {}
        for e in events:
            by_date.setdefault(e.get("date", ""), []).append(e)
        rows = []
        for d in sorted(by_date.keys()):
            st = states.get(d)
            if st:
                if st.get("exited"):
                    st_txt = "已退出,现金 ≈ {:,} 元".format(int(st.get("cash_rmb", 0)))
                elif st.get("hcm_shares"):
                    entry = st.get("entry_price_usd") or 0
                    cur = st.get("exit_price_usd") or entry
                    pnl = (cur - entry) / entry * 100 if entry else 0
                    st_txt = "持有(成本 ${0}·现价 ${1}·{2:+.0f}%)·现金 {3:,}".format(
                        entry, cur, pnl, int(st.get("cash_rmb", 0)))
                else:
                    st_txt = "未持仓,现金 200,000 元"
            else:
                st_txt = "—"
            ev_lines = "<br>".join(
                "〔{}〕{}".format(_html.escape(str(e.get("kind", ""))),
                                 _html.escape(str(e.get("summary", ""))))
                for e in by_date[d])
            rows.append("<tr><td style='white-space:nowrap'><b>{}</b></td>"
                        "<td>{}</td><td style='color:#555'>{}</td></tr>".format(
                            _html.escape(str(d)), ev_lines, st_txt))
        h.append("<table><tr><th>日期</th><th>公开事件</th>"
                 "<th>Ethan 状态</th></tr>{}</table>".format("".join(rows)))

    # 五、最终反馈(09-15)
    fb = rec.get("final_feedback")
    if fb:
        h.append("<div class='section-label'>最终反馈</div>")
        if fb.get("ethan"):
            h.append(_fold("Ethan Lin", "最终陈述",
                           _summarize(fb["ethan"], 64),
                           "<div class='utterance ethan'>{}</div>".format(
                               _md(fb["ethan"])),
                           fold=len(fb["ethan"]) > FOLD_LEN))
        if fb.get("ai"):
            h.append(_fold("Investment AI", "回应",
                           _summarize(fb["ai"], 64),
                           "<div class='utterance ai'>{}</div>".format(
                               _md(fb["ai"])),
                           fold=len(fb["ai"]) > FOLD_LEN))

    # 六、内部反思(Reflection)
    ref = rec.get("reflection")
    if ref and ref.get("text"):
        h.append("<div class='section-label'>内部反思</div>")
        h.append(_fold("Reflection", "Investment AI 内部生成",
                       _summarize(ref["text"], 64),
                       "<div class='utterance ai'>{}</div>".format(
                           _md(ref["text"]))))

    # 七、问题分流(Router)
    router = rec.get("router")
    if router and router.get("issues"):
        h.append("<div class='section-label'>问题分流</div>")
        rows = "".join(
            "<tr><td>{}</td><td>{}</td><td><span class='branch'>{}</span></td>"
            "<td>{}</td></tr>".format(
                _html.escape(str(i.get("id", ""))),
                _html.escape(str(i.get("field", ""))),
                _html.escape(str(i.get("risk", ""))),
                _html.escape(str(i.get("summary", ""))))
            for i in router["issues"])
        h.append("<table><tr><th>#</th><th>专业领域</th><th>风险</th>"
                 "<th>问题</th></tr>{}</table>".format(rows))

    # 页脚
    h.append("<div class='foot'>case01 本地引擎渲染 · <a href='index.html'>"
             "← 全部记录</a></div>")
    h.append("</div></body></html>")
    return "\n".join(h)


def _index_html(recs) -> str:
    h = ["<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
         "<title>GTC Case 01 — 模拟投资咨询记录</title>",
         "<style>{}</style></head><body><div class='wrap'>".format(CSS)]
    h.append("<h1>模拟投资咨询记录</h1>")
    h.append("<div class='meta'>以下每条是一次完整模拟咨询:虚构投资者 Ethan Lin"
             "就虚构公司 HCM 的『百亿级海外订单』传闻咨询 AI 投资助手"
             "(2026-08-27 咨询 → 2026-09-15 最终反馈)。</div>")
    rows = []
    for r in sorted(recs, key=lambda x: x.get("run_id", ""), reverse=True):
        run_id = r["run_id"]
        branch = r.get("branch") or ""
        start = r.get("start_date") or r.get("date") or ""
        ref = bool((r.get("reflection") or {}).get("text"))
        router_n = len((r.get("router") or {}).get("issues", []))
        note = BRANCH_SHORT.get(str(branch), "")
        if ref:
            note += " · 已反思{}".format(
                "(分流 {} 项)".format(router_n) if router_n else "")
        rows.append(
            "<tr><td><a href='{}.html'>{}</a></td>"
            "<td>{}{}</td><td>{}</td></tr>".format(
                _html.escape(run_id), _html.escape(run_id),
                "<span class='branch'>{}</span> ".format(
                    _html.escape(str(branch))) if branch else "",
                _html.escape(note), _html.escape(str(start))))
    h.append("<table><tr><th>记录</th><th>分支与状态</th>"
             "<th>咨询日</th></tr>{}</table>".format("".join(rows)))
    h.append("</div></body></html>")
    return "\n".join(h)


def main():
    targets = sys.argv[1:] or [d for d in os.listdir(RUNS_DIR)
                               if os.path.isdir(os.path.join(RUNS_DIR, d))]
    os.makedirs(OUT_DIR, exist_ok=True)
    recs = []
    for run_id in targets:
        try:
            rec = _load_run(run_id)
        except Exception as e:
            print("skip {}: {}".format(run_id, e))
            continue
        recs.append(rec)
        p = os.path.join(OUT_DIR, run_id + ".html")
        with open(p, "w", encoding="utf-8") as f:
            f.write(_page_html(rec))
        print("rendered -> {}".format(p))
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(_index_html(recs))
    print("index -> {}\\index.html".format(OUT_DIR))


if __name__ == "__main__":
    main()
