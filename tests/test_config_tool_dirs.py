# -*- coding: utf-8 -*-
"""config_tool 目录解析:环境变量 → 本地设置文件 → 自动发现(2026-09-27)。

背景:2026-09-22 为收口反向依赖把"探测兄弟目录"整个删掉、只认 CASE_ENGINE_DIR,
结果是"开箱即废"(用户不会设环境变量)。现在解析顺序为:
显式参数 → 环境变量 → 本地设置文件 → 自动发现(须含 marker 目录),且**写明来源**。
本文件把这条链路的优先级钉住。
"""
import os
import sys

import pytest

_this = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_this)                       # <provenance repo>
_TOOL = os.path.join(_REPO, "provenance", "config_tool")
if _TOOL not in sys.path:
    sys.path.insert(0, _TOOL)

import engine_bridge as eb  # noqa: E402


def _fake_dir_with_marker(tmp_path, name, marker):
    d = tmp_path / name
    (d / marker).mkdir(parents=True)
    return str(d)


def test_explicit_beats_everything(monkeypatch):
    monkeypatch.setenv(eb.ENV_DIR, str("C:/from-env"))
    r = eb.resolve_dir(explicit="C:/from-explicit")
    assert r["source"] == "参数" and r["dir"].replace("\\", "/").endswith("from-explicit")


def test_env_beats_setting_and_autodiscover(tmp_path, monkeypatch):
    monkeypatch.setenv(eb.ENV_DIR, "C:/from-env")
    monkeypatch.setattr(eb, "SETTING_FILE", str(tmp_path / ".cfg.json"))
    eb.write_settings(engine_dir="C:/from-setting")
    monkeypatch.setattr(eb, "auto_candidates", lambda: ["C:/from-auto"])
    r = eb.resolve_dir()
    assert r["source"] == eb.ENV_DIR, r


def test_setting_file_used_when_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv(eb.ENV_DIR, raising=False)
    monkeypatch.setattr(eb, "SETTING_FILE", str(tmp_path / ".cfg.json"))
    monkeypatch.setattr(eb, "auto_candidates", lambda: [])
    eb.write_settings(engine_dir="C:/from-setting")
    r = eb.resolve_dir()
    assert r["source"] == "设置文件" and r["dir"].replace("\\", "/").endswith("from-setting")


def test_autodiscover_picks_only_dir_with_marker(tmp_path, monkeypatch):
    monkeypatch.delenv(eb.ENV_DIR, raising=False)
    monkeypatch.setattr(eb, "SETTING_FILE", str(tmp_path / ".cfg.json"))
    good = _fake_dir_with_marker(tmp_path, "prov", eb.PKG)
    monkeypatch.setattr(eb, "auto_candidates",
                        lambda: [str(tmp_path / "no-marker"), good])
    r = eb.resolve_dir()
    assert r["source"] == "自动发现" and r["dir"] == os.path.abspath(good)


def test_not_found_is_reported_not_guessed(tmp_path, monkeypatch):
    monkeypatch.delenv(eb.ENV_DIR, raising=False)
    monkeypatch.setattr(eb, "SETTING_FILE", str(tmp_path / ".cfg.json"))
    monkeypatch.setattr(eb, "auto_candidates", lambda: [])
    r = eb.resolve_dir()
    assert r["configured"] is False and r["dir"] == ""
    assert eb.status()["available"] is False
    assert eb.reason(), "不可用时必须给出可读原因(不静默)"


def test_platform_marker_needs_frontend_and_engine(tmp_path, monkeypatch):
    """平台根的 marker 是两个:只含 frontend 的"别的仓"不能被误认(2026-09-27 踩过)。"""
    monkeypatch.delenv(eb.ENV_DIR, raising=False)
    monkeypatch.delenv("MAVIS_PLATFORM_DIR", raising=False)
    monkeypatch.setattr(eb, "SETTING_FILE", str(tmp_path / ".cfg.json"))
    only_front = tmp_path / "other-repo"
    (only_front / "frontend").mkdir(parents=True)          # 只有 frontend → 不算
    real = tmp_path / "plat"
    (real / "frontend").mkdir(parents=True)
    (real / eb.PKG).mkdir(parents=True)                     # frontend + 引擎包 → 算
    monkeypatch.setattr(eb, "auto_candidates",
                        lambda: [str(only_front), str(real)])
    r = eb.resolve_dir(env_keys=("MAVIS_PLATFORM_DIR", eb.ENV_DIR),
                       setting_key="platform_dir", marker=("frontend", eb.PKG))
    assert r["source"] == "自动发现" and r["dir"] == os.path.abspath(str(real))


def test_settings_roundtrip_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "SETTING_FILE", str(tmp_path / ".cfg.json"))
    eb.write_settings(engine_dir="C:/e", platform_dir="C:/p")
    assert eb._read_settings() == {"engine_dir": "C:/e", "platform_dir": "C:/p"}
    eb.write_settings(engine_dir="")            # 空串 = 清除该项
    assert eb._read_settings() == {"platform_dir": "C:/p"}


# --------------------------------------------------------------- 逐项路径 M4
# 2026-10-05 第六轮 M4:`config_tool/app.py` 的三个根(MAVIS_ASSETS_ROOT /
# MAVIS_SCENARIOS_DIR / MAVIS_MAZE_PATH)此前是**模块级常量**,import 时求值一次。
# 后果有两个:① 启动后再设环境变量完全不生效(要 reload 整模块);
# ② 若 import 期读到相对值,当场抛 —— 连启动横幅都打不出来。
# 现改为函数(每次实时读)。下面钉住"改环境变量立刻生效"这条语义。

def _tool_module():
    """import config_tool/app.py(它 import 期会打横幅,属正常行为)。"""
    _TOOL = os.path.join(_REPO, "provenance", "config_tool")
    if _TOOL not in sys.path:
        sys.path.insert(0, _TOOL)
    if os.path.join(_REPO, "provenance") not in sys.path:
        sys.path.insert(0, os.path.join(_REPO, "provenance"))
    import app as app_mod
    return app_mod


def test_scenarios_dir_reads_env_at_call_time(tmp_path, monkeypatch):
    """`MAVIS_SCENARIOS_DIR` 在**调用时**读 —— import 之后设也生效(M4)。"""
    app_mod = _tool_module()
    target = str(tmp_path / "my_scenarios")
    monkeypatch.setenv("MAVIS_SCENARIOS_DIR", target)
    assert app_mod.scenarios_dir() == os.path.normpath(target)


def test_assets_root_reads_env_at_call_time(tmp_path, monkeypatch):
    app_mod = _tool_module()
    target = str(tmp_path / "my_assets")
    monkeypatch.setenv("MAVIS_ASSETS_ROOT", target)
    assert app_mod.village_root() == os.path.normpath(target)


def test_maze_path_reads_env_at_call_time(tmp_path, monkeypatch):
    app_mod = _tool_module()
    maze = tmp_path / "maze.json"
    maze.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MAVIS_MAZE_PATH", str(maze))
    assert app_mod.maze_path() == os.path.normpath(str(maze))


def test_module_aliases_are_startup_snapshots(tmp_path, monkeypatch):
    """模块级别名(`VILLAGE_ROOT`/`MAZE_PATH`)是**启动快照**,不随 env 变 —— 这是缺陷不是特性。

    保留别名是为了兼容既有模板引用(以及若干测试 monkeypatch);真正要用"实时值"
    的地方一律调函数。这条把两种语义的差别钉下来,免得后人以为别名会跟着变。

    2026-10-08:素材目录不再是模块别名 —— `SCENARIOS_DIR` 已删。素材由
    `case_scenario_dir(case_id)` 定位:**已登记的 case 按表**(它把素材放在自己代码旁边的
    `scenario/`),**未登记的 case 跟随 `CASE_ENGINE_CASES_ROOT`**(实时)。这条同时钉住
    "别名=快照 / 函数=实时"与"旧别名不许回来"。
    """
    app_mod = _tool_module()
    before_village = app_mod.VILLAGE_ROOT
    monkeypatch.setenv("MAVIS_ASSETS_ROOT", str(tmp_path / "late"))
    monkeypatch.setenv("CASE_ENGINE_CASES_ROOT", str(tmp_path / "late_cases"))
    assert app_mod.VILLAGE_ROOT == before_village, "别名不该随环境变量变"
    assert app_mod.village_root() == os.path.normpath(str(tmp_path / "late")), \
        "函数必须实时读"
    # 已登记的 case:按表定位,与"场景声明根"无关(素材就在 case 自己的 scenario/ 旁边)
    assert app_mod.case_scenario_dir("case00_village") == os.path.join(
        app_mod._PLATFORM_DIR, "case00", "scenario"), "已登记 case 应按表定位"
    # 未登记的 case:跟随 CASE_ENGINE_CASES_ROOT(实时解析)
    fresh = app_mod.case_scenario_dir("some_new_case")
    assert os.path.normcase(fresh).startswith(
        os.path.normcase(os.path.normpath(str(tmp_path / "late_cases")))), fresh
    assert not hasattr(app_mod, "SCENARIOS_DIR"), \
        "旧别名已删:素材按 case 定位,别再引入一个固定的场景根"


def test_relative_env_is_rejected_by_the_functions(monkeypatch):
    """相对值必须当场报错(与 case01 侧同语义),而不是按 cwd 静默解释。"""
    app_mod = _tool_module()
    monkeypatch.setenv("MAVIS_SCENARIOS_DIR", "some/relative/dir")
    with pytest.raises(Exception) as ei:
        app_mod.scenarios_dir()
    assert "MAVIS_SCENARIOS_DIR" in str(ei.value) or "绝对路径" in str(ei.value)
