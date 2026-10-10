"""Case-local safe LLM provider for Mavis agents.

This keeps output validation and generation limits inside provenance instead of
requiring changes to the upstream Mavis package. Both Ollama and vLLM use an
OpenAI-compatible chat-completions endpoint here.
"""
import json
import sys
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


def _red(text):
    """把一行染成红色**亮**。

    为什么只在真终端上色:provider 的 stdout 同时会被重定向进
    `%TEMP%/dsh_srv/live_*.out`,无条件加颜色会把 ANSI 转义符**写进日志文件**,
    之后 grep 日志就会被 \x1b[31m 污染。isatty() 为假(重定向/管道)就返回原文。
    Windows 10/11 的 conhost 默认支持 VT;万一不支持,未开启时原样输出,不会花屏。
    """
    try:
        if not sys.stdout.isatty():
            return text
    except Exception:  # noqa: BLE001 - 没有 isatty 的极端环境就当文件处理
        return text
    return "\x1b[1;31m" + text + "\x1b[0m"


def _env_flag(name):
    """环境变量开关:非空且不是 0/false 即视为开。"""
    import os
    return str(os.environ.get(name, "") or "").strip().lower() not in ("", "0", "false", "no")


def _env_seed():
    """现读种子链的 env(`CASE01_LLM_SEED`);没设或非法都返回 None(=不下发 seed)。"""
    import os
    text = str(os.environ.get("CASE01_LLM_SEED", "") or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        print("[case01.llm] CASE01_LLM_SEED 不是整数,按未设处理:{!r}".format(text), flush=True)
        return None


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
        # 端点不支持 response_format 而降级的次数(运行清单里要能查)
        self.response_format_downgrades = 0
        # None=还没试过 / True=支持 / False=这端点整个 response_format 家族都拒绝
        self._response_format_supported = None
        # 这些调用点**禁用兜底值**:失败就报错,不用 failsafe 充数
        # (它们的 failsafe 是"看起来正常的一句话",会污染对话/记录)。
        # 可用 think.llm.forbid_failsafe 覆盖;设 "__never__" 可关掉这个保护。
        _forbid = self.config.get("forbid_failsafe",
                                  ["generate_chat", "generate_chat_check_repeat"])
        self.forbid_failsafe_callers = set(
            x for x in _forbid if x and x != "__never__")
        self.summary = {"total": [0, 0, 0]}
        # 截断计数(finish_reason == "length"):截断的输出与完整输出长得一模一样,
        # 记进 summary 才会出现在 mavis 的角色日志/state 里,而不是只有天知道。
        self.truncations = 0
        self.last_truncation = None
        # 采样种子:配置里显式给了就用它;没给则在每次调用时现读 env —— 因为 env
        # 可能在 provider 构造之后才被种子链设上(live_run 是"先建桥后播种"的顺序)。
        raw_seed = str(self.config.get("seed", "") or "").strip()
        try:
            self.seed = int(raw_seed) if raw_seed else None
        except ValueError:
            self.seed = None

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
        # ⚠ caller 恒为 "llm_normal"(mavis 从不传 caller),所以按调用点白名单匹配
        # 等于永不命中 —— 实测仍打出 "caller=llm_normal failsafe='嗯'"。
        # 可靠的判据是 **兜底值本身是不是一句自然话**:mavis 的对话类兜底全是字符串
        # ("嗯" / "X 说的话没有得到回应" / "X 进行了一次对话" / "空闲"),它们会**冒充模型
        # 的台词或行为**写进产物;数字/布尔/结构化占位不会冒充成一句话。
        # 精确判据:mavis 的**对话**调用(prompt_generate_chat 等)的 return_type 是一个
        # **函数**(如 generate_chat),结构化调用的 return_type 是 pydantic 模型。
        # 不能按"兜底值是不是字符串"判 —— 连 'Trading Center'(扇区占位)都是字符串,
        # 那会把整局搞崩(实测 2026-10-10)。
        _is_dialogue_call = (return_type is not None
                             and not hasattr(return_type, "model_json_schema"))
        if result is None and (caller in self.forbid_failsafe_callers
                                or (_is_dialogue_call
                                    and "__never__" not in self.forbid_failsafe_callers)):
            # 2026-10-10 用户要求"不允许静默处理",而且对话类兜底最恶劣:
            # mavis 的 failsafe 里存着 "X 说的话没有得到回应" / "X 进行了一次对话"
            # 这种**看起来完全正常**的句子 —— 落到记录里就等于凭空造了一句话。
            # 宁缺毋造:这些调用点失败就让上层知道(对话轮空),不拿兜底值充数。
            print(_red("[case01.llm] !! 调用点 {} 命中兜底值,按宁缺毋造**拒绝返回**"
                       "(不写进产物): failsafe={!r}".format(caller, failsafe)),
                  flush=True)
            raise RuntimeError(
                "{} 调用失败且该调用点禁用兜底值(不静默造假)".format(caller))
        if result is None:
            # 2026-10-10 用户要求"不允许静默处理":走到兜底值必须喊出来 ——
            # 兜底值(如字符串"嗯")会被当成模型的话显示/入库,静默就是造假。
            print(_red("[case01.llm] !! 全部尝试失败,返回**兜底值**(不是模型说的):"
                       " caller={} failsafe={!r} 累计={} 次".format(
                           caller, failsafe, self.summary["total"][2])),
                  flush=True)
            return failsafe
        return result

    def _complete(self, prompt, return_type, temperature=0.5,
                  caller="llm_normal", max_tokens=None, **_kwargs):
        response_format = None
        # ⚠ 2026-10-10 修「该角色每句只剩『嗯』」:mavis 的 prompt 结果里
        # `return_type` **常常是一个函数**(如 `generate_chat`),不是 pydantic 模型。
        # 原代码只要 `return_type is not None` 就去取 `.model_json_schema()` ⇒
        # AttributeError ⇒ 异常被 completion() 吞掉 ⇒ 每次都走 failsafe,
        # 而 `prompt_generate_chat` 的 failsafe 恰好就是字符串「嗯」。
        # 所以只有**真的是 pydantic 模型**时才设 json_schema。
        if return_type is not None and hasattr(return_type, "model_json_schema"):
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
        # 外部 API 默认开着 reasoning(DeepSeek 系实测:reasoning_tokens≈279、还会继续烧),
        # 而下面的 max_tokens 对"对话正文"只给到几百 —— reasoning 一烧,正文就被砍成
        # 一个字(2026-10-10 实测:外部 API 下该角色每句只剩"嗯")。
        # 与 mavis 侧 OpenAIProvider 同一开关(MAVIS_LLM_DISABLE_THINKING),语义一致。
        if _env_flag("MAVIS_LLM_DISABLE_THINKING"):
            body["thinking"] = {"type": "disabled"}
        # 种子链:显式设过 CASE01_SEED/CASE01_LLM_SEED 才下发 —— 不传就是今天的
        # 非确定路径。此前 agent 台词根本不带 seed(`--seed` 只钉住了判定/反思/路由),
        # 所以"一个种子"覆盖不到小镇;这一行把它接上。
        seed = self.seed if self.seed is not None else _env_seed()
        if seed is not None:
            # 把**这一次实际发出去的值**记回实例。清单里的 `seed` 只认客户端自己的
            # 声明(`manifest.detect_seed` 刻意不读 env,免得造出"清单说固定了、
            # 客户端其实没固定"的假证据),而这里此前只在请求体里带 seed、实例仍是
            # None ⇒ 产物写着 `seed: null`,而"一个种子"这件事在记录里查不到
            # (2026-10-06 实测:小镇面三条真跑的 manifest.seed 全为 null)。
            self.seed = seed
            body["seed"] = seed
        # 端点能力记忆:DeepSeek 的 chat/completions 目前对 response_format **整个家族**
        # 都回 400("This response_format type is unavailable now",json_object 同样)。
        # 第一次撞到就记住"这端点不支持",之后不再发 —— 否则每次调用白费两次请求
        # (延迟与成本翻倍),实测一局里能刷出几十次降级日志。
        if response_format and self._response_format_supported is not False:
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
        # 2026-10-10:DeepSeek 等端点**不接受** `response_format={"type":"json_schema",...}`
        # 这一族新格式,直接回 400 ⇒ 上层 3 次 attempt 全失败 ⇒ 落到 mavis 的
        # failsafe("嗯"),看起来就像"模型不说话"。这里降级重试,而且**必须出声**:
        # 端点能力差异不能悄悄吞掉。
        if response.status_code == 400 and response_format:
            self._response_format_supported = False
            print(_red("[case01.llm] !! 端点拒绝 response_format={} (HTTP 400),"
                       "降级为不带 response_format 重试一次(已记住该端点不支持)。"
                       "端点原文: {}".format(
                           response_format.get("type"),
                           (response.text or "")[:200])),
                  flush=True)
            self.response_format_downgrades += 1
            # 退到 JSON mode(DeepSeek 支持 {"type":"json_object"}),而不是把整个
            # response_format 丢掉 —— 丢掉就没有"必须是 JSON"的约束,解析更容易失败。
            body.pop("response_format", None)
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
        # 2026-10-10:structured 档原本 256,实测把结构化输出连续截断 3 次
        # (poignancy_event 解析到空串 → 落兜底值)。抬到 1024 并加下限保护。
        # max_tokens 是**上限**不是目标,给足不会多花钱(用多少发多少),
        # 只会让"输出被截断 → 解析失败 → 落兜底"这条路彻底走不通。
        # 历史:256 把 Structured 连续截断 3 次(poignancy_event 解析到空串);用户要求"那就搞大"。
        limits = {"structured": 4096, "conversation": 8192, "long": 8192, "default": 4096}
        # 已收口(2026-10-10 当天两次抬档 8804250/19c881d):此前日志里
        # "输出被 max_tokens 截断 caller=llm_normal max_tokens=256" —— 小镇的对话正文
        # 走的是 caller 缺省值,拿的是旧 structured/default 档 256;外部 API 又默认开 reasoning,
        # 256 装不下 ⇒ "每句只剩一个字"的另一半原因。抬到上面的档位后当天核对过记录根:
        # 带"小镇 … 截断"警告的最后一条是 11:45,13:12 之后落的 23 条记录 `truncations` 全为 0。
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
        # 模型常把 JSON 包在 ```json …``` 里(实测 DeepSeek 降级后就这么回),
        # 先剥围栏再解析 —— 否则围栏残渣会让 json.loads 直接失败。
        raw = text or ""
        stripped = raw.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[-1]
            if stripped.rstrip().endswith("```"):
                stripped = stripped.rstrip()[:-3]
            stripped = stripped.strip()
        candidates = []
        for candidate_text in (raw, stripped):
            try:
                candidates.append(json.loads(candidate_text))
            except (TypeError, json.JSONDecodeError):
                continue
            break
        # 去掉 response_format 之后(端点不支持),模型会按提示词里的例子回**裸词或
        # Python 字面量**,不再是 JSON。实测三类:
        #   'Yes' / 'No'            → 布尔判定(generate_chat_check_repeat / decide_chat_terminate)
        #   [("Open the AI chat", 5), ...] → **Python 元组列表**(schedule_decompose)
        # 所以再加两条安全兜底:裸词转布尔、ast.literal_eval 读 Python 字面量。
        low = (stripped or raw).strip().lower()
        if low in ("yes", "true"):
            candidates.append(True)
        elif low in ("no", "false"):
            candidates.append(False)
        # 裸数字串:'07'(起床 7 点)、'8'。实测模型对 int 字段常回**带前导零的字符串**,
        # 而 ast.literal_eval("07") 因前导零直接语法错误 ⇒ 这条以前必解析失败,
        # 一次都没成功过(wakeupResponse 实测 10/10 失败,每次落到兜底的"8 点起床")。
        # 这里显式转 int(含前导零与空白);不是数字就交给后面的候选。
        if low:
            try:
                candidates.append(int(low, 10))
            except ValueError:
                pass
        try:
            import ast as _ast
            candidates.append(_ast.literal_eval((stripped or raw).strip()))
        except Exception:
            pass
        candidates.extend(cls._json_objects(stripped or raw))
        for value in candidates:
            for payload in (value if isinstance(value, dict) else {"res": value},
                            # 有些响应模型只有一个 res 字段(如 schedule_daily),
                            # 而模型回的是**裸字典** {"0:00": "..."};按 schema 直接
                            # 校验必然失败,包一层 res 再试(实测 3 次全被误判为无效)。
                            {"res": value}):
                try:
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
