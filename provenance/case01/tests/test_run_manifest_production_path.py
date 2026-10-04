# -*- coding: utf-8 -*-
"""生产路径(`python -m case01.run` → orchestrator)的 manifest 守卫(2026-10-04)。

manifest 的实现早就有(`mavis_case01_injector.manifest`,含"缺项必须留痕"的整套口径),
但**只有注入器路径挂它**。生产路径 `run_case01` 一路 `rec.save()` 直接落盘,于是今天
中午那批 h120 的 39 条、以及此前所有 `case01/runs` 里的记录,都没有"这次跑的是什么
commit / 哪种判定后端 / 哪份检索资料 / 哪版提示词"的指纹 —— 演示里问"这条记录能复现吗"
答不上来,反思是否被 max_tokens 截断也因此拿不到直接证据。

这里钉的是**新加的那一段接线**,不重复 `test_run_manifest.py` 已覆盖的共享实现:
1. 落盘前挂,盘上的 run.json 一定带 manifest,且 13 个必备键齐全;
2. `engine_id` 区分两条路(不能都自称 injector);
3. 三种分支来源(preset / judge / rules)各自的后端、模型名、温度如实;**没有模型客户端时
   不许按"本地 Ollama 默认"去猜**;规则路径的采样温度记 null 而不是 0.1;
4. 清单里的模型名取自这次真拿到的客户端,不另读一遍环境变量(免得与实跑漂移);
5. 哈希是真的吃了这次运行实际读的那份目录/文件;
6. 清单自己生成失败时,记录照写、失败照记(`manifest_error`),不静默丢段。
"""
import inspect
import io
import json
import os
import re

from case01 import orchestrator as OR
from case01.injector.manifest import REQUIRED_KEYS, dir_content_sha256
from case01.orchestrator import BRANCH_MODE_BY_SOURCE, RUN_ENGINE_ID, run_case01

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class StubJudgeClient:
    """判定客户端替身:只实现 `chat`,并自己声明后端/模型/温度。

    返回的 JSON 同时带 `branch` 和 `stance`,让分支判官与立场判官都能解析,
    这样一条桩就能走完"真判定"的路径而不碰 GPU。
    """

    def __init__(self, backend_kind="local", model="qwen3:8b", temperature=0.1):
        self.backend_kind = backend_kind
        self.chat_model = model
        self.temperature = temperature
        self.calls = 0

    def chat(self, messages, temperature=None, max_tokens=None, **kw):
        self.calls += 1
        return '{"branch": "B", "stance": "wait", "reason": "建议观望"}'


def _run(tmp_path, monkeypatch, run_id="probe", **kw):
    """把记录写进临时根(不污染 `case01/runs`),返回盘上的 dict。"""
    root = tmp_path / "records"
    monkeypatch.setenv("CASE01_RUNS_ROOT", str(root))
    rec = run_case01(no_llm=True, run_id=run_id, log=lambda *a, **k: None, **kw)
    p = os.path.join(str(root), run_id, "run.json")
    assert os.path.isfile(p), "记录没落在覆盖根:{}".format(p)
    return rec, json.load(io.open(p, encoding="utf-8"))


# ---------------------------------------------------------------- 盘上结构
def test_record_on_disk_carries_full_manifest(tmp_path, monkeypatch):
    """落盘文件里 manifest 必须齐 13 个必备键 —— 不能只在内存里挂好。"""
    _rec, saved = _run(tmp_path, monkeypatch, "m-keys", timeline="B")
    m = saved.get("manifest")
    assert isinstance(m, dict), "生产路径没挂 manifest(整段缺失)"
    missing = [k for k in REQUIRED_KEYS if k not in m]
    assert not missing, "manifest 缺必备键: {}".format(missing)
    assert m["engine_id"] == RUN_ENGINE_ID, m["engine_id"]
    assert m["manifest_version"], "版本号不能空"
    assert _HEX40.match(str(m["git_commit"])) or m["git_commit"] == "unknown", m["git_commit"]
    assert _HEX64.match(str(m["scenario_sha256"])), m["scenario_sha256"]
    assert m["financial_data_version"] in ("absent", ) or _HEX64.match(
        str(m["financial_data_version"])), m["financial_data_version"]
    # 带时区的运行时间:历史上只有 injector 记录有,现在生产路径也有了
    assert re.search(r"\+0800$|Z$|([+-]\d{4}$)", str(m["created_at"])), m["created_at"]
    assert m["prompt_versions"].get("reflection") and m["prompt_versions"].get("router")


def test_manifest_is_attached_before_the_file_is_written():
    """接线顺序守卫:清单必须在 `rec.save()` 之前挂上。

    事后补挂就分不清"跑的时候就有"和"后来补的",而 manifest 的意义正是前者。
    """
    src = inspect.getsource(OR.run_case01)
    assert "attach_manifest(" in src, "生产路径不再挂 manifest 了"
    assert src.index("attach_manifest(") < src.index("p = rec.save()"), \
        "manifest 挂在了落盘之后"


def test_every_branch_source_the_orchestrator_can_emit_maps_to_a_mode():
    """branch_source 的四种取值都要有 branch_mode 归属,新增第五种时这里要红。"""
    assert BRANCH_MODE_BY_SOURCE == {"preset": "preset", "judge": "judge",
                                     "judge-failed": "judge", "rules": "rules"}
    src = inspect.getsource(OR.run_case01)
    for token in ('branch_source = "preset"',
                  'branch_source = "judge-failed" if branch == "undetermined" else "judge"',
                  'branch_source = "rules"'):
        assert token in src, "分支来源变了但映射表没跟上:{}".format(token)
    # 表里的键必须正好覆盖编排器能产出的来源,不多不少(多了等于给不存在的路分模式)
    assert set(BRANCH_MODE_BY_SOURCE) == {"preset", "judge", "judge-failed", "rules"}


# ---------------------------------------------------------------- 三种分支来源
def test_preset_run_does_not_invent_a_model_backend(tmp_path, monkeypatch):
    """强制时间线 + 一个模型客户端都没有:后端记 rules、温度记 null。

    原实现在"没给 judge_llm"时按注入器真跑路径的兜底猜 local + 默认模型;
    生产路径的 no-llm 场合那个兜底不成立 —— 猜出来的 qwen3:4b 是凭空多出来的证据。
    """
    _rec, saved = _run(tmp_path, monkeypatch, "m-preset", timeline="A")
    m = saved["manifest"]
    assert m["branch_mode"] == "preset" and m["branch_source"] == "preset", m
    assert m["branch"] == "A"
    assert m["judge"] == "rules", m["judge"]
    assert m["judge_model"] is None, "没有模型客户端却记了模型名"
    assert m["temperature"]["judge"] is None, m["temperature"]
    assert any("temperature.judge=null" in w for w in m["manifest_warnings"]), \
        m["manifest_warnings"]


def test_rules_run_is_labeled_rules_not_preset_or_judge(tmp_path, monkeypatch):
    """no-llm 的规则判定不属于源里的 preset/judge,照实记 rules 并落 warning。"""
    _rec, saved = _run(tmp_path, monkeypatch, "m-rules")
    m = saved["manifest"]
    assert m["branch_source"] == "rules" and m["branch_mode"] == "rules", m
    assert any("branch_mode=" in w for w in m["manifest_warnings"]), m["manifest_warnings"]
    assert m["judge"] == "rules"


def test_quick_scan_fallback_leaves_a_trace_in_the_manifest(tmp_path, monkeypatch):
    """判官没上班这件事要留在 manifest_warnings,而不是只在日志里闪一次。

    第八节点名过的退化点:生产路径走 quick_scan 时只 print,不像 pipeline 那样
    记进清单 —— 平台侧读不到"这条 verdict 来自关键词快筛"。
    """
    _rec, saved = _run(tmp_path, monkeypatch, "m-qs", timeline="A")
    assert saved["consistency"]["method"] == "quick_scan", saved["consistency"]
    ws = saved["manifest"]["manifest_warnings"]
    assert any("立场判官未启用" in w for w in ws), ws


def test_judged_run_records_the_client_backend_and_model(tmp_path, monkeypatch):
    """分支真由客户端判出来时,后端/模型名/温度都取自那个客户端自己声明的值。"""
    stub = StubJudgeClient("local", "qwen3:8b", temperature=0.15)
    # router_llm 给了替身 → llm 仍为 None(no-llm 文本),判定与一致性都走这条客户端
    _rec, saved = _run(tmp_path, monkeypatch, "m-judge", router_llm=stub)
    m = saved["manifest"]
    assert stub.calls, "替身没被调用,说明这条没走判定路径"
    assert m["branch_source"] == "judge" and m["branch_mode"] == "judge", m
    assert m["judge"] == "local" and m["judge_model"] == "qwen3:8b", m
    assert m["temperature"]["judge"] == 0.15, m["temperature"]
    assert m["judge_backend_reason"] == "client.backend_kind='local'", m
    # 判官在场时不该有"未启用"那句(有客户端 = 真判过,不是快筛)
    assert saved["consistency"]["method"] == "llm_stance", saved["consistency"]
    assert not any("立场判官未启用" in w for w in m["manifest_warnings"]), m["manifest_warnings"]


def test_api_backend_records_api(tmp_path, monkeypatch):
    """外部 API 判定要记 api,不被类名映射之外的默认值盖掉。"""
    stub = StubJudgeClient("api", "nvidia/nemotron-3-super-480b-a55b", temperature=0.2)
    _rec, saved = _run(tmp_path, monkeypatch, "m-api", router_llm=stub)
    m = saved["manifest"]
    assert m["judge"] == "api" and m["judge_model"] == stub.chat_model, m


# ---------------------------------------------------------------- 哈希的可核对性
def test_hashes_are_of_what_this_run_actually_read(tmp_path, monkeypatch):
    """清单里的资料哈希必须等于本次 FIN_DIR() 的实算值 —— 不是另找一份目录凑数。"""
    _rec, saved = _run(tmp_path, monkeypatch, "m-hash")
    m = saved["manifest"]
    fin = OR.FIN_DIR()
    assert m["financial_data_dir"] == fin
    if os.path.isdir(fin):
        assert m["financial_data_version"] == dir_content_sha256(fin)
    else:
        assert m["financial_data_version"] == "absent"


def test_hash_moves_when_the_data_dir_moves(tmp_path, monkeypatch):
    """换一份资料目录,清单必须跟着变(否则指纹是摆设)。"""
    from case01.injector.manifest import build_manifest, collect_run_meta
    real = OR.FIN_DIR()
    other = tmp_path / "fin"
    (other / "hcm").mkdir(parents=True)
    (other / "hcm" / "doc.json").write_text('{"a": 1}', encoding="utf-8")
    m = build_manifest(collect_run_meta({}, branch="B", branch_mode="preset"),
                       financial_dir=str(other))
    assert m["financial_data_version"] == dir_content_sha256(str(other))
    assert m["financial_data_version"] != dir_content_sha256(real)


# ---------------------------------------------------------------- 后端与真客户端
def test_manifest_model_matches_the_client_the_run_uses(monkeypatch):
    """清单里的模型名必须等于这次真拿到的客户端所用的模型名,不是又读一遍环境变量。

    钉的是踩过的坑:批量台账里 `model_actual` 记空、"设了 8b 却仍跑 4b"(默认构造绕过
    `CASE01_LLM_MODEL`)。清单若自己再猜一次默认值,就会和真跑用的模型对不上还看不出来。
    """
    from case01.agents.llm import local_client_from_env
    from case01.injector.manifest import build_manifest, collect_run_meta

    monkeypatch.setenv("CASE01_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("CASE01_LLM_MODEL", "qwen3:8b")
    client = local_client_from_env()
    m = build_manifest(collect_run_meta({}, branch="B", branch_mode="judge",
                                        judge_llm=client),
                       engine_id=RUN_ENGINE_ID)
    assert m["judge"] == "local"
    assert m["judge_model"] == client.chat_model == "qwen3:8b", m["judge_model"]


# ---------------------------------------------------------------- 失败也要有声
def test_manifest_generation_failure_still_writes_the_record(tmp_path, monkeypatch):
    """清单自己炸了不能带走整条记录:照写,并把失败写进 manifest_error。"""
    import mavis_case01_injector.manifest as M

    def boom(*a, **k):
        raise RuntimeError("清单模块炸了")

    monkeypatch.setattr(M, "build_manifest", boom)
    _rec, saved = _run(tmp_path, monkeypatch, "m-boom", timeline="B")
    m = saved["manifest"]
    assert "RuntimeError" in m["manifest_error"], m
    assert m["engine_id"] == RUN_ENGINE_ID, "降级段也要说清是哪条路"
    assert m["manifest_warnings"], "失败必须留痕"
    assert saved.get("turns"), "记录主体还得在"


def test_blank_run_still_uses_shared_builder_defaults():
    """生产路径不另写一份清单实现:引擎面靠 build_manifest 的默认值说话。"""
    from case01.injector.manifest import build_manifest
    src = inspect.getsource(OR.run_case01)
    assert "engine_id=RUN_ENGINE_ID" in src
    assert "financial_dir=FIN_DIR()" in src, "清单要哈希本次真读的目录,不能用另一套默认路径"
    assert build_manifest({}, engine_id="x")["engine_id"] == "x"


def test_existing_manifest_is_not_overwritten(tmp_path):
    """重跑反思不许把第一次运行的环境指纹换掉(清单只在缺失时生成)。"""
    from case01.injector.manifest import attach_manifest
    record = {"run_id": "x", "manifest": {"created_at": "2026-01-01T00:00:00+0800",
                                          "engine_id": RUN_ENGINE_ID}}
    out = attach_manifest(record, engine_id=RUN_ENGINE_ID)
    assert out["manifest"]["created_at"] == "2026-01-01T00:00:00+0800"

