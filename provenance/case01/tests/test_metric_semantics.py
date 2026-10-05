# -*- coding: utf-8 -*-
"""数理与逻辑正确性专项:内化/行为追随指标的定义边界与退化形态。

为什么单独一个文件:
    `test_analysis_tools.py` 覆盖的是"函数算得对"(合成数据的正常路径);
    本文件覆盖的是**定义本身对不对、以及在真实数据形态下会不会退化**。
    两类问题的性质完全不同:
      - 前者是 bug(算错),修完就该绿;
      - 后者是**口径**(指标在什么输入下失去解释力),必须**钉住事实**,
        免得后人照着字段名想当然。

本文件钉四件事:
  A. `_tv` 的归一化口径:它是"平均每维的 TVD",不是标准 TVD(值域 [0,0.5] 而非 [0,1]);
  B. `internalization_gap` 在真实干预数据上**恒等于** `mean_displacement_treated`
     —— 因为 control 集恒空,那个"减号"从未生效(2026-09-27 文档已诚实披露);
  C. 过冲判据的边界:`s1 == 0`(精确命中)被计入过冲,语义上不该;
  D. 动力学指标的**定义式复算**:延迟/持续性/过冲必须与文档口径一致。

A/B/C 三条都是 2026-10-05 数理审计的产物,配 `docs/261004_平台全面体检_只读核查.md` G5 段(#27–#34)。
"""
import glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from case01.tools import internalization_metrics as im  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
CK_DIR = os.path.join(REPO_ROOT, "provenance", "results", "checkpoints")
INTERVENTIONS = os.path.join(CK_DIR, "interventions.json")


def _tv_replica(v, tgt):
    """复刻 `analyse_intervention` 内嵌的 `_tv`(源码 line 133-135)。

    它没有暴露为模块级函数 —— 这本身是个可测性缺陷,所以这里复刻并**钉住**
    它算的到底是什么。若将来源码改了归一化方式,本测试会与 `_tv` 的实际输出
    不一致,可作为改动信号。
    """
    common = [d for d in tgt if d in v]
    if not common:
        return None
    return sum(abs(v[d] - tgt[d]) for d in common) / (2 * len(common))


# ---------------------------------------------------------------------------
# A. _tv 的归一化口径
# ---------------------------------------------------------------------------

class TestTotalVariationSemantics(unittest.TestCase):
    """`_tv` 除以了维度数 —— 它**不是**标准全变差距离(TVD)。"""

    def test_two_opposite_simplex_points_give_half_not_one(self):
        """完全相反的两个单纯形点为 0.5(而非标准 TVD 的 1.0)。

        这是"除以 len(common)"的直接后果。记下来,因为:
          - 数值本身**没错**(before/after 同除一个常数,单调性与符号不变);
          - 但它**不能**读作"概率距离",也不该在文档里写成 "TV";
          - 跨记录比较时,维度数不同会带来系统偏差。
        """
        a = {"x": 1.0, "y": 0.0}
        b = {"x": 0.0, "y": 1.0}
        self.assertAlmostEqual(_tv_replica(a, b), 0.5)
        # 标准 TVD 会是 L1/2 = 1.0 —— 用一行显式对照,免得后人误以为 0.5 是 bug
        l1 = sum(abs(a[d] - b[d]) for d in a)
        self.assertAlmostEqual(l1 / 2, 1.0)

    def test_value_range_is_zero_to_half(self):
        """值域上界是 0.5 而非 1.0 —— 这是"平均每维"的必然结果。"""
        a = {"x": 1.0, "y": 0.0}
        b = {"x": 0.0, "y": 1.0}
        self.assertLessEqual(_tv_replica(a, b), 0.5)

    def test_identical_distributions_give_zero(self):
        a = {"x": 0.4, "y": 0.6}
        self.assertAlmostEqual(_tv_replica(a, dict(a)), 0.0)

    def test_dimension_count_shrinks_the_value(self):
        """维度数不同 → 同量级差异被压得更低,跨记录不可直接比。

        两个分布在第 1 维上差 0.2:
          - 2 维时贡献 0.2/(2*2) = 0.05
          - 4 维时贡献 0.2/(2*4) = 0.025
        所以"TV 0.05"这种写法在维度数不同的记录间**不可比**。
        """
        two = _tv_replica({"a": 0.6, "b": 0.4}, {"a": 0.4, "b": 0.6})
        four = _tv_replica({"a": 0.6, "b": 0.1, "c": 0.1, "d": 0.2},
                           {"a": 0.4, "b": 0.2, "c": 0.2, "d": 0.2})
        self.assertAlmostEqual(two, 0.1)
        self.assertAlmostEqual(four, 0.05)
        self.assertNotAlmostEqual(two, four)


# ---------------------------------------------------------------------------
# B. control 集恒空 → internalization_gap 退化为 mean_treated
# ---------------------------------------------------------------------------

def _load_corpus_rows():
    """跑全部有干预记录的真实模拟,收集逐条结果(读不到数据则跳过)。"""
    if not os.path.isfile(INTERVENTIONS):
        return None
    with open(INTERVENTIONS, encoding="utf-8") as f:
        iv_all = json.load(f)
    by_sim = {}
    for x in iv_all:
        if x.get("simulation"):
            by_sim.setdefault(x["simulation"], []).append(x)
    rows = []
    for sim in sorted(by_sim):
        ck = os.path.join(CK_DIR, sim)
        if not os.path.isdir(ck):
            continue
        r = im.analyse_simulation(ck, by_sim[sim])
        for row in r["interventions"]:
            if "internalization_gap" in row:
                rows.append((sim, row))
    return rows


class TestControlDegeneratesToEmpty(unittest.TestCase):
    """真实干预都是**同维度集重归一化**,故 control 恒空,gap ≡ mean_treated。

    这是**结构性退化**,不是算错:
      `control = {d for d in old_c if d not in new_c and ...}`
      而真实 new_c 与 old_c 的**维度名完全相同**(只改了权重),故恒为空集。
    后果:`internalization_gap` 这个字段名暗示的"干预 − 对照"在数据上
    **恒等于 mean_displacement_treated**,那个减号从未生效。

    文档 `case01/docs/内化与敏感性_定量分析_20260924.md` §一-2 已诚实披露
    ("对照维度位移恒为 0")。本测试把这条**从散文变成可执行判据**,
    防止后人照着字段名把 `gap` 读成"相对未干预维度的增益"。
    """

    @classmethod
    def setUpClass(cls):
        cls.rows = _load_corpus_rows()

    def setUp(self):
        if not self.rows:
            self.skipTest("无 interventions.json 或 checkpoint 数据(产物不入库)")

    def test_control_is_always_empty_on_real_data(self):
        """真实数据上 control 集恒空 —— 若将来有干预增删维度,这条会红并提醒复核。"""
        with_ctrl = [(sim, r) for sim, r in self.rows if r["control_dims"]]
        self.assertEqual(
            with_ctrl, [],
            "真实数据出现了非空 control_dims:内化的对照口径可能已改变,"
            "请复核 docs/内化与敏感性 的 §一-2 与答辩口径")

    def test_gap_equals_mean_treated_exactly(self):
        """gap 与 mean_treated 逐条完全相等 —— 减号从未生效。"""
        bad = [(sim, r["agent"], r["internalization_gap"],
                r["mean_displacement_treated"])
               for sim, r in self.rows
               if abs(r["internalization_gap"] - r["mean_displacement_treated"]) > 1e-9]
        self.assertEqual(bad, [], "gap 不再等于 mean_treated,说明 control 开始起作用了")

    def test_mean_control_is_always_zero(self):
        nz = [(sim, r["agent"], r["mean_displacement_control"])
              for sim, r in self.rows
              if r["mean_displacement_control"] != 0.0]
        self.assertEqual(nz, [], "mean_displacement_control 非零:口径变了")

    def test_the_headline_count_is_about_treated_only(self):
        """"11/12 正"这个结论只能读作"干预维度自身朝目标移动",不含对照成分。

        这条不是在质疑数字,是在**限定它的解释范围**:它不能支撑
        "干预维度比未干预维度动得多"这个更强的说法。
        """
        pos = sum(1 for _, r in self.rows if r["internalization_gap"] > 0)
        pos_treated = sum(1 for _, r in self.rows
                          if r["mean_displacement_treated"] > 0)
        self.assertEqual(pos, pos_treated,
                         "gap 与 treated 的正数计数应一致(因二者恒等)")


# ---------------------------------------------------------------------------
# C. 过冲判据的边界:s1 == 0 被计入过冲
# ---------------------------------------------------------------------------

def _overshoot_cross(s0: float, s1: float) -> bool:
    """复刻源码 line 208 的判据。"""
    return s0 * s1 < 0 or (s0 != 0 and s1 == 0)


class TestOvershootBoundary(unittest.TestCase):
    """过冲 = 越过目标;`s1 == 0`(恰好命中目标)被算作过冲,语义上不该。

    **实测影响为零**:真实数据里 `s1 == 0` 触发 0 次(浮点值恰等于目标的概率为 0),
    所以这是**理论瑕疵,未污染任何已发布的数字**。记下来是为了:
      - 若将来用整数化的倾向表示(如离散档位),这条会真触发;
      - 后人重读判据时不必再纠结一次。
    """

    def test_sign_flip_is_true_overshoot(self):
        self.assertTrue(_overshoot_cross(0.2, -0.1), "越过目标应判过冲")
        self.assertTrue(_overshoot_cross(-0.2, 0.1), "越过目标应判过冲")

    def test_same_side_is_not_overshoot(self):
        self.assertFalse(_overshoot_cross(0.2, 0.1), "同侧未越过")
        self.assertFalse(_overshoot_cross(-0.2, -0.1), "同侧未越过")

    def test_exact_hit_is_currently_counted_but_should_not_be(self):
        """`s1 == 0` 是"精确命中",不是"越过再回来" —— 当前实现判 True。"""
        self.assertTrue(_overshoot_cross(0.2, 0.0),
                        "当前实现把精确命中算作过冲(记录事实,非认可)")
        # 语义上正确的判据应当只认符号翻转
        strictly_correct = (0.2 * 0.0) < 0
        self.assertFalse(strictly_correct, "严格判据不把命中算作过冲")

    def test_starting_at_target_is_not_overshoot(self):
        self.assertFalse(_overshoot_cross(0.0, 0.1), "起点即目标不算过冲")


# ---------------------------------------------------------------------------
# D. 动力学指标定义式复算
# ---------------------------------------------------------------------------

class TestDynamicsDefinitions(unittest.TestCase):
    """延迟 / 持续性 的定义边界(合成数据,不依赖真实产物)。"""

    def _traj(self, times, values):
        return [{"sim_time": t, "step": i, "tendency": {"a": v}}
                for i, (t, v) in enumerate(zip(times, values))]

    def test_latency_is_first_time_reaching_half_peak(self):
        """延迟 = 位移首次达到峰值一半的 sim 时钟分钟差。

        构造:干预前 a=0.9(目标 0.0,初始 gap 0.9);之后 0.9→0.8→0.5→0.4。
        位移序列 = 0.0, 0.1, 0.4, 0.5;峰值 0.5;半峰 0.25 → 首次达到在"0.4"那点。
        """
        times = ["20250213-08:00", "20250213-08:30",
                 "20250213-09:30", "20250213-10:00", "20250213-10:30"]
        traj = self._traj(times, [0.9, 0.9, 0.8, 0.5, 0.4])
        iv = {"sim_time": "20250213-09:00", "agent": "A",
              "old_constraints": {"a": 0.9}, "new_constraints": {"a": 0.0}}
        d = im.dynamics_metrics(traj, iv)
        self.assertIsNotNone(d)
        # 09:30 的位移 = 0.9-0.8... 注意 gap0 = |0.9-0.0| = 0.9
        # 09:30: gap=|0.8-0|=0.8 → disp=0.1
        # 10:00: gap=0.5 → disp=0.4
        # 10:30: gap=0.4 → disp=0.5  ← 峰值
        self.assertAlmostEqual(d["peak_displacement"], 0.5)
        self.assertAlmostEqual(d["final_displacement"], 0.5)
        # 半峰 0.25:首次达到是 10:00(0.4),距 09:00 为 60 分钟
        self.assertEqual(d["latency_min"], 60.0)

    def test_persistence_ratio_and_label(self):
        """持续性 = 视界末位移 / 峰值;<0.5 判 rebound。"""
        times = ["20250213-08:00", "20250213-09:30",
                 "20250213-10:00", "20250213-10:30"]
        # 干预前 0.9;之后 0.4(disp .5) → 0.85(disp .05) 冲高回落
        traj = self._traj(times, [0.9, 0.9, 0.4, 0.85])
        iv = {"sim_time": "20250213-09:00", "agent": "A",
              "old_constraints": {"a": 0.9}, "new_constraints": {"a": 0.0}}
        d = im.dynamics_metrics(traj, iv)
        self.assertAlmostEqual(d["peak_displacement"], 0.5)
        self.assertAlmostEqual(d["persistence_ratio"], 0.1)
        self.assertEqual(d["persistence"], "rebound")

    def test_no_observation_after_intervention_is_explicit(self):
        """干预后无观测点:延迟 None、峰值 0 —— 不编造。"""
        traj = self._traj(["20250213-08:00"], [0.9])
        iv = {"sim_time": "20250213-09:00", "agent": "A",
              "old_constraints": {"a": 0.9}, "new_constraints": {"a": 0.0}}
        d = im.dynamics_metrics(traj, iv)
        self.assertIsNotNone(d)
        self.assertIsNone(d["latency_min"])
        self.assertEqual(d["peak_displacement"], 0.0)


# ---------------------------------------------------------------------------
# E. 行为层"底噪对照"的可复算性(2026-10-05 审计发现)
# ---------------------------------------------------------------------------

class TestNoiseFloorReproducibility(unittest.TestCase):
    """底噪对照:曾经**无法从仓库复算**,2026-10-05 已收进代码。

    历史(数理审计 G5 #34):`docs/GTC研究边界声明.md` §三 line 44 把
    "行为层位移:与底噪不可区分(中位 0.026 vs 0.019)"的来源标为
    `behavior_follow.json`;但当时该工具的 `summary` 只有 10 个键,
    **没有任何底噪/对照窗字段**:

        ['mean_shift_dropped','mean_shift_raised','n_dim_pairs','n_dropped',
         'n_dropped_negative','n_interventions','n_raised','n_raised_positive',
         'n_skipped','spearman_dw_vs_shift']

    且 `results/analysis/<sim>/behavior_follow.json` 的现有产物重算后,
    22 个 shift 值的中位是 **~0.013**(en7 0.0126 / en8 0.0131),
    与文档写的 **0.0256** 不在同一量级。

    这不是说文档在编数字 —— 它确有来源:
      - 底噪对照是 2026-09-25 用**一次性脚本**手工算的(后来没入库);
      - 观察位移当时用的窗口/样本与现在重算的不同(`--window` / `--sample` 可调)。

    但对**证据链**而言这构成一个真实缺口:一个被写进"评委速查表"的数字
    **没有可复算的路径**。本测试当年把这条事实钉住(守卫);

    **2026-10-05 用户决策 ③:收进代码** —— `behavior_follow.py` 新增
    `noise_floor()` / `compare_to_noise()`,产物出 `summary.noise_floor`。
    守卫随之**翻转**:现在断言"字段与实现都在",防止回落。

    注意:这条**只**关乎可复算性,**不**质疑"不主张行为级改变"这个结论 ——
    该结论有独立的、方向一致的证据(升维/降维双向选择性不成立、Spearman≈0,
    且唯一超带的观察位移方向还与预期相反)。
    """

    NOISE_KEYS = ("noise", "baseline", "floor", "control_window")

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(CK_DIR):
            cls.files = []
            return
        cls.files = sorted(
            os.path.join(CK_DIR, "..", "analysis", d, "behavior_follow.json")
            for d in os.listdir(os.path.join(CK_DIR, "..", "analysis"))
            if os.path.isfile(os.path.join(CK_DIR, "..", "analysis", d,
                                           "behavior_follow.json")))

    def setUp(self):
        if not self.files:
            self.skipTest("无 behavior_follow.json 产物(产物不入库)")

    def test_documented_headline_median_is_not_reproducible(self):
        """文档的"观察位移中位 0.0256"要按**升维组**口径才复算得出。

        审计过程(2026-10-05):
          - 全部 22 个 shift 的中位 = 0.005~0.013,对不上 0.0256;
          - 按干预聚合(11 条)的中位 = 0.0167,仍对不上;
          - **仅 raised 维度(11 个)的中位 = 0.0245** ← 与 0.0256 同量级。

        结论:文档的 0.0256 是**升维组**位移中位,口径自洽,数字真实;
        只是文档没写明分组口径 —— 这条测试把这个口径**写死**,
        免得后人像本次审计一样重走一遍弯路(或误以为数字有问题)。

        底噪那半("vs 0.019")当时不可复算,现已收进代码,
        见 `test_noise_floor_is_now_in_the_products_and_in_code`。
        """
        raised = []
        for f in self.files:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            for row in d.get("interventions") or []:
                for dim in row.get("dims") or []:
                    if dim.get("kind") == "raised" and \
                            isinstance(dim.get("shift"), (int, float)):
                        raised.append(dim["shift"])
        self.assertTrue(raised, "产物里没有 raised 维度的 shift")
        raised.sort()
        median = raised[len(raised) // 2]
        # 与文档 0.0256 同量级(放宽到 0.02~0.03,容忍样本微差)
        self.assertGreater(median, 0.018,
                           "raised 组中位 {:.4f} 掉出了文档 0.0256 的量级".format(median))
        self.assertLess(median, 0.035,
                        "raised 组中位 {:.4f} 超出文档量级 —— 请重核文档口径".format(median))

    def test_noise_floor_is_now_in_the_products_and_in_code(self):
        """底噪对照**已入库**(2026-10-05):产物有字段,代码有实现。

        历史:`docs/GTC研究边界声明.md` 曾写
        "行为层位移:与底噪不可区分(中位 0.026 vs 0.019)",来源标
        `behavior_follow.json` —— 但当时产物**无该字段**、代码**无该逻辑**、
        `git show ef1e556` 也不含计算脚本 ⇒ 那个 0.019 是一次性手工数字,
        写进评委速查表却**没有可复算路径**(数理审计 G5 #34 记为缺口)。

        本测试此前断言"产物里没有底噪字段"(守卫),现已**按用户决策 ③
        收进代码**:`case01/tools/behavior_follow.py` 新增 `noise_floor()`,
        `analyse_simulation` 产出 `summary.noise_floor`。测试随之翻转成
        **断言其存在**,防止再次退化为不可复算。
        """
        for f in self.files:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            keys = set(d.get("summary") or {})
            hit = [k for k in keys if any(t in k.lower() for t in self.NOISE_KEYS)]
            # stock-en7 / stock-en8 应有;demo(跨度不足)允许无
            sim = os.path.basename(os.path.dirname(f))
            if sim.startswith("demo"):
                continue
            self.assertTrue(
                hit,
                "{} 的 summary 里没有底噪字段 —— 底噪对照回落成了不可复算".format(sim))

        # 代码里必须有可复算实现(否则字段是凭空塞的)
        tool_src = os.path.join(REPO_ROOT, "provenance", "case01", "tools",
                                "behavior_follow.py")
        with open(tool_src, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("def noise_floor", src, "behavior_follow 缺 noise_floor 实现")
        self.assertIn("底噪", src, "behavior_follow 缺底噪口径说明")
        self.assertIn("compare_to_noise", src, "缺带内/超带判读函数")


# ---------------------------------------------------------------------------
# F. 前提不变量:value_tendency 是单纯形上的点(Σ=1, 非负)
# ---------------------------------------------------------------------------

class TestValueTendencySimplexInvariant(unittest.TestCase):
    """所有内化统计的前提:`value_tendency` 每维非负且 Σ=1。

    为什么这条重要:
      - `displacement` 用 `|tendency - target|`,语义是"朝目标的绝对差";
        target 与 tendency 都在单纯形上,差值才有界、可比;
      - `_tv` 除以 `2*len(common)` 之所以还能当"平均每维 TVD",
        也正是因为两边都在单纯形上(L1/2 ∈ [0,1]);
      - 若某天 Σ ≠ 1(如更新后忘了重归一化),上述解释全部失效 ——
        而统计量**不会报错**,只会静默给出无意义的数。

    文档 `GTC研究边界声明.md` §一 line 13 把"Σ=1 守恒"列为
    "主张状态级内化"的依据之一,本测试就是那条依据的可执行版本。
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(CK_DIR):
            cls.samples = []
            return
        cls.samples = []
        files = sorted(glob.glob(os.path.join(CK_DIR, "*", "simulate-*.json")))
        for path in files[:400]:      # 抽样上限,避免测试过慢
            try:
                with open(path, encoding="utf-8") as fh:
                    obj = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            for agent, av in (obj.get("agents") or {}).items():
                vt = (av.get("status") or {}).get("value_tendency") or {}
                vals = [v for v in vt.values() if isinstance(v, (int, float))]
                if vals:
                    cls.samples.append((path, agent, vals))

    def setUp(self):
        if not self.samples:
            self.skipTest("无 checkpoint 数据(产物不入库)")

    def test_sum_is_exactly_one(self):
        bad = [(os.path.basename(p), a, sum(v)) for p, a, v in self.samples
               if abs(sum(v) - 1.0) > 1e-6]
        self.assertEqual(bad[:5], [], "存在 Σ≠1 的 value_tendency:{}".format(bad[:5]))

    def test_all_non_negative(self):
        bad = [(os.path.basename(p), a, [x for x in v if x < 0])
               for p, a, v in self.samples if any(x < 0 for x in v)]
        self.assertEqual(bad[:5], [], "出现负权重(不再是单纯形点):{}".format(bad[:5]))

    def test_each_dim_within_unit_interval(self):
        bad = [(os.path.basename(p), a, [x for x in v if x > 1.0 + 1e-9])
               for p, a, v in self.samples if any(x > 1.0 + 1e-9 for x in v)]
        self.assertEqual(bad[:5], [], "单维超过 1(违反 Σ=1 前提):{}".format(bad[:5]))


if __name__ == "__main__":
    unittest.main()
