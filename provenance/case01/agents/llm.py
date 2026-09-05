# -*- coding: utf-8 -*-
"""轻量 LLM client。
- OllamaClient:本地 Ollama(OpenAI 兼容 /v1/chat/completions + /api/embed)
- OpenRouterClient:外部 API(OpenAI 兼容;key 从 secrets/环境变量读取,
  不落盘、不打印)——供 Ethan/Router 等非 Investment AI 角色使用
  (0904 规定 Investment AI 必须本地 Ollama 运行)。

chat(): 文本补全,支持 temperature/max_tokens;重试 + 超时(指数退避)。
"""
import json
import time
import urllib.request
import urllib.error
from typing import List, Optional

from .secrets import openrouter_key


class _ChatMixin:
    """共享的 OpenAI 兼容 chat 实现(base_url/model/headers 由子类提供)"""

    timeout = 120.0
    retries = 3

    def chat(self, messages: List[dict], temperature: float = 0.7,
             max_tokens: int = 1024, num_ctx: Optional[int] = None) -> Optional[str]:
        url = self._chat_url()
        body = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        # Ollama:长 prompt(如 Reflection 材料 12k+ 字符)需显式放大上下文,
        # 否则默认 num_ctx=2048 → HTTP 400
        ctx = num_ctx or getattr(self, "num_ctx", None)
        if ctx:
            body["options"] = {"num_ctx": ctx}
        data = json.dumps(body).encode("utf-8")
        last_err = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(
                    url, data=data,
                    headers=self._headers())
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    obj = json.loads(resp.read().decode("utf-8"))
                return obj["choices"][0]["message"]["content"]
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError("{} chat failed after {} retries: {}".format(
            type(self).__name__, self.retries, last_err))


class OllamaClient(_ChatMixin):
    def __init__(self, base_url: str = "http://127.0.0.1:11434",
                 chat_model: str = "qwen3:4b-instruct-2507-q4_K_M",
                 embed_model: str = "qwen3-embedding:0.6b-q8_0",
                 timeout: float = 120.0, retries: int = 3,
                 num_ctx: int = 32768):
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.timeout = timeout
        self.retries = retries
        self.num_ctx = num_ctx

    def _chat_url(self) -> str:
        return self.base_url + "/v1/chat/completions"

    def _headers(self) -> dict:
        return {"Content-Type": "application/json"}

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

    # ------------------------------------------------------------------
    def native_chat(self, messages: List[dict], temperature: float = 0.4,
                    max_tokens: int = 3072, num_ctx: int = 32768,
                    timeout: float = 600.0) -> Optional[str]:
        """Ollama 原生 /api/chat:支持 num_ctx(长 prompt 必需)。

        OpenAI 兼容 /v1/chat/completions 不接受 options/num_ctx,
        长 prompt(如 Reflection 材料 12k+ 字符)会因默认上下文小返回 400。
        timeout 默认 600s:8 维长反思在 4b 模型上单次生成可能 >120s。
        """
        url = self.base_url + "/api/chat"
        body = {
            "model": self.chat_model,
            "messages": messages,
            "options": {"num_ctx": num_ctx, "temperature": temperature},
            "stream": False,
        }
        data = json.dumps(body).encode("utf-8")
        last_err = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(
                    url, data=data,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    obj = json.loads(resp.read().decode("utf-8"))
                return obj["message"]["content"]
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError("Ollama native_chat failed after {} retries: {}".format(
            self.retries, last_err))


class OpenRouterClient(_ChatMixin):
    """外部 API(OpenAI 兼容)。key 从 secrets/env 读取;记录模型名但不含 key。

    默认模型 minimax/minimax-m3:free(可配);reasoning 支持按模型可选。
    """

    def __init__(self, model: str = "minimax/minimax-m3:free",
                 base_url: str = "https://openrouter.ai/api/v1",
                 api_key: str = "", timeout: float = 180.0, retries: int = 3):
        self.chat_model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self._api_key = api_key or openrouter_key()
        if not self._api_key:
            raise RuntimeError(
                "OpenRouter key 未配置:设置环境变量 OPENROUTER_API_KEY 或"
                "写入 case01/.secrets.json(不入 git)")

    def _chat_url(self) -> str:
        return self.base_url + "/chat/completions"

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._api_key,
        }
