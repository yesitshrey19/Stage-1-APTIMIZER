"""Site layout engine — stage 3b, genetic refinement of tower placement.

The GA's value is not that it finds a better layout; it is that it cannot return a worse
one, cannot return an unlawful one, and returns the same one twice. Those three are what
is asserted here. A refinement that sometimes improves things is useful. A refinement that
sometimes quietly degrades them is worse than no refinement at all, because nobody reviews
a stage that is assumed to be an improvement.

    pytest backend/tests/siteplan_ga_test.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siteplan import (PackContext, SiteLayoutConfig, build_envelope,  # noqa: E402
                      evaluate, greedy_pack, plan, reserve)
from siteplan.fitness import required_spacing  # noqa: E402
from siteplan.frame import LocalFrame  # noqa: E402
from siteplan.ga import refine  # noqa: E402

FRAME = LocalFrame(12.9716, 77.5946)

# Small search settings: these tests are about the guarantees, not about how much the GA
# can find, and the full population would make the file slow for no extra assurance.
FAST = {"population": 12, "generations": 8, "time_budget_s": 1.0}


def to_latlng(points):
    return [FRAME.to_latlng(x, y) for x, y in points]


def seeded(width=200.0, height=120.0, **over):
    """A greedy layout and the context it was packed against."""
    base = {"setbacks": {"default": 5}}
    base.update(over)
    cfg = SiteLayoutConfig.from_dict(base)
    pts = [(0, 0), (width, 0), (width, height), (0, height)]
    env = build_envelope(to_latlng(pts), [], cfg)
    res = reserve(env, cfg)
    ctx = PackContext(region=res.residual, roads=res.roads,
                      plot_area=env.plot.area, cfg=cfg)
    towers, _ = greedy_pack(ctx)
    return towers, ctx


# ---------------------------------------------------------------- the ratchet
def test_refinement_never_returns_a_layout_worse_than_its_seed():
    """The one property the whole stage rests on. Greedy is a floor, not a starting guess
    to be wandered away from."""
    seed, ctx = seeded()
    assert seed, "fixture must produce a seed to refine"
    before = evaluate(seed, ctx)

    best, report = refine(seed, ctx, **FAST)
    after = evaluate(best, ctx)

    assert after.feasible
    assert after.score >= before.score
    if not report["improved"]:
        assert best == seed, "an unimproved run must hand back the seed itself"


def test_an_unimproved_run_reports_zero_rather_than_a_negative_gain():
    seed, ctx = seeded()
    _, report = refine(seed, ctx, **FAST)
    assert report["improvement_pct"] >= 0.0


# ---------------------------------------------------------------- lawfulness
def test_refinement_cannot_buy_floor_area_with_a_spacing_shortfall():
    """`evaluate` prices spacing as a soft penalty, so a large enough area gain would pay
    for a shortfall. Greedy never makes that trade, and neither may this."""
    seed, ctx = seeded(240.0, 150.0)
    assert seed
    best, _ = refine(seed, ctx, **FAST)
    for i, a in enumerate(best):
        for b in best[i + 1:]:
            need = required_spacing(a, b, ctx.cfg)
            assert a.polygon.distance(b.polygon) >= need - 0.05, (
                f"{a.name}/{b.name} closer than the light-and-ventilation minimum")


def test_refined_towers_stay_inside_the_packable_region():
    seed, ctx = seeded()
    best, _ = refine(seed, ctx, **FAST)
    for t in best:
        assert ctx.contains(t.polygon), f"{t.name} escaped the packable region"


def test_refined_towers_do_not_overlap_each_other():
    seed, ctx = seeded(240.0, 150.0)
    best, _ = refine(seed, ctx, **FAST)
    for i, a in enumerate(best):
        for b in best[i + 1:]:
            assert a.polygon.intersection(b.polygon).area == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------- determinism
def test_the_same_layout_refines_the_same_way_twice():
    """A site plan that moved when nothing changed could not be reviewed, or signed."""
    seed, ctx = seeded()
    a, ra = refine(seed, ctx, **FAST)
    b, rb = refine(seed, ctx, **FAST)
    assert ra["final_score"] == rb["final_score"]
    assert [(round(t.cx, 6), round(t.cy, 6), t.floors) for t in a] == \
           [(round(t.cx, 6), round(t.cy, 6), t.floors) for t in b]


# ---------------------------------------------------------------- refusals
def test_an_empty_seed_is_reported_not_bred_from():
    _, ctx = seeded()
    best, report = refine([], ctx, **FAST)
    assert best == []
    assert report["ran"] is False
    assert "nothing to refine" in report["stopped"]


def test_the_time_budget_is_honoured():
    import time
    seed, ctx = seeded(240.0, 150.0)
    started = time.perf_counter()
    refine(seed, ctx, population=40, generations=10_000, time_budget_s=0.5)
    # Generous headroom: the budget is checked between generations, so one generation may
    # overrun it. What must not happen is 10,000 of them.
    assert time.perf_counter() - started < 10.0


# ---------------------------------------------------------------- pipeline wiring
def test_plan_records_which_method_produced_the_layout():
    out = plan(to_latlng([(0, 0), (200, 0), (200, 120), (0, 120)]), [],
               SiteLayoutConfig.from_dict({"setbacks": {"default": 5}}))
    assert out.method in ("greedy", "genetic")
    if out.method == "genetic":
        assert out.refinement and out.refinement["improved"] is True
        assert out.refinement["final_score"] > out.refinement["seed_score"]


def test_refinement_can_be_switched_off_and_then_the_layout_is_the_greedy_one():
    coords = to_latlng([(0, 0), (200, 0), (200, 120), (0, 120)])
    off = plan(coords, [], SiteLayoutConfig.from_dict(
        {"setbacks": {"default": 5}, "ga": {"enabled": False}}))
    assert off.method == "greedy"
    assert off.refinement is None

    seed, ctx = seeded()
    assert [t.name for t in off.towers] == [t.name for t in seed]


def test_the_serialised_layout_carries_the_refinement_account():
    out = plan(to_latlng([(0, 0), (200, 0), (200, 120), (0, 120)]), [],
               SiteLayoutConfig.from_dict({"setbacks": {"default": 5}})).to_dict()
    assert "method" in out and "refinement" in out
    if out["method"] == "genetic":
        assert out["refinement"]["improvement_pct"] > 0
