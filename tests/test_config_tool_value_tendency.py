# -*- coding: utf-8 -*-
"""跨仓守卫:config_tool 生成的 governance.json 必须**逐字等于**引擎的映射结果。

这是 ② 的最后一环(2026-09-21):配置工具不再自己维护一份"声明 → governance.json"的口径,
而是调用 `case_engine.config.value_tendency_plan()` 的 materialize 结果。
本测试直接拿 case00 的真实声明比对两边,谁改了单边就红。

注:config_tool 在 **mavis 仓**,这里用 sys.path 把它 import 进来(只读,不改它)。
"""
import io
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))          # D:\zzr\provenance\tests
_REPO = os.path.dirname(_HERE)                              # D:\zzr\provenance
_PKG = os.path.join(_REPO, "provenance")                    # provenance/provenance
_MAVIS_TOOL = os.path.join(os.path.dirname(_REPO), "mavis", "config_tool")

# 引擎目录自 2026-09-22 起**只认显式声明**(config_tool 不再探测兄弟目录):
# 本测试显式声明引擎包所在目录,走工具的唯一入口。
os.environ.setdefault("CASE_ENGINE_DIR", _PKG)

for p in (_PKG, os.path.dirname(_REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

def _same(a, b, tol=1e-12):
    """递归比较(含浮点容差):pytest.approx 不支持"字典套字典"。"""
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_same(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    return a == b


SCENARIO = os.path.join(_PKG, "cases", "case00_village", "scenario.yaml")


def _yaml_to_dict(path):
    """把 scenario.yaml 读成 dict:用引擎自己的加载器 + 原始 yaml(避免再引依赖)。"""
    import yaml  # PyYAML 在 CI 里有(引擎 load_yaml 依赖它)
    with io.open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.mark.skipif(not os.path.isdir(_MAVIS_TOOL),
                    reason="mavis 仓不在预期位置(本机有则跑)")
def test_config_tool_uses_the_engine_mapping():
    if _MAVIS_TOOL not in sys.path:
        sys.path.insert(0, _MAVIS_TOOL)
    try:
        import scenario_builder                                    # noqa: F401
    except Exception as exc:                                       # noqa: BLE001
        pytest.skip("config_tool 依赖缺失,跳过: {}: {}".format(type(exc).__name__, exc))

    from case_engine.config import load as ce_load, value_tendency_plan

    decl = _yaml_to_dict(SCENARIO)
    engine_want = value_tendency_plan(ce_load(decl))["materialize"]["governance.json"]

    tool = scenario_builder.governance_payload(decl)
    assert tool.get("used_engine") is True, \
        "config_tool 没能用上引擎的映射,退回本地口径了: {}".format(tool.get("engine_note"))
    assert _same(tool["roles"], engine_want), \
        "config_tool 生成的 governance 与引擎映射不一致(单一来源被破坏)"


@pytest.mark.skipif(not os.path.isdir(_MAVIS_TOOL), reason="mavis 仓不在预期位置")
def test_config_tool_payload_is_declaration_based():
    """config_tool 生成的是**声明派生**的治理(不是仓里那份可能已被干预改过的资产)。

    这条以前拿"真实 governance.json"当基准 —— 本轮体检发现该文件会被运行时专家干预改写,
    所以基准改成声明;资产的偏离由 case_engine 的单一来源测试用"审计解释"来管。
    """
    if _MAVIS_TOOL not in sys.path:
        sys.path.insert(0, _MAVIS_TOOL)
    try:
        import scenario_builder                                    # noqa: F401
    except Exception as exc:                                       # noqa: BLE001
        pytest.skip("config_tool 依赖缺失: {}".format(exc))

    from case_engine.config import load as ce_load, value_tendency_plan

    decl = _yaml_to_dict(SCENARIO)
    want = value_tendency_plan(ce_load(decl))["materialize"]["governance.json"]
    tool = scenario_builder.governance_payload(decl)
    assert tool.get("used_engine") is True
    assert _same(tool["roles"], want)
