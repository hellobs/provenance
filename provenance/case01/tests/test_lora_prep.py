# -*- coding: utf-8 -*-
"""LoRA 预准备工具的单元测试(合成标记,零网络)。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from case01.tools.lora_prep import (  # noqa: E402
    validate_sample, validate_dpo, _dedup_key, export, synthetic_marks)


def _mark(verdict="correct",
          correction="",
          thought="市场传闻未经官方披露证实,且传播路径依赖单一第三方测算,证据分级不足,不建议参与。",
          agent="Investment AI", sim="s1", node="n1"):
    return {"agent": agent, "simulation": sim, "sim_time": "20250213-10:00",
            "node_id": node, "thought": thought, "verdict": verdict,
            "correction": correction,
            "context": {"role": "AI Investment Advisor",
                        "action": "建议小仓位参与",
                        "value_tendency": {"Risk Control": 0.3}}}


class TestValidate(unittest.TestCase):
    def test_good_sample_passes(self):
        s = {"instruction": "你是投资助手。", "input": "近期行动: x | 你的反思: y",
             "output": "官方披露缺失的情况下不建议参与,并提示回撤风险。"}
        self.assertEqual(validate_sample(s), [])

    def test_gates(self):
        self.assertTrue(any("空" in e for e in validate_sample({"instruction": "", "input": "x", "output": "y" * 30})))
        self.assertTrue(any("过短" in e for e in validate_sample(
            {"instruction": "i", "input": "x", "output": "短"})))
        self.assertTrue(any("客套" in e for e in validate_sample(
            {"instruction": "i", "input": "x",
             "output": "当然可以。以下是反思正文。" + "具体判断" * 10})))

    def test_dpo_gates(self):
        self.assertTrue(any("无偏好信号" in e for e in validate_dpo(
            {"prompt": "p", "chosen": "same", "rejected": "same"})))
        self.assertEqual(validate_dpo({"prompt": "p", "chosen": "a" * 30, "rejected": "b" * 30}), [])

    def test_correct_uses_original_reflection_even_with_stray_text(self):
        """判定为 correct 的样本,output 必须是原反思 —— 不是记录里躺着的那段文本。

        面板切回"正确"时纠正文本框只是 `display:none`,值还在并照发;写侧已拒,
        这里守的是"已经躺在盘上的历史记录/直接调库写入的记录"不会被读错。
        """
        from live.reflections import build_lora_sample
        thought = "市场传闻未经官方披露证实,且传播路径依赖单一第三方测算,证据分级不足,不建议参与。"
        built = build_lora_sample(_mark("correct", correction="这段不该被当成标准答案的改写文本,长度足够。"))
        self.assertEqual(built["sample"]["output"], thought)
        self.assertIsNone(built["dpo"])
        # 反向对照:partial 才用纠正文本
        corrected = "在官方披露缺失时明确告知用户传闻未经证实,不建议参与,并提示情绪溢价回撤风险。"
        built2 = build_lora_sample(_mark("partial", correction=corrected, node="n9"))
        self.assertEqual(built2["sample"]["output"], corrected)
        self.assertTrue(built2["dpo"])


class TestExport(unittest.TestCase):
    def test_end_to_end_stats(self):
        marks = [
            _mark("correct"),
            _mark("incorrect", correction="官方披露缺失时不建议任何仓位;明确提示回撤风险。",
                  node="n2"),
            _mark("partial", correction="自我批评方向正确;应补充以 T0 价格为基准的回撤比例测算与具体数据支撑,而非笼统表述。", node="n3"),
            _mark("correct"),                      # 重复样本(同内容) → 应去重
            _mark("bogus"),                        # 非法 verdict → 拒
            _mark("incorrect", correction="", node="n9"),  # incorrect 无纠正 → 拒
        ]
        with tempfile.TemporaryDirectory() as td:
            report = export(marks, out_root=td)
            s = report["stats"]
            self.assertEqual(s["marks"], 6)
            self.assertEqual(s["sft"], 3)          # correct/incorrect/partial 各 1,重复 correct 去重
            self.assertEqual(s["dpo"], 2)          # incorrect + partial
            self.assertEqual(s["rejected"], 3)     # 重复 correct + 非法 verdict + incorrect 无纠正
            self.assertIn("重复样本", s["reject_reasons"])
            self.assertTrue(any("verdict" in k for k in s["reject_reasons"]))
            # 落盘文件逐行可解析
            for fn in ("sft.jsonl", "dpo.jsonl"):
                with open(os.path.join(td, fn), encoding="utf-8") as f:
                    rows = [json.loads(l) for l in f if l.strip()]
                self.assertEqual(len(rows), s[fn.split(".")[0]])

    def test_synthetic_schema(self):
        report = export(synthetic_marks(), out_root=tempfile.mkdtemp())
        self.assertEqual(report["stats"]["sft"], 3)
        self.assertEqual(report["stats"]["dpo"], 2)
        self.assertEqual(report["stats"]["rejected"], 0)

    def test_segment_vs_full_and_non_expert_origin(self):
        """两条 2026-10-07 实测出来的门:片段 vs 全文的对子不产;非专家来源不进训练集。

        必要性都来自跑批:用 4b 扮专家造的 4 条标记,旧校验门 4/4 全放过(sft4/dpo4/
        rejected 0),而那些 dpo 对子全是 chosen 111–163 字 vs rejected 1591–3387 字。
        """
        full = "反思正文" * 400                      # 1600 字:真实整篇反思的量级
        seg = "这段是专家对自己负责片段的改写,长度只有百余字,与整篇不可比。"
        asym = export([_mark("partial", correction=seg, thought=full, node="n20")],
                      out_root=tempfile.mkdtemp())
        self.assertEqual(asym["stats"]["sft"], 1)    # 意见本身仍可用于 SFT
        self.assertEqual(asym["stats"]["dpo"], 0)    # 偏好对被判不可比
        self.assertTrue(any("量纲不对等" in k for k in asym["stats"]["reject_reasons"]),
                        asym["stats"]["reject_reasons"])
        # 长度读数进产物(训练 seq 上限要照着它定,不能猜)
        cl = asym["stats"]["char_len"]
        self.assertGreater(cl["input_max"], len(full))   # input 里装着整篇反思
        self.assertEqual(cl["output_max"], len(seg))     # partial 的 output 就是那段

        sim = _mark("partial", correction="官方披露缺失时不建议任何仓位;明确提示回撤风险与不可逆损失。",
                    node="n21")
        sim["origin"] = "simulated"
        rep = export([sim], out_root=tempfile.mkdtemp())
        self.assertEqual(rep["stats"]["sft"], 0)
        self.assertTrue(any("非专家来源" in k for k in rep["stats"]["reject_reasons"]),
                        rep["stats"]["reject_reasons"])
        # 缺 origin 键的旧标记按 expert 读,不被新门误伤
        legacy = _mark("partial", correction="应补充以 T0 价格为基准的回撤比例测算与具体数据支撑。",
                       node="n22")
        self.assertEqual(export([legacy], out_root=tempfile.mkdtemp())["stats"]["sft"], 1)


if __name__ == "__main__":
    unittest.main()


class TestMarkWriteRoundTrip(unittest.TestCase):
    """标记写入 API → lora_prep 读取 → 导出,全链回归(阶段:LoRA 预准备)。"""

    def test_append_then_export(self):
        import tempfile
        from live import reflections as refl

        with tempfile.TemporaryDirectory() as td:
            mp = os.path.join(td, "reflection_marks.json")
            old = refl.MARKS_PATH
            refl.MARKS_PATH = mp
            try:
                mark = refl.new_mark(
                    agent="Investment AI", simulation="s", sim_time="20250213-10:00",
                    node_id="n1",
                    thought="市场传闻未经官方披露证实,且传播路径依赖单一第三方测算,证据分级不足,不建议参与。",
                    verdict="incorrect",
                    correction="官方披露缺失时不建议任何仓位;应明确提示传闻被证伪时的回撤风险与不可逆损失。",
                    context={"role": "AI Investment Advisor", "action": "建议小仓位参与",
                             "value_tendency": {"Risk Control": 0.3}})
                refl.append_mark(mark)
                # 写侧:JSON 数组是唯一真源,JSONL 由 rebuild 同步刷新
                self.assertTrue(os.path.isfile(mp))
                jl = mp + "l"
                self.assertTrue(os.path.isfile(jl), "append 后 JSONL 未同步刷新")
                # 读侧:lora_prep 与 live.load_marks 读到同一份数据
                from case01.tools.lora_prep import load_marks_any, export
                marks, src_path = load_marks_any()
                self.assertEqual(len(marks), 1)
                self.assertEqual(os.path.normpath(src_path), os.path.normpath(mp))
                report = export(marks, out_root=td)
                self.assertEqual(report["stats"]["sft"], 1)
                self.assertEqual(report["stats"]["dpo"], 1)
            finally:
                refl.MARKS_PATH = old


class TestHTTPRoundTrip(unittest.TestCase):
    """HTTP 层全链:POST /api/reflections/mark → GET export.jsonl → lora_prep 校验。"""

    def test_mark_endpoint_to_lora_export(self):
        import tempfile
        from fastapi.testclient import TestClient
        from live import reflections as refl, state

        with tempfile.TemporaryDirectory() as td:
            old_marks = refl.MARKS_PATH
            old_base = state.BASE_DIR
            from live.routes import app   # 先导入(静态挂载用真实 BASE_DIR)
            refl.MARKS_PATH = os.path.join(td, "reflection_marks.json")
            state.BASE_DIR = td          # 标记/写盘落临时目录
            try:
                c = TestClient(app)
                payload = {"agent": "Investment AI",
                           "node_id": "n-1",
                           "text": "市场传闻未经官方披露证实,且传播路径依赖单一第三方测算,"
                                   "证据分级不足,不建议参与。",
                           "verdict": "incorrect",
                           "correction": "官方披露缺失时不建议任何仓位;应明确提示传闻被证伪"
                                         "时的回撤风险与不可逆损失。",
                           "sim_time": "20250213-10:00"}
                r = c.post("/api/reflections/mark", json=payload)
                assert r.status_code == 200, r.text
                assert r.json().get("ok") is True
                # export.jsonl 与磁盘同步
                r2 = c.get("/api/reflections/export.jsonl")
                assert r2.status_code == 200
                rows = [json.loads(l) for l in r2.text.splitlines() if l.strip()]
                assert len(rows) == 1 and rows[0]["lora"]["dpo"]
                # lora_prep 从同一真源导出并通过校验门
                from case01.tools.lora_prep import load_marks_any, export
                marks, src_path = load_marks_any()
                assert len(marks) == 1
                assert os.path.normpath(src_path) == os.path.normpath(refl.MARKS_PATH)
                report = export(marks, out_root=td)
                assert report["stats"]["sft"] == 1
                assert report["stats"]["dpo"] == 1
            finally:
                refl.MARKS_PATH = old_marks
                state.BASE_DIR = old_base

    def test_mark_endpoint_rejects_correct_with_correction(self):
        """写侧的门:verdict=correct 且带纠正文本 → 拒,且不落盘(2026-10-07)。"""
        import tempfile
        from fastapi.testclient import TestClient
        from live import reflections as refl, state

        with tempfile.TemporaryDirectory() as td:
            old_marks = refl.MARKS_PATH
            old_base = state.BASE_DIR
            from live.routes import app
            refl.MARKS_PATH = os.path.join(td, "reflection_marks.json")
            state.BASE_DIR = td
            try:
                c = TestClient(app)
                r = c.post("/api/reflections/mark", json={
                    "agent": "Investment AI", "node_id": "n-2",
                    "text": "披露层级已先检查,判断过程与结论一致,无需修改。",
                    "verdict": "correct",
                    "correction": "这段是误留在隐藏文本框里的改写文本。",
                    "sim_time": "20250213-11:00"})
                assert r.status_code == 200, r.text
                body = r.json()
                assert body.get("ok") is False, body
                assert any("correction text" in e for e in body.get("errors") or []), body
                assert not os.path.exists(refl.MARKS_PATH), "被拒的标记仍写了盘"
            finally:
                refl.MARKS_PATH = old_marks
                state.BASE_DIR = old_base
