# -*- coding: utf-8 -*-
"""体检:静态质量扫描——硬编码敏感信息、死代码/TODO/残留引用、闲置资源。"""
import os, re

ROOTS = [
    r"D:\zzr\provenance\provenance",
]
# 子串匹配(SKIPS 里每一项都是路径子串,不是目录名精确比对):
# 第三方目录必须列全,否则"命中总数"把 site-packages 里的第三方代码算成我们的。
# 2026-10-07 实测:不加这几条时扫到 2072 个文件、其中 1667 个是第三方;
# localhost:port 的 22 命中里只有 8 处属于本仓。
# 已知副作用:".git" 这一条同时跳过 .github/(CI 配置不在本工具射程,要看用 git grep)。
SKIPS = ("node_modules", "dist", ".git", "__pycache__", "runs", "checkpoints", ".conda", "archive",
         ".venv", "site-packages", ".uv-cache", "egg-info")

def iter_files():
    for root in ROOTS:
        for dp, dn, fn in os.walk(root):
            if any(s in dp for s in SKIPS):
                continue
            for f in fn:
                if f.endswith((".py", ".json", ".html", ".js", ".yaml", ".yml",
                              ".toml", ".cfg", ".ini", ".env", ".md", ".txt")):
                    yield os.path.join(dp, f)

print("========== 1) 敏感信息/密钥 ==========")
sec_pat = re.compile(
    r"(password|passwd|api[_-]?key|secret|token|bearer|access[_-]?key|private[_-]?key)"
    r"\s*[=:]\s*['\"][^'\"\s]{6,}['\"]", re.I)
for p in iter_files():
    try:
        s = open(p, encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    # 排除依赖锁定/示例
    if "/node_modules/" in p or "/package-lock" in p or "/Pipfile.lock" in p:
        continue
    for m in sec_pat.finditer(s):
        print("  %s:%d  %s" % (p, s[: m.start()].count("\n") + 1, m.group(0)[:90]))
print("  (若上方为空/仅样例, 判定无敏感硬编码)")

print("\n========== 2) 死代码残留 / TODO / 硬编码端口 ==========")
leak_pat = [
    ("\bTODO\b|\bFIXME\b|\bHACK\b|\bXXX\b", "TODO/FIXME"),
    (r"127\.0\.0\.1:\d+", "硬编码127.0.0.1:port"),
    (r"localhost:\d+", "硬编码localhost:port"),
    (r"api_key\s*=\s*['\"].{4,}['\"]", "硬编码api_key"),
]
for pat, name in leak_pat:
    rxp = re.compile(pat)
    hits = 0
    for p in iter_files():
        if p.endswith(".json") and "/assets/" in p:
            continue
        try:
            s = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for m in rxp.finditer(s):
            hits += 1
            if hits <= 15:
                print("  [%s] %s:%d  %s" % (name, p, s[: m.start()].count("\n") + 1, m.group(0)[:70]))
    print("  [%s] 命中总数=%d" % (name, hits))

print("\n========== 3) 闲置 .bak/备份/重复资产 ==========")
for root in ROOTS:
    for dp, dn, fn in os.walk(root):
        if any(s in dp for s in SKIPS):
            continue
        for f in fn:
            low = f.lower()
            if low.endswith((".bak", ".orig", ".old", "~", ".tmp", ".sample",
                             ".backup", ".copy")):
                print("  %s" % os.path.join(dp, f))