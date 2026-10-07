"""Quantity take-off from the designed structure."""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, engineering as englib, takeoff as T, iscodes as C
from defaults import default_project


def proj(**eng):
    p = default_project("T", "C", "Hyderabad", "S", "o")
    p["engineering"] = {"city": "Hyderabad", "state": "Telangana", **eng}
    return p


def run(**eng):
    p = proj(**eng)
    return p, T.structural_takeoff(p, engine.analyse(p))


# ---------------------------------------------------------------- geometry
def test_grid_counts_matches_hand_arithmetic():
    g = T.grid_counts(2500.0, 5.0, 5.0)          # 50 m x 50 m
    assert g["nx"] == 11 and g["ny"] == 11        # 11 grid lines each way
    assert g["columns"] == 121
    assert g["beam_length_m"] == pytest.approx(11 * 50 + 11 * 50)


def test_a_wider_grid_needs_fewer_columns():
    tight = T.grid_counts(2500.0, 4.0, 4.0)["columns"]
    wide = T.grid_counts(2500.0, 8.0, 8.0)["columns"]
    assert wide < tight


def test_empty_footprint_is_not_a_crash():
    assert T.grid_counts(0, 5, 5)["columns"] == 0


# ---------------------------------------------------------------- member sizing
def test_column_section_rounds_to_25mm_and_respects_minimums():
    b, d = T.column_section(90000)
    # 230 mm is the floor, chosen to sit flush with a brick wall, so it is deliberately
    # not a 25 mm multiple; anything above the floor is rounded to a buildable size.
    assert b >= 230 and d >= 300
    big_b, big_d = T.column_section(500000)
    assert big_b % 25 == 0 and big_d % 25 == 0


def test_column_grows_with_load():
    small = T.column_section(T.column_required_area(500, 25, 415, 1.0))
    large = T.column_section(T.column_required_area(5000, 25, 415, 1.0))
    assert large[0] * large[1] > small[0] * small[1]


def test_higher_grade_concrete_gives_a_smaller_column():
    a25 = T.column_required_area(3000, 25, 415, 1.0)
    a40 = T.column_required_area(3000, 40, 415, 1.0)
    assert a40 < a25


def test_beam_depth_follows_span_over_twelve():
    w, d = T.beam_section(6.0, "simply supported")
    assert d == math.ceil(6000 / 12 / 25) * 25
    assert T.beam_section(6.0, "continuous")[1] < d      # continuous is shallower


def test_takeoff_and_engineering_agree_on_the_column():
    """Both modules must size the same column. They were written out separately once, and
    that is exactly how the app came to size for M25 after the user had picked M40."""
    p = proj(concrete_grade=40, steel_grade=500, column_steel_pct=1.5)
    eng = englib.analyse_engineering(p, engine.analyse(p))
    reported = next(o["value"] for o in eng["modules"]["loads"]["outputs"]
                    if "column size" in o["label"].lower())
    load = eng["modules"]["loads"]["derived"]["column_load"]
    b, d = T.column_section(T.column_required_area(load, 40, 500, 1.5))
    assert reported == f"{int(b)} × {int(d)}"


def test_takeoff_and_engineering_agree_on_the_mix():
    p = proj(concrete_grade=30, exposure_condition="severe", aggregate_size_mm=20)
    eng = englib.analyse_engineering(p, engine.analyse(p))
    boq = {r["material"]: r for r in eng["modules"]["mix"]["boq_link"]["rows"]}
    mine = T.mix_proportions(30, "severe", 20)
    assert boq["Cement"]["per_cum"] == pytest.approx(mine["cement_kg"])
    assert boq["Coarse aggregate"]["per_cum"] == pytest.approx(mine["coarse_kg"])
    assert boq["Fine aggregate (sand)"]["per_cum"] == pytest.approx(mine["fine_kg"])


# ---------------------------------------------------------------- take-off behaviour
def test_quantities_respond_to_the_design_not_just_the_area():
    """The whole point: a thumb rule per m2 cannot tell these two apart."""
    _, low = run(column_steel_pct=1.0)
    _, high = run(column_steel_pct=2.5)
    assert high["totals"]["steel_kg"] > low["totals"]["steel_kg"]
    assert high["towers"][0]["column_section_mm"] != low["towers"][0]["column_section_mm"]


def test_more_floors_means_a_heavier_column_and_more_concrete():
    p = proj()
    a = engine.analyse(p)
    short = T.structural_takeoff(p, a)
    p2 = proj(); p2["towers"][0]["floors"] = 30
    tall = T.structural_takeoff(p2, engine.analyse(p2))
    assert tall["totals"]["concrete_m3"] > short["totals"]["concrete_m3"] * 2
    assert tall["towers"][0]["footing_size_m"] > short["towers"][0]["footing_size_m"]


def test_soft_soil_needs_bigger_footings():
    _, hard = run(soil_type="dense sand")
    _, soft = run(soil_type="soft clay")
    assert soft["towers"][0]["footing_size_m"] > hard["towers"][0]["footing_size_m"]
    assert soft["towers"][0]["concrete"]["footings"] > hard["towers"][0]["concrete"]["footings"]


def test_a_high_seismic_zone_adds_confinement_steel():
    p = proj(); p["engineering"].update(city="Bhuj", state="Gujarat")
    z4 = T.structural_takeoff(p, engine.analyse(p))
    _, z2 = run()
    if z4["basis"]["ductile_detailing"]:
        assert z4["basis"]["seismic_zone"] in T.DUCTILE_ZONES
        assert z4["towers"][0]["steel"]["columns"] > z2["towers"][0]["steel"]["columns"]


def test_column_steel_is_arithmetic_not_a_thumb_rule():
    _, r = run(column_steel_pct=2.0)
    t = r["towers"][0]
    d = t["column_steel_detail"]
    # Main bars are exactly the specified percentage of the column concrete...
    assert d["longitudinal_kg"] == pytest.approx(t["concrete"]["columns"] * 0.02 * T.STEEL_DENSITY, rel=0.01)
    # ...lateral ties are always present (IS 456 Cl. 26.5.3.2)...
    assert d["ties_kg"] > 0
    # ...and the bill is bars plus ties, with confinement and laps applied on top.
    expected = (d["longitudinal_kg"] + d["ties_kg"]) * d["ductile_factor"] * d["lap_allowance"]
    assert t["steel"]["columns"] == pytest.approx(expected, rel=0.01)


def test_the_parts_add_up_to_the_total():
    _, r = run()
    for t in r["towers"]:
        assert sum(v for k, v in t["concrete"].items() if k != "total_m3") == pytest.approx(
            t["concrete"]["total_m3"], rel=1e-6)
        assert sum(v for k, v in t["steel"].items() if k != "total_kg") == pytest.approx(
            t["steel"]["total_kg"], abs=5)


def test_default_soil_is_not_the_best_ground_in_india():
    """The soil table starts at hard rock, 3240 kN/m2. Defaulting to the first entry would
    size every footing on a project that never set a soil type as if it sat on rock."""
    _, r = run()
    assert r["basis"]["sbc_kn_sqm"] == C.SOILS["dense sand"]["sbc"]


def test_results_sit_inside_the_qs_sanity_band():
    _, r = run()
    assert r["warnings"] == [], r["warnings"]


def test_an_absurd_slab_trips_the_sanity_band():
    _, r = run(slab_thickness_mm=600)
    assert any(w["metric"] == "concrete" for w in r["warnings"])


# ---------------------------------------------------------------- engine integration
def test_structural_items_are_flagged_as_taken_off():
    a = engine.analyse(proj())
    by = {i["key"]: i for i in a["quantities"]["items"]}
    assert a["quantities"]["derived"] is True
    for k in ("concrete", "steel", "aggregate", "formwork"):
        assert by[k]["source"] == "take-off"
    # Cement and sand also carry the brick and plaster mortar the mix design does not see.
    for k in ("cement", "sand"):
        assert by[k]["source"] == "take-off + mortar"
    for k in ("tiles", "paint", "doors"):
        assert by[k]["source"] == "ratio", f"{k} genuinely scales with area or units"


def test_takeoff_can_be_turned_off():
    p = proj()
    a = engine.analyse(p)
    ratio_only = engine.quantities(p, a["areas"], use_takeoff=False)
    assert all(i["source"] == "ratio" for i in ratio_only["items"])
    assert ratio_only["derived"] is False


def test_a_takeoff_failure_falls_back_rather_than_breaking_the_bill():
    p = proj()
    a = engine.analyse(p)
    broken = dict(a["areas"]); broken["towers"] = [{"bad": True}]
    q = engine.quantities(p, broken)
    assert q["items"] and all(i["quantity"] >= 0 for i in q["items"])


# ---------------------------------------------------------------- cost adders
def test_adders_are_applied_in_sequence_on_the_running_total():
    b = engine.analyse(proj())["boq"]
    running = b["works_total"]
    for a in b["adders"]:
        assert a["on"] == pytest.approx(running, rel=1e-6)
        assert a["amount"] == pytest.approx(running * a["pct"] / 100.0, rel=1e-6)
        running = round(running + a["amount"], 2)
    assert b["grand_total"] == pytest.approx(running, rel=1e-6)


def test_grand_total_now_exceeds_the_bare_works_cost():
    b = engine.analyse(proj())["boq"]
    assert b["grand_total"] > b["works_total"]
    assert b["adders_total"] == pytest.approx(b["grand_total"] - b["works_total"], rel=1e-6)


def test_wastage_means_more_is_bought_than_is_placed():
    b = engine.analyse(proj())["boq"]
    steel = next(m for m in b["materials"] if m["key"] == "steel")
    assert steel["wastage_pct"] > 0
    assert steel["quantity_ordered"] > steel["quantity"]
    assert steel["amount"] == pytest.approx(steel["quantity_ordered"] * steel["rate"], rel=1e-6)


def test_adders_are_configurable_and_can_be_zeroed():
    p = proj()
    p["cost_adders"] = {k: 0 for k in engine.DEFAULT_COST_ADDERS}
    b = engine.analyse(p)["boq"]
    assert b["grand_total"] == pytest.approx(b["works_total"], rel=1e-6)


def test_downstream_consumers_still_work():
    p = proj()
    a = engine.analyse(p)
    assert a["cost"]["total"] == a["boq"]["grand_total"]
    assert a["compliance"]["total"] > 0
    englib.analyse_engineering(p, a)
    import schedule as S
    assert S.plan_schedule(p, a, {"start_date": "2026-01-05"}, summary=True)["ok"]
