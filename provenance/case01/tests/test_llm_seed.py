# -*- coding: utf-8 -*-
"""采样种子(seed)的透传与留痕守卫(2026-10-04)。

钉的是四件事,顺序即风险顺序:
1. **默认必须不设** —— 加了这段代码之后,不显式给种子时请求体要和改动前逐字节同,
   否则等于悄悄改了源设定里的采样行为;
2. 设了就要真的进请求体(本地 Ollama 两条端点都要:`/v1` 走顶层 `seed`,
   `/api/chat` 走 `options.seed` —— 少一条就是"以为固定了、其实没固定");
3. 取值只有一个来源:显式参数 > `CASE01_LLM_SEED`;非法值**抛**,不静默当没设;
4. 清单如实:`manifest.seed` 有值 = 这次真固定过,`null` = 没固定(默认行为,
   不是缺项,所以不进 `manifest_warnings`)。规则判定没有采样,一律 `null`。

全部用假 urlopen,不联网、不碰 GPU。
"""
import json

import pytest

from case01.agents import llm as llm_mod
from case01.injector.manifest import REQUIRED_KEYS, build_manifest, collect_run_meta


# --------------------------------------------------------------------- 替身
class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _capture(payload):
    """假 urlopen:记下每次请求体,返回固定 payload。"""
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(json.loads(req.data.decode("utf-8")))
        return _Resp(payload)

    return seen, fake_urlopen


OPENAI_OK = {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
NATIVE_OK = {"message": {"content": "ok"}}


# ------------------------------------------------------- 1) 默认不设(零改动)
def test_no_seed_by_default_on_both_endpoints(monkeypatch):
    seen_chat, fake = _capture(OPENAI_OK)
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    llm_mod.OllamaClient(chat_model="qwen3:8b").chat([{"role": "user", "content": "x"}])
    assert "seed" not in seen_chat[0], seen_chat[0]

    seen_native, fake2 = _capture(NATIVE_OK)
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake2)
    llm_mod.OllamaClient(chat_model="qwen3:8b").native_chat([{"role": "user", "content": "x"}])
    assert "seed" not in seen_native[0]["options"], seen_native[0]

    assert llm_mod.local_client_from_env().seed is None, "没配环境时客户端必须不带种子"


# ------------------------------------------------- 2) 设了就要真的进请求体
def test_seed_reaches_openai_compatible_endpoint(monkeypatch):
    seen, fake = _capture(OPENAI_OK)
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    llm_mod.OllamaClient(chat_model="qwen3:8b", seed=1234).chat(
        [{"role": "user", "content": "x"}])
    assert seen[0]["seed"] == 1234, seen[0]


def test_seed_reaches_native_options(monkeypatch):
    seen, fake = _capture(NATIVE_OK)
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    llm_mod.OllamaClient(chat_model="qwen3:8b", seed=1234).native_chat(
        [{"role": "user", "content": "x"}])
    assert seen[0]["options"]["seed"] == 1234, seen[0]
    # 温度照旧带过去:加种子不许挤掉原有采样项
    assert seen[0]["options"]["temperature"] == 0.4, seen[0]


def test_seed_zero_is_a_value_not_absence(monkeypatch):
    """0 是合法种子,不能被当成 falsy 丢掉。"""
    seen, fake = _capture(NATIVE_OK)
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    monkeypatch.setenv("CASE01_LLM_SEED", "0")
    client = llm_mod.local_client_from_env()
    assert client.seed == 0
    client.native_chat([{"role": "user", "content": "x"}])
    assert seen[0]["options"]["seed"] == 0, seen[0]


# ------------------------------------------------------------ 3) 取值来源
def test_explicit_seed_wins_over_env(monkeypatch):
    monkeypatch.setenv("CASE01_LLM_SEED", "7")
    assert llm_mod.local_client_from_env(seed=0).seed == 0
    assert llm_mod.local_client_from_env().seed == 7


def test_vllm_provider_also_carries_seed(monkeypatch):
    monkeypatch.setenv("CASE01_LLM_PROVIDER", "vllm")
    monkeypatch.setenv("CASE01_LLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("CASE01_LLM_SEED", "99")
    client = llm_mod.local_client_from_env()
    assert client.seed == 99
    seen, fake = _capture(OPENAI_OK)
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    client.chat([{"role": "user", "content": "x"}])
    assert seen[0]["seed"] == 99, seen[0]


def test_bad_seed_raises_instead_of_being_ignored():
    assert llm_mod.parse_seed("") is None
    assert llm_mod.parse_seed(None) is None
    assert llm_mod.parse_seed(" 42 ") == 42
    with pytest.raises(ValueError):
        llm_mod.parse_seed("abc")          # 静默吞掉 = 以为固定了其实没固定
    with pytest.raises(ValueError):
        llm_mod.parse_seed("42.5")


# ---------------------------------------------------------------- 4) 清单留痕
def test_seed_is_a_required_key():
    assert "seed" in REQUIRED_KEYS, "清单必备键要含 seed,否则新记录会漏掉这一栏"


def test_manifest_records_the_client_seed_not_env():
    from case01.agents.llm import OllamaClient

    client = OllamaClient(chat_model="qwen3:8b", seed=4242)
    m = build_manifest(collect_run_meta({}, judge_llm=client),
                       engine_id="case01-run")
    assert m["seed"] == 4242, m["seed"]


def test_manifest_seed_null_is_not_a_warning():
    """没固定种子是默认行为,不是缺项:seed=null 但不该污染 manifest_warnings。"""
    m = build_manifest(collect_run_meta({}), financial_dir="", scenario_path="")
    assert m["seed"] is None
    assert not [w for w in m["manifest_warnings"] if "seed" in w], m["manifest_warnings"]


def test_rules_backend_records_no_seed():
    """规则判定没有采样,写个种子等于谎报调过模型(与 temperature.judge 同口径)。"""
    meta = collect_run_meta({}, backend_kind="rules")
    assert meta["seed"] is None
    assert build_manifest(meta, financial_dir="", scenario_path="")["seed"] is None


def test_seed_is_not_invented_when_manifest_meta_is_cached():
    """沿用桥自己记的 manifest_meta 时,种子仍取自实际客户端,不凭空补。"""
    raw = {"manifest_meta": {"judge": "local", "judge_model": "qwen3:8b"}}
    meta = collect_run_meta(raw, judge_llm=None)
    assert meta["seed"] is None


# ------------------------------------------------------- 批量线的 env 透传
def test_batch_child_env_passes_seed(monkeypatch):
    from case01.tools import batch_run

    class _Ns:
        model = "qwen3:8b"
        embed_model = ""
        disable_thinking = True
        seed = 555

    env = batch_run._child_env(_Ns())
    assert env["CASE01_LLM_SEED"] == "555", env

    _Ns.seed = None
    assert "CASE01_LLM_SEED" not in batch_run._child_env(_Ns()), "缺省时不要写这一栏"


def test_seed_base_逐条一个值_且重试沿用同一条():
    """`--seed-base`:多样本的唯一来源(同 master 多跑几条 = 同一份内容,2026-10-06 实测)。

    序号取自 run_id 末尾(`batch_run.py` 起批时按 `-{i:03d}` 铸名字),所以
    ① lane 乱序消费不影响"第 i 条 = base+i";② **重试沿用同一个 run_id ⇒ 同一个种子**
    (否则一次重试同时改了设定,失败原因就分不清是偶发还是换数)。
    """
    from types import SimpleNamespace

    from case01.tools import batch_run

    def ns(base=None, seed=None):
        return SimpleNamespace(model="", embed_model="", disable_thinking=False,
                               seed=seed, seed_base=base)

    assert batch_run.run_seed(ns(base=20261101), "batch-x-001") == 20261102
    assert batch_run.run_seed(ns(base=20261101), "batch-x-012") == 20261113
    # base=0 是合法值,不能当成"没设"
    assert batch_run.run_seed(ns(base=0), "batch-x-005") == 5
    # 逐条值确实进了子进程 env(种子只走 env、不进 cmd,env 是唯一通道)
    env = batch_run._child_env(ns(base=20261101),
                               batch_run.run_seed(ns(base=20261101), "batch-x-003"))
    assert env["CASE01_LLM_SEED"] == "20261104", env
    # 两者都没给 = 不固定,env 里一个 seed 字节都不许多写
    assert batch_run.run_seed(ns()) is None
    assert "CASE01_LLM_SEED" not in batch_run._child_env(ns()), "缺省时不要写这一栏"
    # --seed 那条既有语义不动:整批一个值
    assert batch_run.run_seed(ns(seed=777), "batch-x-009") == 777
    # 名字里没有序号就**不猜**(宁可起跑前炸,不要跑完不知道用的哪个数)
    with pytest.raises(ValueError, match="序号"):
        batch_run.run_seed(ns(base=7), "manual-name")
