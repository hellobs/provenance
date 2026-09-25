# -*- coding: utf-8 -*-
"""行为追随分析的核心单元测试(合成数据 + 假打分器,零网络)。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from case01.tools.behavior_follow import (  # noqa: E402
    _abs_min, _sample, _weight_deltas, split_window, analyse_intervention,
    _spearman, analyse_simulation)


class FakeScorer:
    """假打分器:行动文本含关键词即给高对齐。"""

    def __init__(self):
        self.calls = 0

    def alignment(self, action, goals):
        self.calls += 1
        out = {}
        for g in goals:
            out[g] = 0.9 if g.lower().split()[0] in action.lower() else 0.1
        return out


def _mk_events(spec):
    """spec: [(time, action_text)] → 决策事件列表。"""
    return [{"id": "e{}".format(i), "time": t, "agent": "X", "action": a}
            for i, (t, a) in enumerate(spec)]


class TestWeightDeltas(unittest.TestCase):
    def test_raised_and_dropped(self):
        raised, dropped = _weight_deltas(
            {"a": 0.35, "b": 0.25, "c": 0.4}, {"a": 0.6, "b": 0.4})
        self.assertEqual(raised, {"a": 0.6, "b": 0.4})
        self.assertEqual(dropped, {"c": 0.4})

    def test_pure_drop_and_raise(self):
        raised, dropped = _weight_deltas({"x": 0.5}, {"y": 0.5})
        self.assertEqual(raised, {"y": 0.5})
        self.assertEqual(dropped, {"x": 0.5})


class TestWindow(unittest.TestCase):
    def test_abs_min_monotonic_across_midnight(self):
        self.assertLess(_abs_min("20250213-23:50"), _abs_min("20250214-00:46"))

    def test_split_windows(self):
        ev = _mk_events([("20250213-09:00", "a"), ("20250213-10:00", "b"),
                         ("20250213-10:30", "c"), ("20250213-11:00", "d")])
        before, after = split_window(ev, "20250213-10:15", 60.0)
        self.assertEqual([e["action"] for e in before], ["b"])
        self.assertEqual([e["action"] for e in after], ["c", "d"])  # (10:15, 11:15] 含 10:30 与 11:00

    def test_sample_deterministic(self):
        ev = _mk_events([("20250213-09:{:02d}".format(i), "x{}".format(i))
                         for i in range(100)])
        s1 = _sample(ev, 30)
        s2 = _sample(ev, 30)
        self.assertEqual([e["id"] for e in s1], [e["id"] for e in s2])
        self.assertEqual(len(s1), 30)


class TestAnalyse(unittest.TestCase):
    IV = {"agent": "X", "sim_time": "20250213-10:00",
          "old_constraints": {"risk": 0.25, "churn": 0.4},
          "new_constraints": {"risk": 0.6}, "note": ""}

    def _events(self, before_kw, after_kw):
        spec = [("20250213-09:{:02d}".format(10 + i), "{} content {}".format(k, i))
                for i, k in enumerate(before_kw * 10)]
        spec += [("20250213-10:{:02d}".format(10 + i), "{} content {}".format(k, i))
                 for i, k in enumerate(after_kw * 10)]
        return _mk_events(spec)

    def test_raised_uses_recorded_alignment(self):
        """升维走记录值:事件里 goal_alignment 有 risk → 前后各取均值。"""
        ev = self._events(["risk"], ["risk"])
        for e, v in zip(ev[:10], [0.2] * 10):
            e["goal_alignment"] = {"risk": v}
        for e, v in zip(ev[10:], [0.5] * 10):
            e["goal_alignment"] = {"risk": v}
        r = analyse_intervention(ev, self.IV, window_min=90.0)
        self.assertIsNotNone(r)
        row = r["dims"][0]
        self.assertEqual(row["kind"], "raised")
        self.assertAlmostEqual(row["align_before"], 0.2)
        self.assertAlmostEqual(row["align_after"], 0.5)
        self.assertGreater(row["shift"], 0)

    def test_dropped_uses_offline_scorer(self):
        """降维走离线补算:假打分器按关键词给分,前后反差应被测出。"""
        scorer = FakeScorer()
        ev = self._events(["churn"] * 1, ["calm"] * 1)
        # before 全是 churn 相关(高),after 全是 calm(churn 低)
        ev = _mk_events([("20250213-09:{:02d}".format(10 + i), "churn thing {}".format(i))
                         for i in range(5)] +
                        [("20250213-10:{:02d}".format(10 + i), "calm thing {}".format(i))
                         for i in range(5)])
        r = analyse_intervention(ev, self.IV, window_min=90.0, scorer=scorer)
        self.assertIsNotNone(r)
        kinds = {d["kind"]: d for d in r["dims"]}
        self.assertIn("dropped", kinds)
        self.assertLess(kinds["dropped"]["shift"], 0, "churn 被降维且行为减少 → 位移为负")
        self.assertGreater(scorer.calls, 0, "降维必须真调了补算器")

    def test_both_sides_empty_window_reason(self):
        """空窗口不再静默:返回带 skip_reason 的字典(2026-09-25 体检)。"""
        ev = _mk_events([("20250213-08:00", "x")])
        r = analyse_intervention(ev, self.IV, window_min=90.0)
        self.assertIsNotNone(r)
        self.assertIn("skip_reason", r)
        self.assertEqual(r["skip_reason"], "窗口内无行动文本")

    def test_scorer_failure_reason_is_not_misleading(self):
        """打分器失败 ≠ 窗口内无行动:skip_reason 必须如实区分(不允许静默)。"""
        class DeadScorer:
            def alignment(self, action, goals):
                return {}
        ev = _mk_events([("20250213-09:{:02d}".format(10 + i), "churn thing {}".format(i))
                         for i in range(5)] +
                        [("20250213-10:{:02d}".format(10 + i), "calm thing {}".format(i))
                         for i in range(5)])
        r = analyse_intervention(ev, self.IV, window_min=90.0, scorer=DeadScorer())
        self.assertIsNotNone(r)
        self.assertIn("打分器失败", r["skip_reason"])


class TestAggregate(unittest.TestCase):
    def test_spearman_perfect(self):
        self.assertEqual(_spearman([1, 2, 3, 4], [10, 20, 25, 40]), 1.0)
        self.assertEqual(_spearman([1, 2, 3], [30, 20, 10]), -1.0)
        self.assertIsNone(_spearman([1, 2], [1, 2]), "n<3 应返回 None")

    def test_analyse_simulation_aggregates(self):
        # 临时 decisions.json
        ev = []
        for i, (t, kw) in enumerate(
                [("20250213-09:{:02d}".format(10 + i), "risk stuff") for i in range(5)] +
                [("20250213-10:{:02d}".format(10 + i), "risk stuff much more") for i in range(5)]):
            e = {"id": "e{}".format(i), "time": t, "agent": "X", "action": kw,
                 "goal_alignment": {"risk": 0.2 if i < 5 else 0.6}}
            ev.append(e)
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                          encoding="utf-8")
        json.dump({"simulation": "s", "events": ev}, tmp)
        tmp.close()
        try:
            iv = [{"agent": "X", "sim_time": "20250213-10:00",
                   "old_constraints": {"risk": 0.25}, "new_constraints": {"risk": 0.6},
                   "simulation": "s", "note": ""}]
            r = analyse_simulation(tmp.name, iv, window_min=90.0)
            self.assertEqual(r["summary"]["n_interventions"], 1)
            self.assertGreater(r["summary"]["mean_shift_raised"], 0)
        finally:
            os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
