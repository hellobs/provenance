#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新机一条命令配置:引擎 + venv + 依赖 + Ollama 模型 + 自检。

给刚上手的人用(Windows / macOS / Linux),把 README §2–§3 那六步一次做完:

    Windows :  双击 setup.cmd        (或在 PowerShell 里:  .\\setup.cmd)
    macOS   :  ./setup.sh
    任意    :  python tools/setup_all.py

只读自检(不改任何东西,先看环境差什么):

    python tools/setup_all.py --check

设计原则(与仓库铁律一致):
  * **不静默**:每一步都打印做了什么;跳过也说原因;失败即非零退出并给出下一步命令;
  * **幂等**:重复跑安全 —— 已有的 venv / 已装的依赖 / 已拉的模型都会跳过并说明;
  * **只用标准库**(Python 3.8+):它在 venv 建好之前就得能跑;
  * **不越权**:装 Ollama 需要管理员/包管理器,默认只打印官方命令,`--install-ollama` 才尝试;
  * **不写仓外**:除 venv(仓内)、`../mavis` 的构建产物、Ollama 自己的模型目录外,不动别处。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

IS_WIN = os.name == "nt"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 运行本脚本需要的最低版本(只用标准库,3.8 起都行)
BOOT_MIN = (3, 8)
# venv 里的解释器需要 3.12:mavisframework 与 mavis-vizkit 的 requires-python 都是 >=3.12
VENV_MIN = (3, 12)
DEFAULT_VENV = os.path.join("provenance", ".venv-live")
# 为什么是内层 provenance/.venv-live 而不是仓根 .venv:
# 仓内可执行工具都按这个位置找解释器 —— tools/run_live_watchdog.ps1、
# docs/10月15日演示_运行手册.md、case01/tools/batch_run.py 的自动探测。
# (README §2.2 早先写的是仓根 `.venv`,与该口径不一致,已在同一次提交里对齐。)
ENGINE_ENV = "CASE01_SETUP_ENGINE_DIR"
OLLAMA_URL = "http://127.0.0.1:11434"
# 默认拉三个:代码默认值(4b-instruct + embedding),以及演示/验证轨用的 8b
DEFAULT_MODELS = [
    "qwen3:4b-instruct-2507-q4_K_M",
    "qwen3-embedding:0.6b-q8_0",
    "qwen3:8b",
]

_STEP = {"n": 0}


# ---------------------------------------------------------------------------
# 输出 / 进程
# ---------------------------------------------------------------------------
def say(msg: str) -> None:
    print(msg, flush=True)


def step(title: str) -> None:
    _STEP["n"] += 1
    say("\n" + "=" * 74)
    say("{}. {}".format(_STEP["n"], title))
    say("=" * 74)


def ok(msg: str) -> None:
    say("   [OK]   " + msg)


def info(msg: str) -> None:
    say("   [..]   " + msg)


def warn(msg: str) -> None:
    say("   [警告] " + msg)


def bad(msg: str) -> None:
    say("   [失败] " + msg)


def die(msg: str, code: int = 1):
    bad(msg)
    say("\n未完成。修掉上面这条再重跑同一条命令即可(已完成的步骤会自动跳过)。")
    sys.exit(code)


class Failed(Exception):
    """某一步的硬失败。"""


def run(cmd, cwd=None, check=True, capture=False, env=None, quiet=False):
    """跑外部命令。capture=True 时返回 (rc, stdout+stderr)。"""
    printable = " ".join(str(c) for c in cmd)
    if not quiet:
        info("$ " + printable + ("" if cwd is None else "   (cwd={})".format(cwd)))
    if capture:
        p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace")
        if check and p.returncode != 0 and not quiet:
            warn("命令返回 {}: {}".format(p.returncode, (p.stdout or "").strip()[-500:]))
        return p.returncode, (p.stdout or "")
    rc = subprocess.call(cmd, cwd=cwd, env=env)
    if check and rc != 0:
        raise Failed("命令失败(rc={}):{}".format(rc, printable))
    return rc, ""


def which(name: str):
    return shutil.which(name)


def venv_python(venv_dir: str) -> str:
    return (os.path.join(venv_dir, "Scripts", "python.exe") if IS_WIN
            else os.path.join(venv_dir, "bin", "python"))


def http_json(url: str, timeout: float = 4.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return None


def ask(question: str, auto_yes: bool) -> bool:
    """需要联网/下载/管理员动作时的确认。

    非交互(无 tty)**默认拒绝**(2026-10-06 修)。此前这里是 `return True`,
    于是在 CI / 计划任务里跑 setup 会**自动** `git clone` 上游仓、**自动** `ollama pull`
    约 8GB 模型,甚至自动装 Ollama —— 既可能挂死在凭据提示上,也违背本仓"不静默"的规矩。
    无人值守要跑,请显式加 `--yes`(那时每一问都会打印"自动继续")。
    """
    if auto_yes:
        info("--yes:自动继续:" + question)
        return True
    if not sys.stdin.isatty():
        warn("非交互且未给 --yes:默认**不做** —— " + question)
        info("要无人值守跑,请显式加 --yes")
        return False
    try:
        ans = input("   ? " + question + " [Y/n] ").strip().lower()
    except EOFError:
        return False
    return ans in ("", "y", "yes")


def engine_version(engine_dir: str) -> str:
    """引擎仓自己声明的版本(先 pyproject,再 mavisframework/__init__.py)。

    为什么要它:只看 `dist/` 里有哪些 wheel、再用 `sorted()[-1]` 挑,会踩
    "字典序当版本序"的坑 —— 2026-10-06 实测:dist 里只有 1.1.0/1.2.0/**1.2.1**,
    而引擎仓声明的是 **1.3.4**;`sorted()[-1]` 挑中 1.2.1,而 requirements 的
    `>=1.2.0` 又满足 ⇒ 新机**静默装到旧引擎**,后续 run 直接跑在没有 1.3.x 钩子的框架上。
    """
    for cand, pat in ((os.path.join(engine_dir, "pyproject.toml"),
                       r'^\s*version\s*=\s*["\']([^"\']+)'),
                      (os.path.join(engine_dir, "mavisframework", "__init__.py"),
                       r'__version__\s*=\s*["\']([^"\']+)')):
        if os.path.isfile(cand):
            try:
                for ln in io.open(cand, encoding="utf-8", errors="replace"):
                    m = re.match(pat, ln)
                    if m:
                        return m.group(1)
            except OSError:
                continue
    return ""


def wheel_version(fname: str) -> str:
    """从 `mavisframework-1.3.4-py3-none-any.whl` 取出 `1.3.4`。"""
    m = re.match(r"^.+?-(\d+(?:\.\d+)*)-", os.path.basename(fname))
    return m.group(1) if m else ""


def pick_wheel(dist: str, want: str) -> str:
    """挑**与引擎版本一致**的 wheel;没有就返回空串(让调用方去构建/报错)。

    读不到引擎版本时退回旧行为(取字典序最后一个),但调用方会把它打印出来。
    """
    if not os.path.isdir(dist):
        return ""
    cands = [f for f in sorted(os.listdir(dist)) if f.endswith(".whl")]
    if not cands:
        return ""
    if want:
        for f in cands:
            if wheel_version(f) == want:
                return os.path.join(dist, f)
        return ""
    return os.path.join(dist, cands[-1])


# ---------------------------------------------------------------------------
# 各步骤
# ---------------------------------------------------------------------------
def check_boot_python() -> None:
    step("预检:本脚本的运行环境")
    v = sys.version_info
    info("Python {}.{}.{} ({})".format(v.major, v.minor, v.micro, sys.executable))
    if (v.major, v.minor) < BOOT_MIN:
        die("需要 Python {}.{}+ 才能运行本脚本(当前 {}.{})".format(
            BOOT_MIN[0], BOOT_MIN[1], v.major, v.minor))
    ok("Python 版本够用")


def find_engine_python() -> str:
    """挑一个用来建 venv 的解释器(>=3.12)。

    偏好顺序:3.12 → 3.13 → 其它 >=3.12 里版本最低的那个。
    为什么不是"越新越好":依赖是按 3.12 生态验过的(mavis 开发用 3.12/3.13),
    3.14 这类最新版上 numpy/matplotlib 的轮子未必齐,新机装到一半失败最难排查。
    Windows 上还要避开 Microsoft Store 版 Python(它重定向自身路径,当 venv 宿主容易出怪事),
    所以普通 python.exe 排在 `py -3` 前面。
    """
    uv = which("uv")
    if uv:
        rc, out = run([uv, "python", "find", "3.12"], check=False, capture=True, quiet=True)
        if rc == 0 and out.strip():
            return uv + " :: " + out.strip().splitlines()[-1]
    if IS_WIN:
        cands = [["python"], ["python3.12"], ["py", "-3.12"], ["py", "-3"], ["python3.13"]]
    else:
        cands = [["python3.12"], ["python3.13"], ["python3"], ["python"]]
    found = []
    for c in cands:
        if which(c[0]) is None and not (IS_WIN and c[0] == "py"):
            continue
        rc, out = run(c + ["-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
                      check=False, capture=True, quiet=True)
        if rc != 0:
            continue
        m = re.match(r"^(\d+)\.(\d+)", out.strip())
        if not m:
            continue
        ver = (int(m.group(1)), int(m.group(2)))
        if ver >= VENV_MIN:
            found.append((ver, " ".join(c)))
    if not found:
        return ""
    # 3.12 优先,其次 3.13,再次版本最低的
    def rank(item):
        ver = item[0]
        return (0 if ver == (3, 12) else (1 if ver == (3, 13) else 2), ver)
    found.sort(key=rank)
    return found[0][1]


def ensure_engine(engine_dir: str, auto_yes: bool, editable: bool, force: bool) -> None:
    step("引擎仓 mavisframework(与本仓并列,默认 ../mavis)")
    say("   为什么需要它:requirements.txt 里写的是 mavisframework>=1.2.0,<2.0.0,"
        "它不在 PyPI 上,\n   必须从源码构建或就地安装;低于 1.2.0 会缺 case01 要用到的三个注入钩子。")
    if not os.path.isdir(engine_dir):
        parent = os.path.dirname(engine_dir.rstrip("/\\"))
        say("   目标路径不存在:{}".format(engine_dir))
        if not ask("要现在 git clone 引擎仓到 {} 吗?".format(engine_dir), auto_yes):
            raise Failed("引擎仓缺失;请手动 clone 后重跑(或加 --yes)")
        os.makedirs(parent, exist_ok=True)
        # Git 预检(2026-10-06,第三方复核指出):此前直接 `git clone`,而本机 PATH 里
        # **可能没有 git**(实测裸 shell 里 `git` 不是命令,连带外层 3 条 doc 测试红)。
        # 新机宣传"一条命令",就该在这一步给出可执行的安装提示,而不是抛一个 traceback。
        if not which("git"):
            die("这台机器 PATH 里没有 git —— 引擎仓要用它 clone。\n"
                "       Windows:winget install --id Git.Git   (装完重开终端)\n"
                "       macOS:brew install git     Linux:apt install git\n"
                "       已有的引擎仓也可以:把 mavis 目录拷到 {} 再重跑本脚本。".format(engine_dir))
        try:
            run(["git", "clone", "https://github.com/hellobs/mavis.git", engine_dir])
        except Failed:
            die("clone 失败(网络/权限)。可改用 SSH:\n"
                "       git clone git@github.com:hellobs/mavis.git \"{}\"".format(engine_dir))
        ok("已 clone")
    else:
        ok("引擎仓已在:{}".format(engine_dir))

    py = venv_python(os.path.join(REPO_ROOT, ".probe"))   # 仅用于拼提示,不用它执行
    del py
    if editable:
        info("--editable-engine:就地安装(-e),跳过 wheel 构建")
        return
    dist = os.path.join(engine_dir, "dist")
    want = engine_version(engine_dir)
    whl = pick_wheel(dist, want)
    if whl and not force:
        ok("已有 wheel:{} (版本 {}{})".format(
            os.path.basename(whl), wheel_version(whl),
            ";引擎仓声明 {}".format(want) if want else ";版本未读到"))
        return
    if want and not whl and os.path.isdir(dist) and \
            [f for f in os.listdir(dist) if f.endswith(".whl")]:
        warn("dist/ 里的 wheel 没有一个对得上引擎版本 {} —— 需要重建(否则会静默装旧引擎)".format(want))
    if not ask("要构建引擎 wheel 吗(uv build / python -m build)?约 10 秒", auto_yes):
        raise Failed("未构建 wheel;可加 --editable-engine 改为就地安装")
    uv = which("uv")
    try:
        if uv:
            run([uv, "build"], cwd=engine_dir)
        else:
            run([sys.executable, "-m", "build", "--wheel"], cwd=engine_dir)
    except Failed:
        die("构建失败。pip 用户请先 pip install build;或改用 --editable-engine")
    whl = pick_wheel(dist, want)
    if not whl:
        die("构建后 dist/ 里没有与引擎版本({})一致的 wheel".format(want or "未读到"))
    ok("wheel:{} (版本 {})".format(os.path.basename(whl), wheel_version(whl)))


def ensure_venv(venv_dir: str, force: bool) -> str:
    step("虚拟环境(python {} +)".format(".".join(str(x) for x in VENV_MIN)))
    py = venv_python(venv_dir)
    if os.path.isfile(py) and not force:
        rc, out = run([py, "-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
                      check=False, capture=True, quiet=True)
        if rc == 0 and re.match(r"^(\d+)\.(\d+)", out.strip()):
            mm = re.match(r"^(\d+)\.(\d+)", out.strip())
            have = (int(mm.group(1)), int(mm.group(2)))
            if have < VENV_MIN:
                die("现有 venv 是 Python {}.{},需要 {}+。删掉重建:{}".format(
                    have[0], have[1], ".".join(str(x) for x in VENV_MIN),
                    "rmdir /s /q \"{}\"".format(venv_dir) if IS_WIN else "rm -rf \"{}\"".format(venv_dir)))
            ok("venv 已存在且版本够用:{} (Python {}.{})".format(venv_dir, have[0], have[1]))
            return py
    interp = find_engine_python()
    if not interp:
        die("找不到 Python {}+ 解释器。请先装一个:\n".format(".".join(str(x) for x in VENV_MIN)) +
            ("       winget install Python.Python.3.12\n" if IS_WIN
             else "       macOS: brew install python@3.12   /   Linux: apt install python3.12 python3.12-venv\n"))
    info("用 {} 建 venv".format(interp))
    uv = which("uv")
    try:
        if uv:
            run([uv, "venv", venv_dir, "--python", "3.12"])
        else:
            # interp 可能是 "uv :: <路径>" 形式(uv 在时才会出现),这里只取可执行部分
            run(interp.split(" :: ")[-1].split() + ["-m", "venv", venv_dir])
    except Failed:
        die("建 venv 失败。若提示缺少 ensurepip/venv,请装对应系统包"
            "(Linux: python3.12-venv;conda 用户可直接 conda create -n provenance python=3.12)")
    if not os.path.isfile(py):
        die("建完却找不到 {}".format(py))
    ok("venv 建好:{}".format(venv_dir))
    return py


def pip_install(py: str, args, cwd=None) -> None:
    uv = which("uv")
    if uv:
        run([uv, "pip", "install", "--python", py] + list(args), cwd=cwd)
    else:
        run([py, "-m", "pip", "install"] + list(args), cwd=cwd)


def install_deps(py: str, engine_dir: str, editable: bool, force: bool) -> None:
    step("安装依赖(**顺序不能改**,与 README §2.3 一致)")
    rc, out = run([py, "-c", "import fastapi, mavisframework, mavis_vizkit, "
                             "mavis_case01_injector, pytest; print('imports-ok')"],
                  check=False, capture=True, quiet=True)
    if rc == 0 and "imports-ok" in out and not force:
        ok("五个关键包都已可导入,跳过安装(要强制重装加 --force)")
        return
    # a) 引擎:wheel 或 -e
    if editable:
        info("a) 引擎:就地安装 -e \"{}\"".format(engine_dir))
        pip_install(py, ["-e", engine_dir])
    else:
        dist = os.path.join(engine_dir, "dist")
        want = engine_version(engine_dir)
        whl = pick_wheel(dist, want)
        if not whl:
            # 宁可不装,也不静默装旧引擎:requirements 的 >=1.2.0 会让 pip 认为已满足
            die("dist/ 里没有与引擎版本({})一致的 wheel —— 先重跑 `python tools/setup_all.py`"
                "(它会重建),或加 --editable-engine 就地安装".format(want or "未读到"))
        info("a) 引擎 wheel:{} (版本 {})".format(os.path.basename(whl), wheel_version(whl)))
        pip_install(py, [whl])
    # b) 本仓两个本地包(不在 PyPI 上)
    info("b) 本仓本地包:-e packages/mavis-vizkit -e packages/mavis-case01-injector")
    pip_install(py, ["-e", os.path.join("packages", "mavis-vizkit"),
                     "-e", os.path.join("packages", "mavis-case01-injector")], cwd=REPO_ROOT)
    # c) 其余运行时依赖 + 测试运行器(requirements.txt 里没有 pytest)
    info("c) 运行时依赖 + pytest")
    pip_install(py, ["-r", os.path.join("provenance", "requirements.txt"), "pytest"], cwd=REPO_ROOT)
    ok("依赖装完")


def ollama_status():
    """返回 (binary_path_or_None, service_ok, [model names])。"""
    binary = which("ollama")
    tags = http_json(OLLAMA_URL + "/api/tags")
    names = [m.get("name", "") for m in (tags or {}).get("models", [])]
    return binary, tags is not None, names


def setup_ollama(models, auto_yes: bool, install: bool, skip: bool) -> None:
    step("本地模型(Ollama)")
    if skip:
        warn("--skip-models:跳过模型检查与拉取(LLM 相关功能将不可用)")
        return
    binary, service_ok, names = ollama_status()
    say("   Ollama 二进制:{}{}".format(binary or "(未找到)", ""))
    say("   Ollama 服务({}):{}{}".format(OLLAMA_URL, "在跑" if service_ok else "没连上",
                                        "" if service_ok else "(先启动它,或装完再起)"))
    if not binary:
        warn("没装 Ollama。官方安装方式:")
        if IS_WIN:
            say("       winget install --id Ollama.Ollama -e")
        elif platform.system() == "Darwin":
            say("       brew install --cask ollama      # 或到 https://ollama.com/download 下 .dmg")
        else:
            say("       curl -fsSL https://ollama.com/install.sh | sh")
        if install:
            if not ask("要我尝试自动安装吗(需要管理员/包管理器)?", auto_yes):
                warn("已跳过自动安装")
                return
            try:
                if IS_WIN:
                    run(["winget", "install", "--id", "Ollama.Ollama", "-e"])
                elif platform.system() == "Darwin":
                    run(["brew", "install", "--cask", "ollama"])
                else:
                    run(["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"])
                ok("安装命令已执行;若刚装好,请重开一个终端让 PATH 生效,然后重跑本脚本")
            except Failed:
                warn("自动安装失败,请按上面命令手动装")
        else:
            info("装好后重跑本脚本即可继续(用 --install-ollama 可让它尝试自动装)")
        return
    if not service_ok:
        warn("Ollama 装了但服务没响应。先跑一次 `ollama serve`(Windows 上装完通常已在托盘运行),"
             "然后重跑本脚本拉模型")
        return
    ok("Ollama 服务在跑,已有 {} 个模型".format(len(names)))
    missing = [m for m in models if not any(n == m or n.startswith(m + ":") for n in names)]
    if not missing:
        ok("需要的模型都在:{}".format(", ".join(models)))
        return
    total = {"qwen3:8b": "5.2 GB", "qwen3:4b-instruct-2507-q4_K_M": "2.5 GB",
             "qwen3-embedding:0.6b-q8_0": "639 MB"}
    say("   还缺 {} 个:{}".format(
        len(missing), ", ".join("{}({})".format(m, total.get(m, "~若干")) for m in missing)))
    if not ask("现在拉这些模型吗?合计约 8 GB,取决于网速可能要十几分钟", auto_yes):
        warn("已跳过拉取。手动命令:" + " ; ".join("ollama pull " + m for m in missing))
        return
    failed = []
    for m in missing:
        try:
            run([binary, "pull", m])
            ok("拉到:{}".format(m))
        except Failed:
            failed.append(m)
    if failed:
        warn("这些没拉成功:{}(可重跑本脚本续拉,已成功的会跳过)".format(", ".join(failed)))
    else:
        ok("模型齐了")


def setup_api_key(key: str) -> None:
    step("外部 API key(可选;不填就用本地 Ollama)")
    if not key:
        info("未提供 --api-key,跳过(本地 Ollama 是默认路径,不需要 key)")
        return
    script = os.path.join(REPO_ROOT, "provenance", "tools", "setup_api.py")
    if not os.path.isfile(script):
        warn("找不到 {},跳过".format(os.path.relpath(script, REPO_ROOT)))
        return
    run([sys.executable, script, "--key", key])
    ok("key 已写入(该文件被 .gitignore 忽略;脚本不会打印 key 本身)")


def data_records_root() -> str:
    """成品记录根:走仓内那一个解析口 `case_engine.paths.data_root()`。

    为什么必须有这个函数(2026-10-08 晚实测):此前 self_check 数的是
    `provenance/case01/runs/` —— 那一层当晚归类搬迁时已被删,数它永远得 0,
    于是这台**已经有 583 条**的机器也被报成"还没有成品记录 —— 新克隆的正常状态"。
    `case_engine.paths` 只用标准库,所以引导解释器(3.8+,没装任何依赖)也能 import 它。
    """
    pkg = os.path.join(REPO_ROOT, "provenance")
    if pkg not in sys.path:
        sys.path.insert(0, pkg)
    from case_engine.paths import data_root
    return data_root("case01.records", env_var="CASE01_RUNS_ROOT")


def count_records(root: str) -> int:
    """数"带 run.json 的目录" —— 别拿目录数当记录数(失败批会留空目录)。"""
    if not os.path.isdir(root):
        return 0
    return len([d for d in os.listdir(root)
                if os.path.isfile(os.path.join(root, d, "run.json"))])


def self_check(py: str, engine_dir: str) -> int:
    step("自检(同样的检查也可以单独跑:--check)")
    problems = []

    rc, out = run([py, "-c", "import sys;print('%d.%d.%d'%sys.version_info[:3])"],
                  check=False, capture=True, quiet=True)
    if rc == 0:
        ok("venv 解释器:Python {}".format(out.strip()))
    else:
        bad("venv 解释器跑不起来:{}".format(py))
        problems.append("venv")

    rc, out = run([py, "-c", "import fastapi, uvicorn, mavisframework, mavis_vizkit, "
                             "mavis_case01_injector, pytest, yaml, matplotlib, httpx; print('ok')"],
                  check=False, capture=True, quiet=True)
    (ok if rc == 0 else bad)("关键包导入{}".format("正常" if rc == 0 else "失败:" + out.strip()[-200:]))
    if rc != 0:
        problems.append("imports")

    baseline = os.path.join(REPO_ROOT, "tools", "check_engine_baseline.py")
    if os.path.isfile(baseline):
        rc, out = run([py, baseline], check=False, capture=True, quiet=True)
        (ok if rc == 0 else bad)("引擎基线检查(CI 也跑它){}".format("通过" if rc == 0 else "未通过"))
        if rc != 0:
            problems.append("engine-baseline")
    else:
        warn("找不到 tools/check_engine_baseline.py,跳过")

    binary, service_ok, names = ollama_status()
    (ok if service_ok else warn)("Ollama 服务{}".format("可达" if service_ok else "不可达(只影响跑推演)"))
    if names:
        say("        已有模型:{}".format(", ".join(sorted(names)[:8])))
    runs = data_records_root()
    nruns = count_records(runs)
    if nruns:
        ok("已有成品记录 {} 条(界面开箱有内容)—— 根:{}".format(nruns, runs))
    else:
        info("还没有成品记录(看的根:{})—— 新克隆的正常状态;界面上会/embed/explore 为空。"
             "要造一条:python -m case01.run --run-id my-001(约 1-2 分钟);"
             "想看现成的:把交付包 runs/ 拷进上面那个根,或把 CASE01_RUNS_ROOT 指到 "
             "provenance/case01/tests/fixtures/records(仓内入库了两条)".format(runs))

    say("")
    if problems:
        bad("自检未通过:{}".format(", ".join(problems)))
        say("   下一步:重跑 `python tools/setup_all.py`(它会补装),或看上面的 [失败] 行。")
        return 1
    ok("自检通过。接下来(面与解释器都在包目录 `provenance/` 那层;下面按绝对路径给,免得再猜 cwd):")
    pkg = os.path.join(REPO_ROOT, "provenance")
    venv_py = (os.path.join(".venv-live", "Scripts", "python.exe") if IS_WIN
               else os.path.join(".venv-live", "bin", "python"))
    say("       起实时面(只读,不需要 GPU):cd \"{}\" && {} live_switch.py --start case01 --review-only"
        .format(pkg, venv_py))
    # 这两条此前是坏的:它们按"仓根的上一级"起步写(`cd provenance\provenance`),
    # 而 README/setup 让人在仓根跑 —— 那个相对路径在这里指向不存在的第三层。
    # 跑测试的目录口径也写反过:外层 `tests/` 在仓根,`case01/tests`/`case_engine/tests`
    # 必须在包目录跑(在仓根跑会 "no tests ran" 而 exit 0,是假绿)。
    say("       跑测试(外层):cd \"{}\" && {} -m pytest tests"
        .format(REPO_ROOT, os.path.join("provenance", venv_py)))
    say("       跑测试(内层两套,必须 cd 包目录):cd \"{}\" && {} -m pytest case01/tests case_engine/tests"
        .format(pkg, venv_py))
    return 0


def check_only(args) -> int:
    """--check:只读自检,不装任何东西。"""
    step("只读自检(不安装、不下载、不改文件)")
    v = sys.version_info
    (ok if (v.major, v.minor) >= VENV_MIN else warn)(
        "本脚本的运行环境:Python {}.{}(venv 需要 {}+)".format(
            v.major, v.minor, ".".join(str(x) for x in VENV_MIN)))
    venv_dir = os.path.join(REPO_ROOT, args.venv_name)
    py = venv_python(venv_dir)
    if os.path.isfile(py):
        return self_check(py, args.engine_dir)
    bad("还没建 venv({})—— 跑 `python tools/setup_all.py` 建".format(venv_dir))
    binary, service_ok, names = ollama_status()
    say("   Ollama 二进制:{}   服务:{}   模型:{}".format(
        binary or "(未找到)", "可达" if service_ok else "不可达",
        ", ".join(names) if names else "(无)"))
    return 1


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="新机一条命令配置(引擎 + venv + 依赖 + Ollama + 自检)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="只读自检,不安装任何东西")
    ap.add_argument("--yes", "-y", action="store_true", help="所有询问自动继续(CI/无人值守)")
    ap.add_argument("--venv-name", default=DEFAULT_VENV,
                    help="venv 路径(默认 {},相对仓根;仓内工具按这个位置找解释器)".format(DEFAULT_VENV))
    ap.add_argument("--engine-dir", default=os.environ.get(ENGINE_ENV) or
                    os.path.join(os.path.dirname(REPO_ROOT), "mavis"),
                    help="引擎仓路径(默认与仓根并列的 ../mavis)")
    ap.add_argument("--editable-engine", action="store_true",
                    help="引擎就地安装(pip install -e),跳过 wheel 构建")
    ap.add_argument("--force", action="store_true", help="已满足的步骤也重做")
    ap.add_argument("--skip-models", action="store_true", help="不检查/不拉 Ollama 模型")
    ap.add_argument("--install-ollama", action="store_true",
                    help="缺 Ollama 时尝试用 winget/brew/官方脚本安装(默认只打印命令)")
    ap.add_argument("--models", default="", help="自定义要拉的模型,逗号分隔")
    ap.add_argument("--api-key", default="", help="可选:写入外部 API key(交给 tools/setup_api.py)")
    args = ap.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()] or DEFAULT_MODELS

    say("仓库根:{}".format(REPO_ROOT))
    say("平台:{} {} / Python {}".format(platform.system(), platform.release(),
                                        platform.python_version()))
    if args.check:
        return check_only(args)

    venv_dir = os.path.join(REPO_ROOT, args.venv_name)
    try:
        check_boot_python()
        ensure_engine(args.engine_dir, args.yes, args.editable_engine, args.force)
        py = ensure_venv(venv_dir, args.force)
        install_deps(py, args.engine_dir, args.editable_engine, args.force)
        setup_ollama(models, args.yes, args.install_ollama, args.skip_models)
        setup_api_key(args.api_key)
        return self_check(py, args.engine_dir)
    except Failed as exc:
        die(str(exc))
    except KeyboardInterrupt:
        say("\n被中断。已完成的步骤不会重复做,重跑同一条命令即可继续。")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
