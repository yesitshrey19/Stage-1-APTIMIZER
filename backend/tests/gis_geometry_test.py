"""GIS feature geometry: areas vs centrelines, and the flood reason's wording.

A lake is a closed ring; a river, stream or canal is an open centreline, and drawing
both as an area is what made the map paint a filled "water body" across a built-up plot.
Drainage and sewage channels are not water at all and must not reach the map, the counts
or the flood score. These tests pin both rules.

Offline: nothing here touches the network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "aptimizer_test_gis_geometry")

import gis as gislib  # noqa: E402


# A ~150 x 130 m plot with a stream running through it — the real-world shape of the bug:
# the channel is ~1.1 km long, passes through the plot, and both its ends sit far outside.
PLOT = [[12.9826, 77.6198], [12.9826, 77.6212], [12.9814, 77.6212], [12.9814, 77.6198]]
STREAM = [{"lat": 12.9871, "lon": 77.6202}, {"lat": 12.9820, "lon": 77.6205},
          {"lat": 12.9774, "lon": 77.6199}]
POND = [{"lat": 12.9830, "lon": 77.6190}, {"lat": 12.9830, "lon": 77.6195},
        {"lat": 12.9828, "lon": 77.6195}, {"lat": 12.9828, "lon": 77.6190},
        {"lat": 12.9830, "lon": 77.6190}]


def _payload():
    return {"elements": [
        {"type": "way", "id": 1, "tags": {"waterway": "stream"}, "geometry": STREAM},
        {"type": "way", "id": 2, "tags": {"natural": "water"}, "geometry": POND},
        {"type": "way", "id": 3, "tags": {"building": "yes"}, "geometry": POND},
        {"type": "node", "id": 4, "lat": 12.9820, "lon": 77.6205,
         "tags": {"highway": "bus_stop"}},
        # Infrastructure, not water: a storm drain through the plot, and a sewage pond
        # tagged as water. Neither belongs on the water layer or in the flood score.
        {"type": "way", "id": 5, "tags": {"waterway": "drain"}, "geometry": STREAM},
        {"type": "way", "id": 6, "tags": {"natural": "water", "water": "wastewater"},
         "geometry": POND},
    ]}


# --------------------------------------------------------------- geometry role

def test_streams_are_lines_and_lakes_are_areas():
    assert gislib._geometry_role({"waterway": "stream"}) == "line"
    assert gislib._geometry_role({"waterway": "river"}) == "line"
    assert gislib._geometry_role({"waterway": "canal"}) == "line"
    assert gislib._geometry_role({"natural": "water"}) == "area"
    assert gislib._geometry_role({"natural": "wetland"}) == "area"
    assert gislib._geometry_role({"building": "yes"}) == "area"
    assert gislib._geometry_role({"highway": "residential"}) == "line"
    assert gislib._geometry_role({"highway": "bus_stop"}, "node") == "point"


def test_drains_and_sewage_are_never_classified_as_water():
    for tags in ({"waterway": "drain"}, {"waterway": "ditch"},
                 {"waterway": "sewage"}, {"waterway": "wastewater"},
                 {"natural": "water", "water": "wastewater"},
                 {"natural": "water", "water": "sewage"}):
        assert gislib._classify(tags) is None, tags
    # ...while natural courses on the same tag key stay water.
    for tags in ({"waterway": "river"}, {"waterway": "stream"}, {"waterway": "canal"},
                 {"natural": "water"}, {"natural": "water", "water": "pond"}):
        assert gislib._classify(tags) == "water", tags


def test_parse_overpass_marks_each_feature_with_its_role():
    out = gislib._parse_overpass(_payload(), PLOT, 500)
    by_id = {f["id"]: f for f in out["water"] + out["buildings"] + out["transit"]}
    assert by_id["1"]["geom"] == "line", "a stream is a centreline, not a ring"
    assert by_id["2"]["geom"] == "area", "a lake is a closed ring"
    assert by_id["3"]["geom"] == "area"
    assert by_id["4"]["geom"] == "point"


def test_parse_overpass_drops_drains_and_sewage_entirely():
    out = gislib._parse_overpass(_payload(), PLOT, 500)
    present = {f["id"] for cat in out.values() for f in cat}
    assert "5" not in present, "a storm drain must not reach the map or the flood score"
    assert "6" not in present, "a sewage pond must not reach the map or the flood score"
    assert not any(f["kind"] == "drain" for f in out["water"])


def test_stream_through_the_plot_keeps_its_full_extent():
    """The centreline must still describe the whole channel, so the map draws a line
    across the plot instead of a blob closed around it."""
    out = gislib._parse_overpass(_payload(), PLOT, 500)
    stream = next(f for f in out["water"] if f["geom"] == "line")
    assert stream["geometry"][0] == [STREAM[0]["lat"], STREAM[0]["lon"]]
    assert stream["geometry"][-1] == [STREAM[-1]["lat"], STREAM[-1]["lon"]]
    assert stream["distance_m"] == 0.0 and stream["on_plot"] is True


# --------------------------------------------------------------- geometry cap

def _ring(n):
    """A closed ring of n points on a small circle."""
    import math
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / (n - 1)
        pts.append([12.982 + 0.001 * math.sin(a), 77.620 + 0.001 * math.cos(a)])
    pts[-1] = pts[0]
    return pts


def test_cap_keeps_rings_closed_and_lines_ending_where_they_end():
    ring = _ring(400)
    capped = gislib._cap_geometry(ring, "area", limit=120)
    assert len(capped) <= 120
    assert capped[0] == capped[-1], "a decimated ring must stay closed"
    assert min(p[0] for p in capped) >= min(p[0] for p in ring) - 1e-9
    assert max(p[1] for p in capped) <= max(p[1] for p in ring) + 1e-9

    line = [[12.982 + 0.0001 * i, 77.610 + 0.0001 * i] for i in range(400)]
    capped_line = gislib._cap_geometry(line, "line", limit=120)
    assert len(capped_line) <= 120
    assert capped_line[0] == line[0] and capped_line[-1] == line[-1], \
        "a decimated centreline must still span the channel it came from"


def test_cap_leaves_short_geometry_untouched():
    short = [[g["lat"], g["lon"]] for g in STREAM]
    assert gislib._cap_geometry(short, "line", limit=240) is short


# --------------------------------------------------------------- flood wording

# --------------------------------------------------------------- staleness

def _current(coords=None):
    coords = coords if coords is not None else PLOT
    return {"polygon_signature": gislib._signature(coords),
            "rules_version": gislib.ANALYSIS_RULES_VERSION}


def test_a_current_analysis_is_not_stale():
    assert gislib.staleness(_current(), PLOT) is None
    assert gislib.staleness(None, PLOT) is None, "nothing stored is not a stale analysis"


def test_a_moved_boundary_is_reported_as_polygon():
    moved = [[lat + 0.0002, lng] for lat, lng in PLOT]
    assert gislib.staleness(_current(), moved) == "polygon"


def test_an_analysis_from_older_rules_is_reported_as_rules():
    stored = {**_current(), "rules_version": gislib.ANALYSIS_RULES_VERSION - 1}
    assert gislib.staleness(stored, PLOT) == "rules"


def test_an_analysis_carrying_no_version_is_treated_as_stale():
    """Everything written before the stamp existed has no version, and nobody knows which
    rules produced it — so it must not be presented as current."""
    assert gislib.staleness({"polygon_signature": gislib._signature(PLOT)}, PLOT) == "rules"


def test_a_moved_boundary_wins_over_an_old_rule_set():
    """Both are true here; the boundary is the one that stops the geometry matching at
    all, so it is the reason the user needs to act on."""
    stored = {"polygon_signature": "something else", "rules_version": 0}
    assert gislib.staleness(stored, PLOT) == "polygon"


def test_data_health_uses_the_same_staleness_rule_as_the_gis_module():
    """The reliability report and the GIS screen must not disagree about one artefact.
    This check used to look only at the boundary, so a rule change left it reporting a
    GIS analysis as fresh while the GIS module flagged the same one as out of date."""
    import datahealth

    def entry(rules_version):
        project = {
            "plot": {"coordinates": PLOT},
            "gis": {"polygon_signature": gislib._signature(PLOT),
                    "rules_version": rules_version},
        }
        return next(f for f in datahealth._freshness(project) if f["key"] == "gis")

    assert entry(gislib.ANALYSIS_RULES_VERSION)["state"] == "fresh"
    old = entry(gislib.ANALYSIS_RULES_VERSION - 1)
    assert old["state"] == "stale"
    assert "rules" in old["detail"].lower()


def test_flood_reason_names_the_feature_that_was_found():
    f = gislib.flood_risk({"available": False}, [{"distance_m": 0.0, "kind": "stream", "geom": "line"}])
    assert "Stream" in f["reasons"][0]
    assert f["nearest_water_label"] == "Stream"
    assert f["nearest_water_kind"] == "stream"

    g = gislib.flood_risk({"available": False}, [{"distance_m": 12.0, "kind": "water", "geom": "area"}])
    assert g["reasons"][0].startswith("Water body within 12 m")

    h = gislib.flood_risk({"available": False}, [])
    assert h["nearest_water_m"] is None and h["nearest_water_label"] is None
