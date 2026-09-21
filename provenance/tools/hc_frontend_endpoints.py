# -*- coding: utf-8 -*-
"""体检:抓前端(main_script/index)里 fetch/XMLHttpRequest/WebSocket 端点,列出供与后端路由比对。"""
import re, os

files = [
    r"D:\zzr\provenance\provenance\frontend\templates\main_script.html",
    r"D:\zzr\provenance\provenance\frontend\templates\index.html",
    r"D:\zzr\provenance\provenance\frontend\templates\base.html",
]
urls = {}
for f in files:
    if not os.path.exists(f):
        continue
    s = open(f, encoding="utf-8").read()
    for m in re.finditer("fetch\\((['\"`])([^'\"`]+)\\1|new\\s+WebSocket\\s*\\((?:['\"`])([^'\"`]+)", s):
        u = m.group(2) or m.group(3)
        # 只取相对路径/api/路径,跳过协议占位与纯变量
        if u and u.startswith(("/", "ws")):
            u = re.split(r"[?#]", u)[0]
            urls.setdefault(u, []).append((os.path.basename(f), s[:m.start()].count("\n") + 1))
for u in sorted(urls):
    print("%s   <-- %s" % (u, urls[u][:3]))
print("=== 动态拼接的端点(含模板变量/引,需人工判断) ===")
for f in files:
    if not os.path.exists(f):
        continue
    s = open(f, encoding="utf-8").read()
    for m in re.finditer("fetch\\([^\\n]*\\+|await fetch\\([^\\n]*\\$\\{|fetch\\((`|')/api[^\\n]*\\{", s):
        print("  %s:%d: %s" % (os.path.basename(f), s[:m.start()].count("\n") + 1, m.group(0).strip()[:110]))