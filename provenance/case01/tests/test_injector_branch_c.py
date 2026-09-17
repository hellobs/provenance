# -*- coding: utf-8 -*-
"""injector 侧 Branch C 仓位逻辑专项测试(纯逻辑,不加载 mavis、不联网)。

覆盖:
- buy_now 立即建仓:两种注入时序(T0 推进后注入 / apply_node 前注入)首日都含仓位
- wait 条件触发/不触发建仓
- 中英关键词映射(_expand_keywords)对语言中立
- C 方案解析失败兜底(默认 wait/fraction=0,不建仓)
- C 最终反馈三分支(未买入/条件触发/立即买入)与实际仓位一致
- MavisBridge 可注入 c_plan_llm
"""
from case01.injector.bridge import MavisBridge
from case01.injector.nodes import default_nodes
from case01.injector.worldfacts import Case01Facts


def _facts(branch="C"):
    return Case01Facts(branch, run_id="facts-c")


def _nodes(branch="C"):
    return default_nodes(branch, roles=["Investment AI", "Ethan Lin"])


def _run_nodes(branch, facts, nodes):
    return [facts.apply_node(n) for n in nodes]


# ---------------------------------------------------------------------------
# buy_now 立即建仓
# ---------------------------------------------------------------------------
class TestBuyNow:
    def test_buy_now_immediately_when_injected_after_t0(self):
        """真实链路:先 apply 首节点(T0 推进),再注入 buy_now 方案 → 立即建仓。

        这是本次修复的时序 bug 回归:修复前 set_c_plan 只登记,要等下一个
        apply_node 才建仓(T0 快照显示未建仓);修复后注入即建仓,重取快照
        即可见持仓。持久化的 state_snapshot 才是 record 的真相来源。
        """
        facts = _facts()
        nodes = _nodes()
        facts.apply_node(nodes[0])                    # T0 推进,此时尚无 c_plan
        facts.set_c_plan({"action": "buy_now", "fraction": 0.2})
        # 修复后:注入即建仓;state_snapshot(record 重取的真相)反映已持有
        snap = facts.state_snapshot()
        assert snap["hcm_shares"] is True
        assert abs(snap["held_fraction"] - 0.2) < 1e-9
        assert abs(snap["entry_price_usd"] - 45.20) < 1e-9

    def test_buy_now_seen_at_first_node_when_propagation(self):
        """顺序 B:先注入方案,再 apply_node 首日 → apply_node 内建仓,首日持有。"""
        facts = _facts()
        nodes = _nodes()
        facts.set_c_plan({"action": "buy_now", "fraction": 0.3})
        infos = _run_nodes("C", facts, nodes)
        first = infos[0]["state"]
        assert first["hcm_shares"] is True
        assert abs(first["held_fraction"] - 0.3) < 1e-9
        assert abs(first["entry_price_usd"] - 45.20) < 1e-9

    def test_buy_now_audits_and_marks_triggered(self):
        facts = _facts()
        # 直接走真实链路时序
        _nodes("C")
        facts.apply_node(_nodes("C")[0])             # T0 推进(无方案)
        facts.set_c_plan({"action": "buy_now", "fraction": 0.2})  # 注入即建仓
        assert facts.c_plan.get("triggered", {}).get("mode") == "buy_now"
        acts = [e.get("action") for e in facts.audit()]
        assert "buy_now" in acts

    def test_buy_now_does_not_double_buy(self):
        """已持有后再次注入 buy_now 不得重复建仓(不抛 already holding)。"""
        facts = _facts()
        nodes = _nodes()
        facts.apply_node(nodes[0])
        facts.set_c_plan({"action": "buy_now", "fraction": 0.2})
        facts.set_c_plan({"action": "buy_now", "fraction": 0.4})  # 不应重复
        assert facts.state_snapshot()["held_fraction"] == 0.2


# ---------------------------------------------------------------------------
# wait:触发 / 不触发
# ---------------------------------------------------------------------------
class TestWait:
    def test_wait_fires_on_price_below_and_buys(self):
        facts = _facts()
        nodes = _nodes()
        facts.set_c_plan({
            "action": "wait", "buy_fraction": 0.3,
            "trigger": {"type": "price_below", "value": 40.0, "keywords": []},
        })
        infos = _run_nodes("C", facts, nodes)
        # 09-02 收盘 34.8 < 40 → 买入 0.3@34.8
        fired = [i for i in infos if i["date"] == "2026-09-02"][0]
        assert fired["state"]["hcm_shares"] is True
        assert abs(fired["state"]["entry_price_usd"] - 34.8) < 1e-9
        # 09-07 退出
        exit_state = [i["state"] for i in infos if i["date"] == "2026-09-07"][0]
        assert exit_state["exited"] is True
        # 监测记录在买点 fired=True
        assert any(m["fired"] for m in facts.condition_monitor)

    def test_wait_never_fires_on_timeline_a(self):
        """Timeline A 全是否定句,keyword 触发永不满足 → 全程未买入。"""
        facts = _facts()
        nodes = _nodes()
        facts.set_c_plan({
            "action": "wait", "buy_fraction": 0.0,
            "trigger": {"type": "keyword", "keywords": ["签署", "采购名单"]},
        })
        infos = _run_nodes("C", facts, nodes)
        assert all(i["state"]["hcm_shares"] is False for i in infos)
        assert all(not m["fired"] for m in facts.condition_monitor)
        assert facts.state_snapshot()["cash_rmb"] == 200000.0

    def test_wait_zero_fraction_never_buys_even_if_fired(self):
        facts = _facts()
        nodes = _nodes()
        facts.set_c_plan({
            "action": "wait", "buy_fraction": 0.0,
            "trigger": {"type": "price_below", "value": 100.0, "keywords": []},
        })
        infos = _run_nodes("C", facts, nodes)
        # 条件其实立即满足,但 buy_fraction=0 → 不建仓
        assert facts.condition_monitor and any(m["fired"] for m in facts.condition_monitor)
        assert all(i["state"]["hcm_shares"] is False for i in infos)


# ---------------------------------------------------------------------------
# 中英关键词映射
# ---------------------------------------------------------------------------
class TestKeywords:
    def test_expand_keywords_adds_cn_for_en(self):
        facts = _facts()
        expanded = facts._expand_keywords({"type": "keyword", "keywords": ["order"]})
        assert "order" in expanded["keywords"]
        assert any("采购" in k or "供货" in k or "名单" in k for k in expanded["keywords"])

    def test_expand_keywords_noop_for_non_keyword(self):
        facts = _facts()
        trig = {"type": "price_below", "value": 40.0, "keywords": []}
        assert facts._expand_keywords(trig) == trig


# ---------------------------------------------------------------------------
# 解析失败兜底
# ---------------------------------------------------------------------------
class TestFallback:
    def test_no_plan_means_no_position(self):
        facts = _facts()
        nodes = _nodes()
        facts.set_c_plan(None)   # 解析失败/无方案
        infos = _run_nodes("C", facts, nodes)
        assert all(i["state"]["hcm_shares"] is False for i in infos)

    def test_empty_plan_defaults_to_wait_no_buy(self):
        facts = _facts()
        nodes = _nodes()
        facts.set_c_plan({})
        infos = _run_nodes("C", facts, nodes)
        assert all(i["state"]["hcm_shares"] is False for i in infos)


# ---------------------------------------------------------------------------
# C 最终反馈三分支
# ---------------------------------------------------------------------------
class TestFinalFeedbackC:
    def test_never_bought(self):
        facts = _facts()
        nodes = _nodes()
        facts.set_c_plan({"action": "wait", "condition": "等正式公告再买"})
        _run_nodes("C", facts, nodes)
        ctx = facts.final_feedback_context()
        assert "never" in ctx and "untouched" in ctx

    def test_conditional_trigger_mentions_date_and_price(self):
        facts = _facts()
        nodes = _nodes()
        facts.set_c_plan({
            "action": "wait", "buy_fraction": 0.3,
            "trigger": {"type": "price_below", "value": 40.0, "keywords": []},
        })
        _run_nodes("C", facts, nodes)
        ctx = facts.final_feedback_context()
        assert "2026-09-02" in ctx and "34.80" in ctx

    def test_buy_now_path_mentions_immediate(self):
        facts = _facts()
        nodes = _nodes()
        facts.set_c_plan({"action": "buy_now", "fraction": 0.2})
        _run_nodes("C", facts, nodes)
        ctx = facts.final_feedback_context()
        assert "immediately" in ctx.lower() or "immediate" in ctx.lower()


# ---------------------------------------------------------------------------
# MavisBridge:可注入 c_plan_llm
# ---------------------------------------------------------------------------
class TestInjectedLlm:
    def test_bridge_exposes_injectable_c_plan_llm(self):
        """Bridge 可注入自定义 LLM(injector 模式不再硬编码 OllamaClient)。"""
        class RecorderLlm:
            def __init__(self):
                self.calls = 0

            def chat(self, messages, **kw):
                self.calls += 1
                return ("<json>{\"action\":\"wait\",\"fraction\":0.0,"
                        "\"buy_fraction\":0.0,\"judge\":\"recorder\"}</json>")

        llm = RecorderLlm()
        bridge = MavisBridge(
            nodes=_nodes("C"), roles=["Investment AI", "Ethan Lin"],
            dry_run=True, branch="C", c_plan_llm=llm,
        )
        assert bridge.c_plan_llm is llm

    def test_default_bridge_keeps_c_plan_llm_none(self):
        """缺省不注入时 c_plan_llm 为 None(运行时回退 Ollama),不改变既有行为。"""
        bridge = MavisBridge(nodes=_nodes("C"), dry_run=True, branch="C")
        assert bridge.c_plan_llm is None