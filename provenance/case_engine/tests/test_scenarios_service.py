# -*- coding: utf-8 -*-
"""Phase 4:场景自动发现 + 多场景只读服务(引擎通用)。"""
import os

from case_engine.scenarios import discover, by_id

# case_engine/../cases = provenance/provenance/cases
CASES_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cases"))


def test_discover_finds_all_cases():
    infos = discover(CASES_ROOT)
    ids = {s.case_id for s in infos}
    assert {"case00_village", "case01_stock"} <= ids
    # 每个场景声明了已注册引擎,且无加载 problem
    for s in infos:
        assert not s.problem, "{}.problem: {}".format(s.case_id, s.problem)
        assert s.engine in {"experiment-eval", "sandbox-value"}
        assert s.n_roles > 0


def test_discover_by_id_precise():
    info = by_id(CASES_ROOT, "case01_stock")
    assert info is not None and info.engine == "experiment-eval"
    assert by_id(CASES_ROOT, "case00_village") is not None
    assert by_id(CASES_ROOT, "does_not_exist") is None


def test_discover_tolerates_bad_scenario(tmp_path):
    """宽容扫描:一个坏的 scenario.yaml 记 problem,不拖垮其余。"""
    good = tmp_path / "good"
    good.mkdir()
    (good / "scenario.yaml").write_text(
        "meta:\n  case_id: good\nroles:\n  - id: a\n", encoding="utf-8")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "scenario.yaml").write_text("meta: [这是非字典 : 语法错", encoding="utf-8")
    infos = discover(str(tmp_path))
    ids = {s.case_id: s for s in infos}
    assert "good" in ids and not ids["good"].problem
    assert "bad" in ids and ids["bad"].problem


# ---- 服务层(不走网络,直接调 TestClient) ----
def test_serve_discovery_and_detail():
    from fastapi.testclient import TestClient
    from case_engine.serve import app

    with TestClient(app) as c:
        idx = c.get("/").json()
        assert any("scenarios" in ep for ep in idx["endpoints"])

        r = c.get("/api/scenarios")
        assert r.status_code == 200
        body = r.json()
        ids = {s["case_id"] for s in body["scenarios"]}
        assert {"case00_village", "case01_stock"} <= ids
        assert body["by_engine"].get("sandbox-value", 0) >= 1
        assert body["by_engine"].get("experiment-eval", 0) >= 1

        d = c.get("/api/scenarios/case00_village")
        assert d.status_code == 200
        dj = d.json()
        assert dj["engine"] == "sandbox-value"
        assert dj["engine_meta"]["name"] == "生成式价值权重沙盒"
        assert dj["n_roles"] == 6
        # 自由搭配:该场景可被 sandbox-value 跑(experiment-eval 缺输入,不该进元矩阵)
        sups = {e["engine"] for e in dj["supported_engines"]}
        assert "sandbox-value" in sups
        assert "experiment-eval" not in sups

        d1 = c.get("/api/scenarios/case01_stock")
        assert d1.status_code == 200
        assert d1.json()["engine"] == "experiment-eval"


def test_serve_404_unknown_and_bad_case_id():
    from fastapi.testclient import TestClient
    from case_engine.serve import app

    with TestClient(app) as c:
        assert c.get("/api/scenarios/does_not_exist").status_code == 404
        # 路径穿越字符被拒(而非回读任意文件)
        assert c.get("/api/scenarios/..%2F..%2F").status_code == 404
        assert c.get("/api/scenarios/../").status_code == 404


def test_case02_generality_discovers_and_runs():
    """通用性验证:一个与既有场景无关的最小新场景,零代码即可被发现并真跑。"""
    from case_engine.config import load_yaml
    from case_engine.engines import build_for, supported_by
    from case_engine.strategy import ExperimentEval

    s = load_yaml(os.path.join(CASES_ROOT, "case02_minimal", "scenario.yaml"))
    assert s.case_id == "case02_minimal"
    assert s.name == "最小立项审批场景"
    assert s.engine == "experiment-eval"

    # 发现层:case02 进发现列表,且无加载 problem
    ids = {x.case_id for x in discover(CASES_ROOT)}
    assert "case02_minimal" in ids
    info = by_id(CASES_ROOT, "case02_minimal")
    assert info is not None and not info.problem
    assert info.engine == "experiment-eval"

    # 兼容矩阵:case02 仅被 experiment-eval 支持
    assert supported_by(s) == ["experiment-eval"]

    # 真跑:中文条件化方案 → C 线,一致性信号词中文命中 → consistent(确定性)
    strat = build_for(s)
    res = strat.run(s, input_text="建议分阶段先小规模试点")
    assert strat.engine_id == "experiment-eval"
    assert res["run_type"] == "rule-dryrun"
    assert res["branch"] == "C"
    assert res["consistency"]["verdict"] == "consistent"
    # 状态由 scenario.state_schema 动态构造,无硬编码字段
    assert res["state"] == {"approved": False, "budget": 100000, "staged": False}