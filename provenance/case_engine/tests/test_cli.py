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


def test_cli_run_uses_scenario_recommended_engine_and_runs(capsys):
    """默认用场景推荐引擎,真实跑出分支/一致性(不再只剩"未接线")。"""
    from case_engine import cli
    rc = cli.main(["run", "case01_stock", "--input", "轻仓分批等待确认"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "engine:   experiment-eval" in out
    assert "supports: ✔" in out
    assert "run:      完成" in out
    assert "branch:" in out
    assert "consist:" in out
    assert "未接线" not in out


def test_cli_run_missing_input_is_usage_error(capsys):
    """experiment-eval 需要 --input;缺则无法执行并返回非零。"""
    from case_engine import cli
    rc = cli.main(["run", "case01_stock"])
    out = capsys.readouterr().out
    assert rc == 4
    assert "无法执行" in out


def test_cli_run_engine_override_sandbox_on_case01(capsys):
    """--engine 强选 sandbox-value 跑 case01(无沙盒输入):装配预检 all_ok=False → 返回码 4。"""
    from case_engine import cli
    rc = cli.main(["run", "case01_stock", "--engine", "sandbox-value", "--input", "x"])
    out = capsys.readouterr().out
    assert rc == 4
    assert "engine:   sandbox-value" in out
    assert "显式覆盖: True" in out
    assert "supports: ✘" in out
    assert "assembly-check" in out or "缺失" in out


def test_cli_run_sandbox_value_case00(capsys):
    """sandbox-value 跑 case00 本体:装配预检全绿(all_ok=True)→ 返回码 0。"""
    from case_engine import cli
    rc = cli.main(["run", "case00_village", "--engine", "sandbox-value"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "engine:   sandbox-value" in out
    assert "assembly-check" in out
    assert "all_ok:   True" in out


def test_cli_engines_lists_and_matrix(capsys):
    """engines 列出全部;--case-id 给兼容矩阵。"""
    from case_engine import cli
    rc = cli.main(["engines"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "experiment-eval" in out and "sandbox-value" in out

    rc = cli.main(["engines", "--case-id", "case00_village"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "兼容矩阵" in out
    assert "sandbox-value" in out