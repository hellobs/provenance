# -*- coding: utf-8 -*-
"""暴露面守卫:这些服务**没有任何鉴权**,默认只许本机连。

2026-09-23 安全体检的结论(实测,不是推测):

- 5010 实时面有 3 个**写**端点,且都不需要任何凭证:
  `POST /api/goals`(改治理约束权重)、`POST /api/undo-intervention`(撤销干预)、
  `POST /api/reflections/mark`(标记反思并写档);
- 8060 配置工具有 13 个写端点,含 `POST /api/scenario/save`、`POST /api/run/execute`、
  `POST /api/agent/delete`;
- CORS 原来默认 `*`:跨源读的口子一开,**任意网页**都能把成品记录(研究资料)读走;
- 全栈没有 Host 头校验,也没有登录。

所以策略是:
1. 绑定地址默认回环;要绑非回环地址,必须显式声明 `LIVE_ALLOW_REMOTE=1`,
   并把"暴露了什么"当场打出来(不许静默);
2. 跨源白名单默认只给本机来源,平台侧要跨源取数就显式给域名;
3. 跨机对接优先"不要暴露端口":同机部署、只读反向代理、或 SSH 隧道。
"""

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "")
REMOTE_ENV = "LIVE_ALLOW_REMOTE"

# 把这几个写端点列清楚:开放端口前必须先知道自己在暴露什么
WRITE_ENDPOINTS = (
    "5010  POST /api/goals              (改治理约束的期望目标权重)",
    "5010  POST /api/undo-intervention  (撤销一次专家干预)",
    "5010  POST /api/reflections/mark   (标记反思并写档)",
    "8060  POST /api/scenario/save、/api/run/execute、/api/agent/delete 等 13 个写端点",
)


def is_loopback(host: str) -> bool:
    """回环地址(含空串 = 各框架的默认本机绑定)。"""
    return str(host or "").strip().lower() in LOOPBACK_HOSTS


def require_explicit_remote(host: str, where: str = "该服务") -> None:
    """绑非回环地址前必须显式声明;否则直接拒绝启动并说明原因。

    为什么是"拒绝"而不是"警告一句":这些面没有鉴权,开出去就是同网段人人可读写;
    一句 warning 在日志里很容易被忽略,而端口已经开了。
    """
    import os

    if is_loopback(host):
        return
    if os.environ.get(REMOTE_ENV) == "1":
        return
    raise SystemExit(
        "拒绝把「{where}」绑到 {host}:这些面没有任何鉴权(5010 还有写端点),默认只允许本机。\n"
        "  确实要给外部机器连(平台侧对接),先读《给平台侧_嵌入与数据接入》的安全一节,再显式声明:\n"
        "    PowerShell: $env:{env}='1'\n"
        "    然后重新起(命令里的 host 仍写 {host})\n"
        "  更稳的做法是**不要暴露端口**:同机部署、只读反向代理(只代理 /embed/* 与 GET /api/*)、"
        "或 SSH 隧道把 5010 转给对方。".format(host=host, where=where, env=REMOTE_ENV))


def exposure_lines(host: str, port: int = 5010) -> list:
    """开放端口前把暴露面写出来(谁、能做什么)。"""
    lines = ["把 {}:{} 开给了非本机 —— 以下面**没有任何鉴权**:".format(host, port)]
    lines += ["  " + w for w in WRITE_ENDPOINTS]
    lines.append("  GET 面(成品记录/反思/问题分流)同样人人可读;CORS 白名单见 EMBED_ALLOW_ORIGINS")
    return lines


def resolve_embed_origins(value: str) -> list:
    """解析跨源白名单。

    默认值**不是** `*`(2026-09-23 改):服务没有鉴权,`*` 等于把研究资料开给任意网页
    (浏览器里一个 fetch 就能读走)。默认只给本机来源;平台侧跨源取数请显式设
    `EMBED_ALLOW_ORIGINS=https://<平台域名>`。iframe 嵌入本身不需要 CORS,不受影响。
    """
    items = [o.strip() for o in str(value or "").split(",") if o.strip()]
    if items:
        return items
    return ["http://127.0.0.1:5010", "http://localhost:5010"]
