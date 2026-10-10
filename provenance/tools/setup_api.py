# -*- coding: utf-8 -*-
"""一条命令配好外部 API key(含 Router 用的独立模型),并**联网自检**。

为什么有它:密钥原本要么设环境变量、要么手写 .secrets.json —— 对第一次接触项目的人
都不直观(实测:新人卡在这一步)。这里把"写 key + 选定后端 + 验证真能用"合成一条命令。
配好之后**跑批/起面不用再设任何环境变量**:`case01` 的 Router 会从 `.secrets.json`
读 `router_provider`(env 仍可临时覆盖,用于对照与排障)。

后端名单(deepseek / bigmodel / openrouter / vllm)与默认端点、默认模型、取 key 的
键名顺序都取自注入器的 `ROUTER_PROVIDERS` —— 与跑批用的是**同一张表**,这里不抄第二份。

用法::

    # 演示主力:DeepSeek(模型名取该 key 真的服务的那两个之一,见下表注释)
    python provenance/tools/setup_api.py --router deepseek --key <DeepSeek key>

    # 备选:BigModel 的独立模型(04 §五 / 06 §七 要求 Router 不是本地 Investment AI 自己)
    python provenance/tools/setup_api.py --router bigmodel --key <GLM key>

    # 最最后备选:OpenRouter
    python provenance/tools/setup_api.py --router openrouter --key <key>

    # 只自检**当前生效**的那个后端(不写文件):手册 §一 第 6 行拿这句当演示闸门
    python provenance/tools/setup_api.py --check
    python provenance/tools/setup_api.py --show         # 现在配的是谁(不打印 key)

    # 现场演示/无网:显式声明"这次故意用本地"(产物里记 local_by_config,不冒充外部)
    python provenance/tools/setup_api.py --router local

    # 自托管 OpenAI 兼容端点(vLLM 等):必须给 --base-url 与 --model
    python provenance/tools/setup_api.py --router vllm --base-url http://127.0.0.1:8101/v1 \
        --model qwen3-8b --key 随便填

    # 兼容旧用法:只配 OpenRouter 的 key
    python provenance/tools/setup_api.py --key sk-xxxx
    python provenance/tools/setup_api.py                # 交互式输入(不回显)

安全:key 只写本地(仓库根 .secrets.json,已 gitignore),权限 0600;**绝不打印** key。
写文件是**合并**而不是覆盖 —— 早先这里是整文件重写,配 BigModel 会把 OpenRouter 的 key 抹掉。
"""
import argparse
import getpass
import json
import os
import sys

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # <repo>/provenance
REPO = os.path.dirname(PKG)                                           # <repo>
if PKG not in sys.path:
    sys.path.insert(0, PKG)

# 闸门输出里有 ✓/✗,而 cmd.exe 把 stdout 重定向到文件时按系统 ANSI 码页(GBK)编码 ——
# GBK 没有这两个字符,于是"想看自检日志"反而让工具崩在最后一行。用项目里那份现成的
# 降级(`case01.safestream.tolerant_stdout`,编不出的换成 `?`),不在这里再写一遍
# reconfigure —— 这张表的教训就是"两处各写一份会漂"。
# 挂在 main() 入口而不是模块顶层:`case01/__init__.py` 有包根布局守卫,顶层 import 会把
# "只是 import 这个工具"(比如 serve_all 想复用它的合并写)也变成会崩的动作。

LOCAL = "local"          # --router local:显式"这次故意用本地",与"没配"区分(产物里两个词不同)


def _providers() -> dict:
    """Router 后端清单:**从注入器那一张表取**,这里不再抄一份。

    以前这里是独立字典,于是和跑批用的 `router_client_from_env()` 会漂
    (10-10 实测:DeepSeek 只能借 `vllm` 这个名配,而 `--check` 又不读
    `.secrets.json` 的 `router_provider` ⇒ 闸门测的是另一个后端)。
    """
    from case01.agents.llm import ROUTER_PROVIDERS
    return ROUTER_PROVIDERS


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
    """**合并**写入仓库根 .secrets.json(不是整文件覆盖);值为空串=删掉这个键。"""
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


def _env_overrides(skip=()) -> list:
    """此刻会**压过**本文件的环境变量(排障用,优先级最高)。

    为什么要单独报:跑批解析顺序是 env → `.secrets.json` → 表的默认值,所以在 shell 里
    留着一个 `CASE01_ROUTER_MODEL` 就会让"我明明改了文件"变成"改了不生效"。
    `skip` 用来排除**本工具自己**临时设的那个(见 `_self_check`,不排除就会误导人)。
    """
    skip = set(skip)
    return [n for n in ("CASE01_ROUTER_PROVIDER", "CASE01_ROUTER_BASE_URL",
                        "CASE01_ROUTER_MODEL")
            if n not in skip and os.environ.get(n, "").strip()]


def _configured_provider() -> str:
    from case01.agents.llm import router_provider_name
    return router_provider_name()


def _self_check(provider: str = "") -> int:
    """自检 = **走跑批用的同一份解析**,判据是"正文非空"而不是 HTTP 200。

    这里原来是另一条独立实现(`urllib` 直接 POST,`max_tokens=1` + "ping",回 200 就算过),
    它测不到真问题:推理端点把参数忽略掉照样 200,而思考链吃光预算时正文为空 ——
    Router 拿到空正文等于"拆不出问题"。当天(10-10)因此先出现"自检绿、跑批空",
    又出现"--check 不带 --router 就固定测 OpenRouter,而生效的其实是 DeepSeek"。

    `provider` 非空时临时把 `CASE01_ROUTER_PROVIDER` 指过去再解析 —— 手册 §一 第 6 行的
    "配谁就测谁"靠这个,而测的与跑批用的仍是**同一份代码**(不是第二套请求)。
    """
    from case01.agents.llm import (LOCAL_ROUTER_PROVIDERS, router_client_from_env,
                                   router_identity, router_provider_name)
    prev = os.environ.get("CASE01_ROUTER_PROVIDER")
    named = bool(provider and provider not in LOCAL_ROUTER_PROVIDERS)
    if named:
        os.environ["CASE01_ROUTER_PROVIDER"] = provider
    # 本工具自己临时指过去的那个不算"外部覆盖",否则等于自己吓自己
    skip = ("CASE01_ROUTER_PROVIDER",) if named else ()
    try:
        eff = router_provider_name()
        if not eff or eff in LOCAL_ROUTER_PROVIDERS:
            print("当前没配外部 Router(router_provider={!r})⇒ 跑批会回落本地模型,"
                  "04 §五 要的是独立 API 模型。一条命令配外部后端:"
                  "python provenance/tools/setup_api.py --router deepseek --key <key>"
                  "(bigmodel/openrouter/vllm 同法;名单与优先级见 --show)".format(eff))
            return 1
        try:
            client, _ident = router_client_from_env()
        except Exception as e:  # noqa: BLE001 —— 配置不全要原样说出来
            print("自检结果:失败 ✗ 按当前配置造不出客户端:{}: {}".format(
                type(e).__name__, str(e)[:200]))
            return 1
        if client is None:
            print("自检结果:失败 ✗ router_client_from_env() 返回空(配置为空)。")
            return 1
        # 产物里的 `router.executed_by.provider` 是**按 host 算的** "api"/"local"(见
        # `router_identity()`),配置名(deepseek/bigmodel/…)只在 `router_provider` 这一层。
        # 手册 §一 第 6 行认的是 `external_api`,所以两个都打,别让配置名替它背书。
        host_based = router_identity(client)
        print("{}:{} @ {}  (router_provider={} ⇒ 产物里 external_api={})".format(
            "点名测的后端" if named else "测的是当前生效后端",
            host_based.get("model"), host_based.get("host"), eff,
            host_based.get("provider") == "api"))
        if named and prev and prev != provider:
            print("  (跑批用的仍是 .secrets.json/env 里那个 {};"
                  "这句只是把本次自检指到 {})".format(prev, provider))
        ov = _env_overrides(skip)
        if ov:
            print("[!] 这些环境变量会压过 .secrets.json:{}".format(",".join(ov)))
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
            len(out), json.dumps(dict(host_based, router_provider=eff), ensure_ascii=False)))
        return 0
    finally:
        if named:
            if prev is None:
                os.environ.pop("CASE01_ROUTER_PROVIDER", None)
            else:
                os.environ["CASE01_ROUTER_PROVIDER"] = prev


def _show() -> int:
    """现在配的是谁 + **要给平台填的那张表** —— 全程不联网(联网自检是 `--check` 的事)。

    端点/模型是 env → .secrets.json → 表的默认值 三层按序取,所以这里**逐层列出**而不是
    只报赢家:这里再算一遍优先级就是第三份实现,而这张表的教训正是"两处各写一份会漂"。
    """
    from case_engine.llm import openrouter_source
    PROV = _providers()
    sec = _read_repo_secrets()
    eff = _configured_provider()
    print("Router 后端(router_provider):{}".format(
        eff or "(未配 ⇒ Router 回落本地,产物里是 local_fallback)"))
    # 下面这块是给对接方看的"填哪几个字段"清单:逐家列 key 的 json 键名/环境变量名/
    # 默认端点与模型。写这条的缘由:10-10 用户点明"这个接口到时候要对接平台,
    # 平台需要去填写来接入 API" —— 名单在代码里而对接方只能读文档,那就必须打得出来。
    print("\n可填的 Router 后端({} 个,**按演示优先级排**;{}=故意用本地,不接外部):"
          .format(len(PROV), LOCAL))
    for name, s in PROV.items():
        print("  --router {} —— {}".format(name, s.get("role", "")))
        print("     端点默认:{}".format(s["base"] or "(无默认 ⇒ 必须给 --base-url)"))
        print("     模型默认:{}".format(s["model"] or "(无默认 ⇒ 必须给 --model)"))
        print("     key 写进 .secrets.json:{}".format("/".join(s["key_json"])))
        print("     key 也可用环境变量:{}".format("/".join(s["key_env"])))
    print("     换端点/模型:--base-url/--model(落到 .secrets.json 的 "
          "router_base_url/router_model),或 env CASE01_ROUTER_BASE_URL/_MODEL(压过文件)")
    print("\n同一张表还服务 N3(Ethan 的台词):设 CASE01_ETHAN_PROVIDER=<上面任一名字> 即可,"
          "端点/模型/key 照这套取;要逐项覆盖就用 CASE01_ETHAN_BASE_URL/_MODEL/_API_KEY"
          "(它们优先级最高,且**不**读 CASE01_ROUTER_* 那几个 N7 专用覆盖项)。")
    spec = PROV.get(eff)
    if spec:
        print("\n当前生效那个后端的三层取值:")
        print("  取 key 的顺序:{}".format(
            " → ".join(list(spec["key_env"]) + list(spec["key_json"]))))
        for label, env_name, json_key, default in (
                ("端点", "CASE01_ROUTER_BASE_URL", "router_base_url", spec["base"]),
                ("模型", "CASE01_ROUTER_MODEL", "router_model", spec["model"])):
            print("  {}:env={} .secrets.json={} 默认={}".format(
                label, os.environ.get(env_name, "").strip() or "-",
                str(sec.get(json_key, "")).strip() or "-", default or "(无默认,必填)"))
    elif eff and eff != LOCAL:
        print("[!] router_provider={!r} 不在名单里 ⇒ 跑批会当场 ValueError、"
              "不静默回落本地(配错要看得见)。自检走不出这一步,先改回名单内的名字".format(eff))
    print("\nkey 落盘情况(只看有没有值,绝不打印):")
    for name in sorted({n for s in PROV.values() for n in s["key_json"]}):
        print("  {}:{}".format(name, "已配" if str(sec.get(name, "")).strip() else "未配"))
    print("  OpenRouter key 解析来源:{}".format(openrouter_source() or "(未配置)"))
    ov = _env_overrides()
    if ov:
        print("[!] 这些环境变量会压过 .secrets.json:{}".format(",".join(ov)))
    return 0


def _target_provider(args, PROV) -> str:
    """没点 `--router` 时配谁:当前生效那个(换 key 不必再点名),没配过则 OpenRouter。

    以前这里固定回落 openrouter,于是"已经配了 deepseek、只想换把 key"的人
    会把后端悄悄改回 OpenRouter —— 而他要的正是那把新 key 走 DeepSeek。
    """
    if args.router:
        return args.router
    eff = _configured_provider()
    return eff if eff in PROV else "openrouter"


def main(argv=None) -> int:
    from case01.safestream import tolerant_stdout

    tolerant_stdout()
    PROV = _providers()
    ap = argparse.ArgumentParser(
        description="配置/自检外部 API key 与 Router 后端"
                    "({})".format("/".join(list(PROV) + [LOCAL])))
    ap.add_argument("--key", default="", help="API key(不填则交互输入;不回显)")
    ap.add_argument("--router", default="", choices=list(PROV) + [LOCAL],
                    help="把 N7(Router)配成哪个后端(名单与优先级见 --show);"
                         "不填=沿用当前生效的后端;配成 local 表示故意用本地")
    ap.add_argument("--base-url", default="",
                    help="端点(只配 vllm 时必须;其他后端给了就覆盖默认值)")
    ap.add_argument("--model", default="",
                    help="模型名(只配 vllm 时必须;其他后端给了就覆盖默认值)")
    ap.add_argument("--check", action="store_true", help="只自检(用已配置的 key),不写文件")
    ap.add_argument("--show", action="store_true", help="只看现在配的是谁(不打印 key)")
    args = ap.parse_args(argv)

    if args.show:
        return _show()
    if args.check:
        return _self_check(args.router)

    provider = _target_provider(args, PROV)
    if provider == LOCAL:
        p = _merge_repo_secrets({"router_provider": LOCAL})
        print("已写入:{}  router_provider=local(Router 故意用本地)".format(p))
        print("要换成外部独立模型:python provenance/tools/setup_api.py "
              "--router deepseek --key <key>")
        return 0

    spec = PROV[provider]
    if not args.key.strip():
        args.key = getpass.getpass("粘贴 API key(不回显): ")
    key = args.key.strip()
    if not key:
        print("没给 key(--key)也没交互输入,什么也没做。")
        return 2
    base, model = args.base_url.strip(), args.model.strip()
    if spec.get("needs_base") and not (base and model):
        # 自托管后端没有有意义的默认值 ⇒ 少一个就拒绝写,而不是产一份跑不了的配置。
        print("--router {} 必须同时给 --base-url 与 --model"
              "(例如 --base-url http://127.0.0.1:8101/v1 --model qwen3-8b)。".format(provider))
        return 2
    patch = {"router_provider": provider, spec["key_json"][0]: key}
    # 覆盖项按"这次没给就清掉"处理:router_base_url/router_model 对**所有** provider 通用,
    # 留着上一家(比如 vllm 的 http://127.0.0.1:8101/v1)会让新后端的默认端点永远取不到。
    patch["router_base_url"] = base
    patch["router_model"] = model
    p = _merge_repo_secrets(patch)
    # 这行以前漏了 .format(p),于是把 "已写入:{}" 原样打出来 —— 新人最想知道的
    # "key 落到哪个文件"恰好空着(10-09 实配 bigmodel 时看到)。
    print("已写入:{}  (已 gitignore,不会入库;合并写,其他 provider 的 key 不动)".format(p))
    print("生效后端:router_provider={}  key→{}  端点={}  模型={}".format(
        provider, spec["key_json"][0],
        base or spec["base"] or "(未配)", model or spec["model"] or "(未配)"))
    if not base and not model:
        print("  (没点 --base-url/--model ⇒ 用该后端默认值,并清掉了 .secrets.json 里的旧覆盖)")
    ov = _env_overrides()
    if ov:
        print("[!] 这些环境变量会压过刚写的 .secrets.json:{}".format(",".join(ov)))

    print("自检中(走跑批那份解析,一次最小真实调用)…")
    rc = _self_check(provider)
    if rc == 0:
        print("\n下一步:python provenance/live_switch.py --start case01   (或 --start case00)")
        print("跑批不用再设环境变量;要临时换后端:CASE01_ROUTER_PROVIDER=<名字>(env 优先)。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
