"""case01 测试的公共夹具。

2026-10-10 起：「没配外部 API」是**报错**，不再静默走本地模型（用户拍板：
本地 4B 的输出质量与 DeepSeek 完全不同，新人会误判演示效果）。区分办法是
**显式点名** `CASE01_ETHAN_PROVIDER`。

这条改动让"建桥但不配 Ethan"的用例全线报错 —— 而 CI 上既没有 `.secrets.json`
也没有任何 `CASE01_ETHAN_*` 环境变量（本地有 `case01.local.cmd` 的那台机器才有）。
所以这里给一个统一默认：**显式本地**。

要测外部路径的用例，在用例体里自己 monkeypatch（`monkeypatch` 的改动在后、
优先级更高，会盖掉这里的默认值），例如：

    monkeypatch.delenv("CASE01_ETHAN_PROVIDER", raising=False)
    monkeypatch.setenv("CASE01_ETHAN_BASE_URL", "https://api.example.com/v1")
"""

import pytest


@pytest.fixture(autouse=True)
def _ethan_local_by_default(monkeypatch):
    """除非用例自己动它，否则本套件一律按「显式本地」跑（不发外部请求）。"""
    monkeypatch.setenv("CASE01_ETHAN_PROVIDER", "local")
    yield
