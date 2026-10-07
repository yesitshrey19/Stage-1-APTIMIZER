"""Development controls — setback, height and yield recommendations.

These figures are the ones a user is most likely to act on, so the tests target the
rules that are actually code-derived (NBC Part 3 open space, height vs road width) and
assert that anything merely indicative is labelled as needing verification.

    pytest backend/tests/devcontrols_test.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siteplan.devcontrols import (HEIGHT_OPEN_SPACE, MIN_ROAD_FOR_HIGHRISE,  # noqa: E402
                                  front_setback_for_plot, max_height_from_road,
                                  open_space_for_height, recommend)


def by_key(result):
    return {r["key"]: r for r in result["recommendations"]}


# ---------------------------------------------------------------- NBC open space
@pytest.mark.parametrize("height,expected", HEIGHT_OPEN_SPACE)
def test_open_space_matches_the_nbc_table(height, expected):
    assert open_space_for_height(height) == expected


def test_open_space_is_monotonic_in_height():
    """A taller building can never require less open space than a shorter one."""
    prev = 0.0
    for h in range(5, 90, 1):
        space = open_space_for_height(float(h))
        assert space >= prev, f"open space fell from {prev} to {space} at {h} m"
        prev = space


def test_open_space_is_capped_above_the_table():
    assert open_space_for_height(200.0) == 16.0


def test_front_setback_grows_with_plot_size():
    sizes = [200, 400, 800, 2000, 5000]
    values = [front_setback_for_plot(s) for s in sizes]
    assert values == sorted(values)
    assert front_setback_for_plot(200) == 3.0
    assert front_setback_for_plot(5000) == 12.0


# ---------------------------------------------------------------- height vs road
def test_height_is_limited_by_road_width():
    assert max_height_from_road(12.0, 6.0) == pytest.approx(1.5 * 18.0)


def test_no_road_means_no_derivable_cap():
    assert max_height_from_road(0.0, 6.0) == 0.0


def test_narrow_road_caps_a_large_plot():
    """A big plot on a lane cannot support what its FAR alone suggests."""
    wide = recommend(plot_area=8000, road_width=24, city="Bengaluru")
    narrow = recommend(plot_area=8000, road_width=9, city="Bengaluru")
    assert narrow["height_m"] <= wide["height_m"]
    assert narrow["height_m"] <= max_height_from_road(9, narrow["setbacks"]["front"]) + 0.1


def test_missing_road_width_is_reported_not_assumed():
    r = recommend(plot_area=5000, city="Bengaluru")
    assert not r["height_capped_by_road"]
    assert any("road width" in w.lower() for w in r["warnings"])


def test_very_narrow_road_warns_about_high_rise():
    r = recommend(plot_area=6000, road_width=6.0, city="Bengaluru")
    assert any(str(int(MIN_ROAD_FOR_HIGHRISE)) in w for w in r["warnings"])


# ---------------------------------------------------------------- setback coupling
def test_setback_is_recomputed_after_the_road_cap():
    """The open space demanded must match the height actually recommended, not the
    taller one that was rejected — otherwise a small plot gets an setback for a building
    it is not allowed to build."""
    r = recommend(plot_area=1200, road_width=9, city="Bengaluru")
    assert r["setbacks"]["side"] == open_space_for_height(r["height_m"])


def test_unbuildable_setback_regime_is_flagged():
    r = recommend(plot_area=1200, road_width=9, city="Bengaluru")
    assert any("too narrow" in w for w in r["warnings"])


def test_taller_recommendation_demands_deeper_setbacks():
    small = recommend(plot_area=3000, road_width=30, city="Bengaluru")
    large = recommend(plot_area=40000, road_width=30, city="Bengaluru")
    if large["height_m"] > small["height_m"]:
        assert large["setbacks"]["side"] >= small["setbacks"]["side"]


# ---------------------------------------------------------------- yield
def test_units_scale_with_plot_area():
    a = recommend(plot_area=5000, road_width=18, city="Bengaluru")["units"]
    b = recommend(plot_area=20000, road_width=18, city="Bengaluru")["units"]
    assert b > a


def test_higher_far_yields_more_units():
    low = recommend(plot_area=10000, road_width=24, far_override=1.0)
    high = recommend(plot_area=10000, road_width=24, far_override=3.0)
    assert high["units"] > low["units"]


def test_floors_and_height_are_consistent():
    r = recommend(plot_area=10000, road_width=24, floor_height=3.0)
    assert r["floors"] == max(int(r["height_m"] // 3.0), 1)


def test_zero_plot_area_is_rejected_cleanly():
    assert recommend(plot_area=0)["ok"] is False


# ---------------------------------------------------------------- provenance
def test_every_recommendation_declares_its_source():
    r = recommend(plot_area=5000, road_width=18, city="Bengaluru")
    for item in r["recommendations"]:
        assert item["source"], f"{item['key']} has no source"
        assert item["confidence"] in ("code", "indicative")


def test_far_and_coverage_are_flagged_for_verification():
    """These are bye-law figures that vary by authority and are revised — they must not
    be presented with the same authority as an NBC table."""
    items = by_key(recommend(plot_area=5000, road_width=18, city="Bengaluru"))
    assert items["far"]["requires_verification"] is True
    assert items["ground_coverage_pct"]["requires_verification"] is True


def test_nbc_derived_setbacks_are_marked_as_code():
    items = by_key(recommend(plot_area=5000, road_width=18, city="Bengaluru"))
    for key in ("front_setback", "side_setback", "rear_setback"):
        assert items[key]["confidence"] == "code"
        assert "NBC" in items[key]["source"]


def test_explicit_far_override_is_not_flagged_for_verification():
    items = by_key(recommend(plot_area=5000, road_width=18, far_override=2.5))
    assert items["far"]["requires_verification"] is False
    assert items["far"]["value"] == 2.5


def test_disclaimer_is_always_present():
    r = recommend(plot_area=5000, road_width=18, city="Bengaluru")
    assert "verified" in r["disclaimer"].lower()
