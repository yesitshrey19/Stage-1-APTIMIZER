"""Site layout engine — Stage 2 (road ring, driveways, amenity reservation).

    pytest backend/tests/siteplan_reserve_test.py -v
"""
import math
import os
import sys

import pytest
from shapely.geometry import Point

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siteplan import (AmenityBlock, LayoutError, SiteLayoutConfig, build_envelope,  # noqa: E402
                      reserve, reserve_from_coordinates, reserve_site, resolve_amenity_size)
from siteplan.frame import LocalFrame, polygons_of  # noqa: E402

LAT0, LNG0 = 12.9716, 77.5946
FRAME = LocalFrame(LAT0, LNG0)


def to_latlng(points):
    return [FRAME.to_latlng(x, y) for x, y in points]


def box_pts(w, h, x0=0.0, y0=0.0):
    return [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]


def conf(**over):
    """Config with a small setback so tests focus on stage 2, not stage 1."""
    base = {"setbacks": {"default": 5}}
    base.update(over)
    return SiteLayoutConfig.from_dict(base)


def run(shape, roads=None, **over):
    return reserve_from_coordinates(to_latlng(shape), roads or [], conf(**over))


# ---------------------------------------------------------------- perimeter ring
def test_ring_is_an_annulus_of_the_configured_width():
    # Amenities off: an amenity spanning a full edge of the core would shift its bounds.
    r = run(box_pts(120, 90), amenities={"enabled": False})
    ring_w = r.envelope.config.road.ring_width
    # The core is inset from the envelope by exactly the ring width.
    env_b = r.envelope.envelope.bounds
    core_b = r.residual.bounds
    assert core_b[0] - env_b[0] == pytest.approx(ring_w, abs=0.3)
    assert env_b[2] - core_b[2] == pytest.approx(ring_w, abs=0.3)
    assert not r.ring.is_empty


def test_ring_and_residual_do_not_overlap():
    r = run(box_pts(120, 90))
    assert r.ring.intersection(r.residual).area == pytest.approx(0.0, abs=1e-6)


def test_ring_plus_core_accounts_for_the_whole_envelope():
    """Every square metre of the envelope is ring, driveway, green or packable land —
    apart from the deliberate clearance ring held around the green, which belongs to
    neither it nor the packable region. So this is a tight bound, not an equality."""
    r = run(box_pts(120, 90), amenities={"enabled": False})
    rebuilt = r.ring.union(r.residual).union(r.driveways).union(r.green)
    envelope = r.envelope.envelope.area

    assert rebuilt.area <= envelope + 1.0, "reserved areas exceed the envelope"
    assert rebuilt.area >= envelope * 0.94, "too much land is unaccounted for"

    slack = envelope - rebuilt.area
    clearance = r.envelope.config.open_space.clearance
    ring_area = r.green.buffer(clearance).area - r.green.area if not r.green.is_empty else 0.0
    assert slack == pytest.approx(ring_area, rel=0.05), \
        f"{slack:.0f} m2 unaccounted for, but the green clearance ring is only {ring_area:.0f} m2"


def test_ring_disabled_leaves_the_whole_envelope_packable():
    r = run(box_pts(100, 80), road={"enabled": False}, amenities={"enabled": False},
            open_space={"enabled": False})
    assert r.ring.is_empty and r.driveways.is_empty and r.green.is_empty
    assert r.residual.area == pytest.approx(r.envelope.envelope.area, rel=1e-6)


def test_community_green_is_reserved_and_survives_packing():
    """Reserved before towers so it cannot be quietly built over."""
    r = run(box_pts(200, 150), amenities={"enabled": False})
    assert not r.green.is_empty
    assert r.green.area >= r.envelope.config.open_space.min_area
    assert r.green.intersection(r.residual).area == pytest.approx(0.0, abs=1e-6)
    assert r.envelope.envelope.buffer(1e-6).contains(r.green)


def test_green_can_be_disabled():
    r = run(box_pts(200, 150), open_space={"enabled": False})
    assert r.green.is_empty


def test_envelope_narrower_than_the_ring_warns_and_leaves_nothing_packable():
    env = build_envelope(to_latlng(box_pts(22, 22)), [],
                         SiteLayoutConfig.from_dict({"setbacks": {"default": 5}}))
    r = reserve(env, env.config)
    assert r.residual.is_empty
    assert any("narrower than" in w for w in r.warnings)


def test_a_core_too_small_to_use_says_so_instead_of_returning_an_empty_area():
    """The ring fits, so nothing upstream objects, and what it leaves inside is below the
    minimum usable region and is dropped. An empty packable area whose every warning points
    somewhere else reads as a packer that failed; the truth is a site that never had room
    for one, and that has to travel with the result."""
    env = build_envelope(to_latlng(box_pts(26, 26)), [],
                         SiteLayoutConfig.from_dict({"setbacks": {"default": 5}}))
    r = reserve(env, env.config)
    assert r.residual.is_empty
    assert any("nothing remains for towers" in w for w in r.warnings)


# ---------------------------------------------------------------- driveways
def test_deep_core_gets_driveways_so_nothing_is_stranded():
    reach = 30.0
    r = run(box_pts(260, 200), road={"max_distance_to_road": reach},
            amenities={"enabled": False})
    assert not r.driveways.is_empty

    # Residual parts are bounded only by the ring and the driveways, so "every point is
    # within `reach` of a road" is exactly "eroding the part by `reach` empties it".
    # That is the real reachability property, not a spot check on one interior point.
    for part in polygons_of(r.residual):
        assert part.buffer(-reach).is_empty, (
            f"a {part.area:.0f} m2 pocket sits more than {reach} m from any road")


def test_shallow_core_needs_no_driveways():
    # central_spine_min_core is raised out of the way so this measures the reach rule
    # alone; the rule that gives a deep core a spine regardless has its own test below.
    r = run(box_pts(90, 70), road={"max_distance_to_road": 60.0,
                                   "central_spine_min_core": 10_000.0},
            amenities={"enabled": False})
    assert r.driveways.is_empty


def test_a_core_deep_enough_for_two_rows_of_flats_gets_a_spine_within_reach_anyway():
    """Reach is not the only reason to cut a drive. A core deep enough to hold two rows of
    flats back to back needs one down the middle whatever the distance rule says, or the
    flats on the far side are served by nothing."""
    r = run(box_pts(90, 70), road={"max_distance_to_road": 60.0},
            amenities={"enabled": False})
    assert not r.driveways.is_empty


def test_driveways_stay_inside_the_core_and_off_the_residual():
    r = run(box_pts(240, 180), road={"max_distance_to_road": 25.0},
            amenities={"enabled": False})
    assert r.envelope.envelope.buffer(1e-6).contains(r.driveways)
    assert r.driveways.intersection(r.residual).area == pytest.approx(0.0, abs=1e-6)


def test_driveway_width_is_honoured():
    width = 7.0
    r = run(box_pts(300, 220), road={"max_distance_to_road": 30.0, "driveway_width": width},
            amenities={"enabled": False})
    part = polygons_of(r.driveways)[0]
    # A spine clipped to a rectangular core is itself a rectangle, so w and L are the two
    # roots of x^2 - (P/2)x + A = 0. Take the smaller root as the width.
    half_p, area = part.length / 2.0, part.area
    disc = half_p ** 2 - 4 * area
    assert disc >= 0
    assert (half_p - math.sqrt(disc)) / 2.0 == pytest.approx(width, rel=0.02)


# ---------------------------------------------------------------- amenity sizing
def test_explicit_dimensions_win_over_percentage():
    block = AmenityBlock("club", "Clubhouse", dimensions=[30, 20], plot_area_pct=50)
    w, d, area = resolve_amenity_size(block, plot_area=10000)
    assert (w, d, area) == (30.0, 20.0, 600.0)


def test_explicit_area_wins_over_percentage():
    block = AmenityBlock("club", "Clubhouse", area_sqm=400, plot_area_pct=50, aspect=1.0)
    w, d, area = resolve_amenity_size(block, plot_area=10000)
    assert area == 400.0
    assert w == pytest.approx(20.0) and d == pytest.approx(20.0)


def test_percentage_of_plot_area_sizing():
    block = AmenityBlock("club", "Clubhouse", plot_area_pct=2.0, aspect=1.0)
    w, d, area = resolve_amenity_size(block, plot_area=10000)
    assert area == pytest.approx(200.0)
    assert w == pytest.approx(math.sqrt(200.0))


def test_block_with_no_sizing_is_reported_not_crashed():
    r = run(box_pts(160, 120), amenities={"blocks": [
        {"key": "ghost", "name": "Mystery Block"},
    ]})
    assert r.amenities == []
    assert any("no size" in w for w in r.warnings)


# ---------------------------------------------------------------- amenity placement
# Three separate blocks, for the tests that are about how blocks relate to each other.
# The shipped default is one integrated clubhouse, which cannot exhibit a pairwise gap.
THREE_BLOCKS = [
    {"key": "club", "name": "Clubhouse", "plot_area_pct": 1.5, "height_m": 7.5, "floors": 2},
    {"key": "pool", "name": "Pool", "plot_area_pct": 1.0, "height_m": 1.5},
    {"key": "court", "name": "Sports Court", "plot_area_pct": 1.0, "height_m": 3.0},
]


def test_amenities_are_placed_inside_the_envelope_and_clear_of_roads():
    r = run(box_pts(200, 150))
    assert len(r.amenities) == len(r.envelope.config.amenities.blocks)
    for a in r.amenities:
        assert r.envelope.envelope.buffer(1e-6).contains(a.polygon)
        assert a.polygon.intersection(r.roads).area == pytest.approx(0.0, abs=1e-6)


def test_amenities_do_not_overlap_each_other():
    r = run(box_pts(200, 150))
    for i, a in enumerate(r.amenities):
        for b in r.amenities[i + 1:]:
            assert a.polygon.intersection(b.polygon).area == pytest.approx(0.0, abs=1e-6)


def test_amenities_are_subtracted_from_the_residual():
    r = run(box_pts(200, 150))
    for a in r.amenities:
        assert a.polygon.intersection(r.residual).area == pytest.approx(0.0, abs=1e-6)


def test_amenity_footprints_respect_the_total_cap():
    r = run(box_pts(200, 150), amenities={"total_cap_pct": 3.0, "blocks": [
        {"key": "a", "name": "Huge A", "plot_area_pct": 6.0},
        {"key": "b", "name": "Huge B", "plot_area_pct": 6.0},
    ]})
    total = sum(a.area_sqm for a in r.amenities)
    assert total <= r.envelope.plot.area * 0.03 + 1.0
    assert any("cap" in w for w in r.warnings)


def test_amenity_that_cannot_fit_is_reported_not_dropped_silently():
    r = run(box_pts(70, 60), amenities={"total_cap_pct": 100.0, "blocks": [
        {"key": "mega", "name": "Mega Hall", "dimensions": [200, 90]},
    ]})
    assert r.amenities == []
    assert any("does not fit" in w for w in r.warnings)


def test_amenities_carry_their_own_height_not_a_tower_height():
    """An amenity is never part of a tower and never takes a tower's height. The shipped
    clubhouse stacks its facilities into four storeys on one footprint rather than spending
    three footprints on three pavilions, so "low-rise" here means low against the towers,
    not single-storey -- and the number still comes off the block, not the tower."""
    r = run(box_pts(200, 150))
    placed = {a.key: a for a in r.amenities}
    spec = {b.key: b for b in r.envelope.config.amenities.blocks}
    for key, block in spec.items():
        assert placed[key].height_m == block.height_m
        assert placed[key].floors == block.floors

    club = placed["clubhouse"]
    assert club.floors == 4 and club.height_m == 14.0
    # An absolute ceiling rather than a comparison against the towers: the shortest tower
    # the packer will build is floors_min x floor_height = 12 m, so "shorter than a tower"
    # is not true of a four-storey clubhouse and asserting it would only encode a wrong
    # idea of what low-rise means here. What must hold is that it stays a low building.
    assert club.height_m <= 15.0 and club.floors <= 4


def test_separately_configured_amenities_keep_their_own_heights():
    r = run(box_pts(200, 150), amenities={"blocks": THREE_BLOCKS})
    by_key = {a.key: a for a in r.amenities}
    assert by_key["club"].height_m == 7.5 and by_key["club"].floors == 2
    assert by_key["pool"].height_m == 1.5
    assert all(a.height_m <= 8 for a in r.amenities)


def _min_pairwise_gap(result):
    polys = [a.polygon for a in result.amenities]
    return min(a.distance(b) for i, a in enumerate(polys) for b in polys[i + 1:])


def test_spread_term_disperses_amenities_instead_of_clustering():
    """With the spread weight zeroed the blocks huddle; with it on they separate."""
    clustered = run(box_pts(220, 160), amenities={"blocks": THREE_BLOCKS, "spread_weight": 0.0,
                                                  "compactness_weight": 0.85})
    dispersed = run(box_pts(220, 160), amenities={"blocks": THREE_BLOCKS})
    assert len(clustered.amenities) == len(dispersed.amenities) == 3
    assert _min_pairwise_gap(dispersed) > _min_pairwise_gap(clustered)


def test_dispersed_amenities_are_a_meaningful_distance_apart():
    r = run(box_pts(220, 160), amenities={"blocks": THREE_BLOCKS})
    # Auto target separation is 0.55 * sqrt(packable area); require at least half of it.
    target = math.sqrt(r.residual.area) * 0.55
    assert _min_pairwise_gap(r) >= target * 0.5


def test_explicit_target_separation_overrides_the_derived_one():
    r = run(box_pts(300, 220), amenities={"blocks": THREE_BLOCKS, "target_separation": 60.0})
    assert _min_pairwise_gap(r) >= 30.0


def test_spread_does_not_push_amenities_off_the_roads():
    """Dispersion must not win by exiling blocks into the middle of the packable land."""
    r = run(box_pts(220, 160))
    for a in r.amenities:
        assert a.polygon.distance(r.roads) <= 2.0, f"{a.name} floats away from circulation"


def test_amenities_disabled_leaves_residual_untouched():
    a = run(box_pts(200, 150), amenities={"enabled": False})
    assert a.amenities == []
    assert a.residual.area > 0


# ---------------------------------------------------------------- concave / containment
@pytest.mark.parametrize("shape", [
    box_pts(200, 150),
    [(0, 0), (200, 0), (200, 120), (100, 120), (100, 220), (0, 220)],       # big L
    [(0, 0), (220, 0), (220, 160), (150, 90), (80, 160), (0, 160)],         # notched
])
def test_everything_reserved_stays_inside_the_envelope(shape):
    r = run(shape)
    guard = r.envelope.envelope.buffer(1e-6)
    assert guard.contains(r.ring)
    assert guard.contains(r.driveways) or r.driveways.is_empty
    assert guard.contains(r.residual) or r.residual.is_empty
    for a in r.amenities:
        assert guard.contains(a.polygon)


def test_reserved_areas_sum_to_the_envelope():
    r = run(box_pts(200, 150))
    total = (r.ring.area + r.driveways.area + r.residual.area
             + sum(a.area_sqm for a in r.amenities))
    # Clearance buffers around amenities are unreserved slack, so the sum is a lower bound.
    assert total <= r.envelope.envelope.area + 1.0
    assert total >= r.envelope.envelope.area * 0.85


# ---------------------------------------------------------------- API surface
def test_reserve_site_payload_is_a_superset_of_stage_one():
    project = {"plot": {"coordinates": to_latlng(box_pts(200, 150)),
                        "road_edges": [{"edge_index": 0, "width": 12}]}}
    out = reserve_site(project, {"setbacks": {"default": 5, "front": 9}})

    assert out["ok"] is True and out["stage"] == "reserve"
    assert out["envelope"]["area_sqm"] > 0          # stage 1 fields still present
    assert out["edges"][0]["class"] == "front"

    assert out["roads"]["ring_area_sqm"] > 0
    assert out["roads"]["ring_polygons"]
    assert len(out["amenities"]) == len(SiteLayoutConfig().amenities.blocks)
    assert out["amenities"][0]["polygons"]
    assert out["residual"]["area_sqm"] > 0
    assert 0 < out["residual"]["pct_of_envelope"] < 100

    s = out["reservation_summary"]
    assert s["packable_area_sqm"] + s["road_area_sqm"] + s["amenity_area_sqm"] <= s["envelope_area_sqm"] + 1


def test_reserve_site_returns_error_dict_for_a_bad_polygon():
    out = reserve_site({"plot": {"coordinates": []}})
    assert out["ok"] is False and out["error"]["code"] == "no_polygon"


def test_reserve_site_surfaces_a_collapsed_envelope():
    out = reserve_site({"plot": {"coordinates": to_latlng(box_pts(15, 15))}},
                       {"setbacks": {"default": 9}})
    assert out["ok"] is False and out["error"]["code"] == "envelope_collapsed"
