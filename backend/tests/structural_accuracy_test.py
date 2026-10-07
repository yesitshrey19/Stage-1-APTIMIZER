"""Tier 2 structural accuracy — the errors that pointed the unconservative way.

Two of these made a building look SAFER than it is, which is the direction that matters:
wind force omitted its force coefficient, and the seismic period used the bare-frame
formula on frames that carry brick infill. Both are asserted here in terms of the
*direction* of the correction, not just that a number changed.

    pytest backend/tests/structural_accuracy_test.py -v
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine  # noqa: E402
import engineering as englib  # noqa: E402
import iscodes as C  # noqa: E402


def project(engineering=None, floors=20, footprint=600.0):
    p = {
        "plot": {"length": 60.0, "width": 40.0, "coordinates": [], "road_edges": []},
        "towers": [{
            "id": "t1", "name": "Tower A", "floors": floors, "floor_height": 3.0,
            "footprint_area": footprint, "common_area": 40.0,
            "corridor_width": 1.5, "stair_min_width": 1.5, "stair_count": 2,
            "lift_count": 3, "exits_per_floor": 2, "max_travel_distance_m": 18.0,
            "units": [{"type": "2bhk", "count": 6, "carpet_area": 85.0}], "rooms": [],
        }],
        "parking": {"provided_slots": 60, "accessible_provided": 2, "ev_provided": 12,
                    "ramp": {"slope_pct": 10.0, "width": 3.6}},
        "engineering": {"city": "Bengaluru", "state": "Karnataka", **(engineering or {})},
    }
    return p


def analyse(floors=20, footprint=600.0, **over):
    p = project(engineering=over, floors=floors, footprint=footprint)
    return englib.analyse_engineering(p, engine.analyse(p))


def out(module):
    return {o["label"]: o["value"] for o in module["outputs"]}


# ---------------------------------------------------------------- wind force coefficient
def test_wind_force_applies_a_force_coefficient():
    """F = Cf x Ae x pd. Omitting Cf is the same as Cf = 1.0 and under-states the force."""
    loads = analyse()["modules"]["loads"]
    o = out(loads)
    cf = o["Force coefficient Cf"]
    assert cf >= C.WIND_CF_MIN
    assert o["Lateral wind force F = Cf·Ae·pd"] == pytest.approx(
        cf * o["Effective frontal area Ae"] * o["Design pressure pd (Kd·Ka·Kc)"], rel=1e-3)


def test_force_coefficient_raises_the_lateral_force():
    """The correction must be upward — this is the whole point of the fix."""
    o = out(analyse()["modules"]["loads"])
    with_cf = o["Lateral wind force F = Cf·Ae·pd"]
    without_cf = o["Effective frontal area Ae"] * o["Design pressure pd (Kd·Ka·Kc)"]
    assert with_cf > without_cf
    assert with_cf / without_cf >= 1.2


def test_cf_grows_with_slenderness():
    squat = C.wind_force_coefficient(1.0, 0.5)
    slender = C.wind_force_coefficient(1.0, 10.0)
    assert slender > squat


def test_cf_is_floored():
    assert C.wind_force_coefficient(4.0, 0.1) >= C.WIND_CF_MIN


def test_unverified_cf_table_is_surfaced_not_hidden():
    """A safety-critical figure reproduced from memory must say so in the output."""
    loads = analyse()["modules"]["loads"]
    if not C.WIND_CF_VERIFIED:
        assert any("VERIFIED" in o["note"] for o in loads["outputs"]
                   if o["label"] == "Force coefficient Cf")
        assert any("not been verified" in w["text"] for w in loads["warnings"])


def test_k1_and_k3_are_configurable_not_pinned_to_one():
    base = out(analyse()["modules"]["loads"])["Design wind pressure pz"]
    topo = out(analyse(wind_k3=1.36)["modules"]["loads"])["Design wind pressure pz"]
    assert topo > base, "topography factor k3 had no effect"


# ---------------------------------------------------------------- seismic period
def test_infilled_frame_has_a_shorter_period_than_a_bare_frame():
    bare = out(analyse(frame_type="bare_frame")["modules"]["seismic"])
    infill = out(analyse(frame_type="brick_infill")["modules"]["seismic"])
    assert infill["Fundamental period Ta"] < bare["Fundamental period Ta"]


def test_infilled_frame_demands_more_base_shear():
    """A shorter period sits higher on the spectrum — this is the unconservative error
    that was previously baked in for every residential project."""
    bare = out(analyse(frame_type="bare_frame")["modules"]["seismic"])
    infill = out(analyse(frame_type="brick_infill")["modules"]["seismic"])
    assert infill["Design base shear VB"] > bare["Design base shear VB"]


def test_default_frame_type_is_infilled():
    """Residential apartments carry brick infill — the safe default is the stiffer model."""
    assert C.DEFAULT_FRAME_TYPE == "brick_infill"
    default = out(analyse()["modules"]["seismic"])
    infill = out(analyse(frame_type="brick_infill")["modules"]["seismic"])
    assert default["Fundamental period Ta"] == infill["Fundamental period Ta"]


def test_period_formula_matches_the_code_expression():
    h, d = 45.0, 25.0
    ta, _ = C.seismic_period(h, d, "brick_infill")
    assert ta == pytest.approx(min(0.075 * h ** 0.75, 0.09 * h / math.sqrt(d)), rel=1e-6)


def test_bare_frame_formula_is_unchanged():
    h = 30.0
    ta, desc = C.seismic_period(h, 20.0, "bare_frame")
    assert ta == pytest.approx(0.075 * h ** 0.75, rel=1e-9)
    assert "0.075" in desc


def test_period_formula_is_reported_to_the_user():
    seismic = analyse()["modules"]["seismic"]
    note = next(o["note"] for o in seismic["outputs"] if o["label"] == "Fundamental period Ta")
    assert "0.09" in note and "sqrt(d)" in note


# ---------------------------------------------------------------- material grades
def test_column_sizing_responds_to_concrete_grade():
    m25 = out(analyse(concrete_grade=25, steel_grade=415)["modules"]["loads"])["Recommended column size"]
    m40 = out(analyse(concrete_grade=40, steel_grade=415)["modules"]["loads"])["Recommended column size"]
    assert m25 != m40, "column size ignored the selected concrete grade"


def test_stronger_materials_give_a_smaller_column():
    def area(size):
        a, b = size.split("×")
        return float(a.strip()) * float(b.strip())
    weak = area(out(analyse(concrete_grade=25, steel_grade=415)["modules"]["loads"])["Recommended column size"])
    strong = area(out(analyse(concrete_grade=40, steel_grade=500)["modules"]["loads"])["Recommended column size"])
    assert strong < weak


def test_column_note_states_the_grades_actually_used():
    loads = analyse(concrete_grade=35, steel_grade=500)["modules"]["loads"]
    note = next(o["note"] for o in loads["outputs"] if o["label"] == "Recommended column size")
    assert "M35" in note and "Fe500" in note


def test_steel_percentage_respects_the_code_minimum():
    """IS 456 Cl. 26.5.3.1 — 0.8% is the floor, whatever the user types."""
    low = out(analyse(column_steel_pct=0.1)["modules"]["loads"])["Recommended column size"]
    floor = out(analyse(column_steel_pct=0.8)["modules"]["loads"])["Recommended column size"]
    assert low == floor


# ---------------------------------------------------------------- slenderness / eccentricity
def test_slenderness_is_computed_and_reported():
    o = out(analyse()["modules"]["loads"])
    assert o["Slenderness ratio (least dimension)"] > 0
    assert o["Minimum eccentricity e_min"] >= 20.0


def test_slender_column_is_flagged_critical():
    """Cl. 39.3 does not apply to a slender column; sizing one with it is unsafe."""
    loads = analyse(unsupported_length_m=9.0)["modules"]["loads"]
    slender = [w for w in loads["warnings"] if "SLENDER" in w["text"]]
    assert slender and slender[0]["severity"] == "critical"


def test_short_column_is_not_flagged():
    loads = analyse(unsupported_length_m=2.4)["modules"]["loads"]
    assert not [w for w in loads["warnings"] if "SLENDER" in w["text"]]


def test_minimum_eccentricity_breach_is_flagged():
    """e_min = l/500 + D/30 exceeds 0.05D only once l > 8.33 D, so the breach needs a
    SMALL column on a tall storey — a lightly loaded low-rise frame, not a deep one."""
    loads = analyse(floors=3, footprint=200.0,
                    unsupported_length_m=6.0, grid_bay_x_m=3.0, grid_bay_y_m=3.0)["modules"]["loads"]
    o = out(loads)
    depth = float(o["Recommended column size"].split("×")[1])
    assert o["Minimum eccentricity e_min"] > 0.05 * depth, "test did not build a breaching case"
    assert any("eccentricity" in w["text"].lower() for w in loads["warnings"])


def test_deep_column_does_not_trip_the_eccentricity_flag():
    """The converse: a deep column has a large 0.05D allowance and must stay clean."""
    loads = analyse(unsupported_length_m=3.0)["modules"]["loads"]
    assert not any("eccentricity" in w["text"].lower() for w in loads["warnings"])


# ---------------------------------------------------------------- per-tower consistency
def test_per_tower_wind_uses_the_same_coefficient_path():
    loads = analyse()["modules"]["loads"]
    for t in loads["per_tower"]:
        assert t["cf"] >= C.WIND_CF_MIN
        assert t["wind_force_kn"] > 0


def test_per_tower_seismic_uses_the_configured_frame_type():
    bare = analyse(frame_type="bare_frame")["modules"]["seismic"]["per_tower"][0]
    infill = analyse(frame_type="brick_infill")["modules"]["seismic"]["per_tower"][0]
    assert infill["period_s"] < bare["period_s"]
    assert infill["base_shear_kn"] > bare["base_shear_kn"]


def test_per_tower_columns_use_the_selected_grades():
    a = analyse(concrete_grade=25, steel_grade=415)["modules"]["loads"]["per_tower"][0]
    b = analyse(concrete_grade=40, steel_grade=500)["modules"]["loads"]["per_tower"][0]
    assert a["column_size_mm"] != b["column_size_mm"]
