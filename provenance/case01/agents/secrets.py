# -*- coding: utf-8 -*-
"""敏感配置读取(API key 等)。绝不入库——key 存于 .secrets.json(gitignore)
或环境变量 OPENROUTER_API_KEY。本文件只负责读取,不打印、不落盘 key。
"""
import json
import os


def _secrets_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", ".secrets.json")


def openrouter_key() -> str:
    """优先环境变量,其次 .secrets.json 的 {"openrouter_api_key": "..."}"""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    p = _secrets_path()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            return str(d.get("openrouter_api_key", "")).strip()
        except Exception:
            return ""
    return ""


def write_secrets(api_key: str, base_url: str = "https://openrouter.ai/api/v1"):
    """把 key 写入 .secrets.json(仅本地,不入 git)。"""
    p = _secrets_path()
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"openrouter_api_key": api_key,
                   "openrouter_base_url": base_url},
                  f, indent=2)
    os.chmod(p, 0o600)
    return p
