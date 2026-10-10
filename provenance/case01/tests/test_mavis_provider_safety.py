"""Case01SafeProvider:输出校验、生成上限、截断计数、种子上线(都不联网)。

2026-09-23 补的两条是**回归**:原来的分档只看 `caller`,而 mavis 的
`Agent.completion` 从不传 caller(`Result` 只有 prompt/callback/failsafe/return_type
四个键)→ 镇内每次调用都拿最小档 token,而 AI 的 T0 回答实测 536 汉字。
2026-10-10:各档整体上调(structured 256→4096 / conversation 2048→8192)—— 256 那档实测把结构化输出连续截断 3 次(poignancy_event 解析到
空串 → 落兜底值),证据见当次运行日志"输出被 max_tokens 截断 … max_tokens=256"。
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
    assert calls == [4096, 4096, 4096]


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
    assert seen == [8192]


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
    assert seen == [("llm_normal", 8192), ("llm_normal", 8192)], seen


def test_limits_are_keyed_on_the_return_type():
    """分档看 `return_type` —— 它才是 mavis 真正传得出来的信号。"""
    provider = _provider()
    assert provider._token_limit("llm_normal", generate_chat) == 8192
    assert provider._token_limit("llm_normal", schedule_dailyResponse) == 8192
    assert provider._token_limit("llm_normal", TextResponse) == 8192
    assert provider._token_limit("llm_normal", IntResponse) == 4096


def test_small_answers_stay_small(monkeypatch):
    """int/bool 类回答(打分、起床点、是非)仍然给最小档,不浪费。"""
    provider = _provider()
    seen = []
    monkeypatch.setattr(
        provider, "_chat",
        lambda messages, temperature, response_format, max_tokens, caller="llm_normal":
        seen.append(max_tokens) or '{"res": 3}')
    provider.completion("打分", return_type=IntResponse, failsafe=1)
    assert seen == [4096]


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
    assert provider.last_truncation == {"caller": "llm_normal", "max_tokens": 8192}
    out = capfd.readouterr().out
    assert "truncated" in out and "max_tokens=8192" in out
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
    assert sent["max_tokens"] == 4096          # structured 档(原 256 会截断结构化输出)
    assert json.loads(json.dumps(sent))  # 可序列化(没有被塞进奇怪对象)


def test_json_schema_rejected_by_endpoint_degrades_and_is_remembered(monkeypatch):
    """端点不支持 json_schema(DeepSeek: "This response_format type is unavailable now")
    ⇒ 400 后**出声**降级重试一次,并记住"这端点不支持",后续调用不再发该字段。"""
    sent = []

    class _Resp:
        def __init__(self, code, payload):
            self.status_code = code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("HTTP %s" % self.status_code)

    def fake_post(url, headers=None, json=None, timeout=None):
        import copy
        sent.append(copy.deepcopy(json))   # 快照:实现会原地改 body
        if json.get("response_format", {}).get("type") == "json_schema":
            return _Resp(400, {"error": {"message": "This response_format type is unavailable now"}})
        return _Resp(200, {"choices": [{"message": {"content": "{\"res\": 1}"},
                                        "finish_reason": "stop"}]})

    provider = _provider(base_url="https://api.example.com/v1",
                       model="m", api_key="k")
    import case01.agents.mavis_provider as _mp
    monkeypatch.setattr(_mp.requests, "post", fake_post)
    out = provider.completion("hi", return_type=IntResponse, retry=1)
    assert out == 1
    assert sent[0]["response_format"]["type"] == "json_schema"
    assert "response_format" not in sent[1], sent[1]   # 降级=去掉该字段
    assert provider.response_format_downgrades == 1
    assert sent[1]["max_tokens"] == 4096   # structured 档已从 256 抬到 1024
    # 能力记忆:第一次撞过 400 之后,后续调用**不再发** response_format(别每次白费两次请求)
    sent.clear()
    provider.completion("hi", return_type=IntResponse, retry=1)
    assert "response_format" not in sent[0], sent[0]


# --------------------------------------------------------------- 种子必须真的上线
# 为什么单独钉这三条:2026-10-06 之前 `--seed` 只钉住了判定/反思/路由,镇里 agent
# 的每次发言**根本没带 seed**,而这件事在日志与产物里都看不出来(请求体不落盘)。
# 起面要 5010(本会话不起),所以这里用假 post 把"发出去的字节"钉住 —— 这是
# 不烧 GPU 能拿到的最强证据。

def test_env_seed_reaches_the_request_body(monkeypatch):
    monkeypatch.setenv("CASE01_LLM_SEED", "20261015")
    provider = _provider()
    calls = _patch_post(monkeypatch, "stop", content='{"res": "嗯"}')
    provider.completion("说点什么", return_type=TextResponse, failsafe="嗯")
    assert calls[0].get("seed") == 20261015, \
        "种子没上线 ⇒ 镇里台词仍是非确定的:{}".format(calls[0])
    assert isinstance(calls[0]["seed"], int), "必须是整数,Ollama 才认"


def test_现读env的种子要声明回实例并进清单(monkeypatch):
    """请求体带 seed 还不够 —— `manifest.seed` 也得记上(2026-10-06 小镇实测 null)。

    清单只认客户端自报(`manifest.detect_seed` 刻意不读 env,免得造出"清单说固定了、
    客户端其实没固定"的假证据),而小镇的 provider 是"每次调用现读 env"的 ⇒ 发出去
    那一刻必须把值声明回实例,再由桥把 agent 上的 provider 一并交给 `collect_run_meta`
    (只看判定那个客户端时,起面路径的三项 seed/judge_model/temperature 全落默认值)。
    """
    from mavis_case01_injector.manifest import collect_run_meta

    monkeypatch.setenv("CASE01_LLM_SEED", "20261015")
    provider = _provider()
    assert provider.seed is None, "构造时配置里没给种子"
    calls = _patch_post(monkeypatch, "stop", content='{"res": "嗯"}')
    provider.completion("说点什么", return_type=TextResponse, failsafe="嗯")
    assert provider.seed == 20261015, "发出去的种子必须声明回实例"
    meta = collect_run_meta({}, judge_llm=None, llms={"小镇 Ethan Lin": provider})
    assert meta["seed"] == 20261015, meta
    # 没播种时仍然是 null(不许把"取不到"写成"固定过")
    monkeypatch.delenv("CASE01_LLM_SEED", raising=False)
    bare = _provider()
    _patch_post(monkeypatch, "stop", content='{"res": "嗯"}')
    bare.completion("说点什么", return_type=TextResponse, failsafe="嗯")
    meta2 = collect_run_meta({}, judge_llm=None, llms={"小镇 Ethan Lin": bare})
    assert meta2["seed"] is None, meta2


def test_没有种子时请求体里不许出现_seed_键(monkeypatch):
    monkeypatch.delenv("CASE01_LLM_SEED", raising=False)
    provider = _provider()
    calls = _patch_post(monkeypatch, "stop", content='{"res": "嗯"}')
    provider.completion("说点什么", return_type=TextResponse, failsafe="嗯")
    assert "seed" not in calls[0], calls[0]


def test_显式配置的种子压过环境变量(monkeypatch):
    monkeypatch.setenv("CASE01_LLM_SEED", "999")
    provider = _provider(seed=7)
    calls = _patch_post(monkeypatch, "stop", content='{"res": "嗯"}')
    provider.completion("说点什么", return_type=TextResponse, failsafe="嗯")
    assert provider.seed == 7 and calls[0]["seed"] == 7, calls[0]
    # 非法值不静默回落到 env 之外的第三种状态:配置坏 ⇒ 退回读 env(仍要有种子)
    # 注:实例的 `.seed` 语义是"这一次真发出去的值"(清单的 seed 就取它,见
    # manifest.detect_seed 只认客户端自报),所以 env 兜底之后它不再是 None
    # —— 2026-10-06 起;此前"请求体带 seed 而实例记 None",小镇记录落的是
    # `manifest.seed: null`。
    bad = _provider(seed="abc")
    monkeypatch.setenv("CASE01_LLM_SEED", "999")
    calls2 = _patch_post(monkeypatch, "stop", content='{"res": "嗯"}')
    bad.completion("说点什么", return_type=TextResponse, failsafe="嗯")
    assert calls2[0]["seed"] == 999 and bad.seed == 999, calls2[0]
