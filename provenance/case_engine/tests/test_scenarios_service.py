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