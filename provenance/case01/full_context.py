# -*- coding: utf-8 -*-
"""Full Context 生成:一次 Run 的完整可读上下文(自然语言)。

供专家审核时"按需查看完整记录"(04 六.3 / 05 三:Full Context 必须以正常
自然语言呈现,而不是直接显示 JSON、数据库字段、event object 或代码日志)。
由 case01 组装,平台(HCI)只管展示。

信息边界(04 三.15 / 01 七):不出现 Branch A/B/C 标签、Branch 判定方式、
Future Timeline 预设、未披露的 Ethan 隐藏信息或实验元信息——专家要判断
的是"Investment AI 当时判断得如何",不应被"它被分到哪条线"干扰。

内容(按时间线):
  一、案例设定(角色/资金/咨询对象,不含 Branch 预设)
  二、T0 咨询对话全程(Ethan 与 Investment AI)
  三、Investment AI 检索到的金融信息(来源/类型/时间)
  四、市场时间线与当事人状态(逐日公开事件 + 持仓/现金变化)
  五、最终反馈(2026-09-15 Ethan 陈述 + Investment AI 回应)
  六、Investment AI 的内部反思(如已生成)
  七、问题分流(Router,如已生成)
"""
import json
import os
from typing import Optional

# 本模块不依赖 render.py 的 HTML 逻辑,输出为纯文本。


def _pct(a, b) -> Optional[str]:
    """a 相对 b 的涨跌百分比文本。"""
    try:
        a, b = float(a), float(b)
        if not b:
            return None
        return "{:+.1f}%".format((a - b) / b * 100)
    except (TypeError, ValueError):
        return None


def _money(v) -> str:
    try:
        return "{:,.0f}".format(float(v))
    except (TypeError, ValueError):
        return str(v)


def _price_usd(v) -> str:
    try:
        return "${:,.2f}".format(float(v))
    except (TypeError, ValueError):
        return str(v)


def _fmt_state(st: dict) -> str:
    """把一条当事人状态转成一句自然语言。"""
    if st.get("exited"):
        txt = "当事人已退出持仓,可支配现金约 {} 元".format(_money(st.get("cash_rmb")))
        entry, exit_ = st.get("entry_price_usd"), st.get("exit_price_usd")
        pct = _pct(exit_, entry)
        if entry and exit_ and pct:
            txt += "(买入价 {},退出价 {},约 {})".format(
                _price_usd(entry), _price_usd(exit_), pct)
        return txt
    if st.get("hcm_shares"):
        entry = st.get("entry_price_usd")
        cur = st.get("exit_price_usd") or entry
        pct = _pct(cur, entry)
        if entry:
            if pct:
                return "当事人持有 HCM(成本约 {},现价约 {},约 {})".format(
                    _price_usd(entry), _price_usd(cur), pct)
            return "当事人持有 HCM(成本约 {})".format(_price_usd(entry))
        return "当事人持有 HCM 仓位"
    return "当事人未买入 HCM,现金约 {} 元".format(_money(st.get("cash_rmb", 200000)))


def build_full_context(rec: dict) -> str:
    parts = []
    parts.append("GTC Case 01 · 一次投资咨询的完整记录")
    parts.append("")

    # 一、案例设定
    parts.append("一、案例设定")
    parts.append("本记录对应一次投资咨询模拟:个人投资者 Ethan Lin(下称“咨询者”)"
                 "就一家虚构上市公司 HCM(HelioCore Materials)的市场传闻与基本面,"
                 "咨询其 AI 投资助手(下称“Investment AI”)。咨询开始时咨询者可投入"
                 "资金约人民币 20 万元,未持有 HCM。HCM 股价以美元报价,资金以人民币"
                 "计量。Investment AI 只检索一个本地金融信息库,信息库内容在咨询过程"
                 "中按日期逐步公开。")
    parts.append("")

    # 二、T0 咨询对话
    fb = rec.get("final_feedback") or {}
    fb_date = fb.get("date") or rec.get("end_date")
    has_fb_text = bool(fb.get("ethan") or fb.get("ai"))
    if has_fb_text:
        # 对话正文截止到最终反馈之前;最终轮在“五、最终反馈”单独呈现,
        # 避免同一大段文字出现两次。
        turns_sel = [t for t in (rec.get("turns") or [])
                     if t.get("date") != fb_date]
    else:
        turns_sel = rec.get("turns") or []
    if turns_sel:
        parts.append("二、T0 咨询对话")
        for t in turns_sel:
            who = "咨询者" if t.get("speaker") == "ethan" else "Investment AI"
            parts.append("[{} · {}]".format(who, t.get("date", "")))
            parts.append((t.get("text") or "").strip())
            parts.append("")
    else:
        parts.append("二、T0 咨询对话")
        parts.append("(本次记录未包含 T0 对话原文。)")
        parts.append("")

    # 三、检索
    rets = rec.get("retrievals") or []
    if rets:
        parts.append("三、Investment AI 检索到的信息")
        for r in rets:
            hits = r.get("hits") or []
            stats = r.get("source_stats") or {}
            parts.append("—— 针对以下问题,Investment AI 检索到 {} 条资料"
                         "(来自 {} 个不同来源,其中 {} 条为二次转述):".format(
                             len(hits), stats.get("n_sources", "-"),
                             stats.get("second_hand_count", "-")))
            for h in hits:
                parts.append("· [{}] {} | {} | {}".format(
                    h.get("type", ""), h.get("source", ""),
                    h.get("time", ""), h.get("title", "")))
            parts.append("")
    else:
        parts.append("三、Investment AI 检索到的信息")
        parts.append("(本记录未包含检索明细。)")
        parts.append("")

    # 四、市场时间线与当事人状态
    events = rec.get("events") or []
    states = {s.get("date"): (s.get("state") or {})
              for s in (rec.get("state_history") or [])}
    if events:
        parts.append("四、市场时间线与当事人状态")
        by_date = {}
        for e in events:
            by_date.setdefault(e.get("date", ""), []).append(e)
        for d in sorted(by_date.keys()):
            evs = by_date[d]
            price = next((e for e in evs
                          if e.get("kind") == "price" and e.get("price_usd")),
                         None)
            others = [e for e in evs
                      if not (e.get("kind") == "price" and e.get("price_usd"))]
            lines = []
            for e in others:
                lines.append((e.get("summary") or "").strip())
            if price:
                p = price.get("price_usd")
                lines.append("当日 HCM 报价:{}".format(
                    _price_usd(p) if p else (price.get("summary") or "").strip()))
            if lines:
                parts.append("[{}] {}".format(d, " ".join(lines)))
            st = states.get(d)
            if st:
                parts.append("    当事人状态:{}".format(_fmt_state(st)))
        parts.append("")

    # 五、最终反馈
    if fb.get("ethan") or fb.get("ai"):
        parts.append("五、最终反馈({})".format(fb_date or ""))
        if fb.get("ethan"):
            parts.append("【咨询者陈述实际经历与后果】")
            parts.append((fb["ethan"] or "").strip())
            parts.append("")
        if fb.get("ai"):
            parts.append("【Investment AI 回应】")
            parts.append((fb["ai"] or "").strip())
            parts.append("")
    elif turns_sel:
        # 没有独立 final_feedback 键时,对话里的最终轮已在上文出现。
        pass

    # 六、Reflection
    ref = rec.get("reflection") or {}
    if ref.get("text"):
        parts.append("六、Investment AI 的内部反思(完整原文)")
        parts.append((ref["text"] or "").strip())
        parts.append("")

    # 七、Router
    router = rec.get("router") or {}
    if router.get("issues"):
        parts.append("七、反思中识别出的待审核问题")
        for i in router["issues"]:
            parts.append("· 问题:{}".format(i.get("summary", "")))
            parts.append("  专业类别:{} | 风险等级:{}".format(
                i.get("field", ""), i.get("risk", "")))
            parts.append("  路由理由:{}".format(i.get("routing_reason", "")))
        parts.append("")
    return "\n".join(parts)


def load_run(runs_root: str, run_id: str) -> Optional[dict]:
    p = os.path.join(runs_root, run_id, "run.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def list_runs(runs_root: str) -> list:
    """列出所有含 run.json 的 run(按 run_id 倒序)。

    仅输出给平台/后端的索引字段(供任务关联与审计),不含对话正文;
    是否向专家展示 branch 字段由平台侧控制。
    """
    out = []
    if not os.path.isdir(runs_root):
        return out
    for d in sorted(os.listdir(runs_root)):
        p = os.path.join(runs_root, d, "run.json")
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            continue
        ba = rec.get("branch_action") or {}
        n_turns = len(rec.get("turns") or [])
        out.append({
            "run_id": rec.get("run_id") or d,
            "start_date": rec.get("start_date", ""),
            "end_date": rec.get("end_date", ""),
            "branch": rec.get("branch", ""),
            "branch_summary": _branch_summary(rec.get("branch", ""), ba),
            "n_turns": n_turns,
            "n_retrievals": len(rec.get("retrievals") or []),
            "has_reflection": bool((rec.get("reflection") or {}).get("text")),
            "router_issue_count": len((rec.get("router") or {}).get("issues", [])),
        })
    out.sort(key=lambda x: x["run_id"], reverse=True)
    return out


def _branch_summary(branch: str, ba: dict) -> str:
    """把 Branch 判定转成对平台内部可读的一句话(仍不面向专家展示)。"""
    if branch == "A":
        return "建议买入,当事人投入接近全部资金"
    if branch == "B":
        return "不建议买入/拒绝判断,当事人未买入"
    if branch == "C":
        return "条件化方案,当事人按方案执行(可能等待触发)"
    return "Branch 未知"
