# -*- coding: utf-8 -*-
"""底噪对照的单元测试(合成数据 + 假打分器,零网络)。

底噪对照是"行为位移是否可区分于噪声"的参照系 —— 2026-10-05 从文档里
的一次性手工数字收进代码。这些测试钉住三件事:
1. 时间编解码往返一致(对照窗心需要把绝对分钟转回 sim_time);
2. 对照窗**真的躲开了干预**(距离 > 窗宽);
3. 分布统计量的口径(P50/P75/max 取的是 |位移|)。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from case01.tools.behavior_follow import (  # noqa: E402
    _abs_min, _min_to_time, _interval_of, _reference_windows,
    noise_floor, _merge_noise, compare_to_noise)


class FakeScorer:
    """假打分器:行动文本含维度关键词即给高对齐(与既有测试同口径)。"""

    def __init__(self):
        self.calls = 0

    def alignment(self, action, goals):
        self.calls += 1
        out = {}
        for g in goals:
            out[g] = 0.9 if g.lower().split()[0] in action.lower() else 0.1
        return out


def _mk(spec):
    """spec: [(time, action)] → 决策事件。"""
    return [{"id": "e{}".format(i), "time": t, "agent": "X", "action": a}
            for i, (t, a) in enumerate(spec)]


def _uniform(start_h, end_h, minute_step=30):
    """start_h..end_h 之间等间隔铺事件(同一天)。"""
    out, h, m = [], start_h, 0
    while h < end_h or (h == end_h and m == 0):
        out.append(("20250213-{:02d}:{:02d}".format(h, m), "risk content {}".format(len(out))))
        m += minute_step
        if m >= 60:
            m -= 60
            h += 1
    return out


class TestTimeCodec(unittest.TestCase):
    def test_round_trip(self):
        for s in ["20250213-12:02", "20250214-00:46", "20250213-09:30",
                  "20250215-22:32"]:
            self.assertEqual(_min_to_time(_abs_min(s)), s,
                             "绝对分钟编解码必须往返一致({})".format(s))

    def test_monotonic_across_days(self):
        self.assertLess(_abs_min("20250213-23:50"), _abs_min("20250214-00:10"))


class TestReferenceWindows(unittest.TestCase):
    def test_windows_avoid_interventions(self):
        """对照窗心到任一干预的距离必须 > 窗宽(否则窗会吃到干预)。"""
        ev = _mk(_uniform(0, 24))
        ivs = [{"agent": "X", "sim_time": "20250213-12:00"}]
        w = 120.0
        centers = _reference_windows(ev, ivs, w, 0.0)
        self.assertTrue(centers, "24 小时跨度应能挑出对照窗")
        iv_min = _abs_min("20250213-12:00")
        for c in centers:
            self.assertGreater(abs(c - iv_min), w,
                               "对照窗心离干预 %.0f 分钟,窗会吃到干预" % abs(c - iv_min))

    def test_no_window_when_span_too_short(self):
        """跨度不足两倍窗宽 → 挑不出对照窗(如实返回空,不硬凑)。"""
        ev = _mk([("20250213-09:00", "a"), ("20250213-10:00", "b")])
        self.assertEqual(_reference_windows(ev, [], 120.0, 0.0), [])

    def test_cap_at_target_count(self):
        """可行窗极多时封顶到 DEFAULT_NOISE_WINDOWS,不无限膨胀。"""
        ev = _mk(_uniform(0, 24))
        centers = _reference_windows(ev, [], 120.0, 0.0)
        self.assertLessEqual(len(centers), 20)


class TestNoiseFloor(unittest.TestCase):
    def test_reports_abs_distribution(self):
        """底噪统计量取 |位移| 的 P50/P75/max,并给样本量。"""
        ev = _mk(_uniform(0, 24))
        nf = noise_floor(ev, [], ["risk"], window_min=120.0, scorer=FakeScorer())
        self.assertGreater(nf["n_windows"], 0)
        self.assertIn("median_abs", nf)
        self.assertIn("p75_abs", nf)
        self.assertIn("max_abs", nf)
        self.assertLessEqual(nf["median_abs"], nf["max_abs"], "中位不可能超过最大")
        self.assertGreaterEqual(nf["max_abs"], 0)

    def test_no_dims_returns_empty(self):
        """没有可对照维度 → 不假装算了,如实返回 n_windows=0。"""
        ev = _mk(_uniform(0, 24))
        nf = noise_floor(ev, [], [], window_min=120.0, scorer=FakeScorer())
        self.assertEqual(nf["n_windows"], 0)
        self.assertIn("note", nf)

    def test_short_span_returns_empty_with_note(self):
        ev = _mk([("20250213-09:00", "a"), ("20250213-09:30", "b")])
        nf = noise_floor(ev, [], ["risk"], window_min=120.0, scorer=FakeScorer())
        self.assertEqual(nf["n_windows"], 0)
        self.assertIn("note", nf)


class TestVerdict(unittest.TestCase):
    def test_in_band_and_above_band(self):
        nf = {"n_windows": 10, "max_abs": 0.05}
        self.assertEqual(compare_to_noise(0.03, nf), "in_band")
        self.assertEqual(compare_to_noise(-0.03, nf), "in_band",
                         "判读看绝对值,负位移同样可比")
        self.assertEqual(compare_to_noise(0.08, nf), "above_band")
        self.assertEqual(compare_to_noise(-0.08, nf), "above_band")

    def test_none_when_no_noise_floor(self):
        self.assertIsNone(compare_to_noise(0.1, {"n_windows": 0}))
        self.assertIsNone(compare_to_noise(0.1, {}))


class TestMerge(unittest.TestCase):
    def test_merge_takes_widest_band_and_sums_windows(self):
        by_agent = {
            "A": {"n_windows": 5, "median_abs": 0.01, "p75_abs": 0.02,
                  "max_abs": 0.03, "window_min": 120.0},
            "B": {"n_windows": 7, "median_abs": 0.04, "p75_abs": 0.05,
                  "max_abs": 0.09, "window_min": 120.0},
        }
        m = _merge_noise(by_agent)
        self.assertEqual(m["n_windows"], 12)
        self.assertEqual(m["n_agents"], 2)
        self.assertEqual(m["max_abs"], 0.09, "全局带取最宽的 max")
        self.assertEqual(m["median_abs"], 0.04, "中位取各 agent 中位的中位")

    def test_merge_empty(self):
        self.assertIsNone(_merge_noise({}))
        self.assertIsNone(_merge_noise({"A": {"n_windows": 0}}))


if __name__ == "__main__":
    unittest.main()
