# -*- coding: utf-8 -*-
"""一条命令配好外部 API key(含 Router 用的独立模型),并**联网自检**。

为什么有它:密钥原本要么设环境变量、要么手写 .secrets.json —— 对第一次接触项目的人
都不直观(实测:新人卡在这一步)。这里把"写 key + 选定后端 + 验证真能用"合成一条命令。
配好之后**跑批/起面不用再设任何环境变量**:`case01` 的 Router 会从 `.secrets.json`
读 `router_provider`(env 仍可临时覆盖,用于对照与排障)。

用法::

    # Router 换成 BigModel 的独立模型(04 §五 / 06 §七 要求它不是本地 Investment AI 自己)
    python provenance/tools/setup_api.py --router bigmodel --key <GLM key>
    python provenance/tools/setup_api.py --router bigmodel --check    # 只自检,不写文件

    # 现场演示/无网:显式声明"这次故意用本地"(产物里记 local_by_config,不冒充外部)
    python provenance/tools/setup_api.py --router local

    # 兼容旧用法:只配 OpenRouter 的 key(不动 router_provider)
    python provenance/tools/setup_api.py --key sk-xxxx
    python provenance/tools/setup_api.py                # 交互式输入(不回显)
    python provenance/tools/setup_api.py --show         # 看现在配的是谁(不打印 key)

    # 自托管 OpenAI 兼容端点(vLLM 等)
    python provenance/tools/setup_api.py --router vllm --base-url http://127.0.0.1:8101/v1 \
        --model qwen3-8b --key 随便填

安全:key 只写本地(仓库根 .secrets.json,已 gitignore),权限 0600;**绝不打印** key。
写文件是**合并**而不是覆盖 —— 早先这里是整文件重写,配 BigModel 会把 OpenRouter 的 key 抹掉。
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
BIGMODEL_BASE = "https://open.bigmodel.cn/api/paas/v4"
BIGMODEL_MODEL = "glm-4.7-flash"

# 每个 provider 存哪几个字段(key 的 json 名 / env 名 / 默认 base+model)
PROVIDERS = {
    "bigmodel": {"key_json": "bigmodel_api_key", "key_env": "BIGMODEL_API_KEY",
                 "base": BIGMODEL_BASE, "model": BIGMODEL_MODEL},
    "openrouter": {"key_json": "openrouter_api_key", "key_env": "OPENROUTER_API_KEY",
                   "base": DEFAULT_BASE, "model": DEFAULT_MODEL},
    "vllm": {"key_json": "router_api_key", "key_env": "CASE01_ROUTER_API_KEY",
             "base": "", "model": ""},
    "local": {"key_json": "", "key_env": "", "base": "", "model": ""},
}


def _repo_secrets_path() -> str:
    return os.path.join(REPO, ".secrets.json")


def _read_repo_secrets() -> dict:
    p = _repo_secrets_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _merge_repo_secrets(patch: dict) -> str:
    """**合并**写入仓库根 .secrets.json(不是整文件覆盖)。"""
    p = _repo_secrets_path()
    cur = _read_repo_secrets()
    for k, v in patch.items():
        if v is None or v == "":
            cur.pop(k, None)
        else:
            cur[k] = v
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def _check(key: str, base_url: str, model: str, extra_body: dict = None):
    """一次最小真实调用,验证 key 可用(只回状态/错误,不打印 key)。"""
    body = {
        "model": model, "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    body.update(extra_body or {})
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
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


def _configured_provider() -> str:
    from case01.agents.llm import router_provider_name
    return router_provider_name()


def _show() -> int:
    from case_engine.llm import openrouter_key, openrouter_source
    prov = _configured_provider() or "(未配 ⇒ Router 回落本地,记录里是 local_fallback)"
    sec = _read_repo_secrets()
    print("Router 后端(router_provider):{}".format(prov))
    print("OpenRouter key:{}".format(openrouter_source() or "(未配置)"))
    for name in ("bigmodel_api_key", "router_api_key"):
        print("{}:{}(只看有没有值,不打印)".format(
            name, "已配" if str(sec.get(name, "")).strip() else "未配"))
    print("一条命令配好 → python provenance/tools/setup_api.py --router bigmodel --key <key>")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="配置/自检外部 API key 与 Router 后端(OpenRouter / BigModel / vLLM)")
    ap.add_argument("--key", default="", help="API key(不填则交互输入;不回显)")
    ap.add_argument("--router", default="", choices=sorted(PROVIDERS),
                    help="把 N7(Router)配成哪个后端;配成 local 表示故意用本地")
    ap.add_argument("--base-url", default="", help="端点(不填按 provider 的默认值)")
    ap.add_argument("--model", default="", help="模型名(不填按 provider 的默认值)")
    ap.add_argument("--check", action="store_true", help="只自检(用已配置的 key),不写文件")
    ap.add_argument("--show", action="store_true", help="只看现在配的是谁(不打印 key)")
    args = ap.parse_args(argv)

    if args.show:
        return _show()

    # 没给 --router 时按老用法走 OpenRouter(向后兼容:--key sk-… 单独用仍然配 OpenRouter)
    provider = args.router or "openrouter"
    spec = PROVIDERS[provider]
    base = args.base_url or os.environ.get("CASE01_ROUTER_BASE_URL", "") or spec["base"]
    model = args.model or os.environ.get("CASE01_ROUTER_MODEL", "") or spec["model"]

    if args.check:
        # 自检**必须走跑批用的同一份解析**(env → .secrets.json 的 router_provider/
        # router_base_url/router_model),不能用 `--router` 的字面值类推。
        # 以前 `--check` 不带 `--router` 就固定测 OpenRouter,于是出现过两种假象:
        #   · 实际配的是 vllm/DeepSeek,自检报 OpenRouter 的 403(键额度),看着像"配置坏了";
        #   · 反过来 `--router bigmodel --check` 绿了,跑批用的却是另一个后端 ⇒ 假绿。
        # 手册 §一 第 6 行拿这句当演示闸门,所以它必须"测的就是 Will-Run 用的那个"。
        if provider == "local":
            print("router_provider=local:这次不测外部端点(现场演示用本地)。")
            return 0
        from case01.agents.llm import (router_client_from_env, router_identity,
                                       LOCAL_ROUTER_PROVIDERS)
        from case01.agents.llm import router_provider_name
        eff = router_provider_name()
        if not eff or eff in LOCAL_ROUTER_PROVIDERS:
            print("当前没配外部 Router(router_provider={!r})⇒ 跑批会回落本地模型,"
                  "04 §五 要的是独立 API 模型。配一条:"
                  "python provenance/tools/setup_api.py --router bigmodel --key <key>"
                  "(或 --router vllm --base-url … --model … --key …)".format(eff))
            return 1
        if args.router and args.router != eff:
            print("注意:你点了 --router {},但当前**生效**的是 {};"
                  "下面测的是生效那个(跑批用的就是它)。".format(args.router, eff))
        try:
            client, _ident = router_client_from_env()
        except Exception as e:  # noqa: BLE001 —— 配置不全要原样说出来
            print("自检结果:失败 ✗ 按当前配置造不出客户端:{}: {}".format(
                type(e).__name__, str(e)[:200]))
            return 1
        if client is None:
            print("自检结果:失败 ✗ router_client_from_env() 返回空(配置为空)。")
            return 1
        ident = router_identity(client)
        print("测的是当前生效后端:{} @ {}({})".format(
            ident.get("model"), ident.get("host"), ident.get("provider")))
        # 判据是**正文非空**,不是 HTTP 200:推理端点被忽略参数/思考吃光预算时
        # 照样 200、content 为空,而 Router 拿到空正文就等于拆不出问题。
        try:
            out = client.chat([{"role": "user",
                                "content": 'Reply with JSON only: {"ok": true}'}],
                               temperature=0.1, max_tokens=64)
        except Exception as e:  # noqa: BLE001
            print("自检结果:失败 ✗ {}: {}".format(type(e).__name__, str(e)[:200]))
            return 1
        if not (out or "").strip():
            print("自检结果:失败 ✗ 模型回空正文(思考链可能吃光了 max_tokens=64);"
                  "Router 遇到同样的情况时 `router.issues` 会是空的")
            return 1
        print("自检结果:可用 ✓ 正文 {} 字符 | identity={}".format(
            len(out), json.dumps(ident, ensure_ascii=False)))
        return 0
    else:
        key = args.key.strip()
        if provider != "local" and not key:
            key = getpass.getpass("粘贴 API key(不回显): ").strip()
        if provider == "local":
            p = _merge_repo_secrets({"router_provider": "local"})
            print("已写入:{}  router_provider=local(Router 故意用本地)".format(p))
            print("要换成外部独立模型:python provenance/tools/setup_api.py "
                  "--router bigmodel --key <key>")
            return 0
        if not key:
            print("没给 key(--key)也没交互输入,什么也没做。")
            return 2
        patch = {"router_provider": provider, spec["key_json"]: key}
        if provider == "vllm":
            patch.update({"router_base_url": base, "router_model": model})
            if spec.get("key_json"):
                patch["router_api_key"] = key
        p = _merge_repo_secrets(patch)
        # 这行以前漏了 .format(p),于是把 "已写入:{}" 原样打出来 —— 新人最想知道的
        # "key 落到哪个文件"恰好空着(10-09 实配 bigmodel 时看到)。
        print("已写入:{}  (已 gitignore,不会入库;合并写,其他 provider 的 key 不动)".format(p))
        print("生效后端:router_provider={}  model={}  base={}".format(provider, model, base))

    if not key:
        print("未找到可用的 key。先配: python provenance/tools/setup_api.py --key sk-xxxx")
        print("或用本地 Ollama(默认,零密钥): 见 .env.example 方案 A。")
        return 1

    print("自检中(一次最小真实调用)…")
    extra = {"thinking": {"type": "disabled"}} if provider == "bigmodel" else None
    ok, info = _check(key, base, model, extra)
    print("自检结果:" + ("可用 ✓ " if ok else "失败 ✗ ") + info)
    if ok and not args.check:
        print("\n下一步:python provenance/live_switch.py --start case01   (或 --start case00)")
        print("跑批不用再设环境变量;要临时换后端:CASE01_ROUTER_PROVIDER=vllm/env 优先。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
