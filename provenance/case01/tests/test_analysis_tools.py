# -*- coding: utf-8 -*-
"""内化定量与 Router 敏感性工具的核心单元测试(纯函数,合成数据)。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from case01.tools.internalization_metrics import (  # noqa: E402
    _parse_t, snapshot_at, snapshots_in_window, displacement, analyse_intervention)
from case01.tools.router_sensitivity import (  # noqa: E402
    _bigrams, jaccard, cross_match, risk_dist)


class TestTimeParsing(unittest.TestCase):
    def test_absolute_minutes_monotonic_across_midnight(self):
        d1 = _parse_t("20250213-23:50")
        d2 = _parse_t("20250214-00:46")
        self.assertGreater(d2, d1, "跨午夜必须单调递增")

    def test_bad_input(self):
        self.assertIsNone(_parse_t("garbage"))
        self.assertIsNone(_parse_t(""))
        self.assertIsNone(_parse_t("20250213"))


class TestSnapshots(unittest.TestCase):
    def setUp(self):
        self.traj = [
            {"sim_time": "20250213-09:30", "step": 1},
            {"sim_time": "20250213-10:00", "step": 15},
            {"sim_time": "20250213-10:30", "step": 30},
            {"sim_time": "20250213-11:00", "step": 45},
        ]

    def test_before_takes_latest_past(self):
        self.assertEqual(snapshot_at(self.traj, "20250213-10:40")["step"], 30,
                         "before 应取最近的过去快照,不是最早的")

    def test_after_takes_earliest_future(self):
        self.assertEqual(snapshot_at(self.traj, "20250213-10:00", before=False)["step"], 30)

    def test_window(self):
        win = snapshots_in_window(self.traj, "20250213-10:00", 60.0)
        self.assertEqual([x["step"] for x in win], [30, 45])


class TestDisplacement(unittest.TestCase):
    def test_toward_target_positive(self):
        d = displacement({"a": 0.4, "b": 0.2}, {"a": 0.3, "b": 0.2}, {"a": 0.2})
        self.assertAlmostEqual(d["a"], 0.1)
        self.assertNotIn("b", d, "target 没有的维度不进结果")

    def test_away_negative(self):
        d = displacement({"a": 0.2}, {"a": 0.4}, {"a": 0.2})
        self.assertAlmostEqual(d["a"], -0.2)


class TestAnalyseIntervention(unittest.TestCase):
    def _traj(self):
        # 干预前 a=0.2;干预把 a 目标抬到 0.4;之后 a 逐步到 0.35
        def snap(t, step, a):
            return {"sim_time": t, "step": step,
                    "tendency": {"a": a, "b": 0.5, "c": 0.3}}
        return [snap("20250213-09:00", 1, 0.2),
                snap("20250213-09:30", 10, 0.2),
                snap("20250213-10:00", 20, 0.28),
                snap("20250213-10:30", 30, 0.33),
                snap("20250213-11:00", 40, 0.35)]

    def test_internalization_detected(self):
        iv = {"agent": "X", "sim_time": "20250213-09:45",
              "old_constraints": {"a": 0.2, "b": 0.5, "c": 0.3},
              "new_constraints": {"a": 0.4}, "note": ""}
        r = analyse_intervention(self._traj(), iv, window_min=90.0)
        self.assertIsNotNone(r)
        self.assertGreater(r["mean_displacement_treated"], 0, "a 朝 0.4 移动,应为正")
        self.assertGreaterEqual(r["internalization_gap"], 0)
        self.assertIn("b", r["control_dims"])
        self.assertIn("a", r["treated_dims"])

    def test_no_before_snapshot_returns_none(self):
        iv = {"agent": "X", "sim_time": "20250212-09:00",
              "old_constraints": {"a": 0.2}, "new_constraints": {"a": 0.4}}
        self.assertIsNone(analyse_intervention(self._traj(), iv, window_min=90.0))


class TestRouterSensitivityMetrics(unittest.TestCase):
    def test_cross_match_identical(self):
        self.assertGreater(cross_match(["投资分析师在缺乏核实依据的情况下做了买入判断"],
                                       ["投资分析师在缺乏核实依据的情况下做了买入判断"]), 0.9)

    def test_cross_match_disjoint(self):
        self.assertEqual(cross_match(["今天天气很好"], ["股票估值模型"]), 0.0)

    def test_risk_dist(self):
        d = risk_dist([{"risk": "high"}, {"risk": "High"}, {"risk": "medium"}])
        self.assertEqual(d["high"], 2)
        self.assertEqual(d["medium"], 1)


if __name__ == "__main__":
    unittest.main()
