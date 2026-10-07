"""Block 4 planning optimiser tests.

Two things these guard that are easy to get wrong:
  * the objective. Cost per flat rises with every floor, so minimising it answers "build
    one storey" and throws the site away. Several of these check the optimiser is not
    quietly degenerate in that way.
  * candidates are scored by the REAL engine, so an optimiser can never recommend
    something the compliance tab immediately rejects.
"""
import sys, os, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, planopt as P
from defaults import default_project


@pytest.fixture(scope="module")
def ctx():
    p = default_project("T", "C", "Hyderabad", "S", "o")
    return p, engine.analyse(p)


@pytest.fixture(scope="module")
def opt(ctx):
    p, a = ctx
    return P.analyse(p, a)


ALL = ("floors", "far", "fsi", "open_space", "mix", "parking", "utilities")


@pytest.mark.parametrize("key", ALL)
def test_every_optimiser_reports_current_best_and_the_change(opt, key):
    o = opt[key]
    assert set(o["current"]) >= {"value", "unit", "label"}
    assert set(o["best"]) >= {"value", "unit", "label"}
    assert o["changes"]
    for c in o["changes"]:
        assert set(c) >= {"lever", "from", "to", "effect"}


@pytest.mark.parametrize("key", ALL)
def test_no_optimiser_recommends_a_non_compliant_option(opt, key):
    """Every candidate is scored by the real engine, so a recommendation the compliance
    tab would reject is a contradiction the app must never produce."""
    o = opt[key]
    # Illegal rows may be SHOWN -- seeing the boundary is useful -- but each must say why,
    # and the chosen best must never be one of them.
    for r in o["options"]:
        if isinstance(r, dict) and r.get("compliant") is False:
            assert r.get("fails"), "an option marked non-compliant must say why"
    chosen = [r for r in o["options"]
              if isinstance(r, dict) and r.get("compliant") is False
              and o["best"]["value"] in (r.get("profit"), r.get("open_space_pct"), r.get("revenue"))]
    assert not chosen, f"{key} selected a non-compliant option"


def test_optimisers_do_not_mutate_the_stored_project(ctx):
    p, a = ctx
    before = copy.deepcopy(p)
    P.analyse(p, a)
    assert p == before


# ---------------------------------------------------------------- floors
def test_floor_optimiser_maximises_profit_not_cost_per_flat(ctx):
    """The regression this exists for: cost per flat falls monotonically as the building
    shrinks, so minimising it recommended 3 floors and destroyed 36 flats."""
    p, a = ctx
    r = P.floor_optimisation(p, a)
    rows = {x["floors"]: x for x in r["options"]}
    assert rows[3]["cost_per_unit"] < rows[12]["cost_per_unit"]   # the degenerate direction
    best_floors = max(x["floors"] for x in r["options"] if x["profit"] == r["best"]["value"])
    assert best_floors >= 12, "the optimiser shrank the building"
    assert rows[best_floors]["units"] >= rows[12]["units"]
    assert any("build one storey" in n for n in r["notes"])


def test_floor_optimiser_stops_at_the_first_binding_rule(ctx):
    p, a = ctx
    r = P.floor_optimisation(p, a)
    best_floors = max(x["floors"] for x in r["options"] if x["profit"] == r["best"]["value"])
    rows = {x["floors"]: x for x in r["options"]}
    assert rows[best_floors]["compliant"]
    nxt = rows.get(best_floors + 1)
    if nxt:
        assert not nxt["compliant"], "it stopped short of the real ceiling"
        assert nxt["fails"]


def test_floor_sweep_names_what_blocks_the_next_floor(ctx):
    p, a = ctx
    r = P.floor_optimisation(p, a)
    assert any("fails:" in n for n in r["notes"])


# ---------------------------------------------------------------- FAR / FSI
def test_far_reports_headroom_and_what_would_consume_it(opt):
    o = opt["far"]
    assert o["delta"]["direction"] == "higher is better"
    opt_row = o["options"][0]
    assert opt_row["cap"] > opt_row["current"]
    assert opt_row["unused_buildable_sqm"] > 0
    assert o["changes"][0]["lever"] == "Floors per tower"


def test_far_never_claims_headroom_another_rule_blocks(ctx):
    """Unused FAR is only worth something if the site can actually take it."""
    p, a = ctx
    r = P.far_optimisation(p, a, "far")
    reachable = r["best"]["value"]
    assert reachable <= r["options"][0]["cap"]
    # Whatever it claims is reachable must actually pass every check.
    floors = int(r["changes"][0]["to"]) if r["changes"][0]["to"].isdigit() else None
    if floors:
        p2 = copy.deepcopy(p)
        for t in p2["towers"]:
            t["floors"] = floors
        assert not P._failed(engine.analyse(p2))


def test_fsi_tracks_far(opt):
    assert opt["fsi"]["current"]["value"] == pytest.approx(opt["far"]["current"]["value"], rel=0.01)


def test_far_without_a_cap_says_so_rather_than_optimising_nothing(ctx):
    p, a = ctx
    a2 = copy.deepcopy(a)
    a2["compliance"]["results"] = [r for r in a2["compliance"]["results"] if r["id"] != "far_max"]
    r = P.far_optimisation(p, a2, "far")
    assert r["feasible"] is False
    assert any("no far cap" in n.lower() for n in r["notes"])


# ---------------------------------------------------------------- open space
def test_open_space_never_buys_greenery_by_losing_flats(ctx):
    """A slimmer tower that houses fewer people is not an optimisation, it is a cut."""
    p, a = ctx
    r = P.open_space_optimisation(p, a)
    base_units = a["areas"]["total_units"]
    best_pct = r["best"]["value"]
    winner = [x for x in r["options"] if x["open_space_pct"] == best_pct]
    assert winner
    assert all(x["units"] >= base_units for x in winner)


def test_open_space_is_treated_as_an_objective(opt):
    o = opt["open_space"]
    assert o["delta"]["direction"] == "higher is better"
    assert o["best"]["value"] >= o["current"]["value"]


def test_open_space_warns_that_slimmer_costs_more(opt):
    assert any("costs more" in n for n in opt["open_space"]["notes"])


# ---------------------------------------------------------------- mix
def test_mix_maximises_revenue_within_compliance(ctx):
    p, a = ctx
    r = P.mix_optimisation(p, a)
    assert r["delta"]["direction"] == "higher is better"
    assert r["best"]["value"] >= r["current"]["value"]
    best_rows = [x for x in r["options"] if x["revenue"] == r["best"]["value"]]
    assert all(x["compliant"] for x in best_rows)


def test_mix_holds_the_flat_count_constant(ctx):
    """This is a mix shift, not a density increase -- otherwise it is just 'build more'."""
    p, a = ctx
    r = P.mix_optimisation(p, a)
    counts = {x["units"] for x in r["options"]}
    assert len(counts) == 1, f"flat count moved across the mix sweep: {counts}"


def test_mix_flags_that_market_absorption_is_not_modelled(opt):
    assert any("absorbs" in n for n in opt["mix"]["notes"])


def test_mix_needs_two_unit_types(ctx):
    p, a = ctx
    p2 = copy.deepcopy(p)
    for t in p2["towers"]:
        t["units"] = t["units"][:1]
    r = P.mix_optimisation(p2, engine.analyse(p2))
    assert r["feasible"] is False


# ---------------------------------------------------------------- parking
def test_parking_never_reduces_the_required_slot_count(opt):
    """The NBC requirement is fixed by the unit count. Only the structure housing it moves."""
    o = opt["parking"]
    picked = [r for r in o["options"] if r["total_sqm"] == o["best"]["value"]]
    assert picked and all(r["meets_requirement"] for r in picked)
    assert any("never traded away" in n for n in o["notes"])


def test_parking_prefers_above_ground_because_basement_costs_more(opt):
    o = opt["parking"]
    assert P.BASEMENT_COST_INDEX > P.PODIUM_COST_INDEX
    assert o["best"]["value"] <= o["current"]["value"]


def test_stackers_are_offered_with_their_cost_not_chosen_silently(opt):
    o = opt["parking"]
    stacked = [r for r in o["options"] if r["stack"]]
    assert stacked and all("attendant" in r["note"] for r in stacked)


# ---------------------------------------------------------------- utilities
def test_utility_storage_never_falls_below_the_fire_reserve(opt):
    """Fire storage inside the sump is fixed by code and does not shrink with demand."""
    o = opt["utilities"]
    rows = o["options"]
    base = rows[0]
    for r in rows:
        assert r["ug_tank_cum"] >= base["ug_tank_cum"] * 0.5 - 1e-6
        assert r["oh_tank_cum"] >= base["oh_tank_cum"] * 0.5 - 1e-6
    assert any("fire reserve" in n for n in o["notes"])


def test_utility_reuse_reduces_fresh_demand_monotonically(opt):
    rows = opt["utilities"]["options"]
    demands = [r["net_demand_lpd"] for r in rows]
    assert demands == sorted(demands, reverse=True)


def test_utility_costs_are_labelled_indicative(opt):
    assert any("indicative" in n.lower() for n in opt["utilities"]["notes"])


def test_analyse_returns_every_planning_optimiser(opt):
    for k in ALL:
        assert k in opt and opt[k]["id"]
    assert opt["currency"] == "INR"
