# -*- coding: utf-8 -*-
"""环境变量「根路径」守卫清册(2026-10-05,回应 GTC 体检 N5/N8;第六轮 P2-1 补洞)。

立这条的经过:GTC 两份体检都指出 —— 路径守卫是在**按调用点**铺开,不是
**按所有根**铺开。runs 根刚补上绝对校验(`test_runs_root_env_parity.py`),
而同一轮里 `case01/expert_pool.py:18` 仍在裸读 `CASE01_EXPERT_POOL_PATH`;
更早还有 `live/history.py`、`config_tool/engine_bridge.py` 两处。

这类漏点的危害是"读写不对称":相对值时一侧按 cwd 落盘、另一侧当场报错,
同一条配置在两边给出不同答案。所以本测试分三层钉:

1. **清册式(全仓扫描)**:`os.walk` 扫遍平台自有代码,任何一处
   `os.environ.get("<路径型变量>")` 没走守卫就报红,并指名道姓。
   新增一个根时忘了过守卫,这里会立刻拦下(而不是等下一次体检人工发现)。
2. **行为式**:对已知的几个根逐个断言"相对值必须抛 AmbiguousPathError"。
3. **扫描面自证**:先证明第 1 层的扫描面**真的覆盖了**已知的裸读点 ——
   否则"没报红"可能只是"压根没扫到"(P2-1 的原始 bug 正是硬编码清单漏文件)。

历史(P2-1):第一版的扫描面是**硬编码 6 个文件**,于是 `config_tool/compositions.py`
(`COMPOSITIONS_FILE`)与 `case01/review_app.py`(`CASE01_REVIEW_RUNS_DIR`)两个
真实裸读点既不在清单里、后缀正则也抓不到(`_FILE` 不在 `_ROOT/_PATH/_DIR` 里),
清册"全绿"而洞一直开着。现改为 `os.walk` 全仓 + 后缀集合含 `_FILE/_JSON/...`。
"""
import os
import re
import sys

import pytest

_PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)
# config_tool 是**扁平布局**(模块互 import 用裸名,如 `import engine_bridge`),
# 要把该目录本身放上 sys.path 才能 import 它的模块(`tests/test_config_tool_dirs.py` 同理)。
_CONFIG_TOOL = os.path.join(_PKG, "config_tool")
if _CONFIG_TOOL not in sys.path:
    sys.path.insert(0, _CONFIG_TOOL)

from case_engine.paths import AmbiguousPathError, resolve_root  # noqa: E402

# 扫描面:平台自有代码(不扫虚拟环境 / 缓存 / 测试自身 / 前端产物)
_SKIP_DIRS = frozenset((
    ".git", ".venv", ".venv-live", ".uv-cache", "node_modules", "__pycache__",
    "tests", "runs", "results", "frontend", "fixtures",
))
_SCAN_ROOTS = (
    os.path.join(_PKG, "case01"),
    os.path.join(_PKG, "live"),
    os.path.join(_PKG, "config_tool"),
    os.path.join(_PKG, "case_engine"),
    os.path.join(_PKG, "tools"),
)

# 读环境变量的两种写法:os.environ.get("X") / os.environ.get("X", default)
_ENV_PAT = re.compile(r'os\.environ\.get\(\s*"([A-Z0-9_]+)"')
# 路径型变量的后缀。**必须含 `_FILE`** —— P2-1 的 `COMPOSITIONS_FILE` 正是漏在这。
# `_JSON` 也收:名字型路径变量(如 xxx.json 的落点)常不写 _FILE 后缀。
_PATH_SUFFIXES = ("_ROOT", "_PATH", "_DIR", "_FILE", "_JSON")

# 有意**不走**守卫的变量白名单(每个都要在这里写清理由)。
# 判据:这个值**不是"根目录/文件位置"**,相对值不会造成读写不对称。
_NOT_A_ROOT = {
    # 三者都是"当前进程的临时落盘点",由 os.environ 给出、语义就是相对当前环境;
    # 缺失时括号里的默认值本就是绝对路径,不构成配置漂移风险。
    "TEMP": "Windows 临时目录(进程环境),非本仓配置根",
}


def _iter_py_files():
    """遍历平台代码里的 .py(排除虚拟环境/测试/产物)。"""
    for root in _SCAN_ROOTS:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fn in files:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def _env_path_vars_in(path):
    """该文件里读到的**路径型**环境变量名(已过滤非根白名单)。"""
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    names = set()
    for n in _ENV_PAT.findall(src):
        if n.endswith(_PATH_SUFFIXES) and n not in _NOT_A_ROOT:
            names.add(n)
    return sorted(names)


def _guard_refs_in(path):
    """该文件是否引用了任一守卫入口(用于"裸读"判定)。"""
    with open(path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    return any(k in src for k in (
        "resolve_root", "require_abs_path", "env_abs_path",
    ))


def _offenders():
    """全仓里"读了路径型环境变量却没引守卫"的文件清单。"""
    out = []
    for path in _iter_py_files():
        names = _env_path_vars_in(path)
        if not names:
            continue
        if not _guard_refs_in(path):
            out.append("{} 裸读了 {} 但没走守卫".format(
                os.path.relpath(path, _PKG).replace("\\", "/"), "/".join(names)))
    return out


def test_every_root_env_is_read_through_the_guard():
    """任何路径型环境变量都不得**裸读**(全仓 os.walk 扫描)。

    判据:该文件里若出现 `os.environ.get("XXX_ROOT")`(或 `_PATH`/`_DIR`/`_FILE`),
    同一文件必须同时引用了 `resolve_root` / `require_abs_path` / `env_abs_path`。
    裸读(既 get 又不走守卫)= 漏点。
    """
    offenders = _offenders()
    assert not offenders, (
        "路径守卫按调用点铺开、有根漏网(GTC 体检 N5 / 第六轮 P2-1):\n  " +
        "\n  ".join(offenders) +
        "\n改法:case01/live 走 case_engine.paths.resolve_root(env, default, what=...),"
        "config_tool 走 engine_bridge.env_abs_path(...)")


def test_inventory_scan_actually_sees_the_known_holes():
    """扫描面自证:必须真的扫到那两个曾经的裸读点(P2-1 的原始 bug 是硬编码清单漏文件)。

    这条是"元测试" —— 若哪天有人把扫描面改小/前缀写错,`test_every_root_env_...`
    会因为"啥都没扫到"而假绿,这一条会先炸。

    只钉**以 `os.environ.get("字面量")` 形态出现**的变量。像 `CASE01_RUNS_ROOT`、
    `CASE01_EXPERT_POOL_PATH` 那样把变量名交给 `resolve_root(...)` 的,本来就
    扫不到字符串(也不该扫到)—— 它们由行为式断言(见文件下半)覆盖。
    """
    seen = set()
    for path in _iter_py_files():
        for n in _env_path_vars_in(path):
            seen.add(n)
    for must in ("COMPOSITIONS_FILE", "CASE01_REVIEW_RUNS_DIR",
                 "CASE_ENGINE_CASES_ROOT"):
        assert must in seen, (
            "扫描面漏了这个变量,清册是假的:{}。检查 _SCAN_ROOTS / _PATH_SUFFIXES "
            "/ _SKIP_DIRS 是否把含它的文件排除了".format(must))


def test_compositions_file_is_not_bare_read():
    """`config_tool/compositions.py` 必须过守卫(P2-1 第一处洞)。"""
    path = os.path.join(_PKG, "config_tool", "compositions.py")
    assert "COMPOSITIONS_FILE" in _env_path_vars_in(path)
    assert _guard_refs_in(path), "compositions.py 读了 COMPOSITIONS_FILE 却没引守卫"


def test_review_app_runs_dir_is_not_bare_read():
    """`case01/review_app.py` 必须过守卫(P2-1 第二处洞)。"""
    path = os.path.join(_PKG, "case01", "review_app.py")
    assert "CASE01_REVIEW_RUNS_DIR" in _env_path_vars_in(path)
    assert _guard_refs_in(path), "review_app.py 读了 CASE01_REVIEW_RUNS_DIR 却没引守卫"


@pytest.mark.parametrize("env_name", [
    "CASE01_RUNS_ROOT",
    "CASE01_EXPERT_POOL_PATH",
])
def test_known_roots_reject_relative_values(env_name, monkeypatch):
    """相对值必须当场抛错,消息里带原值 —— 不允许"按 cwd 悄悄解释"。"""
    monkeypatch.setenv(env_name, "some/relative/place")
    with pytest.raises(AmbiguousPathError) as ei:
        resolve_root(env_name, os.path.join(_PKG, "fallback"), what=env_name)
    assert "some/relative/place" in str(ei.value)


def test_expert_pool_path_rejects_relative(monkeypatch):
    """`expert_pool.expert_pool_path()` 具体行为(而不只是它引用了守卫函数)。"""
    from case01 import expert_pool as EP
    monkeypatch.setenv("CASE01_EXPERT_POOL_PATH", "case01/expert_pool.json")
    with pytest.raises(AmbiguousPathError):
        EP.expert_pool_path()


def test_expert_pool_path_accepts_absolute_and_falls_back(monkeypatch, tmp_path):
    from case01 import expert_pool as EP
    monkeypatch.setenv("CASE01_EXPERT_POOL_PATH", str(tmp_path / "pool.json"))
    assert EP.expert_pool_path() == str(tmp_path / "pool.json")
    monkeypatch.delenv("CASE01_EXPERT_POOL_PATH")
    assert EP.expert_pool_path() == os.path.normpath(EP.DEFAULT_POOL_PATH)


# --------------------------------------------------------------- 逐变量行为核对
# 上面两条只证明"文件引用了守卫",不证明"守卫真的作用在这两个变量上"。
# 这里对 P2-1 的两个洞补**行为式**断言:相对值必须拒绝,绝对值必须认出。

def test_compositions_file_rejects_relative(monkeypatch):
    """`compositions._file()` 对相对路径必须当场报错(P2-1 第一处洞的行为核对)。"""
    from config_tool import compositions as C
    monkeypatch.setenv("COMPOSITIONS_FILE", "config_tool/compositions.json")
    with pytest.raises(Exception) as ei:
        C._file()
    assert "COMPOSITIONS_FILE" in str(ei.value) or "绝对路径" in str(ei.value)


def test_compositions_file_accepts_absolute(monkeypatch, tmp_path):
    from config_tool import compositions as C
    target = tmp_path / "comps.json"
    monkeypatch.setenv("COMPOSITIONS_FILE", str(target))
    assert C._file() == os.path.normpath(str(target))
    monkeypatch.delenv("COMPOSITIONS_FILE")
    assert C._file() == os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(C.__file__)), C.DEFAULT_FILE))


def test_review_app_runs_dir_rejects_relative(monkeypatch):
    """`review_app._resolve_runs_dir()` 对相对路径必须当场报错(P2-1 第二处洞)。"""
    import importlib
    from case01 import review_app as R
    monkeypatch.setenv("CASE01_REVIEW_RUNS_DIR", "case01/runs")
    with pytest.raises(AmbiguousPathError):
        R._resolve_runs_dir()
    importlib  # 保持 import 名(供静态检查器识别:此测试故意用函数而非常量)
    assert os.path.isabs(R.RUNS_DIR), "模块常量必须是启动时的合法绝对落点"


def test_review_app_runs_dir_accepts_absolute(monkeypatch, tmp_path):
    from case01 import review_app as R
    target = tmp_path / "records"
    monkeypatch.setenv("CASE01_REVIEW_RUNS_DIR", str(target))
    assert R._resolve_runs_dir() == os.path.normpath(str(target))


def test_guard_message_names_the_variable():
    """错误信息必须**点名环境变量**,否则现场只知道"路径错了"却不知道改哪个。"""
    from case_engine.paths import require_abs_path
    with pytest.raises(AmbiguousPathError) as ei:
        require_abs_path("rel/x", what="CASE01_SOMETHING_ROOT")
    assert "CASE01_SOMETHING_ROOT" in str(ei.value)
