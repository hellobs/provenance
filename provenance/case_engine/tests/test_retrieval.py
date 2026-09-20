# -*- coding: utf-8 -*-
"""case_engine/retrieval.py 单元测试。"""
import json

from case_engine.retrieval import DocumentRetrieval


def _mk(tmp_path, docs0001, docs_other=None):
    root = tmp_path / "alpha"
    root.mkdir()
    (root / "docs.json").write_text(json.dumps(docs0001), encoding="utf-8")
    if docs_other is not None:
        other = tmp_path / "beta"
        other.mkdir()
        (other / "docs.json").write_text(json.dumps(docs_other), encoding="utf-8")
    return str(tmp_path)


def test_load_and_search_fallback(tmp_path):
    docs = [
        {"title": "报告中提及订单落地", "content": "alpha 确认获得大额订单,生产线满负荷",
         "source": "官方公告", "type": "disclosure"},
        {"title": "行业周报", "content": "整体环境平稳", "source": "行业媒体",
         "type": "media"},
    ]
    root = _mk(tmp_path, docs)
    dr = DocumentRetrieval(root)  # 无 embed_fn,走词频降级
    res = dr.search("订单", top_k=5)
    assert res, "应命中订单文档"
    assert res[0]["score"] > 0
    assert "company" in res[0]


def test_type_filter(tmp_path):
    docs = [{"title": "a", "content": "x y z", "source": "s1", "type": "disclosure"},
            {"title": "b", "content": "x y z", "source": "s2", "type": "media"}]
    root = _mk(tmp_path, docs)
    dr = DocumentRetrieval(root)
    assert all(d["type"] == "media" for d in dr.search("x ", type_filter="media"))


def test_since_filters_future(tmp_path):
    docs = [{"title": "旧", "content": "z q g", "source": "s", "time": "2026-01-05"},
            {"title": "新(未来)", "content": "z q g", "source": "s", "time": "2026-05-01"}]
    root = _mk(tmp_path, docs)
    dr = DocumentRetrieval(root)
    res = dr.search("z ", since="2026-03-01")
    assert len(res) == 1 and res[0]["title"] == "旧"


def test_company_filter(tmp_path):
    docs_a = [{"title": "a单", "content": "m n p", "source": "s", "type": "t"}]
    docs_b = [{"title": "b单", "content": "m n p", "source": "s", "type": "t"}]
    root = _mk(tmp_path, docs_a, docs_b)
    dr = DocumentRetrieval(root)
    only_a = dr.search("m ", company="alpha")
    only_b = dr.search("m ", company="beta")
    assert [d["title"] for d in only_a] == ["a单"]
    assert [d["title"] for d in only_b] == ["b单"]


def test_topk_and_sort(tmp_path):
    docs = [{"title": "t{}".format(i), "content": "key word {}".format(i),
             "source": "s", "type": "t"} for i in range(10)]
    root = _mk(tmp_path, docs)
    dr = DocumentRetrieval(root)
    res = dr.search("key 3", top_k=3)
    assert len(res) <= 3
    scores = [d["score"] for d in res]
    assert scores == sorted(scores, reverse=True)


def test_source_stats(tmp_path):
    docs = [
        {"title": "a", "content": "q w e", "source": "S1", "type": "t"},
        {"title": "b", "content": "q w e", "source": "S1",
         "meta": {"second_hand": True}, "type": "t"},
        {"title": "c", "content": "q w e", "source": "S2", "type": "t"},
    ]
    root = _mk(tmp_path, docs)
    dr = DocumentRetrieval(root)
    res = dr.search("q ")
    stats = dr.source_stats(res, second_hand_keys=["cites_marketscope",
                                                    "second_hand",
                                                    "claims_institutions"])
    assert stats["n_sources"] == 2
    assert stats["second_hand_count"] == 1
    assert set(stats["sources"]) == {"S1", "S2"}


def test_source_stats_without_keys(tmp_path):
    docs = [{"title": "a", "content": "q w e", "source": "S1",
             "meta": {"second_hand": True}, "type": "t"}]
    root = _mk(tmp_path, docs)
    dr = DocumentRetrieval(root)
    stats = dr.source_stats(dr.search("q "))   # 不传键 → 二次传播数恒 0
    assert stats["second_hand_count"] == 0


def test_skip_corrupt_json(tmp_path, capsys):
    # 非法 JSON 才触发 skip 打印;合法但非 list 的 JSON 静默跳过(原实现语义)
    root = tmp_path / "alpha"
    root.mkdir()
    (root / "bad.json").write_text("{not valid json !!", encoding="utf-8")
    (root / "scalar.json").write_text('{"not": "a list"}', encoding="utf-8")
    dr = DocumentRetrieval(str(tmp_path))
    assert dr.docs == []
    assert "skip" in capsys.readouterr().out