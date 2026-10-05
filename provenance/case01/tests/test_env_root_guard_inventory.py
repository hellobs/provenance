# -*- coding: utf-8 -*-
"""环境变量「根路径」守卫清册(2026-10-05,回应 GTC 体检 N5/N8)。

立这条的经过:GTC 两份体检都指出 —— 路径守卫是在**按调用点**铺开,不是
**按所有根**铺开。runs 根刚补上绝对校验(`test_runs_root_env_parity.py`),
而同一轮里 `case01/expert_pool.py:18` 仍在裸读 `CASE01_EXPERT_POOL_PATH`;
更早还有 `live/history.py`、`config_tool/engine_bridge.py` 两处。

这类漏点的危害是"读写不对称":相对值时一侧按 cwd 落盘、另一侧当场报错,
同一条配置在两边给出不同答案。所以本测试分两层钉:

1. **清册式**:全仓扫描 `os.environ.get("XXX_ROOT"/"XXX_PATH")`,任何一处
   没走 `resolve_root`/`require_abs_path` 就报红,并指名道姓。新增一个根时
   忘了过守卫,这里会立刻拦下(而不是等下一次体检人工发现)。
2. **行为式**:对已知的几个根逐个断言"相对值必须抛 AmbiguousPathError"。
"""
import os
import re
import sys

import pytest

_PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from case_engine.paths import AmbiguousPathError, resolve_root  # noqa: E402

# 扫描面:平台自有代码(不扫 .venv / 测试自身 / packages 下的第三方)
_SCAN_FILES = [
    os.path.join(_PKG, "case01", "expert_pool.py"),
    os.path.join(_PKG, "case01", "orchestrator.py"),
    os.path.join(_PKG, "case01", "serve.py"),
    os.path.join(_PKG, "live", "history.py"),
    os.path.join(_PKG, "config_tool", "engine_bridge.py"),
    os.path.join(_PKG, "case_engine", "scenarios.py"),
]

# 已知的根类环境变量(名字以 _ROOT / _PATH / _DIR 结尾)
_ENV_PAT = re.compile(r'os\.environ\.get\(\s*"([A-Z0-9_]+)"')


def _env_roots_in(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    return [n for n in _ENV_PAT.findall(src)
            if n.endswith(("_ROOT", "_PATH", "_DIR"))]


def test_every_root_env_is_read_through_the_guard():
    """任何 `*_ROOT`/`*_PATH`/`*_DIR` 环境变量都不得**裸读**。

    判据:该文件里若出现 `os.environ.get("XXX_ROOT")`,同一文件必须同时引用了
    `resolve_root` 或 `require_abs_path`。裸读(既 get 又不走守卫)= 漏点。
    """
    offenders = []
    for path in _SCAN_FILES:
        names = _env_roots_in(path)
        if not names:
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        guarded = ("resolve_root" in src) or ("require_abs_path" in src)
        if not guarded:
            offenders.append("{} 裸读了 {} 但没走守卫".format(
                os.path.relpath(path, _PKG), "/".join(sorted(set(names)))))
    assert not offenders, (
        "路径守卫按调用点铺开、有根漏网(GTC 体检 N5):\n  " + "\n  ".join(offenders) +
        "\n改法:走 case_engine.paths.resolve_root(env, default, what=...)")


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


def test_guard_message_names_the_variable():
    """错误信息必须**点名环境变量**,否则现场只知道"路径错了"却不知道改哪个。"""
    from case_engine.paths import require_abs_path
    with pytest.raises(AmbiguousPathError) as ei:
        require_abs_path("rel/x", what="CASE01_SOMETHING_ROOT")
    assert "CASE01_SOMETHING_ROOT" in str(ei.value)
