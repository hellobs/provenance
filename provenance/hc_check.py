# -*- coding: utf-8 -*-
import urllib.request, re, sys

html = urllib.request.urlopen("http://127.0.0.1:5010/", timeout=10).read().decode("utf-8", "replace")
print("HTML 长度:", len(html))
print("含 '<script' 数:", html.count("<script"))
print("含 '</script>' 数:", html.count("</script>"))
print("含 'tileWall' 数:", html.count("tileWall"))
print("含 'colLayer' 数:", html.count("colLayer"))
# 找 tileWall 首次出现位置,看是否在某 <script>..</script> 内
i = html.find("tileWall")
print("tileWall 首个 index:", i)
if i >= 0:
    seg = html[max(0, i-400):i+500]
    print("=== tileWall 前 400 字符(找最近的 <script 标签) ===")
    m = re.findall(r"<script[^>]*>|</script>", html[max(0, i-3000):i])
    print("tileWall 之前最近的 script 标签序列(倒序3个):", m[-3:] if m else "无")
    # 统计 tileWall 之前 <script 与 </script> 数量差,判断是否在 script 内
    before = html[:i]
    opens = len(re.findall(r"<script[^>]*>", before))
    closes = len(re.findall(r"</script>", before))
    print("tileWall 之前: <script...> 开=%d, </script> 闭=%d -> 差=%d (1=在 script 内)" % (opens, closes, opens - closes))
    if "tileWall" in html:
        j = html.find("function tileWall")
        print("function tileWall 处上下文片断:", html[j:j+160].replace("\n", " ")[:160] if j >= 0 else "找不到")