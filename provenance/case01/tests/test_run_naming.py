# -*- coding: utf-8 -*-
"""实跑记录命名的测试。

2026-09-19 指出:每跑一次就应该有一个记录,记录该用**真实日期**标识。
旧默认是 `live-<分支>`(不带时间),同一分支跑第二次就没法与上一次区分。
"""
import re

from case01.run_naming import run_id_for


def test_run_id_carries_real_timestamp():
    assert run_id_for("B", when="20260919-1424") == "live-B-20260919-1424"
    assert run_id_for("A", when="20260101-0000") == "live-A-20260101-0000"


def test_same_branch_twice_gets_distinct_names():
    """同一分支跑两次必须能区分——这是名字带时间的唯一理由。"""
    first = run_id_for("B", when="20260919-1424")
    second = run_id_for("B", when="20260919-1501")
    assert first != second


def test_default_uses_now_and_matches_format():
    rid = run_id_for("C")
    assert re.fullmatch(r"live-C-\d{8}-\d{4}", rid), rid


def test_live_switch_reuses_the_same_naming():
    """运维入口与驱动侧必须共用一份命名实现。

    各写一份就会漂移:名字写岔的后果是两个面对同一条记录显示两个名字
    (5002 契约按记录里的 run_id 列、5004 面板按目录名列,实测踩过)。
    """
    import live_switch

    from case01 import run_naming

    assert live_switch.run_id_for is run_naming.run_id_for
