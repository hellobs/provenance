# -*- coding: utf-8 -*-
"""P0 现状核对:嵌入面 / 数据面 的跨源与嵌入可用性,逐项出证据。

检查项(全部对 http://127.0.0.1:5010;需先起实时面,见 live_switch.py):
1. 六个 embed 面:HTTP 状态 + 是否带 X-Frame-Options / CSP(frame-ancestors);
2. 数据面:带**已登记 Origin** 与**外来源**两种取数,分别看 ACAO 有无;
3. 预检:`OPTIONS /api/runs` 两边走得通/被拒是否符合 CORS 中间件的规定动作;
4. 深链:`/embed/review?run=<id>&tab=router` 是否 200,且取数面认这条 id;
5. 失败态:同一页面在"查不到 run"时不是白屏壳。

**本探针只看 HTTP 层**:页面是 JS 壳,`?run=`/`?tab=` 由客户端消费,
渲染出来的内容对不对归 `case01/tools/review_panel_probe.js`(无头跑真 DOM)。
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:5010"
PKG = r"D:\zzr\provenance\provenance"


def req(path, method="GET", headers=None, timeout=8):
    r = urllib.request.Request(BASE + path, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), ""
    except Exception as e:  # noqa: BLE001
        return 0, {}, "{}: {}".format(type(e).__name__, e)


def hdr(h, name):
    for k, v in h.items():
        if k.lower() == name.lower():
            return v
    return ""


def blocking(h):
    bad = []
    if hdr(h, "X-Frame-Options"):
        bad.append("XFO=" + hdr(h, "X-Frame-Options"))
    csp = hdr(h, "Content-Security-Policy")
    if csp and "frame-ancestors" in csp.lower():
        bad.append("CSP.frame-ancestors")
    return bad


# 最近一条成品记录(用于深链)。按 **mtime** 取,不按文件名字典序——
# 2026-10-04 实测:字典序会把历史里的 `probe-1003-1217` 当成"最新一条",
# 而真正的最新是当天那条 live 记录;探针报的记录和跑批的产出对不上就是在骗人。
_RUNS_DIR = os.path.join(PKG, "case01", "runs")
runs = sorted([d for d in os.listdir(_RUNS_DIR)
               if os.path.isfile(os.path.join(_RUNS_DIR, d, "run.json"))],
              key=lambda d: os.path.getmtime(os.path.join(_RUNS_DIR, d, "run.json")))
rid = runs[-1] if runs else ""

print("== 1. 嵌入面(状态 / 嵌入阻断头)")
# 5010 是唯一实时入口,case00/case01 互斥共用:goals/timeline 是 case00 的治理面,
# 起着 case01 时它们 404 是**设计如此**,不是坏面。以前不分 case 一律列出来,
# 每次看都要重推一遍"这 404 正常吗"。
CASE00_ONLY = {"/embed/goals", "/embed/timeline"}
for face in ("/", "/embed/scene", "/embed/review", "/embed/explore",
             "/embed/goals", "/embed/timeline"):
    code, h, _ = req(face)
    bad = blocking(h)
    note = "阻断: " + ",".join(bad) if bad else "可嵌入 ✓"
    if code == 404 and face in CASE00_ONLY:
        note += "(case00 专属面,当前跑 case01 → 404 正常)"
    print("  {:<20} {:<4} {}".format(face, code, note))

print("== 2. 数据面跨源头")
# 不带 Origin 的请求本来就不会有 ACAO,那是浏览器同源策略的事,查它等于查不到东西。
# 所以两半都要测:**放行的源**要拿到 ACAO(正向路径没证据 = 没测),
# **没登记的源**必须拿不到(默认只允许本机,平台侧跨源取数要显式设 EMBED_ALLOW_ORIGINS)。
FOREIGN = "http://evil.example"
for path in ("/api/runs", "/health"):
    code, h, _ = req(path, headers={"Origin": BASE})
    ok_code, ok_ao = code, hdr(h, "Access-Control-Allow-Origin")
    code2, h2, _ = req(path, headers={"Origin": FOREIGN})
    print("  {:<28} 本机源 {} ACAO={!r} ;外来源 {} ACAO={!r}(应为空)".format(
        path, ok_code, ok_ao or "无", code2, hdr(h2, "Access-Control-Allow-Origin") or "无"))
if rid:
    code, h, _ = req("/api/run-detail/review/{}".format(rid), headers={"Origin": BASE})
    print("  {:<28} {:<4} ACAO={!r}".format("/api/run-detail/review/<id>", code,
                                            hdr(h, "Access-Control-Allow-Origin")))

print("== 3. 预检 OPTIONS /api/runs(浏览器跨源取数前的必经一步)")
for name, origin in (("本机源", BASE), ("外来源", FOREIGN)):
    code, h, _ = req("/api/runs", method="OPTIONS",
                     headers={"Origin": origin,
                              "Access-Control-Request-Method": "GET"})
    # 外来源拿 400 是 Starlette CORSMiddleware 的**规定动作**(拒绝未登记的 Origin),
    # 不是故障;真正要盯的是本机源/登记过的源得 200 并回显 ACAO。
    print("  {:<8} → {} ACAO={!r} ACAM={!r}".format(
        name, code, hdr(h, "Access-Control-Allow-Origin"),
        hdr(h, "Access-Control-Allow-Methods")))

print("== 4. 深链 ?run=<id>&tab=router")
if rid:
    code, _h, body = req("/embed/review?run={}&tab=router".format(rid))
    # 页面是 JS 壳:`?run=` 由客户端 URLSearchParams 消费(见 review_app.py 的 WANT_RUN),
    # 所以**服务端 HTML 里永远不会有这个 id**——旧版拿 `rid in body` 判断,
    # 那条断言结构上就不可能成立,只会一直报 False。渲染后的内容归
    # case01/tools/review_panel_probe.js 无头跑真 DOM 验,这里只验取数面认这条 id。
    code2, _h2, body2 = req("/api/run-detail/review/{}".format(rid))
    print("  {} → {} ;取数面同一条 → {}(页面为 JS 壳,渲染内容由 review_panel_probe.js 验)".format(
        rid, code, code2))
    if rid not in body2:
        print("    ⚠ 取数面响应里没有该 run_id,深链点开的会不是自己要的那条")
else:
    print("  无成品记录,跳过")

print("== 5. 失败态(查询不存在的 run)")
code, _h, body = req("/embed/review?run=no_such_run_xyz")
# 同上:这里的关键词命中的是**页内 JS 源码**里的提示文案,不等于用户真看得见;
# 只用来确认页面没 500、没白屏壳,可见提示由 review_panel_probe.js 负责。
hint = [w for w in ("不存在", "未找到", "没有", "not found") if w in body]
print("  {} ;页内含提示词(源码级,非渲染级): {} ;页面字节 {}".format(
    code, hint or "无", len(body)))
