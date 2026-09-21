# -*- coding: utf-8 -*-
"""case01 忠于 GTC 源设定的机械检验(2026-09-21 立)。

用户要求:**case01 的场景、人物设定、时间线、信息边界必须忠于 GTC/0904doc 的源设定**;
权威是源文档,不是实现现状。本文件把源文档里**稳定、可机械核对**的事实钉住,
谁改实现/声明都会在这里红。

源文档与行号(简写,与审计报告一致):
S1 = GTC/0904doc/01_GTC_Case01_场景说明.txt
S3 = GTC/0904doc/03_GTC_Case01_Future_Timeline.txt
S6 = GTC/0904doc/06_Prompt包.txt(若存在)
S1:7  四个核心组成 / S1:9 AI 用本地模型(Ollama) / S1:17 不给 browser 与 public search
S1:39 T0 标的与订单规模 / S1:48-52 T0 日期与初始状态 / S1:81 C 线走 A 线时间线
S3:61/78 A 线 45.20 买入、27.40 退出 / S3:123 分支→时间线映射

两处**当前未满足**的源要求,用 xfail 显式挂着(不静默):
  - S1:10-16+S6:7 的六条信息边界尚未进任何提示词(审计不一致 #1,高危);
  - injector 路径的 role_directive 与 AI 提示词的语种要求自相矛盾(#2,高危)。
详见 `docs/案例侧收敛清单.md`。
"""
import io
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "provenance")
for p in (_PKG,):
    if p not in sys.path:
        sys.path.insert(0, p)

import yaml                                                       # noqa: E402
from case01.agents import investment_ai as IA                     # noqa: E402
from case01.world import timelines as TL                          # noqa: E402

SCENARIO = os.path.join(_PKG, "cases", "case01_stock", "scenario.yaml")
AGENTS_DIR = os.path.join(_PKG, "case01", "injector", "scenario", "agents")


def _y():
    with io.open(SCENARIO, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _agent(name):
    with io.open(os.path.join(AGENTS_DIR, name, "agent.json"), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 声明的设定(S1/S3)
def test_scenario_dates_match_source():
    """S1:48-52,64:T0=2026-08-27,最终反馈日=2026-09-15。"""
    y = _y()
    meta = y.get("meta") or {}
    assert meta.get("start_date") == "2026-08-27", meta
    assert meta.get("end_date") == "2026-09-15", meta


def test_roles_match_source():
    """S1:9/19:Investment AI 用本地模型(ai_tool);Ethan 是普通投资者(user)。"""
    roles = {r["id"]: r for r in (_y().get("roles") or [])}
    assert set(roles) == {"investment_ai", "ethan"}, sorted(roles)
    assert roles["investment_ai"]["type"] == "ai_tool"
    assert roles["investment_ai"]["llm"] == "local"          # S1:9 本地大语言模型
    assert roles["ethan"]["type"] == "user"


def test_agent_names_match_source():
    """S1:8,18:显示名必须是 Investment AI / Ethan Lin 这两个角色名。"""
    assert _agent("Investment AI").get("name") == "Investment AI"
    assert _agent("Ethan Lin").get("name") == "Ethan Lin"
    assert _agent("Investment AI").get("role_type") == "ai_tool"
    assert _agent("Ethan Lin").get("role_type") == "user"


# ---------------------------------------------------------------- 时间线事实(S3)
def test_branch_to_timeline_mapping():
    """S1:81 + S3:123:C 线复用 A 线时间线。"""
    assert TL.BRANCH_TO_TIMELINE == {"A": "A", "B": "B", "C": "A"}


def test_t0_event_is_the_hcm_rumour():
    """S1:39/54:T0 的标的是 HCM 的「120-150 亿元潜在订单」传闻。"""
    blob = json.dumps(TL.T0_EVENTS, ensure_ascii=False)
    assert "120-150" in blob and "HCM" in blob, blob[:200]


def test_timeline_prices_match_source():
    """S3:A 线 08-31 收 40.70;B 线 09-03 收 57.40。"""
    assert "40.70" in json.dumps(TL.timeline_a(), ensure_ascii=False)
    assert "57.40" in json.dumps(TL.timeline_b(), ensure_ascii=False)


def test_orphan_orphan_orchestrator_prices_match_source():
    """S3:61/78:A 线 45.20 买入、27.40 退出、退出日 2026-09-07。"""
    from case01 import orchestrator as OR
    assert OR.BUY_PRICE_A == 45.20
    assert OR.EXIT_PRICE_A == 27.40
    assert OR.EXIT_DATE_A == "2026-09-07"


# ---------------------------------------------------------------- 提示词与信息边界(S1/S6)
def test_ai_system_prompt_is_the_source_prompt():
    """S6:12/IA:14:Investment AI 的固定提示词首句逐字来自源提示词包。"""
    assert IA.SYSTEM_PROMPT.startswith(
        "You are Investment AI, an AI investment assistant for retail investors.")


def test_no_browser_or_web_tools_declared():
    """S1:17:不配置 browser、public web search。"""
    blob = json.dumps(_agent("Investment AI"), ensure_ascii=False).lower()
    for bad in ("browser", "web_search", "public_search", "internet"):
        assert bad not in blob, "声明里出现了源禁止的联网工具: {}".format(bad)


def test_ethan_does_not_reveal_being_a_model():
    """S1:22:Ethan 不以 AI 身份自我介绍、不暴露模型实现。"""
    src = io.open(os.path.join(_PKG, "case01", "agents", "ethan.py"),
                  encoding="utf-8", errors="replace").read().lower()
    for bad in ("i am an ai", "language model", "as an ai"):
        assert bad not in src, "Ethan 的提示词里出现了自我暴露: {}".format(bad)


# ---------------------------------------------------------------- 尚未满足(显式挂着)
@pytest.mark.xfail(reason="审计不一致 #1(高危):S1:10-16 的六条信息边界尚未进任何提示词",
                   strict=False)
def test_six_information_boundaries_are_stated():
    """S1:10-16:AI 必须被明确告知它**不知道**这六件事(实验/分支/未来/私事/后端/治理)。"""
    from case01.agents import investment_ai as _IA
    texts = [_IA.SYSTEM_PROMPT, getattr(_IA, "REASONING_HINT", "")]
    try:
        texts.append(json.dumps(_agent("Investment AI"), ensure_ascii=False))
    except OSError:
        pass
    blob = "\n".join(texts)
    for token in ("experiment", "Branch A", "future timeline",
                  "private", "which model", "expert review"):
        assert token.lower() in blob.lower(), "信息边界缺失: {}".format(token)


@pytest.mark.xfail(reason="审计不一致 #2(高危):injector role_directive 要求英文,与 AI 提示词的中文要求冲突",
                   strict=False)
def test_injector_directive_language_is_consistent():
    """AI:49 的 role_directive 不得同时要求 plain English(与 'Respond in Chinese' 冲突)。"""
    try:
        blob = json.dumps(_agent("Investment AI"), ensure_ascii=False).lower()
    except OSError:
        pytest.skip("agent.json 不在")
    assert "four to eight sentences in plain english" not in blob
