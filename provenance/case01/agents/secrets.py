# -*- coding: utf-8 -*-
"""敏感配置读取(API key 等)。绝不入库——key 存于 .secrets.json(gitignore)
或环境变量 OPENROUTER_API_KEY。本文件只负责读取,不打印、不落盘 key。
"""
import json
import os


def _secrets_path() -> str:
    """历史写法:case01/.secrets.json(继续兼容读取)。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", ".secrets.json")


def _repo_secrets_path() -> str:
    """统一写法:仓库根 .secrets.json(与 case_engine.llm 的解析口径一致)。"""
    pkg = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))                       # .../case01/agents/secrets.py
    return os.path.join(os.path.dirname(pkg), ".secrets.json")


def openrouter_key() -> str:
    """解析 OpenRouter key,顺序:环境变量 → case01/.secrets.json(历史) → 引擎统一解析。

    引擎统一解析见 `case_engine.llm.openrouter_key`(环境变量 → .env → .secrets.json,
    含仓库根/包根/包根下任一子目录)。一条命令配好:
    `python provenance/tools/setup_api.py --key sk-...`。
    """
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    p = _secrets_path()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                k = str(json.load(f).get("openrouter_api_key", "")).strip()
            if k:
                return k
        except Exception:
            pass
    from case_engine.llm import openrouter_key as _engine_key
    return _engine_key()


def write_secrets(api_key: str, base_url: str = "https://openrouter.ai/api/v1"):
    """把 key 写入**仓库根** .secrets.json(仅本地,不入 git;权限 0600)。"""
    p = _repo_secrets_path()
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"openrouter_api_key": api_key,
                   "openrouter_base_url": base_url},
                  f, indent=2)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p
