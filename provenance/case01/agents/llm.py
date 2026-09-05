# -*- coding: utf-8 -*-
"""轻量 Ollama client(OpenAI 兼容 /v1/chat/completions + /api/embed)。

case01 自带实现(不依赖 mavisframework 的 import 路径),保持可独立搬出。
- chat(): 文本补全,支持 temperature/max_tokens
- embed(): 文本向量(供 Financial Data 检索)
- 重试 + 超时(简单指数退避)
"""
import json
import time
import urllib.request
import urllib.error
from typing import List, Optional


class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434",
                 chat_model: str = "qwen3:4b-instruct-2507-q4_K_M",
                 embed_model: str = "qwen3-embedding:0.6b-q8_0",
                 timeout: float = 120.0, retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.timeout = timeout
        self.retries = retries

    # ------------------------------------------------------------------
    def chat(self, messages: List[dict], temperature: float = 0.7,
             max_tokens: int = 1024) -> Optional[str]:
        """OpenAI 兼容 chat。返回 assistant 文本;失败重试后返回 None。"""
        url = self.base_url + "/v1/chat/completions"
        body = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        data = json.dumps(body).encode("utf-8")
        last_err = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(
                    url, data=data,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    obj = json.loads(resp.read().decode("utf-8"))
                return obj["choices"][0]["message"]["content"]
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
                last_err = e
                time.sleep(2 * (attempt + 1))  # 退避 2/4/6s
        raise RuntimeError("Ollama chat failed after {} retries: {}".format(
            self.retries, last_err))

    # ------------------------------------------------------------------
    def embed(self, text: str) -> Optional[List[float]]:
        url = self.base_url + "/api/embed"
        body = {"model": self.embed_model, "input": text}
        data = json.dumps(body).encode("utf-8")
        last_err = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(
                    url, data=data,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    obj = json.loads(resp.read().decode("utf-8"))
                embs = obj.get("embeddings")
                if embs:
                    return embs[0]
                return None
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError("Ollama embed failed after {} retries: {}".format(
            self.retries, last_err))

    def is_available(self) -> bool:
        try:
            req = urllib.request.Request(self.base_url + "/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False
