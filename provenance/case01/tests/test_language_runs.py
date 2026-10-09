"""New Case01 runs use English; older records retain their recorded language."""

import pytest
import json
from pathlib import Path

from case01 import reflection
from mavis_case01_injector.language import apply_role_text
from mavis_case01_injector.bridge import MavisBridge
from mavis_case01_injector.nodes import default_nodes
from mavis_case01_injector.pipeline import rerun_router_only, run_pipeline


class CapturingLLM:
    def __init__(self, answer):
        self.answer = answer
        self.messages = []

    def chat(self, messages, **kwargs):
        self.messages.append(messages)
        return self.answer


def test_role_text_is_separate_and_legacy_is_unchanged():
    config = {"agents": {"Investment AI": {"role_directive": "old"},
                          "Ethan Lin": {"role_directive": "old"}}}
    apply_role_text(config, tuple(config["agents"]), "legacy")
    assert config["agents"]["Investment AI"]["role_directive"] == "old"
    apply_role_text(config, tuple(config["agents"]), "zh")
    assert "请用中文" in config["agents"]["Investment AI"]["role_directive"]
    assert "请用中文" in config["agents"]["Ethan Lin"]["role_directive"]
    config["agents"]["Ethan Lin"]["role_directive"] = "old"
    apply_role_text(config, tuple(config["agents"]), "en")
    assert "Respond in English" in config["agents"]["Investment AI"]["role_directive"]
    assert config["agents"]["Ethan Lin"]["role_directive"] == "old"


def test_english_role_uses_original_scene_settings():
    root = Path(__file__).resolve().parents[3]
    agents = {}
    for name in ("Investment AI", "Ethan Lin"):
        path = root / "provenance" / "case01" / "injector" / "scenario" / "agents" / name / "agent.json"
        agents[name] = json.loads(path.read_text(encoding="utf-8"))
    original = json.loads(json.dumps(agents))
    apply_role_text({"agents": agents}, tuple(agents), "en")
    assert agents["Investment AI"]["role_directive"] == original["Investment AI"]["role_directive"].replace(
        "Respond in Chinese.", "Respond in English.")
    assert agents["Ethan Lin"] == original["Ethan Lin"]
    for name in agents:
        assert {k: v for k, v in agents[name].items() if k != "role_directive"} == {
            k: v for k, v in original[name].items() if k != "role_directive"}


def test_raw_language_survives_mapping_and_manifest():
    raw = run_pipeline(branch="B", dry_run=True)
    assert raw["language"] == "en"
    assert raw["injector"]["language"] == "en"
    assert raw["manifest"]["language"] == "en"
    assert raw["manifest"]["prompt_versions"]["role_text"]
    mapped = run_pipeline(raw_record=raw["injector"], dry_run=True)
    assert mapped["language"] == "en"
    assert mapped["manifest"]["language"] == "en"
    with pytest.raises(ValueError, match="conflicts"):
        run_pipeline(raw_record=raw["injector"], dry_run=True, language="zh")

    old_raw = dict(raw["injector"])
    old_raw.pop("language")
    old_mapped = run_pipeline(raw_record=old_raw, dry_run=True)
    assert old_mapped["language"] == "legacy"


def test_bridge_raw_record_manifest_has_language(tmp_path):
    bridge = MavisBridge(nodes=default_nodes("B"), dry_run=True, branch="B")
    bridge.run()
    path = tmp_path / "raw.json"
    bridge.save(str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["language"] == "en"
    assert raw["manifest"]["language"] == "en"
    assert raw["manifest"]["prompt_versions"]["branch_judge"]


def test_english_reflection_and_router_use_english_prompts():
    llm = CapturingLLM("I relied too heavily on the rumor and overlooked its source.")
    result = reflection.run_reflection(llm, {"turns": []}, language="en")
    assert "Respond in English" in llm.messages[0][0]["content"]
    assert "Review the event" in llm.messages[0][1]["content"]
    assert result["quality"]["status"] in {"pass", "review", "fail"}
    assert result["quality"]["max_score"] == 100

    router = CapturingLLM("[]")
    result = reflection.run_router(router, "I overlooked the source.", language="en")
    assert result["issues"] == []
    assert "Respond in English" in router.messages[0][0]["content"]
    assert "Available expert category IDs" in router.messages[0][1]["content"]


def test_router_rerun_uses_record_language(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"language": "en", "reflection": {"text": "I overlooked the source."}}),
                    encoding="utf-8")
    router = CapturingLLM("[]")
    rerun_router_only(str(path), router_llm=router)
    assert "Respond in English" in router.messages[0][0]["content"]


def test_chinese_prompts_are_archived_together():
    from mavis_case01_injector.world import branch
    path = (Path(__file__).resolve().parents[3] / "packages" / "mavis-case01-injector"
            / "src" / "mavis_case01_injector" / "prompts_zh.json")
    archive = json.loads(path.read_text(encoding="utf-8"))
    assert archive["role_text"]["Ethan Lin"]["role_directive"]
    assert archive["reflection_prompt"] == reflection.REFLECTION_PROMPT_CN
    assert archive["router_prompt"] == reflection.ROUTER_PROMPT_CN
    assert archive["branch_judge"] == branch.JUDGE_PROMPT
