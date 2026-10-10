# -*- coding: utf-8 -*-
"""API key 解析的守卫(2026-09-27 首跑体检):路径/优先级必须一致且可发现。

背景:密钥原本有两处不一致的查找路径(case01/.secrets.json 与 <包根>/.secrets.json),
新人不知道写哪;写错位置就静默失效。现在统一为:
环境变量 → .env → .secrets.json(仓库根 / 包根 / 包根下任一子目录);且 `case01` 侧与
`case_engine` 侧**解析结果一致**。
"""
import io
import json
import os
import sys

import pytest

_this = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_this)
_PKG = os.path.join(_REPO, "provenance")
for _p in (_PKG, _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from case_engine import llm as CE            # noqa: E402
from case01.agents import secrets as S       # noqa: E402


def test_env_var_wins(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env")
    monkeypatch.setattr(CE, "_secrets_candidates", lambda: [])
    monkeypatch.setattr(CE, "_env_files", lambda: [])
    assert CE.openrouter_key() == "sk-from-env"
    assert S.openrouter_key() == "sk-from-env"


def test_reads_repo_secrets_json(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    p = tmp_path / ".secrets.json"
    p.write_text(json.dumps({"openrouter_api_key": "sk-from-json"}), encoding="utf-8")
    monkeypatch.setattr(CE, "_env_files", lambda: [])
    monkeypatch.setattr(CE, "_secrets_candidates", lambda: [str(p)])
    assert CE.openrouter_key() == "sk-from-json"
    assert CE.openrouter_source().endswith(str(p))


def test_reads_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("# 注释\nOPENROUTER_API_KEY='sk-from-dotenv'\n", encoding="utf-8")
    monkeypatch.setattr(CE, "_secrets_candidates", lambda: [])
    monkeypatch.setattr(CE, "_env_files", lambda: [str(env)])
    assert CE.openrouter_key() == "sk-from-dotenv"


def test_missing_is_empty_not_error(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(CE, "_secrets_candidates", lambda: [])
    monkeypatch.setattr(CE, "_env_files", lambda: [])
    monkeypatch.setattr(S, "_secrets_path", lambda: "")   # case01 侧也要密封
    assert CE.openrouter_key() == ""
    assert CE.openrouter_source() == ""
    assert S.openrouter_key() == ""


def test_malformed_secrets_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    bad = tmp_path / ".secrets.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(CE, "_env_files", lambda: [])
    monkeypatch.setattr(CE, "_secrets_candidates", lambda: [str(bad)])
    assert CE.openrouter_key() == ""          # 坏档不抛,回空


def test_both_sides_resolve_from_same_repo_file(tmp_path, monkeypatch):
    """case01 侧与 case_engine 侧必须都能从**同一份**仓库根 .secrets.json 取到 key。"""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    p = tmp_path / ".secrets.json"
    p.write_text(json.dumps({"openrouter_api_key": "sk-shared"}), encoding="utf-8")
    monkeypatch.setattr(CE, "_env_files", lambda: [])
    monkeypatch.setattr(CE, "_secrets_candidates", lambda: [str(p)])
    monkeypatch.setattr(S, "_secrets_path", lambda: str(tmp_path / "nope.json"))
    assert CE.openrouter_key() == "sk-shared"
    assert S.openrouter_key() == "sk-shared"


def test_setup_api_help_has_no_key_leak():
    """setup_api.py 的默认路径只写"来源",绝不把 key 打印出来。"""
    src = io.open(os.path.join(_PKG, "tools", "setup_api.py"), encoding="utf-8").read()
    assert "getpass" in src                     # 交互输入不回显
    assert "openrouter_source" in src           # 只报来源


# ---------------------------------------------------------------------------
# Router(N7)后端那张表(2026-10-10)。这里守的不是"DeepSeek 能不能用",而是
# **名单只有一份**:配置工具(`tools/setup_api.py`)与跑批解析
# (`router_client_from_env()`)各自抄一份时,出现过"闸门测的是另一个后端"
# 与"DeepSeek 只能借 vllm 这个名配"。
# 三个用例都把手机的 .secrets.json 挡在外面(`_secrets_files` → 空),
# 否则"在我机器上过"就成了唯一的证据。
# ---------------------------------------------------------------------------
def _seal(monkeypatch):
    for n in ("CASE01_ROUTER_PROVIDER", "CASE01_ROUTER_BASE_URL", "CASE01_ROUTER_MODEL",
              "CASE01_ROUTER_API_KEY", "DEEPSEEK_API_KEY", "BIGMODEL_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    from case01.agents import llm as L
    monkeypatch.setattr(L, "_secrets_files", lambda: [])
    return L


def test_router_provider_list_has_exactly_one_source(monkeypatch):
    L = _seal(monkeypatch)
    from tools import setup_api
    assert setup_api._providers() is L.ROUTER_PROVIDERS    # 同一对象,不是第二份拷贝
    for name, spec in L.ROUTER_PROVIDERS.items():
        assert spec["key_json"] and spec["client"] in ("openai", "vllm"), name
        assert spec.get("role"), name          # --show 那份"给平台填的清单"要印定位
        if not spec.get("needs_base"):
            assert spec["base"] and spec["model"], name    # 有默认值才许不填端点


def test_deepseek_resolves_from_the_table_with_thinking_off(monkeypatch):
    """点名 deepseek ⇒ 端点/模型/key/不许思考全部来自那张表,不联网也成立。"""
    L = _seal(monkeypatch)
    monkeypatch.setenv("CASE01_ROUTER_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-table-test")
    client, ident = L.router_client_from_env()
    assert ident["provider"] == "deepseek"
    assert client.chat_model == L.ROUTER_PROVIDERS["deepseek"]["model"]
    assert client.base_url == "https://api.deepseek.com/v1"
    # 判据是**发出去的请求体**里有 thinking=disabled,不是 HTTP 200(参数被忽略照样 200)
    assert client.default_extra_body.get("thinking") == {"type": "disabled"}


def test_unknown_provider_names_the_list_instead_of_falling_back(monkeypatch):
    """把模型名当后端名敲(deepseek-chat)要当场报错并列出名单。

    静默回落本地是更糟的失败:产物里只会写 `source="local_fallback"`,
    而 04 §五 要的独立模型悄悄没了(10-10 的 DeepSeek 404 同族 —— "名字不存在"
    与"网络失败"不能同形)。
    """
    L = _seal(monkeypatch)
    monkeypatch.setenv("CASE01_ROUTER_PROVIDER", "deepseek-chat")
    with pytest.raises(ValueError) as e:
        L.router_client_from_env()
    assert "bigmodel" in str(e.value) and "deepseek-chat" in str(e.value)
