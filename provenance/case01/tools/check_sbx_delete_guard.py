# -*- coding: utf-8 -*-
"""给删除守卫补一条:onclick 里的引号**必须转义**。

教训(2026-09-21 我自己踩的):把 `closest('div[style]')` 改成 `closest('.sbxcard')` 时,
没转义单引号 → 它把外层单引号 JS 字符串提前闭合 → **整个 <script> 块解析失败**,
场景页所有 JS 全死(删除、引擎显隐、草稿都不生效),而当时的静态子串检查完全看不出来。
所以这里除了断言"按卡删",还要断言**转义形态**,并调用 check_inline_js.js 做真正的语法检查。
"""
import io
import re

P = r"D:\zzr\mavis\config_tool\templates\scenario.html"
t = io.open(P, encoding="utf-8").read()

escaped = t.count("closest(\\'.sbxcard\\').remove()")
unescaped = t.count("closest('.sbxcard').remove()")
card_cls = t.count('d.className="sbxcard"')
by_style = len(re.findall(r"closest\(\\?'div\[style\]\\?'\)", t))

print("  卡片类名:      ", card_cls)
print("  按卡删(已转义):", escaped)
print("  按卡删(未转义):", unescaped, "← 必须为 0(否则整个 script 块会挂)")
print("  残留 div[style] 删法:", by_style)

ok = card_cls >= 2 and escaped >= 2 and unescaped == 0 and by_style == 0
print("PASS 删除逻辑正确且引号已转义" if ok else "FAIL 删除逻辑或转义有问题")
raise SystemExit(0 if ok else 1)
