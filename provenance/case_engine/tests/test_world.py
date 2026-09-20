# -*- coding: utf-8 -*-
"""case_engine/world.py 单元测试(通用层,不带业务词)。"""
import pytest

from case_engine.world import World, WorldConfig, EntityState, PublicEvent

SCHEMA = {
    "cash": {"initial": 200000.0, "type": "float"},
    "held": {"initial": False, "type": "bool"},
    "entry": {"initial": None, "type": "float"},
    "ps": {"initial": "", "type": "str"},      # personal-note 占位(场景自定义字段)
}


def _world():
    cfg = WorldConfig(run_id="t", start_date="2026-01-01", end_date="2026-01-04",
                      timeline_events={
                          "2026-01-02": [{"kind": "price", "summary": "s1", "value": 10.0}],
                          "2026-01-04": [{"kind": "media", "summary": "s2"}],
                      })
    return World(cfg, SCHEMA)


def test_entity_state_created_from_schema():
    st = EntityState(SCHEMA)
    assert st.get("cash") == 200000.0
    assert st.get("held") is False
    assert st.has("held") is False
    st.set("held", True)
    assert st.has("held") is True
    assert st.to_dict()["entry"] is None


def test_delta_pct_uses_value_keys():
    st = EntityState({"ref": {"initial": 45.2, "type": "float"},
                      "cur": {"initial": 27.4, "type": "float"}})
    pct = st.delta_pct("ref", "cur")
    assert pct is not None and abs(pct + 0.3938) < 0.001
    assert EntityState({"ref": {"initial": None}}).delta_pct("ref", "cur") is None


def test_advance_releases_events_in_order():
    w = _world()
    w.advance_to("2026-01-02")
    assert len(w.released) == 1 and w.released[0].kind == "price"
    w.advance_to("2026-01-04")
    assert len(w.released) == 2
    assert [e.kind for e in w.released] == ["price", "media"]


def test_advance_no_duplicate_on_revisit():
    w = _world()
    w.advance_to("2026-01-04")
    w.advance_to("2026-01-04")
    assert len(w.released) == 2


def test_snapshot_reset_on_set_branch():
    w = _world()
    w.advance_to("2026-01-02")
    w.update_state("cash", 50.0)
    w.set_branch("A", {"timeline": "A"})
    assert w.branch == "A"
    assert w.config.timeline == "A"
    assert w.agent.get("cash") == 200000.0  # 重置


def test_tool_visible_only_date_and_released():
    w = _world()
    w.advance_to("2026-01-02")
    v = w.tool_visible()
    assert v["current_date"] == "2026-01-02"
    assert "own_state" not in v


def test_user_visible_hides_private_keys():
    w = _world()
    w.advance_to("2026-01-02")
    w.update_state("ps", "secret")
    v = w.user_visible(["ps"])
    assert "ps" not in v["own_state"]
    assert v["own_state"]["cash"] == 200000.0


def test_user_visible_shows_all_when_no_hide():
    w = _world()
    w.update_state("ps", "secret")
    v = w.user_visible()
    assert "ps" in v["own_state"]


def test_disclose_returns_private_fields():
    w = _world()
    w.update_state("ps", "secret")
    assert w.disclose(["ps"]) == {"ps": "secret"}


def test_update_state_audited():
    w = _world()
    w.update_state("cash", 123.0)
    notes = [e for e in w.audit() if e["action"] == "update_state"]
    assert notes and notes[0]["key"] == "cash" and notes[0]["to"] == 123.0


def test_rewind_raises():
    w = _world()
    w.advance_to("2026-01-04")
    with pytest.raises(ValueError):
        w.advance_to("2026-01-02")


def test_public_event_dict_has_value():
    ev = PublicEvent("2026-01-02", "price", "s", value=10.0)
    assert ev.to_dict()["value"] == 10.0