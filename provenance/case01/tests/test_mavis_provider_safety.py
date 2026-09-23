"""Case01SafeProvider:输出校验、生成上限、截断计数(不联网)。

2026-09-23 补的两条是**回归**:原来的分档只看 `caller`,而 mavis 的
`Agent.completion` 从不传 caller(`Result` 只有 prompt/callback/failsafe/return_type
四个键)→ 镇内每次调用都拿最小档 256 token,而 AI 的 T0 回答实测 536 汉字。
原来的 `test_long_calls_are_bounded` 是**自己手工传了 caller** 才通过的,
所以它绿着,线上照样截断 —— 这里改成按 mavis 的真实调用形状断言。
"""
import json

import pytest
from pydantic import BaseModel

from case01.agents.mavis_provider import Case01SafeProvider


class IntResponse(BaseModel):
    res: int


class TextResponse(BaseModel):
    res: str


class generate_chat(BaseModel):
    """名字必须与 mavis 里的一致:分档按 `return_type.__name__` 判。"""
    res: str


class schedule_dailyResponse(BaseModel):
    res: dict


def _provider(**extra):
    config = {
        "provider": "openai",
        "model": "local-model",
        "base_url": "http://127.0.0.1:8101/v1",
        "structured_attempts": 3,
        "retry_delay": 0,
    }
    config.update(extra)
    return Case01SafeProvider(config)


def test_invalid_integer_output_retries_then_uses_failsafe(monkeypatch):
    provider = _provider()
    calls = []

    def fake_chat(messages, temperature, response_format, max_tokens, caller="llm_normal"):
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
        lambda messages, temperature, response_format, max_tokens, caller="llm_normal": '"8"')
    assert provider.completion(
        "wake up", return_type=IntResponse, failsafe=7, caller="wake_up") == 8


def test_long_calls_are_bounded(monkeypatch):
    """外部显式传 caller 时,原来的分档仍然有效(向后兼容)。"""
    provider = _provider()
    seen = []

    def fake_chat(messages, temperature, response_format, max_tokens, caller="llm_normal"):
        seen.append(max_tokens)
        return '{"res": 8}'

    monkeypatch.setattr(provider, "_chat", fake_chat)
    provider.completion(
        "schedule", return_type=IntResponse, failsafe=8, caller="schedule_daily")
    assert seen == [2048]


# --------------------------------------------------------------- 回归:mavis 调用形状

def test_mavis_call_shape_gets_a_usable_limit(monkeypatch):
    """mavis 不传 caller:对话正文不许被钉在 256(实测 T0 回答 536 汉字)。"""
    provider = _provider()
    seen = []

    def fake_chat(messages, temperature, response_format, max_tokens, caller="llm_normal"):
        seen.append((caller, max_tokens))
        return '{"res": "嗯"}'

    monkeypatch.setattr(provider, "_chat", fake_chat)
    provider.completion("说点什么", return_type=generate_chat, failsafe="嗯")
    provider.completion("随便聊", return_type=TextResponse, failsafe="嗯")
    assert seen == [("llm_normal", 2048), ("llm_normal", 1024)], seen


def test_limits_are_keyed_on_the_return_type():
    """分档看 `return_type` —— 它才是 mavis 真正传得出来的信号。"""
    provider = _provider()
    assert provider._token_limit("llm_normal", generate_chat) == 2048
    assert provider._token_limit("llm_normal", schedule_dailyResponse) == 2048
    assert provider._token_limit("llm_normal", TextResponse) == 1024
    assert provider._token_limit("llm_normal", IntResponse) == 256


def test_small_answers_stay_small(monkeypatch):
    """int/bool 类回答(打分、起床点、是非)仍然给最小档,不浪费。"""
    provider = _provider()
    seen = []
    monkeypatch.setattr(
        provider, "_chat",
        lambda messages, temperature, response_format, max_tokens, caller="llm_normal":
        seen.append(max_tokens) or '{"res": 3}')
    provider.completion("打分", return_type=IntResponse, failsafe=1)
    assert seen == [256]


def test_config_override_wins(monkeypatch):
    """场景 `think.llm.max_tokens` 能直接压住分档(整数或分档字典)。"""
    provider = _provider(max_tokens=4096)
    seen = []
    monkeypatch.setattr(
        provider, "_chat",
        lambda messages, temperature, response_format, max_tokens, caller="llm_normal":
        seen.append(max_tokens) or '{"res": 1}')
    provider.completion("打分", return_type=IntResponse, failsafe=0)
    assert seen == [4096], "整数配置必须直接生效"
    dict_provider = _provider(max_tokens={"conversation": 3072})
    assert dict_provider._token_limit("llm_normal", TextResponse) == 3072


# --------------------------------------------------------------- 截断必须出声

class _Resp:
    def __init__(self, content, finish_reason):
        self._content = content
        self._finish_reason = finish_reason
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content},
                             "finish_reason": self._finish_reason}]}


def _patch_post(monkeypatch, finish_reason, content='{"res": 1}'):
    import case01.agents.mavis_provider as mp
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return _Resp(content, finish_reason)

    monkeypatch.setattr(mp.requests, "post", fake_post)
    return calls


def test_truncation_is_counted_and_loud(monkeypatch, capfd):
    """finish_reason=length 必须留下痕迹:计数 + last_truncation + 一行日志。"""
    provider = _provider()
    _patch_post(monkeypatch, "length", content='{"res": "半句话')
    provider.completion("说点什么", return_type=TextResponse, failsafe="嗯")
    assert provider.truncations == 1
    assert provider.last_truncation == {"caller": "llm_normal", "max_tokens": 1024}
    out = capfd.readouterr().out
    assert "截断" in out and "max_tokens=1024" in out
    assert provider.get_summary()["truncated"] == 1


def test_no_truncation_means_no_noise(monkeypatch, capfd):
    provider = _provider()
    _patch_post(monkeypatch, "stop", content='{"res": "完整一句话。"}')
    provider.completion("说点什么", return_type=TextResponse, failsafe="嗯")
    assert provider.truncations == 0
    assert "truncated" not in provider.get_summary()
    assert "截断" not in capfd.readouterr().out


def test_structured_output_uses_json_schema(monkeypatch):
    """结构化调用要带 response_format(json_schema, strict),不是靠模型自觉。"""
    provider = _provider()
    calls = _patch_post(monkeypatch, "stop", content='{"res": 5}')
    provider.completion("打分", return_type=IntResponse, failsafe=1)
    sent = calls[0]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["max_tokens"] == 256
    assert json.loads(json.dumps(sent))  # 可序列化(没有被塞进奇怪对象)
