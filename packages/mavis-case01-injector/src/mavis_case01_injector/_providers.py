# -*- coding: utf-8 -*-
"""过渡期 provider seam(阶段 3 增量 2,见设计稿附录)。

集中包内对 case01 的全部延迟回退:**这是本包唯一允许出现 "case01" 字样的模块**。
设计终态是把 reflection/prompt 常量等收编或改为调用方注入,届时删本文件;
在那之前,裸包用户缺 provider 会得到清晰的 ImportError,而不是隐式依赖。
"""
import os


def _import_case01():
    import case01
    return case01


def import_reflection():
    """case01.reflection(8 维反思 + Router 的完整实现,留在 provenance)。"""
    try:
        from case01 import reflection
        return reflection
    except ImportError as e:
        raise ImportError(
            "reflection provider 不可用:反思/路由的完整实现留在 case01;"
            "裸包用户请经 run_pipeline(reflection_mod=...) 注入等价实现") from e


def import_orchestrator():
    """case01.orchestrator(事实层的价格常量 BUY_PRICE_A 等)。"""
    try:
        from case01 import orchestrator
        return orchestrator
    except ImportError as e:
        raise ImportError(
            "orchestrator provider 不可用:事实层价格常量留在 case01;"
            "裸包用户请自行注入等价常量") from e


def import_mavis_provider():
    """case01.agents.mavis_provider(Case01SafeProvider 安全包装)。

    与 reflection/orchestrator 不同,这里缺 provider 是**合法降级**(裸包用户
    不需要 case01 安全包装):ImportError → 返回 None,由调用方跳过包装。
    """
    try:
        from case01.agents import mavis_provider
        return mavis_provider
    except ImportError:
        return None


def import_case01_agents_secrets():
    """case01.agents.secrets(OpenRouter key 落盘读取)。"""
    from case01.agents import secrets
    return secrets


def default_scenario() -> str:
    """case01/injector/scenario 目录(节点序列的场景数据);裸包返回空串。"""
    try:
        case01 = _import_case01()
        return os.path.join(os.path.dirname(case01.__file__), "injector", "scenario")
    except ImportError:
        return ""


def default_scenario_yaml() -> str:
    """cases/case01_stock/scenario.yaml(判定提示词的权威来源);找不到/裸包返回空串。"""
    try:
        case01 = _import_case01()
    except ImportError:
        return ""
    cur = os.path.dirname(case01.__file__)
    for _ in range(3):
        cand = os.path.join(cur, "cases", "case01_stock", "scenario.yaml")
        if os.path.isfile(cand):
            return cand
        cur = os.path.dirname(cur)
    return ""


def default_financial_data_dir() -> str:
    """case01/data/financial(检索资料目录);裸包返回空串。"""
    try:
        case01 = _import_case01()
        return os.path.join(os.path.dirname(case01.__file__), "data", "financial")
    except ImportError:
        return ""
