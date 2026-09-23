"""Case-local safe LLM provider for Mavis agents.

This keeps output validation and generation limits inside provenance instead of
requiring changes to the upstream Mavis package. Both Ollama and vLLM use an
OpenAI-compatible chat-completions endpoint here.
"""
import json
import re
import threading
import time

import requests

# mavis 的 `Agent.completion` **不传 caller**(见 mavisframework/core/agent_core.py:
# `res = func(...)._asdict()` 只有 prompt/callback/failsafe/return_type 四个键),
# 所以走到这里 caller 恒为默认的 "llm_normal" —— 分档必须按 `return_type` 判,
# 否则每次调用都掉进最小档。
# 2026-09-23 实测的后果:镇内**所有**调用都被钉在 256 token,而 `auto-1820` 里
# Investment AI 的 T0 回答实测 536 汉字(≈320-380 token)—— 判定者读的正是这段
# 被截断的文字,于是 judge 判不出来、run 停在 T0。原来的 2048/1024 两档永远走不到。
_LONG_OUTPUT_TYPES = frozenset([
    "generate_chat",             # 角色对话正文(1-3 句)
    "schedule_initResponse",     # 一天的活动列表
    "schedule_dailyResponse",    # 24 小时日程表
    "schedule_decomposeResponse",
    "schedule_reviseResponse",
    "reflect_focusResponse",
    "reflect_insightsResponse",  # 洞察列表
    "describe_eventResponse",    # 动作三元组列表
])


def _res_annotation(return_type):
    """取 `res` 字段的类型标注:整数/布尔类回答(打分、起床点、是非)给最小档就够。"""
    field = getattr(return_type, "model_fields", {}).get("res")
    return getattr(field, "annotation", None)


class Case01SafeProvider:
    _semaphore = threading.Semaphore(4)

    def __init__(self, config: dict):
        self.config = dict(config)
        self.base_url = self.config["base_url"].rstrip("/")
        self.model = self.config["model"]
        self.api_key = self.config.get("api_key", "")
        self.timeout = float(self.config.get("request_timeout", 90) or 90)
        self.max_attempts = max(1, int(self.config.get("structured_attempts", 3) or 3))
        self.retry_delay = max(0.0, float(self.config.get("retry_delay", 1) or 0))
        self.enabled = True
        self.summary = {"total": [0, 0, 0]}
        # 截断计数(finish_reason == "length"):截断的输出与完整输出长得一模一样,
        # 记进 summary 才会出现在 mavis 的角色日志/state 里,而不是只有天知道。
        self.truncations = 0
        self.last_truncation = None

    def completion(self, prompt, retry=10, callback=None, failsafe=None,
                   return_type=None, caller="llm_normal", **kwargs):
        attempts = max(1, int(retry or 1))
        if return_type is not None:
            attempts = min(attempts, self.max_attempts)
        self.summary.setdefault(caller, [0, 0, 0])
        result = None
        for attempt in range(1, attempts + 1):
            try:
                with self._semaphore:
                    output = self._complete(
                        prompt, return_type, caller=caller, **kwargs)
                self.summary["total"][0] += 1
                self.summary[caller][0] += 1
                result = callback(output) if callback else output
                if result is not None:
                    break
            except Exception as exc:
                print(
                    "[case01.llm] caller={} attempt={}/{} error={}".format(
                        caller, attempt, attempts, exc),
                    flush=True,
                )
                result = None
            if attempt < attempts and self.retry_delay:
                time.sleep(self.retry_delay)

        index = 2 if result is None else 1
        self.summary["total"][index] += 1
        self.summary[caller][index] += 1
        return failsafe if result is None else result

    def _complete(self, prompt, return_type, temperature=0.5,
                  caller="llm_normal", max_tokens=None, **_kwargs):
        response_format = None
        if return_type is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": return_type.__name__,
                    "strict": True,
                    "schema": return_type.model_json_schema(),
                },
            }
        content = self._chat(
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            response_format=response_format,
            max_tokens=int(max_tokens or self._token_limit(caller, return_type)),
            caller=caller,
        )
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        return self._parse(content, return_type) if return_type is not None else content

    def _chat(self, messages, temperature, response_format, max_tokens,
              caller="llm_normal"):
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if response_format:
            body["response_format"] = response_format
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        response = requests.post(
            self.base_url + "/chat/completions",
            headers=headers,
            json=body,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            self.truncations += 1
            self.last_truncation = {"caller": caller, "max_tokens": int(max_tokens)}
            print("[case01.llm] 输出被 max_tokens 截断: caller={} max_tokens={} "
                  "累计={} 次".format(caller, max_tokens, self.truncations), flush=True)
        return choice["message"]["content"]

    def _token_limit(self, caller, return_type):
        """这一次调用的 max_tokens。

        优先级:配置(`think.llm.max_tokens`,整数或 {"structured"/"conversation"/"long"})
        → 调用方显式传的 caller(只有外部调用方会传)→ `return_type`(mavis 实际
        传得出来的唯一信号)。原实现只按 caller 分档,而 caller 恒为默认值,
        于是 int 打分和整段对话正文拿的是同一个 256。
        """
        configured = self.config.get("max_tokens", {})
        if isinstance(configured, int):
            return configured
        limits = {"structured": 256, "conversation": 1024, "long": 2048, "default": 1024}
        if isinstance(configured, dict):
            limits.update({k: int(v) for k, v in configured.items() if v is not None})
        if caller and caller != "llm_normal":
            if caller.startswith("reflect_") or caller in {
                    "schedule_daily", "schedule_revise", "schedule_decompose"}:
                return limits["long"]
            if caller in {"generate_chat", "summarize_chats", "summarize_relation"}:
                return limits["conversation"]
        if return_type is None:
            return limits["default"]
        if getattr(return_type, "__name__", "") in _LONG_OUTPUT_TYPES:
            return limits["long"]
        if _res_annotation(return_type) in (int, bool):
            return limits["structured"]
        return limits["conversation"]

    @classmethod
    def _parse(cls, text, return_type):
        candidates = []
        try:
            candidates.append(json.loads(text))
        except (TypeError, json.JSONDecodeError):
            pass
        candidates.extend(cls._json_objects(text))
        for value in candidates:
            try:
                payload = value if isinstance(value, dict) else {"res": value}
                return return_type.model_validate(payload).res
            except Exception:
                continue
        field = getattr(return_type, "model_fields", {}).get("res")
        if field is not None and field.annotation is str and text:
            return return_type.model_validate({"res": text}).res
        raise ValueError(
            "invalid structured output for {}: {!r}".format(
                return_type.__name__, (text or "")[:500]))

    @staticmethod
    def _json_objects(text):
        objects = []
        decoder = json.JSONDecoder()
        index = 0
        while True:
            start = text.find("{", index)
            if start < 0:
                return objects
            try:
                value, length = decoder.raw_decode(text[start:])
                objects.append(value)
                index = start + max(length, 1)
            except json.JSONDecodeError:
                index = start + 1

    def is_available(self):
        return self.enabled

    def get_summary(self):
        values = {
            key: "S:{},F:{}/R:{}".format(value[1], value[2], value[0])
            for key, value in self.summary.items()
        }
        out = {"model": self.model, "summary": values}
        if self.truncations:  # 只在真发生时出现,平时不占位
            out["truncated"] = self.truncations
            out["last_truncation"] = dict(self.last_truncation or {})
        return out

    def disable(self):
        self.enabled = False
