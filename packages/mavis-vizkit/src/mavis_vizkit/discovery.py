# -*- coding: utf-8 -*-
"""插件发现:通过 entry point 组 `mavis_vizkit.plugins` 加载第三方插件。

显式调用才生效(不在 import 时自动加载,保持零副作用)。
**不允许静默**:元数据读取失败、某个 entry point 加载失败都记日志后继续,
否则表现就是"插件明明装了却没生效,且没有任何提示"。
"""
import logging
from typing import List

GROUP = "mavis_vizkit.plugins"
log = logging.getLogger("mavis_vizkit.discovery")


def load_entry_point_plugins() -> List[str]:
    """读取 entry point 组并注册,返回新注册的插件名(已存在的跳过)。"""
    loaded = []
    try:
        from importlib.metadata import entry_points
    except ImportError:  # Python <3.10(本包需要 >=3.12,仅防御)
        log.warning("importlib.metadata 不可用,跳过 entry point 插件发现")
        return loaded
    try:
        eps = entry_points()
        group = eps.select(group=GROUP) if hasattr(eps, "select") else \
            eps.get(GROUP, [])
    except Exception:
        log.warning("读取 entry point 组 %s 失败,跳过插件发现", GROUP, exc_info=True)
        return loaded
    from . import names, register

    known = set(names())
    for ep in group:
        if ep.name in known:
            continue
        try:
            factory = ep.load()
        except Exception:
            log.warning("entry point 插件 %s 加载失败,已跳过", ep.name, exc_info=True)
            continue
        register(ep.name, factory)
        loaded.append(ep.name)
        log.info("已注册 entry point 插件:%s", ep.name)
    return loaded