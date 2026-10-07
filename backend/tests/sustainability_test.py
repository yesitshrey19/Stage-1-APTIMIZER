"""Block 1 sustainability tests.

The double-count case is the reason this file exists: the take-off derives cement, sand
and aggregate FROM the concrete volume, so a naive embodied-carbon model that also gives
concrete a ready-mix coefficient counts the clinker twice and roughly doubles the answer.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, engineering as E, gis, iscodes as C
from defaults import default_project


@pytest.fixture(scope="module")
def eng():
    p = default_project("T", "C", "Hyderabad", "S", "o")
    base = engine.analyse(p)
    return p, base, E.analyse_engineering(p, base)


# ---------------------------------------------------------------- embodied carbon
def test_concrete_carbon_does_not_double_count_its_own_cement(eng):
    """takeoff.structural_takeoff derives cement_bags from the concrete volume via the
    mix design. Concrete must therefore carry batching and placing only -- if it ever
    carries a full ready-mix coefficient again, this catches it."""
    p, base, r = eng
    q = {i["key"]: i for i in base["quantities"]["items"]}
    mix_cement_per_m3 = 372.0
    implied_bags = q["concrete"]["quantity"] * mix_cement_per_m3 / 50.0
    mortar_bags = sum(m["cement_bags"] for m in base["quantities"]["mortar"].values())
    # The bill's cement IS the concrete's cement plus the mortar's -- never a ready-mix
    # purchase on top of either.
    assert q["cement"]["quantity"] == pytest.approx(implied_bags + mortar_bags, rel=0.02)
    # So the concrete coefficient must be a placing figure, not a ready-mix one.
    assert C.EMBODIED_CARBON["concrete"]["factor"] < 50
    rows = {m["key"]: m for m in r["modules"]["carbon"]["materials"]}
    assert rows["cement"]["tco2e"] > rows["concrete"]["tco2e"] * 5


def test_carbon_total_is_the_sum_of_its_materials(eng):
    _, _, r = eng
    c = r["modules"]["carbon"]
    assert sum(m["tco2e"] for m in c["materials"]) == pytest.approx(
        c["derived"]["total_tco2e"], abs=0.05)
    assert sum(m["share_pct"] for m in c["materials"]) == pytest.approx(100.0, abs=0.5)


def test_carbon_lands_in_a_defensible_range_for_indian_rcc(eng):
    _, _, r = eng
    per_sqm = r["modules"]["carbon"]["derived"]["per_sqm_kg"]
    assert 100 < per_sqm < 700, f"{per_sqm} kgCO2e/m2 is outside any plausible range"
    assert r["modules"]["carbon"]["derived"]["cement_share_pct"] > 20   # clinker dominates


def test_carbon_bands_label_the_band_the_project_is_in():
    """350 kgCO2e/m2 is typical for Indian RCC, not 'low'."""
    def band(v):
        b = "low"
        for t, n in C.CARBON_BENCHMARKS:
            if v >= t:
                b = n
        return b
    assert band(200) == "low"
    assert band(350) == "typical"
    assert band(480) == "high"
    assert band(600) == "very high"


def test_materials_without_a_defensible_factor_are_named_not_silently_dropped(eng):
    _, _, r = eng
    unpriced = r["modules"]["carbon"]["unpriced"]
    assert unpriced and "Doors" in unpriced


def test_carbon_module_matches_the_shape_every_other_module_returns(eng):
    _, _, r = eng
    c = r["modules"]["carbon"]
    for key in ("id", "title", "codes", "missing", "outputs", "recommendation"):
        assert key in c, key
    o = c["outputs"][0]
    assert set(o) == {"label", "value", "unit", "clause", "note"}


# ---------------------------------------------------------------- solar
def test_annual_insolation_matches_published_indian_ghi():
    """Calibration guard. Published GHI: Hyderabad ~1950, Delhi ~1800, Bengaluru ~2000
    kWh/m2/yr. If the clear-sky model or clearness factor drifts, this fails."""
    for lat, lng, expected in [(17.4, 78.5, 1950), (28.6, 77.2, 1800), (12.97, 77.6, 2000)]:
        got = gis.annual_insolation(lat, lng)["annual_kwh_per_sqm"]
        assert abs(got - expected) / expected < 0.12, f"{got} vs {expected} at lat {lat}"


def test_insolation_is_seasonal_and_higher_in_summer_up_north():
    monthly = gis.annual_insolation(28.6, 77.2)["monthly"]
    assert len(monthly) == 12
    assert sum(m["days"] for m in monthly) == 365
    assert monthly[4]["kwh_per_sqm_day"] > monthly[11]["kwh_per_sqm_day"]   # May beats Dec


def test_specific_yield_is_realistic_for_indian_rooftop_pv():
    """1400-1600 kWh per kWp per year is the accepted Indian range."""
    r = gis.solar_potential(17.4, 78.5, 1200)
    assert 1300 < r["specific_yield_kwh_per_kwp"] < 1700
    assert r["payback_years"] and 3 < r["payback_years"] < 12


def test_solar_scales_with_roof_and_is_zero_without_one():
    big = gis.solar_potential(17.4, 78.5, 2400)
    small = gis.solar_potential(17.4, 78.5, 1200)
    assert big["installable_kwp"] == pytest.approx(small["installable_kwp"] * 2, rel=1e-6)
    none = gis.solar_potential(17.4, 78.5, 0)
    assert none["installable_kwp"] == 0 and none["payback_years"] is None


def test_solar_config_overrides_apply():
    cheap = gis.solar_potential(17.4, 78.5, 1200, {"cost_per_kwp": 25000})
    base = gis.solar_potential(17.4, 78.5, 1200)
    assert cheap["payback_years"] < base["payback_years"]


# ---------------------------------------------------------------- plantation
def test_plantation_reports_both_norms_and_names_the_binding_one(eng):
    """The bye-law count and the canopy target disagree; planting to the smaller one
    silently under-delivers, so both are reported."""
    _, _, r = eng
    d = r["modules"]["trees"]["derived"]
    assert d["required"] > 0 and d["for_canopy"] > 0
    expected = "canopy cover" if d["for_canopy"] > d["required"] else "bye-law count"
    assert d["binding"] == expected


def test_tree_count_follows_the_open_space_norm(eng):
    _, base, r = eng
    open_sqm = base["areas"]["open_space_sqm"]
    import math
    assert r["modules"]["trees"]["derived"]["required"] == math.ceil(
        open_sqm / C.TREE_NORMS["sqm_open_space_per_tree"])


def test_zone_tree_counts_sum_to_the_plan(eng):
    _, _, r = eng
    t = r["modules"]["trees"]
    assert sum(z["trees"] for z in t["zones"]) == t["derived"]["planted"]
    for z in t["zones"]:
        assert sum(s["count"] for s in z["species"]) == z["trees"]


def test_avenue_zones_never_get_aggressive_rooted_species(eng):
    """Peepal next to a ring road lifts the paving and cracks the drains."""
    _, _, r = eng
    for z in r["modules"]["trees"]["zones"]:
        if z["kind"] == "avenue":
            assert all(s["roots"] != "aggressive" for s in z["species"])


def test_plantation_flags_indicative_zones_when_no_layout_exists(eng):
    _, _, r = eng
    assert any("site layout" in w["text"].lower() for w in r["modules"]["trees"]["warnings"])


def test_sequestration_is_not_sold_as_an_offset(eng):
    """Planting absorbs a couple of tonnes a year against a four-figure embodied total.
    The copy must not imply the two cancel."""
    _, _, r = eng
    note = next(o["note"] for o in r["modules"]["trees"]["outputs"]
                if o["label"] == "Carbon absorbed")
    assert "not an offset" in note


def test_engineering_summary_carries_the_new_sustainability_figures(eng):
    _, _, r = eng
    s = r["summary"]
    for k in ("embodied_carbon_tco2e", "carbon_per_sqm_kg", "trees_required"):
        assert k in s and s[k] is not None
