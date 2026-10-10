# -*- coding: utf-8 -*-
"""专家审核参照面板(case01/expert_review_app.py)的行为回归。

为什么是这几条而不是更多:这个文件是发给平台侧当样子抄的,所以要保证的是
**桥的三件事**没坏 —— 词汇映射、身份不可指定、意见真的进训练线。
界面本身好不好看不在这里测。

记录用合成的:入库的两份 fixture(`case01/tests/fixtures/records/`)里的 router issue
是旧形状(只有 `field`,没有 `expert_category_id`),`build_review_package` 会把它们
判进人工分流、一条候选都不出 —— 拿它们测不到判定路径。
"""
import json
import os
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

RUN_ID = "synthetic-review-001"
REFL = ("我在 8 月 27 日建议用户小仓位参与 HCM 的传闻交易。复盘时我区分了官方公告与市场"
        "推测,但把第三方测算当成了可核验的事实来源,没有说明它未经披露证实。"
        "更稳妥的做法是先声明证据分级不足,再决定是否参与,而不是让用户自己去分辨。")


def synthetic_record():
    from case01.expert_pool import load_expert_pool

    pool = load_expert_pool()
    return {
        "run_id": RUN_ID,
        "start_date": "2026-08-27", "end_date": "2026-09-15",
        "turns": [{"date": "2026-08-27", "speaker": "ethan", "text": "要不要跟进 HCM?"},
                  {"date": "2026-08-27", "speaker": "investment_ai", "text": "可以小仓位参与。"}],
        "events": [], "retrievals": [], "state_history": [], "audit": [],
        "reflection": {"text": REFL, "quality": {"status": "pass", "score": 95}},
        "router": {"status": "ok", "executed": True,
                   "expert_pool_version": pool["version"],
                   "issues": [{"id": "issue-1",
                               "summary": "把未经披露证实的第三方测算当作可核验事实来源",
                               "risk": "high",
                               "routing_reason": "涉及信息披露层级与适当性判断",
                               "expert_category_id": "E1",
                               "secondary_expert_category_ids": [],
                               "match_status": "matched",
                               "evidence_quote": "把第三方测算当成了可核验的事实来源",
                               "evidence_sentence_ids": ["S002"],
                               "invalid_evidence_sentence_ids": []}]},
        "final_feedback": {"date": "2026-09-15", "ethan": "我按建议观望了", "ai": "观望是稳妥的"},
        "branch": "B", "branch_action": {"source": "judge"},
    }


class ExpertPanelCase(unittest.TestCase):
    def setUp(self):
        from live import reflections as refl, state
        from case01 import review_app

        self.td = tempfile.mkdtemp(prefix="expert-panel-test-")
        runs = os.path.join(self.td, "runs", RUN_ID)
        os.makedirs(runs)
        with open(os.path.join(runs, "run.json"), "w", encoding="utf-8") as f:
            json.dump(synthetic_record(), f, ensure_ascii=False)
        self._old = (refl.MARKS_PATH, state.BASE_DIR, review_app.RUNS_DIR)
        refl.MARKS_PATH = os.path.join(self.td, "reflection_marks.json")
        state.BASE_DIR = self.td
        review_app.RUNS_DIR = os.path.join(self.td, "runs")
        self.c = TestClient(review_app.app)

    def tearDown(self):
        from live import reflections as refl, state
        from case01 import review_app
        refl.MARKS_PATH, state.BASE_DIR, review_app.RUNS_DIR = self._old
        shutil.rmtree(self.td, ignore_errors=True)

    def payload(self, **kw):
        body = {"run_id": RUN_ID, "issue_id": "issue-1", "expert_category_id": "E1",
                "verdict": "approve", "text": "", "reason": "",
                "anchor": {"sentence_ids": ["S002"], "quote": "x"}}
        body.update(kw)
        return body

    def test_queue_carries_candidates_with_anchor(self):
        q = self.c.get("/api/expert/queue?limit=5").json()
        self.assertEqual(q["n_tasks"], 1, q)
        t = q["tasks"][0]
        self.assertEqual((t["run_id"], t["issue_id"], t["expert_category_id"]),
                         (RUN_ID, "issue-1", "E1"))
        # anchor 是"构造偏好对必填"那一项,面板必须把它带出来,否则平台侧不知道要传
        self.assertEqual(t["anchor"]["sentence_ids"], ["S002"])
        self.assertEqual(q["verdict_words"], ["approve", "edit", "reject"])

    def test_platform_words_map_and_our_words_are_rejected(self):
        r = self.c.post("/api/expert/decision", json=self.payload())
        self.assertTrue(r.json()["ok"], r.text)
        mark = r.json()["mark"]
        self.assertEqual(mark["verdict"], "correct")           # approve → correct
        self.assertEqual(mark["review"]["verdict_platform"], "approve")
        bad = self.c.post("/api/expert/decision",
                          json=self.payload(verdict="correct"))
        self.assertEqual(bad.status_code, 400)
        self.assertIn("unrecognised verdict field", bad.json()["errors"][0])

    def test_approve_with_text_hits_the_same_gate_as_the_mark_endpoint(self):
        """两个口共用 `mark_gate_errors`,所以对同一份输入给同一个结论。

        面板只把门的文案换成专家话术(展示层),判据本身不在这里重写。
        """
        r = self.c.post("/api/expert/decision",
                        json=self.payload(text="顺手写的一句改文"))
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("leave the text empty", r.json()["errors"][0])
        self.assertFalse(os.path.exists(os.path.join(self.td, "reflection_marks.json")),
                         "被拒的意见不该留下半个文件")

    def test_identity_fields_come_from_the_server_not_the_caller(self):
        """内部可留痕、外部不可指定:请求体里的 expert_id/operator/agent 一律不采信。"""
        r = self.c.post("/api/expert/decision", json=self.payload(
            operator="某个别的专家", origin="simulated", expert_id="E-999",
            agent="冒充的角色")).json()
        mark = r["mark"]
        self.assertEqual(mark["agent"], "investment_ai")       # 取被审记录里的说话人
        self.assertEqual(mark["origin"], "expert")             # 不由调用方声明
        self.assertTrue(str(mark["operator"]).startswith("peer:"), mark["operator"])
        self.assertEqual(mark["review"]["expert_ref"], mark["operator"])
        self.assertEqual(sorted(mark["review"]["dropped_client_keys"]),
                         ["agent", "expert_id", "operator", "origin"])

    def test_second_opinion_appends_a_version_and_keeps_the_first(self):
        first = self.c.post("/api/expert/decision",
                            json=self.payload(verdict="edit", text="整篇重写:先说证据分级不足,再谈是否参与,并把传闻被证伪时的回撤风险写进给用户的建议里。")
                            ).json()
        second = self.c.post("/api/expert/decision",
                             json=self.payload(verdict="edit", text="整篇重写第二版:先说证据分级不足,再谈是否参与,并把传闻被证伪时的回撤风险写进建议,同时说明不触发即不减仓。")
                             ).json()
        self.assertEqual(first["mark"]["review"]["version"], 1)
        self.assertEqual(second["mark"]["review"]["version"], 2)
        self.assertEqual(second["mark"]["review"]["supersedes"], "v1")
        from live.reflections import load_marks
        self.assertEqual(len(load_marks()), 2, "旧意见必须保留(《补充规范》§3:101)")

    def test_marks_listing_shows_no_opinion_content(self):
        """首轮两份意见互不可见:`/api/expert/marks` 里既没有结论也没有文本。"""
        self.c.post("/api/expert/decision",
                    json=self.payload(verdict="reject", text="核心逻辑不成立:传闻未经披露证实就不该给任何仓位建议"))
        body = json.dumps(self.c.get("/api/expert/marks").json(), ensure_ascii=False)
        for leak in ("核心逻辑不成立", REFL[:12], "incorrect", "reject"):
            self.assertNotIn(leak, body, "计数接口漏出了意见内容:{}".format(leak))
        self.assertIn("only returns counts", body)

    def test_panel_opinion_reaches_the_training_export(self):
        """面板写的意见经 lora_prep 导出真的成对(SFT + DPO),不只是写进文件。"""
        self.c.post("/api/expert/decision", json=self.payload(
            verdict="edit",
            text="整篇重写:先说明第三方测算未经披露证实、证据分级不足,因此不建议参与,"
                 "并给出传闻被证伪时的回撤区间与不可逆损失,最后才谈若公告确认后的观察条件。"))
        from case01.tools.lora_prep import export, load_marks_any
        marks, src = load_marks_any()
        self.assertEqual(len(marks), 1)
        rep = export(marks, out_root=self.td)
        self.assertEqual(rep["stats"]["sft"], 1, rep["stats"])
        self.assertEqual(rep["stats"]["dpo"], 1, rep["stats"])
        self.assertEqual(rep["stats"]["rejected"], 0, rep["stats"])

    # ---- 两个面之间的往返(2026-10-09 修:以前回不到原来那条记录) ----

    def test_embed_expert_is_a_real_embedded_layout(self):
        """`/embed/expert` 必须切到 `body.embed`:去掉大标题本页返回链接。

        此前它只是 `/review/expert` 的别名 —— 嵌进宿主 iframe 后点"← 回到只读面"
        会整页跳走,宿主收不到消息,于是"点开专家面板就回不去了"。
        """
        html = self.c.get("/embed/expert").text
        self.assertIn("body.embed", html, "嵌入版式规则没了")
        self.assertIn("body.embed header h1", html, "大标题在嵌入面里该隐藏")
        self.assertIn("body.embed #backlink", html, "本页返回链接在嵌入面里该隐藏")
        # 前端要有 embed 判定,否则上面三条 CSS 永远不会生效
        self.assertIn("location.pathname.indexOf(\"/embed/\")", html)
        self.assertIn("classList.add(\"embed\")", html)

    def test_expert_page_backlink_keeps_run_and_source(self):
        """返回链接要带回 `?run=`(全页面版式下可见)。

        以前写死 `/review`,离开后就回不到刚才看的那条记录。
        """
        html = self.c.get("/review/expert").text
        self.assertIn('id="backlink"', html, "返回链接要有 id 供前端改写")
        self.assertIn("var WANT_RUN", html, "前端要读 ?run=")
        self.assertIn("var FROM", html, "前端要读 ?from=")
        self.assertIn('encodeURIComponent(WANT_RUN)', html, "返回时要把 run 拼回去")
        # 前端不能只读参数不用:拼 target 的那几行必须在
        self.assertIn('target = "/embed/review"', html, "来源是嵌入面时要指回嵌入面")

    def test_readonly_face_hides_expert_link_when_embedded(self):
        """结果记录嵌入面里不给跨页的"去专家审核"链接。

        它在宿主 iframe 里整页跳走,宿主只看到"结果记录"卡片被换成专家面板,
        且没有回来的路 —— 所以嵌入模式下隐藏,要跳就用 `/review` 全页面。
        """
        embed = self.c.get("/embed/review").text
        self.assertIn("body.embed .expertlink", embed, "嵌入面缺少隐藏规则")
        plain = self.c.get("/review").text
        self.assertIn("syncExpertLink", plain, "全页面要能同步当前 run 到链接里")
        self.assertIn("from=review", plain)


if __name__ == "__main__":
    unittest.main()
