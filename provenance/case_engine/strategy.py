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
        # 时间线装配:分支 → 选一条声明式 timeline → 节点序列(确定性,不调 LLM)。
        timeline = _assemble_timeline(scenario, branch, result)
        if timeline:
            result["timeline"] = timeline
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

    def run(self, scenario, base: str = "", out_dir: str = "",
            **kw: Any) -> Dict[str, Any]:
        """数据驱动装配预检线:核验 scenario 声明是否能指向 mavis 可装配的资产/参数。

        `run_type = "assembly-check"` —— 不跑沙盒(移动/感知/调度/价值演化需 mavis 实跑),
        只做**确定性静态预检**:把 `custom.scenario_assets`(相对 provenance 根)逐条解析到
        绝对路径,校验存在性 + JSON 可解析性;校验角色数、价值权重(governance/initial)
        是否齐备;校验 `sandbox_params` 是否声明。绝不假装产出 checkpoints。
        """
        import json
        import os

        from case_engine.scenarios import default_cases_root

        base = base or os.path.dirname(default_cases_root())
        custom = getattr(scenario, "custom", None) or {}
        assets = custom.get("scenario_assets") or {}
        params = custom.get("sandbox_params") or {}
        tendency = custom.get("value_tendency") or {}

        checks: Dict[str, Dict[str, Any]] = {}
        # 1. 必填声明段
        for key, present in (("sandbox_params", bool(params)),
                             ("scenario_assets", bool(assets)),
                             ("value_tendency", bool(tendency))):
            checks[key] = {"ok": present,
                           "detail": "已声明" if present else "缺失"}

        # 2. 资产存在性 + JSON 可解析性(逐个,不拖垮整体)
        json_assets = ("story", "relationships", "maze", "governance")
        asset_checks: Dict[str, Dict[str, Any]] = {}
        _all_paths_ok = True
        for key in ("story", "relationships", "maze", "agents", "governance"):
            rel = assets.get(key, "")
            if not rel:
                asset_checks[key] = {"ok": False, "detail": "未声明路径"}
                _all_paths_ok = False
                continue
            abs_p = os.path.normpath(os.path.join(base, rel))
            exists = os.path.exists(abs_p)
            parse_ok, parse_note = True, ""
            if exists and key in json_assets:
                try:
                    with open(abs_p, encoding="utf-8") as f:
                        json.load(f)
                except Exception as exc:  # noqa: BLE001 —— 只记录,不中断预检
                    parse_ok, parse_note = False, type(exc).__name__
            if key == "agents":
                ok = exists and os.path.isdir(abs_p)
            else:
                ok = exists and parse_ok
            asset_checks[key] = {
                "ok": bool(ok),
                "detail": "存在" if exists else "缺失",
                "path": rel,
            }
            if key in json_assets and exists and not parse_ok:
                asset_checks[key]["detail"] = "JSON 无法解析: {}".format(parse_note)
            if not ok:
                _all_paths_ok = False
        checks["assets"] = {"ok": _all_paths_ok, "detail": asset_checks}

        # 3. 行政:角色数 + 每角色的价值权重(governance/initial)是否齐备
        #    权重键用 display_name(如 "AI Advisor"),角色 id 是 snake_case;两者都查。
        gov = tendency.get("governance") or {}
        init = tendency.get("initial_tendency") or {}
        role_checks: Dict[str, Dict[str, Any]] = {}
        for r in getattr(scenario, "roles", []):
            rid = r.id
            names = {rid, r.display_name} if getattr(r, "display_name", "") else {rid}
            has = any(n in gov or n in init for n in names)
            role_checks[rid] = {"ok": bool(has), "detail": "有价值权重" if has else "无权重"}
        checks["roles"] = {"ok": all(v["ok"] for v in role_checks.values())
                           and len(role_checks) >= 2, "detail": role_checks}

        all_ok = all(v["ok"] for v in checks.values())
        result: Dict[str, Any] = {
            "engine": self.engine_id,
            "run_type": "assembly-check",   # 装配预检,非沙盒实跑
            "scenario": scenario.case_id,
            "base": base,
            "checks": checks,
            "all_ok": all_ok,
            "note": "可装配" if all_ok else "存在缺失,需先补齐后再进 mavis 实跑",
        }
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            fn = os.path.join(out_dir, "{}_sandbox.json".format(scenario.case_id))
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            result["artifact"] = fn
        return result


# 默认内置：engine_id → 无状态策略构造器。场景运行所选引擎可从注册表工厂产出。
BUILTIN_STRATEGIES = {
    "experiment-eval": lambda scenario: ExperimentEval(),
    "sandbox-value": lambda scenario: SandboxValue(),
}


def _assemble_timeline(scenario, branch: str, result: dict) -> List[dict]:
    """分支 → 选声明式时间线 → 节点序列(确定性,不调 LLM)。

    场景未声明 timeline 时返回空(保持旧最小行为)。互动的 focus 文案由场景
    `timeline.interactions` 提供(引擎不内置业务词);T0 证据与分支无关,并入首日。
    返回节点 dict 列表;任何异常回退为空,不让分支主环因时间线而崩。
    """
    try:
        from case_engine.config import merge_t0, select_timeline
        from case_engine.nodes import nodes_from_timeline

        sel = select_timeline(scenario, branch)
        if sel is None:
            return []
        line_id, tl, header = sel
        merged = merge_t0(tl, header["t0"], header["start_date"])
        interactions = header["interactions"] or {}
        templates = {}
        if isinstance(interactions, dict) and interactions.get("first"):
            templates["first"] = dict(interactions["first"])
            if interactions.get("final"):
                templates["final"] = dict(interactions["final"])
        nodes = nodes_from_timeline(
            merged,
            roles=header["role_ids"] or None,
            key_nodes="first_last",
            final_date=header["final_date"] or "",
            append_final=True,
            interaction_templates=templates or None,
        )
        return [n.to_dict() for n in nodes]
    except Exception:  # noqa: BLE001 —— 时间线装配失败不该让整个 run 崩
        return []