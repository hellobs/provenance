# -*- coding: utf-8 -*-
"""一条命令配好外部 API key(OpenRouter 等),并**联网自检**。

为什么有它:密钥原本要么设环境变量、要么手写 .secrets.json —— 对第一次接触项目的人
都不直观(实测:新人卡在这一步)。这里把"写 key + 验证 key 真能用"合成一条命令。

用法::

    python provenance/tools/setup_api.py --key sk-xxxx     # 写入仓库根 .secrets.json + 自检
    python provenance/tools/setup_api.py                   # 交互式输入(不回显)
    python provenance/tools/setup_api.py --check           # 只自检(用已配好的 key)
    python provenance/tools/setup_api.py --show            # 只看 key 从哪来(不打印 key)

安全:key 只写本地(仓库根 .secrets.json,已 gitignore),权限 0600;**绝不打印** key。
"""
import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.request

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # <repo>/provenance
REPO = os.path.dirname(PKG)                                           # <repo>
if PKG not in sys.path:
    sys.path.insert(0, PKG)

DEFAULT_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"


def _write_repo_secrets(key: str, base_url: str) -> str:
    """写入仓库根 .secrets.json(与 case_engine.llm 的解析口径一致)。"""
    p = os.path.join(REPO, ".secrets.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"openrouter_api_key": key, "openrouter_base_url": base_url},
                  f, indent=2)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def _check(key: str, base_url: str, model: str):
    """一次最小真实调用,验证 key 可用(只回状态/错误,不打印 key)。"""
    body = json.dumps({
        "model": model, "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return True, "HTTP {}".format(r.status)
    except urllib.error.HTTPError as e:
        tail = (e.read() or b"")[:200].decode("utf-8", "replace")
        return False, "HTTP {} {}".format(e.code, tail)
    except Exception as e:  # noqa: BLE001 —— 失败原因如实报出
        return False, "{}: {}".format(type(e).__name__, e)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="配置/自检外部 API key(OpenRouter 等)")
    ap.add_argument("--key", default="", help="API key(不填则交互输入;不回显)")
    ap.add_argument("--base-url", default=os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE))
    ap.add_argument("--model", default=os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL))
    ap.add_argument("--check", action="store_true", help="只自检(用已配置的 key),不写文件")
    ap.add_argument("--show", action="store_true", help="只看 key 来源,不做别的")
    args = ap.parse_args(argv)

    from case_engine.llm import openrouter_key, openrouter_source

    if args.show:
        print("key 来源:" + (openrouter_source() or "(未配置)"))
        print("一条命令配好 → python provenance/tools/setup_api.py --key sk-xxxx")
        return 0

    if args.check:
        key = openrouter_key()
    else:
        key = args.key.strip() or getpass.getpass("粘贴 API key(不回显): ").strip()
        if not key:
            print("没给 key(--key)也没交互输入,什么也没做。")
            return 2
        p = _write_repo_secrets(key, args.base_url)
        print("已写入:{}  (已 gitignore,不会入库)".format(p))

    if not key:
        print("未找到可用的 key。先配: python provenance/tools/setup_api.py --key sk-xxxx")
        print("或用本地 Ollama(默认,零密钥): 见 .env.example 方案 A。")
        return 1

    print("自检中(一次最小真实调用)…")
    ok, info = _check(key, args.base_url, args.model)
    print("自检结果:" + ("可用 ✓ " if ok else "失败 ✗ ") + info)
    if ok and not args.check:
        print("\n下一步:python provenance/live_switch.py --start case01   (或 --start case00)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())