# -*- coding: utf-8 -*-
"""mavis_case01_injector:注入器 + 世界事实层(阶段 3 抽包,自封闭单元)。

对外公开面与原 case01/injector 一致(MavisBridge / default_nodes / to_case01_record
等);硬依赖 mavisframework 与 mavis-vizkit(bridge 无条件 import);场景资源根与
reflection/价格常量经 _providers 或参数注入(见 README)。"""
from mavis_case01_injector.bridge import MavisBridge
from mavis_case01_injector.nodes import NodeSpec, default_nodes, nodes_from_timeline
from mavis_case01_injector.record import CASE01_TOP_KEYS, to_case01_record

__all__ = ["MavisBridge", "NodeSpec", "default_nodes", "nodes_from_timeline",
           "CASE01_TOP_KEYS", "to_case01_record"]
