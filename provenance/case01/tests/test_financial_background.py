import hashlib
import json
from pathlib import Path

import pytest

from case01.agents.financial import FinancialData
from case01.agents.investment_ai import InvestmentAI
from case01.agents.passages import passages
from case01.tools.import_financial_background import convert
from mavis_case01_injector.bridge import MavisBridge
from mavis_case01_injector.nodes import NodeSpec
from mavis_case01_injector.record import _retrievals

DATA = Path(__file__).resolve().parents[1] / "data"


def library(tmp_path, future=False):
    primary = [{"id": f"hcm-{i}", "company": "hcm", "title": "HCM 闪充",
                "content": "HCM 闪充资料", "source": "HCM", "time": "2026-08-01",
                "type": "disclosure"} for i in range(8)]
    background = [{"id": "bg-long", "company": "byd", "company_name": "比亚迪",
                   "title": "公司业务", "source": "公司官网", "type": "disclosure",
                   "time": "2026-09-01" if future else "2026-08-24",
                   "content": ("公司生产汽车。" * 170) + "\n\n计划建设4000座闪充站。",
                   "meta": {"corpus": "market_background_test", "second_hand": True}},
                  {"id": "bg-two", "company": "other", "title": "闪充报告",
                   "source": "研究机构", "type": "research", "time": "2026-08-01",
                   "content": "其他公司的闪充计划。", "meta": {"corpus": "market_background_test"}}]
    for subdir, docs in (("hcm", primary), ("background/byd", background)):
        directory = tmp_path / subdir
        directory.mkdir(parents=True)
        (directory / "docs.json").write_text(json.dumps(docs), encoding="utf-8")
    return primary, background


def test_real_import_retains_original_and_dates():
    original = DATA / "financial_sources" / "模型版_100条.jsonl"
    raw, groups = convert(original)
    assert len(groups) == 10
    docs = [doc for group in groups.values() for doc in group]
    assert len(docs) == len({d["id"] for d in docs}) == 100
    assert max(d["time"] for d in docs) == "2026-08-24"
    rows = [json.loads(line) for line in raw.decode().splitlines()]
    for company, group in groups.items():
        assert json.loads((DATA / "financial" / "background" / company / "docs.json").read_text()) == group
    for doc in docs:
        assert doc["content"] == rows[doc["meta"]["source_line"] - 1]["正文"]
        assert doc["meta"]["source_sha256"] == hashlib.sha256(raw).hexdigest()
        chunks = list(passages(doc["content"]))
        assert "".join(text for _, _, text in chunks) == doc["content"]
        assert all(doc["content"][start:end] == text and len(text) <= 600
                   for start, end, text in chunks)
    mixed_date = next(d for d in docs if "官网发布" in d["meta"]["publication_time_raw"])
    assert mixed_date["time"] == "2025-07-09"


@pytest.mark.parametrize("text", ["短文", "", "无标点" * 900, "第一段。\n\n" + "正文。" * 300])
def test_passages_lossless(text):
    chunks = list(passages(text))
    assert "".join(chunk for _, _, chunk in chunks) == text
    assert all(len(chunk) <= 600 for _, _, chunk in chunks)


def test_disabled_preserves_original_documents(tmp_path, monkeypatch):
    primary, _ = library(tmp_path)
    monkeypatch.delenv("CASE01_FINANCIAL_BACKGROUND", raising=False)
    fin = FinancialData(str(tmp_path))
    assert fin.docs == primary
    assert fin.search_docs == primary
    assert len(fin.search_context("闪充", "2026-08-27")) == 8


def test_tail_reaches_model_and_audit(tmp_path):
    library(tmp_path)
    fin = FinancialData(str(tmp_path), include_background=True)

    class Capture:
        def chat(self, messages, **kwargs):
            self.messages = messages
            return "根据资料回答。"

    llm = Capture()
    ai = InvestmentAI(llm, fin)
    ai.answer("4000座闪充站", "2026-08-27")
    assert "4000座闪充站" in llm.messages[-1]["content"]
    hit = ai.last_retrieval["hits"][0]
    assert hit["document_id"] == "bg-long"
    assert hit["char_start"] > 600
    assert "4000座闪充站" in hit["content"]
    assert "比亚迪" in ai.last_retrieval["context"]
    assert ai.last_retrieval["context"] in llm.messages[-1]["content"]


def test_future_filter_and_primary_quota(tmp_path):
    library(tmp_path, future=True)
    fin = FinancialData(str(tmp_path), include_background=True)
    hits = fin.search_context("闪充", "2026-08-27")
    assert sum(h["company"] == "hcm" for h in hits) == 8
    assert "bg-long" not in {h["id"] for h in hits}
    late = fin.search_context("闪充", "2026-09-02")
    assert len(late) == 10
    assert len({h["document_id"] for h in late}) == 10


def test_source_stats_do_not_count_passages_as_sources(tmp_path):
    library(tmp_path)
    fin = FinancialData(str(tmp_path), include_background=True)
    chunks = [d for d in fin.search_docs if d["id"] == "bg-long"]
    assert len(chunks) > 1
    assert fin.source_stats(chunks)["second_hand_count"] == 1


def test_town_only_assistant_receives_background_and_mapping_retains_it(tmp_path, monkeypatch):
    library(tmp_path)
    ai = InvestmentAI(None, FinancialData(str(tmp_path), include_background=True))
    monkeypatch.setattr("mavis_case01_injector._providers.background_retrieval_provider",
                        lambda **kwargs: ai)
    node = NodeSpec("T0", "2026-08-27", interactions=[
        {"from": "Ethan Lin", "to": "Investment AI", "focus": "4000座闪充站"}])
    bridge = MavisBridge([node], dry_run=True)
    record = bridge.run()
    context = record["nodes"][0]["context"]
    assert "Financial Data background" not in context["Ethan Lin"]
    assert "4000座闪充站" in context["Investment AI"]["Financial Data background"]
    mapped = _retrievals(record["nodes"])
    evidence = next(r for r in mapped if r["mode"] == "background_retrieval")
    assert evidence["query_source"] == "node_focus"
    assert evidence["hits"][0]["document_id"] == "bg-long"
    assert evidence["context"] == context["Investment AI"]["Financial Data background"]


def test_enabled_missing_corpus_fails(tmp_path):
    with pytest.raises(ValueError, match="no background"):
        FinancialData(str(tmp_path), include_background=True)


def test_embedding_cache_uses_full_text(tmp_path):
    library(tmp_path)
    calls = []
    def embed(text):
        calls.append(text)
        return [len(calls), 1]
    fin = FinancialData(str(tmp_path), embed_fn=embed)
    prefix = "a" * 2000
    assert fin._vec(prefix + "x") != fin._vec(prefix + "y")
    assert len(calls) == 2


def test_town_provider_switch_and_vector_tail(monkeypatch, tmp_path):
    from mavis_case01_injector import _providers
    monkeypatch.setenv("CASE01_FINANCIAL_BACKGROUND", "0")
    assert _providers.background_retrieval_provider(use_default_embed=True) is None
    library(tmp_path)
    monkeypatch.setenv("CASE01_FINANCIAL_BACKGROUND", "1")
    monkeypatch.setattr(_providers, "default_financial_data_dir", lambda: str(tmp_path))
    calls = []
    def embed(text):
        calls.append(text)
        return [1., 0.] if "4000" in text else [0., 1.]
    ai = _providers.background_retrieval_provider(embed_fn=embed)
    hits = ai.retrieve("4000座闪充站", "2026-08-27", background_only=True)
    assert hits[0]["document_id"] == "bg-long"
    assert hits[0]["char_start"] > 600
    assert ai.last_retrieval["library"]["ranking"] == "embedding"
    before = len(calls)
    ai.retrieve("4000座闪充站", "2026-08-27", background_only=True)
    assert len(calls) == before
