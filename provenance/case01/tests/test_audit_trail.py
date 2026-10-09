# -*- coding: utf-8 -*-
"""Audit Trail(`/api/expert/audit-trail`)的判据回归。

这个口的存在意义是"把散在三个来源里的链条拼成一条能被平台侧直接渲染的记录"
(0904doc 05 §五 / 01 §十三 两处点名)。所以它的核心风险**不是崩溃,而是说谎**:
把平台侧的步骤显示成已完成、把"判定不了"写成"没有"、把过期的池快照当成进池时间。
本文件的六条测试就钉这三件事 + 两条边界。

用合成记录,不碰任何 gitignored 数据 —— 本机有数据、干净检出没有的东西不能进测试。
"""
import json
import os
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

RUN_ID = "synthetic-audit-001"
REFL = ("我在 8 月 27 日建议用户小仓位参与 HCM 的传闻交易。复盘时我区分了官方公告与市场"
        "推测,但把第三方测算当成了可核验的事实来源,没有说明它未经披露证实。")


def _record():
    from case01.expert_pool import load_expert_pool

    pool = load_expert_pool()
    return {
        "run_id": RUN_ID, "start_date": "2026-08-27", "end_date": "2026-09-15",
        "turns": [], "events": [], "retrievals": [], "state_history": [], "audit": [],
        "reflection": {"text": REFL, "quality": {"status": "pass"}},
        "router": {"status": "ok", "executed": True,
                   "expert_pool_version": pool["version"],
                   "executed_by": {"source": "local_by_config", "model": "qwen3:8b",
                                   "host": "127.0.0.1"},
                   "issues": [{"id": "issue-1", "expert_category_id": "E1",
                               "summary": "证据分级不足", "risk": "high"}]},
        "manifest": {"created_at": "2026-08-27T10:00:00+08:00",
                     "judge_model": "qwen3:8b"},
        "branch": "B", "branch_action": {"source": "judge"},
    }


def _mark(run_id=RUN_ID, issue_id="issue-1", cat="E1", verdict="correct",
          at="2026-09-01T10:00:00+08:00", origin="expert", version=1, ref=None):
    return {
        "verdict": verdict, "origin": origin, "operator": "peer:127.0.0.1",
        "marked_at": at,
        "review": {"run_id": run_id, "issue_id": issue_id,
                   "expert_category_id": cat, "expert_ref": ref or "peer:127.0.0.1",
                   "version": version,
                   "verdict_platform": {"correct": "approve", "incorrect": "reject",
                                        "amend": "edit"}.get(verdict, verdict)},
    }


class AuditTrailCase(unittest.TestCase):
    def setUp(self):
        from live import reflections as refl, state
        from case01 import review_app
        from case01.tools import lora_prep

        self.td = tempfile.mkdtemp(prefix="audit-trail-test-")
        runs = os.path.join(self.td, "runs", RUN_ID)
        os.makedirs(runs)
        with open(os.path.join(runs, "run.json"), "w", encoding="utf-8") as f:
            json.dump(_record(), f, ensure_ascii=False)
        self.marks_path = os.path.join(self.td, "reflection_marks.json")
        self.lora_root = os.path.join(self.td, "lora")
        self._old = (refl.MARKS_PATH, state.BASE_DIR, review_app.RUNS_DIR,
                     lora_prep.OUT_ROOT)
        refl.MARKS_PATH = self.marks_path
        state.BASE_DIR = self.td
        review_app.RUNS_DIR = os.path.join(self.td, "runs")
        # 池报告根重定向到临时目录 —— 不读仓内 results/lora(CI 上不存在)
        lora_prep.OUT_ROOT = self.lora_root
        self.c = TestClient(review_app.app)

    def tearDown(self):
        from live import reflections as refl, state
        from case01 import review_app
        from case01.tools import lora_prep
        (refl.MARKS_PATH, state.BASE_DIR, review_app.RUNS_DIR,
         lora_prep.OUT_ROOT) = self._old
        shutil.rmtree(self.td, ignore_errors=True)

    def write_marks(self, marks):
        with open(self.marks_path, "w", encoding="utf-8") as f:
            json.dump(marks, f, ensure_ascii=False)

    def chain(self, body):
        return {c["step"]: c["covered"] for c in body["chain"]}

    def events(self, body):
        return {e["step"]: e for e in body["events"]}

    # ---- 一、覆盖判据:平台侧与"判定不了"都不能显示成已完成 ------------------

    def test_platform_side_step_is_not_reported_as_covered(self):
        """专家分配在平台侧执行,产物里推不出来 ⇒ 必须显示成未覆盖。

        第一版拿"step 出现过"当 covered,把三步平台侧动作显示成全绿
        (2026-10-09 在 sample8b15-009 上实测暴露)。
        """
        body = self.c.get("/api/expert/audit-trail?run_id=" + RUN_ID).json()
        cov = self.chain(body)
        self.assertFalse(cov["assignment"], "平台侧的步骤不能算已覆盖")
        self.assertFalse(cov["conflict"], "判定不了的冲突不能算已覆盖")
        ev = self.events(body)
        self.assertTrue(ev["assignment"].get("platform_side"))
        self.assertIs(ev["assignment"].get("has_data"), False)
        for e in body["events"]:
            if e.get("platform_side"):
                self.assertIsNone(e["at"], "平台侧的步骤不能编一个时间")
                self.assertIsNone(e["who"], "平台侧的步骤不能编一个责任人")

    def test_no_opinion_means_final_verdict_is_undecidable(self):
        """一份计票意见都没有时,"最终结果"是推不出来的,不是"0 份"这个结论。"""
        body = self.c.get("/api/expert/audit-trail?run_id=" + RUN_ID).json()
        ev = self.events(body)
        self.assertFalse(self.chain(body)["final_verdict"])
        self.assertIn("判定不了", ev["final_verdict"]["result"])
        self.assertIs(ev["final_verdict"].get("has_data"), False)

    # ---- 二、只合并不推断:数据来自哪个产物要能对上 ----------------------

    def test_chain_merges_three_sources_in_time_order(self):
        self.write_marks([_mark(at="2026-09-01T10:00:00+08:00")])
        body = self.c.get("/api/expert/audit-trail?run_id=" + RUN_ID).json()
        self.assertEqual(body["n_events"], len(body["events"]))
        ev = self.events(body)
        # 三个来源各出一条:记录 run.json / 标记台账
        self.assertIn("反思正文", ev["reflection"]["result"])
        self.assertEqual(ev["router"]["detail"]["n_issues"], 1)
        self.assertIn("issue-1:E1", ev["expert_review"]["result"])
        self.assertEqual(ev["router"]["detail"]["executed_by"]["model"], "qwen3:8b")
        # 有时间的事件必须在没时间的事件之前
        seen_untimed = False
        for e in body["events"]:
            if e.get("at"):
                self.assertFalse(seen_untimed, "有时间的事件排到了没时间的后面")
            else:
                seen_untimed = True

    def test_probe_origin_is_shown_but_not_counted(self):
        """探针行(origin=probe)要出现在链上,但明确标 counted=False。

        导出侧同样拒收这类行;链上藏着它会让"谁审的"看起来比实际多一份。
        """
        self.write_marks([_mark(origin="probe", at="2026-09-01T09:00:00+08:00"),
                          _mark(at="2026-09-01T10:00:00+08:00")])
        body = self.c.get("/api/expert/audit-trail?run_id=" + RUN_ID).json()
        reviews = [e for e in body["events"] if e["step"] == "expert_review"]
        self.assertEqual(len(reviews), 2, "被取代/探针行不能从链上消失")
        self.assertEqual([e["detail"]["counted"] for e in reviews], [False, True])
        self.assertEqual(self.events(body)["final_verdict"]["detail"]["opinions_counted"], 1)

    # ---- 三、池快照不是进池时间 -------------------------------------------

    def test_pool_event_is_pinned_to_the_end_not_sorted_by_report_time(self):
        """导出报告的 at 是"报告生成时间",可能早于最后一份意见 ⇒ 不能按它排中间。

        实测:10-07 的报告排到了 10-09 的审核之前(sample8b15-009)。
        """
        self.write_marks([_mark(at="2026-10-09T15:11:36+08:00")])
        os.makedirs(self.lora_root, exist_ok=True)
        with open(os.path.join(self.lora_root, "dataset_report.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"generated_at": "2026-10-07T23:24:00+08:00",
                       "rows": [{"run_id": RUN_ID, "issue_id": "issue-1",
                                 "expert_category_id": "E1", "sft_in": 1,
                                 "dpo_in": 1, "errors": []}]}, f, ensure_ascii=False)
        body = self.c.get("/api/expert/audit-trail?run_id=" + RUN_ID).json()
        pool = [e for e in body["events"] if e["step"] == "training_pool"]
        self.assertEqual(len(pool), 1, body)
        self.assertEqual(body["events"][-1]["step"], "training_pool",
                         "池事件必须钉在链尾,不能按报告生成时间插进审核中间")
        self.assertEqual(pool[0]["at_semantics"], "report_generated_at")

    def test_run_absent_from_report_is_not_covered(self):
        """报告里没有这条 Run ⇒ 池状态未知,不能显示成已覆盖。"""
        os.makedirs(self.lora_root, exist_ok=True)
        with open(os.path.join(self.lora_root, "dataset_report.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"generated_at": "2026-10-07T23:24:00+08:00",
                       "rows": [{"run_id": "another-run", "issue_id": "i",
                                 "expert_category_id": "E1", "sft_in": 1,
                                 "dpo_in": 1}]}, f, ensure_ascii=False)
        body = self.c.get("/api/expert/audit-trail?run_id=" + RUN_ID).json()
        self.assertFalse(self.chain(body)["training_pool"])
        self.assertIs(self.events(body)["training_pool"].get("has_data"), False)

    def test_run_in_report_but_rejected_is_not_covered(self):
        """报告里有这条 Run、但一份都没进 SFT/偏好对 ⇒ 没真进池,不算已覆盖。

        与上一条是**两个不同分支**(报告缺这条 Run / 报告有但被拒),
        只测一条会漏掉另一条(2026-10-09 变异验证实测:去掉这条判据测试仍全绿)。
        """
        os.makedirs(self.lora_root, exist_ok=True)
        with open(os.path.join(self.lora_root, "dataset_report.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"generated_at": "2026-10-07T23:24:00+08:00",
                       "rows": [{"run_id": RUN_ID, "issue_id": "issue-1",
                                 "expert_category_id": "E1", "sft_in": 0,
                                 "dpo_in": 0, "errors": ["缺 anchor"]}]},
                      f, ensure_ascii=False)
        body = self.c.get("/api/expert/audit-trail?run_id=" + RUN_ID).json()
        ev = self.events(body)["training_pool"]
        self.assertFalse(self.chain(body)["training_pool"])
        self.assertIs(ev.get("has_data"), False)
        self.assertIn("缺 anchor", ev["detail"]["rejected_reasons"])

    # ---- 四、边界 ---------------------------------------------------------

    def test_bad_run_id_is_rejected_without_touching_disk(self):
        self.assertEqual(self.c.get("/api/expert/audit-trail").status_code, 400)
        self.assertEqual(
            self.c.get("/api/expert/audit-trail?run_id=../etc").status_code, 400)
        self.assertEqual(
            self.c.get("/api/expert/audit-trail?run_id=a/b").status_code, 400)
        self.assertEqual(
            self.c.get("/api/expert/audit-trail?run_id=no-such-run").status_code, 404)

    def test_boundaries_are_stated_in_the_payload(self):
        """接口要把"能看到什么/看不到什么"写在返回里,不靠读的人猜。"""
        body = self.c.get("/api/expert/audit-trail?run_id=" + RUN_ID).json()
        b = body["boundaries"]
        self.assertIn("platform_side", b["what_this_cannot_show"])
        self.assertIn("peer:", b["who_note"])


if __name__ == "__main__":
    unittest.main()
