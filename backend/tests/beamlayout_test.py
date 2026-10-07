"""Block 5 beam layout tests.

The continuity rule is the reason this file exists. A beam continuous over interior
supports carries the same load in a shallower section than a simply supported one, so the
END bays of a run must be sized simply supported and the interior bays continuous. Sizing
a whole run as continuous is the common error and it under-sizes the ends.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, engineering as E, takeoff as T
from defaults import default_project


@pytest.fixture(scope="module")
def layout():
    return T.beam_layout(6.0, 4.5, 4, 3)


def test_a_layout_is_drawable_lines_not_just_sizes(layout):
    """A section without a position is not a layout and cannot be checked against anything."""
    for b in layout["beams"]:
        for k in ("x1", "y1", "x2", "y2"):
            assert isinstance(b[k], float)
        # Every beam must have real length in exactly one direction.
        dx, dy = abs(b["x2"] - b["x1"]), abs(b["y2"] - b["y1"])
        assert (dx > 0) != (dy > 0)
        assert b["span_m"] == pytest.approx(max(dx, dy), abs=0.01)


def test_end_bays_are_simply_supported_and_interior_bays_continuous(layout):
    run = [b for b in layout["beams"] if b["run"] == "BX1"]
    run.sort(key=lambda b: b["x1"])
    assert len(run) == 4
    assert run[0]["support"] == "simply supported"
    assert run[-1]["support"] == "simply supported"
    assert all(b["support"] == "continuous" for b in run[1:-1])


def test_continuity_buys_a_shallower_section_at_the_same_span(layout):
    """This is the whole point of tracking continuity: same span, less depth."""
    run = sorted([b for b in layout["beams"] if b["run"] == "BX1"], key=lambda b: b["x1"])
    end, interior = run[0], run[1]
    assert end["span_m"] == interior["span_m"]
    assert interior["depth_mm"] < end["depth_mm"]


def test_a_single_bay_run_is_never_treated_as_continuous():
    """One span between two columns has no interior support to be continuous over."""
    r = T.beam_layout(5.0, 5.0, 1, 1)
    assert r["beams"]
    assert all(b["support"] == "simply supported" for b in r["beams"])


def test_beam_count_matches_the_grid(layout):
    # 4x3 bays: (bays_y+1) runs of bays_x, plus (bays_x+1) runs of bays_y.
    assert layout["summary"]["beam_count"] == (3 + 1) * 4 + (4 + 1) * 3


def test_every_beam_sits_on_a_grid_intersection(layout):
    bx, by = 6.0, 4.5
    for b in layout["beams"]:
        for x in (b["x1"], b["x2"]):
            assert abs(x / bx - round(x / bx)) < 1e-6
        for y in (b["y1"], b["y2"]):
            assert abs(y / by - round(y / by)) < 1e-6


def test_span_depth_ratio_stays_inside_the_preliminary_divisors(layout):
    for b in layout["beams"]:
        limit = 15.0 if b["support"] == "continuous" else 12.0
        assert b["span_depth_ratio"] <= limit + 0.01


def test_sections_respect_the_is456_minimums(layout):
    for b in layout["beams"]:
        assert b["width_mm"] >= 230        # minimum practical beam width
        assert b["depth_mm"] >= 300


def test_schedule_counts_every_beam(layout):
    assert sum(r["count"] for r in layout["schedule"]) == layout["summary"]["beam_count"]
    assert layout["summary"]["distinct_sections"] == len(layout["schedule"])


def test_runs_partition_the_beams(layout):
    assert sum(r["spans"] for r in layout["runs"]) == layout["summary"]["beam_count"]
    assert sum(r["length_m"] for r in layout["runs"]) == pytest.approx(
        layout["summary"]["total_length_m"], abs=0.05)


def test_an_empty_grid_returns_nothing_rather_than_crashing():
    r = T.beam_layout(5.0, 5.0, 0, 0)
    assert r["beams"] == [] and r["runs"] == []


def test_layout_reaches_the_engineering_module():
    p = default_project("T", "C", "Hyderabad", "S", "o")
    grid = E.analyse_engineering(p, engine.analyse(p))["modules"]["grid"]
    assert grid["beam_layout"]["summary"]["beam_count"] > 0
    labels = [o["label"] for o in grid["outputs"]]
    assert "Beams in the frame" in labels
    assert "Deepest beam" in labels
