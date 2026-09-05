# -*- coding: utf-8 -*-
"""Financial Data 本地库加载 + 检索(Investment AI 唯一信息源)。

设计要点(对齐 0904「Financial Data」):
- 资料含来源类型/发布主体/时间/依赖线索(meta),检索结果保留这些,
  供 Investment AI 判断"多条消息是否同一来源 / 二次传播 / 情景假设"。
- M1 实现:余弦相似度向量检索(embedding 由 OllamaClient.embed 提供),
  支持按 type 过滤;后续资料库变大再加倒排/分块。
- 检索附加元信息:
    n_sources_after_agg: 命中结果中不同来源主体数(判断"来源独立性")
    second_hand_count:   cites_marketscope 等二次传播标记数
"""
import json
import math
import os
from typing import Dict, List, Optional


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(y * y for y in b)) or 1e-9
    return dot / (na * nb)


class FinancialData:
    """文档库:加载 docs.json 目录,向量化缓存,检索返回带元数据的结果。"""

    def __init__(self, data_dir: str, embed_fn=None):
        """
        data_dir: 根目录,内含 <company>/docs.json(或平铺 *.json)
        embed_fn: callable(text)->vector;缺省 None 时仅词频检索(降级)
        """
        self.data_dir = data_dir
        self.embed_fn = embed_fn
        self.docs: List[dict] = []
        self._vec_cache: Dict[str, List[float]] = {}
        self._load()

    def _load(self):
        for root, _dirs, files in os.walk(self.data_dir):
            for fn in sorted(files):
                if fn.endswith(".json"):
                    p = os.path.join(root, fn)
                    try:
                        with open(p, encoding="utf-8") as f:
                            arr = json.load(f)
                        if isinstance(arr, list):
                            for d in arr:
                                d.setdefault("company", os.path.basename(root))
                                self.docs.append(d)
                    except Exception as e:
                        print("[financial] skip {}: {}".format(p, e))

    # ------------------------------------------------------------------
    def _vec(self, text: str) -> Optional[List[float]]:
        if not self.embed_fn:
            return None
        key = text[:2000]
        if key not in self._vec_cache:
            v = self.embed_fn(text)
            if v is not None:
                self._vec_cache[key] = v
        return self._vec_cache.get(key)

    def search(self, query: str, top_k: int = 8,
               type_filter: Optional[str] = None,
               since: Optional[str] = None) -> List[dict]:
        """返回带 score 与元信息的文档列表(降序)。
        since: 只返回 >= 该日期(模拟日期过滤,已释放过滤由上层做)。
        """
        qv = self._vec(query)
        scored = []
        for d in self.docs:
            if type_filter and d.get("type") != type_filter:
                continue
            if since and str(d.get("time", ""))[:10] < since:
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
        return scored[:top_k]

    def source_stats(self, results: List[dict]) -> dict:
        """来源独立性统计:不同发布主体数、二次传播数。"""
        sources = set()
        second_hand = 0
        for r in results:
            if r.get("source"):
                sources.add(str(r["source"]))
            meta = r.get("meta") or {}
            if meta.get("cites_marketscope") or meta.get("second_hand") \
               or meta.get("claims_institutions"):
                second_hand += 1
        return {"n_sources": len(sources),
                "second_hand_count": second_hand,
                "sources": sorted(sources)}
