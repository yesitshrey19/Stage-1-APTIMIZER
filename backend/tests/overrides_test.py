"""Wave 2 tests: the override layer over the generated programme.

The rule this file exists to hold: IS 456 curing and prop-removal lags outrank every user
input, including a pinned date. A pin that would strike props early is the one instruction
this engine must refuse, and it must say why rather than silently ignoring it.
"""
import sys, os
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, schedule as S
from defaults import default_project


@pytest.fixture(scope="module")
def base():
    p = default_project("OV", "C", "Hyderabad", "S", "o")
    a = engine.analyse(p)
    return p, a, S.plan_schedule(p, a, {"start_date": "2026-01-05"})


def plan(p, a, overrides, **extra):
    cfg = {"start_date": "2026-01-05", "task_overrides": overrides}
    cfg.update(extra)
    return S.plan_schedule(p, a, cfg)


def task(result, tid):
    return next(x for x in result["activities"] if x["id"] == tid)


def a_real_task(result):
    return next(x for x in result["activities"]
                if not x["milestone"] and x["quantity"] > 0 and x["output_per_day"] > 0)


# ---------------------------------------------------------------- absent means generated
def test_no_overrides_changes_nothing(base):
    p, a, b = base
    assert plan(p, a, {})["finish"] == b["finish"]


def test_an_override_of_one_field_leaves_the_others_generated(base):
    p, a, b = base
    t = a_real_task(b)
    r = task(plan(p, a, {t["id"]: {"name": "Renamed"}}), t["id"])
    assert r["name"] == "Renamed"
    assert r["work_days"] == t["work_days"] and r["crew"] == t["crew"]
    assert r["cost"] == t["cost"]


def test_the_generated_value_is_kept_so_a_reset_can_restore_it(base):
    p, a, b = base
    t = a_real_task(b)
    r = task(plan(p, a, {t["id"]: {"name": "Renamed"}}), t["id"])
    assert r["generated"]["name"] == t["name"]


# ---------------------------------------------------------------- crew and days
def test_editing_crew_alone_recomputes_days_from_the_quantity(base):
    """This is the arithmetic that makes this an engine rather than a spreadsheet: if the
    crew doubles and the days do not move, the tool is lying about productivity."""
    p, a, b = base
    t = next(x for x in b["activities"]
             if not x["milestone"] and x["quantity"] > 0 and x["work_days"] >= 4)
    r = task(plan(p, a, {t["id"]: {"crew": t["crew"] * 2}}), t["id"])
    assert r["crew"] == t["crew"] * 2
    assert r["work_days"] < t["work_days"]
    assert r["edited"].get("work_days_from_crew") is True


def test_an_edited_days_beats_an_edited_crew(base):
    """Precedence: a pinned date > edited Days > edited Crew > generated."""
    p, a, b = base
    t = next(x for x in b["activities"]
             if not x["milestone"] and x["quantity"] > 0 and x["work_days"] >= 4)
    r = task(plan(p, a, {t["id"]: {"crew": t["crew"] * 2, "work_days": 9}}), t["id"])
    assert r["work_days"] == 9                      # Days wins
    assert r["crew"] == t["crew"] * 2               # crew still applied, for labour cost
    assert "work_days_from_crew" not in r["edited"]


def test_editing_days_reports_the_productivity_it_implies(base):
    p, a, b = base
    t = next(x for x in b["activities"] if not x["milestone"] and x["quantity"] > 0)
    r = task(plan(p, a, {t["id"]: {"work_days": 20}}), t["id"])
    assert r["edited"]["implied_output_per_day"] == pytest.approx(
        t["quantity"] / (20 * r["crew"]), rel=0.01)


def test_editing_days_moves_downstream_work(base):
    p, a, b = base
    t = next(x for x in b["activities"] if not x["milestone"] and x["critical"])
    r = plan(p, a, {t["id"]: {"work_days": t["work_days"] + 40}})
    assert date.fromisoformat(r["finish"]) > date.fromisoformat(b["finish"])


# ---------------------------------------------------------------- pinned dates
def test_pinning_later_is_honoured_and_moves_downstream(base):
    """This is how a user models a slip the generated plan cannot know about."""
    p, a, b = base
    t = next(x for x in b["activities"] if not x["milestone"] and x["critical"])
    later = (date.fromisoformat(t["start"]) + timedelta(days=30)).isoformat()
    r = plan(p, a, {t["id"]: {"start": later}})
    assert task(r, t["id"])["start"] == later
    assert not task(r, t["id"])["pin_conflict"]
    assert date.fromisoformat(r["finish"]) > date.fromisoformat(b["finish"])


def test_a_pin_that_would_strike_props_early_is_refused(base):
    """The one instruction this engine must never follow."""
    p, a, b = base
    t = next(x for x in b["activities"]
             if any(d.get("hard") for d in x["predecessors"]) and not x["milestone"])
    early = (date.fromisoformat(t["start"]) - timedelta(days=20)).isoformat()
    r = task(plan(p, a, {t["id"]: {"start": early}}), t["id"])
    assert r["start"] == t["start"], "the unsafe pin was applied"
    assert r["pin_conflict"]
    assert "code-mandated" in r["pin_conflict"] or "IS 456" in r["pin_conflict"]


def test_a_refused_pin_names_the_blocking_predecessor_and_the_shortfall(base):
    p, a, b = base
    t = next(x for x in b["activities"]
             if any(d.get("hard") for d in x["predecessors"]) and not x["milestone"])
    early = (date.fromisoformat(t["start"]) - timedelta(days=20)).isoformat()
    msg = task(plan(p, a, {t["id"]: {"start": early}}), t["id"])["pin_conflict"]
    assert "20 days" in msg
    pred_names = {x["name"] for x in b["activities"]}
    assert any(n in msg for n in pred_names), "no predecessor named"


def test_clearing_a_pin_restores_the_generated_date(base):
    p, a, b = base
    t = next(x for x in b["activities"] if not x["milestone"] and x["critical"])
    later = (date.fromisoformat(t["start"]) + timedelta(days=30)).isoformat()
    pinned = plan(p, a, {t["id"]: {"start": later}})
    assert task(pinned, t["id"])["start"] == later
    cleared = plan(p, a, {t["id"]: {}})
    assert task(cleared, t["id"])["start"] == t["start"]


def test_an_unreadable_date_is_reported_not_crashed(base):
    p, a, b = base
    t = a_real_task(b)
    r = plan(p, a, {t["id"]: {"start": "not-a-date"}})
    assert r["ok"]
    assert any("not a date the programme could read" in w["text"] for w in r["warnings"])


def test_no_pin_can_produce_a_programme_that_fails_the_date_audit(base):
    """Whatever the user pins, the realised gaps must still clear the code minimums."""
    p, a, b = base
    hard = [x for x in b["activities"]
            if any(d.get("hard") for d in x["predecessors"]) and not x["milestone"]][:6]
    overrides = {x["id"]: {"start": (date.fromisoformat(x["start"])
                                     - timedelta(days=25)).isoformat()} for x in hard}
    r = plan(p, a, overrides)
    assert [f for f in r["safety"]["findings"] if f["severity"] == "critical"] == []


def test_the_date_audit_catches_a_violation_the_forward_pass_missed(base):
    """Belt and braces: _forward refuses unsafe pins, and this is the check that would
    catch a future path which forgot to."""
    p, a, _ = base
    cfg = S.ScheduleConfig.from_dict({"start_date": "2026-01-05"})
    acts, _ = S.build_activities(p, a, cfg)
    acts, _ = S.apply_task_edits(acts, cfg)
    by = {x.id: x for x in acts}
    tgt = next(x for x in acts
               if any(d.hard and "Cl. 13.5" in (d.reason or "") for d in x.deps))
    pred = by[next(d.pred for d in tgt.deps if d.hard)]
    pred.ef = date(2026, 3, 1)
    tgt.es = date(2026, 3, 2)                      # one day where the code wants seven
    findings = S.audit_scheduled_dates(acts, cfg, 4.0)
    assert findings and findings[0]["severity"] == "critical"
    assert "pinned date" in findings[0]["text"]


# ---------------------------------------------------------------- cost
def test_a_cost_override_moves_the_phase_and_project_totals(base):
    p, a, b = base
    t = max((x for x in b["activities"] if x["cost"] > 0), key=lambda x: x["cost"])
    ph = next(x for x in b["phases"] if x["phase"] == t["phase"])
    r = plan(p, a, {t["id"]: {"cost": t["cost"] * 2}})
    ph2 = next(x for x in r["phases"] if x["phase"] == t["phase"])
    assert ph2["cost"] == pytest.approx(ph["cost"] + t["cost"], rel=1e-6)
    assert r["cost_check"]["scheduled_cost"] > b["cost_check"]["scheduled_cost"]


def test_a_cost_override_does_not_touch_the_boq(base):
    """Decided and not reopened: the variance line reports the gap instead."""
    p, a, b = base
    t = max((x for x in b["activities"] if x["cost"] > 0), key=lambda x: x["cost"])
    r = plan(p, a, {t["id"]: {"cost": t["cost"] * 5}})
    assert r["cost_check"]["boq_grand_total"] == b["cost_check"]["boq_grand_total"]


# ---------------------------------------------------------------- ordering
def test_order_is_display_only_and_never_moves_a_date(base):
    """Dependencies decide when work happens. Dragging a row must not change the plan."""
    p, a, b = base
    t = a_real_task(b)
    r = plan(p, a, {t["id"]: {"order": 0}})
    assert r["finish"] == b["finish"]
    assert task(r, t["id"])["start"] == t["start"]
    assert task(r, t["id"])["order"] == 0


def test_a_new_task_lands_where_it_was_dropped(base):
    """Left to date ties, a day-zero task landed third behind two others sharing the date."""
    p, a, _ = base
    r = S.plan_schedule(p, a, {"start_date": "2026-01-05", "extra_tasks": [
        {"name": "Survey", "phase": "Pre-construction", "days": 2, "order": 0}]})
    pre = [x for x in r["activities"] if x["phase"] == "Pre-construction"]
    pre.sort(key=lambda x: (x["order"] if x["order"] is not None else 10 ** 6, x["start"]))
    assert pre[0]["name"] == "Survey"


def test_overrides_round_trip_through_the_config(base):
    p, a, _ = base
    ov = {"t0_pcc": {"name": "X", "work_days": 3, "order": 1}}
    cfg = S.ScheduleConfig.from_dict({"task_overrides": ov})
    assert cfg.to_dict()["task_overrides"] == ov


def test_an_override_for_a_task_that_no_longer_exists_is_ignored(base):
    p, a, b = base
    r = plan(p, a, {"gone_task_id": {"name": "X", "work_days": 5}})
    assert r["ok"] and r["finish"] == b["finish"]
