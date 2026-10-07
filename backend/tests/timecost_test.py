"""Fix 5: acceleration is not free, and neither is delay.

The bug this closes. Duration was quantity / (output x crew) and labour cost was
work_days x crew x wage, so the crew term cancelled exactly:

    cost = (qty / (out x crew)) x crew x wage = (qty / out) x wage

Doubling the crew halved the days, doubled the headcount, and landed on the same rupee
figure. Material was qty x rate and did not move either. So pulling a finish date in --
which is exactly what solve_for_target() does by adding labour -- left the budget
unchanged. The model said acceleration was free, which is wrong in the direction that
matters commercially: it is the one trade a developer is being asked to price.

The shape that must hold, and the reason this file exists: cost is at its MINIMUM near the
naturally derived duration and rises in BOTH directions.
"""
import sys, os
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, schedule as S
from defaults import default_project


@pytest.fixture(scope="module")
def base():
    p = default_project("TC", "QA", "Hyderabad", "TC-1", "owner")
    a = engine.analyse(p)
    return p, a, S.plan_schedule(p, a, {"start_date": "2026-01-05"})


def plan(p, a, **cfg):
    return S.plan_schedule(p, a, {"start_date": "2026-01-05", **cfg})


# ---------------------------------------------------------------- the curve
def test_the_baseline_is_the_cheapest_of_the_three(base):
    """The headline requirement. Compressed costs more, extended costs more, and the
    naturally derived duration sits at the bottom."""
    p, a, b = base
    finish = date.fromisoformat(b["finish"])
    baseline = b["time_cost"]["total_cost"]

    compressed = plan(p, a, target_finish=(finish - timedelta(days=90)).isoformat())
    extended = plan(p, a, target_finish=(finish + timedelta(days=120)).isoformat())

    assert compressed["time_cost"]["total_cost"] > baseline, "compression was free"
    assert extended["time_cost"]["total_cost"] > baseline, "delay was free"


def test_compressing_further_costs_more_than_compressing_a_little(base):
    p, a, b = base
    finish = date.fromisoformat(b["finish"])
    a_bit = plan(p, a, target_finish=(finish - timedelta(days=45)).isoformat())
    a_lot = plan(p, a, target_finish=(finish - timedelta(days=120)).isoformat())
    assert a_lot["time_cost"]["total_cost"] > a_bit["time_cost"]["total_cost"]


def test_extending_further_costs_more_than_extending_a_little(base):
    p, a, b = base
    finish = date.fromisoformat(b["finish"])
    a_bit = plan(p, a, target_finish=(finish + timedelta(days=60)).isoformat())
    a_lot = plan(p, a, target_finish=(finish + timedelta(days=240)).isoformat())
    assert a_lot["time_cost"]["total_cost"] > a_bit["time_cost"]["total_cost"]


# ---------------------------------------------------------------- (a) productivity
def test_the_crew_term_no_longer_cancels(base):
    """The original bug, stated directly: doubling the crew must not land on the same cost."""
    p, a, b = base
    doubled = plan(p, a, crew_multipliers={k: 2.0 for k in b["crews"]})
    assert doubled["cost_check"]["scheduled_cost"] > b["cost_check"]["scheduled_cost"] * 1.02


def test_output_per_head_falls_as_crews_are_added():
    assert S.crew_efficiency(1.0) == 1.0
    assert S.crew_efficiency(2.0) == pytest.approx(0.85, abs=0.01)
    assert S.crew_efficiency(3.0) == pytest.approx(0.72, abs=0.01)
    for lo, hi in ((1.0, 1.5), (1.5, 2.0), (2.0, 3.0)):
        assert S.crew_efficiency(hi) < S.crew_efficiency(lo)


def test_efficiency_is_flat_below_and_above_the_table():
    assert S.crew_efficiency(0.5) == 1.0        # never better than the natural crew
    assert S.crew_efficiency(9.0) == S.crew_efficiency(3.0)


def test_doubling_the_crew_no_longer_halves_the_duration(base):
    """If it still halved, the derating is not reaching the duration calculation."""
    p, a, b = base
    doubled = plan(p, a, crew_multipliers={k: 2.0 for k in b["crews"]})
    base_days = b["duration_calendar_days"]
    got = doubled["duration_calendar_days"]
    assert got > base_days * 0.5, "duration halved -- derating is not applied"
    assert got < base_days, "more crew did not shorten anything"


# ---------------------------------------------------------------- (b) premium
def test_the_premium_rises_with_the_multiplier():
    assert S.wage_premium(1.0) == 0.0
    assert 0.25 <= S.wage_premium(3.0) <= 0.35     # the brief's 25-35% at the ceiling
    assert S.wage_premium(2.0) > S.wage_premium(1.5)


def test_the_premium_is_reported_as_its_own_line(base):
    """The user is trading money for time and has to see the price separately, not find
    it blended into a rate."""
    p, a, b = base
    assert b["time_cost"]["acceleration_premium"] == 0.0
    fast = plan(p, a, crew_multipliers={k: 2.0 for k in b["crews"]})
    assert fast["time_cost"]["acceleration_premium"] > 0
    assert fast["time_cost"]["lost_productivity"] > 0


def test_each_task_carries_its_own_premium_and_efficiency(base):
    p, a, b = base
    fast = plan(p, a, crew_multipliers={k: 2.0 for k in b["crews"]})
    worked = [x for x in fast["activities"] if not x["milestone"] and x["crew_multiplier"] > 1]
    assert worked
    assert all(x["crew_efficiency"] < 1.0 for x in worked)
    assert sum(x["acceleration_premium"] for x in worked) == pytest.approx(
        fast["time_cost"]["acceleration_premium"], rel=0.01)


# ---------------------------------------------------------------- (c) preliminaries
def test_preliminaries_use_the_engines_own_rate_not_a_parallel_one():
    """Reusing engine.DEFAULT_COST_ADDERS means the two modules cannot disagree about
    what preliminaries cost."""
    import engine as E
    p = default_project("TC2", "QA", "Hyderabad", "TC-2", "owner")
    a = engine.analyse(p)
    works = float(a["boq"]["works_total"])
    pct = E.DEFAULT_COST_ADDERS["preliminaries_pct"]
    got = S.preliminaries_per_month(p, a, 12.0)
    assert got == pytest.approx(works * pct / 100.0 / 12.0, rel=1e-6)


def test_the_monthly_rate_does_not_self_cancel(base):
    """If the rate were divided by the ACTUAL duration rather than the natural one, the
    total would be identical at every programme length -- reintroducing the bug one level
    up. This is the test that caught exactly that."""
    p, a, b = base
    finish = date.fromisoformat(b["finish"])
    short = plan(p, a, target_finish=(finish - timedelta(days=90)).isoformat())
    long_ = plan(p, a, target_finish=(finish + timedelta(days=180)).isoformat())
    assert long_["time_cost"]["preliminaries_total"] > b["time_cost"]["preliminaries_total"]
    assert short["time_cost"]["preliminaries_total"] < b["time_cost"]["preliminaries_total"]


def test_a_committed_later_date_keeps_the_site_open(base):
    """The work finishes when it finishes, but the site is not demobilised until the date
    the project committed to."""
    p, a, b = base
    finish = date.fromisoformat(b["finish"])
    later = (finish + timedelta(days=120)).isoformat()
    r = plan(p, a, target_finish=later)
    assert r["finish"] == b["finish"]                    # the work did not get slower
    assert r["time_cost"]["billed_to"] == later          # but preliminaries run to the date
    assert r["time_cost"]["extended_days"] == 120


def test_the_baseline_bills_to_its_own_finish(base):
    _, _, b = base
    assert b["time_cost"]["billed_to"] == b["finish"]
    assert b["time_cost"]["extended_days"] == 0


# ---------------------------------------------------------------- payload
def test_the_breakdown_adds_up(base):
    p, a, b = base
    fast = plan(p, a, crew_multipliers={k: 2.5 for k in b["crews"]})
    t = fast["time_cost"]
    assert t["total_cost"] == pytest.approx(
        t["scheduled_cost"] + t["preliminaries_total"], rel=1e-6)


def test_the_note_explains_both_directions(base):
    _, _, b = base
    note = b["time_cost"]["note"]
    assert "Compressing" in note and "extending" in note
