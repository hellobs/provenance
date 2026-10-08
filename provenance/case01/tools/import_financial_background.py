"""Convert the supplied Chinese JSONL to traceable FinancialData documents.

Run from repository root: python provenance/case01/tools/import_financial_background.py
"""
import argparse
from collections import defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
import re


CASE_DIR = Path(__file__).resolve().parents[1]
SOURCE_FILE = CASE_DIR / "data" / "financial_sources" / "模型版_100条.jsonl"
TYPES = {"公司资料": "company_profile", "财务数据": "financial", "公告": "disclosure",
         "行业媒体": "industry_media", "社交媒体": "social",
         "财经自媒体": "self_media", "专业机构研究": "research"}
CORPUS = "market_background_100_v1"


def convert(source: Path):
    raw = source.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    groups = defaultdict(list)
    for line_no, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        for field in ("公司名称和股票代码", "资料类型", "来源", "发布时间", "标题", "正文"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"line {line_no}: missing {field}")
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", row["发布时间"])
        if not dates:
            raise ValueError(f"line {line_no}: cannot resolve publication date")
        # Conservative day-level availability: latest date in the supplied field.
        # Do not use report period or invent missing timezone/source URLs.
        available = max(date.fromisoformat(d) for d in dates).isoformat()
        company_name = row["公司名称和股票代码"]
        codes = re.findall(r"\d+\.(?:SZ|SH|HK)|(?:NYSE|NASDAQ|SGX):\s*[A-Z]+", company_name)
        if not codes:
            raise ValueError(f"line {line_no}: no company identifier")
        company = re.sub(r"[^a-z0-9]+", "_", codes[0].lower()).strip("_")
        doc_hash = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        groups[company].append({
            "id": "bg-" + doc_hash[:20], "company": company,
            "company_name": company_name, "tickers": codes,
            "type": TYPES[row["资料类型"]], "source": row["来源"],
            "time": available, "title": row["标题"], "content": row["正文"],
            "meta": {"corpus": CORPUS, "publication_time_raw": row["发布时间"],
                     "date_policy": "latest_explicit_date_day_precision",
                     "source_file": "模型版_100条.jsonl", "source_line": line_no,
                     "source_sha256": source_hash, "document_sha256": doc_hash,
                     "verification": "source_not_independently_verified"},
        })
    ids = [d["id"] for docs in groups.values() for d in docs]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate source documents")
    return raw, dict(groups)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_FILE)
    args = parser.parse_args()
    raw, groups = convert(args.source)
    # Keep the original outside the recursively loaded financial directory.
    original = SOURCE_FILE
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(raw)
    for company, docs in sorted(groups.items()):
        dest = CASE_DIR / "data" / "financial" / "background" / company / "docs.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(docs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {sum(map(len, groups.values()))} documents / {len(groups)} companies")


if __name__ == "__main__":
    main()
