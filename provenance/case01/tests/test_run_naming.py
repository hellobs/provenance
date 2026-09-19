# -*- coding: utf-8 -*-
"""记录命名的测试。

2026-09-19 定稿格式:**日期在最前**,带 case 与引擎,实跑再带 HHMM——

    样本  260905-demo-case01-old-B      /  260917-demo-case01-mavis-A
    实跑  260919-live-case01-mavis-B-1424

用户指出:每跑一次就应该有一个记录,记录该用真实日期标识;而"日期在前"才能按时间排序,
"带 case"才能在 case00 / case01 并存的现在分清归属。
"""
import re

from case01.run_naming import live_run_id, sample_run_id


def test_live_run_id_has_date_first_case_engine_branch_and_time():
    assert live_run_id("B", when="260919-1424") == "260919-live-case01-mavis-B-1424"
    assert live_run_id("A", when="260101-0000") == "260101-live-case01-mavis-A-0000"


def test_live_run_id_honours_case_and_engine():
    assert live_run_id("B", when="260919-1424", case="case02", engine="old") == \
        "260919-live-case02-old-B-1424"


def test_sample_run_id_has_no_time():
    assert sample_run_id("case01", "old", "B", "260905") == "260905-demo-case01-old-B"
    assert sample_run_id("case01", "mavis", "A", "260917") == "260917-demo-case01-mavis-A"


def test_date_first_sorts_chronologically():
    """日期在最前的目的就是按时间排序。

    旧格式 `live-<分支>-<日期>` 会把同一分支的记录排在一起,一眼看不出哪次最新。
    """
    ids = [sample_run_id("case01", "old", "B", "260905"),
           sample_run_id("case01", "mavis", "A", "260917"),
           live_run_id("B", when="260919-1424")]
    assert sorted(ids) == ids


def test_same_branch_twice_gets_distinct_names():
    """同一分支跑两次必须能区分——这是实跑名字带 HHMM 的理由。"""
    assert live_run_id("B", when="260919-1424") != live_run_id("B", when="260919-1501")


def test_default_uses_now_and_matches_format():
    rid = live_run_id("C")
    assert re.fullmatch(r"\d{6}-live-case01-mavis-C-\d{4}", rid), rid


def test_live_switch_reuses_the_same_naming():
    """运维入口与驱动侧必须共用一份命名实现。

    各写一份就会漂移:名字写岔的后果是两个面对同一条记录显示两个名字
    (5002 契约按记录里的 run_id 列、5004 面板按目录名列,实测踩过)。
    """
    import live_switch

    from case01 import run_naming

    assert live_switch.live_run_id is run_naming.live_run_id
