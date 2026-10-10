# -*- coding: utf-8 -*-
"""文档体检:把所有 .md 的路径/最后改动/首行标题/是否已有状态行列出来。

用途(2026-09-21,用户:"项目里有很多过时的文件,加上日期或做修改,尤其 md"):
给"哪些文档该标状态/该退休"提供一份可核对的清单,并让 `--banner` 能批量补状态行。

用法(在 provenance/provenance 下):
    python tools/doc_audit.py                 # 只列
    python tools/doc_audit.py --banner        # 给缺状态行的 md 补一个**待填**状态行
    python tools/doc_audit.py --banner --check  # 只读:报告有几份 md 待规范化,不写盘
                                                 # (有待修时退出码 1;deep_check 用它,体检永不改工作树)

更新口径(2026-10-03 修):**状态值与说明没变就一个字都不写** —— 哪怕「最后核对」
日期是旧的。旧实现每次跑都用当天日期重建全部状态块,于是"每天第一次体检"必然
改动几十个 md(日期被刷成今天 = 谎报"今天核对过",还把工作树弄脏)。只有这几种
情况才重写:缺块、块重复(历史 bug 叠了多份)、状态/说明与 STATUS 表不一致。
"""
import argparse
import io
import os
import re
import time

ROOTS = (
    ("docs", "平台/架构文档"),
    ("case01/docs", "case01 文档"),
    (".", "仓库根散落文档"),
    # 2026-09-27 起纳入:治理盲区 = 仓库根 docs/(7 份)、packages/*/README.md、
    # tools/tilemap_to_maze_README.md 与两个仓库 README,此前都不在状态块守卫与索引内,
    # 而索引的维护约定写的是"本仓每份 .md"。这里直接 walk 上一级(realpath 去重会跳过
    # 上面三个已覆盖的根)。
    # 2026-10-08:仓库根那份 `docs/` 改名 `handbook/`(消掉"两个 docs/"的同名歧义:
    # 内层 docs/ 是应用文档,根 handbook/ 是框架/对接手册 + 四张录入模板)。
    ("..", "仓库根(handbook/ packages/ tools/ README)"),
)
# 说明:mavis 仓的 md **不在这里管** —— 那是另一个仓(有自己的 README/教程节奏),
# 本轮只给本仓的文档打状态,避免把自动状态块写进别人的仓库。

# 状态块 = 连续的若干 "> **…**" 行(可能因为历史 bug 叠了多份);
# 归一化时必须把**所有**这种行都删掉再插一条,否则越跑越多(2026-09-21 修)
BANNER_RE = re.compile(r"^>\s*\*\*(状态|Status|最后核对|说明)\*\*.*$", re.M)

# 标题行:Markdown 的 `#` 或**居中标题**用的 `<h1 align="center">…</h1>`
# (2026-10-01:两个 README 的项目名改成居中,状态块仍要认得出插在哪一行之后)
HEADING_RE = re.compile(r"^\s*(?:#|<h1[\s>])", re.I)

# 「状态」键的**整段**:键行 + 紧跟其后的 `>` 续行。
# 2026-09-27 修:只删键行的话,旧状态的续行会留在原地、接到新插上去的「说明」下面,
# 变成一句断掉的话(实测见 case01/docs/转接_给下一棒_20260924.md:旧状态是两行,
# 续行「中的阶段 3 与 LoRA 预准备均已完成,以新版为准)」被挂到了新说明底下)。
# 续行只认 `>` 开头的行,且不能是另一个键行 —— 避免把正文或下一个键吃掉。
STATE_PARA_RE = re.compile(
    r"^>\s*\*\*(?:状态|Status)\*\*[^\n]*"
    r"(?:\n>(?![ \t]*\*\*(?:状态|Status|最后核对|说明)\*\*)[^\n]*)*",
    re.M)

# 人工判断过的状态表(2026-09-21 核对)。key = 相对 here 的路径。
# 值 = (状态, 一句话说明)。没列到的按文件名模式兜底。
# **只登记仓内真实存在的文件**:2026-10-10 交付文档收缩后这里曾留着 37 条已退仓的路径
# (表里 69 条、盘上 32 个 —— 一张"看起来现行"的假清单)。退场文档的可读副本在仓外
# `GTC/archive/provenance_docs_261010/`,不登记、不生成状态块。
STATUS = {
    # —— 现行(权威) ——
    "docs/架构总览与对接指南.md": ("现行", "**治理平台对接的权威口径**(§11 嵌入面与数据接口;2026-09-21)"),
    "case01/docs/case01_触点白名单.md": ("现行", "case01 侧触碰 mavis 半公开面的白名单,越界要有登记"),
    "case00/README.md": ("现行", "case00 已冻结;只作对照与展示"),
    "case01/README.md": ("现行", "case01 子包说明(端口/入口/契约见 `../docs/` 现存 5 份交付文档)"),
    "case01/injector/README.md": ("现行", "注入器说明"),
    "../../mavis/README.md": ("现行", "mavis 框架说明(英文)"),
    "../../mavis/README_zh.md": ("现行", "mavis 框架说明(中文)"),
    "../../mavis/docs/tutorial-extension.md": ("现行", "接入方可依赖的稳定扩展面(与 1.2.1 对齐)"),
    "../../mavis/docs/tutorial-extension-en.md": ("现行", "扩展面教程(英文)"),
    "config_tool/README.md": ("现行", "provenance 内置的角色/场景/运行方式配置工具说明"),
    "config_tool/角色字段清单.md": ("现行", "config_tool 角色表单字段清单(必填/选填口径)"),
    # —— 仓库根 / 本地包 / 工具(2026-09-27 起纳入治理) ——
    # 这 12 份此前在 PKG 之外,既无状态块也不在索引里(见 0923 体检报告之后的补检)。
    # 只给结论明确的标状态;仓库根 docs/ 那 7 份需人工判断归属,先留「待核对」由索引点名。
    "../README.md": ("现行", "provenance 平台总览(英文);本仓入口"),
    "../README_zh.md": ("现行", "provenance 平台总览(中文);本仓入口"),
    "../packages/mavis-vizkit/README.md": ("现行", "可视化插件包说明(挂 mavis 插件面,与案例解耦)"),
    "../packages/mavis-case01-injector/README.md": ("现行", "case01 注入器包说明"),
    "../tools/tilemap_to_maze_README.md": ("现行", "Tiled 地图转 maze.json 的 CLI 工具说明"),
    "../tools/README.md": ("现行", "仓根 tools/ 与内层 provenance/tools/ 的分工(装新机/起面/打包/搬迁 vs 体检/探针/审计)"),
    "tools/README.md": ("现行", "内层 tools/ 的体检与探针工具清单(含与仓根 tools/ 的边界)"),
    # —— 已被取代 ——
    "case01/docs/Governance平台对接说明.md": ("参考(5002 面)", "5002 历史参考；对接使用 5010 契约，审核执行规则以《平台对接契约_5010唯一入口》§三/§四 为准"),
    "docs/平台对接契约_5010唯一入口.md": ("现行", "**交给治理平台的唯一口径**:嵌入面 + 数据接口 + 质检口径 + 信息边界 + 运维(2026-09-21 起)"),
    "case01/docs/转接_给下一棒_20260926.md": ("现行", "**当前权威交接**:基线 + 四个方向(行为层证据/GTC 演示/工程收尾/LoRA)+ 坑清单"),
    "case01/docs/LoRA预准备_20260924.md": ("现行", "LoRA 线预准备:标记→训练 JSONL 管道+校验门已就绪(0 标记,闸门在专家数据侧);环境体检与 Runbook"),
    "case01/docs/内化与敏感性_定量分析_20260924.md": ("现行", "IVD 定量证据第一批:内化位移 11/12 为正、双模型 Router 敏感性、人工校验框架;工具在 case01/tools/"),
    # 2026-10-08:这条必须与 `case01/docs/金融背景资料库.md` 文首的状态行**逐字一致**——
    # needs_banner 只比 状态/说明 两个值,表里没有这份文件就按兜底 ("待核对","尚未人工核对状态")
    # 判,于是作者写的"现行"永远对不上,doc_audit --check 直接 rc=1(实测合并 origin/main 后红的那条)。
    "case01/docs/金融背景资料库.md": ("现行", "100 条外部公司资料的导入、可选检索、分段和审计方式。"),
    "case01/docs/问题清单_20260925.md": ("部分过时", "**现行但部分过时**:六条待修问题清单;问题 2/3/4/6 已修、问题 1 已缓解(待 Branch C 重录)、问题 5 待外部数据(状态见文首)"),
    "docs/核验主张与证据说明书.md": ("草稿", "GTC 10/15 提交件:五部分(项目概况/评估标准/演示计划/主张登记表/主张与证据对应)。数字全部实测可复跑;含【待填】团队信息与专家署名,提交前删〇节内部须知"),
    "docs/10月15日演示_运行手册.md": ("现行", "GTC 10/15 现场演示操作手册:启动(一条命令 tools/serve_all.py / 二方案)/三标签/15 分钟走查/故障回退(三级兜底)/口径话说(评委追问标准答法);命令一律 PowerShell 写法"),
    "case01/docs/GTC侧文档口径补充_拟稿_20260918.md": ("待人工贴入", "拟稿,等研究侧确认后贴进 0904doc 三份 .docx(不可回滚,不在本仓改)"),
    "case01/docs/case01_可插拔可视化设计.md": ("参考", "可插拔可视化设计(仍有效)"),
    "case01/docs/框架层级图_LLM接入节点.md": ("参考", "框架层级与 LLM 接入节点;仍有效"),
    "case01/injector/scenario/README.md": ("现行", "注入器场景(生成物)说明"),
    "case00/scenario/README.md": ("现行", "case00_village 的运行期素材说明(关系/剧情);2026-10-08 从 `scenarios/investment/` 归并到 cases/ 下"),
}

# 文件名模式兜底
PATTERN_STATUS = (
    ("任务_", ("历史存档", "任务派发单:任务已完成,留作过程记录")),
    ("tutorial-", ("待核对", "mavis 教程,需按当前版本核一遍")),
)


def _status_for(rel):
    if rel in STATUS:
        return STATUS[rel]
    base = os.path.basename(rel)
    for pat, val in PATTERN_STATUS:
        if base.startswith(pat):
            return val
    return ("待核对", "尚未人工核对状态")


# 仓库根两个 README 是**入口文档**,不是审计产物,所以:
#   - 不带「最后核对」日期(2026-10-01 起;日期给不了读者任何东西,却要天天刷新);
#   - **各用自己的语言** —— 英文版挂一句中文说明,读者会当成漏改(2026-10-01 反馈)。
# 其余文档一律三行中文,理由见索引「三、维护约定」。
README_BANNER = {
    "../README.md": "> **Status**:current — Provenance platform overview (English); the entry point of this repository",
    "../README_zh.md": "> **状态**:现行\n> **说明**:provenance 平台总览(中文);本仓入口",
}


def banner_text(rel, date):
    if rel in README_BANNER:
        return README_BANNER[rel] + "\n"
    state, note = _status_for(rel)
    return ("> **状态**:{}\n"
            "> **最后核对**:{}\n"
            "> **说明**:{}\n").format(state, date, note)


def _banner_fields(txt):
    """从文件头提取现有的 (状态行列表, 规范日期行列表, 说明行列表)。

    只看前 1500 字符 —— 状态块按约定就在标题正下方。
    """
    head = txt[:1500]
    states = re.findall(r"^>\s*\*\*(?:状态|Status)\*\*:\s*(.+?)\s*$", head, re.M)
    dates = re.findall(r"^>\s*\*\*最后核对\*\*:\s*(\d{4}-\d{2}-\d{2})\s*$", head, re.M)
    notes = re.findall(r"^>\s*\*\*说明\*\*:\s*(.+?)\s*$", head, re.M)
    return states, dates, notes


def normalize_banner(txt, rel, date):
    """把状态块规范化(旧块整段删、在标题后插一块),返回新文本(纯函数,不写盘)。"""
    block = banner_text(rel, date).rstrip("\n")
    txt2 = STATE_PARA_RE.sub("", txt)                 # 旧的「状态」键**连续行**整段删
    txt2 = BANNER_RE.sub("", txt2)                    # 其余旧状态行(可能叠了好几份)全删
    txt2 = re.sub(r"\n{3,}", "\n\n", txt2)            # 别留下大片空行
    lines = txt2.split("\n")
    ins = 0
    for i, line in enumerate(lines[:12]):
        if HEADING_RE.match(line):
            ins = i + 1
            break
    # 标题后面紧跟的空行也算进来,保证只留一个空行
    while ins < len(lines) and not lines[ins].strip():
        lines.pop(ins)
    # 状态块后面也补一个空行:否则它和正文第一段会被看成同一段(2026-10-01)
    lines[ins:ins] = [""] + block.split("\n") + [""]
    return "\n".join(lines)


def needs_banner(txt, rel):
    """判断这份 md 的状态块是否**真的需要**重写(2026-10-03 改口径)。

    旧逻辑是"重建后的文本与现状不同就要写",而重建必然带上当天日期,于是每份带
    日期的文档**每天**都被判成要改 —— 体检脚本因此天天弄脏工作树、还谎报核对日期。

    新口径:
    - 普通文档:现有块恰好一份(状态/日期/说明各一键行),且状态值、说明与 STATUS 表
      一致 → 不需要改,**旧日期保留**(日期只在人工/工具真改状态时才随重写刷新);
    - README(无日期的固定块):规范化文本与现状逐字一致才不需要改;
    - 缺块、块重复(键行多出来)、值不一致 → 需要重写。
    """
    if rel in README_BANNER:
        return normalize_banner(txt, rel, "") != txt
    states, _dates, notes = _banner_fields(txt)
    tgt_state, tgt_note = _status_for(rel)
    if len(states) != 1 or len(notes) != 1:
        return True                       # 缺块或叠了多份
    return states[0] != tgt_state or notes[0] != tgt_note


def rewrite_if_needed(path, rel, date, write):
    """需要规范化就重建;write=True 落盘。返回"是否(会)被改动"。"""
    with io.open(path, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    if not needs_banner(txt, rel):
        return False
    txt3 = normalize_banner(txt, rel, date)
    if txt3 == txt:
        return False
    if write:
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(txt3)
    return True


SKIP_DIRS = (".git", "node_modules", "__pycache__", "venv", "_shared",
             ".pytest_cache", ".uv-cache", ".audit-tmp", "results", "runs", "runs_injector",
             "runs_html",
             # 2026-10-05:`runs_archive_*` 不再逐个列举 —— 统一由下面 walk 里的
             # `d.startswith("runs_archive")` 前缀过滤覆盖,与 tests/test_docs_status.py
             # 同一机制(此前这里写死 `runs_archive_20260919`,再加新归档目录就漏筛)。
             # 2026-10-06:venv 同样改成前缀 —— 下面 `d.startswith(".venv")`。
             # 此前只写死 `.venv`/`.venv-live`,于是 `tools/setup_all.py --venv-name` 建的
             # 自定义 venv 里 site-packages/**/LICENSE.md 会被列进"文档待核对"清单。
             # 2026-10-03 加:.workbuddy 是 agent 工具的工作区(记忆/日志/草稿),
             # 不是本仓文档 —— 记忆文件没有状态块,把它们按项目文档治理会红测试
             # (test_docs_status)。工具目录与项目文档分开管。
             # 2026-10-07 同理加 .trae(Trae IDE 的工作区)。实测:别人落进来的
             # ../.trae/documents/*.md、../.trae/skills/*/SKILL.md 会让外层
             # tests/test_docs_status.py 本地红(CI 绿,因为 CI 机器上没这目录)。
             # 注意 .gitignore 管不到这里 —— 本函数按文件系统 walk,不按 git 索引。
             ".workbuddy", ".trae")


def md_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not d.startswith("runs_archive")
                   and not d.startswith(".venv")]
        dirs.sort()   # 目录顺序也钉住:否则日期并列的行随 scandir 顺序漂移,产生假 diff
        for fn in sorted(files):
            if fn.lower().endswith(".md"):
                out.append(os.path.join(dirpath, fn))
    return out


def first_heading(path):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            for _ in range(12):
                line = f.readline()
                if not line:
                    break
                s = line.strip()
                if s.startswith("#"):
                    return s.lstrip("# ").strip()[:60]
    except OSError:
        pass
    return ""


def git_last_dates(here):
    """path(相对**仓库根**, posix) -> 该文件**最后一次提交**的日期。

    为什么不用 mtime:给文档补状态块会刷新 mtime,那样 index 里"最后改动"会全变成今天,
    反而把"内容多久没动过"这个信号抹掉了。

    `-c core.quotepath=false` 必须加(2026-10-05 修 N6 深层根因):默认 quotepath=true 时
    git 会把非 ASCII 路径转义成八进制并加**外层双引号**(`"provenance/docs/GTC\\347\\240..."`),
    于是**所有中文文档都匹配不上**、只能回退 mtime —— 索引上"今天核对过"其实是 mtime 假象。

    **不要加 `"--", "."` 路径限制**(2026-10-08 修):`cwd=here` 是内层包根 `<仓>/provenance`,
    `.` 只覆盖这一棵子树,而 `git log --name-only` 输出的是**仓库根相对**路径,于是仓库根那半边
    (`README*.md`、`tools/`、`packages/`、`handbook/`)一个键都进不来 —— 实测 950 个键**全部**以
    `provenance/` 开头。后果不是"少个日期"而是**系统性撒谎**:索引表头写明"本仓全仓都在这里管",
    而 `cdate()` 查不到就标 `未提交`,读者据此以为这 10 份根级文档没入库,实际 git 都答得上日期。
    不加路径限制即覆盖全仓;键本来就是仓库根相对,`cdate()` 的 `rel_to_root` 直接对上。
    """
    import subprocess
    out = {}
    try:
        p = subprocess.run(["git", "-c", "core.quotepath=false",
                            "log", "--date=short", "--pretty=format:%ad",
                            "--name-only"],
                           cwd=here, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        cur = ""
        for line in (p.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}$", line):
                cur = line
            elif cur:
                # 极旧版 git 即便关了 quotepath 也可能留外层引号:去一层
                name = line[1:-1] if line.startswith('"') and line.endswith('"') else line
                key = name.replace("\\", "/")
                # git log 按**新→旧**遍历:第一次遇到即最新那次提交,别被更早的覆盖
                out.setdefault(key, cur)
    except Exception:  # noqa: BLE001 - 拿不到就退回 mtime
        return {}
    return out


def git_repo_root(here):
    """`here` 所属仓库的**根目录**(绝对)。取不到返回 here 本身。"""
    import subprocess
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=here,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        top = (p.stdout or "").strip()
        return top or here
    except Exception:  # noqa: BLE001
        return here


def main():
    ap = argparse.ArgumentParser(description=".md 文档体检(列清单 / 补状态行)")
    ap.add_argument("--banner", action="store_true", help="按 STATUS 表给 md 补/更新状态块")
    ap.add_argument("--check", action="store_true",
                    help="与 --banner 同口径但**只读**:报告待规范化的 md 数,不写盘;"
                         "有待修时退出码 1(供体检/CI;永不改工作树)")
    ap.add_argument("--index", action="store_true", help="生成 docs/文档索引.md(状态一览)")
    ap.add_argument("--index-out", default="",
                    help="配合 --index:把索引写到这个绝对路径而不是 docs/文档索引.md"
                         "(给测试用,免得跑一次测试就改写真实入库索引)")
    ap.add_argument("--banner-all", action="store_true",
                    help="连同 `--banner`:把没在 STATUS 表里的也补上「待核对」状态块")
    args = ap.parse_args()

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = []
    seen = set()
    for rel, label in ROOTS:
        root = os.path.join(here, rel)
        for p in md_files(root):
            real = os.path.realpath(p)
            if real in seen:            # ROOTS 之间有包含关系(如 case01/docs ⊂ .),去重
                continue
            seen.add(real)
            st = os.stat(p)
            rows.append((os.path.relpath(p, here).replace("\\", "/"), label,
                         time.strftime("%Y-%m-%d", time.localtime(st.st_mtime)),
                         st.st_size, first_heading(p),
                         bool(BANNER_RE.search(io.open(p, encoding="utf-8",
                                                        errors="replace").read()[:1200]))))
    rows.sort(key=lambda r: r[2], reverse=True)      # 最近改的排前面
    print("共 {} 个 .md\n".format(len(rows)))
    print("{:<58} {:<10} {:>7} {:<6} {}".format("路径", "最后改动", "KB", "有状态", "标题"))
    for path, _label, date, size, head, has in rows:
        print("{:<58} {:<10} {:>7.1f} {:<6} {}".format(
            path[:58], date, size / 1024.0, "是" if has else "-", head))

    if args.banner or args.check:
        today = time.strftime("%Y-%m-%d")
        write = not args.check
        changed_paths = []
        for path, _label, _date, _size, _head, _has in rows:
            if rewrite_if_needed(os.path.join(here, path), path, today, write):
                changed_paths.append(path)
        n = len(changed_paths)
        if args.check:
            print("\n待更新 {} 个 md 的状态块(只读 --check,未写盘;跑 --banner 修复)".format(n))
            for p in changed_paths[:20]:
                print("  · {}".format(p))
            if len(changed_paths) > 20:
                print("  · … 其余 {} 个".format(len(changed_paths) - 20))
            return 1 if n else 0
        print("\n已更新 {} 个 md 的状态块(状态取自 STATUS 表 / 模式兜底)".format(n))

    if args.index:
        # --index-out 写别处(2026-10-05 第九审计 N9-6):--index 是**生成物**命令,
        # 默认落 `docs/文档索引.md` —— 也就是真实入库的那份。测试若图省事直接跑它,
        # 等于"跑一次测试就写一次真实索引":`finally` 存档还原只挡得住异常,
        # 挡不住进程被杀/断电,也挡不住两个会话并行跑 pytest 时互相覆盖
        # (A 存档 → B 改写 → A 还原 → B 的写入被抹掉)。给测试一条写临时路径的出口,
        # 从此不必碰真实索引。`cdate()` 仍读真实 git 历史(那是它的语义来源,搬不走)。
        idx = os.path.abspath(args.index_out) if args.index_out else \
            os.path.join(here, "docs", "文档索引.md")
        gdates = git_last_dates(here)
        repo_root = git_repo_root(here)

        def cdate(path):
            """内容日期 = **最后一次提交该文件的日期**;查不到就如实标注,不冒充。

            2026-10-05 修(GTC 体检 N6):此前匹配失败会**静默回退 `os.stat().st_mtime`**,
            正是表头点名要避免的那种回退 —— 后果是索引给《GTC研究边界声明》标 2026-10-05
            (mtime),而 `git log -1` 实际是 2026-10-03,把"这页纸多久没核对过"的信号抹掉了。
            根因有二:① `git log --name-only` 输出的是**仓库根相对**路径(`provenance/docs/x.md`),
            而 `path` 是**相对 here** 的(`docs/x.md`),候选里的 `"provenance/" + path`
            才对得上,`path` 本身几乎永不命中,于是回退成了常态;② 回退本身不报错。
            现在:先按"仓库根相对"正解匹配,命中不了再依次试旧候选;
            全都命中不了就返回 `未提交`(新文件 / 未入库),**绝不用 mtime 冒充提交日**。
            """
            rel_to_root = os.path.relpath(os.path.join(here, path), repo_root).replace("\\", "/")
            for cand in (rel_to_root, path, "provenance/" + path,
                         path.replace("../", "", 1)):
                if cand in gdates:
                    return gdates[cand]
            return "未提交"

        cur = [r for r in rows if _status_for(r[0])[0] == "现行"]
        other = [r for r in rows if r not in cur]
        out = ["# 文档索引(状态一览)", "",
               banner_text("docs/文档索引.md", time.strftime("%Y-%m-%d")).rstrip("\n"),
               "> **用途**:一眼看出哪些文档是现行口径、哪些已被取代。每份 md 顶部也有同样的状态块。",
               "> **「内容日期」= 最后一次提交该文件的日期**(不是文件 mtime:补状态块会刷新 mtime,"
               "那样会把「内容多久没动过」这个信号抹掉;查不到提交日期时标 `未提交`,不冒充)。"
               "**本仓全仓都在这里管**(含仓库根 `docs/`、"
               "`packages/`、`tools/` 与两个 README;2026-09-27 起);mavis 仓的文档不在这里管。",
               "",
               "## 一、现行(以这些为准)", "",
               "| 文件 | 内容日期 | 说明 |", "| --- | --- | --- |"]
        for path, _l, _date, _s, _h, _has in cur:
            out.append("| `{}` | {} | {} |".format(path, cdate(path), _status_for(path)[1]))
        out += ["", "## 二、其余(已被取代 / 历史存档 / 参考 / 待核对)", "",
                "| 状态 | 文件 | 内容日期 | 说明 |", "| --- | --- | --- | --- |"]
        for path, _l, _date, _s, _h, _has in other:
            st, note = _status_for(path)
            out.append("| {} | `{}` | {} | {} |".format(st, path, cdate(path), note))
        out += ["", "## 三、维护约定", "",
                "1. 文档顶部必须有状态块:一般文档是 `> **状态**:` / `> **最后核对**:` / `> **说明**:` 三行;"
                "仓库根两个 README 是入口文档,只留 `> **状态**:` / `> **说明**:` 两行(不带日期);",
                "2. 口径变了就**改状态并写明被谁取代**,不要删文件(评审要能追);",
                "3. 新增文档后跑一次 `python tools/doc_audit.py --banner --index`;",
                "4. `task_*` / `任务_*` 这类派发单完成后一律标「历史存档」。"]
        # 目标目录可能不存在(测试给的临时路径),建出来而不是让调用方自己造
        _d = os.path.dirname(idx)
        if _d and not os.path.isdir(_d):
            os.makedirs(_d, exist_ok=True)
        with io.open(idx, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(out) + "\n")
        print("已写 {}".format(idx))


if __name__ == "__main__":
    import sys
    sys.exit(main())
