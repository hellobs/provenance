# -*- coding: utf-8 -*-
"""深度体检:一次跑完所有维度,输出紧凑的 ✓/⚠ 清单(2026-09-21 第二轮)。

维度:仓库状态 / 四套测试 / 六面服务 / 数据面 / 契约面 / IVD 纯度 / 文档 / 守卫脚本 /
场景碰撞与连通 / 未收口项。只做**事实收集**,不改任何东西。
"""
import glob
import io
import json
import os
import re
import subprocess
import sys
import urllib.request

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # provenance/provenance
REPO = os.path.dirname(PKG)                                               # D:\zzr\provenance
MAVIS = os.path.join(os.path.dirname(REPO), "mavis")
PY = sys.executable
ISSUES = []


def say(tag, ok, text):
    mark = "✓" if ok else "⚠"
    if not ok:
        ISSUES.append("{}: {}".format(tag, text))
    print("  {} [{}] {}".format(mark, tag, text))


def sh(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=True)
    return (p.stdout or "") + (p.stderr or "")


def http(url, timeout=6):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, "{}: {}".format(type(exc).__name__, exc)


print("=" * 88)
print("A. 仓库状态")
for name, path in (("provenance", REPO), ("mavis", MAVIS)):
    st = sh("git status --short", path)
    dirty = len([l for l in st.splitlines() if l.strip()])
    br = sh("git rev-list --left-right --count origin/main...HEAD", path).split()
    ahead = br[1] if len(br) > 1 else "?"
    say("git", dirty == 0 or name == "mavis", "{}: 未提交 {} 个文件, 领先 origin {}".format(
        name, dirty, ahead))

print("=" * 88)
print("B. 四套测试")
suites = [
    ("case_engine+case01", "case_engine/tests case01/tests", PKG),
    ("vizkit", "", os.path.join(REPO, "packages", "mavis-vizkit")),
    ("outer", "tests", REPO),
    ("mavis", "", os.path.join(MAVIS, "tests")),
]
import tempfile
import xml.etree.ElementTree as ET

for name, paths, cwd in suites:
    xml = os.path.join(tempfile.gettempdir(), "deep_{}.xml".format(name.replace("+", "_")))
    if os.path.exists(xml):
        os.remove(xml)
    cmd = '"{}" -m pytest {} -q --tb=no -p no:cacheprovider --junit-xml="{}"'.format(
        PY, paths, xml)
    sh(cmd, cwd)
    try:
        root = ET.parse(xml).getroot()
        cases = root.findall(".//testcase")
        bad = [c.get("name") for c in cases if c.find("failure") is not None
               or c.find("error") is not None]
        skipped = [c for c in cases if c.find("skipped") is not None]
        say("tests", not bad, "{}: {} 条 / 跳过 {} / 失败 {}".format(
            name, len(cases), len(skipped), len(bad) or 0))
    except Exception as exc:  # noqa: BLE001
        say("tests", False, "{}: junit 解析失败 {}: {}".format(name, type(exc).__name__, exc))

print("=" * 88)
print("C. 六面服务")
faces = [("5010 实时面", "http://127.0.0.1:5010/health"),
         ("5002 契约", "http://127.0.0.1:5002/api/runs"),
         ("5003 case00 存档", "http://127.0.0.1:5003/api/runs"),
         ("5004 多场景", "http://127.0.0.1:5004/api/scenarios"),
         ("5020 布景", "http://127.0.0.1:5020/health"),
         ("8060 配置工具", "http://127.0.0.1:8060/engines")]
for name, url in faces:
    code, body = http(url)
    say("service", code == 200, "{}: HTTP {}".format(name, code))

print("=" * 88)
print("D. 数据面")
sys.path.insert(0, PKG)
from case01.full_context import quality_of                                   # noqa: E402

rows = []
for p in sorted(glob.glob(os.path.join(PKG, "case01", "runs", "*", "run.json"))):
    d = json.load(io.open(p, encoding="utf-8"))
    q = quality_of(d)
    iss = ((d.get("router") or {}).get("issues")) or []
    bad = [i for i in iss if not (i.get("field") or "").strip()
           or not (i.get("risk_note") or "").strip()]
    rows.append((os.path.basename(os.path.dirname(p)), q["quality"],
                 bool(((d.get("reflection") or {}).get("text") or "").strip()), len(iss), len(bad)))
say("data", all(r[4] == 0 for r in rows), "{} 条成品:router 缺字段 {} 条".format(
    len(rows), sum(1 for r in rows if r[4])))
say("data", all(r[2] for r in rows), "无反思的成品:{} 条".format(sum(1 for r in rows if not r[2])))
dist = {}
for r in rows:
    dist[r[1]] = dist.get(r[1], 0) + 1
say("data", True, "质检分布:{}".format(dist))

print("=" * 88)
print("E. 契约面")
code, body = http("http://127.0.0.1:5010/api/runs")
if code == 200:
    j = json.loads(body)
    say("contract", "filter" in j and ("excluded" in j or not any(
        x.get("quality") in ("questionable", "debug") for x in j["runs"])),
        "5010 聚合:count={} excluded={} filter={}".format(
            j.get("count"), (j.get("excluded") or {}).get("count"), "filter" in j))
code, body = http("http://127.0.0.1:5010/api/runs?include_questionable=1")
if code == 200:
    j2 = json.loads(body)
    say("contract", j2.get("count", 0) >= j.get("count", 0),
        "include_questionable=1 → count={}".format(j2.get("count")))
sample = sorted(glob.glob(os.path.join(PKG, "case01", "runs", "*", "run.json")))[-1]
rid = os.path.basename(os.path.dirname(sample))
code, body = http("http://127.0.0.1:5010/api/run-detail/review/{}".format(rid))
if code == 200:
    j = json.loads(body)
    leak = [k for k in ("injector", "branch_action", "consistency", "debug", "quality")
            if k in (j.get("data") or {})]
    say("contract", j.get("view") == "expert-safe" and not leak,
        "run-detail 默认视图={} 泄漏={}".format(j.get("view"), leak or "无"))
code, body = http("http://127.0.0.1:5002/api/runs/{}/full-context".format(rid))
if code == 200:
    bad = [k for k in ("分支来源", "预设分支", "injector", "branch_action") if k in body]
    say("contract", not bad, "full-context 实验元信息:{}".format(bad or "无"))
yaml_p = os.path.join(PKG, "case01", "docs", "case01_api.openapi.yaml")
y = io.open(yaml_p, encoding="utf-8").read() if os.path.isfile(yaml_p) else ""
say("contract", all(k in y for k in ("quality", "include_questionable", "excluded")),
    "契约 YAML 含 quality/include_questionable/excluded")

print("=" * 88)
print("F. IVD 纯度")
code_hits, doc_hits = [], []
import tokenize as _tok
_ROOT = os.path.join(MAVIS, "mavisframework")
_word = re.compile("投资|股票|治理|审批|案例|HCM|Ethan|Investment AI")
for dp, dn, fn in os.walk(_ROOT):
    dn[:] = [d for d in dn if d not in ("__pycache__", "build")]
    for f in fn:
        if not f.endswith(".py"):
            continue
        src = io.open(os.path.join(dp, f), encoding="utf-8", errors="replace").read()
        docs = set()
        try:
            for tok in _tok.generate_tokens(io.StringIO(src).readline):
                if tok.type == _tok.STRING:
                    for ln in range(tok.start[0], tok.end[0] + 1):
                        docs.add(ln)
        except Exception:  # noqa: BLE001
            pass
        for i, line in enumerate(src.split("\n"), 1):
            if not _word.search(line):
                continue
            (doc_hits if (line.strip().startswith("#") or i in docs) else code_hits).append(
                (os.path.relpath(os.path.join(dp, f), MAVIS), i))
say("ivd", not code_hits,
    "mavis 真代码里的业务词:{} 处;注释/文档串 {} 处(允许)".format(len(code_hits), len(doc_hits)))
rev = sh('git grep -n -E "case01|case_engine|provenance" -- mavisframework/', MAVIS)
say("ivd", not rev.strip(), "mavis 反向依赖案例层:{} 处".format(len(rev.splitlines())))
ct = sh('git grep -c -E "provenance|case_engine" -- config_tool/', MAVIS)
say("ivd", not ct.strip(), "config_tool 反向依赖(已知未收口):{} 个文件命中".format(len(ct.splitlines())))
say("ivd", os.path.isfile(os.path.join(PKG, "case_engine", "tests", "test_ivd_invariants.py")),
    "IVD 不变量守卫存在")

print("=" * 88)
print("G. 文档面")
mds = []
_DOC_SKIP = (".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", ".venv-live",
             "runs", "results", "runs_html", "_pages", ".uv-cache", "build", "runs_injector")
for root, dirs, files in os.walk(PKG):
    dirs[:] = [d for d in dirs if d not in _DOC_SKIP and not d.startswith("runs_archive")]
    for fn in files:
        if fn.endswith(".md"):
            mds.append(os.path.join(root, fn))
no_banner = [os.path.relpath(p, PKG) for p in mds
             if "> **状态**" not in io.open(p, encoding="utf-8", errors="replace").read(1500)]
say("docs", not no_banner, "{} 份 md,缺状态块 {} 份".format(len(mds), len(no_banner)))
dups = []
for p in mds:
    t = io.open(p, encoding="utf-8", errors="replace").read(4000)
    if len(re.findall(r"^> \*\*状态\*\*", t, re.M)) > 1:
        dups.append(os.path.relpath(p, PKG))
say("docs", not dups, "状态块重复的文件:{}".format(dups or "无"))
idx = os.path.join(PKG, "docs", "文档索引.md")
say("docs", os.path.isfile(idx), "文档索引存在")

print("=" * 88)
print("H. 守卫脚本")
guards = [
    ("内联 JS 语法(配置工具)", [PY, os.path.join(PKG, "case01", "tools", "check_inline_js.js")],
     None),
]
node = sh("node --version")
say("guard", node.strip().startswith("v"), "node 可用:{}".format(node.strip()[:12]))
g1 = sh('node "{}" "http://127.0.0.1:8060/scenario"'.format(
    os.path.join(PKG, "case01", "tools", "check_inline_js.js")))
say("guard", "OK" in g1 and "语法错误" not in g1, "配置工具内联 JS 语法")
g2 = sh('node "{}" "{}"'.format(os.path.join(PKG, "case01", "tools", "test_engine_toggle.js"),
                                os.path.join(MAVIS, "config_tool", "templates", "scenario.html")))
say("guard", "全部通过" in g2, "引擎显隐逻辑(三态)")
g3 = sh('"{}" "{}"'.format(PY, os.path.join(PKG, "case01", "tools", "check_sbx_delete_guard.py")))
say("guard", "PASS" in g3, "沙盒卡片删除+引号转义")

print("=" * 88)
print("I. 场景碰撞与连通")
for name, maze, agents_dir in (
        ("case00", os.path.join(PKG, "case00", "scenario", "maze.json"),
         os.path.join(PKG, "case00", "scenario", "agents")),
        ("case01", os.path.join(PKG, "case01", "injector", "scenario", "maze.json"),
         os.path.join(PKG, "case01", "injector", "scenario", "agents"))):
    m = json.load(io.open(maze, encoding="utf-8"))
    tiles = {}
    for x in m.get("tiles") or []:
        c = x.get("coord")
        if c:
            tiles[(int(c[0]), int(c[1]))] = x
    walk = {c for c, x in tiles.items() if not x.get("collision")}
    seen, sizes = set(), []
    for s in walk:
        if s in seen:
            continue
        stack, n = [s], 0
        seen.add(s)
        while stack:
            x, y = stack.pop()
            n += 1
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb in walk and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        sizes.append(n)
    sp_bad = []
    for d in sorted(os.listdir(agents_dir)):
        p = os.path.join(agents_dir, d, "agent.json")
        if not os.path.isfile(p):
            continue
        c = json.load(io.open(p, encoding="utf-8")).get("coord")
        if not (isinstance(c, list) and len(c) >= 2):
            continue
        c = (int(c[0]), int(c[1]))
        if c not in walk:
            sp_bad.append(d)
    say("scene", len(sizes) == 1 and not sp_bad,
        "{}: 可走 {} 格 / {} 块;出生点在墙里的角色 {}".format(
            name, len(walk), len(sizes), sp_bad or "无"))

inst = os.path.join(PKG, "frontend", "static", "assets", "village", "maze.json")
if os.path.isfile(inst):
    m = json.load(io.open(inst, encoding="utf-8"))
    walk = {(int(x["coord"][0]), int(x["coord"][1])) for x in m.get("tiles") or []
            if x.get("coord") and not x.get("collision")}
    seen, sizes = set(), []
    for s in walk:
        if s in seen:
            continue
        stack, n = [s], 0
        seen.add(s)
        while stack:
            x, y = stack.pop()
            n += 1
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb in walk and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        sizes.append(n)
    say("scene", len(sizes) == 1,
        "装好的 scene(static):可走 {} 格 / {} 块(碎块=可能有走不到的死区)".format(
            len(walk), len(sizes)))

print("=" * 88)
print("J. 工具幂等 / 未收口")
a = sh('"{}" -X utf8 "{}" --banner'.format(PY, os.path.join(PKG, "tools", "doc_audit.py")))
b = sh('"{}" -X utf8 "{}" --banner'.format(PY, os.path.join(PKG, "tools", "doc_audit.py")))
say("tool", "已更新 0 个" in b, "doc_audit --banner 幂等(第二遍: {})".format(
    b.strip().splitlines()[-1] if b.strip() else "?"))
mavis_dirty = len([l for l in sh("git status --short", MAVIS).splitlines() if l.strip()])
say("open", mavis_dirty == 0, "mavis 仓未提交 {} 个文件(config_tool 与那位 agent 的在途改动)".format(
    mavis_dirty))

print("=" * 88)
print("结论:{} 个 ⚠".format(len(ISSUES)))
for i, s in enumerate(ISSUES, 1):
    print("  {}. {}".format(i, s))
