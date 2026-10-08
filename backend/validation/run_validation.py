"""Batch validation of the Stage 1 (Site) engines against independently measured ground truth.

Runs every plot in a GIS testing workbook through the same code the web app uses -- the GIS
site analysis, the development-control recommendation and the site layout engine -- and
writes a validation workbook:

  Comparison       one row per plot: Aptimizer's value next to a yellow cell for the value
                   you measured (Google Earth, NOAA, PVGIS, IS codes...), with live error and
                   PASS/FAIL formulas against a stated tolerance.
  Summary          pass rate per metric and the suitability-vs-expert rank correlation.
  Aptimizer_Detail every raw output per plot, for traceability.
  Method           tolerances, reference sources and how to measure each ground-truth value.
  Run_Info         when it ran, engine versions and where each plot's data came from.

Usage (from Aptimizer-Stage1/backend):

    ../../Apt--main/backend/.venv/bin/python -m validation.run_validation \\
        --input  "~/Desktop/GIS_Testing_Base_P01_updated (1).xlsx" \\
        --kml    ~/Desktop/plot_boundaries            # optional: P01.kml, P02.kml ... or one KML
        --merge  previous_validation.xlsx             # optional: carry over ground truth already typed

Without KML boundaries each plot is modelled as a square of its recorded area around its
centre point and flagged APPROXIMATE: area and setback results are then not a valid test,
but every location-based check (roads, water, terrain, seismic, wind, sun, solar) still is.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
import warnings
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)                      # gis.py keeps its map-data cache beside itself

from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.formatting.rule import CellIsRule, FormulaRule  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

import engine  # noqa: E402
import gis  # noqa: E402
import siteplan  # noqa: E402

# The layout optimiser's minimum-rectangle step emits harmless numpy warnings on degenerate
# candidate shapes; they are noise in a batch log.
warnings.filterwarnings("ignore", category=RuntimeWarning, module="shapely")

MAP_RETRIES = 2                   # public Overpass mirrors rate-limit bursts (HTTP 429)
MAP_RETRY_WAIT_S = 20
LAYOUT_MAX_SQM = 200_000          # the Site stage targets development plots, not 500 ha campuses
DEFAULT_ROAD_WIDTH_M = 12.0
SQ_M_PER_DEG_LAT = 111_320.0

# ------------------------------------------------------------------------- boundaries

def _parse_kml_coords(text: str):
    pts = []
    for tok in (text or "").split():
        parts = tok.split(",")
        if len(parts) >= 2:
            lng, lat = float(parts[0]), float(parts[1])
            pts.append([lat, lng])
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]                 # KML repeats the first vertex to close the ring
    return pts


def _kml_polygons(path: Path):
    """[(placemark name, [[lat, lng], ...]), ...] for every polygon in a KML file."""
    tree = ET.parse(path)
    out = []
    for pm in tree.iter():
        if not pm.tag.endswith("Placemark"):
            continue
        name = ""
        for child in pm.iter():
            if child.tag.endswith("name") and child.text:
                name = child.text.strip()
                break
        for el in pm.iter():
            if el.tag.endswith("outerBoundaryIs"):
                for c in el.iter():
                    if c.tag.endswith("coordinates"):
                        pts = _parse_kml_coords(c.text)
                        if len(pts) >= 3:
                            out.append((name, pts))
    return out


def _geojson_polygons(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    feats = data.get("features") or ([data] if data.get("type") == "Feature" else [{"geometry": data}])
    out = []
    for f in feats:
        g = f.get("geometry") or {}
        rings = []
        if g.get("type") == "Polygon":
            rings = [g["coordinates"][0]]
        elif g.get("type") == "MultiPolygon":
            rings = [g["coordinates"][0][0]]
        for ring in rings:
            pts = [[float(p[1]), float(p[0])] for p in ring]
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(pts) >= 3:
                out.append(((f.get("properties") or {}).get("name", ""), pts))
    return out


def load_boundaries(kml_dir: str | None, plot_ids):
    """{plot_id: (coords, source_file)} from a folder of KML/GeoJSON files or one file."""
    found = {}
    if not kml_dir:
        return found
    base = Path(os.path.expanduser(kml_dir))
    files = [base] if base.is_file() else sorted(p for p in base.iterdir()
                                                 if p.suffix.lower() in (".kml", ".geojson", ".json"))
    for f in files:
        polys = _kml_polygons(f) if f.suffix.lower() == ".kml" else _geojson_polygons(f)
        for name, pts in polys:
            for pid in plot_ids:
                key = pid.upper()
                if pid in found:
                    continue
                # Match on the placemark name, else on the file name ("P03.kml", "P03 mall.kml").
                if re.search(rf"\b{key}\b", (name or "").upper()) or \
                   (len(polys) == 1 and re.match(rf"{key}\b", f.stem.upper())):
                    found[pid] = (pts, f.name)
    return found


def square_around(lat, lng, area_sqm):
    half = math.sqrt(max(area_sqm, 100.0)) / 2.0
    dlat = half / 110540.0             # metres per degree of latitude, as the app measures
    dlng = half / (SQ_M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return [[lat - dlat, lng - dlng], [lat - dlat, lng + dlng],
            [lat + dlat, lng + dlng], [lat + dlat, lng - dlng]]


def polygon_area_sqm(coords):
    """The app's own plot-area function, so the workbook and the web app cannot disagree."""
    return engine.polygon_area_sqm(coords)


# ------------------------------------------------------------------------- input sheet

def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def read_plots(path: str):
    wb = load_workbook(os.path.expanduser(path), data_only=True)
    ws = wb["Plot_Master"] if "Plot_Master" in wb.sheetnames else wb.worksheets[0]
    header = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]

    def col(*names):
        for n in names:
            for i, h in enumerate(header):
                if h.lower().startswith(n.lower()):
                    return i
        return None

    ix = {"id": col("Plot ID"), "loc": col("Location"), "lat": col("Latitude"), "lng": col("Longitude"),
          "area": col("Approx Area"), "landuse": col("Ground Truth Land Use"), "road": col("Nearest Road"),
          "conf": col("Boundary Confidence"), "water": col("Nearby Water"),
          "road_w": col("Measured road width")}
    missing = [k for k in ("id", "lat", "lng") if ix[k] is None]
    if missing:
        raise SystemExit(f"Input sheet is missing column(s): {missing}")

    plots = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        pid = row[ix["id"]]
        if not pid or row[ix["lat"]] in (None, "") or row[ix["lng"]] in (None, ""):
            continue
        get = lambda k: (row[ix[k]] if ix[k] is not None else None)  # noqa: E731
        loc = str(get("loc") or "")
        city = loc.split(",")[-1].strip() if "," in loc else loc.strip()
        plots.append({
            "id": str(pid).strip(), "location": loc, "city": city or "Bengaluru",
            "lat": float(get("lat")), "lng": float(get("lng")),
            "area": float(get("area") or 0) or 10_000.0,
            "landuse": get("landuse") or "", "road_name": get("road") or "",
            "boundary_conf": get("conf") or "", "water_note": get("water") or "",
            "road_width": _num(get("road_w")),
        })
    return plots

# ------------------------------------------------------------------------- engines

def _hours_to_excel_time(h):
    return None if h is None else (h % 24) / 24.0


async def run_plot(p, boundary, radius_m):
    coords, bsource = boundary
    project = {"plot": {"coordinates": coords, "orientation_deg": 0, "road_edges": []},
               "location": p["city"], "towers": []}
    t0 = time.time()
    g = await gis.analyse_site(project, radius_m)
    for attempt in range(MAP_RETRIES):
        if g["sources"]["overpass"].get("ok"):
            break
        print(f"map servers busy, retrying in {MAP_RETRY_WAIT_S} s … ", end="", flush=True)
        await asyncio.sleep(MAP_RETRY_WAIT_S)
        g = await gis.analyse_site(project, radius_m)
    secs = round(time.time() - t0, 2)
    map_ok = bool(g["sources"]["overpass"].get("ok"))

    paths = {s["key"]: s for s in g["sun"]["paths"]}
    noon = {}
    for key, doy, _label in gis.SUN_DATES:
        # Highest sun of the day from the engine's own solar-position function, 1-minute steps.
        best = max(((gis.solar_position(p["lat"], p["lng"], doy, 10 + m / 60.0)[1], m)
                    for m in range(0, 241)), key=lambda x: x[0])
        noon[key] = round(best[0], 2)

    ins = g["solar"]["insolation"]
    pr = g["solar"]["config"]["performance_ratio"]
    area = polygon_area_sqm(coords)

    layout = {"ran": False, "note": ""}
    if area > LAYOUT_MAX_SQM:
        layout["note"] = (f"Skipped: {area / 10_000:,.1f} ha is outside the Site stage's target range "
                          f"(development plots up to {LAYOUT_MAX_SQM / 10_000:.0f} ha).")
    else:
        widest = g["accessibility"].get("widest_road_m") or 0
        road_w = p.get("road_width") or DEFAULT_ROAD_WIDTH_M
        road_src = "measured (sheet)" if p.get("road_width") else f"assumed {DEFAULT_ROAD_WIDTH_M:g} m"
        rec = siteplan.recommend_controls(plot_area=area, road_width=road_w, city=p["city"])
        if rec.get("ok"):
            floors = int(rec.get("floors") or 4)
            proj = {**project, "dev_controls": {"setbacks": rec["setbacks"]},
                    "towers": [{"floors": floors, "floor_height": 3.0}]}
            t1 = time.time()
            res = siteplan.plan_site(proj, {"setbacks": rec["setbacks"], "far_cap": rec.get("far_cap") or 3.0,
                                           "ground_coverage_cap_pct": rec.get("ground_coverage_cap_pct") or 40.0,
                                           "towers": {"floors_min": min(4, floors), "floors_max": floors}})
            m = res.get("layout_metrics") or {}
            layout = {
                "ran": True, "secs": round(time.time() - t1, 2), "road_width_assumed": road_w,
                "road_width_source": road_src,
                "osm_widest_road_m": widest,
                "setback_front": rec["setbacks"].get("front"), "setback_rear": rec["setbacks"].get("rear"),
                "setback_side": rec["setbacks"].get("side"), "rec_floors": rec.get("floors"),
                "rec_height_m": rec.get("height_m"), "far_cap": rec.get("far_cap"),
                "envelope_sqm": (res.get("envelope") or {}).get("area_sqm"),
                "packable_sqm": (res.get("residual") or {}).get("area_sqm"),
                "towers": m.get("tower_count", 0), "units": m.get("unit_count", 0),
                "achieved_far": m.get("achieved_far"), "coverage_pct": m.get("ground_coverage_pct"),
                "feasible": m.get("feasible"), "fallback": res.get("small_plot_fallback") or "",
                "note": "; ".join((res.get("warnings") or [])[-2:]),
            }
        else:
            layout["note"] = rec.get("error", "Recommendation failed")

    out = {
        "secs": secs, "boundary_source": bsource, "area_sqm": round(area, 1), "vertices": len(coords),
        "nearest_road_m": g["accessibility"].get("nearest_road_m"),
        "nearest_road_kind": g["accessibility"].get("nearest_road_kind"),
        "nearest_road_name": g["accessibility"].get("nearest_road_name"),
        "nearest_internal_road_m": g["accessibility"].get("nearest_internal_road_m"),
        "roads_within_100m": g["accessibility"].get("roads_within_100m"),
        "widest_road_m": g["accessibility"].get("widest_road_m"),
        "nearest_transit_m": g["accessibility"].get("nearest_transit_m"),
        "access_score": g["accessibility"].get("score"),
        # "none" when no water body lies within the study radius -- the same rule the
        # measurer follows, so "none" on both sides is agreement, not a missing value.
        "nearest_water_m": (g["flood"].get("nearest_water_m")
                            if g["flood"].get("nearest_water_m") is not None
                            and g["flood"]["nearest_water_m"] <= radius_m else "none"),
        "nearest_water_label": g["flood"].get("nearest_water_label"),
        "flood_level": g["flood"].get("level"), "flood_score": g["flood"].get("score"),
        "nearest_drain_m": g["flood"].get("nearest_drain_m"),
        "elev_delta_m": g["flood"].get("elevation_delta_m"), "plinth_m": g["flood"].get("plinth_height_m"),
        "elev_mean_m": g["terrain"].get("mean_m"), "elev_min_m": g["terrain"].get("min_m"),
        "elev_max_m": g["terrain"].get("max_m"), "relief_m": g["terrain"].get("relief_m"),
        "slope_pct": g["terrain"].get("avg_slope_pct"), "slope_class": g["terrain"].get("slope_class"),
        "seismic_zone": g["seismic"].get("zone"), "pga_surface_g": g["seismic"].get("pga_surface_g"),
        "wind_vb_ms": (g["wind"].get("is875_design") or {}).get("basic_wind_speed_vb_ms"),
        "wind_prevailing": g["wind"].get("prevailing"),
        "sunrise_mar": _hours_to_excel_time(paths["equinox"].get("sunrise_hour")),
        "sunset_mar": _hours_to_excel_time(paths["equinox"].get("sunset_hour")),
        "sunrise_jun": _hours_to_excel_time(paths["summer_solstice"].get("sunrise_hour")),
        "sunset_jun": _hours_to_excel_time(paths["summer_solstice"].get("sunset_hour")),
        "sunrise_dec": _hours_to_excel_time(paths["winter_solstice"].get("sunrise_hour")),
        "sunset_dec": _hours_to_excel_time(paths["winter_solstice"].get("sunset_hour")),
        "noon_elev_mar": noon["equinox"], "noon_elev_jun": noon["summer_solstice"],
        "noon_elev_dec": noon["winter_solstice"],
        "ghi_kwh_m2_yr": ins.get("annual_kwh_per_sqm"),
        "ghi_day": ins.get("daily_average_kwh_per_sqm"),
        "wind_mean_ms": g["wind"].get("mean_speed_ms"),
        # same formula as gis.solar_potential: site irradiation x optimal-tilt gain x PR
        "specific_yield": round(ins.get("annual_kwh_per_sqm", 0) * ins.get("tilt_gain", 1.0) * pr, 0),
        "buildings": (f'{g["feature_counts"].get("buildings")}+' if (g.get("feature_counts_capped") or {}).get("buildings")
                      else g["feature_counts"].get("buildings")),
        "green": g["feature_counts"].get("green"),
        "water_features": g["feature_counts"].get("water"), "transit": g["feature_counts"].get("transit"),
        "suitability": g["suitability"].get("score"), "grade": g["suitability"].get("grade"),
        "buildable": g["buildability"].get("buildable"),
        "flags": "; ".join(f"{f['severity'].upper()}: {f['title']}" for f in g["buildability"].get("flags", [])),
        "overpass_source": "cache" if g["sources"]["overpass"].get("endpoint") == "cache"
                           else (g["sources"]["overpass"].get("endpoint") or g["sources"]["overpass"].get("error")),
        "overpass_ok": g["sources"]["overpass"].get("ok"),
        "elevation_source": g["sources"]["elevation"].get("endpoint") or g["sources"]["elevation"].get("error"),
        "rules_version": g.get("rules_version"), "generated_at": g.get("generated_at"),
        "layout": layout,
        "map_ok": map_ok,
    }
    if not map_ok:
        # Without OpenStreetMap data the road, water and building figures are absences, not
        # measurements; leave them blank rather than let them be scored as results.
        for k in ("nearest_road_m", "nearest_road_kind", "roads_within_100m", "widest_road_m", "nearest_drain_m",
                  "nearest_transit_m", "access_score", "nearest_water_m", "nearest_water_label",
                  "flood_level", "flood_score", "buildings", "green", "water_features", "transit",
                  "suitability", "grade", "buildable", "flags"):
            out[k] = None
        out["boundary_source"] = "MAP DATA FAILED — rerun · " + bsource
    return out

# ------------------------------------------------------------------------- workbook

YELLOW = PatternFill("solid", fgColor="FFF7CC")
HEAD = PatternFill("solid", fgColor="0F172A")
SUB = PatternFill("solid", fgColor="E2E8F0")
APT = PatternFill("solid", fgColor="E0ECFF")
GREEN_F = PatternFill("solid", fgColor="D1FAE5")
RED_F = PatternFill("solid", fgColor="FEE2E2")
WHITE_B = Font(color="FFFFFF", bold=True)
BOLD = Font(bold=True)
THIN = Border(*(Side(style="thin", color="CBD5E1"),) * 4)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

# (key, label, kind, tolerance, unit, reference, how to measure)
# kind: abs | rel | time (minutes) | exact | flood | info
RADIUS_FOR_WATER = 500      # study radius; set from --radius at run time

METRICS = [
    ("area_sqm", "Plot area", "rel", 0.02, "m²", "Google Earth Pro polygon",
     "Draw the plot boundary in Google Earth Pro (Add > Polygon) and read Area in the Measurements tab. "
     "Only meaningful when the boundary came from your KML (column Boundary)."),
    ("nearest_road_m", "Distance to nearest road", "abs", 10, "m", "Google Earth Pro ruler",
     "Ruler tool: shortest distance from the plot boundary to the centreline of the nearest PUBLIC road "
     "(not campus driveways, parking aisles or private internal roads). 0 if it runs along the plot. "
     "Aptimizer_Detail names the road Aptimizer used."),
    ("nearest_water_m", "Distance to nearest water body", "water", 25, "m", "Google Earth Pro ruler",
     "Shortest distance from the plot boundary to the edge of the nearest lake, tank or river "
     "(not storm drains). Leave blank if none within the study radius."),
    ("elev_mean_m", "Mean ground elevation", "abs", 5, "m", "Google Earth Pro / Bhuvan CartoDEM",
     "Average the elevation shown at the plot centre and its four corners (status bar 'elev')."),
    ("slope_pct", "Average slope across plot", "abs", 1.0, "%", "Google Earth elevation profile",
     "Draw a north-south and an east-west line across the plot; slope = sqrt(s1^2 + s2^2), "
     "each s = height difference / length x 100."),
    ("seismic_zone", "Seismic zone", "exact", None, "", "IS 1893 (Part 1):2016 zone map",
     "Read the zone (II, III, IV or V) for the city from IS 1893 Annex E / the BIS seismic map."),
    ("wind_vb_ms", "Basic wind speed Vb", "abs", 0.01, "m/s", "IS 875 (Part 3):2015 Annex A",
     "Read Vb for the city from IS 875-3 Annex A (e.g. Bengaluru 33 m/s)."),
    ("sunrise_mar", "Sunrise 21 Mar 2026 (IST)", "time", 2, "min", "NOAA Solar Calculator",
     "gml.noaa.gov/grad/solcalc: enter the plot lat/long, date 2026-03-21, time zone +5.5; read Sunrise."),
    ("sunset_mar", "Sunset 21 Mar 2026 (IST)", "time", 2, "min", "NOAA Solar Calculator",
     "Same page, read Sunset."),
    ("noon_elev_jun", "Max sun elevation 21 Jun", "abs", 0.5, "°", "NOAA Solar Calculator",
     "Date 2026-06-21, set the time to Solar Noon and read the Solar Elevation."),
    ("specific_yield", "Solar yield per kWp", "rel", 0.10, "kWh/kWp/yr", "PVGIS or Global Solar Atlas",
     "globalsolaratlas.info: click the plot, read 'Specific photovoltaic power output' (kWh/kWp per year)."),
    ("ghi_day", "Daily solar radiation (GHI)", "rel", 0.10, "kWh/m²/day", "Global Solar Atlas (Solargis) GHI ÷ 365",
     "globalsolaratlas.info: click the plot, read 'Global horizontal irradiation' (kWh/m² per year) and divide "
     "by 365. Aptimizer reads NASA POWER, so Global Solar Atlas -- a different satellite dataset -- keeps this "
     "an independent check."),
    ("wind_mean_ms", "Mean wind speed at 10 m", "abs", 0.5, "m/s", "NASA POWER (WS10M)",
     "power.larc.nasa.gov/data-access-viewer: Single Point, Climatology, parameter 'Wind Speed at 10 Meters'; "
     "read the annual (ANN) value. Aptimizer reads this same dataset, so this confirms the data link rather "
     "than giving an independent check."),
    ("flood_level", "Flood risk vs flood history", "flood", None, "", "BBMP flood-prone list / news / KSNDMC",
     "Enter Yes if the plot or its street flooded in a known event (e.g. 2022 Bengaluru floods), else No. "
     "PASS = Yes with Aptimizer moderate/high, or No with Aptimizer low/moderate."),
]


def _fmt_time(cell):
    cell.number_format = "hh:mm"


def write_workbook(out_path, plots, results, run_meta, carried):
    wb = Workbook()

    # ---------------------------------------------------------------- Comparison
    ws = wb.active
    ws.title = "Comparison"
    fixed = ["Plot ID", "Location", "Boundary", "Boundary confidence"]
    merges = []                       # applied after styling: merged cells cannot be styled
    for i, h in enumerate(fixed, 1):
        ws.cell(row=1, column=i, value=h)
        ws.cell(row=2, column=i)
        merges.append((1, i, 2, i))
    col = len(fixed) + 1
    metric_cols = {}
    for key, label, kind, tol, unit, ref, how in METRICS:
        tol_txt = ("exact" if kind in ("exact", "flood") else
                   f"±{tol * 100:.0f}%" if kind == "rel" else f"±{tol:g} {unit}".strip())
        ws.cell(row=1, column=col, value=f"{label}  ({tol_txt})")
        merges.append((1, col, 1, col + 3))
        for j, sub in enumerate(["Aptimizer", "Measured (you)", "Error", "Result"]):
            ws.cell(row=2, column=col + j, value=sub)
        metric_cols[key] = col
        col += 4
    rank_col = col
    for j, h in enumerate(["Suitability score", "Aptimizer rank", "Expert rank (you)", "Notes"]):
        ws.cell(row=1, column=rank_col + j, value=h)
        ws.cell(row=2, column=rank_col + j)
        merges.append((1, rank_col + j, 2, rank_col + j))

    for c in ws[1] + ws[2]:
        c.fill, c.font, c.alignment, c.border = HEAD, WHITE_B, CENTER, THIN
    for c in ws[2]:
        if c.value in ("Aptimizer", "Measured (you)", "Error", "Result"):
            c.fill, c.font = SUB, BOLD
    for r1, c1, r2, c2 in merges:
        ws.merge_cells(start_row=r1, start_column=c1, end_row=r2, end_column=c2)

    first = 3
    last = first + len(plots) - 1
    for r, (p, res) in enumerate(zip(plots, results), start=first):
        prev = carried.get(p["id"], {})
        approx = "APPROX" in (res["boundary_source"] or "")
        ws.cell(row=r, column=1, value=p["id"]).font = BOLD
        ws.cell(row=r, column=2, value=p["location"])
        ws.cell(row=r, column=3, value=res["boundary_source"])
        ws.cell(row=r, column=4, value=p["boundary_conf"])
        if approx or "FAILED" in (res["boundary_source"] or ""):
            ws.cell(row=r, column=3).fill = RED_F
        for key, label, kind, tol, unit, ref, how in METRICS:
            c0 = metric_cols[key]
            A = get_column_letter(c0)
            G = get_column_letter(c0 + 1)
            apt = ws.cell(row=r, column=c0, value=res.get(key))
            apt.fill = APT
            gt = ws.cell(row=r, column=c0 + 1, value=prev.get(key))
            gt.fill = YELLOW
            if kind == "time":
                _fmt_time(apt)
                _fmt_time(gt)
                err = f'=IF({G}{r}="","",ROUND(({A}{r}-{G}{r})*1440,1))'
                res_f = f'=IF({G}{r}="","",IF(ABS(({A}{r}-{G}{r})*1440)<={tol},"PASS","FAIL"))'
            elif kind == "abs":
                err = f'=IF(OR({G}{r}="",{A}{r}=""),"",ROUND({A}{r}-{G}{r},2))'
                res_f = f'=IF(OR({G}{r}="",{A}{r}=""),"",IF(ABS({A}{r}-{G}{r})<={tol},"PASS","FAIL"))'
            elif kind == "rel":
                err = f'=IF(OR({G}{r}="",{A}{r}="",{G}{r}=0),"",ROUND(({A}{r}-{G}{r})/{G}{r},4))'
                res_f = (f'=IF(OR({G}{r}="",{A}{r}="",{G}{r}=0),"",'
                         f'IF(ABS({A}{r}-{G}{r})/{G}{r}<={tol},"PASS","FAIL"))')
                ws.cell(row=r, column=c0 + 2).number_format = "0.0%"
            elif kind == "water":
                # A measured distance beyond the study radius is "none" by the method's own rule.
                if isinstance(gt.value, (int, float)) and gt.value > RADIUS_FOR_WATER:
                    gt.value = "none"
                err = (f'=IF(OR({G}{r}="",{A}{r}="",{G}{r}="none",{A}{r}="none"),"",'
                       f'ROUND({A}{r}-{G}{r},2))')
                res_f = (f'=IF(OR({G}{r}="",{A}{r}=""),"",IF(AND({G}{r}="none",{A}{r}="none"),"PASS",'
                         f'IF(OR({G}{r}="none",{A}{r}="none"),"FAIL",'
                         f'IF(ABS({A}{r}-{G}{r})<={tol},"PASS","FAIL"))))')
            elif kind == "exact":
                err = ""
                res_f = f'=IF({G}{r}="","",IF(UPPER(TRIM({G}{r}))=UPPER(TRIM({A}{r})),"PASS","FAIL"))'
            else:  # flood
                err = ""
                res_f = (f'=IF({G}{r}="","",IF(OR(AND(UPPER({G}{r})="YES",{A}{r}<>"low"),'
                         f'AND(UPPER({G}{r})="NO",{A}{r}<>"high")),"PASS","FAIL"))')
            if key == "area_sqm" and approx:
                res_f = '="n/a (approx. boundary)"'
            ws.cell(row=r, column=c0 + 2, value=err or None)
            ws.cell(row=r, column=c0 + 3, value=res_f)
        S = get_column_letter(rank_col)
        ws.cell(row=r, column=rank_col, value=res["suitability"]).fill = APT
        ws.cell(row=r, column=rank_col + 1, value=f"=RANK({S}{r},${S}${first}:${S}${last},0)")
        ws.cell(row=r, column=rank_col + 2, value=prev.get("expert_rank")).fill = YELLOW
        ws.cell(row=r, column=rank_col + 3, value=prev.get("notes"))
        for c in ws[r]:
            c.border = THIN
            if c.alignment.wrap_text is not True:
                c.alignment = Alignment(vertical="top")

    # flood input dropdown
    dv = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
    ws.add_data_validation(dv)
    fc = get_column_letter(metric_cols["flood_level"] + 1)
    dv.add(f"{fc}{first}:{fc}{last}")
    # seismic dropdown
    dv2 = DataValidation(type="list", formula1='"II,III,IV,V"', allow_blank=True)
    ws.add_data_validation(dv2)
    sc = get_column_letter(metric_cols["seismic_zone"] + 1)
    dv2.add(f"{sc}{first}:{sc}{last}")

    rng = f"A{first}:{get_column_letter(rank_col + 3)}{last}"
    ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"PASS"'], fill=GREEN_F,
                                                  font=Font(color="065F46", bold=True)))
    ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"FAIL"'], fill=RED_F,
                                                  font=Font(color="991B1B", bold=True)))
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 34
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 12
    for c in range(5, rank_col + 4):
        ws.column_dimensions[get_column_letter(c)].width = 12
    ws.column_dimensions[get_column_letter(rank_col + 3)].width = 30
    ws.row_dimensions[1].height = 42
    ws.freeze_panes = "E3"

    # ---------------------------------------------------------------- Summary
    sm = wb.create_sheet("Summary", 0)
    sm.append(["Aptimizer Stage 1 (Site) — Validation Summary"])
    sm["A1"].font = Font(bold=True, size=14)
    sm.append([f"Generated {run_meta['run_at']} · {len(plots)} plots · fill the yellow cells on "
               f"'Comparison' and this sheet updates automatically."])
    sm.append([])
    sm.append(["Metric", "Tolerance", "Reference", "Plots measured", "PASS", "FAIL", "Pass rate"])
    for c in sm[4]:
        c.fill, c.font, c.border = HEAD, WHITE_B, THIN
    for key, label, kind, tol, unit, ref, how in METRICS:
        R = get_column_letter(metric_cols[key] + 3)
        span = f"Comparison!{R}{first}:{R}{last}"
        tol_txt = ("exact match" if kind == "exact" else "history rule" if kind == "flood" else
                   f"±{tol * 100:.0f}%" if kind == "rel" else f"±{tol:g} {unit}")
        r = sm.max_row + 1
        sm.append([label, tol_txt, ref,
                   f'=COUNTIF({span},"PASS")+COUNTIF({span},"FAIL")',
                   f'=COUNTIF({span},"PASS")', f'=COUNTIF({span},"FAIL")',
                   f'=IF(D{r}=0,"—",E{r}/D{r})'])
        sm.cell(row=r, column=7).number_format = "0%"
    tot = sm.max_row + 1
    sm.append(["All checks", "", "", f"=SUM(D5:D{tot - 1})", f"=SUM(E5:E{tot - 1})",
               f"=SUM(F5:F{tot - 1})", f'=IF(D{tot}=0,"—",E{tot}/D{tot})'])
    sm.cell(row=tot, column=7).number_format = "0%"
    for c in sm[tot]:
        c.font = BOLD
    sm.append([])
    S = get_column_letter(rank_col + 1)
    E = get_column_letter(rank_col + 2)
    r = sm.max_row + 1
    sm.append(["Suitability vs expert ranking (Spearman ρ)", "≥ 0.7 strong", "Independent expert, blind to Aptimizer",
               f'=COUNT(Comparison!{E}{first}:{E}{last})', "", "",
               f'=IF(COUNT(Comparison!{E}{first}:{E}{last})<3,"needs ≥3 expert ranks",'
               f'ROUND(CORREL(Comparison!{S}{first}:{S}{last},Comparison!{E}{first}:{E}{last}),2))'])
    sm.cell(row=r, column=1).font = BOLD
    sm.append([])
    sm.append(["How to read this: PASS/FAIL only counts plots whose 'Measured (you)' cell is filled. "
               "Report the pass rate per metric with its tolerance, and keep the known limits from the "
               "Method sheet next to it."])
    sm.merge_cells(start_row=sm.max_row, start_column=1, end_row=sm.max_row, end_column=7)
    sm.cell(row=sm.max_row, column=1).alignment = WRAP
    sm.row_dimensions[sm.max_row].height = 45
    sm.conditional_formatting.add(f"G5:G{tot}", CellIsRule(operator="greaterThanOrEqual", formula=["0.8"],
                                                           fill=GREEN_F))
    sm.conditional_formatting.add(f"G5:G{tot}", CellIsRule(operator="lessThan", formula=["0.8"], fill=RED_F))
    for c, w in zip("ABCDEFG", (40, 16, 36, 15, 9, 9, 11)):
        sm.column_dimensions[c].width = w
    for row in sm.iter_rows(min_row=5, max_row=tot):
        for c in row:
            c.border = THIN

    # ---------------------------------------------------------------- Aptimizer_Detail
    dt_ = wb.create_sheet("Aptimizer_Detail")
    cols = [("Plot ID", lambda p, x: p["id"]), ("Location", lambda p, x: p["location"]),
            ("Lat", lambda p, x: p["lat"]), ("Lng", lambda p, x: p["lng"]),
            ("Boundary", lambda p, x: x["boundary_source"]), ("Vertices", lambda p, x: x["vertices"]),
            ("Area (m²)", lambda p, x: x["area_sqm"]), ("Recorded area (m²)", lambda p, x: p["area"]),
            ("GIS run (s)", lambda p, x: x["secs"]),
            ("Nearest road (m)", lambda p, x: x["nearest_road_m"]),
            ("Nearest road type", lambda p, x: x["nearest_road_kind"]),
            ("Nearest public road name", lambda p, x: x.get("nearest_road_name")),
            ("Nearest internal/service road (m)", lambda p, x: x.get("nearest_internal_road_m")),
            ("Your nearest road (sheet)", lambda p, x: p["road_name"]),
            ("Roads within 100 m", lambda p, x: x["roads_within_100m"]),
            ("Widest road nearby (m, by OSM class)", lambda p, x: x["widest_road_m"]),
            ("Nearest transit (m)", lambda p, x: x["nearest_transit_m"]),
            ("Access score", lambda p, x: x["access_score"]),
            ("Nearest water (m)", lambda p, x: x["nearest_water_m"]),
            ("Water type", lambda p, x: x["nearest_water_label"]),
            ("Your water note (sheet)", lambda p, x: p["water_note"]),
            ("Flood level", lambda p, x: x["flood_level"]), ("Flood score", lambda p, x: x["flood_score"]),
            ("Nearest storm drain (m)", lambda p, x: x.get("nearest_drain_m")),
            ("Elevation vs surroundings (m)", lambda p, x: x["elev_delta_m"]),
            ("Min plinth (m)", lambda p, x: x["plinth_m"]),
            ("Elev mean (m)", lambda p, x: x["elev_mean_m"]), ("Elev min (m)", lambda p, x: x["elev_min_m"]),
            ("Elev max (m)", lambda p, x: x["elev_max_m"]), ("Relief (m)", lambda p, x: x["relief_m"]),
            ("Slope (%)", lambda p, x: x["slope_pct"]), ("Slope class", lambda p, x: x["slope_class"]),
            ("Seismic zone", lambda p, x: x["seismic_zone"]), ("Surface shaking, screening (g)", lambda p, x: x["pga_surface_g"]),
            ("Wind Vb (m/s)", lambda p, x: x["wind_vb_ms"]), ("Prevailing wind", lambda p, x: x["wind_prevailing"]),
            ("Sunrise 21 Mar", lambda p, x: x["sunrise_mar"]), ("Sunset 21 Mar", lambda p, x: x["sunset_mar"]),
            ("Sunrise 21 Jun", lambda p, x: x["sunrise_jun"]), ("Sunset 21 Jun", lambda p, x: x["sunset_jun"]),
            ("Sunrise 21 Dec", lambda p, x: x["sunrise_dec"]), ("Sunset 21 Dec", lambda p, x: x["sunset_dec"]),
            ("Max sun elev 21 Mar (°)", lambda p, x: x["noon_elev_mar"]),
            ("Max sun elev 21 Jun (°)", lambda p, x: x["noon_elev_jun"]),
            ("Max sun elev 21 Dec (°)", lambda p, x: x["noon_elev_dec"]),
            ("Insolation (kWh/m²/yr)", lambda p, x: x["ghi_kwh_m2_yr"]),
            ("Solar yield (kWh/kWp/yr)", lambda p, x: x["specific_yield"]),
            ("GHI (kWh/m²/day)", lambda p, x: x.get("ghi_day")),
            ("Mean wind 10 m (m/s)", lambda p, x: x.get("wind_mean_ms")),
            ("Buildings in radius", lambda p, x: x["buildings"]), ("Green areas", lambda p, x: x["green"]),
            ("Water features", lambda p, x: x["water_features"]), ("Transit stops", lambda p, x: x["transit"]),
            ("Suitability", lambda p, x: x["suitability"]), ("Grade", lambda p, x: x["grade"]),
            ("Buildable", lambda p, x: x["buildable"]), ("Buildability flags", lambda p, x: x["flags"]),
            ("Your land use (sheet)", lambda p, x: p["landuse"]),
            ("Layout: ran", lambda p, x: x["layout"].get("ran")),
            ("Layout: road width used (m)", lambda p, x: x["layout"].get("road_width_assumed")),
            ("Road width source", lambda p, x: x["layout"].get("road_width_source")),
            ("Rec. setback front (m)", lambda p, x: x["layout"].get("setback_front")),
            ("Rec. setback rear (m)", lambda p, x: x["layout"].get("setback_rear")),
            ("Rec. setback side (m)", lambda p, x: x["layout"].get("setback_side")),
            ("Rec. floors", lambda p, x: x["layout"].get("rec_floors")),
            ("Rec. height (m)", lambda p, x: x["layout"].get("rec_height_m")),
            ("FAR cap (indicative)", lambda p, x: x["layout"].get("far_cap")),
            ("Envelope (m²)", lambda p, x: x["layout"].get("envelope_sqm")),
            ("Packable land (m²)", lambda p, x: x["layout"].get("packable_sqm")),
            ("Towers packed", lambda p, x: x["layout"].get("towers")),
            ("Units", lambda p, x: x["layout"].get("units")),
            ("Achieved FAR", lambda p, x: x["layout"].get("achieved_far")),
            ("Coverage (%)", lambda p, x: x["layout"].get("coverage_pct")),
            ("Constraints satisfied", lambda p, x: x["layout"].get("feasible")),
            ("Small-plot fallback", lambda p, x: x["layout"].get("fallback")),
            ("Layout run (s)", lambda p, x: x["layout"].get("secs")),
            ("Layout note", lambda p, x: x["layout"].get("note")),
            ("Overpass source", lambda p, x: x["overpass_source"]),
            ("Elevation source", lambda p, x: x["elevation_source"]),
            ("GIS rules version", lambda p, x: x["rules_version"]),
            ("Generated at (UTC)", lambda p, x: x["generated_at"])]
    dt_.append([c[0] for c in cols])
    for c in dt_[1]:
        c.fill, c.font, c.alignment, c.border = HEAD, WHITE_B, CENTER, THIN
    time_cols = {i for i, c in enumerate(cols, 1) if c[0].startswith(("Sunrise", "Sunset"))}
    for p, x in zip(plots, results):
        dt_.append([f(p, x) for _, f in cols])
        rr = dt_.max_row
        for i in time_cols:
            _fmt_time(dt_.cell(row=rr, column=i))
        for c in dt_[rr]:
            c.border = THIN
    for i in range(1, len(cols) + 1):
        dt_.column_dimensions[get_column_letter(i)].width = 14
    for name in ("Location", "Buildability flags", "Layout note", "Your nearest road (sheet)",
                 "Your water note (sheet)", "Small-plot fallback", "Overpass source", "Elevation source"):
        i = [c[0] for c in cols].index(name) + 1
        dt_.column_dimensions[get_column_letter(i)].width = 34
    dt_.row_dimensions[1].height = 45
    dt_.freeze_panes = "C2"

    # ---------------------------------------------------------------- Method
    me = wb.create_sheet("Method")
    me.append(["How each check works"])
    me["A1"].font = Font(bold=True, size=14)
    me.append([])
    me.append(["Metric", "Tolerance", "Reference (independent of Aptimizer)", "How to measure the yellow cell"])
    for c in me[3]:
        c.fill, c.font, c.border = HEAD, WHITE_B, THIN
    for key, label, kind, tol, unit, ref, how in METRICS:
        tol_txt = ("exact" if kind in ("exact", "flood") else
                   f"±{tol * 100:.0f}%" if kind == "rel" else f"±{tol:g} {unit}")
        me.append([label, tol_txt, ref, how])
    me.append(["Suitability ranking", "Spearman ρ ≥ 0.7", "Faculty / practising civil engineer",
               "Give an expert the 10 plots (map + your notes, NOT Aptimizer's scores) and ask them to rank "
               "1 = most suitable for residential development. Enter in 'Expert rank (you)'."])
    for row in me.iter_rows(min_row=4, max_row=me.max_row):
        for c in row:
            c.alignment, c.border = WRAP, THIN
    me.append([])
    notes = [
        "Known limits (state these alongside the results):",
        "• Elevation comes from the Copernicus 90 m DEM (via Open-Meteo); slope on plots under ~1 ha is coarse.",
        "• Roads, water and buildings come from OpenStreetMap, which can be incomplete; OSM has no road widths "
        "here, so 'widest road' is inferred from the road class, not measured.",
        "• Nearest-road distances are measured to the road centreline; nearest-water to the water body's edge.",
        "• Seismic zone and design wind speed are looked up by city (IS 1893 / IS 875-3), so every plot in one "
        "city gets the same value. Test plots in other cities to exercise them.",
        "• Prevailing wind and mean wind speed are regional climatology values, not site measurements; "
        "NASA POWER is a coarse grid, so treat both as a city-level check.",
        "• FAR caps and ground coverage are indicative defaults; Bengaluru's binding rules are the BBMP RMP-2015 "
        "zoning regulations. Setback minimums follow NBC 2016 Part 3.",
        "• Plots with an APPROX boundary were modelled as a square of the recorded area: location checks are "
        "valid, area and layout results are not. Supply KML boundaries to fix this.",
        "• Land use is not an Aptimizer output; the sheet's land-use column checks OpenStreetMap/Bhuvan, not Aptimizer.",
    ]
    for n in notes:
        me.append([n])
        me.merge_cells(start_row=me.max_row, start_column=1, end_row=me.max_row, end_column=4)
        me.cell(row=me.max_row, column=1).alignment = WRAP
        me.row_dimensions[me.max_row].height = 30
    me.cell(row=me.max_row - len(notes) + 1, column=1).font = BOLD
    for c, w in zip("ABCD", (34, 14, 36, 90)):
        me.column_dimensions[c].width = w

    # ---------------------------------------------------------------- Run_Info
    ri = wb.create_sheet("Run_Info")
    for k, v in run_meta.items():
        ri.append([k, v])
    ri.column_dimensions["A"].width = 32
    ri.column_dimensions["B"].width = 90
    for row in ri.iter_rows():
        row[0].font = BOLD
        row[1].alignment = WRAP

    wb.save(out_path)


def read_carried(path):
    """Ground truth already typed into a previous validation workbook, by plot id."""
    if not path:
        return {}
    wb = load_workbook(os.path.expanduser(path))
    if "Comparison" not in wb.sheetnames:
        return {}
    ws = wb["Comparison"]
    titles = {}
    for c in ws[1]:
        if c.value:
            titles[c.column] = str(c.value)
    out = {}
    for r in range(3, ws.max_row + 1):
        pid = ws.cell(row=r, column=1).value
        if not pid:
            continue
        d = {}
        for key, label, *_ in METRICS:
            col = next((k for k, t in titles.items() if t.startswith(label)), None)
            if col:
                d[key] = ws.cell(row=r, column=col + 1).value
        er = next((k for k, t in titles.items() if t.startswith("Expert rank")), None)
        nt = next((k for k, t in titles.items() if t == "Notes"), None)
        d["expert_rank"] = ws.cell(row=r, column=er).value if er else None
        d["notes"] = ws.cell(row=r, column=nt).value if nt else None
        out[str(pid).strip()] = d
    return out

# ------------------------------------------------------------------------- main

async def main_async(args):
    global RADIUS_FOR_WATER
    RADIUS_FOR_WATER = args.radius
    plots = read_plots(args.input)
    if not plots:
        raise SystemExit("No plots with Plot ID, Latitude and Longitude found.")
    boundaries = load_boundaries(args.kml, [p["id"] for p in plots])
    carried = read_carried(args.merge)
    results = []
    for p in plots:
        if p["id"] in boundaries:
            coords, fname = boundaries[p["id"]]
            b = (coords, f"KML: {fname}")
        else:
            b = (square_around(p["lat"], p["lng"], p["area"]), "APPROX square of recorded area")
        print(f"  {p['id']}  {p['location'][:48]:<48} … ", end="", flush=True)
        try:
            res = await run_plot(p, b, args.radius)
            src = res["overpass_source"] if res.get("map_ok") else "FAILED — rerun later"
            print(f"done in {res['secs']} s  (suitability {res['suitability']}, map data: {src})")
            if res.get("map_ok") and res["overpass_source"] != "cache":
                await asyncio.sleep(3)    # be polite to the public mirrors between live fetches
        except Exception as exc:  # one plot failing must not lose the other nine
            print(f"FAILED: {exc}")
            res = {k: None for k, *_ in METRICS}
            res.update({"secs": None, "boundary_source": b[1], "area_sqm": round(polygon_area_sqm(b[0]), 1),
                        "vertices": len(b[0]), "suitability": None, "layout": {"note": f"GIS failed: {exc}"}})
            for k in ("nearest_road_kind", "roads_within_100m", "widest_road_m", "nearest_transit_m",
                      "access_score", "nearest_water_label", "flood_score", "elev_delta_m", "plinth_m",
                      "elev_min_m", "elev_max_m", "relief_m", "slope_class", "pga_surface_g", "wind_prevailing",
                      "sunrise_jun", "sunset_jun", "sunrise_dec", "sunset_dec", "noon_elev_mar", "noon_elev_dec",
                      "ghi_kwh_m2_yr", "ghi_day", "wind_mean_ms", "buildings", "green", "water_features", "transit", "grade", "buildable",
                      "flags", "overpass_source", "overpass_ok", "elevation_source", "rules_version",
                      "generated_at"):
                res.setdefault(k, None)
        results.append(res)

    run_meta = {
        "run_at": datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
        "input_workbook": os.path.expanduser(args.input),
        "kml_boundaries": f"{len(boundaries)} of {len(plots)} plots" + (f" from {args.kml}" if args.kml else ""),
        "study_radius_m": args.radius,
        "site_layout_engine": getattr(siteplan, "ENGINE_VERSION", "n/a"),
        "gis_rules_version": results[0].get("rules_version") if results else "n/a",
        "map_data": "OpenStreetMap via Overpass (raced mirrors; cached 7 days in backend/.gis_cache)",
        "elevation_data": "Copernicus 90 m DEM via Open-Meteo, Open-Elevation as fallback",
        "sun_and_solar": "NOAA solar position equations; clear-sky insolation model in gis.py",
        "codes": "IS 1893 (Part 1):2016 seismic zones, IS 875 (Part 3):2015 basic wind speed, NBC 2016 Part 3 setbacks",
        "layout_inputs": (f"Recommended controls at the sheet's 'Measured road width (m)' where given, else an assumed "
                          f"{DEFAULT_ROAD_WIDTH_M:g} m abutting road; layout skipped "
                          f"above {LAYOUT_MAX_SQM / 10_000:.0f} ha"),
    }
    out = os.path.expanduser(args.output) if args.output else \
        str(Path(os.path.expanduser(args.input)).with_name(Path(args.input).stem + "_Aptimizer_Validation.xlsx"))
    write_workbook(out, plots, results, run_meta, carried)
    print(f"\nWrote {out}")


def main():
    ap = argparse.ArgumentParser(description="Validate Aptimizer's Site engines against ground truth.")
    ap.add_argument("--input", required=True, help="GIS testing workbook (sheet Plot_Master)")
    ap.add_argument("--kml", help="Folder of P01.kml / P01.geojson files, or one KML with placemarks named P01…")
    ap.add_argument("--merge", help="Previous validation workbook to carry typed ground truth from")
    ap.add_argument("--output", help="Output .xlsx (default: next to the input)")
    ap.add_argument("--radius", type=int, default=500, help="GIS study radius in metres (default 500)")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
