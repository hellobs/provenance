# -*- coding: utf-8 -*-
"""文档体检:把所有 .md 的路径/最后改动/首行标题/是否已有状态行列出来。

用途(2026-09-21,用户:"项目里有很多过时的文件,加上日期或做修改,尤其 md"):
给"哪些文档该标状态/该退休"提供一份可核对的清单,并让 `--banner` 能批量补状态行。

用法(在 provenance/provenance 下):
    python tools/doc_audit.py                 # 只列
    python tools/doc_audit.py --banner        # 给缺状态行的 md 补一个**待填**状态行
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
)
# 说明:mavis 仓的 md **不在这里管** —— 那是另一个仓(有自己的 README/教程节奏),
# 本轮只给本仓的文档打状态,避免把自动状态块写进别人的仓库。

# 状态块 = 连续的若干 "> **…**" 行(可能因为历史 bug 叠了多份);
# 归一化时必须把**所有**这种行都删掉再插一条,否则越跑越多(2026-09-21 修)
BANNER_RE = re.compile(r"^>\s*\*\*(状态|Status|最后核对|说明)\*\*.*$", re.M)

# 人工判断过的状态表(2026-09-21 核对)。key = 相对 here 的路径。
# 值 = (状态, 一句话说明)。没列到的按文件名模式兜底。
STATUS = {
    # —— 现行(权威) ——
    "docs/IVD不变量.md": ("现行", "IVD 硬约束:决策必须由 agent 产生,场景/引擎不得替它判断"),
    "docs/架构总览与对接指南.md": ("现行", "**治理平台对接的权威口径**(§11 嵌入面与数据接口;2026-09-21)"),
    "docs/实施规格_组合运行展示与数据界面.md": ("现行", "组合—运行—展示链路与统一数据界面的实施规格"),
    "docs/case00_case01_并列说明.md": ("现行", "两个案例并列时的口径(谁冻结、谁在推进)"),
    "case01/docs/转接_给下一棒_20260918.md": ("现行", "case01 的权威交接文档(§14 起为最新;§20 是分支一致性核查)"),
    "case01/docs/case01_触点白名单.md": ("现行", "case01 侧触碰 mavis 半公开面的白名单,越界要有登记"),
    "case00/README.md": ("现行", "case00 已冻结;只作对照与展示"),
    "case01/README.md": ("现行", "case01 子包说明(端口/入口/契约见文档索引)"),
    "case01/injector/README.md": ("现行", "注入器说明"),
    "../../mavis/README.md": ("现行", "mavis 框架说明(英文)"),
    "../../mavis/README_zh.md": ("现行", "mavis 框架说明(中文)"),
    "../../mavis/docs/tutorial-extension.md": ("现行", "接入方可依赖的稳定扩展面(与 1.2.1 对齐)"),
    "../../mavis/docs/tutorial-extension-en.md": ("现行", "扩展面教程(英文)"),
    "../../mavis/config_tool/README.md": ("现行", "角色/场景配置工具说明(**注意:该工具当前反向依赖 provenance,见 IVD 不变量·历史疤痕**)"),
    # —— 已被取代 ——
    "case01/docs/架构总览_嵌入case01_20260919.md": ("已被取代", "被 `docs/架构总览与对接指南.md` 取代(那时还没有 case_engine 与多场景)"),
    "case01/docs/Governance平台对接说明.md": ("参考(5002 面)", "5002 冻结契约的说明;数据口径/信息边界/平台侧状态机三节仍有效。**唯一对接入口已改为 5010** —— 见 `docs/平台对接契约_5010唯一入口.md`"),
    "docs/平台对接契约_5010唯一入口.md": ("现行", "**交给治理平台的唯一口径**:嵌入面 + 数据接口 + 质检口径 + 信息边界 + 运维(2026-09-21 起)"),
    "docs/名词与分层.md": ("现行", "**名词、分层与数据流**:五个名词各是什么/在哪层 + 引擎的 case01 味道审计 + 搬 ①② 不搬 ③ 的决定"),
    "docs/文档索引.md": ("生成物", "由 `tools/doc_audit.py --banner --index` 生成;状态块与内容日期自动维护,勿手工大改"),
    "case01/docs/通知平台侧_换样本_20260917.md": ("未发出·已过时", "从未发出;样本改名、quality/debug 默认过滤、A 线样本不自洽都发生在此文之后 —— 与 `架构总览与对接指南 §11` 合并后重发"),
    "case01/docs/对接启动_给Tongmu_20260905.md": ("历史依据", "2026-09-05 的启动包;契约口径以 2026-09-21 指南为准"),
    "case01/docs/对接确认单_引擎侧_20260905.md": ("历史依据", "同上(平台侧提出、引擎侧复核的那一版)"),
    "case01/docs/对接确认单_引擎侧答复_20260905.md": ("历史依据", "同上(引擎侧答复)"),
    "case01/docs/ExpertReview数据结构草案_v0.md": ("历史依据", "平台侧 Expert Review 数据结构草案;流程依据仍有效"),
    "case01/docs/mavis_插件面合并决策_20260918.md": ("历史存档", "决策已执行(mavis 插件面已合主线)"),
    "case01/docs/mavis合并bump预演_20260918.md": ("历史存档", "预演材料;bump 已落地(v1.2.0/1.2.1 已 tag)"),
    "case01/docs/mavis版本bump方案_确证_20260918.md": ("历史存档", "同上"),
    "case01/docs/GTC侧文档口径补充_拟稿_20260918.md": ("待人工贴入", "拟稿,等研究侧确认后贴进 0904doc 三份 .docx(不可回滚,不在本仓改)"),
    "case01/docs/case01_over_mavis_验收报告.md": ("历史存档", "阶段 3 验收记录;**阶段 3 抽包仍未开工**"),
    "case01/docs/case01_over_mavis_设计说明.md": ("参考", "阶段 2/3 完成时的设计说明;分层图已过时(缺 case_engine)"),
    "case01/docs/case01_over_mavis_执行计划.md": ("参考", "v0.1 执行计划;实际走向已变(多了通用化重构)"),
    "case01/docs/case01_可插拔可视化设计.md": ("参考", "可插拔可视化设计(仍有效)"),
    "case01/docs/框架层级图_LLM接入节点.md": ("参考", "框架层级与 LLM 接入节点;仍有效"),
    "case01/injector/scenario/README.md": ("现行", "注入器场景(生成物)说明"),
    "scenarios/investment/README.md": ("可能过时", "旧业务层场景说明;场景声明已迁到 `cases/*/scenario.yaml`(待核对是否还需保留)"),
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


def banner_text(rel, date):
    state, note = _status_for(rel)
    return ("> **状态**:{}\n"
            "> **最后核对**:{}\n"
            "> **说明**:{}\n").format(state, date, note)


def apply_banner(path, rel, date):
    """把状态块插在第一个标题行之后;已有状态行**全部**清掉再插(幂等)。"""
    with io.open(path, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    block = banner_text(rel, date).rstrip("\n")
    had = bool(BANNER_RE.search(txt))
    txt2 = BANNER_RE.sub("", txt)                       # 旧的(可能叠了好几份)全删
    txt2 = re.sub(r"\n{3,}", "\n\n", txt2)            # 别留下大片空行
    lines = txt2.split("\n")
    ins = 0
    for i, line in enumerate(lines[:12]):
        if line.strip().startswith("#"):
            ins = i + 1
            break
    # 标题后面紧跟的空行也算进来,保证只留一个空行
    while ins < len(lines) and not lines[ins].strip():
        lines.pop(ins)
    lines[ins:ins] = [""] + block.split("\n")
    txt3 = "\n".join(lines)
    if txt3 != txt:
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(txt3)
        return True
    return False


SKIP_DIRS = (".git", "node_modules", "__pycache__", ".venv", ".venv-live", "_shared",
             ".pytest_cache", ".uv-cache", ".audit-tmp", "results", "runs", "runs_injector",
             "runs_archive_20260919", "runs_html")


def md_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not d.startswith("runs_archive")]
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
    """path(相对 here, posix) -> 该文件**最后一次提交**的日期。

    为什么不用 mtime:给文档补状态块会刷新 mtime,那样 index 里"最后改动"会全变成今天,
    反而把"内容多久没动过"这个信号抹掉了。
    """
    import subprocess
    out = {}
    try:
        p = subprocess.run(["git", "log", "--date=short", "--pretty=format:%ad",
                            "--name-only", "--", "."],
                           cwd=here, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        cur = ""
        for line in (p.stdout or "").splitlines():
            if not line.strip():
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}$", line.strip()):
                cur = line.strip()
            elif cur:
                out[line.strip().replace("\\", "/")] = cur
    except Exception:  # noqa: BLE001 - 拿不到就退回 mtime
        return {}
    return out


def main():
    ap = argparse.ArgumentParser(description=".md 文档体检(列清单 / 补状态行)")
    ap.add_argument("--banner", action="store_true", help="按 STATUS 表给 md 补/更新状态块")
    ap.add_argument("--index", action="store_true", help="生成 docs/文档索引.md(状态一览)")
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

    if args.banner:
        today = time.strftime("%Y-%m-%d")
        n = 0
        for path, _label, _date, _size, _head, _has in rows:
            if apply_banner(os.path.join(here, path), path, today):
                n += 1
        print("\n已更新 {} 个 md 的状态块(状态取自 STATUS 表 / 模式兜底)".format(n))

    if args.index:
        idx = os.path.join(here, "docs", "文档索引.md")
        gdates = git_last_dates(here)

        def cdate(path):
            """内容日期:优先取最后一次提交日期,取不到退回文件 mtime。"""
            for cand in (path, "provenance/" + path):
                if cand in gdates:
                    return gdates[cand]
            st = os.stat(os.path.join(here, path))
            return time.strftime("%Y-%m-%d", time.localtime(st.st_mtime))

        cur = [r for r in rows if _status_for(r[0])[0] == "现行"]
        other = [r for r in rows if r not in cur]
        out = ["# 文档索引(状态一览)", "",
               banner_text("docs/文档索引.md", time.strftime("%Y-%m-%d")).rstrip("\n"),
               "> **用途**:一眼看出哪些文档是现行口径、哪些已被取代。每份 md 顶部也有同样的状态块。",
               "> **「内容日期」= 最后一次提交该文件的日期**(不是文件 mtime:补状态块会刷新 mtime,"
               "那样会把「内容多久没动过」这个信号抹掉)。mavis 仓的文档不在这里管。",
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
                "1. 文档顶部必须有 `> **状态**:` / `> **最后核对**:` / `> **说明**:` 三行;",
                "2. 口径变了就**改状态并写明被谁取代**,不要删文件(评审要能追);",
                "3. 新增文档后跑一次 `python tools/doc_audit.py --banner --index`;",
                "4. `task_*` / `任务_*` 这类派发单完成后一律标「历史存档」。"]
        with io.open(idx, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(out) + "\n")
        print("已写 {}".format(idx))


if __name__ == "__main__":
    main()
