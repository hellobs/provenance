# -*- coding: utf-8 -*-
"""引擎策略层(引擎通用):引擎 = 可注入、可组合的「运行策略」。

设计(策略模式 + 注册表工厂):
- 场景 `scenario.yaml` 是**纯数据**;`meta.engine` 只是它**推荐的默认引擎**,可被运行时
  `requested` 覆盖 → 同一份场景可与不同引擎自由搭配。
- 每个引擎是一个 `EngineStrategy`:统一 `supports(scenario)` / `describe()` / `run()` 契约。
- `supports()` 用「本引擎运行所需的最小输入段是否齐备」判定候选,而非唯一归属 ——
  允许对同一场景枚举「哪些引擎能跑」。
- 新增引擎 = 写一个策略类 + 在 `engines.register` 挂一个 builder,零改动消费端。

引擎实例只有 `engine_id` 与判定/描述逻辑,不持状态;`run()` 目前尚未接线
(pipeline / mavis 桥),故一律抛清晰错误,绝不造假假装能跑。
"""
from typing import Any, Dict, List


class EngineStrategy:
    """引擎策略抽象接口。场景经鸭子类型访问,不 import 具体 config 类型。"""
    engine_id: str = ""

    def supports(self, scenario) -> bool:
        raise NotImplementedError

    def describe(self) -> Dict[str, str]:
        raise NotImplementedError

    def run(self, scenario, **kw: Any) -> Any:
        raise NotImplementedError("引擎 {!r} 的运行尚未接线;目前仅 supports/describe 可用".format(
            self.engine_id))


class ExperimentEval(EngineStrategy):
    """受控实验/评估引擎:消费 scenario.branch 为核心。"""
    engine_id = "experiment-eval"

    def supports(self, scenario) -> bool:
        # 需要一条可判定的分支规则(实验线核心输入)
        return bool(getattr(scenario, "branch", None))

    def describe(self) -> Dict[str, str]:
        from case_engine.engines import ENGINES
        return {"engine": self.engine_id, **ENGINES.get(self.engine_id, {})}

    def run(self, scenario, **kw: Any) -> Any:
        raise NotImplementedError("experiment-eval 的运行(pipeline 通用化)尚未接线")


class SandboxValue(EngineStrategy):
    """生成式价值权重沙盒:消费 scenario.custom.sandbox_params;原语需桥 mavisframework。"""
    engine_id = "sandbox-value"

    def supports(self, scenario) -> bool:
        custom = getattr(scenario, "custom", None) or {}
        return bool(custom.get("sandbox_params"))

    def describe(self) -> Dict[str, str]:
        from case_engine.engines import ENGINES
        return {"engine": self.engine_id, **ENGINES.get(self.engine_id, {})}

    def run(self, scenario, **kw: Any) -> Any:
        raise NotImplementedError("sandbox-value 需桥接 mavisframework 沙盒,尚未实现")


# 默认内置：engine_id → 无状态策略构造器。场景运行所选引擎可从注册表工厂产出。
BUILTIN_STRATEGIES = {
    "experiment-eval": lambda scenario: ExperimentEval(),
    "sandbox-value": lambda scenario: SandboxValue(),
}