# -*- coding: utf-8 -*-
"""节点触发条件:让 story 事件"到点必发"(引擎通用)。

通过 mavis 提供的通用扩展点(`Simulator.register_condition`)注册,不改框架主逻辑。
仅在真实运行时调用(需要 import mavisframework),dry-run 路径不 import。

通用化要点(2026-09-20,从原 case 仓 injector/conditions 模块迁移):
- 条件标签名参数化(缺省中性 "engine_node"),不再硬编码具体场景名。
"""


def install_node_condition(node_state: dict, tag: str = "engine_node"):
    """注册节点条件:事件的 condition.<tag> 等于当前节点 id 时触发。

    node_state: 驱动维护的可变 dict,形如 {"id": "node-1"}。
    tag: condition 里的键名(场景可自定义,缺省中性)。
    返回 Simulator 类,便于调用方断言注册结果。
    """
    from mavisframework.runtime.simulator import Simulator

    @Simulator.register_condition(tag)
    def _check_node(game, ev):
        condition = ev.get("condition") or {}
        return str(condition.get("node_id", "")) == str(node_state.get("id", ""))

    return Simulator