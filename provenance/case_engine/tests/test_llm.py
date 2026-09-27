# -*- coding: utf-8 -*-
"""case_engine/llm.py 单元测试(不触网,只验证构造/逻辑分支/报错)。"""
import os

import pytest

from case_engine.llm import (
    openrouter_key, OllamaClient, LocalHFClient, OpenRouterClient,
)


def test_openrouter_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "  sk-test-key  ")
    assert openrouter_key() == "sk-test-key"


def test_openrouter_key_empty_no_env(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # 密封:关掉 .env 与 .secrets.json 两处兜底,确保"确实没有"→ 空串(不报错)
    # (不关的话,本机若存在 .env / .secrets.json 会读到真实 key,测试不再密封)
    monkeypatch.setattr("case_engine.llm._env_files", lambda: [])
    monkeypatch.setattr("case_engine.llm._secrets_candidates", lambda: [])
    assert openrouter_key() == ""


def test_ollama_defaults():
    c = OllamaClient()
    assert c.base_url == "http://127.0.0.1:11434"
    assert c._chat_url() == "http://127.0.0.1:11434/v1/chat/completions"
    assert c._headers() == {"Content-Type": "application/json"}


def test_ollama_base_url_trailing_slash_stripped():
    c = OllamaClient(base_url="http://localhost:8080/")
    assert c.base_url == "http://localhost:8080"


def test_localhf_requires_deps_or_path():
    with pytest.raises(RuntimeError):
        LocalHFClient(chat_model_path="")._ensure_chat()


def test_openrouter_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("case_engine.llm._env_files", lambda: [])
    monkeypatch.setattr("case_engine.llm._secrets_candidates", lambda: [])
    with pytest.raises(RuntimeError, match="key"):
        OpenRouterClient()
    # 显式传 key 则 OK(不报错)
    oc = OpenRouterClient(api_key="k")
    assert oc._chat_url() == "https://openrouter.ai/api/v1/chat/completions"
    assert oc._headers()["Authorization"] == "Bearer k"


def test_openrouter_key_not_in_headers_log(monkeypatch):
    # 确认 headers 只含 Bearer + key,不额外打印
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    oc = OpenRouterClient(api_key="secret123")
    h = oc._headers()
    assert h["Authorization"] == "Bearer secret123"
    assert "secret123" not in h.get("Content-Type", "")