# -*- coding: utf-8 -*-
"""case01 全链路测试(no-llm 模式:不依赖 Ollama,验证流程与落盘)。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.world.state import World, WorldConfig
from case01.world.timelines import build_timeline
from case01.world.branch import RuleBranchRouter
from case01.agents.financial import FinancialData

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "data", "financial")


class TestFinancialData:
    def test_loads_hcm_docs(self):
        fd = FinancialData(DATA_DIR)
        assert len(fd.docs) >= 20
        # 各类型齐全
        types = {d["type"] for d in fd.docs}
        assert {"disclosure", "industry_media", "social", "research",
                "self_media", "company_profile", "financial"} <= types

    def test_search_keyword_fallback(self):
        fd = FinancialData(DATA_DIR, embed_fn=None)  # 词频降级
        r = fd.search("HCM 海外订单 120亿", top_k=5)
        assert len(r) >= 1
        # 高相关 doc 应含"MarketScope"或"公告"
        top = r[0]
        assert "content" in top and "score" in top

    def test_source_stats_counts_duplicates(self):
        fd = FinancialData(DATA_DIR, embed_fn=None)
        r = fd.search("HCM 百亿订单 可靠吗", top_k=20)
        stats = fd.source_stats(r)
        # 有来源且检测到二次传播(多个账号转述 MarketScope)
        assert stats["n_sources"] >= 1
        # social 里 cites_marketscope 的多,second_hand_count 应 > 0
        assert stats["second_hand_count"] >= 1

    def test_since_filter_blocks_future(self):
        fd = FinancialData(DATA_DIR, embed_fn=None)
        # 08-27(T0)检索:不得出现 08-31/09-07 的公告(未来信息泄露)
        r = fd.search("HCM 公告 订单", top_k=100, since="2026-08-27")
        leaked = [x for x in r if str(x.get("time", ""))[:10] > "2026-08-27"]
        assert leaked == [], "未来资料泄露: {}".format([x["id"] for x in leaked])
        # 09-15(期末)检索:全部可见
        late = fd.search("HCM 公告", top_k=100, since="2026-09-15")
        assert any("hcm-disc-0907" == x["id"] for x in late)


class TestFullT0NoLLM:
    """no-llm 全链路:T0 咨询 → 回答 → Branch → 落盘"""

    def _setup(self, tmp_path):
        cfg = WorldConfig(run_id="t-t0", timeline_events=build_timeline("A"))
        w = World(cfg)
        w.advance_to(cfg.start_date)
        return w

    def test_t0_events_released_on_start(self):
        w = self._setup(None)
        assert w.date == "2026-08-27"
        # T0 当天事件已释放(公告确认验证+商务沟通)
        assert any(e.kind == "disclosure" for e in w.released)
        assert any(e.kind == "price" for e in w.released)

    def test_branch_router_on_typical_answers(self):
        # 对应 run.py 的规则降级路径(no-llm)
        router = RuleBranchRouter()
        # 保守回答 → B(观望/否定)
        assert router.route("我检索了现有资料:120-150 亿是 MarketScope 的情景测算,"
                            "不是公司数据;多个账号转述同一测算。不建议买。")[0] == "B"
        # 明确否定 → B
        assert router.route("该传闻不具备事实基础,建议不参与。")[0] == "B"
        # 条件化 → C
        assert router.route("建议等待公司正式确认后再考虑小仓位参与。")[0] == "C"

    def test_full_t0_writes_records(self, tmp_path):
        """模拟 run.py 主流程(no-llm),验证落盘结构"""
        out = tmp_path / "runs" / "t0test"
        out.mkdir(parents=True)
        w = self._setup(None)

        ethan_msg = "这些 HCM 消息可靠吗?值得买吗?"
        answer = ("120-150 亿是 MarketScope 情景测算,非公司披露;不建议买入。")
        router = RuleBranchRouter()
        branch, action = router.route(answer)
        assert branch == "B"  # 明确否定

        rec = {"run_id": "t0test", "turns": [
            {"speaker": "ethan", "text": ethan_msg},
            {"speaker": "investment_ai", "text": answer}],
            "branch": branch, "branch_action": action}
        (out / "run.json").write_text(json.dumps(rec, ensure_ascii=False),
                                      encoding="utf-8")
        loaded = json.loads((out / "run.json").read_text(encoding="utf-8"))
        assert loaded["run_id"] == "t0test"
        assert len(loaded["turns"]) == 2
        assert loaded["branch"] in ("A", "B", "C")
