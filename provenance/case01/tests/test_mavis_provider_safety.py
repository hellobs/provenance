from pydantic import BaseModel

from case01.agents.mavis_provider import Case01SafeProvider


class IntResponse(BaseModel):
    res: int


def _provider():
    return Case01SafeProvider({
        "provider": "openai",
        "model": "local-model",
        "base_url": "http://127.0.0.1:8101/v1",
        "structured_attempts": 3,
        "retry_delay": 0,
    })


def test_invalid_integer_output_retries_then_uses_failsafe(monkeypatch):
    provider = _provider()
    calls = []

    def fake_chat(messages, temperature, response_format, max_tokens):
        calls.append(max_tokens)
        return "not-json"

    monkeypatch.setattr(provider, "_chat", fake_chat)
    assert provider.completion(
        "wake up", return_type=IntResponse, failsafe=8, caller="wake_up") == 8
    assert calls == [256, 256, 256]


def test_numeric_string_is_coerced(monkeypatch):
    provider = _provider()
    monkeypatch.setattr(
        provider, "_chat",
        lambda messages, temperature, response_format, max_tokens: '"8"')
    assert provider.completion(
        "wake up", return_type=IntResponse, failsafe=7, caller="wake_up") == 8


def test_long_calls_are_bounded(monkeypatch):
    provider = _provider()
    seen = []

    def fake_chat(messages, temperature, response_format, max_tokens):
        seen.append(max_tokens)
        return '{"res": 8}'

    monkeypatch.setattr(provider, "_chat", fake_chat)
    provider.completion(
        "schedule", return_type=IntResponse, failsafe=8, caller="schedule_daily")
    assert seen == [2048]
