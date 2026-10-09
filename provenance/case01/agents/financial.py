# -*- coding: utf-8 -*-
"""Financial Data 本地库加载 + 检索(Investment AI 唯一信息源)。

设计要点(对齐 0904「Financial Data」):
- 资料含来源类型/发布主体/时间/依赖线索(meta),检索结果保留这些,
  供 Investment AI 判断"多条消息是否同一来源 / 二次传播 / 情景假设"。
- 余弦相似度向量检索(embedding 由 OllamaClient.embed 提供),支持日期/公司过滤;
  显式启用背景库时按段落/句子分块,保留原文位置。
- 检索附加元信息:
    n_sources_after_agg: 命中结果中不同来源主体数(判断"来源独立性")
    second_hand_count:   cites_marketscope 等二次传播标记数
"""
import json
import hashlib
import math
import os
from typing import Dict, List, Optional

from .passages import passages, VERSION as PASSAGE_VERSION

BACKGROUND_ENV = "CASE01_FINANCIAL_BACKGROUND"


def background_enabled():
    value = os.environ.get(BACKGROUND_ENV, "0").strip().lower()
    if value not in ("0", "1", "false", "true", "off", "on", "no", "yes"):
        raise ValueError(f"{BACKGROUND_ENV} must be 0 or 1")
    return value in ("1", "true", "on", "yes")


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(y * y for y in b)) or 1e-9
    return dot / (na * nb)


class FinancialData:
    """文档库:加载 docs.json 目录,向量化缓存,检索返回带元数据的结果。"""

    def __init__(self, data_dir: str, embed_fn=None, include_background=None):
        """
        data_dir: 根目录,内含 <company>/docs.json(或平铺 *.json)
        embed_fn: callable(text)->vector;缺省 None 时仅词频检索(降级)
        """
        self.data_dir = data_dir
        self.embed_fn = embed_fn
        self.include_background = (background_enabled() if include_background is None
                                   else bool(include_background))
        self.docs: List[dict] = []
        # 资料目录里每一个 .json 都按"一份资料数组"读;读不出/不是数组的都留名字在这里,
        # 由 configuration() 带进每条检索记录 —— 静默跳过会让"少了一批资料"和
        # "目录里塞了个别的 .json"在产物里长得一模一样(2026-10-08 补)。
        self.load_skipped: List[str] = []
        self._vec_cache: Dict[str, List[float]] = {}
        self.last_ranking = "not_queried"
        self._load()
        if self.include_background and not any(self._background(d) for d in self.docs):
            raise ValueError("Background enabled but no background documents loaded")
        self.corpus_version = hashlib.sha256(json.dumps(
            self.docs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.search_docs = []
        for doc in self.docs:
            if not self.include_background:
                self.search_docs.append(doc)
                continue
            for index, (start, end, content) in enumerate(passages(doc.get("content", ""))):
                self.search_docs.append(dict(doc, content=content,
                    document_id=doc["id"], chunk_id=f'{doc["id"]}:p{index:04d}',
                    char_start=start, char_end=end))

    @staticmethod
    def _background(doc):
        # `or ""` 而不是 `.get("corpus","")`:手写/半截的 meta 里 `"corpus": null` 会让
        # `.startswith` 当场 AttributeError,把整个资料库的加载打断在半路。
        return bool(str((doc.get("meta") or {}).get("corpus") or "")
                    .startswith("market_background_"))

    def _embed_backend(self):
        """embed 是谁给的 —— 判官客户端 / 环境变量新建的本地客户端 / 没有。

        要进产物是因为这条会**静默换后端**:`bridge._retrieve_background` 先取
        `getattr(judge_llm, "embed", None)`,取不到才回落到 `local_client_from_env()`
        (= 打本机 11434)。只看代码看不出一局用的是哪个;落到记录里才追得回。
        """
        fn = self.embed_fn
        if fn is None:
            return "none(keyword 降级)"
        owner = getattr(fn, "__self__", None)
        if owner is None:
            return "callable(未绑定客户端)"
        model = getattr(owner, "embed_model", "") or "?"
        base = getattr(owner, "base_url", "") or getattr(owner, "api_base", "")
        return "{} model={} base={}".format(type(owner).__name__, model, base or "-")

    def configuration(self):
        return {"background_enabled": self.include_background,
                "corpus_version": self.corpus_version,
                "document_count": len(self.docs),
                "passage_count": len(self.search_docs),
                "chunking": PASSAGE_VERSION if self.include_background else "legacy-first-600",
                "ranking_requested": "embedding" if self.embed_fn else "keyword",
                "ranking": self.last_ranking,
                "embed_backend": self._embed_backend(),
                "skipped_json": list(self.load_skipped),
                "primary_top_k": 8,
                "background_top_k": 2 if self.include_background else 0}

    def _load(self):
        for root, _dirs, files in os.walk(self.data_dir):
            _dirs.sort()
            if not self.include_background:
                _dirs[:] = [name for name in _dirs if name != "background"]
            for fn in sorted(files):
                if fn.endswith(".json"):
                    p = os.path.join(root, fn)
                    try:
                        with open(p, encoding="utf-8") as f:
                            arr = json.load(f)
                    except Exception as e:
                        self.load_skipped.append("{}(读不出:{})".format(p, e))
                        print("[financial] skip {}: {}".format(p, e))
                        continue
                    if not isinstance(arr, list):
                        # 此前这条**连一声都不出**(只是 not isinstance 就什么也不做):
                        # 目录里放一个 dict 形的 manifest.json,资料数就静默少一批。
                        self.load_skipped.append("{}(不是资料数组)".format(p))
                        print("[financial] skip {}: JSON 不是资料数组".format(p))
                        continue
                    for d in arr:
                        if self._background(d) and not self.include_background:
                            continue
                        d.setdefault("company", os.path.basename(root))
                        self.docs.append(d)

    # ------------------------------------------------------------------
    def _vec(self, text: str) -> Optional[List[float]]:
        if not self.embed_fn:
            return None
        key = text
        if key not in self._vec_cache:
            v = self.embed_fn(text)
            if v is not None:
                self._vec_cache[key] = v
        return self._vec_cache.get(key)

    def search(self, query: str, top_k: int = 8,
               type_filter: Optional[str] = None,
               since: Optional[str] = None,
               company: Optional[str] = None,
               background: Optional[bool] = None,
               distinct_documents: bool = False) -> List[dict]:
        """返回带 score 与元信息的文档列表(降序)。
        since: 只返回 time <= since 的文档(信息权限:不得让模型看到
        模拟日期之后才发生的资料——未来公告/事件泄露会污染判断)。
        company: 只检索该公司的资料。加载侧本来就按 `<company>/docs.json`
        递归读(见 `_load`),所以接第二家、第三家公司时只要放目录;
        这里补的是**检索侧的公司过滤**,不然多公司数据一进来会互相串味。
        """
        if top_k <= 0:
            return []
        qv = self._vec(query)
        self.last_ranking = "embedding" if qv is not None else "keyword"
        scored = []
        for d in self.search_docs:
            if background is not None and self._background(d) != background:
                continue
            if company and str(d.get("company", "")) != company:
                continue
            if type_filter and d.get("type") != type_filter:
                continue
            if since:
                d_time = str(d.get("time", ""))[:10]
                # 无 time 的资料视为公司基础资料,允许(不随时间失效)
                if d_time and d_time > since:
                    continue
            text = "{} {} {}".format(d.get("title", ""), d.get("content", ""),
                                     d.get("source", ""))
            if qv is not None:
                dv = self._vec(text)
                if dv is None:
                    continue
                score = _cosine(qv, dv)
            else:
                # 降级:词频(查询词出现次数)
                qwords = set(query.lower().split())
                score = sum(1 for w in qwords if w in text.lower())
                if score == 0:
                    continue
            item = dict(d)
            item["score"] = round(score, 4)
            scored.append(item)
        scored.sort(key=lambda x: x["score"], reverse=True)
        if distinct_documents:
            unique = []
            seen = set()
            for item in scored:
                identity = item.get("document_id", item.get("id"))
                if identity not in seen:
                    unique.append(item)
                    seen.add(identity)
            scored = unique
        return scored[:top_k]

    def search_context(self, query, current_date, top_k=8, background_only=False):
        if not self.include_background:
            return self.search(query, top_k=top_k, since=current_date)
        # 主料名额**不写死公司**:`background=False` 已经把这批"非背景库资料"整份隔开了,
        # 再接第二家目标公司时不必改这里;此前写 `company="hcm"` 会让新公司的资料
        # 静默拿不到主料名额(实测当前语料只有 hcm,所以今天不咬人)。
        primary = [] if background_only else self.search(
            query, top_k=top_k, since=current_date, background=False,
            distinct_documents=True)
        background = self.search(query, top_k=2, since=current_date, background=True,
                                 distinct_documents=True)
        return ([dict(d, evidence_scope="target_company") for d in primary]
                + [dict(d, evidence_scope="market_background") for d in background])

    def source_stats(self, results: List[dict]) -> dict:
        """来源独立性统计:不同发布主体数、二次传播数。"""
        sources = set()
        second_hand = 0
        seen = set()
        for r in results:
            identity = r.get("document_id") or r.get("id")
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            if r.get("source"):
                sources.add(str(r["source"]))
            meta = r.get("meta") or {}
            if meta.get("cites_marketscope") or meta.get("second_hand") \
               or meta.get("claims_institutions"):
                second_hand += 1
        return {"n_sources": len(sources),
                "second_hand_count": second_hand,
                "sources": sorted(sources)}
