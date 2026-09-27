# -*- coding: utf-8 -*-
"""分支判定评估工具与 extra_body 透传的测试(2026-09-27 深度体检固化)。"""
import json
import os
import sys
import urllib.request as _ur

import pytest

_this_dir = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_this_dir)                # D:\zzr\provenance
_PKG = os.path.join(_REPO, "provenance")          # live/ 与 case01 包所在
for _p in (_PKG, _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from case01.tools.branch_judge_eval import (  # noqa: E402
    load_cases, rules_verdict, summarize)


class TestBranchJudgeEval:
    def test_rules_verdict_deterministic(self):
        """规则判定是纯函数:同输入同输出(评估报告可复现的前提)。
        用合成输入证明(CI 无 runs 目录,不依赖真实语料)。"""
        inputs = ["不建议买入,风险自负。", "可以小仓位分批介入,设止损。",
                  "综合分析如下……若参与建议小仓位并等确认。" * 3, ""]
        first = [rules_verdict(t) for t in inputs]
        second = [rules_verdict(t) for t in inputs]
        assert first == second

    def test_load_cases_skips_when_no_runs(self):
        """CI/新克隆无 case01/runs(gitignored)→ load_cases 返回空而非崩溃。"""
        cases = load_cases()
        assert isinstance(cases, list)  # 本地有 runs 则 >0,CI 则为 0

    def test_summarize_no_division_by_zero(self):
        assert summarize([])["rules_llm_agree_rate"] is None
        err_rows = [{"run_id": "x", "t0": "t", "actual": "A", "source": "preset",
                     "rules": "C", "llm": "error", "llm_attempts": None,
                     "llm_reason": "", "rules_eq_llm": False,
                     "llm_eq_actual": None, "rules_eq_actual": False,
                     "secs": 1.0}]
        s = summarize(err_rows)
        assert s["llm_ok"] == 0 and s["rules_llm_agree_rate"] is None

    def test_rules_verdict_defaults_to_c_on_long_neutral_text(self):
        """已知缺陷的回归锚:长中性回答含条件词 → 规则表判 C。
        这正是"规则表对长文失效、需 LLM 判定"的证据;若此测试反转,
        说明词表语义变了,branch_judge_eval 的历史结论需重新标定。"""
        long_text = ("我们分析了市场传闻,建议如果您仍想参与,可以考虑小仓位分批介入,"
                     "并设置止损,同时等待公司正式公告确认后再评估。")
        assert rules_verdict(long_text) == "C"


class TestExtraBodyPassthrough:
    """client 级 extra_body 透传(2026-09-27 为 BigModel thinking-off 新增)。"""

    def _capture(self, monkeypatch):
        captured = {}

        def fake(req, timeout=None):
            captured["body"] = json.loads(req.data)

            class _R:
                def read(self):
                    return json.dumps({"choices": [{"message": {"content": "ok"}}],
                                       "usage": {}}).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            return _R()
        monkeypatch.setattr(_ur, "urlopen", fake)
        return captured

    def test_client_default_merged(self, monkeypatch):
        from case01.agents.llm import OpenRouterClient
        c = OpenRouterClient(api_key="x", extra_body={"thinking": {"type": "disabled"}})
        cap = self._capture(monkeypatch)
        c.chat([{"role": "user", "content": "x"}], max_tokens=64)
        assert cap["body"]["thinking"] == {"type": "disabled"}

    def test_call_site_overrides_client_default(self, monkeypatch):
        from case01.agents.llm import OpenRouterClient
        c = OpenRouterClient(api_key="x", extra_body={"thinking": {"type": "disabled"}})
        cap = self._capture(monkeypatch)
        c.chat([{"role": "user", "content": "x"}], max_tokens=64,
               extra_body={"thinking": {"type": "enabled"}, "seed": 7})
        assert cap["body"]["thinking"] == {"type": "enabled"}
        assert cap["body"]["seed"] == 7

    def test_no_leak_between_instances(self, monkeypatch):
        from case01.agents.llm import OpenRouterClient
        glm = OpenRouterClient(api_key="x",
                               extra_body={"thinking": {"type": "disabled"}})
        plain = OpenRouterClient(api_key="x")
        cap = self._capture(monkeypatch)
        plain.chat([{"role": "user", "content": "x"}], max_tokens=64)
        assert "thinking" not in cap["body"], "extra_body 泄漏到无配置实例"
        glm.chat([{"role": "user", "content": "x"}], max_tokens=64)
        assert cap["body"]["thinking"] == {"type": "disabled"}

    def test_judge_max_tokens_floor(self):
        from mavis_case01_injector.world.branch import LLMBranchJudge
        assert LLMBranchJudge(None, max_tokens=1).max_tokens == 64
        assert LLMBranchJudge(None, max_tokens=-5).max_tokens == 64
        assert LLMBranchJudge(None, max_tokens=3072).max_tokens == 3072
