"""Programme engine tests. The safety cases are the reason this file exists."""
import sys, os, time
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, schedule as S
from defaults import default_project


@pytest.fixture(scope="module")
def plan():
    p = default_project("Test", "C", "Hyderabad", "S", "o")
    return S.plan_schedule(p, engine.analyse(p), {"start_date": "2026-01-05"})


@pytest.fixture(scope="module")
def acts(plan):
    return {a["id"]: a for a in plan["activities"]}


# ---------------------------------------------------------------- calendar
def test_calendar_skips_sundays_and_holidays():
    c = S.WorkCalendar(holidays=["2026-01-07"])
    assert not c.is_working(date(2026, 1, 4))     # Sunday
    assert not c.is_working(date(2026, 1, 7))     # holiday
    assert c.is_working(date(2026, 1, 6))


def test_monsoon_blocks_only_exposed_work():
    c = S.WorkCalendar(monsoon_months=(7,))
    d = date(2026, 7, 15)
    assert c.is_working(d, exposed=False)
    assert not c.is_working(d, exposed=True)


def test_add_and_sub_work_days_are_inverse():
    c = S.WorkCalendar()
    start = c.next_working(date(2026, 1, 5))
    for n in (1, 3, 7, 20):
        fin = c.add_work_days(start, n)
        assert c.work_days_between(start, fin) == n
        assert c.sub_work_days(fin, n) == start


def test_duration_scales_inversely_with_crew():
    assert S.duration_days(100, 10, 1) == 10
    assert S.duration_days(100, 10, 2) == 5
    assert S.duration_days(0, 10, 1) == 1        # never zero-length


# ---------------------------------------------------------------- SAFETY
def test_prop_removal_follows_is456_span_rule():
    assert S.prop_removal_days(4.0)[0] == 7.0
    assert S.prop_removal_days(6.0)[0] == 14.0   # over 4.5 m -> 14 days


def test_blended_cement_extends_short_cure_but_not_long_props():
    assert S.prop_removal_days(4.0, blended_cement=True)[0] == 10.0   # cure governs
    assert S.prop_removal_days(6.0, blended_cement=True)[0] == 14.0   # props still govern


def test_every_floor_waits_the_full_prop_period(plan, acts):
    """The load-bearing safety test: no floor's formwork may start before the slab it
    stands on has served its full IS 456 prop period."""
    req = plan["safety"]["prop_removal_days"]
    checked = 0
    for a in plan["activities"]:
        if "_fw_" not in a["id"]:
            continue
        for d in a["predecessors"]:
            if "slabcast" in d["id"]:
                cast_fin = date.fromisoformat(acts[d["id"]]["finish"])
                fw_start = date.fromisoformat(a["start"])
                assert (fw_start - cast_fin).days >= req, (
                    f"{a['id']} starts {(fw_start - cast_fin).days} days after {d['id']}, "
                    f"needs {req}")
                checked += 1
    assert checked >= 5, "expected many floor-cycle links to verify"


def test_prop_lags_are_marked_hard_and_carry_a_code_reason(plan):
    hard = [d for a in plan["activities"] for d in a["predecessors"]
            if d["hard"] and "props" in (d["reason"] or "").lower()]
    assert hard, "prop-removal links must be marked hard"
    assert all("IS 456" in d["reason"] for d in hard)


def test_audit_clamps_an_unsafe_lag_and_reports_it():
    cfg = S.ScheduleConfig()
    a1 = S.Activity(id="t0_slabcast_1", name="cast", phase="S")
    a2 = S.Activity(id="t0_fw_2", name="formwork", phase="S",
                    deps=[S.Dep("t0_slabcast_1", lag_days=2.0, hard=True,
                                reason="IS 456 Cl. 11.3 props")])
    found = S.audit_safety([a1, a2], cfg, span_m=6.0)
    assert len(found) == 1 and found[0]["severity"] == "critical"
    assert a2.deps[0].lag_days == 14.0, "unsafe lag must be raised to the code minimum"


def test_wider_span_produces_a_longer_programme():
    p = default_project("T", "C", "Hyderabad", "S", "o")
    a = engine.analyse(p)
    narrow = S.plan_schedule(p, a, {"start_date": "2026-01-05", "slab_span_m": 4.0})
    wide = S.plan_schedule(p, a, {"start_date": "2026-01-05", "slab_span_m": 6.0})
    assert wide["duration_calendar_days"] > narrow["duration_calendar_days"]
    assert wide["safety"]["prop_removal_days"] == 14.0


def test_curing_lag_is_calendar_days_not_working_days(plan, acts):
    """Concrete cures on Sundays. Counting cure in working days would shorten a 7-day
    cure to 6 real days for every Sunday it spans."""
    plinth = acts["t0_plinth"]
    fdn_fin = date.fromisoformat(acts["t0_fdn"]["finish"])
    assert (date.fromisoformat(plinth["start"]) - fdn_fin).days >= S.CURING_MIN_DAYS


# ---------------------------------------------------------------- CPM correctness
def test_cpm_matches_a_hand_computed_network():
    """A->B(FS), A->C(FS), B->D, C->D. Durations 2,3,5,2 on a 7-day week.
    Hand result: A 1-2, B 3-5, C 3-7, D 8-9. C is critical, B has 2 days float."""
    cal = S.WorkCalendar(work_week=(0, 1, 2, 3, 4, 5, 6))
    mk = lambda i, n, deps=(): S.Activity(id=i, name=i, phase="p", work_days=n,
                                          deps=[S.Dep(d) for d in deps])
    acts = [mk("A", 2), mk("B", 3, ["A"]), mk("C", 5, ["A"]), mk("D", 2, ["B", "C"])]
    S.run_cpm(acts, date(2026, 1, 1), cal)
    by = {a.id: a for a in acts}
    assert by["A"].es == date(2026, 1, 1) and by["A"].ef == date(2026, 1, 3)
    assert by["C"].es == date(2026, 1, 3) and by["C"].ef == date(2026, 1, 8)
    assert by["D"].es == date(2026, 1, 8)
    assert by["A"].critical and by["C"].critical and by["D"].critical
    assert not by["B"].critical and by["B"].total_float == 2


def test_lag_pushes_the_successor():
    cal = S.WorkCalendar(work_week=tuple(range(7)))
    a = S.Activity(id="A", name="A", phase="p", work_days=1)
    b = S.Activity(id="B", name="B", phase="p", work_days=1,
                   deps=[S.Dep("A", lag_days=5)])
    S.run_cpm([a, b], date(2026, 1, 1), cal)
    assert (b.es - a.ef).days == 5


def test_start_to_start_runs_activities_in_parallel():
    cal = S.WorkCalendar(work_week=tuple(range(7)))
    a = S.Activity(id="A", name="A", phase="p", work_days=10)
    b = S.Activity(id="B", name="B", phase="p", work_days=2,
                   deps=[S.Dep("A", "SS", lag_days=1)])
    S.run_cpm([a, b], date(2026, 1, 1), cal)
    assert b.es == a.es + timedelta(days=1)
    assert b.ef < a.ef


def test_circular_dependency_is_an_error_not_a_hang():
    a = S.Activity(id="A", name="A", phase="p", deps=[S.Dep("B")])
    b = S.Activity(id="B", name="B", phase="p", deps=[S.Dep("A")])
    with pytest.raises(S.ScheduleError) as e:
        S.run_cpm([a, b], date(2026, 1, 1), S.WorkCalendar())
    assert e.value.code == "circular_dependency"


def test_unknown_predecessor_is_rejected():
    a = S.Activity(id="A", name="A", phase="p", deps=[S.Dep("ghost")])
    with pytest.raises(S.ScheduleError) as e:
        S.run_cpm([a], date(2026, 1, 1), S.WorkCalendar())
    assert e.value.code == "unknown_predecessor"


def test_critical_path_is_continuous_from_start_to_finish(plan, acts):
    crit = [acts[i] for i in plan["critical_path"]]
    assert crit, "a programme must have a critical path"
    assert min(date.fromisoformat(a["start"]) for a in crit) == date.fromisoformat(plan["start"])
    assert max(date.fromisoformat(a["finish"]) for a in crit) == date.fromisoformat(plan["finish"])


def test_no_activity_starts_before_its_predecessor_finishes(plan, acts):
    for a in plan["activities"]:
        for d in a["predecessors"]:
            if d["type"] != "FS":
                continue
            pf = date.fromisoformat(acts[d["id"]]["finish"])
            assert date.fromisoformat(a["start"]) >= pf + timedelta(days=d["lag_days"])


def test_free_float_never_exceeds_total_float(plan):
    for a in plan["activities"]:
        assert a["free_float_days"] <= a["total_float_days"]


def test_critical_path_is_an_unbroken_chain(plan, acts):
    """Longest-path criticality must form one continuous chain of driving links -- that is
    the whole reason it is used instead of zero float once calendars are involved."""
    chain = plan["critical_path"]
    for earlier, later in zip(chain, chain[1:]):
        assert earlier in [d["id"] for d in acts[later]["predecessors"]], (
            f"{later} does not follow {earlier}")


def test_reported_float_is_actually_absorbable(plan):
    """The float number must survive contact with reality.

    A single backward pass over a working calendar reports float that vanishes when the
    delay is really taken, so every non-zero value is measured against a live forward pass.
    This test spot-checks that guarantee end to end: delay an activity by exactly the float
    it claims and the completion date must not move; one day more and it must."""
    assert plan["floats_verified"], "floats must be verified, not left as an upper bound"
    p = default_project("T", "C", "Hyderabad", "S", "o")
    a = engine.analyse(p)
    base = S.plan_schedule(p, a, {"start_date": "2026-01-05"})
    mob = next(x for x in base["activities"] if x["id"] == "mobilise")
    tf = mob["total_float_days"]
    at_float = S.plan_schedule(p, a, {"start_date": "2026-01-05",
                                      "mobilisation_days": 10 + tf})
    assert at_float["finish"] == base["finish"], "delaying by the reported float must be free"
    beyond = S.plan_schedule(p, a, {"start_date": "2026-01-05",
                                    "mobilisation_days": 10 + tf + 1})
    assert beyond["finish"] > base["finish"], "one day past the float must cost time"


def test_driving_chain_has_no_meaningful_float(plan):
    for a in plan["activities"]:
        if a["critical"]:
            assert a["total_float_days"] <= 1


# ---------------------------------------------------------------- derivation
def test_durations_are_derived_from_quantities(plan, acts):
    a = acts["t0_rebar_1"]
    assert a["quantity"] > 0 and a["output_per_day"] == 350.0
    assert a["work_days"] == S.duration_days(a["quantity"], 350.0, a["crew"])


def test_more_floors_means_a_longer_programme():
    p = default_project("T", "C", "Hyderabad", "S", "o")
    short = S.plan_schedule(p, engine.analyse(p), {"start_date": "2026-01-05"})
    p["towers"][0]["floors"] = 20
    long = S.plan_schedule(p, engine.analyse(p), {"start_date": "2026-01-05"})
    assert long["duration_calendar_days"] > short["duration_calendar_days"]
    assert long["activity_count"] > short["activity_count"]


def test_bigger_crews_shorten_the_job():
    p = default_project("T", "C", "Hyderabad", "S", "o")
    a = engine.analyse(p)
    base = S.plan_schedule(p, a, {"start_date": "2026-01-05"})
    big = S.plan_schedule(p, a, {"start_date": "2026-01-05",
                                 "crews": {"carpenter": 20, "bar_bender": 20,
                                           "concretor": 20, "mason": 20}})
    assert big["duration_calendar_days"] < base["duration_calendar_days"]


def test_crews_cannot_beat_the_concrete(plan):
    """Even with an unlimited workforce the floor cycle cannot go below the prop period --
    this is the guarantee that stops schedule pressure becoming a site failure."""
    p = default_project("T", "C", "Hyderabad", "S", "o")
    huge = {t: 500 for t in ("carpenter", "bar_bender", "concretor", "mason",
                             "tiler", "painter", "plumber")}
    r = S.plan_schedule(p, engine.analyse(p), {"start_date": "2026-01-05", "crews": huge,
                                               "slab_span_m": 6.0})
    assert r["safety"]["floor_cycle_days"] >= 14


def test_monsoon_delays_a_job_with_exposed_work():
    p = default_project("T", "C", "Hyderabad", "S", "o")
    a = engine.analyse(p)
    off = S.plan_schedule(p, a, {"start_date": "2026-01-05", "monsoon_blocks_exposed": False})
    on = S.plan_schedule(p, a, {"start_date": "2026-01-05", "monsoon_blocks_exposed": True})
    assert on["duration_calendar_days"] >= off["duration_calendar_days"]


# ---------------------------------------------------------------- outputs
def test_cash_flow_is_monotonic_and_totals_the_activity_cost(plan):
    cf = plan["cash_flow"]
    assert cf and all(cf[i]["cumulative"] <= cf[i + 1]["cumulative"] for i in range(len(cf) - 1))
    assert abs(cf[-1]["cumulative"] - plan["cost_check"]["scheduled_cost"]) < 1.0


def test_line_of_balance_has_ordered_floors(plan):
    lob = plan["line_of_balance"]
    assert lob
    for line in lob:
        fl = [p["floor"] for p in line["points"]]
        assert fl == sorted(fl)


def test_resource_histogram_reports_head_count(plan):
    r = plan["resources"]
    assert r and all(w["total"] > 0 for w in r)


def test_bad_input_returns_an_error_not_an_exception():
    r = S.plan_schedule({"towers": []}, {"areas": {"towers": []}}, {})
    assert r["ok"] is False and r["error"]["code"] == "no_towers"


# ---------------------------------------------------------------- target completion date
@pytest.fixture(scope="module")
def base_plan():
    p = default_project("T", "C", "Hyderabad", "S", "o")
    a = engine.analyse(p)
    return p, a, S.plan_schedule(p, a, {"start_date": "2026-01-05"}, summary=True)


def test_target_after_the_natural_finish_changes_nothing(base_plan):
    """A generous deadline must not quietly shrink the crews the user typed."""
    p, a, base = base_plan
    late = (date.fromisoformat(base["finish"]) + timedelta(days=400)).isoformat()
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05", "target_finish": late},
                        summary=True)
    assert r["target"]["status"] == "already_met"
    assert r["config"]["crew_multipliers"] == {}
    assert r["finish"] == base["finish"]


def test_target_inside_the_natural_finish_adds_labour_and_meets_it(base_plan):
    p, a, base = base_plan
    want = date.fromisoformat(base["finish"]) - timedelta(days=60)
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05",
                               "target_finish": want.isoformat()}, summary=True)
    assert r["target"]["status"] == "met_with_more_labour"
    assert date.fromisoformat(r["finish"]) <= want
    assert r["config"]["crew_multipliers"]                       # somebody got more people
    assert all(m > 1.0 for m in r["config"]["crew_multipliers"].values())


def test_solver_only_staffs_the_trades_that_drive_the_date(base_plan):
    """The point of the trim: a uniform staff-up is feasible but wasteful."""
    p, a, base = base_plan
    want = (date.fromisoformat(base["finish"]) - timedelta(days=60)).isoformat()
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05", "target_finish": want},
                        summary=True)
    assert len(r["target"]["crew_changes"]) < len(base["crews"])


def test_impossible_target_is_refused_not_faked(base_plan):
    """The floor is IS 456 curing and prop removal, which no crew size shortens.

    A programme that claimed to hit an impossible date could only do it by striking
    props early, so the engine has to say no and show the earliest safe date instead.
    """
    p, a, _ = base_plan
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05", "target_finish": "2026-04-01"},
                        summary=True)
    t = r["target"]
    assert t["status"] == "unreachable"
    assert t["days_short"] > 0
    assert date.fromisoformat(t["earliest_possible_finish"]) > date(2026, 4, 1)
    assert r["config"]["crew_multipliers"] == {}     # crews left as entered


def test_target_never_beats_the_code_minimum_floor_cycle(base_plan):
    """Compressing to a date must not compress the prop period."""
    p, a, base = base_plan
    want = (date.fromisoformat(base["finish"]) - timedelta(days=90)).isoformat()
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05", "target_finish": want,
                               "slab_span_m": 6.0})
    assert r["safety"]["floor_cycle_days"] >= 14
    assert not [f for f in r["safety"]["findings"] if f["severity"] == "critical"]


def test_target_before_start_is_rejected_gracefully(base_plan):
    p, a, _ = base_plan
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05", "target_finish": "2025-06-01"},
                        summary=True)
    assert r["ok"] is True and r["target"]["status"] == "invalid"


def test_solver_stays_within_its_evaluation_budget(base_plan):
    """Each evaluation is a forward pass over every activity; this runs per request."""
    p, a, base = base_plan
    want = (date.fromisoformat(base["finish"]) - timedelta(days=60)).isoformat()
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05", "target_finish": want},
                        summary=True)
    assert r["target"]["evaluations"] <= 60


# ---------------------------------------------------------------- user task edits
@pytest.fixture(scope="module")
def edit_base():
    p = default_project("T", "C", "Hyderabad", "S", "o")
    a = engine.analyse(p)
    return p, a, S.plan_schedule(p, a, {"start_date": "2026-01-05"})


def test_every_activity_says_whether_it_can_be_removed(edit_base):
    _, _, base = edit_base
    acts = base["activities"]
    assert any(x["removable"] for x in acts)
    assert any(not x["removable"] for x in acts)


def test_a_custom_task_joins_the_network_and_can_push_the_finish(edit_base):
    p, a, base = edit_base
    last = base["activities"][-1]["id"]
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05", "extra_tasks": [
        {"name": "Lift erection by vendor", "phase": "Finishing", "days": 20, "after": last}]})
    assert r["activity_count"] == base["activity_count"] + 1
    added = [x for x in r["activities"] if x["custom"]]
    assert len(added) == 1 and added[0]["name"] == "Lift erection by vendor"
    assert date.fromisoformat(r["finish"]) > date.fromisoformat(base["finish"])


def test_a_custom_task_with_an_unknown_predecessor_starts_at_the_project_start(edit_base):
    p, a, _ = edit_base
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05", "extra_tasks": [
        {"name": "Orphan", "days": 3, "after": "does_not_exist"}]})
    assert r["ok"] is True
    assert [x for x in r["activities"] if x["name"] == "Orphan"]


def test_removing_a_task_relinks_its_successors(edit_base):
    p, a, base = edit_base
    victim = next(x for x in base["activities"]
                  if x["removable"] and any(y for y in base["activities"]
                                            if any(d["id"] == x["id"] for d in y["predecessors"])))
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05", "excluded_tasks": [victim["id"]]})
    ids = {x["id"] for x in r["activities"]}
    assert victim["id"] not in ids
    # nothing may still point at the removed task, or the forward pass would KeyError
    for x in r["activities"]:
        for d in x["predecessors"]:
            assert d["id"] in ids


def test_removing_a_task_a_code_lag_hangs_off_is_refused(edit_base):
    """The whole point: deleting a pour would orphan its prop-removal wait."""
    p, a, base = edit_base
    protected = next(x for x in base["activities"]
                     if not x["removable"] and not x["milestone"])
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05",
                               "excluded_tasks": [protected["id"]]})
    assert r["activity_count"] == base["activity_count"]
    assert any(w["severity"] == "critical" for w in r["warnings"])


def test_removing_an_unknown_task_is_a_note_not_a_crash(edit_base):
    p, a, base = edit_base
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05", "excluded_tasks": ["nope"]})
    assert r["ok"] is True and r["activity_count"] == base["activity_count"]


def test_custom_tasks_are_counted_when_solving_for_a_finish_date(edit_base):
    """A task the user added has to be part of what the solver plans around."""
    p, a, base = edit_base
    last = base["activities"][-1]["id"]
    want = base["finish"]
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05", "target_finish": want,
                               "extra_tasks": [{"name": "Snagging", "days": 15,
                                                "after": last, "phase": "Handover"}]},
                        summary=True)
    # The extra 15 days sit past the old finish, so hitting it now needs the solver.
    assert r["target"]["status"] in ("met_with_more_labour", "unreachable")


def test_float_verification_budget_scales_with_programme_size():
    """A replay is a pass over every activity, so the budget has to count activities.

    Budgeting replays alone made a four-tower job spend over a minute per re-plan.
    """
    p = default_project("T", "C", "Hyderabad", "S", "o")
    base = (p.get("towers") or [{}])[0]
    p["towers"] = [dict(base, name=f"T{i + 1}", floors=18) for i in range(4)]
    a = engine.analyse(p)
    t0 = time.time()
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05"})
    assert r["ok"] and r["activity_count"] > 500
    assert time.time() - t0 < 10          # was over 120 s with a replay-count budget
    assert r["floats_verified"] is False  # and it says so rather than pretending
