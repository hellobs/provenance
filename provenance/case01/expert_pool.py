# -*- coding: utf-8 -*-
"""Case01 专家类别池。

需求材料要求 Router 匹配“当前系统中的专家类别 / 专家池”，而不是自由创造
类别名。本模块提供仓库内初始目录，并允许治理平台用环境变量替换目录文件。
这里只管理类别，不持有专家姓名、可用状态或任务分配状态。
"""
import json
import os
from typing import Dict, Optional


DEFAULT_POOL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "expert_pool.json")


def expert_pool_path() -> str:
    """专家池文件路径:环境变量优先(**须绝对**),否则用仓库内默认。

    2026-10-05 补(GTC 体检 N5):此前裸读 `os.environ.get(...).strip() or DEFAULT`,
    相对值按 cwd 解释、`..` 不拦 —— 是"守卫按调用点铺开、而非按所有根铺开"的
    第二个漏点(`review_export.py` 会打开这个路径)。现改走 `resolve_root`,
    与 `case01/orchestrator.RUNS_ROOT()`、`live/history._data_root` 同语义。
    """
    from case_engine.paths import resolve_root
    return resolve_root("CASE01_EXPERT_POOL_PATH", DEFAULT_POOL_PATH,
                        what="CASE01_EXPERT_POOL_PATH")


def load_expert_pool(path: str = "") -> dict:
    target = path or expert_pool_path()
    with open(target, encoding="utf-8") as f:
        raw = json.load(f)
    categories = raw.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError("expert pool requires a non-empty categories list")
    seen = set()
    normalized = []
    for item in categories:
        if not isinstance(item, dict):
            raise ValueError("expert category must be an object")
        category_id = str(item.get("id") or "").strip().upper()
        name = str(item.get("name") or "").strip()
        if not category_id or not name or category_id in seen:
            raise ValueError("expert category id/name missing or duplicated: {}".format(
                category_id))
        seen.add(category_id)
        normalized.append({
            "id": category_id,
            "name": name,
            "description": str(item.get("description") or "").strip(),
            "aliases": [str(x).strip() for x in item.get("aliases") or [] if str(x).strip()],
            "keywords": [str(x).strip() for x in item.get("keywords") or [] if str(x).strip()],
        })
    return {
        "version": str(raw.get("version") or "").strip() or "unversioned",
        "name": str(raw.get("name") or "").strip(),
        "description": str(raw.get("description") or "").strip(),
        "categories": normalized,
    }


def category_index(pool: Optional[dict] = None) -> Dict[str, dict]:
    data = pool or load_expert_pool()
    return {x["id"]: x for x in data["categories"]}


def resolve_category(category_id: str = "", field: str = "",
                     pool: Optional[dict] = None) -> Optional[dict]:
    """优先按稳定 ID 匹配；兼容旧输出时允许按规范名称/别名精确匹配。"""
    data = pool or load_expert_pool()
    by_id = category_index(data)
    cid = str(category_id or "").strip().upper()
    if cid in by_id:
        return by_id[cid]
    label = str(field or "").strip().casefold()
    if not label:
        return None
    for item in data["categories"]:
        labels = [item["name"]] + item.get("aliases", [])
        if label in {x.casefold() for x in labels}:
            return item
    return None


_EN_CATEGORIES = {
    "E1": "Information and evidence verification: disclosures, rumors, source independence and reliability",
    "E2": "Finance and valuation: financial statements, order value, forecasts and valuation",
    "E3": "Industry and business: supply chains, customers, procurement and competition",
    "E4": "Market and trading risk: price, volatility, position sizing and execution",
    "E5": "Legal and compliance: disclosure, contracts, securities and advisory duties",
    "E6": "Client suitability and investment advice: resources, objectives and risk tolerance",
    "E7": "Behavioral finance and conflicts of interest: bias, sentiment and incentives",
    "E8": "AI models and governance: hallucination, reasoning, traceability and model risk",
}


def prompt_catalog(pool: Optional[dict] = None, language: str = "legacy") -> str:
    data = pool or load_expert_pool()
    if language == "en":
        lines = ["Available expert category IDs (version={}):".format(data["version"])]
        for item in data["categories"]:
            lines.append("- {} | {}".format(
                item["id"], _EN_CATEGORIES.get(item["id"], item["name"])))
        lines.append("Choose only an ID above. Use UNMATCHED and suggested_field if none fits.")
        return "\n".join(lines)
    lines = ["当前可用专家类别池(version={}):".format(data["version"])]
    for item in data["categories"]:
        lines.append("- {} | {} | {}".format(
            item["id"], item["name"], item["description"]))
    lines.append("只能从以上 ID 中选择。确实无法匹配时使用 UNMATCHED，并填写 suggested_field。")
    return "\n".join(lines)
