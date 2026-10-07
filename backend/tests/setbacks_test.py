"""Fix 1: setbacks have one owner and one stored value.

The bug this closes: setbacks were editable in Plot & Site (local component state) AND in
Setbacks & Controls (stored on the project). A user could set one value in one screen, see
a different value in the other, and get an envelope built from the second. Worse,
`dev_controls` was in neither the server's allowed patch fields nor the frontend's editable
list, so the screen that owned them never actually saved.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, server
from siteplan.devcontrols import (setback_minimums, validate_setbacks,
                                  front_setback_for_plot, open_space_for_height,
                                  MIN_ROAD_FOR_HIGHRISE)
from defaults import default_project


@pytest.fixture(scope="module")
def proj():
    p = default_project("SB", "QA", "Hyderabad", "S-1", "owner")
    return p, engine.analyse(p)


# ---------------------------------------------------------------- statutory minimums
def test_front_setback_is_the_larger_of_plot_and_height_rules(proj):
    """A large plot and a tall building each set a floor; the binding one is the higher."""
    m = setback_minimums(plot_area=15219.0, road_width=12.0, height_m=36.0)
    assert m["front"]["minimum_m"] == max(front_setback_for_plot(15219.0),
                                          open_space_for_height(36.0))


def test_the_minimum_rises_with_building_height(proj):
    """This is the whole point of tying open space to height."""
    low = setback_minimums(1000.0, 12.0, 10.0)["side"]["minimum_m"]
    high = setback_minimums(1000.0, 12.0, 50.0)["side"]["minimum_m"]
    assert high > low


def test_every_edge_names_the_rule_and_the_clause():
    """A value rejected without a reason is a value the user overrides."""
    m = setback_minimums(15219.0, 12.0, 36.0)
    for edge in ("front", "rear", "side", "default"):
        assert m[edge]["rule"]
        assert "NBC" in m[edge]["clause"]


def test_a_narrow_road_is_flagged():
    m = setback_minimums(15219.0, road_width=MIN_ROAD_FOR_HIGHRISE - 3, height_m=40.0)
    assert "_note" in m and "high-rise" in m["_note"]


# ---------------------------------------------------------------- validation
def test_setbacks_below_the_minimum_are_reported_with_the_shortfall():
    v = validate_setbacks({"front": 3, "rear": 3, "side": 3, "default": 3},
                          plot_area=15219.0, road_width=12.0, height_m=36.0)
    assert v["ok"] is False
    for row in v["edges"]:
        assert row["ok"] is False
        assert row["shortfall_m"] > 0
        assert row["rule"] and row["clause"]


def test_setbacks_at_or_above_the_minimum_pass():
    mins = setback_minimums(15219.0, 12.0, 36.0)
    applied = {e: mins[e]["minimum_m"] for e in ("front", "rear", "side", "default")}
    assert validate_setbacks(applied, 15219.0, 12.0, 36.0)["ok"] is True


def test_the_shipped_defaults_do_not_clear_the_minimum_for_a_tall_block(proj):
    """Recorded deliberately: 9/4.5/4.5/6 m was never checked against the code. On the
    sample project -- 36 m on 15,219 m2 -- every edge is short. Enabling validation is
    supposed to surface that, not hide it."""
    _, an = proj
    v = validate_setbacks(server.DEFAULT_SETBACKS,
                          plot_area=an["areas"]["plot_area_sqm"],
                          road_width=12.0, height_m=an["areas"]["max_height_m"])
    assert v["ok"] is False
    assert all(r["shortfall_m"] > 0 for r in v["edges"])


def test_a_missing_edge_reads_as_zero_not_a_crash():
    v = validate_setbacks({}, 1000.0, 9.0, 20.0)
    assert v["ok"] is False
    assert all(r["applied_m"] == 0 for r in v["edges"])


def test_junk_input_does_not_crash_validation():
    v = validate_setbacks({"front": "abc", "rear": None}, 1000.0, 9.0, 20.0)
    assert v["ok"] is False


# ---------------------------------------------------------------- one owner
def test_dev_controls_is_persistable():
    """It was in neither the allowed patch set nor the frontend editable list, so every
    setback edit was silently discarded on reload."""
    import inspect
    src = inspect.getsource(server.patch_project)
    assert '"dev_controls"' in src


def test_the_seed_matches_what_plot_module_used_to_hold():
    """Migration must not change the behaviour of a project saved before setbacks were
    stored."""
    assert server.DEFAULT_SETBACKS == {"default": 6.0, "front": 9.0, "rear": 4.5, "side": 4.5}


def test_the_envelope_and_the_validator_read_the_same_minimum():
    """One function decides what "too small" means, so the screen that edits setbacks and
    the engine that builds the envelope cannot disagree."""
    from siteplan import validate_setbacks as exported
    assert exported is validate_setbacks
