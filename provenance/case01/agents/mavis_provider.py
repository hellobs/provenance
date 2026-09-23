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
        )
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        return self._parse(content, return_type) if return_type is not None else content

    def _chat(self, messages, temperature, response_format, max_tokens):
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
        return data["choices"][0]["message"]["content"]

    def _token_limit(self, caller, return_type):
        configured = self.config.get("max_tokens", {})
        if isinstance(configured, int):
            return configured
        limits = {"structured": 256, "conversation": 1024,
                  "long": 2048, "default": 1024}
        if isinstance(configured, dict):
            limits.update({k: int(v) for k, v in configured.items() if v is not None})
        if caller.startswith("reflect_") or caller in {
                "schedule_daily", "schedule_revise", "schedule_decompose"}:
            return limits["long"]
        if caller in {"generate_chat", "summarize_chats", "summarize_relation"}:
            return limits["conversation"]
        return limits["structured"] if return_type is not None else limits["default"]

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
        return {"model": self.model, "summary": values}

    def disable(self):
        self.enabled = False
