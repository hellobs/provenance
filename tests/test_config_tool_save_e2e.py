# -*- coding: utf-8 -*-
"""端到端守卫:走**配置工具的真实保存路径**,检查生成的 governance.json 与引擎映射一致。

比单元测试强的地方:它验证的是"工具实际写盘的那份文件"(
`app._write_scenario_content` → `scenario_builder.governance_payload` → 引擎 `value_tendency_plan`),
而不是只比函数返回值。做法是在 tmp 里重定向资产目录(`app._PLATFORM_DIR` 与
`app.scenario_assets_dir`),绝不写真实仓库。
"""
import io
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "provenance")
_MAVIS_TOOL = os.path.join(os.path.dirname(_REPO), "mavis", "config_tool")

# 引擎目录自 2026-09-22 起**只认显式声明**(config_tool 不再探测兄弟目录,也不再拿平台根顶替):
# 本测试显式声明引擎包所在目录;资产落盘目录仍由用例另行重定向到 tmp。
os.environ.setdefault("CASE_ENGINE_DIR", _PKG)

for p in (_PKG, os.path.dirname(_REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

pytestmark = pytest.mark.skipif(not os.path.isdir(_MAVIS_TOOL), reason="mavis 仓不在预期位置")


def _tool_app():
    if _MAVIS_TOOL not in sys.path:
        sys.path.insert(0, _MAVIS_TOOL)
    import app as app_mod          # config_tool/app.py
    return app_mod


def _form():
    """一个最小 sandbox 表单:两个角色 + 声明价值权重。"""
    gov = {"AI Advisor": {"Serve Users": 0.6, "Risk Control": 0.4},
           "Wendy Lin": {"Compliance Rigor": 0.7, "Client Protection": 0.3}}
    init = {"AI Advisor": {"Serve Users": 0.5, "Risk Control": 0.5},
            "Wendy Lin": {"Compliance Rigor": 0.5, "Client Protection": 0.5}}
    return {
        "engine": "sandbox-value",
        "case_id": "e2e_tmp_case",
        "name": "E2E 临时场景",
        "description": "单测用",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
        "agents": [
            {"id": "ai_advisor", "display_name": "AI Advisor", "type": "ai_tool",
             "llm": "local", "system_prompt": "你是投资顾问", "max_tokens": 2048},
            {"id": "wendy_lin", "display_name": "Wendy Lin", "type": "user",
             "llm": "local", "system_prompt": "你是合规", "max_tokens": 2048},
        ],
        "relationships": [],
        "story": [],
        "value_tendency": json.dumps({"governance": gov, "initial_tendency": init},
                                     ensure_ascii=False),
        "sandbox_params": json.dumps({"percept": {"mode": "box"}}),
        "asset_maze": "",
    }


def test_saved_governance_json_equals_engine_mapping(tmp_path, monkeypatch):
    app_mod = _tool_app()

    # 资产目录重定向到 tmp:cases/<case_id>/assets/
    monkeypatch.setattr(app_mod, "_PLATFORM_DIR", str(tmp_path))
    monkeypatch.setattr(app_mod, "scenario_assets_dir",
                        lambda cid: os.path.join(str(tmp_path), "cases", cid, "assets"))

    form = _form()
    rel = app_mod._write_scenario_content(form["case_id"], form)
    gov_path = os.path.join(app_mod.scenario_assets_dir(form["case_id"]), "governance.json")
    assert os.path.isfile(gov_path), "工具没有写出 governance.json"
    written = json.loads(io.open(gov_path, encoding="utf-8").read())

    # 引擎的同一映射(声明 → governance)
    from case_engine.config import load as ce_load, value_tendency_plan

    scenario = app_mod.scenario_builder.build_scenario(form)
    want = value_tendency_plan(ce_load(scenario))["materialize"]["governance.json"]
    assert written["roles"] == want, "工具写出的 governance.json 与引擎映射不一致"
    assert rel.get("governance"), rel


def test_saved_agents_carry_initial_tendency(tmp_path, monkeypatch):
    """角色的 initial_tendency 必须来自同一声明(②的另一半)。

    若某个角色因表单字段不全被跳过,则**必须**在返回值里报出来(`skipped_agents`)——
    这正是本项目"不允许静默"的落点(2026-09-21 修掉了 `except: continue`)。
    """
    app_mod = _tool_app()
    monkeypatch.setattr(app_mod, "_PLATFORM_DIR", str(tmp_path))
    monkeypatch.setattr(app_mod, "scenario_assets_dir",
                        lambda cid: os.path.join(str(tmp_path), "cases", cid, "assets"))
    form = _form()
    rel = app_mod._write_scenario_content(form["case_id"], form)

    from case_engine.config import load as ce_load, value_tendency_plan

    scenario = app_mod.scenario_builder.build_scenario(form)
    want = value_tendency_plan(ce_load(scenario))["materialize"]["initial_tendency"]

    agents_dir = os.path.join(app_mod.scenario_assets_dir(form["case_id"]), "agents")
    seen = {}
    if os.path.isdir(agents_dir):
        for name in os.listdir(agents_dir):
            p = os.path.join(agents_dir, name, "agent.json")
            if os.path.isfile(p):
                seen[name] = json.loads(
                    io.open(p, encoding="utf-8").read()).get("initial_tendency")

    if not seen:
        assert rel.get("skipped_agents"), \
            "一个角色都没写出来,却也没有 skipped_agents 报告 —— 这就是静默丢角色"
        pytest.skip("表单字段不全导致角色被跳过(已如实报告): {}".format(rel["skipped_agents"]))

    for role, dims in want.items():
        if role not in seen:
            assert rel.get("skipped_agents"), "角色 {} 没写出来也没报告".format(role)
            continue
        assert seen[role] == dims, (role, seen[role], dims)
