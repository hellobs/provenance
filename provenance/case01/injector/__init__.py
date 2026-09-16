# -*- coding: utf-8 -*-
"""case01 injector:把 case01 的节点剧本作为 mavis 的事件参数约束注入。

设计依据:`../docs/case01_over_mavis_设计说明.md`、`../docs/case01_over_mavis_执行计划.md`。

分工:
- 事实(日期/价格/公告/持仓)由 case01 的 world 层维护,junction 只把"已释放"的部分转成 mavis 的 story 事件;
- 临时状态走 mavis 的通用外部状态入口;关键交互走通用交互请求入口;
- 每个节点推进 1 步;关键节点若未发生交互,在节点内重试。

本包不 import mavisframework(dry_run 路径完全独立),真实运行在 bridge 内懒加载。
"""
from .bridge import MavisBridge
from .nodes import NodeSpec, default_nodes, nodes_from_timeline
from .record import CASE01_TOP_KEYS, to_case01_record

__all__ = [
    "CASE01_TOP_KEYS", "MavisBridge", "NodeSpec", "default_nodes",
    "nodes_from_timeline", "to_case01_record",
]
