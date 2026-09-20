# -*- coding: utf-8 -*-
"""case_engine: 场景引擎通用层(不含任何场景业务词)。

从原 case 仓抽离的通用逻辑 —— 状态机 / 分支判定 / 角色扮演 / 检索 / 反思 /
一致性 / 编排 / mavis 桥接 / 记录等。场景差异全部通过 ScenarioConfig
(scenario.yaml) 注入;各 case 只是传配置的壳。
"""