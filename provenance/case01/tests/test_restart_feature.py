# -*- coding: utf-8 -*-
"""「重开一局」相关的回归测试(2026-09-19 用户反馈"这个功能有问题"后补)。

查出来的两个真问题:
1. **撞名覆盖**:实跑名字只到分钟,同一分钟内重开一局 → 新一局的 run_id 与上一局相同,
   `runs_injector/<id>/raw.json` 与 `runs/<id>/run.json` 被**静默覆盖**。
   → `unique_run_id()` 让开已占用的名字。
2. **保持期一过服务就退出,页面上的按钮变成谎言**:按钮还在,点了没反应。
   → 只要注册了 on_restart 且没加 `--exit-after-hold`,保持期结束后继续等重开。
"""
import os

from case01.run_naming import live_run_id, unique_run_id


class TestUniqueRunId:
    def test_free_name_is_used_as_is(self, tmp_path):
        base = live_run_id("B", when="260919-2147")
        assert unique_run_id(base, roots=(str(tmp_path),)) == base

    def test_taken_name_gets_a_suffix(self, tmp_path):
        base = live_run_id("B", when="260919-2147")
        os.makedirs(tmp_path / base)
        assert unique_run_id(base, roots=(str(tmp_path),)) == base + "-2"

    def test_suffix_keeps_incrementing(self, tmp_path):
        base = live_run_id("B", when="260919-2147")
        for name in (base, base + "-2", base + "-3"):
            os.makedirs(tmp_path / name)
        assert unique_run_id(base, roots=(str(tmp_path),)) == base + "-4"

    def test_both_roots_are_checked(self, tmp_path):
        """runs/ 与 runs_injector/ 任一占用都要让开(成品与原始记录同用一个名字)。"""
        base = live_run_id("B", when="260919-2147")
        a, b = tmp_path / "runs", tmp_path / "raw"
        a.mkdir()
        b.mkdir()
        os.makedirs(b / base)
        assert unique_run_id(base, roots=(str(a), str(b))) == base + "-2"

    def test_judge_mode_uses_auto_not_a_fallback_branch(self):
        """judge 模式下分支要等 T0 才定,名字里就不该写一个假的分支字母。"""
        assert live_run_id("auto", when="260919-2201").endswith("-auto-2201")


class TestRestartDoesNotExitWhileButtonIsShown:
    """保持期结束后若页面上仍有按钮,服务必须继续等(而不是让按钮点了没反应)。"""

    def _source(self):
        import io
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "vizkit", "live_run.py")
        return io.open(p, encoding="utf-8").read()

    def test_default_keeps_waiting(self):
        src = self._source()
        assert "can_restart and not args.exit_after_hold" in src, "默认必须继续等重开"
        assert "while not restart_flag.wait(timeout=3600)" in src, "要继续等,不能退出"

    def test_opt_in_exit_flag_exists(self):
        src = self._source()
        assert "--exit-after-hold" in src, "旧行为要能显式要回来(CI/批处理)"
