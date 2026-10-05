"""治理平台只读交接包：导出建单候选，不创建专家任务、不写审核结果。"""
import copy
import hashlib
import json
from collections import Counter
from typing import Literal, Optional

from pydantic import BaseModel

from .expert_pool import load_expert_pool, resolve_category
from .full_context import build_full_context, normalize_record, quality_of


class TaskCandidate(BaseModel):
    key: list[str]
    issue_id: str
    expert_category_id: str
    summary: str
    risk: Literal["low", "medium", "high"]
    routing_reason: str
    evidence_quote: str
    evidence_sentence_ids: list[str]


class ManualTriage(BaseModel):
    issue_id: str
    reason: str
    key: list[str]


class ReviewPackage(BaseModel):
    schema_version: Literal["1.0"]
    source: Literal["review"]
    run_id: str
    status: Literal["ready", "manual_triage", "blocked"]
    blocked_reasons: list[str]
    task_candidates: list[TaskCandidate]
    manual_triage: list[ManualTriage]
    expert_pool_version: str
    expert_categories: list[dict]
    router_pool_version: Optional[str]
    snapshot: Optional[dict]
    full_context: str
    ownership: dict[str, str]
    record_revision: str
    category_revision: str
    revision: str


class ReviewPackageResponse(BaseModel):
    ok: Literal[True]
    data: ReviewPackage


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_review_package(record, run_id, pool=None):
    """以磁盘目录 ID 为身份；版本取内容哈希，重复读取不会生成新版本。"""
    from live.history import expert_safe_record

    pool = load_expert_pool() if pool is None else pool
    if not isinstance(record, dict):
        raise ValueError("Run 记录必须是对象")
    if record.get("run_id") and record["run_id"] != run_id:
        raise ValueError("Run 记录与目录标识不一致")
    safe = expert_safe_record(copy.deepcopy(record))
    safe["run_id"] = run_id
    normalized = normalize_record(safe)
    ref = record.get("reflection")
    rout = record.get("router")
    ref = ref if isinstance(ref, dict) else {}
    rout = rout if isinstance(rout, dict) else {}
    text = ref.get("text")
    issues = rout.get("issues")
    candidates, triage, blocked = [], [], []
    q = quality_of(normalize_record(record))
    # 不能通过本接口绕过索引的质量门；只返回通用原因，避免暴露实验分支。
    if q.get("quality") in ("questionable", "debug", "deprecated", "unreadable"):
        blocked.append("run_not_reviewable")
    if not isinstance(text, str) or not text.strip():
        blocked.append("reflection_missing")
    if not isinstance(record.get("final_feedback"), dict) or not record["final_feedback"]:
        blocked.append("final_feedback_missing")
    ref_quality = ref.get("quality")
    if isinstance(ref_quality, dict) and ref_quality.get("status") == "error":
        blocked.append("reflection_failed")

    def manual(code, issue_id="reflection-review"):
        triage.append({"issue_id": issue_id, "reason": code,
                       "key": ["review", run_id, issue_id, "manual-triage"]})

    stage = rout.get("status")
    if not blocked:
        if stage in ("error", "skipped") or rout.get("executed") is False:
            manual("router_" + (stage if stage in ("error", "skipped") else "skipped"))
        elif not isinstance(issues, list):
            manual("router_missing_or_invalid")
        elif not issues:
            manual("router_empty")
        elif rout.get("expert_pool_version") not in (None, "", pool["version"]):
            manual("category_version_mismatch")
        else:
            ids = Counter(x.get("id") for x in issues
                          if isinstance(x, dict) and isinstance(x.get("id"), str))
            for pos, item in enumerate(issues):
                if not isinstance(item, dict):
                    manual("issue_invalid", "invalid-issue-{}".format(pos + 1))
                    continue
                iid = item.get("id")
                if not isinstance(iid, str) or not iid.strip() or ids[iid] != 1:
                    manual("issue_id_missing_or_duplicated", "invalid-issue-{}".format(pos + 1))
                    continue
                if (any(not isinstance(item.get(k), str) or not item[k].strip()
                        for k in ("summary", "routing_reason"))
                        or item.get("risk") not in ("low", "medium", "high")
                        or not isinstance(item.get("evidence_quote", ""), str)
                        or not isinstance(item.get("evidence_sentence_ids", []), list)
                        or any(not isinstance(x, str) for x in item.get("evidence_sentence_ids", []))):
                    manual("issue_fields_invalid", iid)
                    continue
                cid = item.get("expert_category_id")
                # 有 ID 时不通过名称掩盖无效 ID；旧记录无 ID 才允许精确名称/别名匹配。
                category = resolve_category(category_id=cid, pool=pool) if cid else resolve_category(
                    field=item.get("field", ""), pool=pool)
                secondary = item.get("secondary_expert_category_ids", [])
                if (item.get("match_status") == "unmatched" or not category
                        or not isinstance(secondary, list)
                        or any(not isinstance(x, str) or not resolve_category(category_id=x, pool=pool)
                               for x in secondary)):
                    manual("category_unmatched_or_invalid", iid)
                    continue
                categories = [category["id"]] + [resolve_category(category_id=x, pool=pool)["id"]
                                                   for x in secondary]
                for category_id in dict.fromkeys(categories):
                    candidates.append({
                        "key": ["review", run_id, iid, category_id],
                        "issue_id": iid, "expert_category_id": category_id,
                        "summary": item["summary"], "risk": item["risk"],
                        "routing_reason": item["routing_reason"],
                        "evidence_quote": item.get("evidence_quote", ""),
                        "evidence_sentence_ids": item.get("evidence_sentence_ids", []),
                    })
    status = "blocked" if blocked else "manual_triage" if triage else "ready"
    # blocked 不提供可直接用于建单的正文；平台从列表获取导入失败提示。
    payload = {
        "schema_version": "1.0", "source": "review", "run_id": run_id,
        "status": status, "blocked_reasons": blocked,
        "task_candidates": candidates, "manual_triage": triage,
        "expert_pool_version": pool["version"],
        "expert_categories": copy.deepcopy(pool["categories"]),
        "router_pool_version": rout.get("expert_pool_version") or None,
        "snapshot": None if blocked else safe,
        "full_context": "" if blocked else build_full_context(normalized),
        "ownership": {"tasks": "governance_platform", "reviews": "governance_platform"},
    }
    # 包含数据与类别目录哈希，数据或配置变化时要求平台显式建立新审核批次。
    payload["record_revision"] = _digest(safe)
    payload["category_revision"] = _digest(pool)
    payload["revision"] = _digest(payload)
    ReviewPackage(**payload)
    return payload
