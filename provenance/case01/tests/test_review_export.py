"""平台交接包：多专业、失败分流、版本稳定性和信息边界。"""
import copy

import pytest

from case01.review_export import build_review_package


@pytest.fixture
def record():
    # 池版本从签入的 expert_pool.json 现读，不写死：专家池升版时本 fixture 不会误红
    # （被测的是分流逻辑，不是池的版本号）；测不匹配的分支请显式覆盖该字段。
    from case01.expert_pool import load_expert_pool
    return {"run_id": "r1", "final_feedback": {"content": "最终反馈"},
            "reflection": {"text": "我没有充分核实信息来源。"},
            "router": {"expert_pool_version": load_expert_pool()["version"], "issues": [{
                "id": "issue-1", "summary": "信息核验不足", "risk": "high",
                "routing_reason": "需要核验来源", "expert_category_id": "E1",
                "secondary_expert_category_ids": ["E4", "E1", "E4"]}]}}


def test_multiple_categories_and_stable_revision(record):
    original = copy.deepcopy(record)
    out = build_review_package(record, "r1")
    assert out["status"] == "ready"
    assert [x["key"] for x in out["task_candidates"]] == [
        ["review", "r1", "issue-1", "E1"], ["review", "r1", "issue-1", "E4"]]
    assert out == build_review_package(record, "r1")
    assert record == original
    record["reflection"]["text"] += "下一次需要交叉验证。"
    assert out["revision"] != build_review_package(record, "r1")["revision"]


@pytest.mark.parametrize("router,reason", [
    ({"status": "error", "issues": []}, "router_error"),
    ({"status": "skipped", "executed": False}, "router_skipped"),
    ({"issues": []}, "router_empty"), ({}, "router_missing_or_invalid"),
])
def test_failed_router_preserves_reflection(record, router, reason):
    record["router"] = router
    out = build_review_package(record, "r1")
    assert out["status"] == "manual_triage"
    assert out["manual_triage"][0]["reason"] == reason
    assert out["snapshot"]["reflection"] == record["reflection"]
    assert not out["task_candidates"]


@pytest.mark.parametrize("changes", [
    {"expert_category_id": "BAD"}, {"secondary_expert_category_ids": ["BAD"]},
    {"risk": "wrong"}, {"summary": ""}, {"id": None}, {"match_status": "unmatched"},
    {"evidence_quote": None}, {"evidence_sentence_ids": [1]},
])
def test_bad_issue_requires_manual_triage(record, changes):
    record["router"]["issues"][0].update(changes)
    out = build_review_package(record, "r1")
    assert out["status"] == "manual_triage"
    assert not out["task_candidates"]


def test_duplicate_ids_and_category_version(record):
    record["router"]["issues"] *= 2
    out = build_review_package(record, "r1")
    assert not out["task_candidates"]
    # 撞名与"缺 id"分开报，且点名撞的是哪个 id，平台侧才能定位（N7）
    reasons = {t["reason"] for t in out["manual_triage"]}
    assert reasons == {"issue_ids_duplicated"}
    assert [t.get("conflict_id") for t in out["manual_triage"]] == ["issue-1", "issue-1"]
    record["router"]["expert_pool_version"] = "future"
    assert build_review_package(record, "r1")["manual_triage"][0]["reason"] == "category_version_mismatch"


def test_missing_id_is_reported_distinctly_from_duplicate(record):
    """缺 id 与撞名是两种问题：处置不同（补字段 vs 回退重跑 Router），reason 必须可分。"""
    record["router"]["issues"] = [
        {"id": "issue-1", "summary": "a", "risk": "low", "routing_reason": "r",
         "expert_category_id": "E1"},
        {"summary": "无 id", "risk": "low", "routing_reason": "r", "expert_category_id": "E1"},
        {"id": "issue-1", "summary": "撞名", "risk": "low", "routing_reason": "r",
         "expert_category_id": "E1"},
    ]
    out = build_review_package(record, "r1")
    by_reason = {}
    for t in out["manual_triage"]:
        by_reason.setdefault(t["reason"], []).append(t)
    assert "issue_id_missing" in by_reason and "issue_ids_duplicated" in by_reason
    # 缺 id 的项不带 conflict_id；撞名的项点名冲突 id
    assert "conflict_id" not in by_reason["issue_id_missing"][0]
    assert by_reason["issue_ids_duplicated"][0]["conflict_id"] == "issue-1"
    # 合法那条（issue-1 出现两次，均被踢）之外无候选；这里三条里 issue-1 撞名故无候选
    assert not out["task_candidates"]


def test_all_keys_are_unique_for_platform_dedup(record):
    """平台按 key 去重（见《平台对接契约》）：candidates 与 triage 的 key 必须全局唯一。

    位置占位键（invalid-issue-N）保证撞名/缺 id 的逐条 triage 不会因 issue_id 相同而互相覆盖；
    若两条 triage 的 key 相同，平台去重会静默丢一条。这条把该契约钉住。
    """
    record["router"]["issues"] = [
        {"id": "issue-1", "summary": "a", "risk": "low", "routing_reason": "r",
         "expert_category_id": "E1"},
        {"id": "issue-1", "summary": "撞名", "risk": "low", "routing_reason": "r",
         "expert_category_id": "E1"},
        {"summary": "无 id", "risk": "low", "routing_reason": "r", "expert_category_id": "E1"},
        {"id": "reflection-review", "summary": "与全文锚点同名", "risk": "low",
         "routing_reason": "r", "expert_category_id": "E1"},
    ]
    out = build_review_package(record, "r1")
    all_keys = [tuple(x["key"]) for x in out["task_candidates"]] + \
               [tuple(x["key"]) for x in out["manual_triage"]]
    assert len(all_keys) == len(set(all_keys)), "存在重复 key，平台去重会丢条目: {}".format(all_keys)
    # 撞名/缺 id 的逐条 triage 走位置占位键，能逐条定位
    triage_ids = {t["issue_id"] for t in out["manual_triage"]}
    assert {"invalid-issue-2", "invalid-issue-3"} <= triage_ids, triage_ids


def test_blocked_and_safe_views(record):
    record.update(injector={"secret": True}, debug="truncated")
    out = build_review_package(record, "r1")
    assert out["status"] == "blocked"
    assert out["snapshot"] is None and not out["task_candidates"]
    record.pop("debug")
    out = build_review_package(record, "r1")
    assert "branch" not in out["snapshot"] and "injector" not in out["snapshot"]
    assert "secret" not in out["full_context"]


def test_identity_and_missing_feedback(record):
    with pytest.raises(ValueError):
        build_review_package(record, "wrong")
    record.pop("final_feedback")
    assert build_review_package(record, "r1")["status"] == "blocked"


def test_mixed_candidates_and_triage_are_both_preserved(record):
    bad = copy.deepcopy(record["router"]["issues"][0])
    bad.update(id="issue-2", expert_category_id="BAD")
    record["router"]["issues"].append(bad)
    out = build_review_package(record, "r1")
    assert out["status"] == "manual_triage"
    assert len(out["task_candidates"]) == 2
    assert out["manual_triage"][0]["issue_id"] == "issue-2"


def test_legacy_field_and_failed_reflection(record):
    issue = record["router"]["issues"][0]
    issue.pop("expert_category_id")
    issue["field"] = "Evidence & Source Verification"
    assert build_review_package(record, "r1")["status"] == "ready"
    record["reflection"]["quality"] = {"status": "error"}
    out = build_review_package(record, "r1")
    assert out["status"] == "blocked" and not out["task_candidates"]


def test_category_content_changes_revision_even_with_same_version(record):
    from case01.expert_pool import load_expert_pool
    pool = load_expert_pool()
    out = build_review_package(record, "r1", pool)
    pool["categories"][0]["description"] += "补充职责"
    new = build_review_package(record, "r1", pool)
    assert out["record_revision"] == new["record_revision"]
    assert out["category_revision"] != new["category_revision"]
    assert out["revision"] != new["revision"]
