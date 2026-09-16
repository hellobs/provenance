# -*- coding: utf-8 -*-
"""case01_node 自定义触发条件:让 story 事件"到点必发"。

这用的是 mavis 提供的通用扩展点(`Simulator.register_condition`),不改框架主逻辑。
仅在真实运行时调用(需要 import mavisframework),dry-run 路径不 import。
"""


def install_case01_node_condition(node_state: dict):
    """注册 `case01_node` 条件:事件的 condition.node_id 等于当前节点 id 时触发。

    node_state: 驱动维护的可变 dict,形如 {"id": "node-1"}。
    返回 Simulator 类,便于调用方断言注册结果。
    """
    from mavisframework.runtime.simulator import Simulator

    @Simulator.register_condition("case01_node")
    def _check_case01_node(game, ev):
        condition = ev.get("condition") or {}
        return str(condition.get("node_id", "")) == str(node_state.get("id", ""))

    return Simulator
