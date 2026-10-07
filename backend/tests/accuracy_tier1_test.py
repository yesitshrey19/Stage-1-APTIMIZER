"""Tier 1 accuracy regressions — the five bugs that produced visibly wrong output.

Each test states the defect it locks down, so a future change that reintroduces one
fails with an explanation rather than a bare assertion.

    pytest backend/tests/accuracy_tier1_test.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine  # noqa: E402
import engineering as englib  # noqa: E402
import iscodes as C  # noqa: E402


def project(**over):
    """A small but complete residential project the engine can analyse end to end."""
    p = {
        "plot": {"length": 60.0, "width": 40.0, "coordinates": [], "road_edges": []},
        "towers": [{
            "id": "t1", "name": "Tower A", "floors": 8, "floor_height": 3.0,
            "footprint_area": 420.0, "common_area": 40.0,
            "corridor_width": 1.5, "stair_min_width": 1.5, "stair_count": 2,
            "lift_count": 2, "exits_per_floor": 2, "max_travel_distance_m": 18.0,
            "units": [{"type": "2bhk", "count": 4, "carpet_area": 85.0}],
            "rooms": [],
        }],
        "parking": {"provided_slots": 40, "accessible_provided": 1, "ev_provided": 8,
                    "ramp": {"slope_pct": 10.0, "width": 3.6}},
        "engineering": {"city": "Bengaluru", "state": "Karnataka"},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(p.get(k), dict):
            p[k] = {**p[k], **v}
        else:
            p[k] = v
    return p


def analyse(p):
    base = engine.analyse(p)
    return base, englib.analyse_engineering(p, base)


def outputs(module):
    return {o["label"]: o for o in module["outputs"]}


def checks(module):
    return {c["label"]: c for c in module["checks"]}


# ---------------------------------------------------------------- 1. bearing pressure
def test_applied_bearing_pressure_is_not_just_the_sbc():
    """It used to be service_load / (service_load / sbc), which is identically the SBC —
    so the 'vs SBC' comparison could never inform anything."""
    _, eng = analyse(project())
    o = outputs(eng["modules"]["foundation"])
    sbc = o["Safe bearing capacity (SBC)"]["value"]
    applied = o["Applied bearing pressure"]["value"]

    assert applied != sbc, "applied pressure is still pinned to the SBC"
    assert applied < sbc, "a footing rounded up from the required area must be under-stressed"
    assert o["Provided footing area"]["value"] >= o["Required footing area"]["value"]


def test_bearing_utilisation_is_a_real_percentage():
    _, eng = analyse(project())
    o = outputs(eng["modules"]["foundation"])
    u = o["Bearing capacity utilisation"]["value"]
    assert 0 < u < 100, f"utilisation {u}% should be a genuine fraction of the SBC"


def test_weaker_soil_raises_utilisation():
    _, soft = analyse(project(engineering={"soil_type": "soft clay"}))
    _, firm = analyse(project(engineering={"soil_type": "hard rock"}))
    su = outputs(soft["modules"]["foundation"])["Bearing capacity utilisation"]["value"]
    fu = outputs(firm["modules"]["foundation"])["Bearing capacity utilisation"]["value"]
    assert su > fu


# ---------------------------------------------------------------- 2. RWH check
def test_small_plot_does_not_fail_the_rwh_check():
    """Below the threshold the rule does not bite — that is compliance, not a red fail."""
    small = project(plot={"length": 12.0, "width": 12.0})   # 144 m2, under 200
    _, eng = analyse(small)
    c = checks(eng["modules"]["storm"])["Rainwater harvesting provided"]
    assert c["status"] == "pass"
    assert "not mandatory" in c["note"].lower()


def test_large_plot_without_rwh_fails():
    big = project(plot={"length": 60.0, "width": 40.0},
                  engineering={"rwh_provided": False})       # 2400 m2, over 200
    _, eng = analyse(big)
    c = checks(eng["modules"]["storm"])["Rainwater harvesting provided"]
    assert c["status"] == "fail"


def test_large_plot_with_rwh_passes():
    _, eng = analyse(project(engineering={"rwh_provided": True}))
    assert checks(eng["modules"]["storm"])["Rainwater harvesting provided"]["status"] == "pass"


# ---------------------------------------------------------------- 3. extinguishers
def test_extinguisher_check_can_fail():
    """ceil(plate/200) >= 1 was true for any positive plate — a free pass."""
    _, eng = analyse(project(engineering={"extinguishers_per_floor": 0}))
    assert checks(eng["modules"]["fire"])["Fire extinguishers 1 per 200 m² per floor"]["status"] == "fail"


def test_extinguisher_check_passes_when_enough_are_provided():
    _, eng = analyse(project(engineering={"extinguishers_per_floor": 20}))
    assert checks(eng["modules"]["fire"])["Fire extinguishers 1 per 200 m² per floor"]["status"] == "pass"


def test_extinguisher_requirement_scales_with_floor_plate():
    # The fire floor plate is `builtup_per_floor_sqm`, derived from the unit mix — it is
    # independent of the hand-typed `footprint_area`, so vary the units to move it.
    def with_units(count):
        base_tower = project()["towers"][0]
        return project(towers=[{**base_tower,
                                "units": [{"type": "2bhk", "count": count, "carpet_area": 85.0}]}])

    _, small = analyse(with_units(2))
    _, large = analyse(with_units(16))
    s = outputs(small["modules"]["fire"])["Extinguishers per floor"]["value"]
    l = outputs(large["modules"]["fire"])["Extinguishers per floor"]["value"]
    assert l > s, f"{l} extinguishers for a big plate vs {s} for a small one"


# ---------------------------------------------------------------- 4. shared constants
def test_travel_distance_is_one_number_across_modules():
    """A project must not pass Compliance and fail Fire Safety on the same parameter."""
    rule = next(r for r in engine.DEFAULT_RULES if r["id"] == "travel_distance")
    assert rule["threshold"] == C.FIRE["max_travel_m"]
    assert C.FIRE["max_travel_m"] == 30.0, "NBC 2016 Part 4 Table 4, Group A-2 residential"


def test_corridor_width_rule_reads_the_shared_constant():
    rule = next(r for r in engine.DEFAULT_RULES if r["id"] == "corridor_width")
    assert rule["threshold"] == C.FIRE["corridor_min_m"]


def test_travel_distance_threshold_is_identical_in_both_modules():
    """Both surfaces must quote the same limit — the bug was Fire Safety enforcing
    22.5 m while Compliance enforced 30 m on the same project."""
    base, eng = analyse(project())

    rule = next(r for r in base["compliance"]["results"] if r["code"] == "FIR-02")
    # Look the fire check up by prefix: its label is built from the constant, so pinning
    # the full string would re-introduce the hardcoded number this test guards.
    fire = next(c for c in eng["modules"]["fire"]["checks"]
                if c["label"].startswith("Travel distance to nearest exit"))

    limit = C.FIRE["max_travel_m"]
    assert rule["threshold"] == limit
    shown = str(limit).rstrip("0").rstrip(".")
    assert shown in fire["label"] and shown in str(fire["required"]), \
        f"fire module shows {fire['required']!r} against a {limit} m constant"


def test_both_modules_judge_the_same_travel_distance_the_same_way():
    """Same input, same verdict — whatever the derived travel distance happens to be."""
    base, eng = analyse(project())
    actual = base["compliance"]["params"]["max_travel_distance_m"]
    rule = next(r for r in base["compliance"]["results"] if r["code"] == "FIR-02")
    within_limit = actual <= C.FIRE["max_travel_m"]
    assert (rule["status"] == "pass") == within_limit


def test_accessible_bays_agree_between_parking_and_accessibility():
    _, eng = analyse(project())
    parking = outputs(eng["modules"]["parking_nbc"])["Accessible bays required"]["value"]
    access = checks(eng["modules"]["accessibility"])["Accessible parking bays"]["required"]
    assert parking == access, "the two modules still demand different accessible bay counts"


# ---------------------------------------------------------------- 5. water demand
def test_water_demand_matches_between_utilities_and_water_module():
    """The Utilities panel and the IS 1172 Water module must not size different sumps."""
    base, eng = analyse(project())
    util = base["utilities"]["water_demand_lpd"]
    water = eng["modules"]["water"]["derived"]["total_lpd"]
    assert round(util) == round(water), f"Utilities {util} lpd vs Water module {water} lpd"


def test_population_matches_between_modules():
    base, eng = analyse(project())
    assert base["utilities"]["persons"] == outputs(eng["modules"]["water"])["Population"]["value"]


def test_demand_uses_the_is1172_breakdown():
    base, _ = analyse(project())
    u = base["utilities"]
    total = C.WATER_LPCD["domestic"] + C.WATER_LPCD["flushing"] + C.WATER_LPCD["external"]
    assert u["lpcd"] == total == 195
    assert round(u["domestic_lpd"] + u["flushing_lpd"] + u["external_lpd"]) == round(u["water_demand_lpd"])


def test_user_lpcd_override_scales_the_split_without_breaking_the_total():
    base, _ = analyse(project(utility_config={"lpcd": 150}))
    u = base["utilities"]
    assert u["lpcd"] == 150
    assert round(u["domestic_lpd"] + u["flushing_lpd"] + u["external_lpd"]) == round(u["water_demand_lpd"])
    assert u["domestic_lpd"] > u["flushing_lpd"] > u["external_lpd"]


def test_sewage_matches_between_modules():
    base, eng = analyse(project())
    util_stp = base["utilities"]["stp_capacity_kld"]
    water_stp = eng["modules"]["water"]["derived"]["stp_kld"]
    assert round(util_stp, 1) == round(water_stp, 1)


def test_rwh_rainfall_defaults_to_the_project_city_not_a_flat_900():
    base, _ = analyse(project(engineering={"city": "Mangaluru", "state": "Karnataka"}))
    assert base["utilities"]["annual_rainfall_mm"] == C.CITIES["Mangaluru"][3] == 3500


def test_explicit_rainfall_override_still_wins():
    base, _ = analyse(project(utility_config={"annual_rainfall_mm": 1234}))
    assert base["utilities"]["annual_rainfall_mm"] == 1234


# ---------------------------------------------------------------- 5. code version
def test_the_clause_card_cannot_drift_from_the_constant_it_describes():
    """The card said "Travel 22.5 m" long after FIRE was corrected to 30 m.

    The IS/NBC Engineering module renders CODE_LIBRARY beside the compliance check that
    reads FIRE, so the screen showed two numbers for one rule. Fixing the literal fixed
    that instance; formatting the card from the constant is what stops the next one.
    """
    card = next(e for e in C.CODE_LIBRARY if e["id"] == "nbc4")
    assert f"Travel {C.FIRE['max_travel_m']:g} m" in card["key_value"]
    assert f"> {C.FIRE['fire_lift_above_m']:g} m" in card["key_value"]
    assert "22.5" not in card["key_value"]


def test_projects_are_checked_against_nbc_2016_unless_told_otherwise():
    """SP 7:2026 withdrew NBC 2016 nationally, but state bye-laws still reference it and
    that is what an approval is measured against. The default has to stay there."""
    assert C.DEFAULT_CODE_VERSION == C.NBC_2016
    assert englib.DEFAULT_ENGINEERING["code_version"] == C.NBC_2016
    _, eng = analyse(project())
    assert eng["code_version"]["id"] == C.NBC_2016
    assert eng["modules"]["fire"]["unchecked"] is False
    assert eng["modules"]["fire"]["checks"], "NBC 2016 has the values, so it must check"


def test_an_unread_threshold_refuses_every_operation_a_check_would_perform():
    """The sentinel is the guarantee. A blank that compared as False would pass some
    checks and fail others, and both would be rendered in the same shape as a finding."""
    unread = C.fire_table(C.SP7_2026)["max_travel_m"]
    assert unread is C.UNREAD
    for label, op in (("bool", lambda: bool(unread)),
                      ("compare", lambda: 30.0 > unread),
                      ("float", lambda: float(unread)),
                      ("arithmetic", lambda: unread + 1)):
        with pytest.raises(C.CodeValueUnread):
            op()
    assert str(unread) == "UNREAD", "must stay printable so a log line cannot explode"


def test_sp7_refuses_to_score_fire_safety_rather_than_scoring_a_half_empty_table():
    """Not a fallback to the 2016 numbers, and not a partial score either. "3 of 8 passed"
    computed from a table that is half empty reads as a finding about the building when it
    is a finding about the table."""
    _, eng = analyse(project(engineering={"code_version": C.SP7_2026}))
    fire = eng["modules"]["fire"]
    assert eng["code_version"]["id"] == C.SP7_2026
    assert fire["unchecked"] is True
    assert fire["checks"] == [] and fire["score"] is None
    assert len(fire["unread_values"]) == len(C.fire_table(C.SP7_2026))
    assert any("not been read" in w["message"] for w in fire["warnings"])

    # The water module still answers the IS 1172 half, and declines to invent the NBC half.
    water = eng["modules"]["water"]
    reserve = outputs(water)["Fire reserve in sump"]
    assert reserve["value"] == 0 and "not read" in reserve["note"]
    assert outputs(water)["Total daily demand"]["value"] > 0


def test_an_unknown_code_version_falls_back_to_the_default_and_says_which():
    _, eng = analyse(project(engineering={"code_version": "sp7_2027_typo"}))
    assert eng["code_version"]["id"] == C.NBC_2016
    assert eng["modules"]["fire"]["checks"], "the fallback must be a version that can check"
