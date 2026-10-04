# -*- coding: utf-8 -*-
"""stdout 容错:让"给人看的日志"没资格打死一次生产跑。

为什么有这个模块(2026-10-04,批次 261004-123726-h120/-035):
`batch_run` 把子进程 `python -m case01.run` 的 stdout 重定向进 `.log` 文件,
子进程里的文本流却仍按 Windows locale(GBK)编码。模型答案里出现一个 `▶`
(U+25B6)就抛 `UnicodeEncodeError`,栈顶在 `orchestrator.run_case01` 的
`log(answer + "\n")` —— 一条已经烧了十几分钟 GPU 的 run 死在 T0,只能靠重试
再跑一遍。日志只是给人看的,显示失败不该决定一次跑的生死。

用法:CLI 入口在 parse_args 之前调一次 `tolerant_stdout()`,进程内所有
`print` 从此降级成 `?` 而不是抛异常。库内调用方(测试)不需要它。
"""
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
