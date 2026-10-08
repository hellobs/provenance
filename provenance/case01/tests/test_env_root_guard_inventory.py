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
import io
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
    """该文件是否引用了任一守卫入口(用于"裸读"判定)。

    2026-10-08 加 `data_root`:它是 `case_engine.paths` 里新的"数据根解析器"
    (新位置优先、老位置回落并出声),环境变量那一支内部走的仍是 `require_abs_path`,
    所以它同样算"走了守卫"。不加这一项,把调用点从 `resolve_root` 换成 `data_root`
    的改动会被误判成裸读。
    """
    with open(path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    return any(k in src for k in (
        "resolve_root", "require_abs_path", "env_abs_path", "data_root",
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
    同一文件必须同时引用了 `resolve_root` / `require_abs_path` / `env_abs_path` / `data_root`。
    裸读(既 get 又不走守卫)= 漏点。
    """
    offenders = _offenders()
    assert not offenders, (
        "路径守卫按调用点铺开、有根漏网(GTC 体检 N5 / 第六轮 P2-1):\n  " +
        "\n  ".join(offenders) +
        "\n改法:case01/live 走 case_engine.paths.resolve_root 或 data_root(env_var=..., what=...),"
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


# ---------------------------------------------------------------------------
# mavis 存档根(第十一轮定因):测试构造 Game 时不设根 ⇒ 往生产路径建空目录
# ---------------------------------------------------------------------------
def test_tests_never_construct_real_game_without_checkpoints_root():
    """清册式:测试里构造 `Game(...)` 必须先设 `MAVIS_CHECKPOINTS_ROOT`。

    为什么要专门钉这一条(第十轮 N9-7 / 第十一轮定因):
    `mavisframework/runtime/game.py:39-41` 在构造 Game 时会
    `os.makedirs(os.path.join(checkpoints_root, name, "storage"))`,而
    `checkpoints_root` 默认取**相对当前工作目录**的 `results/checkpoints`。

    ⇒ CI 上`cd provenance` 跑 case01 套件时,任何一个不设根就构造 Game 的测试,
    都会在**生产路径** `provenance/results/checkpoints/` 留一个空目录
    (实测是 `visual-path-test/storage`,0 个文件)。

    危害有两层,第二层才是真的:① 多一个生产目录;
    ② `test_metric_semantics.py` 的 F 组判据是 `os.path.isdir(CK_DIR)`,目录一旦
    存在,CI 上本该打印的"results/checkpoints/未入库(gitignore)"就变成
    "checkpoints 目录在但无 value_tendency 样本" —— **测试把自己的依据说明改成了
    错的**,而那3 条正是研究边界红线 1 引用的性质断言。
    (实证:修之前干净检出 case01 跑完 skip 是 6 条且文案自相矛盾;修之后回到 8 条、
    文案全部一致。)

    判据用 AST 找 `Game(` 的**真实调用**(排除 `_FakeGame(`、类定义、注释),
    再看它所在的测试函数有没有 `setenv("MAVIS_CHECKPOINTS_ROOT")`。这样新增测试
    忘了设根会立刻报红,而不是等下一轮体检从 skip 文案里反推。

    **扫描根必须自己证明覆盖到位**(第十二轮 4.1 订正):第一版三个根里第三个
    算成了 `provenance/tests` —— **该目录不存在**,而真正的外层套件在**仓根**
    `tests/`(30 个 `test_*.py`,CI 第一步跑的就是它)。配合 `if not isdir: continue`
    就是**静默空扫**:守卫全绿,而外层新增一个不设根就构造真 Game 的测试它拦不住。
    两处一起改:
      ① 根改成从 `__file__` 逐级数到**仓库根**再拼 `tests`(仓根布局变时会fail,
         而不是悄悄扫空);
      ② 根不存在一律 **fail**,不再 `continue` —— 清册式守卫"全绿"本身必须
         可信,扫面为空就该报红而不是放过。
    另补 `packages/mavis-case01-injector/tests`(CI 第二步跑它,而
    `bridge.py:447` 真的构造 `Game`)。
    """
    import ast

    tests_root = os.path.join(os.path.dirname(os.path.abspath(__file__))
    )
    # tests_root = <repo>/provenance/case01/tests ⇒ 上溯 2 级到 <repo>/provenance,
    # 上溯 3 级到 <repo>。两个层级的目录各在哪:case_engine 与 case01 平级,
    # 仓根 tests 与 packages 在 <repo> 下。原先这两个根各算错一次(第十二轮 4.1)。
    prov_root = os.path.dirname(os.path.dirname(tests_root))
    repo_root = os.path.dirname(prov_root)
    scan_roots = [
        tests_root,                                        # case01/tests
        os.path.join(prov_root, "case_engine", "tests"),
        os.path.join(repo_root, "tests"),                   # 仓根外层套件(CI 第一步)
        os.path.join(repo_root, "packages",
                     "mavis-case01-injector", "tests"),     # CI 第二步
    ]
    missing = [r for r in scan_roots if not os.path.isdir(r)]
    assert not missing, (
        "存档根守卫的扫描根不存在:{}。"
        "根算错会让本守卫**静默空扫**(全绿而没扫到任何东西)。"
        "目录真的被移走了才允许改这条断言,并要同步更新本docstring。\\n"
        "  当前推导: tests_root={} ⇒ repo_root={}".format(missing, tests_root, repo_root))
    offenders, checked = [], 0
    per_root = {}            # 每根的真实 Game( 命中数(诊断用)
    per_root_files = {}      # 每根扫到的 .py 文件数(自证判据,见下)
    for root in scan_roots:
        # 根存在性已在上方断言,这里不再 `if not isdir: continue` ——
        # 那正是本条守卫第一版"全绿而没扫到"的原因(见 docstring)。
        per_root[root] = 0
        per_root_files[root] = 0
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                per_root_files[root] += 1
                path = os.path.join(dirpath, fn)
                with io.open(path, encoding="utf-8") as fh:
                    src = fh.read()
                # 跳过守卫自己:它的报错文案里就写着 `Game(` 与
                # `MAVIS_CHECKPOINTS_ROOT`,扫到自己是自指误报(第一版就被这条绊住:
                # 还原修复后守卫仍红,报的 offender 是它自己)。
                if os.path.abspath(path) == os.path.abspath(__file__):
                    continue
                if "Game(" not in src:
                    continue
                try:
                    tree = ast.parse(src)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.FunctionDef):
                        continue
                    body = ast.get_source_segment(src, node) or ""
                    if not re.search(r"(?<![\w.])Game\s*\(", body.replace(
                            "_FakeGame(", "").replace("class Game", "")):
                        continue
                    checked += 1
                    per_root[root] += 1
                    # 判据必须是**真实的 setenv 调用**,不能是"函数体里出现过这个字符串"。
                    # 第一版用后者,结果自家 docstring 里那句"必须设 MAVIS_CHECKPOINTS_ROOT"
                    # 就把违规函数判成了合规 —— 变异验证时才发现(守卫放过了刚被回退的修复)。
                    has = False
                    for sub in ast.walk(node):
                        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                                and sub.func.attr in ("setenv", "setdefault")):
                            if sub.args and isinstance(sub.args[0], ast.Constant) \
                                    and sub.args[0].value == "MAVIS_CHECKPOINTS_ROOT":
                                has = True
                    if not has:
                        offenders.append("{}:{} {}".format(
                            os.path.relpath(path, tests_root).replace("\\", "/"),
                            node.lineno, node.name))
    assert not offenders, (
        "这些测试构造了真实 Game 却没设 MAVIS_CHECKPOINTS_ROOT —— 会在生产路径 "
        "provenance/results/checkpoints/ 留下空目录,并让 Σ=1 那3 条 skip 打印"
        "错误的理由:\n  " + "\n  ".join(offenders))
    assert checked, ("扫描面没抓到任何 Game( 调用点 —— 本守卫已失效:"
                     "要么 Game( 改了写法,要么测试目录挪了位置")
    # ---- 扫描面**逐根**自证(第十二轮 4.1)----
    # 第一版三个根里一个算成了不存在的 `provenance/tests`,配 `if not isdir: continue`
    # 就是**静默空扫**:总数照样 > 0,守卫全绿而仓根外层套件从未被扫过。
    #
    # 判据用**扫到的 .py 文件数**而不是 `Game(` 命中数:命中数会把
    # "扫到了、里面确实只有 _FakeGame("(外层套件就是这种)和
    # "路径错、根本没扫到"混成同一个 0,豁免机制随即把两者一起放过 ——
    # 变异验证实测:去掉本段断言,守卫照样全绿(等于没加)。
    # 文件数能区分这两者:根存在且被walk 过,文件数就 > 0。
    empty_roots = sorted(r for r, n in per_root_files.items() if n == 0)
    assert not empty_roots, (
        "这些扫描根一个 .py 都没扫到 —— 路径算错了(或根被清空):\n  {}\n"
        "外层套件在**仓根** tests/、case_engine/tests 在 provenance/ 下、"
        "injector 包在 packages/ 下;推导见上方 prov_root/repo_root。"
        .format("\\n  ".join(empty_roots)))
    # 每个根扫到的文件数下限:防止"根算错成某个子目录"这类**部分**扫描。
    # 数字取自2026-10-05 实测,只当"明显偏少"的哨兵,不要求精确(测试文件会增删)。
    MIN_FILES = {
        os.path.join(repo_root, "tests"): 20,
        os.path.join(prov_root, "case_engine", "tests"): 5,
    }
    thin = ["{}: 只扫到 {} 个 .py(下限 {})".format(r, n, MIN_FILES[r])
            for r, n in per_root_files.items() if r in MIN_FILES and n < MIN_FILES[r]]
    assert not thin, (
        "这些根扫到的文件数明显偏少,多半是算成了子目录或路径漂了:\n  {}"
        .format("\\n  ".join(thin)))
    # 外层套件必须真的被扫到(它是 CI 第一步,且这条洞就是它引起的)
    outer = os.path.join(repo_root, "tests")
    assert per_root_files.get(outer, 0) >= MIN_FILES[outer], (
        "仓根外层套件没被有效扫描(扫到 {} 个 .py)—— 这正是第十二轮 4.1 的洞"
        .format(per_root_files.get(outer, 0)))
