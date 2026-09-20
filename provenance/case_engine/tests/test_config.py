# -*- coding: utf-8 -*-
"""case_engine/config.py 单元测试。"""
import pytest

from case_engine.config import load, load_yaml, validate, validated


def _proto(**kw):
    base = {
        "meta": {"case_id": "demo_case", "name": "演示场景",
                 "start_date": "2026-01-01", "end_date": "2026-02-01"},
        "roles": [
            {"id": "advisor", "display_name": "顾问", "type": "ai_tool",
             "llm": "local", "system_prompt": "你是一位顾问。"},
            {"id": "asker", "display_name": "提问者", "type": "user",
             "llm": "external",
             "conflict_rules": [{"pattern": "买入", "label": "bought"}]},
        ],
        "world": {"state_schema": {
            "cash": {"initial": 1000, "type": "float"},
            "holding": {"initial": False, "type": "bool"},
            "entry": {"initial": None, "type": "float"},
        }},
        "reflection": {"system_prompt": "SYS"},
        "router": {},
        "consistency": {"positive_signals": ["buy"]},
        "retrieval": {"data_dir": "data/fin"},
    }
    base.update(kw)
    return base


def test_meta_and_case_id():
    cfg = load(_proto())
    assert cfg.case_id == "demo_case"
    assert cfg.name == "演示场景"


def test_roles_coerced():
    cfg = load(_proto())
    assert len(cfg.roles) == 2
    r = cfg.roles[0]
    assert r.id == "advisor" and r.display_name == "顾问"
    assert r.type == "ai_tool" and r.llm == "local"
    assert r.max_tokens == 2048          # 默认值兜底
    ask = cfg.roles[1]
    assert ask.conflict_rules[0]["label"] == "bought"


def test_state_schema_typed():
    cfg = load(_proto())
    assert cfg.state_schema["cash"]["initial"] == 1000
    assert cfg.state_schema["holding"]["initial"] is False
    assert cfg.state_schema["entry"]["initial"] is None


def test_state_initial():
    cfg = load(_proto())
    init = cfg.state_initial()
    assert init["cash"] == 1000 and init["holding"] is False
    assert init["entry"] is None


def test_display_name_falls_back_to_id():
    cfg = load(_proto())
    assert cfg.roles[1].display_name == "提问者"


def test_sections_default_to_empty_dict():
    cfg = load(_proto())
    assert cfg.reflection
    assert cfg.router == {}


def test_custom_passthrough():
    cfg = load(_proto(), custom={"x": 1})
    assert cfg.custom == {"x": 1}


def test_validate_ok():
    assert validate(load(_proto())) == []


def test_validate_missing_case_id():
    p = _proto(); p["meta"] = {"name": "no id"}
    errs = validate(load(p))
    assert any("case_id" in e for e in errs)


def test_validate_bad_case_id():
    p = _proto(); p["meta"]["case_id"] = "bad case!"
    errs = validate(load(p))
    assert any("case_id" in e for e in errs)


def test_validate_dup_role_id():
    p = _proto()
    p["roles"].append({"id": "advisor"})
    errs = validate(load(p))
    assert any("重复" in e for e in errs)


def test_validated_raises_on_error():
    p = _proto(); p["meta"] = {}
    with pytest.raises(ValueError):
        validated(load(p))


def test_load_yaml_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_yaml(str(tmp_path / "nope.yaml"))


def test_load_yaml_roundtrip(tmp_path):
    import yaml
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(_proto(), allow_unicode=True), encoding="utf-8")
    cfg = load_yaml(str(p))
    assert cfg.case_id == "demo_case"
    assert len(cfg.roles) == 2


def test_load_yaml_bool_still_bool(tmp_path):
    import yaml
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump({"meta": {"case_id": "x"},
                                 "world": {"state_schema": {
                                     "f": {"initial": False, "type": "bool"}}}}),
                 encoding="utf-8")
    cfg = load_yaml(str(p))
    assert cfg.state_schema["f"]["initial"] is False