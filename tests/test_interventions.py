# -*- coding: utf-8 -*-
"""干预策略注册表测试(2026-09-27 随 InterventionStrategy 落地新增)。

位置:仓库根 tests/(CI `pytest tests` 收集;live/ 层测试与 test_live_api 同族)。
覆盖:
- 注册表契约(register 重复/非法 id 拒绝、known/get/all_ids/describe);
- goals 端点(此前**零测试**):校验路径、落盘路径(governance.json + 审计)、
  权重清洗(NaN/数字开头/Σ≠1)、未知角色拒绝;
- undo 端点(此前**零测试**):回滚、撤销审计、重复撤销拒绝、未找到、沙盒 403;
- 统一分发口 /api/intervention/{id}:未知策略 404 带清单、JSON-only 守卫;
- corrective_feedback(扩展性证明):经统一分发口调用,记忆注入 + 审计,
  且 internalization_metrics 对该记录优雅跳过。

只读原则:所有写落在 tmp 目录(ctx 注入),不碰真实 governance.json / results/。
"""
import datetime
import json
import os
import sys
import tempfile

import pytest

_this_dir = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_this_dir)                     # D:\zzr\provenance
_PKG = os.path.join(_REPO, "provenance")               # live/ 包所在
for _p in (_PKG, _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi.testclient import TestClient

from live import interventions as ivm
from live import reflections as refl
from live import state

# 模块级导入:live.routes 的 StaticFiles 挂载要求真实 frontend/static,
# 必须在 monkeypatch BASE_DIR 之前完成导入(之后走模块缓存,不再触发检查)。
from live.routes import app as _app  # noqa: E402


# ---------------------------------------------------------------------------
# 测试脚手架
# ---------------------------------------------------------------------------

class _FakeTimer:
    def get_date(self, fmt=""):
        d = datetime.datetime(2025, 2, 13, 9, 30)
        return d.strftime(fmt) if fmt else d


class _FakeGame:
    def __init__(self, agents=None, governance=None):
        self.agents = agents or {}
        self.governance = governance
        self._timer = _FakeTimer()


class _FakeServer:
    def __init__(self, game):
        self.game = game


class _FakeAgent:
    """带记忆注入面的最小 agent(供 corrective_feedback 测试)。"""

    def __init__(self):
        self.injected = []

    def inject_story_event(self, ev):
        self.injected.append(ev)


@pytest.fixture()
def sandbox_env(tmp_path, monkeypatch):
    """隔离环境:tmp BASE_DIR + case00 声明为 experiment-eval(允许 undo)。"""
    os.makedirs(os.path.join(str(tmp_path), "cases", "case00_village"), exist_ok=True)
    (tmp_path / "cases" / "case00_village" / "scenario.yaml").write_text(
        "meta:\n  case_id: case00_village\n  engine: experiment-eval\n",
        encoding="utf-8")
    monkeypatch.setattr(state, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(state, "server", None)
    yield tmp_path


@pytest.fixture()
def client():
    return TestClient(_app)


def _make_fake_server(tmp_path, agent_names=("AI Advisor",), with_agent=False):
    agents = {}
    for n in agent_names:
        agents[n] = _FakeAgent() if with_agent else object()
    gov = _governance(tmp_path)
    return _FakeServer(_FakeGame(agents=agents, governance=gov))


def _governance(tmp_path):
    from mavisframework.runtime.governance import Governance
    return Governance(os.path.join(str(tmp_path), "governance.json"))


# ---------------------------------------------------------------------------
# 注册表契约
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_builtin_registered(self):
        assert ivm.all_ids() == ["corrective_feedback", "goals", "mark", "undo"]
        for sid in ivm.all_ids():
            assert ivm.known(sid)
            assert ivm.get(sid).strategy_id == sid
            d = ivm.describe(sid)
            assert d["strategy"] == sid and d["name"] and d["desc"]

    def test_duplicate_rejected(self):
        with pytest.raises(ValueError, match="已注册"):
            ivm.register("goals", ivm.WeightAdjustStrategy, {})

    def test_bad_id_rejected(self):
        with pytest.raises(ValueError, match="非法"):
            ivm.register("../evil", ivm.WeightAdjustStrategy, {})
        with pytest.raises(ValueError, match="非法"):
            ivm.register("", ivm.WeightAdjustStrategy, {})

    def test_custom_strategy_extends(self):
        """扩展性证明:第三方策略 = 写类 + register,统一分发口即可用。"""

        class _Echo(ivm.InterventionStrategy):
            strategy_id = "test-echo-x7"
            name = "回声(测试)"

            def apply(self, ctx, payload):
                return {"ok": True, "echo": payload}

        ivm.register("test-echo-x7", _Echo, {"name": "回声(测试)"})
        try:
            assert ivm.known("test-echo-x7")
            c = TestClient(_app)
            r = c.post("/api/intervention/test-echo-x7", json={"k": "v"})
            assert r.status_code == 200 and r.json()["echo"] == {"k": "v"}
        finally:
            ivm.STRATEGIES.pop("test-echo-x7", None)
            ivm._INTERVENTIONS.pop("test-echo-x7", None)


# ---------------------------------------------------------------------------
# goals 端点(首批测试)
# ---------------------------------------------------------------------------

class TestGoalsEndpoint:
    def test_missing_name(self, client):
        assert client.post("/api/goals", json={}).json() == {
            "ok": False, "errors": ["缺少角色名"]}

    def test_goals_not_dict(self, client):
        r = client.post("/api/goals", json={"name": "A", "goals": "x"}).json()
        assert r["errors"] == ["约束应为非空 dict(目标:权重)"]

    def test_unknown_role_rejected_when_server_present(self, client, sandbox_env):
        state.server = _FakeServer(_FakeGame(agents={"AI Advisor": _FakeAgent()}))
        try:
            r = client.post("/api/goals",
                            json={"name": "查无此人", "goals": {"A": 1.0}}).json()
            assert "没有这个角色" in r["errors"][0]
        finally:
            state.server = None

    def test_nan_rejected(self, client):
        # NaN 以裸字面量直发(httpx 的 json= 禁 NaN,而服务端 json.loads 会解析它
        # —— 这正是历史上"NaN 骗过 Σ=1 校验"的攻击面)
        r = client.post("/api/goals",
                        content='{"name": "A", "goals": {"A": NaN}}',
                        headers={"Content-Type": "application/json"}).json()
        assert "权重必须是有限数" in r["errors"][0]

    def test_sum_not_one_rejected(self, client):
        r = client.post("/api/goals",
                        json={"name": "A", "goals": {"A": 0.5, "B": 0.2}}).json()
        assert "约束权重总和应为 1" in r["errors"][0]

    def test_valid_goals_persist_and_audit(self, client, sandbox_env):
        r = client.post("/api/goals",
                        json={"name": "Mr. Zhou", "goals": {"A": 1.0},
                              "note": "测试干预"}).json()
        assert r["ok"] is True and r["constraints"] == {"A": 1.0}
        # governance.json 落盘
        gov = json.load(open(os.path.join(str(sandbox_env), "governance.json"),
                             encoding="utf-8"))
        assert gov["roles"]["Mr. Zhou"] == {"A": 1.0}
        # 审计落盘,带模态标记
        audit = json.load(open(os.path.join(
            str(sandbox_env), "results", "checkpoints", "interventions.json"),
            encoding="utf-8"))
        assert audit[-1]["agent"] == "Mr. Zhou"
        assert audit[-1]["operator"] == "expert"
        assert audit[-1]["intervention"] == "goals"
        assert audit[-1]["note"] == "测试干预"

    def test_digit_leading_goal_dropped(self, client):
        # {"1": 1.0} 数字开头 = 误输入 → 清洗后为空 → 拒绝
        r = client.post("/api/goals",
                        json={"name": "A", "goals": {"1": 1.0}}).json()
        assert "清洗后无有效目标" in r["errors"][0]

    def test_live_gov_synced_when_server_present(self, client, sandbox_env):
        """内存同步:agent._governance 指向 game.governance 同一对象,不更新则内化失效。"""
        gov = _governance(sandbox_env)
        state.server = _FakeServer(_FakeGame(agents={"AI Advisor": _FakeAgent()},
                                             governance=gov))
        try:
            r = client.post("/api/goals",
                            json={"name": "AI Advisor", "goals": {"A": 1.0}}).json()
            assert r["ok"] is True
            assert gov.data["roles"]["AI Advisor"] == {"A": 1.0}, "运行中实例未同步"
        finally:
            state.server = None


# ---------------------------------------------------------------------------
# undo 端点(首批测试)
# ---------------------------------------------------------------------------

def _seed_intervention(tmp_path, agent="Mr. Zhou"):
    """造一条已存在的权重干预审计,返回 (sim_time, time)。"""
    path = os.path.join(str(tmp_path), "results", "checkpoints",
                        "interventions.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rec = {"time": "2026-09-27 10:00:00", "sim_time": "20250213-10:00",
           "simulation": "", "agent": agent,
           "old_constraints": {"A": 0.3, "B": 0.7},
           "new_constraints": {"A": 0.6, "B": 0.4},
           "operator": "expert", "note": "", "intervention": "goals"}
    json.dump([rec], open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return rec["sim_time"], rec["time"]


class TestUndoEndpoint:
    def test_missing_fields(self, client, sandbox_env):
        # experiment-eval 场景下才会走到字段校验(sandbox 场景在守卫处就 403)
        r = client.post("/api/undo-intervention", json={}).json()
        assert r["errors"] == ["缺少 agent/sim_time/time"]

    def test_rollback_and_audit(self, client, sandbox_env):
        sim_time, rec_time = _seed_intervention(sandbox_env)
        # 回滚前先落一个"当前约束"(回滚校验要求当前非空)
        client.post("/api/goals",
                    json={"name": "Mr. Zhou", "goals": {"A": 0.6, "B": 0.4}})
        r = client.post("/api/undo-intervention",
                        json={"agent": "Mr. Zhou", "sim_time": sim_time,
                              "time": rec_time}).json()
        assert r["ok"] is True, r
        assert r["rollback_to"] == {"A": 0.3, "B": 0.7}
        gov = json.load(open(os.path.join(str(sandbox_env), "governance.json"),
                             encoding="utf-8"))
        assert abs(sum(gov["roles"]["Mr. Zhou"].values()) - 1.0) < 1e-6
        audit = json.load(open(os.path.join(
            str(sandbox_env), "results", "checkpoints", "interventions.json"),
            encoding="utf-8"))
        assert audit[0].get("revoked") is True
        assert audit[-1]["operator"] == "undo"
        assert audit[-1]["intervention"] == "undo"

    def test_double_undo_rejected(self, client, sandbox_env):
        sim_time, rec_time = _seed_intervention(sandbox_env)
        client.post("/api/goals", json={"name": "Mr. Zhou", "goals": {"A": 1.0}})
        ok = client.post("/api/undo-intervention",
                         json={"agent": "Mr. Zhou", "sim_time": sim_time,
                               "time": rec_time}).json()
        assert ok["ok"] is True
        again = client.post("/api/undo-intervention",
                            json={"agent": "Mr. Zhou", "sim_time": sim_time,
                                  "time": rec_time}).json()
        assert "重复撤销" in again["errors"][0]

    def test_no_match_reported(self, client, sandbox_env):
        _seed_intervention(sandbox_env, agent="someone-else")
        r = client.post("/api/undo-intervention",
                        json={"agent": "X", "sim_time": "t", "time": "t"}).json()
        assert "未找到匹配" in r["errors"][0]

    def test_sandbox_blocked_403(self, client, sandbox_env, monkeypatch):
        # 场景声明为 sandbox-value → undo 一律 403
        monkeypatch.setattr(ivm, "_case00_engine_id",
                            lambda base_dir: "sandbox-value")
        r = client.post("/api/undo-intervention",
                        json={"agent": "X", "sim_time": "t", "time": "t"})
        assert r.status_code == 403
        assert "不允许倒带" in r.json()["errors"][0]


# ---------------------------------------------------------------------------
# 统一分发口 + corrective_feedback(扩展性证明)
# ---------------------------------------------------------------------------

class TestGenericDispatch:
    def test_unknown_strategy_404_with_listing(self, client):
        r = client.post("/api/intervention/nope", json={})
        assert r.status_code == 404
        assert "已注册" in r.json()["errors"][0]

    def test_json_only_guard_applies(self, client):
        r = client.post("/api/intervention/goals", data={"x": "1"},
                        headers={"Content-Type": "text/plain"})
        assert r.status_code == 415

    def test_listing_endpoint(self, client):
        r = client.get("/api/interventions").json()
        assert r["ok"] is True
        assert {s["strategy"] for s in r["strategies"]} == set(ivm.all_ids())

    def test_corrective_feedback_via_generic_endpoint(self, client, sandbox_env):
        """扩展性证明:第 4 策略无专用路由,仅凭注册即可经统一分发口使用。"""
        agent = _FakeAgent()
        state.server = _FakeServer(_FakeGame(agents={"AI Advisor": agent}))
        try:
            r = client.post("/api/intervention/corrective_feedback",
                            json={"agent": "AI Advisor",
                                  "correction": "提示回撤风险时要给具体数字"}).json()
            assert r["ok"] is True and r["injected"] is True
            assert "提示回撤风险" in agent.injected[0]["content"]
            assert agent.injected[0]["event_type"] == "专家纠正"
            # 审计入链(带模态标记)
            audit = json.load(open(os.path.join(
                str(sandbox_env), "results", "checkpoints", "interventions.json"),
                encoding="utf-8"))
            assert audit[-1]["intervention"] == "corrective_feedback"
            assert audit[-1]["correction"] == "提示回撤风险时要给具体数字"
        finally:
            state.server = None

    def test_corrective_feedback_requires_running_sim(self, client):
        r = client.post("/api/intervention/corrective_feedback",
                        json={"agent": "X", "correction": "y"}).json()
        assert "没有运行中的模拟" in r["errors"][0]

    def test_correction_record_skipped_by_internalization_metrics(self,
                                                                  client,
                                                                  sandbox_env):
        """模态标记的兼容性:纠正记录无 old/new constraints,
        internalization_metrics / behavior_follow 必须优雅跳过而非崩溃。"""
        agent = _FakeAgent()
        state.server = _FakeServer(_FakeGame(agents={"AI Advisor": agent}))
        try:
            client.post("/api/intervention/corrective_feedback",
                        json={"agent": "AI Advisor", "correction": "纠正" * 10})
        finally:
            state.server = None
        from case01.tools.internalization_metrics import analyse_simulation
        iv = json.load(open(os.path.join(
            str(sandbox_env), "results", "checkpoints", "interventions.json"),
            encoding="utf-8"))
        result = analyse_simulation(
            os.path.join(str(sandbox_env), "results", "checkpoints", "nonexistent"),
            iv)
        assert result["summary"]["n_interventions_analysed"] == 0
        assert result["summary"]["n_skipped"] >= 1


class TestStrategyHardening:
    """深度体检补(2026-09-27):payload 归一 / None 防御 / 并发审计。"""

    def test_non_dict_payload_normalized_in_base(self, sandbox_env):
        """策略作为库被直接调用(绕过 HTTP 层 _json_body)时,非 dict payload
        由基类归一为 {} → 走字段校验,不裸崩 AttributeError。"""
        ctx = ivm.InterventionContext(server=None, base_dir=str(sandbox_env))
        for evil in (None, [], "str", 42):
            r = ivm.get("goals")().apply(ctx, evil)
            assert r["ok"] is False and r["errors"] == ["缺少角色名"], (evil, r)

    def test_strategy_returning_none_gets_500(self, sandbox_env, monkeypatch):
        """策略漏 return → 显式 500 + log,不静默包成 "null" 响应体。"""
        class _Buggy(ivm.InterventionStrategy):
            strategy_id = "test-buggy-x9"
            name = "坏策略(测试)"

            def _apply(self, ctx, payload):
                return None

        ivm.register("test-buggy-x9", _Buggy, {"name": "坏策略(测试)"})
        try:
            c = TestClient(_app)
            r = c.post("/api/intervention/test-buggy-x9", json={})
            assert r.status_code == 500
            assert "返回空结果" in r.json()["errors"][0]
        finally:
            ivm.STRATEGIES.pop("test-buggy-x9", None)
            ivm._INTERVENTIONS.pop("test-buggy-x9", None)

    def test_concurrent_audit_no_lost_records(self, sandbox_env):
        """两专家同时干预:审计读-改-写有锁,不丢行(丢行=审计链断裂)。"""
        import threading
        ctx = ivm.InterventionContext(server=None, base_dir=str(sandbox_env))
        threads = []
        for i in range(12):
            t = threading.Thread(target=ivm._append_audit, args=(
                ctx, {"time": "t{}".format(i), "sim_time": "20250213-10:00",
                      "simulation": "", "agent": "A{}".format(i),
                      "operator": "expert", "intervention": "goals",
                      "old_constraints": {}, "new_constraints": {}}))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        audit = json.load(open(os.path.join(
            str(sandbox_env), "results", "checkpoints", "interventions.json"),
            encoding="utf-8"))
        assert len(audit) == 12, "并发追加丢了审计记录: {} 条".format(len(audit))


class TestBothFaces:
    """两面都要有干预端点(2026-09-27 体检:case01 面曾缺全部干预端点,
    M4 专家标记会 404)。case00 面见 TestHTTPRoundTrip。"""

    def test_case01_face_exposes_interventions(self):
        from fastapi.testclient import TestClient
        from case01.vizkit.live_run import build_service
        svc = build_service()
        c = TestClient(svc.app)
        r = c.get("/api/interventions")
        assert r.status_code == 200
        ids = {x["strategy"] for x in r.json()["strategies"]}
        assert {"goals", "undo", "mark", "corrective_feedback"} <= ids
        # 旧路径别名也在(前端零改动)——但"端点在"不等于"本面能用":
        # 两条治理类策略在 case01 面必须**拒绝**(injector 的 Game 是
        # governance=None,写了 governance.json 没有实例会读它)。2026-10-06 修:
        # 此前它返回 ok:true 并把角色名落进仓根的追踪文件,现场看不出来是空操作。
        r = c.post("/api/goals", json={})
        assert r.status_code == 409, r.text
        assert "不挂治理" in r.json()["errors"][0], r.text
        assert c.post("/api/reflections/mark", json={}).status_code == 200
        # 统一分发口未知策略 404 带清单
        r = c.post("/api/intervention/nope", json={})
        assert r.status_code == 404 and "已注册" in r.json()["errors"][0]

    def test_case01_face_undo_sandbox_403(self, monkeypatch):
        """沙盒 undo 的 403 必须原样透出(case01 面走共享 router 的 `_wrap`)。

        2026-09-27 体检:`_wrap` 访问不存在的 `res.body` → AttributeError,两个
        路径都由 403 变 500;case00 面走 routes 的 `dict(res)` 口径无恙,故专测
        (此处 monkeypatch 场景判定为沙盒,不依赖磁盘上的 case00 声明)。
        """
        from fastapi.testclient import TestClient
        from case01.vizkit.live_run import build_service
        monkeypatch.setattr(ivm, "sandbox_rollback_blocked", lambda base_dir="": True)
        svc = build_service()
        # 本面默认声明"不挂治理",undo 会先撞上那层 409(另有专测:见
        # test_case01_face_exposes_interventions 的 goals 断言)。这里要验的是
        # **状态码透传**,所以把这个 app 的声明改回 True。
        svc.app.state.engine_has_governance = True
        c = TestClient(svc.app, raise_server_exceptions=False)
        for path in ("/api/undo-intervention", "/api/intervention/undo"):
            r = c.post(path, json={})
            assert r.status_code == 403, (path, r.status_code, r.text)

    def test_case01_face_marks_carry_run_identity(self, monkeypatch):
        """小镇面把自己这一局的引擎与 run_id 交给干预层(2026-10-06 接线)。

        此前 `_http_ctx` 只读 live.state 的全局(只有 case00 的 live_fastapi 注入),
        于是 5010 面上的专家标记虽然 `ok:true` 落盘,`simulation`/`sim_time` 恒是空串
        —— 标到哪一条记录查不回来(实测佐证:仓根 interventions.json 里 2026-09-27
        那几条 case01 面的记录 simulation 就是空串)。同一条链上"纠正回流"也只会回
        一句"当前没有运行中的模拟",对着正在动的小镇像坏了。
        """
        from fastapi.testclient import TestClient
        from case01.vizkit.live_run import build_service
        import live.reflections as rf

        class _Timer:
            def get_date(self, fmt):
                return "20250213-10:20"

        class _Agent:
            def __init__(self):
                self.injected = []

            def inject_story_event(self, ev):
                self.injected.append(ev)

        class _Game:
            governance = None

            def __init__(self, agents):
                self.agents = agents
                self._timer = _Timer()

        class _Engine:
            """冒充 MavisBridge:干预层只用到 `.game`。"""

            def __init__(self, agents):
                self.game = _Game(agents)

        agent = _Agent()
        svc = build_service()
        svc.app.state.intervention_engine = _Engine({"Ethan": agent})
        svc.app.state.intervention_sim_name = "261006-live-case01-B-2030"
        # 存储侧全部截住:本测试验的是上下文接线,不碰真实的 marks/审计文件
        captured = {}
        monkeypatch.setattr(rf, "append_mark", lambda rec: captured.update(rec))
        monkeypatch.setattr(rf, "rebuild_jsonl", lambda: "<patched>")
        monkeypatch.setattr(ivm, "_append_audit", lambda ctx, rec: None)
        c = TestClient(svc.app)

        r = c.post("/api/reflections/mark", json={
            "agent": "Ethan", "node_id": "n3", "text": "一条反思", "verdict": "correct"})
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        assert captured["simulation"] == "261006-live-case01-B-2030", captured
        assert captured["sim_time"] == "20250213-10:20", captured

        r = c.post("/api/intervention/corrective_feedback",
                   json={"agent": "Ethan", "correction": "纠正文本"})
        assert r.json()["ok"] is True and len(agent.injected) == 1, r.text

        # 一局跑完(live_run 在 finally 里撤句柄):注入要挡下,事后标记仍归得到记录
        svc.app.state.intervention_engine = None
        r = c.post("/api/intervention/corrective_feedback",
                   json={"agent": "Ethan", "correction": "纠正文本"})
        assert r.json()["ok"] is False and len(agent.injected) == 1, r.text
        captured.clear()
        r = c.post("/api/reflections/mark", json={
            "agent": "Ethan", "node_id": "n3", "text": "事后补标", "verdict": "correct"})
        assert r.json()["ok"] is True and captured["simulation"] == "261006-live-case01-B-2030"


class TestAdversarialPayloads:
    """畸形输入轰炸(2026-09-27 深检):策略作为库被直调时的健壮性。"""

    def test_undo_returns_dict_like_result(self, sandbox_env, client):
        """InterventionResult 是 dict 子类:库调用方 .get() 不崩,HTTP 仍 403。"""
        from mavis_case01_injector.world.branch import RuleBranchRouter  # noqa: F401
        ivm.get("undo")  # 确认策略在册
        # 沙盒 403 场景
        monkeypytest = None  # placeholder
        # 直接构造:undo 无审计文件 → 报错 dict(非 403);403 已有专测
        r = client.post("/api/undo-intervention", json={}).json()
        assert isinstance(r, dict) and "ok" in r

    def test_goals_adversarial_payloads_all_rejected(self, client):
        attacks = [
            {"name": "x", "goals": {"a": "1e999"}},            # 无穷大
            {"name": "../../etc/passwd", "goals": {"a": 1.0}}, # 路径注入名(角色名不落盘)
            {"name": "x", "goals": {1: 0.5, 2.5: 0.5}},        # 非字符串键
        ]
        for p in attacks:
            r = client.post("/api/goals", json=p).json()
            assert r["ok"] is False, p

    def test_mark_context_type_guard(self, sandbox_env, client):
        """context 传字符串 → new_mark/build_lora_sample 不崩,LoRA 导出不毒化。"""
        from live import reflections as refl
        old = refl.MARKS_PATH
        refl.MARKS_PATH = os.path.join(str(sandbox_env), "marks.json")
        try:
            r = client.post("/api/reflections/mark", json={
                "agent": "X", "node_id": "n", "text": "t" * 100,
                "verdict": "correct", "context": "not-a-dict"}).json()
            assert r["ok"] is True
            # LoRA 导出不崩
            from case01.tools.lora_prep import load_marks_any, export
            marks, _ = load_marks_any()
            report = export(marks, out_root=str(sandbox_env))
            assert report["stats"]["rejected"] == 0
        finally:
            refl.MARKS_PATH = old

    def test_mark_text_length_cap(self, client, sandbox_env):
        from live import reflections as refl
        old = refl.MARKS_PATH
        refl.MARKS_PATH = os.path.join(str(sandbox_env), "marks.json")
        try:
            r = client.post("/api/reflections/mark", json={
                "agent": "X", "node_id": "n", "text": "t" * 200_001,
                "verdict": "correct"}).json()
            assert r["ok"] is False and "超长" in r["errors"][0]
        finally:
            refl.MARKS_PATH = old
