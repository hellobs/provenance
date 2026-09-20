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

    def run(self, scenario, input_text: str = "", out_dir: str = "",
            **kw: Any) -> Dict[str, Any]:
        """免 LLM 最小真跑线:用 scenario 数据 + 一条给定回答,产出分支/状态/一致性。

        `run_type = "rule-dryrun"` —— 这是实验线第一段可运行的闭环(分支路由 +
        状态构造 + 一致性快筛),不调用 LLM;LLM 表达/反思/检索留待 pipeline 接入,
        故绝不伪装成完整 `run.json`。返回结果 dict,`out_dir` 给出则落盘 JSON。
        """
        from case_engine.branch import RuleBranchRouter
        from case_engine.config import branch_args, consistency_signals
        from case_engine.consistency import quick_scan
        from case_engine.world import EntityState

        input_text = (input_text or "").strip()
        if not input_text:
            raise ValueError("experiment-eval.run 需要 input_text(受判的对象回答)")
        bargs = branch_args(scenario)
        router = RuleBranchRouter(default=bargs["default_branch"],
                                  rules=bargs["rules"],
                                  fallback_map=bargs["fallback_map"])
        branch = router.classify(input_text)
        st = EntityState(scenario.state_schema)
        ai_ids = tuple(r.id for r in scenario.roles if r.type == "ai_tool")
        ai = ai_ids[0] if ai_ids else "ai"
        start_date = str((scenario.meta or {}).get("start_date") or "")
        rec = {
            "scenario": scenario.case_id,
            "start_date": start_date,
            "branch": branch,
            "turns": [{"speaker": ai, "date": start_date, "text": input_text}],
        }
        verdict, reason = quick_scan(rec, consistency_signals(scenario),
                                     speaker_ids=ai_ids or ("ai",))
        result: Dict[str, Any] = {
            "engine": self.engine_id,
            "run_type": "rule-dryrun",      # 免 LLM 最小线;LLM 线留待 pipeline 接入
            "scenario": scenario.case_id,
            "branch": branch,
            "state": st.to_dict(),
            "consistency": {"verdict": verdict, "reason": reason},
        }
        if out_dir:
            import json
            import os
            os.makedirs(out_dir, exist_ok=True)
            fn = os.path.join(out_dir, "{}_exp.json".format(scenario.case_id))
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            result["artifact"] = fn
        return result


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