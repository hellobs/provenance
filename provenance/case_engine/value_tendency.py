# -*- coding: utf-8 -*-
"""价值权重「声明 → 资产」的落盘器(2026-09-21:收口 ②)。

分工(与 `config.value_tendency_plan()` 配套):
- `value_tendency_plan()` 只**算**:声明能不能落到每个角色、该写成什么(`materialize`);
- 本模块**落盘**:把 `materialize` 的结果写进 `governance.json` 与各 `agent.json` 的
  `initial_tendency`,并如实报告"改了哪些、哪些本来就一致"。

三条硬规矩:
1. **声明为准**:资产与声明不一致时,以声明覆盖资产(单向:scenario.yaml → 资产)。
2. **默认不写**:`apply=False` 只做 dry-run,逐项报告;要写必须显式 `apply=True`
   (与项目"不允许静默"一致:任何写操作都要能被看见)。
3. **不动排版**:按原文件的缩进宽度与末尾换行原样写回,只改该改的值 ——
   否则每次落盘都会产生一个巨大的 diff,没人敢看。

只有 `case_engine` 一侧会写这里的东西(案例侧工具也应当调本模块,而不是自己再写一份)。
"""
from __future__ import annotations

import io
import json
import os
import re
from typing import Any, Dict, Optional

from case_engine.config import Config, value_tendency_plan


def _detect_indent(text: str) -> int:
    """从第二行(第一个真键)猜缩进宽度;猜不到按 4。"""
    for line in text.split("\n")[1:6]:
        m = re.match(r"^(\s+)\S", line)
        if m:
            return len(m.group(1))
    return 4


def _dump_like(path: str, obj: Any) -> str:
    """按原文件的缩进与末尾换行风格序列化(不动排版)。"""
    try:
        with io.open(path, encoding="utf-8") as f:
            old = f.read()
    except OSError:
        old = ""
    indent = _detect_indent(old) if old else 4
    body = json.dumps(obj, ensure_ascii=False, indent=indent)
    if old.endswith("\n"):
        body += "\n"
    return body


def _load_json(path: str) -> Optional[Any]:
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _same(a: Any, b: Any, tol: float = 1e-12) -> bool:
    """数值容差比较(递归):权重是长小数,JSON 往返后要能判等。"""
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_same(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    return a == b


def _load_interventions(base: str, path: str = "") -> list:
    """读运行时干预审计(默认 <base>/results/checkpoints/interventions.json)。

    读不到就回空 —— 但不能因此假装"没有干预":调用方据"文件是否存在"来区分
    "没有干预" 与 "查不到审计",见 explain_divergence() 的 note 字段。
    """
    p = path or os.path.join(base, "results", "checkpoints", "interventions.json")
    data = _load_json(p)
    return [x for x in (data or []) if isinstance(x, dict)]


def explain_divergence(current: dict, declared: dict, interventions: list,
                       tol: float = 1e-9) -> Dict[str, Any]:
    """逐角色判断:资产与声明的不一致,能不能被运行时干预解释。

    返回 {role: {"diverged": bool, "explained": bool, "by": "谁 @ 何时"}}。
    解释成立的判据:该角色最后一条 operator=expert 的 new_constraints 与**当前资产**一致
    (即"资产的现状就是那次干预的结果")。
    """
    out: Dict[str, Any] = {}
    for role, want in (declared or {}).items():
        cur = (current or {}).get(role)
        if cur is None:
            out[role] = {"diverged": True, "explained": False, "by": ""}
            continue
        diverged = not _same(cur, want, tol)
        if not diverged:
            out[role] = {"diverged": False, "explained": False, "by": ""}
            continue
        last = None
        for rec in interventions or []:
            if str(rec.get("agent", "")) == role and rec.get("operator") != "undo":
                last = rec
        ok = bool(last) and _same(dict(last.get("new_constraints") or {}), cur, tol)
        out[role] = {"diverged": True, "explained": ok,
                     "by": "{} @ {}".format(last.get("operator", "?"), last.get("time", "?"))
                     if ok else ""}
    return out


def materialize(cfg: Config, base: str, apply: bool = False,
                interventions_path: str = "",
                allow_overwrite_interventions: bool = False) -> Dict[str, Any]:
    """把声明里的价值权重落到资产上;返回逐项报告(默认 dry-run)。

    `base`:资产相对路径的根(与 `SandboxValue.run(base=...)` 同一口径)。
    """
    plan = value_tendency_plan(cfg)
    report: Dict[str, Any] = {
        "case_id": getattr(cfg, "case_id", "") or "",
        "apply": bool(apply),
        "present": plan["present"],
        "in_sync": [], "changed": [], "written": [], "problems": [],
    }
    if not plan["present"]:
        report["problems"].append("声明里没有 world.value_tendency:拒绝落盘(不凭空造权重)")
        report["ok"] = False
        return report
    if plan["roles_missing_ledger"]:
        report["problems"].append(
            "这些角色缺账,拒绝落盘(先补声明): {}".format(plan["roles_missing_ledger"]))
        report["ok"] = False
        return report
    for key in plan["unknown_keys"]:
        report["problems"].append(
            "声明里多出的角色键不属于任何已声明角色,未落盘: {}".format(key))

    assets = getattr(cfg, "assets", None) or {}
    mat = plan["materialize"]

    # 1) governance.json(路径由声明给)
    rel_gov = assets.get("governance") or ""
    gov_path = os.path.normpath(os.path.join(base, rel_gov)) if rel_gov else ""
    want_gov = {"roles": mat["governance.json"]}
    if gov_path:
        cur = _load_json(gov_path)
        cur_roles = (cur or {}).get("roles") if isinstance(cur, dict) else None
        if _same(cur, want_gov):
            report["in_sync"].append({"asset": rel_gov, "kind": "governance"})
        else:
            # 先问一句:这份偏离能不能被运行时干预解释?能 → 默认不许覆盖(否则抹掉专家动作)
            inter = _load_interventions(base, interventions_path)
            expl = explain_divergence(cur_roles or {}, mat["governance.json"], inter)
            blocked = {r: v for r, v in expl.items() if v["explained"]}
            if blocked:
                report["runtime_interventions"] = blocked
                if not allow_overwrite_interventions:
                    report["problems"].append(
                        "治理资产已被运行时干预改写,拒绝覆盖(要重置回声明基线请显式 --force): "
                        + "; ".join("{} ({})".format(r, v["by"]) for r, v in blocked.items()))
                    report["ok"] = False
                    report["changed"].append({"asset": rel_gov, "kind": "governance",
                                              "skipped": "有运行时干预"})
                    gov_path = ""      # 跳过本文件的写入,继续处理 initial_tendency
                else:
                    report.setdefault("forced", []).append(
                        {"asset": rel_gov, "note": "强行覆盖运行时干预: "
                         + "; ".join("{} ({})".format(r, v["by"]) for r, v in blocked.items())})
            if gov_path:
                report["changed"].append({"asset": rel_gov, "kind": "governance"})
            if apply and gov_path:
                os.makedirs(os.path.dirname(gov_path), exist_ok=True)
                # 先算文本再打开写:open(...,"w") 会截断文件,而 _dump_like 要读原文件探测排版
                body = _dump_like(gov_path, want_gov)
                with io.open(gov_path, "w", encoding="utf-8", newline="") as f:
                    f.write(body)
                report["written"].append(rel_gov)
    else:
        report["problems"].append("声明没给 world.assets.governance,无法定位 governance.json")

    # 2) 各角色 agent.json 的 initial_tendency(按声明里的角色键找目录)
    rel_agents = assets.get("agents") or ""
    for role_key, dims in mat["initial_tendency"].items():
        if not rel_agents:
            report["problems"].append("声明没给 world.assets.agents,无法定位角色资产")
            break
        p = os.path.normpath(os.path.join(base, rel_agents, role_key, "agent.json"))
        if not os.path.isfile(p):
            report["problems"].append("角色资产不存在,跳过: {}".format(
                os.path.relpath(p, base).replace("\\", "/")))
            continue
        data = _load_json(p) or {}
        cur = data.get("initial_tendency") or {}
        rel = os.path.relpath(p, base).replace("\\", "/")
        if _same(cur, dims):
            report["in_sync"].append({"asset": rel, "kind": "initial_tendency"})
            continue
        report["changed"].append({"asset": rel, "kind": "initial_tendency"})
        if apply:
            data["initial_tendency"] = dict(dims)
            body = _dump_like(p, data)      # 同上:先算文本,再截断写
            with io.open(p, "w", encoding="utf-8", newline="") as f:
                f.write(body)
            report["written"].append(rel)

    report["ok"] = not report["problems"]
    return report


def summary_line(report: Dict[str, Any]) -> str:
    """一行人类可读结论(CLI 与测试都用它,口径一致)。"""
    if report.get("problems"):
        return "拒绝落盘: " + "; ".join(report["problems"])
    n_sync, n_chg = len(report["in_sync"]), len(report["changed"])
    if not report.get("apply"):
        return "dry-run:{} 项一致、{} 项需改(加 --apply 才写)".format(n_sync, n_chg)
    return "已落盘:{} 项一致、{} 项已写".format(n_sync, len(report["written"]))
