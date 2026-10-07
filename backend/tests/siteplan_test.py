"""Site layout engine — Stage 1 (buildable envelope) unit tests.

Pure and offline: the engine is decoupled from FastAPI and Leaflet, so nothing here needs
a server or a database.

    pytest backend/tests/siteplan_test.py -v
"""
import math
import os
import sys

import pytest
from shapely.geometry import LineString, Point, Polygon

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siteplan import LayoutError, SiteLayoutConfig, build_envelope, buildable_envelope  # noqa: E402
from siteplan.envelope import classify_edges, clean_ring, signed_area  # noqa: E402
from siteplan.frame import LocalFrame, polygons_of  # noqa: E402

LAT0, LNG0 = 12.9716, 77.5946
FRAME = LocalFrame(LAT0, LNG0)


def to_latlng(points):
    """Local metres -> [lat, lng], so tests can be written in plain metres."""
    return [FRAME.to_latlng(x, y) for x, y in points]


def box(width, height, x0=0.0, y0=0.0):
    return [(x0, y0), (x0 + width, y0), (x0 + width, y0 + height), (x0, y0 + height)]


def local_polygon(result):
    """The envelope as a shapely geometry in metres."""
    return result.envelope


def cfg(**kw):
    """SiteLayoutConfig with a flat setback override, e.g. cfg(default=5, front=9)."""
    return SiteLayoutConfig.from_dict({"setbacks": kw}) if kw else SiteLayoutConfig()


# ---------------------------------------------------------------- ring hygiene
def test_clean_ring_drops_duplicates_and_closing_vertex():
    ring, idx = clean_ring([(0, 0), (0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    assert ring == [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert idx == [0, 2, 3, 4]


def test_signed_area_sign_follows_winding():
    ccw = box(10, 10)
    assert signed_area(ccw) == pytest.approx(100.0)
    assert signed_area(list(reversed(ccw))) == pytest.approx(-100.0)


# ---------------------------------------------------------------- basic inset
def test_square_uniform_setback_area_and_shape():
    c = cfg(default=6)
    result = build_envelope(to_latlng(box(60, 40)), [], c)
    inset = 2 * (6 + c.setback_safety)          # both sides, incl. the conservative margin
    assert result.envelope.area == pytest.approx((60 - inset) * (40 - inset), rel=1e-3)
    assert len(result.parts) == 1
    assert result.warnings == []


def test_uniform_inset_matches_negative_buffer():
    """The identity the module docstring claims: equal setbacks == polygon.buffer(-d),
    offset by the deliberate `setback_safety` margin."""
    c = cfg(default=7)
    plot = Polygon(box(80, 55))
    result = build_envelope(to_latlng(box(80, 55)), [], c)
    assert result.envelope.area == pytest.approx(plot.buffer(-(7 + c.setback_safety)).area,
                                                 rel=1e-3)


def test_zero_setback_returns_the_plot_itself():
    # Residual is lat/lng quantisation in the round trip (to_latlng rounds to 8 dp,
    # about 1 mm), not an inset — hence rel rather than an exact compare.
    result = build_envelope(to_latlng(box(50, 50)), [], cfg(default=0))
    assert result.envelope.area == pytest.approx(2500.0, rel=1e-4)


@pytest.mark.parametrize("shape", [
    box(70, 45),                                                    # rectangle
    [(0, 0), (60, 0), (60, 30), (30, 30), (30, 60), (0, 60)],       # concave L
    [(0, 0), (50, 5), (70, 40), (35, 65), (5, 45)],                 # irregular convex-ish
    [(0, 0), (80, 0), (80, 50), (55, 25), (30, 50), (0, 50)],       # concave notch
    [(0, 0), (40, 12), (75, 4), (90, 45), (48, 38), (12, 52)],      # jagged concave
])
def test_envelope_is_always_contained_in_the_plot(shape):
    result = build_envelope(to_latlng(shape), [], cfg(default=5))
    assert result.plot.buffer(1e-9).contains(result.envelope)
    assert result.envelope.area < result.plot.area


def test_concave_notch_is_respected_not_bridged():
    """A bounding-box inset would bridge the notch; a real inset must not."""
    shape = [(0, 0), (80, 0), (80, 50), (55, 25), (30, 50), (0, 50)]
    result = build_envelope(to_latlng(shape), [], cfg(default=4))
    # Deep inside the V-notch, well outside the plot itself.
    assert not result.envelope.contains(Point(42.5, 45))
    assert not result.plot.contains(Point(42.5, 45))


def test_envelope_keeps_minimum_distance_from_every_edge():
    """The setback is a hard constraint: no slack allowed in the encroaching direction."""
    shape = [(0, 0), (60, 0), (60, 30), (30, 30), (30, 60), (0, 60)]
    setback = 5.0
    result = build_envelope(to_latlng(shape), [], cfg(default=setback))
    ring = list(result.plot.exterior.coords[:-1])
    for i in range(len(ring)):
        edge = LineString([ring[i], ring[(i + 1) % len(ring)]])
        assert edge.distance(result.envelope) >= setback


# ---------------------------------------------------------------- per-edge setbacks
def test_front_rear_side_setbacks_are_applied_per_edge():
    """Edge 0 is the south edge and is flagged road-facing, so it is the front; the
    north edge (index 2) faces the other way and must come out as the rear."""
    shape = box(80, 60)
    result = build_envelope(to_latlng(shape), [{"edge_index": 0, "width": 12}],
                            cfg(default=4, front=10, rear=7, side=4))

    by_index = {e["index"]: e for e in result.edges}
    assert by_index[0]["class"] == "front" and by_index[0]["setback_m"] == 10
    assert by_index[2]["class"] == "rear" and by_index[2]["setback_m"] == 7
    assert by_index[1]["class"] == "side" and by_index[3]["class"] == "side"

    # Compared against the plot's own bounds: the engine re-projects about the polygon
    # centroid, so absolute coordinates are frame-dependent but insets are not.
    pminx, pminy, pmaxx, pmaxy = result.plot.bounds
    minx, miny, maxx, maxy = result.envelope.bounds
    assert miny - pminy == pytest.approx(10, abs=0.1)   # front, south edge
    assert pmaxy - maxy == pytest.approx(7, abs=0.1)    # rear, north edge
    assert minx - pminx == pytest.approx(4, abs=0.1)    # side, west edge
    assert pmaxx - maxx == pytest.approx(4, abs=0.1)    # side, east edge


def test_no_road_edges_means_every_edge_is_a_side():
    result = build_envelope(to_latlng(box(40, 40)), [], cfg(default=3, front=12))
    assert {e["class"] for e in result.edges} == {"side"}
    assert all(e["setback_m"] == 3 for e in result.edges)


def test_two_opposing_front_edges_yield_no_rear():
    """Plot fronting roads on both sides — there is no single rear to pick."""
    result = build_envelope(to_latlng(box(60, 60)),
                            [{"edge_index": 0}, {"edge_index": 2}],
                            cfg(default=4, front=8, rear=15))
    classes = [e["class"] for e in result.edges]
    assert classes.count("front") == 2
    assert "rear" not in classes


def test_clockwise_ring_classifies_the_same_as_counter_clockwise():
    ccw = box(80, 60)
    cw = list(reversed(ccw))
    a = build_envelope(to_latlng(ccw), [{"edge_index": 0}], cfg(default=4, front=10, rear=7))
    # Reversed, the ring is [(0,60),(80,60),(80,0),(0,0)], so the south edge — the one
    # flagged as front in `a` — is now edge index 2, not 0.
    b = build_envelope(to_latlng(cw), [{"edge_index": 2}], cfg(default=4, front=10, rear=7))
    assert a.envelope.bounds == pytest.approx(b.envelope.bounds, abs=0.1)
    classes = {e["index"]: e["class"] for e in b.edges}
    assert classes[2] == "front" and classes[0] == "rear"


# ---------------------------------------------------------------- edge cases
def test_collapsed_envelope_raises_a_clear_error():
    with pytest.raises(LayoutError) as exc:
        build_envelope(to_latlng(box(15, 15)), [], cfg(default=9))
    assert exc.value.code == "envelope_collapsed"
    assert "setback" in exc.value.message.lower()


def test_too_few_vertices_raises():
    with pytest.raises(LayoutError) as exc:
        build_envelope(to_latlng([(0, 0), (10, 0)]), [], cfg())
    assert exc.value.code == "no_polygon"


def test_duplicate_vertices_collapsing_below_three_raises():
    pts = to_latlng([(0, 0), (0, 0), (10, 0), (10, 0)])
    with pytest.raises(LayoutError) as exc:
        build_envelope(pts, [], cfg())
    assert exc.value.code == "degenerate_polygon"


def test_self_intersecting_bowtie_is_repaired_with_a_warning():
    bowtie = [(0, 0), (60, 60), (60, 0), (0, 60)]
    result = build_envelope(to_latlng(bowtie), [], cfg(default=3))
    assert result.warnings, "repair must be reported, not silent"
    assert any("self-intersect" in w.lower() for w in result.warnings)
    assert result.plot.is_valid
    assert result.plot.buffer(1e-9).contains(result.envelope)


def test_narrow_waist_splits_the_envelope_into_multiple_regions():
    """An hourglass whose waist is thinner than twice the setback must split, not error.
    Waist is the 10 m channel between x=30 and x=40; a 6 m setback eats through it."""
    hourglass = [(0, 0), (70, 0), (40, 26), (40, 34), (70, 60), (0, 60), (30, 34), (30, 26)]
    result = build_envelope(to_latlng(hourglass), [], cfg(default=6))
    assert len(result.parts) >= 2
    assert any("disjoint" in w for w in result.warnings)
    for part in result.parts:
        assert result.plot.buffer(1e-9).contains(part)


def test_slivers_below_min_region_area_are_discarded():
    shape = [(0, 0), (60, 0), (60, 40), (0, 40)]
    tight = SiteLayoutConfig.from_dict({"setbacks": {"default": 5}, "min_region_area": 5000})
    with pytest.raises(LayoutError) as exc:
        build_envelope(to_latlng(shape), [], tight)
    assert exc.value.code == "envelope_collapsed"


def test_out_of_range_road_edge_index_is_ignored():
    result = build_envelope(to_latlng(box(50, 50)), [{"edge_index": 99}],
                            cfg(default=4, front=12))
    assert {e["class"] for e in result.edges} == {"side"}


# ---------------------------------------------------------------- config
def test_config_partial_override_keeps_other_defaults():
    c = SiteLayoutConfig.from_dict({"setbacks": {"front": 9}, "far_cap": 2.5})
    assert c.setbacks.front == 9
    assert c.setbacks.default == 6.0          # untouched
    assert c.far_cap == 2.5
    assert c.road.ring_width == 6.0           # untouched section


def test_config_ignores_unknown_keys():
    c = SiteLayoutConfig.from_dict({"setbacks": {"front": 9, "bogus": 1}, "nope": True})
    assert c.setbacks.front == 9


def test_setback_of_zero_is_not_treated_as_unset():
    c = SiteLayoutConfig.from_dict({"setbacks": {"default": 6, "front": 0}})
    assert c.setbacks.for_class("front") == 0.0
    assert c.setbacks.for_class("side") == 6.0


def test_amenity_blocks_override_parses_into_dataclasses():
    c = SiteLayoutConfig.from_dict({"amenities": {"blocks": [
        {"key": "club", "name": "Clubhouse", "area_sqm": 400},
    ]}})
    assert len(c.amenities.blocks) == 1
    assert c.amenities.blocks[0].area_sqm == 400


# ---------------------------------------------------------------- API surface
def test_buildable_envelope_returns_error_dict_instead_of_raising():
    out = buildable_envelope({"plot": {"coordinates": []}})
    assert out["ok"] is False
    assert out["error"]["code"] == "no_polygon"


def test_buildable_envelope_serialises_leaflet_ready_rings():
    project = {"plot": {"coordinates": to_latlng(box(70, 50)),
                        "road_edges": [{"edge_index": 0, "width": 12}]}}
    out = buildable_envelope(project, {"setbacks": {"default": 5, "front": 9}})

    assert out["ok"] is True
    assert out["envelope"]["part_count"] == 1
    assert out["envelope"]["area_sqm"] == pytest.approx((70 - 10) * (50 - 14), rel=1e-2)
    assert 0 < out["envelope"]["pct_of_plot"] < 100

    ring = out["envelope"]["polygons"][0][0]
    assert len(ring) >= 4
    for lat, lng in ring:
        assert abs(lat - LAT0) < 0.01 and abs(lng - LNG0) < 0.01
    assert out["config"]["setbacks"]["front"] == 9


def test_round_trip_projection_is_accurate_to_a_millimetre():
    for x, y in [(0, 0), (250, -180), (-90, 640)]:
        lat, lng = FRAME.to_latlng(x, y)
        rx, ry = FRAME.to_local(lat, lng)
        assert math.dist((x, y), (rx, ry)) < 1e-3
