"""Site layout engine — Stage 3a (greedy tower packing).

The defect this stage exists to fix is towers escaping a non-rectangular plot boundary,
so containment is asserted on every shape, not just the easy rectangle.

    pytest backend/tests/siteplan_pack_test.py -v
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siteplan import SiteLayoutConfig, plan, plan_site  # noqa: E402
from siteplan.fitness import PackContext, evaluate, required_spacing  # noqa: E402
from siteplan.frame import LocalFrame, polygons_of  # noqa: E402

LAT0, LNG0 = 12.9716, 77.5946
FRAME = LocalFrame(LAT0, LNG0)

# Concave shapes whose bounding box is much larger than the polygon — exactly the case
# the old bbox layout got wrong.
SHAPES = {
    "rect": [(0, 0), (220, 0), (220, 160), (0, 160)],
    "L": [(0, 0), (230, 0), (230, 130), (110, 130), (110, 250), (0, 250)],
    "notched": [(0, 0), (240, 0), (240, 170), (150, 95), (70, 170), (0, 170)],
    "triangle": [(0, 0), (260, 0), (130, 200)],
    "irregular": [(0, 0), (200, 20), (250, 140), (150, 110), (60, 190), (0, 150)],
}


def to_latlng(points):
    return [FRAME.to_latlng(x, y) for x, y in points]


def conf(**over):
    base = {"setbacks": {"default": 5, "front": 9}, "fast_preview": True}
    base.update(over)
    return SiteLayoutConfig.from_dict(base)


def run(shape_key, **over):
    return plan(to_latlng(SHAPES[shape_key]), [{"edge_index": 0}], conf(**over))


# ---------------------------------------------------------------- the actual defect
@pytest.mark.parametrize("shape_key", list(SHAPES))
def test_every_tower_is_inside_the_plot_boundary(shape_key):
    """The reported defect: a footprint extending outside the drawn boundary."""
    r = run(shape_key)
    assert r.towers, f"no towers packed on {shape_key}"
    plot = r.reservation.envelope.plot
    for t in r.towers:
        assert plot.contains(t.polygon), f"{t.name} escapes the plot on {shape_key}"


@pytest.mark.parametrize("shape_key", list(SHAPES))
def test_every_tower_respects_the_setback_envelope(shape_key):
    r = run(shape_key)
    envelope = r.reservation.envelope.envelope.buffer(1e-6)
    for t in r.towers:
        assert envelope.contains(t.polygon), f"{t.name} breaches the setback"


@pytest.mark.parametrize("shape_key", list(SHAPES))
def test_towers_never_overlap_roads_or_amenities(shape_key):
    r = run(shape_key)
    reserved = r.reservation.roads
    for a in r.reservation.amenities:
        reserved = reserved.union(a.polygon)
    for t in r.towers:
        assert t.polygon.intersection(reserved).area == pytest.approx(0.0, abs=1e-6), \
            f"{t.name} overlaps reserved land"


@pytest.mark.parametrize("shape_key", list(SHAPES))
def test_towers_never_overlap_each_other(shape_key):
    r = run(shape_key)
    for i, a in enumerate(r.towers):
        for b in r.towers[i + 1:]:
            assert a.polygon.intersection(b.polygon).area == pytest.approx(0.0, abs=1e-6)


def test_bounding_box_placement_would_have_escaped():
    """Guards the premise: on a triangle the bbox centre is outside the plot, which is
    why the old grid-over-bbox layout produced towers beyond the boundary."""
    from shapely.geometry import Point
    r = run("triangle")
    # Use the engine's own plot geometry: it re-projects about the polygon centroid, so a
    # Polygon built from the raw test coordinates would be in a different frame.
    tri = r.reservation.envelope.plot
    minx, miny, maxx, maxy = tri.bounds
    assert not tri.contains(Point(minx + 20, maxy - 20))   # bbox corner cell, outside
    for t in r.towers:
        assert tri.buffer(1e-6).contains(t.polygon)


# ---------------------------------------------------------------- spacing / caps
def test_inter_tower_spacing_scales_with_height():
    cfg = conf()
    r = run("rect")
    for i, a in enumerate(r.towers):
        for b in r.towers[i + 1:]:
            need = required_spacing(a, b, cfg)
            assert a.polygon.distance(b.polygon) >= need - 0.05, \
                f"{a.name}/{b.name} closer than the light-and-ventilation gap"


def test_taller_towers_demand_a_wider_gap():
    tall = run("rect", towers={"floors_min": 20, "floors_max": 20})
    short = run("rect", towers={"floors_min": 4, "floors_max": 4})

    def min_gap(res):
        return min((a.polygon.distance(b.polygon)
                    for i, a in enumerate(res.towers) for b in res.towers[i + 1:]),
                   default=float("inf"))

    assert min_gap(tall) > min_gap(short)


def test_far_cap_is_respected():
    r = run("rect", far_cap=1.5)
    assert r.to_dict()["layout_metrics"]["achieved_far"] <= 1.5 + 1e-6


def test_lower_far_cap_yields_less_buildable_area():
    high = run("rect", far_cap=3.0).to_dict()["layout_metrics"]
    low = run("rect", far_cap=1.2).to_dict()["layout_metrics"]
    assert low["total_buildable_area_sqm"] < high["total_buildable_area_sqm"]
    assert low["achieved_far"] <= 1.2 + 1e-6


def test_ground_coverage_cap_is_respected():
    r = run("rect", ground_coverage_cap_pct=12.0)
    assert r.to_dict()["layout_metrics"]["ground_coverage_pct"] <= 12.0 + 1e-6


def test_far_cap_trims_floors_before_deleting_towers():
    """Losing a storey everywhere beats losing a whole building."""
    generous = run("rect", far_cap=3.0)
    tight = run("rect", far_cap=2.0)
    assert len(tight.towers) >= len(generous.towers) - 1
    assert max(t.floors for t in tight.towers) <= max(t.floors for t in generous.towers)


# ---------------------------------------------------------------- fitness agreement
def test_greedy_layout_is_reported_feasible():
    for key in SHAPES:
        r = run(key)
        assert r.fitness.feasible, f"{key}: {r.fitness.hard_violations}"
        assert not r.fitness.hard_violations


def test_fitness_rejects_a_tower_pushed_outside_the_region():
    from shapely.affinity import translate
    r = run("rect")
    ctx = PackContext(region=r.reservation.residual, roads=r.reservation.roads,
                      plot_area=r.reservation.envelope.plot.area, cfg=conf())
    assert evaluate(r.towers, ctx).feasible

    rogue = list(r.towers)
    rogue[0].polygon = translate(rogue[0].polygon, 10_000, 10_000)
    bad = evaluate(rogue, ctx)
    assert not bad.feasible and bad.score == float("-inf")
    assert any("outside" in v for v in bad.hard_violations)


def test_fitness_rejects_overlapping_towers():
    r = run("rect")
    ctx = PackContext(region=r.reservation.residual, roads=r.reservation.roads,
                      plot_area=r.reservation.envelope.plot.area, cfg=conf())
    clashing = list(r.towers)
    clashing[1].polygon = clashing[0].polygon
    result = evaluate(clashing, ctx)
    assert not result.feasible
    assert any("overlap" in v for v in result.hard_violations)


# ---------------------------------------------------------------- rotation
def test_rotation_is_swept_and_reported():
    r = run("irregular")
    assert all(0.0 <= t.rotation_deg < 180.0 for t in r.towers)
    # Every tower in one region shares the grid's orientation.
    assert len({round(t.rotation_deg, 2) for t in r.towers}) <= len(polygons_of(r.reservation.residual))


def test_tower_polygon_matches_its_declared_size_and_rotation():
    r = run("rect")
    for t in r.towers:
        assert t.polygon.area == pytest.approx(t.width * t.depth, rel=1e-6)
        ring = list(t.polygon.exterior.coords[:-1])
        edge = math.dist(ring[0], ring[1])
        assert edge == pytest.approx(t.width, rel=1e-6) or edge == pytest.approx(t.depth, rel=1e-6)


# ---------------------------------------------------------------- payload
def test_plan_site_payload_carries_towers_and_metrics():
    project = {"plot": {"coordinates": to_latlng(SHAPES["L"]),
                        "road_edges": [{"edge_index": 0, "width": 12}]}}
    out = plan_site(project, {"setbacks": {"default": 5, "front": 9}, "fast_preview": True})

    assert out["ok"] is True and out["stage"] == "layout"
    assert out["envelope"]["area_sqm"] > 0        # stage 1 still there
    assert out["roads"]["ring_area_sqm"] > 0      # stage 2 still there
    assert out["towers"]

    t = out["towers"][0]
    for key in ("name", "polygons", "polygons_local", "centre_local", "centre_latlng",
                "width_m", "depth_m", "rotation_deg", "floors", "height_m",
                "footprint_sqm", "floor_area_sqm", "units"):
        assert key in t, f"missing {key}"
    assert len(t["polygons_local"][0][0]) >= 4

    m = out["layout_metrics"]
    assert m["tower_count"] == len(out["towers"])
    assert m["feasible"] is True
    assert 0 < m["ground_coverage_pct"] < 100
    assert 0 < m["open_space_pct"] < 100
    assert m["unit_count"] > 0


def test_plan_site_surfaces_errors_rather_than_raising():
    assert plan_site({"plot": {"coordinates": []}})["error"]["code"] == "no_polygon"
    out = plan_site({"plot": {"coordinates": to_latlng([(0, 0), (15, 0), (15, 15), (0, 15)])}},
                    {"setbacks": {"default": 9}})
    assert out["ok"] is False and out["error"]["code"] == "envelope_collapsed"


def test_no_room_for_towers_is_reported_not_silent():
    """A plot big enough to survive setbacks but too small for any configured footprint."""
    out = plan_site({"plot": {"coordinates": to_latlng([(0, 0), (38, 0), (38, 34), (0, 34)])}},
                    {"setbacks": {"default": 4}, "amenities": {"enabled": False},
                     "fast_preview": True})
    assert out["ok"] is True
    assert out["layout_metrics"]["tower_count"] == 0
    assert any("No tower footprint fits" in w for w in out["warnings"])


def test_fast_preview_searches_less_but_stays_valid():
    """Preview coarsens the floor sweep. Asserting wall-clock here is flaky on a loaded
    machine, so assert the search-space reduction and the quality floor instead."""
    full = plan(to_latlng(SHAPES["rect"]), [], conf(fast_preview=False))
    prev = plan(to_latlng(SHAPES["rect"]), [], conf(fast_preview=True))

    c_full, c_prev = conf(fast_preview=False), conf(fast_preview=True)
    span = c_full.towers.floors_max - c_full.towers.floors_min + 1
    assert len(range(c_prev.towers.floors_max, c_prev.towers.floors_min - 1, -2)) < span

    assert prev.fitness.feasible and full.fitness.feasible
    assert prev.fitness.score >= full.fitness.score * 0.85


# ---------------------------------------------------------------- cross-region spacing
def test_spacing_holds_across_separate_packable_regions():
    """Regions are packed independently, so two towers facing each other across a
    driveway are the case a per-region grid pitch cannot see."""
    cfg = conf()
    for key in SHAPES:
        r = run(key)
        assert len(polygons_of(r.reservation.residual)) >= 1
        for i, a in enumerate(r.towers):
            for b in r.towers[i + 1:]:
                need = required_spacing(a, b, cfg)
                assert a.polygon.distance(b.polygon) >= need - 0.05,                     f"{key}: {a.name}/{b.name} gap {a.polygon.distance(b.polygon):.2f} < {need:.2f}"


def test_spacing_fix_is_reported_in_warnings():
    r = run("rect")
    if any(t.floors < conf().towers.floors_max for t in r.towers):
        assert any("light-and-ventilation" in w or "FAR" in w for w in r.warnings)


# ---------------------------------------------------------------- tower cap
def test_max_towers_caps_the_layout():
    # Bar footprints are much larger than the old square ones, so fewer fit on `rect`.
    # Use the bigger L-shape so the cap has something to actually bite on.
    uncapped = run("L")
    capped = run("L", towers={"max_towers": 4})
    assert len(uncapped.towers) > 4, f"only {len(uncapped.towers)} bars fit — cap cannot bite"
    assert len(capped.towers) == 4
    assert any("Capped the layout at 4" in w for w in capped.warnings)


def test_max_towers_keeps_the_highest_yield_towers():
    capped = run("L", towers={"max_towers": 3})
    uncapped = run("L")
    smallest_kept = min(t.floor_area for t in capped.towers)
    dropped = sorted(t.floor_area for t in uncapped.towers)[:len(uncapped.towers) - 3]
    assert all(d <= smallest_kept + 1e-6 for d in dropped)


def test_max_towers_above_what_fits_changes_nothing():
    a = run("L")
    b = run("L", towers={"max_towers": 99})
    assert len(a.towers) == len(b.towers)
    assert not any("Capped" in w for w in b.warnings)


def test_max_towers_is_reflected_in_the_payload():
    out = plan_site({"plot": {"coordinates": to_latlng(SHAPES["rect"])}},
                    {"setbacks": {"default": 5}, "fast_preview": True,
                     "towers": {"max_towers": 5}})
    assert out["layout_metrics"]["tower_count"] == 5
    assert out["config"]["towers"]["max_towers"] == 5


# ---------------------------------------------------------------- bar typology
def test_footprints_are_bars_not_boxes():
    """Square plates leave habitable rooms with no external wall, and they are what made
    the generated site read as office boxes rather than housing."""
    cfg = conf()
    for key in SHAPES:
        r = run(key)
        for t in r.towers:
            aspect = max(t.width, t.depth) / min(t.width, t.depth)
            assert cfg.towers.min_aspect <= aspect <= cfg.towers.max_aspect,                 f"{key}: {t.name} is {t.width} x {t.depth} (aspect {aspect:.1f})"


def test_aspect_band_is_configurable():
    r = run("L", towers={"min_aspect": 1.0, "max_aspect": 1.6,
                         "candidate_widths": [24.0, 30.0], "candidate_depths": [20.0, 24.0],
                         "min_footprint": 400.0})
    assert r.towers
    for t in r.towers:
        assert max(t.width, t.depth) / min(t.width, t.depth) <= 1.6


# ---------------------------------------------------------------- surface parking
def test_surface_bays_are_generated_along_the_roads():
    r = run("rect")
    assert r.bays, "no surface parking generated"
    roads = r.reservation.roads
    for b in r.bays:
        assert b.polygon.distance(roads) < 1.5, "a bay is stranded away from the carriageway"


def test_surface_bays_never_overlap_buildings_or_each_other():
    r = run("L")
    blocks = [t.polygon for t in r.towers] + [a.polygon for a in r.reservation.amenities]
    for b in r.bays:
        for g in blocks:
            assert b.polygon.intersection(g).area == pytest.approx(0.0, abs=1e-6)
    for i, b in enumerate(r.bays):
        for c in r.bays[i + 1:]:
            assert b.polygon.intersection(c.polygon).area == pytest.approx(0.0, abs=1e-6)


def test_bays_stay_inside_the_envelope():
    r = run("irregular")
    guard = r.reservation.envelope.envelope.buffer(1e-6)
    for b in r.bays:
        assert guard.contains(b.polygon)


def test_bay_dimensions_match_the_config():
    cfg = conf()
    r = run("rect")
    expected = cfg.parking.stall_width * cfg.parking.stall_depth
    for b in r.bays[:20]:
        assert b.polygon.area == pytest.approx(expected, rel=1e-6)


def test_surface_parking_can_be_disabled():
    r = run("rect", parking={"enabled": False})
    assert r.bays == []


def test_bay_count_is_reported_in_the_payload():
    out = plan_site({"plot": {"coordinates": to_latlng(SHAPES["rect"])}},
                    {"setbacks": {"default": 5}, "fast_preview": True})
    assert out["layout_metrics"]["surface_bays"] == out["surface_parking"]["bay_count"] > 0
    assert out["surface_parking"]["polygons"]
