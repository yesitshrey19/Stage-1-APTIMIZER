"""BIM & CAD interoperability: DXF (AutoCAD) in, DXF + IFC4 (Revit) out.

Three real exchange paths, not format guesses:

* DXF import — a surveyor's or architect's drawing is uploaded, its layers are
  inventoried, and a chosen layer's largest closed ring becomes the plot boundary.
  Coordinates are read as metres on a local grid and re-projected onto the globe
  with the same equirectangular frame the layout engine uses, so an imported
  boundary lands exactly where the packer, setbacks and 3D view expect it.
* DXF export — the whole scheme (boundary, tower footprints with labels, parking
  bays) written as layered polylines on an R2018 drawing. Any CAD product opens
  it; AutoCAD, BricsCAD and LibreCAD are the ones we name.
* IFC export — a georeferenced IFC4 model: IfcSite anchored at the plot
  centroid, one IfcBuilding per tower, a real IfcBuildingStorey per floor with a
  slab mass, and the boundary as an IfcGeographicElement. Revit opens it
  (Link IFC) and so does BIMvision/Solibri.

ezdxf's `recover` reader is used on import because production drawings are
rarely valid per the spec — it returns what it can salvage plus an auditor
report, which the UI surfaces instead of failing the upload.
"""
from __future__ import annotations

import io
import math
from typing import Dict, List, Optional, Tuple

import ezdxf
from ezdxf import recover
from ezdxf.enums import TextEntityAlignment
from ezdxf.lldxf.const import DXFStructureError

from siteplan.frame import LocalFrame

DEFAULT_ORIGIN = (12.9716, 77.5946)  # Bengaluru — only when the project has neither plot nor city

# Layer convention the exported drawing uses. AEC naming style; descriptions
# become layer descriptions in the DXF so they survive into the CAD session.
CAD_LAYERS = {
    "A-PLOT-BNDRY": ("Plot boundary as surveyed/drawn", 1),      # red
    "A-TOWER-FOOT": ("Tower footprints with name + floors", 5),  # blue
    "A-PARK-BAYS": ("Surface parking bays", 2),                  # yellow
    "A-ANNO-TXT": ("Annotation: names, areas, floors", 7),       # white/black
}


# --------------------------------------------------------------------------- DXF import

def _closed_rings(entities) -> List[List[List[float]]]:
    """Closed rings from the polyline entities a drawing uses for boundaries."""
    rings: List[List[List[float]]] = []
    for e in entities:
        try:
            kind = e.dxftype()
        except Exception:
            continue
        if kind == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points("xy")]
            closed = bool(e.closed) or (len(pts) > 2 and
                                        math.isclose(pts[0][0], pts[-1][0], abs_tol=1e-6) and
                                        math.isclose(pts[0][1], pts[-1][1], abs_tol=1e-6))
            if closed and len(pts) >= 3:
                rings.append([[float(x), float(y)] for x, y in pts])
        elif kind == "POLYLINE":
            try:
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
            except Exception:
                continue
            if e.is_closed and len(pts) >= 3:
                rings.append([[float(x), float(y)] for x, y in pts])
    return rings


def _ring_area_sqm(ring: List[List[float]]) -> float:
    """Shoelace. DXF y is north here, matching the frame, so no sign fix is needed."""
    n = len(ring)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def inspect_dxf(data: bytes) -> Dict:
    """Inventory a drawing: layers with entity counts, closed rings and areas."""
    try:
        doc, auditor = recover.read(io.BytesIO(data))
    except (DXFStructureError, IOError, ValueError, EOFError) as e:
        raise ValueError(f"This file could not be read as a DXF drawing: {e}") from e

    msp = doc.modelspace()
    layers: Dict[str, Dict] = {}
    entities_by_layer: Dict[str, List] = {}
    for e in msp:
        try:
            layer = e.dxf.layer
        except Exception:
            continue
        entry = layers.setdefault(layer, {"name": layer, "entities": 0,
                                          "closed_rings": 0, "largest_ring_area_sqm": 0.0})
        entry["entities"] += 1
        entities_by_layer.setdefault(layer, []).append(e)

    for layer, ents in entities_by_layer.items():
        rings = _closed_rings(ents)
        entry = layers[layer]
        entry["closed_rings"] = len(rings)
        if rings:
            entry["largest_ring_area_sqm"] = round(max(_ring_area_sqm(r) for r in rings), 2)

    listing = sorted(layers.values(),
                     key=lambda d: (-d["closed_rings"], -d["largest_ring_area_sqm"], d["name"]))
    for entry in listing:
        # Boundary layers are usually *named* like one — a hint, not a rule.
        name = entry["name"].lower()
        entry["likely_boundary"] = any(
            k in name for k in ("boundary", "plot", "parcel", "site", "khasra", "survey", "limit"))

    pretty = {"AC1032": "AutoCAD 2018", "AC1027": "AutoCAD 2013", "AC1024": "AutoCAD 2010",
              "AC1021": "AutoCAD 2007", "AC1015": "AutoCAD 2000"}.get(doc.dxfversion, doc.dxfversion)
    return {
        "dxf_version": pretty,
        "recovered_errors": len(auditor.errors),
        "layers": listing,
        "total_entities": sum(e["entities"] for e in listing),
    }


def import_boundary(data: bytes, layer: str, origin_latlng: Optional[List[float]] = None) -> Dict:
    """Extract the largest closed ring on `layer` as the plot boundary, in lat/lng.

    The ring is read as metres on a local grid and centred on `origin_latlng` —
    the project's existing centroid when it has one — so the imported boundary
    lands where the site map, setbacks and 3D view already point.
    """
    inv = inspect_dxf(data)
    match = next((l for l in inv["layers"] if l["name"] == layer), None)
    if match is None:
        raise ValueError(f"Layer '{layer}' is not in this drawing")
    if match["closed_rings"] == 0:
        raise ValueError(f"Layer '{layer}' has no closed polyline ring to import. "
                         "Boundaries must be closed LWPOLYLINE or POLYLINE entities.")

    doc, _ = recover.read(io.BytesIO(data))
    msp = doc.modelspace()
    ents = [e for e in msp if getattr(e.dxf, "layer", None) == layer]
    rings = _closed_rings(ents)
    ring = max(rings, key=_ring_area_sqm)

    if ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3:
        raise ValueError("The largest ring on that layer has fewer than 3 vertices")

    area = _ring_area_sqm(ring)
    if area < 25.0:
        raise ValueError(f"The largest ring on '{layer}' covers only {area:.1f} m² — too small "
                         "to be a plot boundary. Check the drawing's units (metres expected) and "
                         "that the boundary is a single closed polyline.")

    lat0, lng0 = (origin_latlng or list(DEFAULT_ORIGIN))[:2]
    frame = LocalFrame(lat0, lng0)
    # Centre the ring on the frame origin, then project with the shared constants —
    # the same ones scene.js and the layout engine use, so downstream is consistent.
    cx = sum(p[0] for p in ring) / len(ring)
    cy = sum(p[1] for p in ring) / len(ring)
    centred = [[p[0] - cx, p[1] - cy] for p in ring]
    coords = frame.ring_to_latlng(centred)
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return {
        "coordinates": coords,
        "area_sqm": round(area, 2),
        "vertex_count": len(ring),
        "source": f"DXF layer '{layer}' ({inv['dxf_version']})",
    }


# --------------------------------------------------------------------------- Robust geometry extraction

def extract_rings(raw) -> List[List[Tuple[float, float]]]:
    """Recursively extract valid coordinate rings [(x, y), ...] from any nested GeoJSON or layout structure."""
    if not raw or not isinstance(raw, (list, tuple)):
        return []
    # If this is directly a list of coordinate pairs: [[x, y], [x, y], ...]
    if len(raw) >= 3 and all(isinstance(p, (list, tuple)) and len(p) >= 2 for p in raw):
        try:
            pts = [(float(p[0]), float(p[1])) for p in raw]
            return [pts]
        except (ValueError, TypeError):
            pass

    # Otherwise recurse through children
    out = []
    for item in raw:
        if isinstance(item, (list, tuple)):
            out.extend(extract_rings(item))
    return out


def _tower_fallback_ring(t: Dict) -> List[Tuple[float, float]]:
    """Synthesize a rectangular footprint if a tower lacks explicit polygons."""
    c = t.get("centre_local") or [0.0, 0.0]
    try:
        cx, cy = float(c[0]), float(c[1])
    except (ValueError, TypeError, IndexError):
        cx, cy = 0.0, 0.0
    w = 20.0
    d = 20.0
    if t.get("width_m") and t.get("depth_m"):
        try:
            w = float(t["width_m"])
            d = float(t["depth_m"])
        except (ValueError, TypeError):
            pass
    elif t.get("footprint_sqm"):
        try:
            side = math.sqrt(float(t["footprint_sqm"]))
            w, d = side, side
        except (ValueError, TypeError):
            pass
    hw, hd = max(2.0, w / 2.0), max(2.0, d / 2.0)
    return [
        (cx - hw, cy - hd),
        (cx + hw, cy - hd),
        (cx + hw, cy + hd),
        (cx - hw, cy + hd),
        (cx - hw, cy - hd),
    ]


# --------------------------------------------------------------------------- DXF export

def export_siteplan_dxf(project: Dict) -> bytes:
    """Layered R2018 drawing of the scheme, in metres about the plot centroid."""
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    for name, (desc, color) in CAD_LAYERS.items():
        doc.layers.add(name, color=color)
        try:
            doc.layers.get(name).dxf.description = desc
        except Exception:
            pass

    plot = (project.get("plot") or {})
    coords = plot.get("coordinates") or []
    layout = project.get("site_layout") or {}
    towers = layout.get("towers") or project.get("towers") or []
    surface_parking = layout.get("surface_parking") or {}
    bays_raw = surface_parking.get("polygons_local") or []

    # The stored polygons_local rings are already centred on the plot's frame origin,
    # so the drawing is simply those metres. The boundary ring is rebuilt in the same
    # frame from its lat/lng so every layer shares one origin.
    if coords:
        try:
            valid_coords = [(float(c[0]), float(c[1])) for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
            if len(valid_coords) >= 3:
                frame = LocalFrame.from_coords(valid_coords)
                boundary = frame.ring_to_local(valid_coords)
                bndry_pts = [(float(p[0]), float(p[1])) for p in boundary if len(p) >= 2]
                if len(bndry_pts) >= 3:
                    msp.add_lwpolyline(bndry_pts, close=True, dxfattribs={"layer": "A-PLOT-BNDRY"})
        except Exception:
            pass

    for t in towers:
        rings = extract_rings(t.get("polygons_local"))
        if not rings:
            fallback = _tower_fallback_ring(t)
            if fallback:
                rings = [fallback]
        for ring in rings:
            pts = [(float(p[0]), float(p[1])) for p in ring]
            if len(pts) >= 3:
                msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "A-TOWER-FOOT"})
        c = t.get("centre_local") or [0.0, 0.0]
        try:
            cx, cy = float(c[0]), float(c[1])
        except Exception:
            cx, cy = 0.0, 0.0
        label = f"{t.get('name', 'Tower')}  {t.get('floors', '?')}F  {t.get('footprint_sqm', '?')} m2"
        msp.add_text(label, dxfattribs={"layer": "A-ANNO-TXT", "height": 2.0}).set_placement(
            (cx, cy), align=TextEntityAlignment.MIDDLE_CENTER)

    for ring in extract_rings(bays_raw):
        pts = [(float(p[0]), float(p[1])) for p in ring]
        if len(pts) >= 3:
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "A-PARK-BAYS"})

    metrics = layout.get("layout_metrics") or {}
    title = (f"{project.get('name', 'Site plan')}  |  plot {plot.get('area_sqm') or '?'} m2  |  "
             f"towers {metrics.get('tower_count', len(towers))}  |  units {metrics.get('unit_count', '?')}  |  "
             f"coverage {metrics.get('ground_coverage_pct', '?')}%")
    msp.add_text(title, dxfattribs={"layer": "A-ANNO-TXT", "height": 3.0}).set_placement(
        (0.0, -8.0), align=TextEntityAlignment.MIDDLE_CENTER)
    try:
        doc.header["$INSUNITS"] = 6  # metres
    except Exception:
        pass

    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


class DwgConverterUnavailable(RuntimeError):
    """True DWG needs the ODA File Converter; without it only DXF can be written."""


def find_odafc() -> str:
    """Path to ODAFileConverter, or "" when it is not installed.

    Looks at ODAFC_PATH first, then the Windows installer's versioned folders
    (C:\\Program Files\\ODA\\ODAFileConverter 25.x.0\\), then PATH (the Linux .deb).
    """
    import glob
    import os
    import shutil
    explicit = os.environ.get("ODAFC_PATH", "").strip()
    if explicit and os.path.isfile(explicit):
        return explicit
    if os.name == "nt":
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")]
        hits = []
        for root in filter(None, roots):
            hits += glob.glob(os.path.join(root, "ODA", "ODAFileConverter*", "ODAFileConverter.exe"))
        if hits:
            return sorted(hits)[-1]          # newest version folder sorts last
    return shutil.which("ODAFileConverter") or ""


def _configure_odafc() -> None:
    import ezdxf
    path = find_odafc()
    if path:
        key = "win_exec_path" if __import__("os").name == "nt" else "unix_exec_path"
        ezdxf.options.set("odafc-addon", key, path)


def export_siteplan_dwg(project: Dict) -> bytes:
    """A real AutoCAD R2018 DWG, converted from the DXF by the ODA File Converter.

    DWG is a closed binary format that no Python library writes natively. This used to
    return the DXF text under a .dwg name, which AutoCAD rejects as an invalid drawing. Now
    it either produces a genuine DWG or raises DwgConverterUnavailable so the caller can
    hand over the DXF honestly (AutoCAD, BricsCAD and Revit all open DXF directly).
    """
    import os
    import tempfile
    from ezdxf.addons import odafc
    _configure_odafc()
    if not odafc.is_installed():
        raise DwgConverterUnavailable("ODA File Converter is not installed on the server.")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "siteplan.dxf")
        dst = os.path.join(tmp, "siteplan.dwg")
        with open(src, "wb") as fh:
            fh.write(export_siteplan_dxf(project))
        odafc.convert(src, dst, version="R2018", replace=True)
        with open(dst, "rb") as fh:
            return fh.read()


def export_siteplan_cad(project: Dict) -> tuple:
    """(bytes, "dwg") when a true DWG can be made, else (bytes, "dxf")."""
    try:
        return export_siteplan_dwg(project), "dwg"
    except DwgConverterUnavailable:
        return export_siteplan_dxf(project), "dxf"


# --------------------------------------------------------------------------- IFC export

def _deg_to_ifc(deg: float) -> List[int]:
    """Degrees -> IfcCompoundPlaneAngleMeasure: (deg, min, sec, microsec), ints.

    The IFC type is integer-only; sign lives on the first component and the
    remainder components stay positive.
    """
    try:
        total = abs(float(deg)) * 3600.0
        d = int(total // 3600)
        m = int((total % 3600) // 60)
        s = int(total % 60)
        return [d, m, s, 0] if deg >= 0 else [-d, -m, -s, 0]
    except Exception:
        return [0, 0, 0, 0]


def export_ifc4(project: Dict) -> bytes:
    """Georeferenced IFC4: one building per tower, a storey + slab mass per floor."""
    import ifcopenshell
    import ifcopenshell.api

    model = ifcopenshell.api.run("project.create_file", version="IFC4")
    ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject",
                         name=project.get("name", "Aptimizer scheme"))
    ctx = ifcopenshell.api.run("context.add_context", model, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", model, context_type="Model",
                                context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
    ifcopenshell.api.run("unit.assign_unit", model)

    site = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite",
                                name=f"Site - {project.get('name', 'plot')}")

    plot = (project.get("plot") or {})
    coords = plot.get("coordinates") or []
    if coords:
        try:
            valid_coords = [(float(c[0]), float(c[1])) for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
            if len(valid_coords) >= 3:
                lat0 = sum(c[0] for c in valid_coords) / len(valid_coords)
                lng0 = sum(c[1] for c in valid_coords) / len(valid_coords)
                site.RefLatitude = _deg_to_ifc(lat0)
                site.RefLongitude = _deg_to_ifc(lng0)
                frame = LocalFrame.from_coords(valid_coords)
                boundary = [[float(x), float(y)] for x, y in frame.ring_to_local(valid_coords)]
                if len(boundary) >= 3:
                    _add_extruded(model, body, "Plot boundary", boundary, 0.05,
                                  ifc_class="IfcGeographicElement", parent=site)
        except Exception:
            pass

    layout = project.get("site_layout") or {}
    towers = layout.get("towers") or project.get("towers") or []

    for t in towers:
        rings = extract_rings(t.get("polygons_local"))
        if not rings:
            fallback = _tower_fallback_ring(t)
            if fallback:
                rings = [fallback]
        if not rings:
            continue
        ring = [[float(p[0]), float(p[1])] for p in rings[0]]
        if len(ring) < 3:
            continue
        floors = max(1, int(t.get("floors") or 1))
        fh = float(t.get("floor_height_m") or 3.0)
        name = t.get("name") or "Tower"
        bldg = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuilding", name=name)
        ifcopenshell.api.run("aggregate.assign_object", model, relating_object=site, products=[bldg])
        slab_h = min(0.2, fh)
        for storey_i in range(floors):
            z = storey_i * fh
            storey = ifcopenshell.api.run(
                "root.create_entity", model, ifc_class="IfcBuildingStorey",
                name=f"{name} - L{storey_i + 1}")
            storey.Elevation = z
            ifcopenshell.api.run("aggregate.assign_object", model, relating_object=bldg, products=[storey])
            _add_extruded(model, body, f"{name} slab L{storey_i + 1}", ring, slab_h,
                          ifc_class="IfcSlab", container=storey, z_offset=z)

    import os
    import tempfile

    fd, tmp_path = tempfile.mkstemp(suffix=".ifc")
    os.close(fd)  # Windows: the open handle would block the write and the delete
    try:
        model.write(tmp_path)
        with open(tmp_path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def _add_extruded(model, body_ctx, name, ring, height, ifc_class="IfcSlab",
                  parent=None, container=None, z_offset=0.0):
    """Closed-ring extrusion at z_offset, aggregated or contained as given."""
    import ifcopenshell.api

    pts = [[float(p[0]), float(p[1]), float(z_offset)] for p in ring]
    poly = model.createIfcPolyline(
        [model.createIfcCartesianPoint([float(c) for c in p]) for p in pts + [pts[0]]])
    profile = model.createIfcArbitraryClosedProfileDef("AREA", None, poly)
    solid = model.createIfcExtrudedAreaSolid(
        profile,
        model.createIfcAxis2Placement3D(model.createIfcCartesianPoint([0.0, 0.0, 0.0])),
        model.createIfcDirection([0.0, 0.0, 1.0]),
        float(height))
    shape = model.createIfcShapeRepresentation(body_ctx, "Body", "SweptSolid", [solid])
    prod = ifcopenshell.api.run("root.create_entity", model, ifc_class=ifc_class, name=name)
    if container is not None:
        ifcopenshell.api.run("spatial.assign_container", model, relating_structure=container, products=[prod])
    elif parent is not None:
        ifcopenshell.api.run("aggregate.assign_object", model, relating_object=parent, products=[prod])
    ifcopenshell.api.run("geometry.assign_representation", model, product=prod, representation=shape)
    ifcopenshell.api.run("geometry.edit_object_placement", model, product=prod)
    return prod


def ifc_summary(project: Dict) -> Dict:
    """What an export would contain — shown before the user clicks download."""
    layout = project.get("site_layout") or {}
    towers = layout.get("towers") or project.get("towers") or []
    return {
        "schema": "IFC4",
        "viewer_hint": "Revit: Insert > Link IFC. Free viewers: BIMvision, usBIM.viewer, Solibri Anywhere.",
        "site_latlng": _centroid_latlng(project),
        "buildings": len(towers),
        "storeys": sum(int(t.get("floors") or 0) for t in towers),
        "unit_count": (layout.get("layout_metrics") or {}).get("unit_count"),
        "towers": [{"name": t.get("name"), "floors": t.get("floors"),
                    "footprint_sqm": t.get("footprint_sqm")} for t in towers][:25],
    }


def dxf_summary(project: Dict) -> Dict:
    """What the DXF export would contain, for the pre-download card."""
    layout = project.get("site_layout") or {}
    towers = layout.get("towers") or project.get("towers") or []
    surface_parking = layout.get("surface_parking") or {}
    bays = extract_rings(surface_parking.get("polygons_local") or [])
    return {
        "format": "DXF R2018 (ASCII)",
        "layers": [{"name": name, "description": desc} for name, (desc, _) in CAD_LAYERS.items()],
        "tower_footprints": len(towers),
        "parking_bays": len(bays),
        "has_boundary": bool((project.get("plot") or {}).get("coordinates")),
        "units": "metres",
        "opens_in": "AutoCAD, BricsCAD, LibreCAD, DraftSight and any DXF reader",
    }


def _centroid_latlng(project: Dict) -> Optional[List[float]]:
    coords = (project.get("plot") or {}).get("coordinates") or []
    if not coords:
        return None
    try:
        valid_coords = [(float(c[0]), float(c[1])) for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
        if not valid_coords:
            return None
        return [round(sum(c[0] for c in valid_coords) / len(valid_coords), 6),
                round(sum(c[1] for c in valid_coords) / len(valid_coords), 6)]
    except Exception:
        return None
