"""config_tool.scenario_builder 单元测试:确定性构建 + 导出 + 引擎校验 + 落盘。"""
import json
import os
import sys

import pytest

PLATFORM_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "provenance")
CONFIG_TOOL_DIR = os.path.join(PLATFORM_DIR, "config_tool")
sys.path.insert(0, CONFIG_TOOL_DIR)
import scenario_builder  # noqa: E402

# 只有真正要 case_engine 的用例才检查本仓引擎目录;纯构建/导出用例不依赖它。
needs_case_engine = pytest.mark.skipif(
    not os.path.isdir(os.path.join(PLATFORM_DIR, "case_engine")),
    reason="本仓 case_engine 不在预期位置")


def _sample_form(**over):
    form = {
        "engine": "experiment-eval",
        "case_id": "case03_test",
        "name": "测试中立场景",
        "description": "单元测试场景",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
        "roles": [
            {"id": "analyst", "display_name": "分析师", "type": "ai_tool",
             "llm": "local", "system_prompt": "给出结论", "max_tokens": 2048, "temperature": 0.4},
            {"id": "client", "display_name": "客户", "type": "user",
             "llm": "local", "system_prompt": "", "max_tokens": 1024, "temperature": 0.5},
        ],
        "state_schema": [{"field": "approved", "initial": False, "type": "bool"},
                         {"field": "budget", "initial": 100000, "type": "float"}],
    }
    form.update(over)
    return form


def test_build_scenario_meta_and_roles():
    cfg = scenario_builder.build_scenario(_sample_form())
    meta = cfg["meta"]
    assert meta["case_id"] == "case03_test"
    assert meta["name"] == "测试中立场景"
    assert meta["engine"] == "experiment-eval"
    assert meta["start_date"] == "2026-09-01"
    assert len(cfg["roles"]) == 2
    assert cfg["roles"][0]["id"] == "analyst"
    assert cfg["roles"][0]["max_tokens"] == 2048
    assert cfg["world"]["state_schema"]["approved"] == {"initial": False, "type": "bool"}


def test_build_scenario_no_longer_emits_branch_or_consistency():
    """2026-10-09 会议:「分支判定」功能删除、「一致性信号词」前端不再显示。

    表单不再采集这两段 ⇒ 构建出的场景 draft 里不该再有 `branch` / `consistency`
    键(有了就说明某一层还在偷偷写,与"前端不显示"的口径不符)。
    """
    cfg = scenario_builder.build_scenario(_sample_form(
        branch_default="C", branch_no_buy="反对", cons_buy_words="同意"))
    assert "branch" not in cfg
    assert "consistency" not in cfg


def test_merge_preserves_unmanaged_sections_and_role_fields():
    """编辑表单字段时，未展示的场景段和角色扩展字段不能丢。"""
    original = scenario_builder.build_scenario(_sample_form())
    original["meta"]["protocol"] = "experiment"
    original["roles"][0]["conflict_rules"] = [{"when": "pressure", "do": "escalate"}]
    original["inputs"] = {"brief": "keep me"}
    original["timeline"] = [{"at": "09:00", "event": "open"}]
    original["reflection"] = {"enabled": True}
    changed = scenario_builder.build_scenario(_sample_form(description="只修改描述"))

    merged = scenario_builder.merge_preserving_unmanaged(
        original, changed, "experiment-eval")

    assert merged["meta"]["description"] == "只修改描述"
    assert merged["meta"]["protocol"] == "experiment"
    assert merged["roles"][0]["conflict_rules"] == original["roles"][0]["conflict_rules"]
    assert merged["inputs"] == original["inputs"]
    assert merged["timeline"] == original["timeline"]
    assert merged["reflection"] == original["reflection"]


def test_merge_preserves_legacy_branch_and_consistency_from_original():
    """表单不再管理 branch / consistency(2026-10-09),但**旧场景文件里的这两段
    要原样保留** —— 保存是整份重写,不能因为表单不显示就把它们抹掉。

    这里显式往 original 里塞回这两段(模拟一份历史 scenario.yaml),再走
    "只改描述"的保存路径,断言它们一字不差地留在结果里。
    """
    original = scenario_builder.build_scenario(_sample_form())
    original["branch"] = {"default_branch": "C", "no_buy": ["不建议买"],
                          "judge_prompt": "历史判定稿"}
    original["consistency"] = {"buy_words": ["recommend buying"], "negators": ["not"]}
    changed = scenario_builder.build_scenario(_sample_form(description="只修改描述"))

    merged = scenario_builder.merge_preserving_unmanaged(
        original, changed, "experiment-eval")

    assert merged["meta"]["description"] == "只修改描述"
    assert merged["branch"] == original["branch"]
    assert merged["consistency"] == original["consistency"]


def test_dump_load_roundtrip():
    """导出的 YAML 再 safe_load 应等于原 cfg(确定性、可 diff)。"""
    import yaml
    cfg = scenario_builder.build_scenario(_sample_form())
    text = scenario_builder.dump_scenario_yaml(cfg)
    assert "测试中立场景" in text          # 中文明文,未转义成 \uXXXX
    assert "\\u" not in text
    loaded = yaml.safe_load(text)
    assert loaded == cfg


@needs_case_engine
def test_validate_with_case_engine_schema():
    """生成场景能通过 case_engine 的 Validate 契约(引擎可用时)。"""
    cfg = scenario_builder.build_scenario(_sample_form())
    ok, errors = scenario_builder.validate_scenario(cfg, PLATFORM_DIR)
    assert ok, errors
    assert errors == []


@needs_case_engine
def test_validate_fails_on_bad_case_id():
    cfg = scenario_builder.build_scenario(_sample_form(case_id=" 非法 / 空格"))
    ok, errors = scenario_builder.validate_scenario(cfg, PLATFORM_DIR)
    assert ok is False
    assert any("case_id" in e for e in errors)


@needs_case_engine
def test_save_writes_to_cases_and_loads_back(monkeypatch, tmp_path):
    """保存后落盘 cases/<case_id>/scenario.yaml,且能被 case_engine 加载。"""
    # 必须用 monkeypatch:裸写 os.environ 会跨测试文件泄漏到 case_engine 的用例
    # (tmp_path 用完即删,泄漏后 case00/scenario/* 一律"缺失",红 12 条)。
    monkeypatch.setenv("CASE_ENGINE_CASES_ROOT", str(tmp_path / "_cases"))
    from case_engine.config import load_yaml
    cfg = scenario_builder.build_scenario(_sample_form())
    path = scenario_builder.save_scenario(PLATFORM_DIR, cfg)
    assert os.path.isfile(path)
    loaded = load_yaml(path)
    assert loaded.case_id == "case03_test"
    assert loaded.engine == "experiment-eval"
    assert len(loaded.roles) == 2


@needs_case_engine
def test_relative_cases_root_is_rejected_not_written_to_cwd(monkeypatch, tmp_path):
    """相对根路径必须报错,而**不是**把场景写进进程 cwd。

    2026-10-05:此前 `CASE_ENGINE_CASES_ROOT=cases` 非空即放行,
    `save_scenario` 真的 `makedirs` 到 cwd —— 绕过作者特意写下的
    "不能把场景写进当前工作目录"那条防线(它只挡住了空串)。
    """
    monkeypatch.setenv("CASE_ENGINE_CASES_ROOT", "cases")
    with pytest.raises(ValueError) as ei:
        scenario_builder.cases_dir("")
    assert "绝对路径" in str(ei.value)
    # 兜底:真调落盘也必须是"拒绝",不能在 cwd 造出目录
    cfg = scenario_builder.build_scenario(_sample_form(case_id="leaked_case"))
    with pytest.raises(ValueError):
        scenario_builder.save_scenario("", cfg)
    assert not os.path.isdir(os.path.join(os.getcwd(), "cases", "leaked_case"))
