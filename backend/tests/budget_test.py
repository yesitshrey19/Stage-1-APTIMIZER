"""The headline project cost must move when the programme does.

The bug: the sidebar showed engine.analyse()["cost"]["total"] -- the BOQ grand total and
nothing else. Compressing the finish date, extending it, or adding a task all left it at
exactly the same figure, so the app said building faster was free and that added work cost
nothing. The BOQ prices the WORK; the programme prices HOW and WHEN it is built.
"""
import sys, os
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, schedule as S
from defaults import default_project


@pytest.fixture(scope="module")
def base():
    p = default_project("BG", "QA", "Hyderabad", "BG-1", "owner")
    a = engine.analyse(p)
    return p, a, S.plan_schedule(p, a, {"start_date": "2026-01-05"})


def plan(p, a, **cfg):
    return S.plan_schedule(p, a, {"start_date": "2026-01-05", **cfg})


def test_the_budget_starts_from_the_boq(base):
    _, a, b = base
    assert b["budget"]["boq_total"] == pytest.approx(a["boq"]["grand_total"], rel=1e-9)


def test_the_parts_add_up_to_the_total(base):
    _, _, b = base
    x = b["budget"]
    assert x["project_total"] == pytest.approx(
        x["boq_total"] + x["acceleration_premium"] + x["lost_productivity"]
        + x["preliminaries"] + x["added_tasks"], rel=1e-9)
    assert x["programme_adjustment"] == pytest.approx(
        x["project_total"] - x["boq_total"], rel=1e-9)


def test_more_crew_raises_the_project_total(base):
    """The reported bug, directly: the headline cost never moved when crews were added."""
    p, a, b = base
    base_total = b["budget"]["project_total"]
    for mult in (2.0, 3.0):
        r = plan(p, a, crew_multipliers={k: mult for k in b["crews"]})
        assert r["budget"]["project_total"] > base_total
        assert r["budget"]["acceleration_premium"] > 0
        assert r["budget"]["lost_productivity"] > 0
    assert (plan(p, a, crew_multipliers={k: 3.0 for k in b["crews"]})["budget"]["project_total"]
            > plan(p, a, crew_multipliers={k: 2.0 for k in b["crews"]})["budget"]["project_total"])


def test_an_added_task_reaches_the_project_total(base):
    """The second reported bug: adding work did not change the budget."""
    p, a, b = base
    before = b["budget"]["project_total"]
    r = plan(p, a, extra_tasks=[{"name": "Lift erection", "phase": "Finishing",
                                 "days": 20, "cost": 5_000_000}])
    assert r["budget"]["added_tasks"] == pytest.approx(5_000_000, rel=1e-6)
    assert r["budget"]["project_total"] == pytest.approx(before + 5_000_000, rel=1e-4)


def test_several_added_tasks_all_count(base):
    p, a, b = base
    before = b["budget"]["project_total"]
    r = plan(p, a, extra_tasks=[
        {"name": "A", "phase": "Finishing", "days": 5, "cost": 1_000_000},
        {"name": "B", "phase": "Handover", "days": 5, "cost": 2_500_000},
    ])
    assert r["budget"]["added_tasks"] == pytest.approx(3_500_000, rel=1e-6)
    assert r["budget"]["project_total"] > before


def test_a_later_finish_raises_the_total_through_site_running_cost(base):
    p, a, b = base
    later = (date.fromisoformat(b["finish"]) + timedelta(days=150)).isoformat()
    r = plan(p, a, target_finish=later)
    assert r["budget"]["preliminaries"] > b["budget"]["preliminaries"]
    assert r["budget"]["project_total"] > b["budget"]["project_total"]


def test_the_boq_itself_is_never_altered(base):
    """Programme cost is added on top; the bill of quantities is not rewritten."""
    p, a, b = base
    r = plan(p, a, crew_multipliers={k: 3.0 for k in b["crews"]},
             extra_tasks=[{"name": "X", "phase": "Finishing", "days": 5, "cost": 900_000}])
    assert r["budget"]["boq_total"] == b["budget"]["boq_total"]


def test_the_budget_is_in_the_summary_payload_too(base):
    """The sidebar uses the summary form, so it has to carry the budget."""
    p, a, _ = base
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05"}, summary=True)
    assert r["budget"]["project_total"] > 0


def test_the_note_explains_what_was_added(base):
    _, _, b = base
    note = b["budget"]["note"]
    assert "BOQ prices the work" in note
