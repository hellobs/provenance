# -*- coding: utf-8 -*-
"""P0 现状核对:嵌入面 / 数据面 的跨源与嵌入可用性,逐项出证据。

检查项(全部对 http://127.0.0.1:5010):
1. 六个 embed 面:HTTP 状态 + 是否带 X-Frame-Options / CSP(frame-ancestors);
2. 数据面:`/api/runs`、`/api/run-detail/review/<最近一条>`、`/health` 的 CORS 头;
3. 预检:`OPTIONS /api/runs` 带 Origin 时是否 200 且回 ACAO;
4. 深链:`/embed/review?run=<id>&tab=router` 是否 200 且页面里出现该 run_id;
5. 失败态:同一页面在"查不到 run"时是否有可见提示(不是白屏)。
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


# 最近一条成品记录(用于深链)
runs = sorted([d for d in os.listdir(os.path.join(PKG, "case01", "runs"))
               if os.path.isfile(os.path.join(PKG, "case01", "runs", d, "run.json"))])
rid = runs[-1] if runs else ""

print("== 1. 嵌入面(状态 / 嵌入阻断头)")
for face in ("/", "/embed/scene", "/embed/review", "/embed/explore",
             "/embed/goals", "/embed/timeline"):
    code, h, _ = req(face)
    bad = blocking(h)
    print("  {:<20} {:<4} {}".format(face, code, "阻断: " + ",".join(bad) if bad else "可嵌入 ✓"))

print("== 2. 数据面跨源头")
for path in ("/api/runs", "/health"):
    code, h, _ = req(path)
    print("  {:<28} {:<4} ACAO={!r} ACAC={!r}".format(
        path, code, hdr(h, "Access-Control-Allow-Origin"),
        hdr(h, "Access-Control-Allow-Credentials")))
if rid:
    code, h, _ = req("/api/run-detail/review/{}".format(rid))
    print("  {:<28} {:<4} ACAO={!r}".format("/api/run-detail/review/<id>", code,
                                            hdr(h, "Access-Control-Allow-Origin")))

print("== 3. 预检 OPTIONS /api/runs(带 Origin)")
code, h, _ = req("/api/runs", method="OPTIONS",
                 headers={"Origin": "http://localhost:3000",
                          "Access-Control-Request-Method": "GET"})
print("  {} ACAO={!r} ACAM={!r}".format(code, hdr(h, "Access-Control-Allow-Origin"),
                                        hdr(h, "Access-Control-Allow-Methods")))

print("== 4. 深链")
if rid:
    code, _h, body = req("/embed/review?run={}&tab=router".format(rid))
    print("  /embed/review?run={}&tab=router → {} ;页面含该 run_id: {}".format(
        rid[-12:], code, rid in body))
else:
    print("  无成品记录,跳过")

print("== 5. 失败态(查询不存在的 run)")
code, _h, body = req("/embed/review?run=no_such_run_xyz")
hint = [w for w in ("不存在", "未找到", "没有", "not found", "error", "失败") if w in body]
print("  {} ;可见提示词: {}".format(code, hint or "无(可能是白屏)"))
