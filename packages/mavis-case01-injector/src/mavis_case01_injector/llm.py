# -*- coding: utf-8 -*-
"""轻量 LLM client。
- OllamaClient:本地 Ollama(OpenAI 兼容 /v1/chat/completions + /api/embed)
- LocalHFClient:直接加载本地 HuggingFace 权重(chat + embedding)
- OpenRouterClient:外部 API(OpenAI 兼容;key 从 secrets/环境变量读取,
  不落盘、不打印)——供 Ethan/Router 等非 Investment AI 角色使用
  (0904 规定 Investment AI 必须本地 Ollama 运行)。

chat(): 文本补全,支持 temperature/max_tokens;重试 + 超时(指数退避)。
"""
import json
import os
import time
import urllib.request
import urllib.error
from typing import List, Optional


def _openrouter_key() -> str:
    """key 解析:环境变量优先;缺省时经过渡 seam 读 case01/.secrets.json(抽包收尾前)。"""
    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if k:
        return k
    try:
        from mavis_case01_injector._providers import import_case01_agents_secrets
        return import_case01_agents_secrets().openrouter_key()
    except ImportError:
        return ""



class _ChatMixin:
    """共享的 OpenAI 兼容 chat 实现(base_url/model/headers 由子类提供)"""

    timeout = 120.0
    retries = 3
    # 截断计数:OpenAI 兼容端点用 `choices[0].finish_reason == "length"` 表态输出被
    # max_tokens 截断。**必须数出来** —— 截断的答案看起来和完整答案一模一样
    # (尤其结构化调用的 res:str 兜底会照收),不数就没人知道。
    truncations = 0

    def _note_truncation(self, finish_reason, max_tokens: int) -> bool:
        """截断出声:谁、上限多少、累计几次。返回是否截断。"""
        if finish_reason != "length":
            return False
        self.truncations += 1
        print("[case01.llm] 输出被 max_tokens 截断: {} max_tokens={} 累计={} 次".format(
            type(self).__name__, max_tokens, self.truncations), flush=True)
        return True

    def chat(self, messages: List[dict], temperature: float = 0.7,
             max_tokens: int = 1024, num_ctx: Optional[int] = None,
             extra_body: Optional[dict] = None) -> Optional[str]:
        url = self._chat_url()
        body = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        # provider 私有参数透传(2026-09-27):如 BigModel 的
        # {"thinking": {"type": "disabled"}}(关推理,判定类任务省 token 提速)。
        # 客户端级默认(self.default_extra_body)+ 调用级覆盖,后者优先。
        merged = {}
        merged.update(getattr(self, "default_extra_body", None) or {})
        merged.update(extra_body or {})
        if merged:
            body.update(merged)
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
                choice = obj["choices"][0]
                self._note_truncation(choice.get("finish_reason"), max_tokens)
                return choice["message"]["content"]
            except (urllib.error.URLError, KeyError, json.JSONDecodeError,
                    TimeoutError) as e:
                # TimeoutError 必须在这里(2026-10-03):urlopen 超时抛的是
                # socket.timeout(=TimeoutError),它**不是** URLError 的子类,
                # 原先漏掉 → 8b 单次生成超 120s 时不重试、直接冒泡把整条 run 打死
                # (批次 261003-165042 的 014 就是这样,run.json 全丢)。
                last_err = e
                if attempt + 1 < self.retries:   # 末轮不白睡
                    time.sleep(2 * (attempt + 1))
        raise RuntimeError("{} chat failed after {} retries: {}".format(
            type(self).__name__, self.retries, last_err))


class OllamaClient(_ChatMixin):
    def __init__(self, base_url: str = "http://127.0.0.1:11434",
                 chat_model: str = "qwen3:4b-instruct-2507-q4_K_M",
                 embed_model: str = "qwen3-embedding:0.6b-q8_0",
                 timeout: float = 120.0, retries: int = 3,
                 num_ctx: int = 32768, think: Optional[bool] = None):
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.timeout = timeout
        self.retries = retries
        self.num_ctx = num_ctx
        self.think = think

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
            except (urllib.error.URLError, KeyError, json.JSONDecodeError,
                    TimeoutError) as e:
                last_err = e
                if attempt + 1 < self.retries:   # 末轮不白睡
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
                    timeout: float = 600.0,
                    think: Optional[bool] = None) -> Optional[str]:
        """Ollama 原生 /api/chat:支持 num_ctx(长 prompt 必需)。

        OpenAI 兼容 /v1/chat/completions 不接受 options/num_ctx,
        长 prompt(如 Reflection 材料 12k+ 字符)会因默认上下文小返回 400。
        timeout 默认 600s:8 维长反思在 4b 模型上单次生成可能 >120s。

        `think`:qwen3 的 hybrid 思考模型(如 8b)默认开推理,thinking 文本与正文
        **共享 `num_predict` 预算** —— 推理一长,正文就被截到只剩几十字(实测
        3072 预算下正文 57 字 / 思考 796 字,关思考后正文 104 字)。结构化任务
        (反思/路由)不需要推理链,关掉既省 token 又避免截断。
        None=沿用构造时的 `self.think`(再缺省则模型默认)。
        """
        url = self.base_url + "/api/chat"
        body = {
            "model": self.chat_model,
            "messages": messages,
            "options": {"num_ctx": num_ctx, "temperature": temperature,
                        "num_predict": max_tokens},
            "stream": False,
        }
        if think is None:
            think = getattr(self, "think", None)
        if think is not None:
            body["think"] = think
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
            except (urllib.error.URLError, KeyError, json.JSONDecodeError,
                    TimeoutError) as e:
                last_err = e
                if attempt + 1 < self.retries:   # 末轮不白睡
                    time.sleep(2 * (attempt + 1))
        raise RuntimeError("Ollama native_chat failed after {} retries: {}".format(
            self.retries, last_err))


class VLLMClient(_ChatMixin):
    """OpenAI-compatible vLLM client with the same API as OllamaClient."""

    def __init__(self, base_url: str, chat_model: str,
                 embed_base_url: str = "", embed_model: str = "",
                 api_key: str = "", timeout: float = 120.0, retries: int = 3,
                 extra_body: Optional[dict] = None):
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embed_base_url = (embed_base_url or base_url).rstrip("/")
        self.embed_model = embed_model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        # Qwen3 等混合思考模型可在客户端级固定 chat-template 参数。
        # 判定/路由这类结构化任务通常应关闭 thinking，避免 reasoning 挤占
        # max_tokens 后 content 为空；调用级 extra_body 仍可覆盖这里。
        self.default_extra_body = dict(extra_body or {})

    def _chat_url(self) -> str:
        return self.base_url + "/chat/completions"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def native_chat(self, messages: List[dict], temperature: float = 0.4,
                    max_tokens: int = 2048, num_ctx: int = 32768,
                    timeout: float = 120.0) -> Optional[str]:
        # vLLM context length is controlled by server startup options.
        old_timeout = self.timeout
        self.timeout = timeout
        try:
            return self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        finally:
            self.timeout = old_timeout

    def embed(self, text: str) -> Optional[List[float]]:
        if not self.embed_model:
            return None
        body = {"model": self.embed_model, "input": text}
        data = json.dumps(body).encode("utf-8")
        last_err = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(
                    self.embed_base_url + "/embeddings", data=data,
                    headers=self._headers())
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    obj = json.loads(resp.read().decode("utf-8"))
                return obj["data"][0]["embedding"]
            except (urllib.error.URLError, KeyError, IndexError,
                    json.JSONDecodeError, TimeoutError) as exc:
                last_err = exc
                if attempt + 1 < self.retries:   # 末轮不白睡
                    time.sleep(2 * (attempt + 1))
        raise RuntimeError("VLLM embed failed after {} retries: {}".format(
            self.retries, last_err))

    def is_available(self) -> bool:
        try:
            req = urllib.request.Request(self.base_url + "/models", headers=self._headers())
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False


def local_client_from_env(retries: int = 3):
    """Select a local backend from environment variables; default to Ollama."""
    provider = os.environ.get("CASE01_LLM_PROVIDER", "ollama").strip().lower()
    chat_model = os.environ.get(
        "CASE01_LLM_MODEL", "qwen3:4b-instruct-2507-q4_K_M").strip()
    embed_model = os.environ.get(
        "CASE01_EMBED_MODEL", "qwen3-embedding:0.6b-q8_0").strip()
    if provider == "ollama":
        base_url = os.environ.get(
            "CASE01_LLM_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        # hybrid 思考模型(qwen3:8b)默认开推理,思考与正文共享 max_tokens 预算
        # → 正文被截到几十字(见 native_chat 的 think 参数说明)。显式可关。
        if os.environ.get("CASE01_LLM_DISABLE_THINKING", "").strip().lower() in (
                "1", "true", "yes", "on"):
            os.environ.setdefault("CASE01_OLLAMA_THINK", "false")
        think = os.environ.get("CASE01_OLLAMA_THINK", "").strip().lower()
        return OllamaClient(
            base_url=base_url,
            chat_model=chat_model, embed_model=embed_model, retries=retries,
            think=False if think in ("0", "false", "no", "off") else None)
    if provider == "vllm":
        base_url = os.environ.get("CASE01_LLM_BASE_URL", "").strip()
        if not base_url:
            raise ValueError("vLLM requires CASE01_LLM_BASE_URL including /v1")
        disable_thinking = os.environ.get(
            "CASE01_LLM_DISABLE_THINKING", "").strip().lower() in (
                "1", "true", "yes", "on")
        return VLLMClient(
            base_url=base_url, chat_model=chat_model,
            embed_base_url=os.environ.get("CASE01_EMBED_BASE_URL", "").strip(),
            embed_model=embed_model,
            api_key=os.environ.get("CASE01_LLM_API_KEY", "").strip(), retries=retries,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}}
            if disable_thinking else None)
    raise ValueError("CASE01_LLM_PROVIDER must be ollama or vllm")


class LocalHFClient:
    """本地 HuggingFace 权重 client。

    用于没有 Ollama/vLLM 服务、但已有 safetensors 权重目录的环境。接口与
    OllamaClient 对齐:chat()/native_chat()/embed()。
    """

    def __init__(self, chat_model_path: str,
                 embed_model_path: str = "",
                 device: str = "",
                 embed_device: str = "",
                 max_input_tokens: int = 32768,
                 embed_max_length: int = 8192):
        self.chat_model_path = chat_model_path
        self.embed_model_path = embed_model_path
        self.device = device
        self.embed_device = embed_device or device
        self.max_input_tokens = max_input_tokens
        self.embed_max_length = embed_max_length
        self.chat_model = None
        self.chat_tokenizer = None
        self.embed_model = None
        self.embed_tokenizer = None

    # ------------------------------------------------------------------
    def _deps(self):
        try:
            import torch
            import torch.nn.functional as F
            from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer
        except ImportError as e:
            raise RuntimeError(
                "LocalHFClient 需要 torch/transformers/safetensors;当前环境缺少: {}"
                .format(e.name or e))
        return torch, F, AutoModel, AutoModelForCausalLM, AutoTokenizer

    def _pick_device(self, requested: str = "") -> str:
        torch, _F, _AutoModel, _AutoModelForCausalLM, _AutoTokenizer = self._deps()
        if requested:
            return requested
        return "cuda" if torch.cuda.is_available() else "cpu"

    def _ensure_chat(self):
        if self.chat_model is not None:
            return
        if not self.chat_model_path:
            raise RuntimeError("未配置本地 chat 模型路径")
        torch, _F, _AutoModel, AutoModelForCausalLM, AutoTokenizer = self._deps()
        dev = self._pick_device(self.device)
        self.chat_tokenizer = AutoTokenizer.from_pretrained(
            self.chat_model_path, local_files_only=True)
        self.chat_model = AutoModelForCausalLM.from_pretrained(
            self.chat_model_path, torch_dtype="auto", local_files_only=True)
        self.chat_model.to(dev)
        self.chat_model.eval()
        self.device = dev

    def _ensure_embed(self):
        if self.embed_model is not None:
            return
        if not self.embed_model_path:
            raise RuntimeError("未配置本地 embedding 模型路径")
        torch, _F, AutoModel, _AutoModelForCausalLM, AutoTokenizer = self._deps()
        dev = self._pick_device(self.embed_device)
        self.embed_tokenizer = AutoTokenizer.from_pretrained(
            self.embed_model_path, padding_side="left", local_files_only=True)
        self.embed_model = AutoModel.from_pretrained(
            self.embed_model_path, torch_dtype="auto", local_files_only=True)
        self.embed_model.to(dev)
        self.embed_model.eval()
        self.embed_device = dev

    # ------------------------------------------------------------------
    def chat(self, messages: List[dict], temperature: float = 0.7,
             max_tokens: int = 1024, num_ctx: Optional[int] = None) -> Optional[str]:
        self._ensure_chat()
        torch, _F, _AutoModel, _AutoModelForCausalLM, _AutoTokenizer = self._deps()
        tokenizer = self.chat_tokenizer
        model = self.chat_model
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        limit = num_ctx or self.max_input_tokens
        inputs = tokenizer(
            [prompt],
            return_tensors="pt",
            truncation=True,
            max_length=limit,
        ).to(model.device)
        gen_kwargs = {
            "max_new_tokens": max_tokens,
            "pad_token_id": tokenizer.pad_token_id or tokenizer.eos_token_id,
        }
        if temperature and temperature > 0:
            gen_kwargs.update({
                "do_sample": True,
                "temperature": temperature,
                "top_p": 0.8,
                "top_k": 20,
            })
        else:
            gen_kwargs["do_sample"] = False
        with torch.inference_mode():
            out = model.generate(**inputs, **gen_kwargs)
        new_tokens = out[0][inputs.input_ids.shape[-1]:]
        return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    def native_chat(self, messages: List[dict], temperature: float = 0.4,
                    max_tokens: int = 3072, num_ctx: int = 32768,
                    timeout: float = 600.0) -> Optional[str]:
        return self.chat(messages, temperature=temperature, max_tokens=max_tokens,
                         num_ctx=num_ctx)

    # ------------------------------------------------------------------
    @staticmethod
    def _last_token_pool(last_hidden_states, attention_mask):
        torch = __import__("torch")
        left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]
        if left_padding:
            return last_hidden_states[:, -1]
        sequence_lengths = attention_mask.sum(dim=1) - 1
        batch_size = last_hidden_states.shape[0]
        return last_hidden_states[
            torch.arange(batch_size, device=last_hidden_states.device),
            sequence_lengths,
        ]

    def embed(self, text: str) -> Optional[List[float]]:
        self._ensure_embed()
        torch, F, _AutoModel, _AutoModelForCausalLM, _AutoTokenizer = self._deps()
        tokenizer = self.embed_tokenizer
        model = self.embed_model
        batch = tokenizer(
            [text or ""],
            padding=True,
            truncation=True,
            max_length=self.embed_max_length,
            return_tensors="pt",
        ).to(model.device)
        with torch.inference_mode():
            outputs = model(**batch)
            emb = self._last_token_pool(outputs.last_hidden_state,
                                        batch["attention_mask"])
            emb = F.normalize(emb, p=2, dim=1)
        return emb[0].detach().float().cpu().tolist()


class OpenRouterClient(_ChatMixin):
    """外部 API(OpenAI 兼容)。key 从 secrets/env 读取;记录模型名但不含 key。

    默认模型 nvidia/nemotron-3-ultra-550b-a55b(可配);reasoning 支持按模型可选。
    (2026-09-24:原 minimax/minimax-m3:free 免费档已被 OpenRouter 下线,付费档 key 限额,整体换型。)
    """

    def __init__(self, model: str = "nvidia/nemotron-3-ultra-550b-a55b",
                 base_url: str = "https://openrouter.ai/api/v1",
                 api_key: str = "", timeout: float = 180.0, retries: int = 3,
                 extra_body: Optional[dict] = None):
        self.chat_model = model
        self.default_extra_body = dict(extra_body or {})
        # provider 私有参数默认透传(如 BigModel thinking 开关;None=不附加)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self._api_key = api_key or _openrouter_key()
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
