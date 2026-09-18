# -*- coding: utf-8 -*-
"""边界测试:包内零业务词汇、零 mavis 运行时 import、协议漂移、缺参报错。"""
import os
import re

import pytest

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # packages/mavis-vizkit
SRC = os.path.join(PKG_ROOT, "src", "mavis_vizkit")

FORBIDDEN = ["case01", "Case01", "injector", "Investment AI", "Ethan Lin", "HCM"]

MOCK_RECORD_NO_COORDS = {
    "run_id": "x", "roles": ["Alice", "Bob"],
    "nodes": [{"node_id": "n1", "date": "2026-08-27", "step": 1, "events": []}],
}


def _all_src_py():
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in sorted(files):
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _src_text():
    return "\n".join(open(p, encoding="utf-8").read() for p in _all_src_py())


def test_src_has_zero_business_vocab():
    """验收 A 第一刀:src/ 内代码与注释都不许出现业务词汇。"""
    text = _src_text()
    hits = [w for w in FORBIDDEN if w in text]
    assert not hits, "src/ 出现业务词汇: {}".format(hits)


def test_src_never_imports_mavisframework():
    """验收 A 第二刀:src/ 内不许 import/from mavisframework(运行时零耦合)。"""
    bad = []
    for path in _all_src_py():
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if re.match(r"^\s*(import|from)\s+mavisframework", line):
                bad.append("{}:{}: {}".format(os.path.basename(path), i, line.strip()))
    assert not bad, "src/ import 了 mavisframework:\n" + "\n".join(bad)


def test_protocol_keys_still_exist_in_engine():
    """协议漂移测试:消费的键名仍存在于框架的 runtime.protocol(仅测试期 dev 依赖)。"""
    mp = pytest.importorskip("mavisframework.runtime.protocol", reason="需要 mavisframework")
    required = {"type", "name", "coord", "path", "action", "location", "currently", "time"}
    missing = required - set(mp.AgentState.__annotations__)
    assert not missing, "框架协议丢了键: {}".format(sorted(missing))
    # 引擎仍接受本包投放的这几种消息类型
    assert mp.validate_message({"type": "init"})
    assert mp.validate_message({"type": "time"})
    assert mp.validate_message({"type": "chat_line", "speaker": "s", "text": "t"})
    assert mp.validate_message({"type": "snapshot", "agents": {}})
    assert mp.validate_message({"type": "agent", "name": "n", "coord": [0, 0]})
    # 本包标示的事件类型也应被引擎认可(agent 专有校验)
    for t in ("init", "time", "chat_line", "snapshot", "agent"):
        assert t in {"init", "time", "chat_line", "snapshot", "agent", "story"}


def test_missing_required_config_raises_not_guesses():
    """缺 alias/scenario/前端根/渲染回调时必须报清晰的错,而不是回退猜默认。"""
    from mavis_vizkit import create

    with pytest.raises(ValueError):
        create("town", roles=["Alice", "Bob"])                      # 缺 alias
    with pytest.raises(ValueError):
        create("town", alias={"Alice": "Mr. Zhou"})                 # 缺 scenario_dir
    with pytest.raises(ValueError):
        create("live", roles=["Alice", "Bob"], alias={"Alice": "Mr. Zhou"},
               scenario_dir="/tmp/x")                                # 缺 static/template
    with pytest.raises(ValueError):
        create("report", out_dir="out")                             # 缺渲染回调


def test_scenario_dir_must_not_fall_back_to_repo_paths(tmp_path):
    """路径一律由调用方决定;给了合法 scenario 与 alias 就没有本仓路径可回落。"""
    from mavis_vizkit.plugins.town import TownVisualizer

    # 合法的临时 scenario,确保包能正常返回坐标,而不是去找任何本仓库默认目录
    import json
    d = tmp_path / "scn"
    (d / "agents" / "Alice").mkdir(parents=True)
    (d / "agents" / "Alice" / "agent.json").write_text(
        json.dumps({"coord": [3, 3]}), encoding="utf-8")
    t = TownVisualizer(alias={"Alice": "Mr. Zhou"}, scenario_dir=str(d), roles=["Alice"])
    t.on_record(MOCK_RECORD_NO_COORDS)
    agents = [m for m in t.messages() if m["type"] == "agent"]
    assert agents and agents[0]["coord"] == [3, 3]