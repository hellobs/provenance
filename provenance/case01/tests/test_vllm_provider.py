from case01.agents.llm import OllamaClient, VLLMClient, local_client_from_env
from case01.injector.bridge import MavisBridge


def test_local_client_defaults_to_ollama(monkeypatch):
    monkeypatch.delenv("CASE01_LLM_PROVIDER", raising=False)
    assert isinstance(local_client_from_env(), OllamaClient)


def test_local_client_selects_vllm(monkeypatch):
    monkeypatch.setenv("CASE01_LLM_PROVIDER", "vllm")
    monkeypatch.setenv("CASE01_LLM_BASE_URL", "http://127.0.0.1:8101/v1")
    monkeypatch.setenv("CASE01_LLM_MODEL", "chat-local")
    monkeypatch.setenv("CASE01_EMBED_BASE_URL", "http://127.0.0.1:8102/v1")
    monkeypatch.setenv("CASE01_EMBED_MODEL", "embed-local")
    client = local_client_from_env()
    assert isinstance(client, VLLMClient)
    assert client._chat_url() == "http://127.0.0.1:8101/v1/chat/completions"
    assert client.embed_base_url == "http://127.0.0.1:8102/v1"


def test_bridge_injects_vllm_into_mavis_config(monkeypatch):
    monkeypatch.setenv("CASE01_LLM_PROVIDER", "vllm")
    monkeypatch.setenv("CASE01_LLM_BASE_URL", "http://127.0.0.1:8101/v1")
    monkeypatch.setenv("CASE01_LLM_MODEL", "chat-local")
    bridge = object.__new__(MavisBridge)
    config = {"agent_base": {"think": {"llm": {"provider": "ollama"}}}}
    bridge._apply_local_provider(config)
    assert config["agent_base"]["think"]["llm"] == {
        "provider": "vllm", "base_url": "http://127.0.0.1:8101/v1",
        "model": "chat-local", "api_key": "",
    }
