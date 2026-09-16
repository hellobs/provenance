# -*- coding: utf-8 -*-
"""injector dry-run 冒烟测试:验证节点生成、记录结构与"不依赖 mavis"。"""
import sys

from case01.injector.bridge import MavisBridge
from case01.injector.nodes import default_nodes


def test_nodes_from_timeline_b():
    nodes = default_nodes("B", roles=["Investment AI", "Ethan Lin"])
    assert len(nodes) >= 5
    # 每节点都要有可释放的公开事件
    assert all(n.events for n in nodes)
    # 事件 id 唯一
    ids = [e["id"] for n in nodes for e in n.events]
    assert len(ids) == len(set(ids))
    # targets 按角色列表分发
    assert nodes[0].events[0]["targets"] == ["Investment AI", "Ethan Lin"]
    # 世界事实(价格)被记录
    assert any("price_usd" in n.world for n in nodes)


def test_key_nodes_require_interaction():
    nodes = default_nodes("B")
    assert nodes[0].require_interaction is True
    assert nodes[-1].require_interaction is True
    assert all(not n.require_interaction for n in nodes[1:-1])


def test_dry_run_record_and_no_mavis_dependency():
    nodes = default_nodes("B", roles=["Investment AI", "Ethan Lin"])
    bridge = MavisBridge(nodes=nodes, dry_run=True, run_id="dry-B")
    record = bridge.run()

    assert record["schema_version"] == "injector-0.1"
    assert record["mode"] == "dry-run"
    assert record["case01_run_compatible"] is False
    assert record["summary"]["node_count"] == len(nodes)
    # 每个节点一条记录,步骤连续递增
    assert [r["step"] for r in record["nodes"]] == list(range(1, len(nodes) + 1))
    # dry-run 下关键节点视为已发生交互
    assert record["summary"]["interaction_started"] >= 2
    # 全程不加载 mavis
    assert "mavisframework" not in sys.modules


def test_external_state_and_interaction_callbacks():
    nodes = default_nodes("B")
    nodes[0].context = {"Investment AI": {"stress": 0.4}, "Ethan Lin": {"suspicion": "high"}}
    nodes[0].interactions = [{"from": "Ethan Lin", "to": "Investment AI", "focus": "HCM 是否值得买入"}]
    bridge = MavisBridge(nodes=nodes, dry_run=True)
    record = bridge.run()

    first = record["nodes"][0]
    # 交互主题会写入双方步级状态:发起方 current task,接收方 user request
    assert first["context"]["Investment AI"] == {"stress": 0.4, "user request": "HCM 是否值得买入"}
    assert first["interactions"][0]["focus"] == "HCM 是否值得买入"
    # 回调返回"当前节点"的注入内容
    bridge.activate(nodes[0])
    assert bridge.external_state("Ethan Lin", 1, "20260827-09:30", None) == {
        "suspicion": "high", "current task": "HCM 是否值得买入"}
    assert bridge.interaction_request(1, "20260827-09:30", None)[0]["from"] == "Ethan Lin"
    # 切到别的节点后,回调内容随之切换
    bridge.activate(nodes[1])
    assert bridge.external_state("Ethan Lin", 2, "20260828-09:30", None) == {}
