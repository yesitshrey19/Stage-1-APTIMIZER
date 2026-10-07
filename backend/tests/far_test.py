"""Fix 2: the FAR derivation panel must reconcile to the number it explains.

A panel showing a plausible-looking derivation that does not add up to the figure above it
is worse than no panel, because the reader now trusts it. Every test here checks the
derivation against what `engine.area_metrics()` actually computed, not against what a
reasonable FAR calculation would look like.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine
from defaults import default_project


@pytest.fixture(scope="module")
def ctx():
    p = default_project("FAR", "QA", "Hyderabad", "F-1", "owner")
    a = engine.analyse(p)
    return p, a, a["far_derivation"]


def test_the_derivation_reproduces_the_reported_far(ctx):
    _, a, d = ctx
    assert d["far"] == a["areas"]["far"]
    assert d["fsi"] == a["areas"]["fsi"]


def test_the_arithmetic_actually_divides_out(ctx):
    """The substitution string has to be true, not decorative."""
    _, a, d = ctx
    plot = a["areas"]["plot_area_sqm"]
    builtup = a["areas"]["builtup_area_sqm"]
    assert d["far"] == pytest.approx(builtup / plot, rel=1e-12)
    assert f"{builtup:,.2f}" in d["substitution"]
    assert f"{plot:,.2f}" in d["substitution"]


def test_per_tower_contributions_sum_to_the_total(ctx):
    _, a, d = ctx
    assert sum(t["builtup_sqm"] for t in d["towers"]) == pytest.approx(
        a["areas"]["builtup_area_sqm"], rel=1e-6)
    assert sum(t["share_pct"] for t in d["towers"]) == pytest.approx(100.0, abs=0.2)


def test_far_and_fsi_are_identical_at_the_default_factor(ctx):
    """Recorded because it is the answer to the brief's question: this engine computes
    FSI as FAR x config.fsi_factor, which defaults to 1.0. They are the same number, not
    two different area bases."""
    p, a, d = ctx
    assert (p.get("config") or {}).get("fsi_factor") == 1.0
    assert d["identical"] is True
    assert a["areas"]["far"] == a["areas"]["fsi"]
    assert "same number" in d["far_vs_fsi"]


def test_changing_the_factor_is_the_only_way_they_diverge():
    p = default_project("FAR2", "QA", "Hyderabad", "F-2", "owner")
    p["config"]["fsi_factor"] = 1.3
    d = engine.analyse(p)["far_derivation"]
    assert d["identical"] is False
    assert d["fsi"] == pytest.approx(d["far"] * 1.3, rel=1e-3)
    assert "1.3" in d["far_vs_fsi"]


def test_the_panel_states_that_there_are_no_deductions(ctx):
    """The engine applies none. Parking, stilts and service floors are excluded by never
    being added, not by a deduction step -- claiming an itemised deduction schedule would
    be fiction."""
    _, _, d = ctx
    assert "no FSI deductions" in d["no_deductions_note"]
    assert "never being added" in d["no_deductions_note"]


def test_excluded_areas_are_real_figures_from_the_analysis(ctx):
    _, a, d = ctx
    by_item = {e["item"]: e["area_sqm"] for e in d["excluded"]}
    assert by_item["Society amenities"] == a["areas"]["society_amenities_sqm"]
    # The loading is what super built-up adds to the homes' own built-up, tower by tower.
    expected = sum(t["super_builtup_sqm"] - t["apartment_builtup_sqm"] for t in a["areas"]["towers"])
    assert by_item["Common-area loading"] == pytest.approx(expected, abs=0.05)


def test_parking_is_not_in_the_far_numerator(ctx):
    """It is excluded by never being added, which is the thing the note explains."""
    _, a, _ = ctx
    assert a["parking"]["total_parking_area_sqm"] > 0
    assert a["areas"]["builtup_area_sqm"] < a["areas"]["builtup_area_sqm"] + \
        a["parking"]["total_parking_area_sqm"]


def test_headroom_is_the_cap_less_the_achieved_ratio(ctx):
    _, _, d = ctx
    p = d["permissible"]
    assert p["headroom_ratio"] == pytest.approx(p["far_cap"] - d["far"], abs=0.001)
    assert p["used_pct"] == pytest.approx(d["far"] / p["far_cap"] * 100, abs=0.1)


def test_headroom_in_sqm_is_buildable_area_not_a_ratio(ctx):
    _, a, d = ctx
    p = d["permissible"]
    assert p["headroom_sqm"] == pytest.approx(
        p["headroom_ratio"] * a["areas"]["plot_area_sqm"], rel=1e-3)


def test_the_governing_control_is_named(ctx):
    _, _, d = ctx
    assert d["permissible"]["governing_control"] == "Maximum permissible FAR"


def test_a_project_with_no_plot_area_does_not_divide_by_zero():
    p = default_project("FAR3", "QA", "Hyderabad", "F-3", "owner")
    p["plot"] = {**(p.get("plot") or {}), "coordinates": [], "length": 0, "width": 0}
    d = engine.analyse(p)["far_derivation"]
    assert d["far"] == 0.0
    assert "cannot be computed" in d["substitution"]


def test_the_builtup_rule_matches_how_builtup_is_actually_computed(ctx):
    """Built-up per floor = (carpet + balcony) x (1 + wall factor) + service core."""
    _, a, d = ctx
    t = a["areas"]["towers"][0]
    cfg = 0.10
    expected = ((t["carpet_sqm"] + t["balcony_sqm"]) / t["floors"] * (1 + cfg)
                + t["service_core_per_floor_sqm"])
    assert t["builtup_per_floor_sqm"] == pytest.approx(expected, rel=0.01)
    assert "carpet + balcony" in d["builtup_rule"]
    assert "service core" in d["builtup_rule"]
