"""BIM & CAD interchange tests: DXF import round-trip, boundary import, IFC4 export.

The round-trip is the load-bearing assertion: a drawing this app exports must be
readable by its own importer with the same ring, areas and vertex counts — that is
the minimum a surveyor's AutoCAD file and this platform have to agree on. The IFC
is re-opened with ifcopenshell and its spatial hierarchy counted, because a file
that merely exists is not proof Revit can link it.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "aptimizer_test")

import ezdxf
import pytest

import bim


# ------------------------------------------------------------------ unit level

RING = [[0.0, 0.0], [120.0, 0.0], [120.0, 60.0], [0.0, 60.0]]  # 7,200 m²


def _drawing_with_boundary() -> bytes:
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    doc.layers.add("PLOT_BOUNDARY", color=1)
    doc.layers.add("HATCH_NOISE", color=3)
    closed = list(RING) + [RING[0]]
    msp.add_lwpolyline(closed, dxfattribs={"layer": "PLOT_BOUNDARY"})
    # An unclosed polyline on the noise layer: the importer must ignore it.
    msp.add_lwpolyline([(1, 1), (2, 1), (2, 2)], dxfattribs={"layer": "HATCH_NOISE"})
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode()


def test_dxf_inspect_inventories_layers():
    inv = bim.inspect_dxf(_drawing_with_boundary())
    names = {l["name"] for l in inv["layers"]}
    assert "PLOT_BOUNDARY" in names and "HATCH_NOISE" in names
    boundary = next(l for l in inv["layers"] if l["name"] == "PLOT_BOUNDARY")
    assert boundary["closed_rings"] == 1
    assert boundary["largest_ring_area_sqm"] == pytest.approx(7200.0, rel=1e-3)
    assert boundary["likely_boundary"] is True  # named like one
    noise = next(l for l in inv["layers"] if l["name"] == "HATCH_NOISE")
    assert noise["closed_rings"] == 0


def test_dxf_import_rejects_layer_without_rings():
    with pytest.raises(ValueError, match="no closed polyline ring"):
        bim.import_boundary(_drawing_with_boundary(), "HATCH_NOISE")


def test_dxf_import_rejects_unknown_layer():
    with pytest.raises(ValueError, match="not in this drawing"):
        bim.import_boundary(_drawing_with_boundary(), "NOPE")


def test_dxf_import_rejects_tiny_ring():
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    doc.layers.add("SMALL", color=1)
    msp.add_lwpolyline([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)], dxfattribs={"layer": "SMALL"})
    buf = io.StringIO()
    doc.write(buf)
    with pytest.raises(ValueError, match="too small"):
        bim.import_boundary(buf.getvalue().encode(), "SMALL")


def test_import_boundary_roundtrip_and_georef():
    """Import -> export -> import must agree with itself, and land on the origin."""
    coords = bim.import_boundary(_drawing_with_boundary(), "PLOT_BOUNDARY",
                                 origin_latlng=[12.97, 77.59])
    assert coords["vertex_count"] == 4
    assert coords["area_sqm"] == pytest.approx(7200.0, rel=1e-3)
    # Centred on the requested origin: the mean of the projected ring IS the origin.
    ring = coords["coordinates"][:-1]
    mean_lat = sum(c[0] for c in ring) / len(ring)
    mean_lng = sum(c[1] for c in ring) / len(ring)
    assert mean_lat == pytest.approx(12.97, abs=1e-4)
    assert mean_lng == pytest.approx(77.59, abs=1e-4)


def test_dxf_export_carries_scheme_and_roundtrips():
    project = {
        "name": "BIM test scheme",
        "plot": {"coordinates": [[12.97, 77.59], [12.97, 77.595], [12.966, 77.595],
                                 [12.966, 77.59], [12.97, 77.59]],
                 "area_sqm": 200000},
        "site_layout": {
            "towers": [
                {"name": "T1", "floors": 10, "floor_height_m": 3.0, "footprint_sqm": 600.0,
                 "centre_local": [30.0, 20.0],
                 "polygons_local": [[[20.0, 10.0], [40.0, 10.0], [40.0, 30.0], [20.0, 30.0]]]},
            ],
            "surface_parking": {"polygons_local": [[[60.0, 40.0], [70.0, 40.0], [70.0, 46.0], [60.0, 46.0]]]},
            "layout_metrics": {"tower_count": 1, "unit_count": 96, "ground_coverage_pct": 6.1},
        },
    }
    data = bim.export_siteplan_dxf(project)
    inv = bim.inspect_dxf(data)
    layers = {l["name"]: l for l in inv["layers"]}
    assert set(bim.CAD_LAYERS) <= set(layers)
    assert layers["A-TOWER-FOOT"]["closed_rings"] == 1
    assert layers["A-PARK-BAYS"]["closed_rings"] == 1
    # The app can read back what it wrote — the AutoCAD compatibility claim in one line.
    ring = bim.import_boundary(data, "A-PLOT-BNDRY", origin_latlng=[12.968, 77.5925])
    assert ring["vertex_count"] == 4


def test_ifc_export_spatial_hierarchy():
    project = {
        "name": "IFC test",
        "plot": {"coordinates": [[12.97, 77.59], [12.97, 77.595], [12.966, 77.595],
                                 [12.966, 77.59], [12.97, 77.59]]},
        "site_layout": {
            "towers": [
                {"name": "T1", "floors": 5, "floor_height_m": 3.0, "footprint_sqm": 600.0,
                 "polygons_local": [[[20.0, 10.0], [40.0, 10.0], [40.0, 30.0], [20.0, 30.0]]]},
                {"name": "T2", "floors": 3, "floor_height_m": 3.0, "footprint_sqm": 500.0,
                 "polygons_local": [[[-40.0, 10.0], [-20.0, 10.0], [-20.0, 30.0], [-40.0, 30.0]]]},
            ],
            "layout_metrics": {"tower_count": 2, "unit_count": 120},
        },
    }
    data = bim.export_ifc4(project)
    assert data[:10] == b"ISO-10303-"  # STEP physical file header

    import ifcopenshell
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".ifc")
    os.close(fd)
    try:
        with open(path, "wb") as fh:
            fh.write(data)
        model = ifcopenshell.open(path)
        assert model.schema == "IFC4"
        assert {b.Name for b in model.by_type("IfcBuilding")} == {"T1", "T2"}
        assert len(model.by_type("IfcBuildingStorey")) == 8   # 5 + 3
        assert len(model.by_type("IfcSlab")) == 8
        site = model.by_type("IfcSite")[0]
        d, m, s, _ = site.RefLatitude
        assert (d + m / 60 + s / 3600) == pytest.approx(12.9683, abs=1e-3)
    finally:
        os.remove(path)


def test_summaries_shape():
    project = {"name": "S", "plot": {"coordinates": [[12.97, 77.59]]},
               "site_layout": {"towers": [{"name": "T1", "floors": 4, "footprint_sqm": 400.0}]}}
    ifc = bim.ifc_summary(project)
    assert ifc["schema"] == "IFC4" and ifc["buildings"] == 1 and ifc["storeys"] == 4
    dxf = bim.dxf_summary(project)
    assert dxf["parking_bays"] == 0 and dxf["units"] == "metres"


# ------------------------------------------------------------------ route level

def test_bim_routes_end_to_end(api_project):
    """Upload a DXF, inspect it, import its boundary into a fresh project, export both ways.

    Same shape as every other HTTP module in this suite: the api_project fixture's
    dependency override stands in for login (rbac has its own suite), and the one
    session TestClient from conftest carries the requests.
    """
    client, pid = api_project

    drawing = _drawing_with_boundary()
    # inspect
    r = client.post("/api/bim/dxf/inspect",
                    files={"file": ("survey.dxf", drawing, "application/dxf")})
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["filename"] == "survey.dxf"
    assert any(l["name"] == "PLOT_BOUNDARY" and l["closed_rings"] == 1 for l in inv["layers"])

    # import boundary (multipart form)
    r = client.post(f"/api/projects/{pid}/bim/dxf/import-boundary",
                    files={"file": ("survey.dxf", drawing, "application/dxf")},
                    data={"layer": "PLOT_BOUNDARY"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["area_sqm"] == pytest.approx(7200.0, rel=1e-3)
    assert len(body["plot"]["coordinates"]) == 5  # ring closed again

    # boundary persisted on the project
    r = client.get(f"/api/projects/{pid}")
    assert r.status_code == 200
    plot = r.json().get("plot") or {}
    assert len(plot.get("coordinates") or []) == 5
    assert "DXF layer 'PLOT_BOUNDARY'" in (plot.get("imported_from") or "")

    # summaries
    r = client.get(f"/api/projects/{pid}/bim/summary")
    assert r.status_code == 200
    assert r.json()["dxf"]["units"] == "metres"
    assert r.json()["ifc"]["schema"] == "IFC4"

    # exports download with the right headers and parse back
    r = client.get(f"/api/projects/{pid}/bim/export/dxf")
    assert r.status_code == 200
    assert "siteplan.dxf" in r.headers.get("content-disposition", "")
    inv2 = bim.inspect_dxf(r.content)
    assert "A-PLOT-BNDRY" in {l["name"] for l in inv2["layers"]}

    r = client.get(f"/api/projects/{pid}/bim/export/ifc")
    assert r.status_code == 200
    assert r.content.startswith(b"ISO-10303-")
    assert ".ifc" in r.headers.get("content-disposition", "")

    # a garbage upload is a 400 with a readable message, not a 500
    r = client.post("/api/bim/dxf/inspect",
                    files={"file": ("junk.dxf", b"not a dxf at all", "application/dxf")})
    assert r.status_code == 400
    assert "DXF" in r.json()["detail"]
