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


def test_case00_sandbox_value_assembly_check():
    """sandbox-value 装配预检:case00 声明能指向 mavis 可装配的资产/参数。

    不跑沙盒,只验证声明式配置能**静态指向**冻结留档 assets(确定性预检线)。
    """
    from case_engine.config import load_yaml
    from case_engine.engines import build_for
    from case_engine.strategy import SandboxValue

    s = load_yaml(os.path.join(CASES_ROOT, "case00_village", "scenario.yaml"))
    assert isinstance(build_for(s), SandboxValue)
    res = build_for(s).run(s)

    assert res["engine"] == "sandbox-value"
    assert res["run_type"] == "assembly-check"
    # 关键段齐备(world 标准段命名)
    for sec in ("params", "assets", "value_tendency"):
        assert res["checks"][sec]["ok"], res["checks"][sec]
    # 资产:case00/scenario 冻结留档应全部存在且 JSON 可解析;agents 是目录
    assets = res["checks"]["assets"]["detail"]
    for key in ("story", "relationships", "maze", "agents", "governance"):
        assert assets[key]["ok"], "{}: {}".format(key, assets[key])
    # 行政:6 角色各自有价值权重
    roles = res["checks"]["roles"]["detail"]
    assert len(roles) == 6
    assert all(v["ok"] for v in roles.values())
    assert res["all_ok"] is True


def test_case00_sandbox_assembly_reports_missing_asset(tmp_path):
    """装配预检要能如实报出缺失,绝不假装可装配(反作弊)。"""
    from case_engine.strategy import SandboxValue

    # 迷你场景:声明指向的 story 在 base(tmp)下必然不存在;无角色
    s = _MiniScenario(
        case_id="mini_sandbox",
        assets={"story": "case00/scenario/story.json"},
        params={"percept": {"mode": "box"}},
    )
    res = SandboxValue().run(s, base=str(tmp_path))
    assert res["run_type"] == "assembly-check"
    assert res["all_ok"] is False
    assert res["checks"]["assets"]["detail"]["story"]["ok"] is False
    assert res["checks"]["roles"]["ok"] is False  # 无角色 → 行政预检不通过


class _MiniScenario:
    """最小场景鸭子类型:只带 SandboxValue 预检消费的 world 标准段/roles/case_id。"""

    def __init__(self, case_id, assets, params, roles=None):
        self.case_id = case_id
        self.assets = assets
        self.params = params
        self.roles = roles or [type("R", (), {"id": "a"})(), type("R", (), {"id": "b"})()]

    @property
    def engine(self):
        return "sandbox-value"


# ---------------------------------------------------------------------------
# 时间线声明式化 + 装配
# ---------------------------------------------------------------------------
def _case01():
    from case_engine.config import load_yaml
    return load_yaml(os.path.join(CASES_ROOT, "case01_stock", "scenario.yaml"))


def test_timeline_loaded_into_config():
    """case01 的 timeline 段能声明式加载进 Config,不给其它段造成回归。"""
    s = _case01()
    assert s.timeline["branch_map"] == {"A": "A", "B": "B", "C": "A"}
    assert len(s.timeline["t0"]) == 3
    assert set(s.timeline["lines"].keys()) == {"A", "B"}
    # 仍能构造状态(时间线不应干扰世界状态)
    assert s.state_initial()["cash_rmb"] == 200000


def test_select_timeline_by_branch():
    """分支 → 正确选中 A/B 线(优先用 branch_map;兼容 fallback_map 携带的 timeline)。"""
    from case_engine.config import select_timeline
    s = _case01()
    _, tl_a, _ = select_timeline(s, "A")
    assert tl_a["2026-09-07"][0]["kind"] == "disclosure"   # A 线:落空
    _, tl_b, _ = select_timeline(s, "B")
    assert tl_b["2026-09-07"][0]["kind"] == "research"     # B 线:落地
    # C 走 A 线(与 case01 BRANCH_TO_TIMELINE 一致)
    lid_c, tl_c, _ = select_timeline(s, "C")
    assert lid_c == "A"
    assert tl_c["2026-09-07"][0]["kind"] == "disclosure"


def test_run_assembles_timeline_nodes():
    """ExperimentEval 真跑后产出按日期装配的节点序列,首末含对角交互 + 价格 world。

    确定性、不调 LLM:节点来自声明式 timeline,而非模型。
    """
    from case_engine.engines import build_for
    s = _case01()
    res = build_for(s).run(s, input_text="我建议分阶段先小规模试点")  # → C → Timeline A
    assert res["branch"] == "C"
    nodes = res.get("timeline") or []
    assert nodes, "应有时间线节点"
    # 首节点是 T0 咨询日(meta.start_date),含 T0 三事实 → 价格 world 45.80
    first = nodes[0]
    assert first["date"] == "2026-08-27"
    assert first["require_interaction"] is True
    kinds0 = {e["event_type"] for e in first["events"]}
    assert "disclosure" in kinds0 and "price" in kinds0
    assert first["world"].get("price_usd") == 45.80
    # 次日(08-28)进入所选线的当日收盘价
    assert nodes[1]["date"] == "2026-08-28"
    assert nodes[1]["world"].get("price_usd") == 49.20
    # 首末节点各带一次对角交互(T0 咨询 / 最终反馈)
    assert first["interactions"] and first["interactions"][0]["to"] == "investment_ai"
    assert nodes[-1]["interactions"] and nodes[-1]["interactions"][0]["to"] == "investment_ai"
    # 最终反馈落 meta.end_date
    assert nodes[-1]["date"] == s.meta["end_date"]


def test_timeline_no_drift_vs_case01_timelines():
    """防漂移守卫:scenario.yaml 的 timeline 与 case01/world/timelines.py 逐字一致。

    双份必须同步(案例当前仍保留 .py 实现在线);若任一侧改动,此测试标红提醒。
    比较:branch_map、T0 三事件、A/B 两线逐日事件(kind/summary/source/price_usd)。
    """
    from case01.world.timelines import BRANCH_TO_TIMELINE, T0_EVENTS
    from case01.world.timelines import build_timeline
    s = _case01()
    # branch_map ↔ BRANCH_TO_TIMELINE
    assert {str(k): str(v) for k, v in BRANCH_TO_TIMELINE.items()} == s.timeline["branch_map"]
    # t0 ↔ T0_EVENTS(仅比较语义字段)
    assert _strip_timeline_events(s.timeline["t0"]) == _strip_timeline_events(T0_EVENTS)
    # lines ↔ timeline_a()/timeline_b()
    for branch, line_id in BRANCH_TO_TIMELINE.items():
        py = _strip_timeline_events_by_date(build_timeline(branch))
        yaml = _strip_timeline_events_by_date(s.timeline["lines"][line_id])
        # py 含 t0 首日,yaml 的 t0 独立;比较时去掉 py 首日里的 t0 事件
        py_root = {d: evs for d, evs in py.items() if d in yaml}
        assert py_root == yaml, "timeline {} 声明与 .py 不一致".format(line_id)


def _strip_timeline_events(events):
    return [(e.get("kind"), e.get("summary"), e.get("source"), e.get("price_usd"))
            for e in events]


def _strip_timeline_events_by_date(lines):
    return {d: _strip_timeline_events(evs) for d, evs in lines.items()}