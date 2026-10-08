# -*- coding: utf-8 -*-
"""轻量 LLM client。
- OllamaClient:本地 Ollama(OpenAI 兼容 /v1/chat/completions + /api/embed)
- LocalHFClient:直接加载本地 HuggingFace 权重(chat + embedding)
- OpenRouterClient:外部 API(OpenAI 兼容;key 从 secrets/环境变量读取,
  不落盘、不打印)——供 Ethan/Router 等非 Investment AI 角色使用
  (0904 规定 Investment AI 必须本地 Ollama 运行)。

chat(): 文本补全,支持 temperature/max_tokens;重试 + 超时(指数退避)。

seed(采样种子,2026-10-04 加):**默认不设**——不设就是原来的非确定行为,
一条记录的输出和源设定都不因这段代码改变。设了才进请求体(Ollama/vLLM 走
`seed`,`LocalHFClient` 走 `torch.manual_seed`),并同时应被 `manifest.seed` 记下。
它只把"同机同版本的逐字重生成"变成大概率可复现,**不等于**跨机逐字一致
(见 docs/复现说明_三档口径.md §五),客户端也不自己造默认值。
"""
import json
import os
import time
import urllib.request
import urllib.error
from typing import List, Optional


def parse_seed(value) -> Optional[int]:
    """把 seed 配置收成 int;空/None → None(= 不设种子)。

    非法值**抛 ValueError**,不静默当成"没设":`CASE01_LLM_SEED=abc` 若被吞掉,
    记录里就是 seed=null,而调用方以为自己固定过种子了。
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 10)
    except ValueError:
        raise ValueError("seed 必须是整数,收到:{!r}".format(value))


def seed_from_env() -> Optional[int]:
    """环境里的采样种子(`CASE01_LLM_SEED`);没设就是 None。"""
    return parse_seed(os.environ.get("CASE01_LLM_SEED"))


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
        seed = getattr(self, "seed", None)
        if seed is not None:
            body["seed"] = seed
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
    # timeout 120 → 300(2026-10-07 实测):8 GB 单机上"刚加载完/刚把 num_ctx 从小升到 32768"
    # 的第一发请求要 ~185 s(只把权重拉进驻留是 ~14 s),120 s 会在 3 次重试内全部超时,
    # 表现为 `OllamaClient chat failed after 3 retries: timed out` 并把整条 run 打死
    # —— 两次独立复现(批次 261007-163008-rep6h 死在 T0;261007-171828-cmpB8b 首条重试后才出产物)。
    # 预热按 num_ctx=32768 做可以把这 185 s 压成 16 s(见《10月15日演示_运行手册》),
    # 但彩排/现场的预热不一定每次都按这条走,所以默认值本身要给余量。
    def __init__(self, base_url: str = "http://127.0.0.1:11434",
                 chat_model: str = "qwen3:4b-instruct-2507-q4_K_M",
                 embed_model: str = "qwen3-embedding:0.6b-q8_0",
                 timeout: float = 300.0, retries: int = 3,
                 num_ctx: int = 32768, think: Optional[bool] = None,
                 seed: Optional[int] = None):
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.timeout = timeout
        self.retries = retries
        self.num_ctx = num_ctx
        self.think = think
        self.seed = seed

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
        options = {"num_ctx": num_ctx, "temperature": temperature,
                   "num_predict": max_tokens}
        if self.seed is not None:
            options["seed"] = self.seed
        body = {
            "model": self.chat_model,
            "messages": messages,
            "options": options,
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
                # 原生端点用 `done_reason` 表态(`"length"` = 被 `num_predict` 截),
                # 而**反思正是唯一主要走这条路的长输出** —— 少这一行时,反思被预算截了
                # 清单里仍是 `truncations: 0`,事后无从分辨"模型只写这么短"和"写到一半断了"
                # (2026-10-07 实测:批 261007-130642-sb20b 的 `-012` 只有 47 字/质量分 15、
                # `-011` 194 字/30 分,当场答不出是不是截断;而 `_ChatMixin` 只接了
                # OpenAI 兼容端的 `finish_reason`,原生端一个字段都没读)。
                self._note_truncation(obj.get("done_reason"), max_tokens)
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
                 extra_body: Optional[dict] = None,
                 seed: Optional[int] = None):
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embed_base_url = (embed_base_url or base_url).rstrip("/")
        self.embed_model = embed_model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.seed = seed
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


def local_client_from_env(retries: int = 3, seed=None):
    """Select a local backend from environment variables; default to Ollama."""
    provider = os.environ.get("CASE01_LLM_PROVIDER", "ollama").strip().lower()
    chat_model = os.environ.get(
        "CASE01_LLM_MODEL", "qwen3:4b-instruct-2507-q4_K_M").strip()
    embed_model = os.environ.get(
        "CASE01_EMBED_MODEL", "qwen3-embedding:0.6b-q8_0").strip()
    # 显式传的 seed 优先;其次 CASE01_LLM_SEED;都没有 = 不设(保持原非确定行为)
    # (0 是合法种子值,所以只把 None/空串当"没给")
    if seed is None or (isinstance(seed, str) and not seed.strip()):
        seed = seed_from_env()
    else:
        seed = parse_seed(seed)
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
            seed=seed,
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
            seed=seed,
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
                 embed_max_length: int = 8192,
                 seed: Optional[int] = None):
        self.chat_model_path = chat_model_path
        self.embed_model_path = embed_model_path
        self.device = device
        self.embed_device = embed_device or device
        self.max_input_tokens = max_input_tokens
        self.embed_max_length = embed_max_length
        self.seed = seed
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
        if self.seed is not None:
            # 本地权重没有"请求体",种子只能设在 torch 的采样上(贪心路径本就不随机)
            torch.manual_seed(int(self.seed))
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


# ---------------------------------------------------------------------------
# Router(N7)专用:把"反思的问题拆分"交给一个**不是本地 Investment AI 自己**的模型
# ---------------------------------------------------------------------------
# 出处:0904doc 04 §五"由一个独立 API 大语言模型承担 Router 功能。Router 不使用本地
# Investment AI 本身进行自我分类";06 §七、03 §十四同。2026-10-07 逐条对照查出批路径
# 一直在违反:`router_llm` 缺省回落到本地同一个模型,于是"反思自己拆问题给自己审"。
# 这里只切 N7 一处 —— N4 分支判定/N5 仓位解析/一致性判官仍用原来的 `router_llm`,
# 因为换它们会直接改变分支分布(那是已入库的测量结论,#43/#56 依赖它)。

BIGMODEL_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
BIGMODEL_ROUTER_MODEL = "glm-4.7-flash"
OPENROUTER_ROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"


def _host_of(base: str) -> str:
    return str(base or "").split("//")[-1].split("/")[0]


def _secrets_files() -> List[str]:
    """按引擎同一顺序找 .secrets.json:case01 包根、其上一层(应用根)、git 根。"""
    out = []
    try:
        import case01
        app = os.path.dirname(os.path.dirname(os.path.abspath(case01.__file__)))
    except ImportError:
        app = ""
    for base in (app, os.path.dirname(app) if app else ""):
        if base:
            out.append(os.path.join(base, ".secrets.json"))
            out.append(os.path.join(base, "case01", ".secrets.json"))
    out.append(os.path.join(os.path.abspath(__file__), "..", "..", "..", "..",
                            ".secrets.json"))
    seen, res = set(), []
    for p in out:
        p = os.path.normpath(p)
        if p not in seen and os.path.exists(p):
            seen.add(p)
            res.append(p)
    return res


def _secret_value(env_name: str, json_name: str) -> str:
    """env 优先,再逐个 secrets 文件取 json_name(只取值,绝不打印)。"""
    k = os.environ.get(env_name, "").strip()
    if k:
        return k
    for p in _secrets_files():
        try:
            with open(p, encoding="utf-8") as f:
                v = str(json.load(f).get(json_name, "")).strip()
        except (OSError, ValueError):
            continue
        if v:
            return v
    return ""


LOCAL_ROUTER_PROVIDERS = ("local", "none", "off")


def router_provider_name() -> str:
    """这次要用哪个 Router 后端:**env 优先,其次 `.secrets.json`,都没有=没配**。

    做成两级是因为"配外部 API"这件事必须对第一次接触项目的人足够简单(实测新人卡在
    key 这一步):`tools/setup_api.py --router bigmodel --key …` 一条命令把 provider 和
    key 一起写进 `.secrets.json`(已 gitignore),之后跑批/起面**不用再记环境变量名**。
    env 仍保持最高优先级,因为它是一次性对照与排障用的。
    显式写 `local` 与"没配"要区分:前者是"这次故意用本地"(现场演示无网/无 key),
    后者是配置缺失 —— 产物里分别记 `local_by_config` / `local_fallback`。
    """
    p = os.environ.get("CASE01_ROUTER_PROVIDER", "").strip().lower()
    if p:
        return p
    return _secret_value("", "router_provider").strip().lower()


def router_identity(client) -> dict:
    """这台机器**实际**是谁:从对象属性读,不读配置字符串。

    必要性:04 §五 要的是"独立模型"这件事**可查**。若只写一句"已用外部 API",
    批产物里就没有判据 —— 与 #13 那条陷阱同族(结论依赖的变量没落进产物)。
    合规相关的两个布尔(`separate_client_from_reflection` / `external_api`)由调用方
    比对象、看 host 之后填 —— 它们不等价:把 vllm provider 指向本机 Ollama 时,
    前一个是真、后一个是假。
    """
    if client is None:
        return {"provider": "none", "model": "", "host": ""}
    host = _host_of(getattr(client, "base_url", ""))
    provider = "local" if host.startswith(("127.0.0.1", "localhost", "::1")) else "api"
    return {"provider": provider, "model": str(getattr(client, "chat_model", "")),
            "host": host}


def router_client_from_env():
    """按 `CASE01_ROUTER_PROVIDER` 造 N7 的客户端,返回 (client, identity)。

    没配 provider ⇒ 返回 (None, {}),调用方照实回落本地并在产物里记
    `source="local_fallback"`(违规要看得见,不给静默兜底);显式配 `local` 记
    `local_by_config`。
    provider 取值:`bigmodel`(默认 `glm-4.7-flash`,key 走 `BIGMODEL_API_KEY`
    或 `.secrets.json` 的 `bigmodel_api_key`)/ `openrouter`(沿用既有 key 解析)/
    `vllm`(任何 OpenAI 兼容端点;`router_base_url`/`router_model` 也能写在
    `.secrets.json`,所以一条 setup 命令就配得完自托管模型)。
    覆盖项:`CASE01_ROUTER_MODEL`、`CASE01_ROUTER_BASE_URL`、`CASE01_ROUTER_TIMEOUT`。
    """
    provider = router_provider_name()
    if provider in LOCAL_ROUTER_PROVIDERS or not provider:
        return None, {}
    model = os.environ.get("CASE01_ROUTER_MODEL", "").strip() \
        or _secret_value("", "router_model")
    base = os.environ.get("CASE01_ROUTER_BASE_URL", "").strip() \
        or _secret_value("", "router_base_url")
    try:
        timeout = float(os.environ.get("CASE01_ROUTER_TIMEOUT", "") or 180.0)
    except ValueError:
        raise ValueError("CASE01_ROUTER_TIMEOUT 要是一个数,收到:{!r}".format(
            os.environ.get("CASE01_ROUTER_TIMEOUT")))
    if provider == "bigmodel":
        key = _secret_value("BIGMODEL_API_KEY", "bigmodel_api_key")
        if not key:
            raise RuntimeError(
                "Router provider=bigmodel 但取不到 key。一条命令配好:"
                "`python provenance/tools/setup_api.py --router bigmodel --key <GLM key>`"
                "(写进 .secrets.json,已 gitignore;不打印 key)")
        client = OpenRouterClient(
            model=model or BIGMODEL_ROUTER_MODEL, base_url=base or BIGMODEL_BASE_URL,
            api_key=key, timeout=timeout,
            # 拆问题是短 JSON 任务:开着 reasoning 会把正文挤出 max_tokens
            # (与 branch_judge_eval 对 BigModel 的同一处置,2026-09-27 实测)
            extra_body={"thinking": {"type": "disabled"}})
    elif provider == "openrouter":
        key = _openrouter_key()
        if not key:
            raise RuntimeError(
                "Router provider=openrouter 但取不到 OPENROUTER_API_KEY。一条命令配好:"
                "`python provenance/tools/setup_api.py --router openrouter --key sk-…`")
        client = OpenRouterClient(model=model or OPENROUTER_ROUTER_MODEL,
                                  base_url=base or "https://openrouter.ai/api/v1",
                                  api_key=key, timeout=timeout)
    elif provider == "vllm":
        if not base:
            raise ValueError(
                "Router provider=vllm 需要端点。一条命令配好:"
                "`python provenance/tools/setup_api.py --router vllm "
                "--base-url http://…/v1 --model <名字> --key …`"
                "(或设 CASE01_ROUTER_BASE_URL)")
        if not model:
            raise ValueError("CASE01_ROUTER_PROVIDER=vllm 需要 CASE01_ROUTER_MODEL"
                            "(这个后端没有有意义的默认模型名)")
        client = VLLMClient(base_url=base, chat_model=model,
                            embed_model=model,
                            api_key=_secret_value("CASE01_ROUTER_API_KEY",
                                                  "router_api_key"),
                            timeout=timeout)
    else:
        raise ValueError("CASE01_ROUTER_PROVIDER 只认 bigmodel/openrouter/vllm,"
                         "收到:{}".format(provider))
    ident = router_identity(client)
    ident["provider"] = provider
    return client, ident
