# -*- coding: utf-8 -*-
"""包自测共享 fixture:在临时目录造最小 scenario 与前端资源根。

包内不内置任何路径/别名;测试作为"通用调用方",自己造数据再传入。
"""
import json

import pytest

ROLE_A, ROLE_B = "Alice", "Bob"
ALIAS = {ROLE_A: "Mr. Zhou", ROLE_B: "AI Advisor"}


@pytest.fixture
def scenario_dir(tmp_path):
    d = tmp_path / "scenario"
    for role, coord in ((ROLE_A, [9, 7]), (ROLE_B, [10, 6])):
        p = d / "agents" / role
        p.mkdir(parents=True, exist_ok=True)
        (p / "agent.json").write_text(json.dumps({"coord": coord}), encoding="utf-8")
    return str(d)


@pytest.fixture
def frontend(tmp_path):
    static = tmp_path / "static"
    tpl = tmp_path / "templates"
    for tex in ALIAS.values():
        ad = static / "assets" / "village" / "agents" / tex
        ad.mkdir(parents=True, exist_ok=True)
        (ad / "portrait.png").write_bytes(b"png")
    (static / "vendor").mkdir(parents=True, exist_ok=True)  # 无本地 phaser → CDN 兜底
    tpl.mkdir(parents=True, exist_ok=True)
    (tpl / "index.html").write_text(
        "<html><body>{%- for p in persona_names -%}{{ p }};{%- endfor -%}</body></html>",
        encoding="utf-8")
    return {"root": str(tmp_path), "static": str(static), "template": str(tpl)}