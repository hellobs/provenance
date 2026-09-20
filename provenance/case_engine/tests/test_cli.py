# -*- coding: utf-8 -*-
"""CLI 入口(发现/查看场景声明)测试。"""
import os

from case_engine.scenarios import default_cases_root

REAL_CASES = default_cases_root()


def test_default_cases_root_points_to_real_cases():
    assert os.path.isdir(REAL_CASES)
    assert {"case00_village", "case01_stock"} <= set(os.listdir(REAL_CASES))


def test_cli_list_shows_all_cases(capsys):
    from case_engine import cli
    rc = cli.main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "case01_stock" in out
    assert "case00_village" in out
    assert "sandbox-value" in out


def test_cli_show_case01_engine(capsys):
    from case_engine import cli
    rc = cli.main(["show", "case01_stock"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "engine:  experiment-eval" in out
    assert "roles[" in out


def test_cli_show_case00_roles(capsys):
    from case_engine import cli
    rc = cli.main(["show", "case00_village"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "engine:  sandbox-value" in out
    assert "state_schema[" in out


def test_cli_show_unknown_case_returns_2(capsys):
    from case_engine import cli
    rc = cli.main(["show", "does_not_exist"])
    assert rc == 2
    assert "不存在" in capsys.readouterr().out


def test_cli_honors_custom_cases_dir(tmp_path, capsys):
    """--cases-dir 覆盖取根,复刻宽容扫描对坏文件标 problem。"""
    from case_engine import cli
    (tmp_path / "only").mkdir()
    (tmp_path / "only" / "scenario.yaml").write_text(
        "meta:\n  case_id: only\nroles:\n  - id: a\n", encoding="utf-8")
    rc = cli.main(["--cases-dir", str(tmp_path), "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "only" in out
    assert "case01_stock" not in out