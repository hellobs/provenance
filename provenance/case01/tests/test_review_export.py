"""平台交接包：多专业、失败分流、版本稳定性和信息边界。"""
import copy

import pytest

from case01.review_export import build_review_package


@pytest.fixture
def record():
    return {"run_id": "r1", "final_feedback": {"content": "最终反馈"},
            "reflection": {"text": "我没有充分核实信息来源。"},
            "router": {"expert_pool_version": "1.0", "issues": [{
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
])
def test_bad_issue_requires_manual_triage(record, changes):
    record["router"]["issues"][0].update(changes)
    out = build_review_package(record, "r1")
    assert out["status"] == "manual_triage"
    assert not out["task_candidates"]


def test_duplicate_ids_and_category_version(record):
    record["router"]["issues"] *= 2
    assert not build_review_package(record, "r1")["task_candidates"]
    record["router"]["expert_pool_version"] = "future"
    assert build_review_package(record, "r1")["manual_triage"][0]["reason"] == "category_version_mismatch"


def test_blocked_and_safe_views(record):
    record.update(branch="A", injector={"secret": True}, debug="truncated")
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
