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
