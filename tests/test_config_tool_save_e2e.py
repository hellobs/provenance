# -*- coding: utf-8 -*-
"""端到端守卫:走**配置工具的真实保存路径**,检查生成的 governance.json 与引擎映射一致。

比单元测试强的地方:它验证的是"工具实际写盘的那份文件"(
`app._write_scenario_content` → `scenario_builder.governance_payload` → 引擎 `value_tendency_plan`),
而不是只比函数返回值。做法是在 tmp 里重定向资产目录(`app._PLATFORM_DIR` 与
`app.scenario_assets_dir`),绝不写真实仓库。
"""
import io
import asyncio
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "provenance")
_CONFIG_TOOL = os.path.join(_PKG, "config_tool")

# 引擎目录自 2026-09-22 起**只认显式声明**(config_tool 不再探测兄弟目录,也不再拿平台根顶替):
# 本测试显式声明引擎包所在目录;资产落盘目录仍由用例另行重定向到 tmp。
os.environ.setdefault("CASE_ENGINE_DIR", _PKG)

for p in (_PKG, os.path.dirname(_REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

pytestmark = pytest.mark.skipif(not os.path.isdir(_CONFIG_TOOL), reason="provenance/config_tool 不存在")


def _tool_app():
    if _CONFIG_TOOL not in sys.path:
        sys.path.insert(0, _CONFIG_TOOL)
    import app as app_mod          # config_tool/app.py
    return app_mod


def _form():
    """一个最小 sandbox 表单:两个角色 + 声明价值权重。

    表单分两段:`roles` 是引擎可见的角色声明,`agents` 是完整保真的资产内容;两段
    角色名必须一致(引擎按 display_name / 资产落盘按 name,见 config_tool
    `build_agent_json`)。每个 agent 的 initial_tendency 与顶层声明同源 ——
    测试据此断言"资产的初始底色 == 声明"。
    """
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
        "roles": [
            {"id": "ai_advisor", "display_name": "AI Advisor", "type": "ai_tool"},
            {"id": "wendy_lin", "display_name": "Wendy Lin", "type": "user"},
        ],
        "agents": [
            {"name": "AI Advisor", "role_type": "ai_tool",
             "initial_tendency": "Serve Users:0.5\nRisk Control:0.5"},
            {"name": "Wendy Lin", "role_type": "user",
             "initial_tendency": "Compliance Rigor:0.5\nClient Protection:0.5"},
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

    2026-09-27 体检:原夹具的 agent 字段用了 `display_name`/`type`,而 config_tool
    读 `name`/`role_type` → 两个角色全被跳过,本用例一直走 skip,②从未真正断言。
    夹具已按表单口径(roles + agents.name/role_type)补齐,这里改成**硬断言**:
    完整夹具不该跳过任何角色,若再漂移必须红,而不是静默 skip。
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

    assert not rel.get("skipped_agents"), \
        "夹具已给全字段,角色不该被跳过(与 config_tool 表单口径漂移?): {}".format(
            rel.get("skipped_agents"))
    assert seen, "一个角色都没写出来,却也没有 skipped_agents 报告 —— 静默丢角色"

    for role, dims in want.items():
        if role not in seen:
            assert rel.get("skipped_agents"), "角色 {} 没写出来也没报告".format(role)
            continue
        assert seen[role] == dims, (role, seen[role], dims)


class TestHTTPLayerFormIntegrity:
    """HTTP 层表单完整性(2026-09-27 体检):_json_body 曾自递归,
    所有 POST 的表单字段被静默丢成 {} —— e2e 直接调内部函数绕过了 HTTP 层,
    CI 全绿但真实保存全空。此测试通过 ASGI transport 真打端点。"""

    @staticmethod
    def _post(app, path, form):
        """通过 ASGI transport 走完整 HTTP 层，避开当前环境 TestClient 线程死锁。"""
        import httpx

        async def request():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                    transport=transport, base_url="http://config-tool.test") as client:
                return await client.post(path, json=form)

        return asyncio.run(request())

    def test_save_preserves_form_fields(self, tmp_path, monkeypatch):
        app_mod = _tool_app()
        monkeypatch.setattr(app_mod, "_PLATFORM_DIR", str(tmp_path))
        monkeypatch.setenv("CASE_ENGINE_CASES_ROOT", str(tmp_path / "cases"))
        form = {"case_id": "http_integrity_case",
                "name": "HTTP 层完整性场景",
                "engine": "experiment-eval",
                "description": "表单字段必须活着到达落盘",
                "start_date": "2026-09-01", "end_date": "2026-09-30"}
        r = self._post(app_mod.app, "/api/scenario/save", form)
        assert r.status_code == 200, r.text
        body = r.json()
        # 关键断言:字段活着(case_id/name 不能退化成默认值)
        assert body.get("case_id") == "http_integrity_case", (
            "表单字段丢失(_json_body 自递归复发?): {}".format(body))
        saved = body.get("path")
        assert saved and os.path.isfile(saved)
        import io as _io
        text = _io.open(saved, encoding="utf-8").read()
        assert "http_integrity_case" in text and "HTTP 层完整性场景" in text

    def test_preview_reflects_fields(self, tmp_path):
        app_mod = _tool_app()
        r = self._post(
            app_mod.app, "/api/scenario/preview",
            {"case_id": "pv_case", "name": "预览场景",
             "engine": "experiment-eval"})
        assert r.status_code == 200
        assert "pv_case" in r.json().get("yaml", ""), "预览未收到表单字段"
