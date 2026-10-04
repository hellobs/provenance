# -*- coding: utf-8 -*-
"""stdout 容错与外部命令取文本:让"给人看的日志"没资格打死一次生产跑。

为什么有这个模块(2026-10-04,批次 261004-123726-h120/-035):
`batch_run` 把子进程 `python -m case01.run` 的 stdout 重定向进 `.log` 文件,
子进程里的文本流却仍按 Windows locale(GBK)编码。模型答案里出现一个 `▶`
(U+25B6)就抛 `UnicodeEncodeError`,栈顶在 `orchestrator.run_case01` 的
`log(answer + "\n")` —— 一条已经烧了十几分钟 GPU 的 run 死在 T0,只能靠重试
再跑一遍。日志只是给人看的,显示失败不该决定一次跑的生死。

同一个 locale 口径还从**反方向**咬人:本仓约定一律 `-X utf8` 运行,而
`subprocess.run(..., text=True)` 此时按严格 UTF-8 解码外部命令的输出;
netstat / 未设 `PYTHONIOENCODING` 的自家 python 子进程写的都是 GBK 字节,
读取线程抛 `UnicodeDecodeError` 后 `communicate()` 交回 **stdout=None**
(本机 2026-10-04 实测复现)。调用方 `(proc.stdout or "")` 于是把整段输出
静默吞掉,`re.search(pat, None)` 直接 TypeError —— 与 live_switch 当年踩的是
同一个坑,只是发生在别的调用点。

所以本模块给三件事提供**单一实现**,免得各处各写一份再各自漂移:
- `tolerant_stdout()`:CLI 入口挂一次,进程内所有 print 编不了的字降级成 `?`;
- `utf8_env()`:起 python 子进程时钉 `PYTHONIOENCODING=utf-8`;
- `run_text()`:取外部命令输出,保证拿到的是字符串而不是 None。
"""
import os
import subprocess
import sys


def tolerant_stdout(stream=None):
    """把流的编码错误策略改成 replace,返回**可直接 print 的流**。

    原地 reconfigure(不改编码,只改 errors):locale 是 GBK 时仍写 GBK,
    编不了的字变 `?`;真崩溃的原因因此消失,而控制台可读性不受影响。

    改不动就原样返回:被 pytest capture、`sys.stdout=None` 之类的替身对象
    没有可用的 `reconfigure`,此时守卫退化为"尽力而为"—— 调用方拿到的一定
    还是原来那个流,不会被悄悄换成另一个对象。
    """
    target = stream if stream is not None else sys.stdout
    try:
        target.reconfigure(errors="replace")
    except (AttributeError, OSError, ValueError):
        pass
    return target


def utf8_env(base=None):
    """返回带 `PYTHONIOENCODING=utf-8` 的**环境副本**(不改 os.environ)。

    给 python 子进程用:父进程按 utf-8 读日志/输出,子进程也必须按 utf-8 写,
    否则中文进管道就是 GBK 字节,父进程严格解码只能拿到 None。
    外部原生命令(netstat/PowerShell)不吃这个变量,由 `run_text` 的
    `errors="replace"` 兜住。
    """
    env = dict(os.environ if base is None else base)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_text(cmd, cwd=None, env=None, timeout=None):
    """跑外部命令并保证 `stdout`/`stderr` 是字符串(取不到给空串)。

    为什么不直接用 `text=True`:见模块 docstring —— `-X utf8` 下严格解码会把
    GBK 字节变成 `stdout=None`,而 None 既能把正则匹配炸成 TypeError,又能让
    `(proc.stdout or "")` 静默吞掉整段诊断输出。`errors="replace"` 保住 ASCII
    部分(端口/PID/退出码),中文表头换成 U+FFFD 不影响判断。
    返回值仍是 CompletedProcess,调用方照旧读 returncode。
    """
    proc = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    proc.stdout = proc.stdout or ""
    proc.stderr = proc.stderr or ""
    return proc

