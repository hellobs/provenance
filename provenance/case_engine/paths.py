# -*- coding: utf-8 -*-
"""路径解析守卫(引擎通用):环境变量给的根目录一律要求**绝对路径**。

为什么必须有这一层(2026-10-05):
    本仓有一票 `*_ROOT` / `*_DIR` 环境变量(`CASE_ENGINE_CASES_ROOT`、
    `CASE01_RUNS_ROOT`、`CASE00_CHECKPOINTS_ROOT`、`MAVIS_ASSETS_ROOT` …)。
    它们此前**原样透传**,于是给相对值时行为随进程 cwd 漂移:

        CASE_ENGINE_CASES_ROOT=cases
        cwd = 仓库根                → list_cases 返回 0 条(静默空,界面显示"没有场景")
        cwd = provenance/provenance → list_cases 返回 3 条

    同一个配置两种南辕北辙的表现,而且**不报错** —— 排障时只看到"场景没了"。
    更坏的一条:`scenario_builder.save_scenario` 会 `makedirs` 到解析结果,
    相对值等于把场景**写进进程 cwd**(作者本就在注释里写明"不能写进当前工作目录",
    但那条防线只挡住了空串,挡不住相对值)。

    结论:根目录类配置**只接受绝对路径**。收到相对值时立刻抛出可读错误,
    把"隐式按 cwd 解释"这个不可见行为彻底消灭 —— 与其猜用户想相对谁,
    不如让配置错误当场可见。

边界(如实说明):
    - 只管**环境变量/配置来源**的根目录;程序内部由 `__file__` 推导的默认根本就绝对,
      不走这里;
    - 不做 `expanduser`/`expandvars` 之外的魔法(这两个做了,因为 `~` 与 `%VAR%`
      在配置里是常见且无歧义的);
    - 不校验"目录是否存在" —— 存在性是调用方的事(有的根允许暂时不存在,
      例如首批运行前 `case01/runs`)。

放在引擎层而非各仓各写一份:provenance 的 config_tool/live 与 case_engine
都要用,各自实现一份必然漂移。
"""
import os
import sys

# 仓根 / 包根:本文件在 <仓根>/provenance/case_engine/paths.py
PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(PKG_ROOT)

__all__ = ["AmbiguousPathError", "require_abs_path", "resolve_root", "data_root", "DATA_KINDS"]


# ---------------------------------------------------------------------------
# 数据根(2026-10-08 归类搬迁):所有"跑出来的存档"统一搬到仓根 `data/`。
#
# 为什么需要这一层:此前**同一个概念有三个落点** ——
#   `results/checkpoints/`(case00 沙盒存档 + 跨局登记簿)、
#   `case01/injector/scenario/checkpoints/`(case01 引擎状态)、
#   `case01/results/checkpoints/`(空壳,历史路径痕迹);
# 而 case01 还把成品/原始记录放在自己目录下。见 `provenance/docs/仓库布局说明.md`。
#
# 搬迁期策略(**不用目录联接**):新位置优先;新位置不在而老位置在 → 用老位置并**打一行提示**;
# 两个都不在 → 返回新位置(调用方负责创建)。这样"边搬边跑"安全,且**漏改的读取方会出声**。
# ---------------------------------------------------------------------------
DATA_KINDS = {
    # kind                    新位置(相对仓根)                 老位置(相对包根)
    "case01.records": ("data/case01/runs", "case01/runs"),
    "case01.raw": ("data/case01/raw", "case01/runs_injector"),
    "case01.runtime": ("data/case01/runtime", "case01/runs_injector"),
    "case01.state": ("data/case01/state", "case01/injector/scenario/checkpoints"),
    "case01.html": ("data/case01/html", "case01/runs_html"),
    "case01.archive": ("data/case01/archive", "case01/runs_archive_20260919"),
    "case00.state": ("data/case00/state", "results/checkpoints"),
    "ledgers": ("data/ledgers", "results/checkpoints"),
}

_warned = set()


def data_root(kind: str, *, env_var: str = "", what: str = "") -> str:
    """解析某类数据根:环境变量(须绝对) > 新位置 > 老位置 > 新位置(待创建)。

    环境变量仍然最高优先 —— 它是对接/演示的显式覆盖口(如 `CASE01_RUNS_ROOT`)。
    """
    if env_var:
        raw = os.environ.get(env_var)
        if raw:
            return require_abs_path(raw, what=what or env_var)
    if kind not in DATA_KINDS:
        raise KeyError("Unknown data root {!r}; available roots: {}".format(kind, sorted(DATA_KINDS)))
    new_rel, old_rel = DATA_KINDS[kind]
    new = os.path.join(REPO_ROOT, *new_rel.split("/"))
    old = os.path.join(PKG_ROOT, *old_rel.split("/"))
    if os.path.isdir(new):
        return new
    if os.path.isdir(old):
        if kind not in _warned:
            _warned.add(kind)
            sys.stderr.write(
                "[data_root] {} remains at the legacy path {}. Move it to {} "
                "as described in the repository layout guide. Continuing with the legacy path.\n"
                .format(kind, old, new))
        return old
    return new


class AmbiguousPathError(ValueError):
    """配置里的路径是相对路径(语义随 cwd 漂移),拒绝静默接受。"""


def require_abs_path(value: str, *, what: str) -> str:
    """把配置来源的路径规范成绝对路径;相对值直接报错。

    `what` 是给人看的来源说明(通常是环境变量名),用于拼错误信息。
    """
    text = (value or "").strip()
    if not text:
        raise AmbiguousPathError("{} 为空,无法确定目录".format(what))
    expanded = os.path.expanduser(os.path.expandvars(text))
    if not os.path.isabs(expanded):
        raise AmbiguousPathError(
            "{} 必须是绝对路径,收到相对路径 {!r}。相对路径的语义随进程工作目录漂移"
            "(同一配置换个目录启动会指向不同位置),因此被拒绝。"
            "请改成绝对路径,例如 {!r}。".format(
                what, value, os.path.abspath(expanded)))
    return os.path.normpath(expanded)


def resolve_root(env_var: str, default: str, *, what: str = "") -> str:
    """解析一个根目录:环境变量优先(须绝对),否则用 `default`(须已是绝对)。

    这是各调用点应统一走的入口 —— 一处归一,处处同语义。
    """
    raw = os.environ.get(env_var)
    if raw:
        return require_abs_path(raw, what=what or env_var)
    return os.path.normpath(default)
