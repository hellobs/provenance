# -*- coding: utf-8 -*-
"""专家可见性口径守卫:`quality` 标签与"默认隐藏"的两处实现必须同口径(2026-10-04)。

这条线是科研口径的承重点:
- `ok` / `unverified` **必须默认可见** —— "跑得差"(质量门 fail 但反思是真文本)与
  "判不了"(旧记录没戳)都是要留给专家复核的样本,藏起来等于替专家做决定;
- `questionable` / `debug` / `deprecated` / `unreadable` **必须默认隐藏且计入 excluded**
  —— 隐藏本身可以,但必须报出条数与 run_id(铁律:失败了必须有人知道)。

同一个"隐藏集合"在两处各写了一份:5002 的 `case01/serve.py` 里是函数内的局部
`hidden`,评审侧是 `live/history.py` 的 `_HIDDEN_QUALITY`。两处一漂移,平台面与专家面
就会对同一批记录给出不同总数(批前基线 272/234/38 与本仓文件侧 242/213/29 的差
就是这类"同一问题两套口径"的账)。所以这里不比对源码字符串,而是**逐标签喂进去、
看两边各自放出来哪些**,把口径钉在行为上。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01 import serve  # noqa: E402
from case01.full_context import quality_of  # noqa: E402
from live import history as lh  # noqa: E402

LABELS = ("ok", "unverified", "questionable", "debug", "deprecated", "unreadable")
VISIBLE = {"ok", "unverified"}


def _rows():
    return [{"run_id": "r-" + q, "quality": q, "consistency": "unverified"}
            for q in LABELS]


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    """把索引源换成六个标签各一条的合成记录(不起服务、不碰真实 runs)。"""
    monkeypatch.setattr(serve.fc, "list_runs", lambda root: _rows())
    return tmp_path


def test_service_view_keeps_poor_and_unverifiable_visible(synthetic):
    body = serve.list_runs(False)
    assert {r["quality"] for r in body["runs"]} == VISIBLE, \
        "跑得差/判不了的样本必须留给专家,默认隐藏它们=替专家做决定"
    assert body["count"] == len(VISIBLE)


def test_service_view_hides_the_unreviewable_and_says_which(synthetic):
    body = serve.list_runs(False)
    hidden = set(LABELS) - VISIBLE
    assert set(body["excluded"]["by_kind"]) == hidden
    assert body["excluded"]["count"] == len(hidden)
    # 不静默:隐藏的要能点名
    for q in hidden:
        assert body["excluded"]["by_kind"][q] == ["r-" + q]
    assert "include_questionable" in body["excluded"]["reason"]


def test_full_listing_is_the_whole_population(synthetic):
    body = serve.list_runs(True)
    assert body["count"] == len(LABELS) and "excluded" not in body, \
        "全量口径必须一条不少;既然一条没藏,就不该再报 excluded"


def test_both_implementations_hide_exactly_the_same_kinds(synthetic):
    """两处隐藏集合必须**完全一致**。

    `live/history._HIDDEN_QUALITY` 的注释写的就是"与 5002 serve.list_runs 同口径",
    2026-10-04 实测两边差一个 `unreadable`(平台面会把它当可见记录发出去)。
    同口径不该靠注释维持,所以这里逐标签喂进去比行为。
    """
    serve_hidden = set(LABELS) - {r["quality"] for r in serve.list_runs(False)["runs"]}
    assert serve_hidden == set(lh._HIDDEN_QUALITY), \
        "两处隐藏集合分叉:同一批记录在平台面与专家面会有不同总数"
    assert "unverified" not in lh._HIDDEN_QUALITY and "ok" not in lh._HIDDEN_QUALITY


# ---------------------------------------------------------------------------
# quality_of 能给出的标签,一个都不许落在两套可见性名单之外
# ---------------------------------------------------------------------------
def _record_for(label):
    if label == "ok":
        return {"consistency": {"verdict": "consistent"}}
    if label == "unverified":
        return {"consistency": {"verdict": "unknown"}}
    if label == "questionable":
        return {"consistency": {"verdict": "inconsistent"}}
    if label == "debug":
        return {"debug": "--nodes 截断", "consistency": {"verdict": "consistent"}}
    if label == "deprecated":
        return {"deprecated": True, "consistency": {"verdict": "consistent"}}
    return None


@pytest.mark.parametrize("label", [x for x in LABELS if x != "unreadable"])
def test_every_label_quality_of_can_emit_is_classified(label):
    rec = _record_for(label)
    assert quality_of(rec)["quality"] == label, "合成记录本身要先对上标签"
    visible = label not in lh._HIDDEN_QUALITY
    assert visible == (label in VISIBLE), \
        "{} 的可见性归类与守卫口径不一致".format(label)


def test_unreadable_only_comes_from_a_missing_run_json():
    """`unreadable` 不是 quality_of 的输出,是"有目录没文件"的占位行专属。"""
    row = lh._brief_unreadable("r-gone")
    assert row["quality"] == "unreadable" and row["consistency"] == "unverified"
    assert row["error"], "占位行必须自带原因,否则与『没有这条』无从区分"
    assert row["quality"] in lh._HIDDEN_QUALITY, "没跑成的不给平台建单"
    assert quality_of({"reflection": {"text": ""}})["quality"] != "unreadable"


def test_quality_lookup_failure_is_reported_not_disguised_as_ok(monkeypatch):
    """质检取不到时只能明说"取不到",不许给 ok、也不许留空 reason。"""
    import case01.full_context as fc

    def boom(rec):
        raise RuntimeError("quality_of 挂了")

    monkeypatch.setattr(fc, "quality_of", boom)
    q = lh._quality_of({"run_id": "r-1"})
    assert q["quality"] == "unverified"
    assert q["reason"], "降级必须留痕:静默 unverified 会让读者以为是记录没有戳"
    assert "取不到" in q["reason"]


def test_expert_safe_view_does_not_leak_manifest_or_branch_truth():
    """安全视图的白名单外一律不进:manifest(实验元信息)与顶层 branch。"""
    rec = {"run_id": "r-1", "branch": "A",
           "manifest": {"manifest_warnings": ["max_tokens 截断"], "scenario": "x"},
           "consistency": {"verdict": "inconsistent"},
           "reflection": {"text": "正文"}}
    out = lh.expert_safe_record(rec)
    assert "manifest" not in out and "consistency" not in out and "branch" not in out
    assert out["reflection"]["text"] == "正文", "专家要看的正文不能被连带删掉"
    assert out["_view"] == "expert-safe"
