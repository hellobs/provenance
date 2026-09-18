# -*- coding: utf-8 -*-
"""插件发现:通过 entry point 组 `mavis_vizkit.plugins` 加载第三方插件。

显式调用才生效(不在 import 时自动加载,保持零副作用)。
"""
from typing import List

GROUP = "mavis_vizkit.plugins"


def load_entry_point_plugins() -> List[str]:
    """读取 entry point 组并注册,返回新注册的插件名(已存在的跳过)。"""
    loaded = []
    try:
        from importlib.metadata import entry_points
    except ImportError:  # Python <3.10(本包需要 >=3.12,仅防御)
        return loaded
    try:
        eps = entry_points()
        group = eps.select(group=GROUP) if hasattr(eps, "select") else \
            eps.get(GROUP, [])
    except Exception:
        return loaded
    from . import names, register

    known = set(names())
    for ep in group:
        if ep.name in known:
            continue
        try:
            factory = ep.load()
        except Exception:
            continue
        register(ep.name, factory)
        loaded.append(ep.name)
    return loaded