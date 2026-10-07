"""Block 3 optimiser tests.

The contract every one of these must hold is that it reports a CHANGE, not a description:
current, best, and the levers between. A test that only checks a number exists would pass
on an optimiser that optimises nothing, so each one here checks the change too.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, engineering as E, optimise as O, iscodes as C
from defaults import default_project


@pytest.fixture(scope="module")
def ctx():
    p = default_project("T", "C", "Hyderabad", "S", "o")
    a = engine.analyse(p)
    e = E.analyse_engineering(p, a)
    return p, a, e


@pytest.fixture(scope="module")
def opt(ctx):
    p, a, e = ctx
    return O.analyse(p, a, e)


ALL = ("waste", "quantity", "budget", "materials")


# ---------------------------------------------------------------- shared contract
@pytest.mark.parametrize("key", ALL)
def test_every_optimiser_reports_current_best_and_the_change(opt, key):
    """Constraint 7: a number that only describes the present scheme is not an optimiser."""
    o = opt[key]
    assert set(o["current"]) >= {"value", "unit", "label"}
    assert set(o["best"]) >= {"value", "unit", "label"}
    assert o["changes"], f"{key} proposes no change"
    for c in o["changes"]:
        assert set(c) >= {"lever", "from", "to", "effect"}, c


@pytest.mark.parametrize("key", ALL)
def test_delta_agrees_with_current_and_best(opt, key):
    o = opt[key]
    assert o["delta"]["value"] == pytest.approx(
        abs(o["current"]["value"] - o["best"]["value"]), abs=0.02)


def test_rates_are_read_from_the_bill_not_the_quantity_items(ctx):
    """quantities()["items"] carries no rate. Reading one off it yields zero and makes
    every cost optimiser report a free project, which is how this was first written."""
    p, a, e = ctx
    assert all("rate" not in i for i in a["quantities"]["items"])
    priced = O._priced(a)
    assert priced["steel"]["rate"] > 0
    assert O.quantity_optimisation(p, a, e)["current"]["value"] > 0


# ---------------------------------------------------------------- waste
def test_cutting_to_a_schedule_beats_cutting_member_by_member(opt):
    o = opt["waste"]
    assert o["best"]["value"] < o["current"]["value"]
    assert o["steel"]["bars_packed"] < o["steel"]["bars_naive"]


def test_first_fit_decreasing_never_overfills_a_bar():
    lengths = [5.0, 4.2, 3.1, 7.5, 2.0, 6.0, 11.9, 0.4]
    bars = O._first_fit_decreasing(lengths, O.REBAR_STOCK_M)
    for b in bars:
        assert sum(b) <= O.REBAR_STOCK_M + 1e-9
    # Nothing may be lost or invented in the nesting.
    flat = sorted(x for b in bars for x in b)
    assert flat == sorted(lengths)


def test_a_member_longer_than_stock_is_not_silently_dropped():
    bars = O._first_fit_decreasing([15.0, 3.0], O.REBAR_STOCK_M)
    assert len(bars) == 2                       # the 15 m needs its own bar plus a splice


def test_waste_is_compared_like_with_like_not_against_the_bill_allowance(opt):
    """The bill's flat wastage allowance is a commercial figure, not a cutting result.
    Comparing the schedule against it made good practice look like a regression."""
    o = opt["waste"]
    assert "member by member" in o["current"]["label"]
    assert "nested schedule" in o["best"]["label"]
    assert any("commercial allowance" in n for n in o["notes"])


def test_tile_waste_falls_with_smaller_modules(opt):
    rows = {r["module_mm"]: r["waste_pct"] for r in opt["waste"]["tiles"]}
    assert rows[300] < rows[800]


# ---------------------------------------------------------------- grades
def test_grade_sweep_never_selects_below_the_is456_minimum(ctx):
    """IS 456 sets a minimum grade per exposure. The sweep may show M20 for a moderate
    exposure but must never recommend it."""
    p, a, e = ctx
    for exposure, floor in (("mild", 20), ("moderate", 25), ("severe", 30), ("very severe", 35)):
        e2 = {**e, "config": {**e["config"], "exposure_condition": exposure}}
        sweep = O._grade_sweep(a, e2)
        assert sweep["min_grade"] == floor
        for r in sweep["rows"]:
            grade = int(r["concrete_grade"][1:])
            assert r["legal"] == (grade >= floor)
        best = O.quantity_optimisation(p, a, e2)["best"]["label"]
        assert int(best.split("M")[1].split(" ")[0]) >= floor


def test_illegal_grades_are_shown_with_a_reason_not_hidden(ctx):
    p, a, e = ctx
    rows = O.quantity_optimisation(p, a, e)["options"]
    illegal = [r for r in rows if not r["legal"]]
    assert illegal and all(r["reason"] for r in illegal)


def test_a_higher_steel_grade_reduces_steel_weight(ctx):
    _, a, e = ctx
    rows = {(r["concrete_grade"], r["steel_grade"]): r for r in O._grade_sweep(a, e)["rows"]}
    assert rows[("M25", "Fe550")]["steel_kg"] < rows[("M25", "Fe415")]["steel_kg"]


# ---------------------------------------------------------------- budget
def test_budget_reports_the_shortfall_rather_than_pretending_to_reach_it(ctx):
    """An impossible target must come back infeasible and say what is missing, not
    silently return the best it could do as if that were the answer."""
    p, a, e = ctx
    total = a["boq"]["grand_total"]
    hard = O.budget_optimisation(p, a, e, total * 0.2)
    assert hard["feasible"] is False
    assert hard["shortfall"] > 0
    assert any("less building" in n for n in hard["notes"])

    easy = O.budget_optimisation(p, a, e, total * 0.97)
    assert easy["feasible"] is True and easy["shortfall"] == 0


def test_budget_only_ever_offers_compliant_levers(ctx):
    p, a, e = ctx
    r = O.budget_optimisation(p, a, e, a["boq"]["grand_total"] * 0.85)
    assert all(lv["compliant"] for lv in r["options"])
    assert all(lv.get("cost_elsewhere") for lv in r["options"])


def test_budget_defaults_to_ten_percent_under_when_no_target_given(ctx):
    p, a, e = ctx
    r = O.budget_optimisation(p, a, e, 0)
    assert r["target"] == pytest.approx(a["boq"]["grand_total"] * 0.9, rel=1e-6)


# ---------------------------------------------------------------- materials
def test_materials_never_recommend_something_dearer_and_dirtier(opt):
    for fam in opt["materials"]["options"]:
        cur = next(o for o in fam["options"] if o["option"] == fam["current"])
        rec = next(o for o in fam["options"] if o["option"] == fam["recommended"])
        assert not (rec["cost"] > cur["cost"] and rec["carbon_t"] > cur["carbon_t"])


def test_materials_respect_exposure_compliance(ctx):
    _, a, e = ctx
    e2 = {**e, "config": {**e["config"], "exposure_condition": "mild"}}
    r = O.material_recommendations(a, e2)
    for fam in r["options"]:
        rec = next(o for o in fam["options"] if o["option"] == fam["recommended"])
        assert rec["compliant"], f'{fam["family"]} recommended a non-compliant option'


def test_carbon_price_is_named_and_actually_drives_the_ranking(ctx):
    """The cost/carbon trade-off decides the answer, so the price must be explicit --
    at zero the cheapest option always wins and the carbon column is decoration."""
    _, a, e = ctx
    assert O.CARBON_PRICE_INR_PER_TONNE > 0
    base = O.material_recommendations(a, e)
    assert any(f"{O.CARBON_PRICE_INR_PER_TONNE:,.0f}" in n for n in base["notes"])

    old = O.CARBON_PRICE_INR_PER_TONNE
    try:
        O.CARBON_PRICE_INR_PER_TONNE = 0.0
        cheap = O.material_recommendations(a, e)
        for fam in cheap["options"]:
            rec = next(o for o in fam["options"] if o["option"] == fam["recommended"])
            legal = [o for o in fam["options"] if o["compliant"]]
            assert rec["cost"] == min(o["cost"] for o in legal)
    finally:
        O.CARBON_PRICE_INR_PER_TONNE = old


def test_material_carbon_uses_the_same_coefficients_as_the_carbon_module(ctx):
    """Two carbon numbers from two tables is how they drift apart."""
    _, a, e = ctx
    r = O.material_recommendations(a, e)
    priced = O._priced(a)
    cement = next(f for f in r["options"] if f["family"] == "Cement")
    opc = next(o for o in cement["options"] if o["option"] == "OPC 53")
    expected = priced["cement"]["quantity"] * C.EMBODIED_CARBON["cement"]["factor"] / 1000
    assert opc["carbon_t"] == pytest.approx(expected, rel=0.01)


def test_analyse_returns_every_optimiser(opt):
    for k in ALL:
        assert k in opt and opt[k]["id"]
    assert opt["currency"] == "INR"
