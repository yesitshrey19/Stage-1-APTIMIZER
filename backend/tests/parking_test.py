"""Per-building parking. The multi-tower cases are the reason this file exists."""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, parking as P
from defaults import default_project


def mk_tower(tid, name, floors, units):
    return {"id": tid, "name": name, "floors": floors, "floor_height": 3.0,
            "footprint_area": 620.0, "common_area": 240.0, "corridor_width": 1.8,
            "corridor_length": 32.0, "exits_per_floor": 2, "max_travel_distance": 24.0,
            "units": units, "staircases": [{"id": "s", "count": 2, "width": 1.5}],
            "lifts": [{"id": "l", "count": 2, "capacity": 8}], "common_spaces": [], "rooms": []}


def u(t, count, carpet):
    return {"id": t, "type": t, "count": count, "carpet_area": carpet, "balcony_area": 0}


# ---------------------------------------------------------------- norm table
def test_udcpr_bands_match_the_published_table():
    n = P.norm_for("Maharashtra")
    assert n["verified"] is True
    cases = [(25, 0.0), (30, 0.5), (35, 0.5), (40, 0.5), (79, 0.5),
             (80, 1.0), (149, 1.0), (150, 2.0), (300, 2.0)]
    for carpet, expected in cases:
        assert P._band(n, carpet)[0] == expected, f"{carpet} m2 -> {expected} cars"


def test_a_small_tenement_owes_no_car_space():
    """UDCPR exempts <=30 m2 outright. A flat per-unit ratio cannot express that."""
    t = mk_tower("t", "T", 10, [u("studio", 4, 28.0)])
    d = P.tower_demand(t, P.norm_for("Maharashtra"), 1000)
    assert d["cars_required"] == 0 and d["units"] == 40
    assert d["scooters_required"] == 40      # two-wheelers are still required


def test_demand_tracks_unit_size_not_unit_count():
    norm = P.norm_for("Maharashtra")
    small = P.tower_demand(mk_tower("a", "A", 10, [u("1bhk", 4, 45.0)]), norm, 1000)
    large = P.tower_demand(mk_tower("b", "B", 10, [u("4bhk", 4, 160.0)]), norm, 1000)
    assert small["units"] == large["units"] == 40
    assert small["cars_required"] == 20 and large["cars_required"] == 80


def test_unknown_state_falls_back_and_flags_itself_unverified():
    n = P.norm_for("Nowhere")
    assert n["verified"] is False and n["basis"] == "builtup_per_100"


def test_project_override_beats_the_state_table():
    n = P.norm_for("Maharashtra", {"basis": "builtup_per_100", "ecs_per_100_sqm": 3.0})
    assert n["basis"] == "builtup_per_100" and n["ecs_per_100_sqm"] == 3.0
    assert "edited" in n["authority"]


# ---------------------------------------------------------------- allocation
def test_shared_pool_is_distributed_exactly():
    for pool, needs in [(10, [5, 5]), (7, [1, 2, 3]), (100, [33, 33, 33]), (5, [10, 1])]:
        got = P._allocate(pool, needs)
        assert sum(got) == min(pool, math.ceil(sum(needs))), (pool, needs, got)
        assert all(g >= 0 for g in got)


def test_allocation_favours_the_tower_that_needs_more():
    got = P._allocate(10, [30, 10])
    assert got[0] > got[1] and sum(got) == 10


def test_pool_larger_than_demand_gives_everyone_what_they_need():
    assert P._allocate(500, [10, 20]) == [10, 20]


# ---------------------------------------------------------------- per building
def _multi(state="Maharashtra", **parking):
    p = default_project("T", "C", "Mumbai", "S", "o")
    p["engineering"] = {"state": state, "city": "Mumbai"}
    p["towers"] = [
        mk_tower("t1", "Tower A", 10, [u("3bhk", 4, 120.0)]),   # 40 large units -> 40 cars
        mk_tower("t2", "Tower B", 10, [u("1bhk", 4, 45.0)]),    # 40 small units -> 20 cars
    ]
    p["parking"] = {"area_per_slot": 30, "basement_levels": 0, "basement_area_per_level": 0,
                    "ground_area": 0, **parking}
    return p


def test_each_building_gets_its_own_demand():
    a = engine.analyse(_multi())
    rows = {t["name"]: t for t in a["parking"]["towers"]}
    assert rows["Tower A"]["cars_required"] == 40
    assert rows["Tower B"]["cars_required"] == 20
    assert a["parking"]["required_slots"] == 60


def test_a_short_building_is_reported_even_when_the_site_is_in_surplus():
    """The case a site-wide total hides, and the reason sanction is granted per building."""
    p = _multi()
    p["towers"][0]["parking"] = {"stilt_slots": 0}
    p["towers"][1]["parking"] = {"stilt_slots": 200}   # Tower B hugely over-supplied
    pk = engine.analyse(p)["parking"]
    assert pk["provided_slots"] > pk["required_slots"], "site total is in surplus"
    assert pk["surplus"] > 0
    rows = {t["name"]: t for t in pk["towers"]}
    assert rows["Tower A"]["deficit"] == 40, "Tower A owns nothing and the pool is empty"
    assert pk["buildings_short"] == ["Tower A"]
    assert not pk["all_checks_pass"]


def test_own_slots_are_counted_before_the_shared_pool():
    p = _multi(basement_levels=1, basement_area_per_level=600)   # 20 shared slots
    p["towers"][0]["parking"] = {"stilt_slots": 40}              # A is self-sufficient
    rows = {t["name"]: t for t in engine.analyse(p)["parking"]["towers"]}
    assert rows["Tower A"]["deficit"] == 0
    assert rows["Tower A"]["shared_slots"] == 0, "a satisfied tower must not draw on the pool"
    assert rows["Tower B"]["shared_slots"] == 20


def test_per_tower_numbers_add_back_to_the_site_total():
    p = _multi(basement_levels=2, basement_area_per_level=900)
    pk = engine.analyse(p)["parking"]
    assert sum(t["cars_required"] for t in pk["towers"]) == pk["required_slots"]
    assert sum(t["provided_slots"] for t in pk["towers"]) == pk["provided_slots"]


# ---------------------------------------------------------------- metrics
def test_efficiency_is_no_longer_tautological():
    """It used to divide the area by a slot count derived from that same area, so it read
    ~100% whatever you entered. It must now respond to the area allowed per slot."""
    loose = engine.analyse(_multi(area_per_slot=46))["parking"]["efficiency_pct"]
    tight = engine.analyse(_multi(area_per_slot=23))["parking"]["efficiency_pct"]
    assert tight == 100.0 and loose == 50.0


def test_visitor_default_comes_from_the_state_norm():
    pk = engine.analyse(_multi())["parking"]
    assert pk["norm"]["visitor_pct"] == 5.0                 # UDCPR, not the old hardcoded 10
    assert pk["visitor_required"] == math.ceil(60 * 0.05)


def test_reserved_categories_cannot_exceed_slots_built():
    p = _multi(visitor_provided=500, ev_provided=500, accessible_provided=500)
    pk = engine.analyse(p)["parking"]
    fit = next(c for c in pk["checks"] if "fit inside" in c["label"])
    assert fit["pass"] is False


def test_unverified_norm_raises_a_warning():
    assert any("unverified" in w["text"] for w in engine.analyse(_multi("Nowhere"))["parking"]["warnings"])
    assert not engine.analyse(_multi("Maharashtra"))["parking"]["warnings"]


def test_two_wheeler_demand_is_reported():
    pk = engine.analyse(_multi())["parking"]
    assert pk["scooters_required"] == 80
    assert pk["scooter_ecs_equivalent"] == round(80 / 3, 1)


# ---------------------------------------------------------------- compatibility
def test_every_legacy_key_survives():
    pk = engine.analyse(_multi())["parking"]
    for k in ["units", "ratio_per_unit", "required_slots", "provided_slots", "basement_slots",
              "ground_slots", "deficit", "surplus", "visitor_required", "ev_required",
              "accessible_required", "visitor_provided", "ev_provided", "accessible_provided",
              "total_parking_area_sqm", "area_per_slot_actual", "efficiency_pct",
              "ramp_checks", "ramp_pass"]:
        assert k in pk, f"consumers still read parking['{k}']"


def test_downstream_modules_still_work():
    import engineering as englib
    p = _multi()
    base = engine.analyse(p)
    assert base["compliance"]["total"] > 0
    eng = englib.analyse_engineering(p, base)
    assert eng["modules"]["parking_nbc"]["outputs"]
