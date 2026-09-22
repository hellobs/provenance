# -*- coding: utf-8 -*-
"""运行清单(manifest)+ 原子落盘的守卫测试(2026-09-22)。

守的是三件事:
1. **可复现**:同一份 scenario 哈希稳定,改一个字节就变;
2. **不静默**:缺资料目录时 `financial_data_version == "absent"` 且 `manifest_warnings`
   非空;判定后端(local/api/rules)如实进记录,判不出来要留痕;
3. **不留半截**:落盘走"临时文件 → os.replace",中途失败时目标文件仍是旧内容,
   且目录里没有残留的临时文件。

用 dry-run + 桩,不联网、不加载 mavis、不调 Ollama。
"""
import glob
import json
import os

import pytest

from case01.atomicio import atomic_write_text
from case01.injector.manifest import (
    MANIFEST_VERSION, REQUIRED_KEYS, attach_manifest, build_manifest,
    collect_run_meta, file_sha256, sha256_12, text_sha256,
)
from case01.injector.pipeline import run_pipeline


# ----------------------------------------------------------------------
# 测试替身/工具
# ----------------------------------------------------------------------
class FakeJudge:
    """判定后端替身:只声明"我是哪种后端",不实现判定逻辑。"""

    def __init__(self, backend_kind, model, temperature=0.1):
        self.backend_kind = backend_kind
        self.chat_model = model
        self.temperature = temperature


def _judge_kind(**kw):
    """从 collect_run_meta 的结果里取 judge 类型(测试只关心这一栏)。"""
    return collect_run_meta({}, **kw)["judge"]


def _raw_record(branch="B", branch_mode="preset", **extra):
    raw = {"schema_version": "injector-0.1", "run_id": "m-1", "mode": "mavis",
           "branch": branch, "branch_mode": branch_mode, "roles": [],
           "nodes": [{"node_id": "node-1", "date": "2026-08-27", "step": 1,
                      "released_events": [], "events": [],
                      "world_state": {"date": "2026-08-27", "branch": branch}}],
           "world_audit": [], "summary": {"node_count": 1}}
    raw.update(extra)
    return raw


# ----------------------------------------------------------------------
# 1) 必备键齐全
# ----------------------------------------------------------------------
def test_manifest_has_all_required_keys(tmp_path):
    """dry-run 落盘的记录里必须有完整的 manifest 段,键一个不缺。"""
    out = tmp_path / "run.json"
    rec = run_pipeline(branch="B", dry_run=True, out_path=str(out))
    saved = json.load(open(out, encoding="utf-8"))

    assert isinstance(saved.get("manifest"), dict), "记录里必须有 manifest 段"
    missing = [k for k in REQUIRED_KEYS if k not in saved["manifest"]]
    assert missing == [], "manifest 缺键: {}".format(missing)
    assert saved["manifest"]["manifest_version"] == MANIFEST_VERSION
    # 与内存里返回的记录是同一份(dict 相等;落盘走的是同一次构造)
    assert saved["manifest"] == rec["manifest"]
    # 真实时间与当前仓 commit:不是占位
    assert len(saved["manifest"]["created_at"]) >= 19
    assert saved["manifest"]["git_commit"] != ""
    assert saved["manifest"]["engine_id"] == "case01-injector"


def test_manifest_written_before_reflection_and_survives(tmp_path):
    """manifest 在写盘前挂好:接反思只补反思/路由,不许把清单冲掉。"""
    out = tmp_path / "run.json"
    rec = run_pipeline(branch="B", dry_run=True, out_path=str(out))
    before = json.loads(json.dumps(rec["manifest"]))
    # 再挂一次(模拟"重跑 Router 又走了一遍 attach")→ 必须原样保留,不覆盖
    again = attach_manifest(rec, collect_run_meta({}))
    assert again["manifest"] == before


# ----------------------------------------------------------------------
# 2) 缺资料目录:absent + 非空 warnings
# ----------------------------------------------------------------------
def test_financial_data_absent_is_loud(tmp_path):
    """资料目录不存在 → financial_data_version == "absent" 且 warnings 说明缺什么。"""
    missing_dir = tmp_path / "no_such_financial_dir"
    m = build_manifest(collect_run_meta({}), financial_dir=str(missing_dir),
                       scenario_path="")

    assert m["financial_data_version"] == "absent"
    assert m["manifest_warnings"], "缺资料目录必须有 manifest_warnings"
    assert any("absent" in w and str(missing_dir) in w
               for w in m["manifest_warnings"]), m["manifest_warnings"]
    # 缺项只影响这一栏:其余键仍在(不许因为一个缺项把整段削掉)
    assert [k for k in REQUIRED_KEYS if k not in m] == []


def test_financial_data_hash_stable_and_content_bound(tmp_path):
    """资料目录存在时:内容不变哈希稳定,改一个字节就变。"""
    d = tmp_path / "financial" / "hcm"
    d.mkdir(parents=True)
    (d / "docs.json").write_text('[{"title": "a"}]', encoding="utf-8")
    first = build_manifest(collect_run_meta({}), financial_dir=str(tmp_path / "financial"))
    second = build_manifest(collect_run_meta({}), financial_dir=str(tmp_path / "financial"))
    assert first["financial_data_version"] == second["financial_data_version"]
    assert len(first["financial_data_version"]) == 64

    (d / "docs.json").write_text('[{"title": "b"}]', encoding="utf-8")
    changed = build_manifest(collect_run_meta({}), financial_dir=str(tmp_path / "financial"))
    assert changed["financial_data_version"] != first["financial_data_version"]


# ----------------------------------------------------------------------
# 3) scenario 哈希:同内容稳定、一字节变
# ----------------------------------------------------------------------
def test_scenario_hash_stable_and_one_byte_sensitive(tmp_path):
    scenario = tmp_path / "scenario.yaml"
    scenario.write_text("meta:\n  case_id: case01_stock\n", encoding="utf-8")

    h1 = build_manifest(collect_run_meta({}), scenario_path=str(scenario))["scenario_sha256"]
    h2 = build_manifest(collect_run_meta({}), scenario_path=str(scenario))["scenario_sha256"]
    assert h1 == h2 and len(h1) == 64

    scenario.write_text("meta:\n  case_id: case01_stockX\n", encoding="utf-8")   # 一个字节
    h3 = build_manifest(collect_run_meta({}), scenario_path=str(scenario))["scenario_sha256"]
    assert h3 != h1

    # CRLF/LF 折成同一份内容(Windows 上 checkout 不应改变哈希)
    scenario.write_bytes(b"meta:\r\n  case_id: case01_stockX\r\n")
    assert file_sha256(str(scenario)) == h3


def test_scenario_missing_is_null_with_warning(tmp_path):
    m = build_manifest(collect_run_meta({}), scenario_path=str(tmp_path / "nope.yaml"))
    assert m["scenario_sha256"] is None
    assert any("scenario_sha256" in w for w in m["manifest_warnings"])


# ----------------------------------------------------------------------
# 4) 原子写:中途异常 → 旧内容仍在、无残留临时文件
# ----------------------------------------------------------------------
def test_atomic_write_failure_keeps_old_content_and_no_temp_left(tmp_path):
    target = tmp_path / "run.json"
    old = '{"old": true}'
    target.write_text(old, encoding="utf-8")

    import case_engine.atomicio as atomicio

    real_replace = os.replace
    seen_tmp = []

    def boom(src, dst, *a, **kw):
        seen_tmp.append(src)                    # 记下临时文件名(供残留检查)
        raise OSError("模拟写入中途磁盘错误")

    atomicio.os.replace = boom
    try:
        with pytest.raises(OSError):
            atomicio.write_json_atomic(str(target), {"new": True})
    finally:
        atomicio.os.replace = real_replace

    assert seen_tmp, "应当先写临时文件再 replace"
    assert target.read_text(encoding="utf-8") == old, "失败时目标文件必须仍是旧内容"
    leftovers = [p for p in glob.glob(str(tmp_path / "*")) if os.path.basename(p) != "run.json"]
    assert leftovers == [], "失败后不许留下任何残留临时文件: {}".format(leftovers)


def test_pipeline_write_failure_does_not_clobber_existing_record(tmp_path, monkeypatch):
    """走完整 run_pipeline:落盘失败时,目标文件仍是上一次那条记录。"""
    out = tmp_path / "run.json"
    good = run_pipeline(branch="B", dry_run=True, out_path=str(out))
    before = out.read_text(encoding="utf-8")

    import case_engine.atomicio as atomicio

    def boom(src, dst, *a, **kw):
        raise OSError("模拟写入中途磁盘错误")

    monkeypatch.setattr(atomicio.os, "replace", boom)
    with pytest.raises(OSError):
        run_pipeline(branch="A", dry_run=True, out_path=str(out))

    assert out.read_text(encoding="utf-8") == before
    assert json.load(open(out, encoding="utf-8"))["manifest"] == good["manifest"]
    assert [p for p in os.listdir(str(tmp_path)) if p != "run.json"] == []


def test_atomic_write_text_replaces_whole_file(tmp_path):
    """正常路径:一次写入就是完整新内容(不是"追加/截断一半")。"""
    target = tmp_path / "turns.jsonl"
    atomic_write_text(str(target), '{"a": 1}\n{"a": 2}\n')
    atomic_write_text(str(target), '{"a": 3}\n')
    assert target.read_text(encoding="utf-8") == '{"a": 3}\n'


# ----------------------------------------------------------------------
# 5) judge 字段如实反映后端(local / api / rules 各一次)
# ----------------------------------------------------------------------
def test_judge_reports_local_backend():
    assert _judge_kind(judge_llm=FakeJudge("local", "qwen3:4b")) == "local"
    # 真客户端类名也能判出来(local 双实现:HG 权重 / Ollama)
    from case01.agents.llm import LocalHFClient, OllamaClient
    assert _judge_kind(judge_llm=OllamaClient()) == "local"
    assert _judge_kind(judge_llm=LocalHFClient(chat_model_path="x")) == "local"


def test_judge_reports_api_backend():
    assert _judge_kind(judge_llm=FakeJudge("api", "minimax/minimax-m3:free")) == "api"
    from case01.agents.llm import OpenRouterClient
    # 没配 key 时客户端构造会抛;只验类名映射,不碰网络
    assert collect_run_meta({}, judge_llm=object.__new__(OpenRouterClient))["judge"] == "api"


def test_judge_reports_rules_backend():
    assert _judge_kind(backend_kind="rules") == "rules"
    from case01.world.branch import RuleBranchRouter
    assert _judge_kind(judge_llm=RuleBranchRouter()) == "rules"


def test_judge_unknown_is_loud_not_silent():
    """判不出后端时如实写 unknown + 原因,不许默默写个 local 蒙混过去。"""
    class Mystery:
        pass

    meta = collect_run_meta({}, judge_llm=Mystery())
    assert meta["judge"] == "unknown"
    m = build_manifest(meta, financial_dir="")
    assert any("judge=" in w for w in m["manifest_warnings"]), m["manifest_warnings"]
    assert m["judge"] == "unknown"


def test_judge_fields_flow_into_saved_record(tmp_path):
    """端到端:judge/api 与 rules 两种后端都如实进落盘记录。"""
    out_api = tmp_path / "api.json"
    run_pipeline(branch="B", dry_run=True, out_path=str(out_api),
                 judge_llm=FakeJudge("api", "some/model", temperature=0.25))
    m_api = json.load(open(out_api, encoding="utf-8"))["manifest"]
    assert m_api["judge"] == "api"
    assert m_api["judge_model"] == "some/model"
    assert m_api["temperature"]["judge"] == 0.25

    out_rules = tmp_path / "rules.json"
    run_pipeline(branch="B", dry_run=True, out_path=str(out_rules),
                 judge_backend_kind="rules")
    m_rules = json.load(open(out_rules, encoding="utf-8"))["manifest"]
    assert m_rules["judge"] == "rules"
    assert m_rules["judge_model"] is None


def test_branch_mode_and_judge_prompt_version_recorded(tmp_path):
    """branch_mode 如实;judge_prompt_version 是判定提示词的 12 位哈希。"""
    rec = run_pipeline(branch="B", dry_run=True, branch_mode="judge")
    assert rec["manifest"]["branch_mode"] == "judge"
    v = rec["manifest"]["judge_prompt_version"]
    assert isinstance(v, str) and len(v) == 12

    from case01.world.branch import JUDGE_PROMPT
    assert v == text_sha256(JUDGE_PROMPT)[:12]
    assert v == sha256_12(JUDGE_PROMPT)

    # 反思/路由提示词版本也记了
    pv = rec["manifest"]["prompt_versions"]
    assert len(pv.get("reflection", "")) == 12 and len(pv.get("router", "")) == 12
    assert pv.get("scenario_branch_judge"), "scenario 的判定提示词也要留版本"


def test_prompt_hash_is_content_bound():
    assert sha256_12("abc") == text_sha256("abc")[:12]
    assert sha256_12("abc") != sha256_12("abd")
    # 已经是哈希的直接截前 12 位,不二次哈希(否则与原始哈希对不上)
    full = text_sha256("abc")
    assert sha256_12(full) == full[:12]
