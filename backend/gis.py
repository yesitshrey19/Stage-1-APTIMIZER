"""GIS & site intelligence: Overpass feature detection, Open-Elevation terrain,
flood / wind / sun-path / accessibility analysis, suitability and buildability scoring.

All functions read the plot polygon already stored on the project document
(project["plot"]["coordinates"]) — there is no separate plot model here.
"""
import asyncio
import datetime as _dt
import logging
import math
import re
from datetime import datetime, timezone

import requests
import time

import iscodes

# All are raced at once and the first good answer wins, so one mirror being down (as
# overpass-api.de, private.coffee and mail.ru all were on 9 Oct 2026, while the French
# mirror answered in under 4 s) costs nothing.
OVERPASS_ENDPOINTS = [
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]
ELEVATION_URL = "https://api.open-elevation.com/api/v1/lookup"
# Open-Meteo serves the Copernicus 90 m DEM and answers in well under a second, where
# Open-Elevation regularly takes tens of seconds; it is tried first, Open-Elevation after.
OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
OVERPASS_TIMEOUT_S = 25
ELEVATION_TIMEOUT_S = 12
logger = logging.getLogger(__name__)
EARTH_R = 6371000.0

# The rules that turn raw OSM and elevation data into what is on screen: which tags count
# as water, how the flood score is weighted, what the design response says. A stored
# analysis is only reusable while those rules are unchanged, so EVERY edit to them must
# bump this number. A stale result is then reported as out of date and re-run, instead of
# sitting on the project disagreeing with the map drawn beside it.
#
#   1 — drains, ditches and sewage are no longer water; waterways draw as centrelines.
# 2: water/park/transit no longer truncated by the building count, multipolygon lakes and
#    parks read from relations, and distances measured edge-to-edge with containment.
# 3: enclosed water areas under MIN_WATER_AREA_SQM (ornamental pools, fountains) are not
#    treated as water bodies, so they no longer drive the flood score.
# 4: storm-water drains (rajakaluves) are fetched as their own layer and add to the flood
#    score by proximity -- in Bengaluru they, not lakes, are what overflows in heavy rain.
# 5: "nearest road" is the nearest PUBLIC road. Service roads (campus driveways, parking
#    aisles) and roads tagged access=private no longer count as site access; they made
#    every campus plot read 0 m from a road.
# 6: IS 875-3 k2 factors corrected to Table 2 of the 2015 code (terrain 2: 1.00 at 10 m),
#    and basic wind speeds to Annex A as amended in 2020 (Delhi 50 m/s).
# 7: IS 1893 PGA reported as Z (Cl. 3.28) with the code's soil spectral ratios 1/1.36/1.67;
#    liquefaction flagged in every zone; Kd = 1.0 in the cyclone belt (IS 875-3 Cl. 7.2.1).
# 8: plinth guidance cites NBC Part 3 Cl. 12.1.1 (the 'Part 9 Table 4' citation was wrong).
# 9: sun times from the full NOAA algorithm (to the second); drain-like canals (culverts,
#    named drains/nalas, <= 5 m wide) are storm drains, not water bodies.
# 10: site climate (monthly irradiation, optimal-tilt gain, 10 m wind) from NASA POWER, with
#     the clear-sky model and regional wind table as the fallback.
# 11: slope flagged low-confidence on plots under 2 ha (below the 90 m elevation grid).
# 12: elevation retries rate limits and falls back to NASA SRTM 30 m, so hosted runs no
#     longer come back with "elevation data unavailable".
# 13: IS 875-3 check (9 Oct): six cities beside 50 m/s Annex A cities raised 47 -> 50 m/s,
#     Guntur and Vadodara added to the cyclone belt, k3 flagged on slopes over 3 degrees (Cl. 6.3.3.1).
ANALYSIS_RULES_VERSION = 13

# A fountain or ornamental pool is tagged natural=water like a lake, and on its own was
# enough to mark a plot "high" flood risk. Ponds and tanks that matter for drainage are
# larger than this; waterways (rivers, streams, canals) are lines and are always kept.
MIN_WATER_AREA_SQM = 500.0


# ------------------------------------------------------------------ geometry
def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def centroid(coords):
    return [sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords)]


def bbox(coords, pad_m=0.0):
    lats = [c[0] for c in coords]
    lngs = [c[1] for c in coords]
    lat0 = sum(lats) / len(lats)
    dlat = pad_m / 110540.0
    dlng = pad_m / (111320.0 * max(math.cos(math.radians(lat0)), 0.1))
    return (min(lats) - dlat, min(lngs) - dlng, max(lats) + dlat, max(lngs) + dlng)


def point_in_polygon(pt, coords):
    x, y = pt[1], pt[0]
    inside = False
    n = len(coords)
    for i in range(n):
        y1, x1 = coords[i][0], coords[i][1]
        y2, x2 = coords[(i + 1) % n][0], coords[(i + 1) % n][1]
        if (y1 > y) != (y2 > y):
            xint = x1 + (y - y1) * (x2 - x1) / ((y2 - y1) or 1e-12)
            if x < xint:
                inside = not inside
    return inside


def distance_to_polygon(pt, coords):
    """Distance in metres from a point to the polygon BOUNDARY (0 if inside).

    Measured to each edge, not to the vertices. Vertex-only distance reports a road
    running along the middle of a long edge as being as far away as the corner, which
    then understates road access and flood risk on any plot with long sides.
    """
    if point_in_polygon(pt, coords):
        return 0.0
    lat0 = sum(c[0] for c in coords) / len(coords)
    k = math.cos(math.radians(lat0))

    def xy(c):
        return (c[1] * 111320.0 * k, c[0] * 110540.0)

    px, py = xy(pt)
    best = float("inf")
    n = len(coords)
    for i in range(n):
        ax, ay = xy(coords[i])
        bx, by = xy(coords[(i + 1) % n])
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        # Project the point onto the segment, clamped to its ends.
        t = 0.0 if seg <= 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg))
        best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def _point_to_path_m(pt, path, closed):
    """Distance in metres from a point to a polyline (or closed ring), edge by edge."""
    if not path:
        return float("inf")
    if len(path) == 1:
        lat0 = (pt[0] + path[0][0]) / 2
        k = math.cos(math.radians(lat0))
        return math.hypot((pt[1] - path[0][1]) * 111320.0 * k, (pt[0] - path[0][0]) * 110540.0)
    lat0 = pt[0]
    k = math.cos(math.radians(lat0))
    px, py = pt[1] * 111320.0 * k, pt[0] * 110540.0
    best = float("inf")
    n = len(path)
    for i in range(n if closed else n - 1):
        ax, ay = path[i][1] * 111320.0 * k, path[i][0] * 110540.0
        bx, by = path[(i + 1) % n][1] * 111320.0 * k, path[(i + 1) % n][0] * 110540.0
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        t = 0.0 if seg <= 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg))
        best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def feature_distance(geometry, coords, role="area"):
    """Shortest distance in metres between a mapped feature and the plot.

    Measured both ways -- feature vertices to the plot's edges and plot vertices to the
    feature's edges -- so a long straight road or lake shore whose vertices happen to be
    far apart is not reported as further away than it is. Zero when they touch, overlap,
    or when the plot sits inside an area feature (a plot within a park or a lake bed).
    """
    if not geometry:
        return None
    d = min(distance_to_polygon(p, coords) for p in geometry)
    if d == 0.0:
        return 0.0
    if role == "area" and len(geometry) >= 3:
        c = centroid(coords)
        if any(point_in_polygon(v, geometry) for v in list(coords) + [c]):
            return 0.0
    d = min(d, min(_point_to_path_m(v, geometry, closed=(role == "area")) for v in coords))
    return round(d, 1)


# ------------------------------------------------------------------ overpass
# Output caps per group. One shared cap let a dense neighbourhood's buildings fill the whole
# response, so lakes, parks and transit stops -- the features flood risk and access depend
# on -- were silently cut. Context features are few and always returned in full.
OVERPASS_ROAD_CAP = 2000
OVERPASS_BUILDING_CAP = 1200


def _overpass_query(bb, radius_m):
    s, w, n, e = bb
    box = f"{s},{w},{n},{e}"
    return f"""[out:json][timeout:{OVERPASS_TIMEOUT_S}];
(
  way["leisure"~"^(park|garden|pitch|playground)$"]({box});
  way["landuse"~"^(grass|forest|meadow|recreation_ground|village_green|orchard)$"]({box});
  way["natural"~"^(wood|scrub|water|wetland)$"]({box});
  way["waterway"~"^(river|stream|canal|drain|ditch)$"]({box});
  relation["natural"~"^(water|wetland)$"]({box});
  relation["leisure"="park"]({box});
  node["public_transport"="station"]({box});
  node["highway"="bus_stop"]({box});
  node["railway"~"^(station|halt|subway_entrance)$"]({box});
)->.ctx;
.ctx out geom;
way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|service|living_street)$"]({box})->.rd;
.rd out geom {OVERPASS_ROAD_CAP};
way["building"]({box})->.bd;
.bd out geom {OVERPASS_BUILDING_CAP};"""


# Storm drains, ditches and sewage channels are built infrastructure, not water bodies.
# In a built-up block they run under and beside the buildings, so treating them as
# "water" put a water feature on top of developed land and let a storm drain drive the
# flood score and the design response. They are dropped entirely: not drawn, not
# counted, not scored. (The Overpass query no longer asks for drains either; this guard
# also catches a drain or treatment pond reached through the `natural=water` clause.)
ENGINEERED_WATERWAYS = {"drain", "ditch", "wastewater", "sewage", "sewer", "storm_drain"}
ENGINEERED_WATER_TYPES = {"wastewater", "sewage", "sewer", "drain", "ditch",
                          "stormwater", "storm_water"}


# Storm-water drains carry rain run-off and overflow in a cloudburst; sewage and
# wastewater channels do not drive flooding the same way and stay excluded.
SEWAGE_TYPES = {"wastewater", "sewage", "sewer"}


# Indian city storm drains are often mapped as waterway=canal. A "canal" that runs in a
# culvert, is named as a drain (drain / nala / nallah / storm water), or is no wider than a
# street drain is treated as the storm drain it is -- in validation a culvert named "drain
# path" had been reported as a water body 12.6 m from a Chennai plot.
_DRAIN_NAME = re.compile(r"\b(drain|drainage|nala|nalla|nallah|nullah|storm\s*water|sewer)\b", re.I)
DRAIN_CANAL_MAX_WIDTH_M = 5.0


def _drain_like_canal(tags):
    if str(tags.get("waterway") or "").lower() != "canal":
        return False
    if str(tags.get("tunnel") or "").lower() in {"culvert", "yes", "flooded"}:
        return True
    if _DRAIN_NAME.search(str(tags.get("name") or "")):
        return True
    try:
        width = float(str(tags.get("width") or "").split()[0])
    except (ValueError, IndexError):
        return False
    return width <= DRAIN_CANAL_MAX_WIDTH_M


def _is_storm_drain(tags):
    waterway = str(tags.get("waterway") or "").lower()
    water = str(tags.get("water") or "").lower()
    if waterway in SEWAGE_TYPES or water in SEWAGE_TYPES or tags.get("usage") == "sewage":
        return False
    return (waterway in {"drain", "ditch", "storm_drain"}
            or water in {"drain", "ditch", "stormwater", "storm_water"}
            or _drain_like_canal(tags))


def _is_engineered_water(tags):
    """True for a drainage or sewage conveyance tagged as water on OSM."""
    if str(tags.get("waterway") or "").lower() in ENGINEERED_WATERWAYS:
        return True
    return str(tags.get("water") or "").lower() in ENGINEERED_WATER_TYPES


def _classify(tags):
    if "building" in tags:
        return "buildings"
    if "highway" in tags and tags.get("highway") not in ("bus_stop",):
        return "roads"
    if tags.get("highway") == "bus_stop" or tags.get("public_transport") == "station" or "railway" in tags:
        return "transit"
    if tags.get("natural") in ("water", "wetland") or "waterway" in tags:
        if _is_storm_drain(tags):
            return "drains"            # its own layer: never a "water body", but a flood factor
        return None if _is_engineered_water(tags) else "water"
    if tags.get("leisure") or tags.get("landuse") or tags.get("natural") in ("wood", "scrub"):
        return "green"
    return None


# OSM puts areas and centrelines in the same tag space. `natural=water` is a CLOSED RING
# (a lake, a pond, a wetland); `waterway=stream|river|canal|drain` is a CENTRELINE. The
# two cannot be drawn the same way: a map library closes whatever ring it is handed, so
# painting a centreline as an area turns a storm drain running the length of a built-up
# block into a filled "water body" lying across every plot it passes. Which renderer a
# feature gets is therefore a property of its geometry, not of the layer it sits in.
LINE_TAGS = ("waterway", "highway")


def _geometry_role(tags, element_type="way"):
    """How the geometry must be drawn: a node is a point, a waterway or road is a line,
    everything else this module fetches is an area."""
    if element_type == "node":
        return "point"
    if any(tags.get(k) for k in LINE_TAGS):
        return "line"
    return "area"


def _cap_geometry(points, role, limit=240):
    """Bound the stored vertex count WITHOUT changing what the shape is.

    Truncating a ring to its first N vertices and letting the map close it silently
    redraws the feature somewhere else: a 400-node lake ring cut at 60 becomes a
    different polygon spanning ground the lake does not cover, and an open centreline
    cut short stops describing the channel it came from. Decimate uniformly instead, so
    the first vertex, the last vertex and the overall extent all survive.
    """
    n = len(points)
    if n <= limit or limit < 3:
        return points
    if role == "area" and points[0] == points[-1]:
        body = points[:-1]
        step = (len(body) - 1) / float(limit - 2)
        picked = [body[int(round(i * step))] for i in range(limit - 1)]
        return picked + [points[-1]]
    step = (n - 1) / float(limit - 1)
    return [points[int(round(i * step))] for i in range(limit)]


# Overpass answers are cached by query. The public mirrors take 8-20 s for a 500 m study
# area, so the same site is fetched once: re-runs, a changed setback or a backend restart
# read the stored answer, and `prefetch_overpass` lets the workspace start the download
# before the user asks for the analysis. Map data changes slowly; a week is fresh enough.
import hashlib
import json as _json
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gis_cache")
_CACHE_TTL_S = 7 * 24 * 3600
_cache_lock = threading.Lock()
_inflight: dict = {}


def _cache_path(query):
    return os.path.join(_CACHE_DIR, hashlib.sha1(query.encode()).hexdigest() + ".json")


def _cache_read(query, max_age_s=_CACHE_TTL_S):
    path = _cache_path(query)
    try:
        if max_age_s is not None and time.time() - os.path.getmtime(path) > max_age_s:
            return None
        with open(path, encoding="utf-8") as fh:
            return _json.load(fh)
    except (OSError, ValueError):
        return None


def _cache_write(query, payload):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        tmp = _cache_path(query) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            _json.dump(payload, fh)
        os.replace(tmp, _cache_path(query))
    except OSError:
        pass                     # a cache that cannot be written is only a slower run


def _race_mirrors(query):
    """Query every Overpass mirror at once and keep the first good answer."""
    def ask(url):
        r = requests.post(url, data={"data": query}, timeout=OVERPASS_TIMEOUT_S + 5,
                          headers={"User-Agent": "Aptimizer/1.0 (site-intelligence)"})
        if r.status_code != 200:
            raise RuntimeError(f"{url} -> HTTP {r.status_code}")
        return url, r.json()

    errors = []
    pool = ThreadPoolExecutor(max_workers=len(OVERPASS_ENDPOINTS))
    try:
        futures = [pool.submit(ask, url) for url in OVERPASS_ENDPOINTS]
        for fut in as_completed(futures):
            try:
                return fut.result()
            except Exception as exc:  # network / json failure -> wait for another mirror
                errors.append(str(exc))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("; ".join(errors) or "all Overpass endpoints failed")


def _overpass_raw(query):
    """(endpoint, data) for a query: from the cache, from a fetch already running for the
    same query, or from a fresh race of the mirrors."""
    cached = _cache_read(query)
    if cached is not None:
        return "cache", cached
    with _cache_lock:
        fut = _inflight.get(query)
        owner = fut is None
        if owner:
            fut = Future()
            _inflight[query] = fut
    if not owner:
        return fut.result()
    try:
        url, data = _race_mirrors(query)
        _cache_write(query, data)
        fut.set_result((url, data))
        return url, data
    except Exception as exc:
        # Every mirror down: an older copy of the same site's map data beats an analysis
        # with no roads or lakes at all. Map data changes slowly.
        stale = _cache_read(query, max_age_s=None)
        if stale is not None:
            fut.set_result(("cache (older copy; map servers unavailable)", stale))
            return "cache (older copy; map servers unavailable)", stale
        fut.set_exception(exc)
        raise
    finally:
        with _cache_lock:
            _inflight.pop(query, None)


def prefetch_overpass(coords, radius_m):
    """Start fetching the Overpass data for this site in the background, if it is not
    cached or already being fetched. Returns at once."""
    if len(coords) < 3:
        return False
    query = _overpass_query(bbox(coords, radius_m), radius_m)
    if _cache_read(query) is not None or query in _inflight:
        return False
    threading.Thread(target=lambda: _swallow(_overpass_raw, query), daemon=True).start()
    return True


def _swallow(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass


def fetch_overpass(coords, radius_m):
    query = _overpass_query(bbox(coords, radius_m), radius_m)
    try:
        url, data = _overpass_raw(query)
    except Exception as exc:
        return {"buildings": [], "roads": [], "green": [], "water": [], "transit": [], "drains": []}, {
            "ok": False, "error": str(exc)}
    # Whether OpenStreetMap had more buildings/roads than the query returns, so the counts
    # shown are lower bounds rather than totals.
    els = data.get("elements", [])
    n_bld = sum(1 for e in els if "building" in (e.get("tags") or {}))
    n_rd = sum(1 for e in els if "highway" in (e.get("tags") or {}) and e.get("type") == "way")
    capped = {"buildings": n_bld >= OVERPASS_BUILDING_CAP, "roads": n_rd >= OVERPASS_ROAD_CAP}
    return _parse_overpass(data, coords, radius_m), {"ok": True, "endpoint": url, "capped": capped}


def _ring_area_sqm(ring):
    lat0 = sum(p[0] for p in ring) / len(ring)
    k = math.cos(math.radians(lat0))
    pts = [(p[1] * 111320.0 * k, p[0] * 110540.0) for p in ring]
    return abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                   for i in range(len(pts)))) / 2.0


def _relation_rings(el):
    """Closed outer rings of a multipolygon relation, stitched from its member ways.

    Large lakes and parks are mapped as relations whose outline is split across several
    ways in no particular order; they used to be dropped, which is how a plot on Hebbal
    Lake came back with no water body near it.
    """
    pieces = [[[g["lat"], g["lon"]] for g in (m.get("geometry") or []) if g.get("lat") is not None]
              for m in el.get("members") or [] if m.get("type") == "way" and m.get("role", "outer") in ("outer", "")]
    pieces = [p for p in pieces if len(p) >= 2]
    rings = []
    while pieces:
        ring = pieces.pop(0)
        grown = True
        while ring[0] != ring[-1] and grown:
            grown = False
            for i, p in enumerate(pieces):
                if p[0] == ring[-1]:
                    ring += p[1:]
                elif p[-1] == ring[-1]:
                    ring += p[::-1][1:]
                elif p[-1] == ring[0]:
                    ring = p[:-1] + ring
                elif p[0] == ring[0]:
                    ring = p[::-1][:-1] + ring
                else:
                    continue
                pieces.pop(i)
                grown = True
                break
        if len(ring) >= 4:
            rings.append(ring[:-1] if ring[0] == ring[-1] else ring)
    return rings


def _parse_overpass(data, coords, radius_m):
    out = {"buildings": [], "roads": [], "green": [], "water": [], "transit": [], "drains": []}
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        cat = _classify(tags)
        if not cat:
            continue
        role = _geometry_role(tags, el.get("type") or "way")
        if el.get("type") == "node":
            shapes = [[[el["lat"], el["lon"]]]]
        elif el.get("type") == "relation":
            shapes = _relation_rings(el)
        else:
            shapes = [[[g["lat"], g["lon"]] for g in (el.get("geometry") or []) if g.get("lat")]]
        for idx, geometry in enumerate(shapes):
            if not geometry:
                continue
            if cat == "water" and role == "area" and len(geometry) >= 3 \
                    and _ring_area_sqm(geometry) < MIN_WATER_AREA_SQM:
                continue
            dist = feature_distance(geometry, coords, role)
            if dist is None or dist > radius_m * 1.6:
                continue
            _append_feature(out, cat, el, tags, role, geometry, dist, idx)
    for k in out:
        out[k].sort(key=lambda f: f["distance_m"])
        out[k] = out[k][:1000]
    return out


def _append_feature(out, cat, el, tags, role, geometry, dist, idx=0):
    item = {
        "id": str(el.get("id")) + (f"-{idx}" if idx else ""),
        "name": tags.get("name", ""),
        "kind": tags.get("building") or tags.get("highway") or tags.get("waterway")
        or tags.get("natural") or tags.get("leisure") or tags.get("landuse") or tags.get("railway") or cat,
        "geom": role,
        "geometry": _cap_geometry(geometry, role),
        "distance_m": dist,
        "on_plot": dist == 0.0,
    }
    if cat == "roads":
        item["road_width_m"] = _road_width(tags)
        item["lanes"] = tags.get("lanes", "")
        item["access"] = tags.get("access", "")
    out[cat].append(item)


ROAD_WIDTH = {"motorway": 24, "trunk": 18, "primary": 15, "secondary": 12,
              "tertiary": 9, "residential": 7.5, "unclassified": 6, "service": 5, "living_street": 5}


def _road_width(tags):
    if tags.get("width"):
        try:
            return float(str(tags["width"]).split()[0])
        except ValueError:
            pass
    return ROAD_WIDTH.get(tags.get("highway"), 6)


# ------------------------------------------------------------------ elevation
def _sample_points(coords):
    s, w, n, e = bbox(coords)
    grid, profile, ring = [], [], []
    steps = 5
    for i in range(steps):
        for j in range(steps):
            lat = s + (n - s) * (i / (steps - 1))
            lng = w + (e - w) * (j / (steps - 1))
            if point_in_polygon([lat, lng], coords):
                grid.append([round(lat, 6), round(lng, 6)])
    grid += [[round(c[0], 6), round(c[1], 6)] for c in coords]
    for k in range(11):
        profile.append([round(s + (n - s) * k / 10, 6), round(w + (e - w) * k / 10, 6)])
    rs, rw, rn, re = bbox(coords, 250)
    for lat, lng in [(rs, rw), (rs, (rw + re) / 2), (rs, re), ((rs + rn) / 2, rw),
                     ((rs + rn) / 2, re), (rn, rw), (rn, (rw + re) / 2), (rn, re)]:
        ring.append([round(lat, 6), round(lng, 6)])
    return grid, profile, ring


_http = requests.Session()         # keep-alive: repeat lookups skip the TLS handshake


OPENTOPODATA_URL = "https://api.opentopodata.org/v1/{dataset}"
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _get_with_retry(url, params, tries=3):
    """GET that waits and retries on rate-limit / server errors. Free elevation APIs answer a
    shared cloud address (a hosted server) with 429 far more often than a home connection."""
    last = None
    for attempt in range(tries):
        r = _http.get(url, params=params, timeout=ELEVATION_TIMEOUT_S)
        if r.status_code == 200:
            return r
        last = f"HTTP {r.status_code}"
        if r.status_code not in _RETRY_STATUS:
            break
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last or "no response")


def _elevation_chunk(chunk):
    r = _get_with_retry(OPEN_METEO_ELEVATION_URL, {
        "latitude": ",".join(f"{p[0]:.6f}" for p in chunk),
        "longitude": ",".join(f"{p[1]:.6f}" for p in chunk),
    })
    values = r.json().get("elevation") or []
    if len(values) != len(chunk) or any(v is None for v in values):
        raise RuntimeError("incomplete elevation response")
    return [float(v) for v in values]


def _fetch_elevation_open_meteo(points):
    chunks = [points[i:i + 100] for i in range(0, len(points), 100)]   # 100 points a call
    if len(chunks) == 1:
        return _elevation_chunk(chunks[0])
    with ThreadPoolExecutor(max_workers=min(len(chunks), 4)) as pool:
        return [v for part in pool.map(_elevation_chunk, chunks) for v in part]


def _fetch_elevation_opentopodata(points, dataset="srtm30m"):
    """NASA SRTM 30 m via OpenTopoData: 100 points a call, at most one call a second."""
    out = []
    for i in range(0, len(points), 100):
        if i:
            time.sleep(1.1)
        chunk = points[i:i + 100]
        r = _get_with_retry(OPENTOPODATA_URL.format(dataset=dataset),
                            {"locations": "|".join(f"{p[0]:.6f},{p[1]:.6f}" for p in chunk)})
        res = r.json().get("results") or []
        if len(res) != len(chunk) or any(x.get("elevation") is None for x in res):
            raise RuntimeError("incomplete elevation response")
        out += [float(x["elevation"]) for x in res]
    return out


def _fetch_elevation_open_elevation(points):
    out = []
    for i in range(0, len(points), 100):
        chunk = points[i:i + 100]
        r = _http.post(ELEVATION_URL, timeout=ELEVATION_TIMEOUT_S * 2,
                       json={"locations": [{"latitude": p[0], "longitude": p[1]} for p in chunk]})
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        res = r.json().get("results") or []
        if len(res) != len(chunk):
            raise RuntimeError("incomplete elevation response")
        out += [float(x.get("elevation") or 0) for x in res]
    return out


# Tried in order. Copernicus 90 m (Open-Meteo) is the primary source -- it matched official
# surveyed airport heights to within a metre in validation; NASA SRTM 30 m is the validated
# second source; Open-Elevation is the last resort.
ELEVATION_PROVIDERS = (
    ("open-meteo (Copernicus 90 m)", _fetch_elevation_open_meteo),
    ("opentopodata (NASA SRTM 30 m)", _fetch_elevation_opentopodata),
    ("open-elevation", _fetch_elevation_open_elevation),
)


def fetch_elevation(points):
    # Ground levels do not change, so a re-run of the same plot reads them from the cache
    # the map data already uses instead of asking the elevation service again.
    key = "elevation:" + ";".join(f"{p[0]:.6f},{p[1]:.6f}" for p in points)
    cached = _cache_read(key)
    if cached is not None and len(cached.get("values") or []) == len(points):
        return cached["values"], {"ok": True, "endpoint": cached.get("source") or "cache"}
    errors = []
    for name, fetch in ELEVATION_PROVIDERS:
        try:
            values = fetch(points)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue
        if len(values) == len(points):
            _cache_write(key, {"values": values, "source": name})
            if errors:
                logger.warning("Elevation served by %s after: %s", name, "; ".join(errors))
            return values, {"ok": True, "endpoint": name}
        errors.append(f"{name}: incomplete response")
    logger.warning("Elevation unavailable for %d points: %s", len(points), "; ".join(errors))
    return None, {"ok": False, "error": "; ".join(errors)}


def _plane_slope_pct(points, elevations):
    """Slope (%) of the least-squares plane z = a + b*x + c*y through the samples, or None
    when there are too few distinct points to define a plane."""
    if len(points) < 3:
        return None
    lat0 = sum(p[0] for p in points) / len(points)
    k = math.cos(math.radians(lat0))
    xs = [(p[1] - points[0][1]) * 111320.0 * k for p in points]
    ys = [(p[0] - points[0][0]) * 110540.0 for p in points]
    n = len(points)
    mx, my, mz = sum(xs) / n, sum(ys) / n, sum(elevations) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxz = sum((x - mx) * (z - mz) for x, z in zip(xs, elevations))
    syz = sum((y - my) * (z - mz) for y, z in zip(ys, elevations))
    det = sxx * syy - sxy * sxy
    if abs(det) < 1e-6:
        return None
    b = (sxz * syy - syz * sxy) / det
    c = (syz * sxx - sxz * sxy) / det
    return math.hypot(b, c) * 100.0


def terrain_analysis(coords):
    grid, profile, ring = _sample_points(coords)
    all_pts = grid + profile + ring
    elevations, status = fetch_elevation(all_pts)
    if elevations is None or len(elevations) < len(all_pts):
        return {"available": False, "samples": [], "profile": [], "ring_mean_m": None,
                "min_m": None, "max_m": None, "mean_m": None, "relief_m": None,
                "avg_slope_pct": None, "slope_class": "unknown"}, status

    g = elevations[: len(grid)]
    p = elevations[len(grid): len(grid) + len(profile)]
    r = elevations[len(grid) + len(profile):]

    samples = [{"lat": pt[0], "lng": pt[1], "elevation_m": e} for pt, e in zip(grid, g)]
    s_, w_, n_, e_ = bbox(coords)
    horizontal = haversine([s_, w_], [n_, e_]) or 1.0  # plot diagonal extent
    relief = max(g) - min(g)
    # Average slope = gradient of the best-fit plane through every in-plot sample. Taking
    # (highest - lowest) / diagonal used only the two extreme samples, and on a 90 m DEM
    # that reports whole metres a single 1 m step read as several percent on a small, flat
    # plot. The plane uses all samples, so isolated steps average out.
    fitted = _plane_slope_pct(grid, g)
    slope_pct = round(fitted if fitted is not None else relief / horizontal * 100, 2)

    start = profile[0]
    prof = [{"distance_m": round(haversine(start, pt), 1), "elevation_m": e} for pt, e in zip(profile, p)]

    return {
        "available": True,
        "samples": samples,
        "profile": prof,
        "ring_mean_m": round(sum(r) / len(r), 2) if r else None,
        "min_m": round(min(g), 2),
        "max_m": round(max(g), 2),
        "mean_m": round(sum(g) / len(g), 2),
        "relief_m": round(relief, 2),
        "horizontal_run_m": round(horizontal, 1),
        "avg_slope_pct": slope_pct,
        "slope_class": ("flat" if slope_pct < 2 else "gentle" if slope_pct < 5
                        else "moderate" if slope_pct < 10 else "steep"),
        **_slope_confidence(coords),
    }, status


# The elevation grid is ~90 m (Copernicus GLO-90). A plot narrower than about two grid cells
# is sampled from one or two cells, so the fitted plane mostly reflects neighbouring land.
# Validation on 10 plots: every plot under 2 ha disagreed with both Google Earth and SRTM,
# while plots of 2 ha and more largely agreed. Below that size the slope is reported but
# flagged, and a site survey is the stated source of truth.
SLOPE_MIN_PLOT_SQM = 20000.0


def _slope_confidence(coords):
    area = _ring_area_sqm(coords) if len(coords) >= 3 else 0.0
    if area < SLOPE_MIN_PLOT_SQM:
        return {"slope_confidence": "low",
                "slope_note": (f"Plot is {area / 10000:.2f} ha -- smaller than the ~90 m elevation grid can "
                               "resolve, so this slope is indicative only. Confirm with a site survey.")}
    return {"slope_confidence": "normal", "slope_note": None}


# ------------------------------------------------------------------ flood risk
# What the nearest water feature actually is. A lake on the boundary and a stream
# running through the plot are different hazards with different responses, so the label
# is derived from the OSM tag the feature was classified from rather than calling every
# one of them "a water body".
WATER_KIND_LABEL = {
    "water": "Water body", "wetland": "Wetland", "bay": "Bay", "reservoir": "Reservoir",
    "river": "River", "stream": "Stream", "canal": "Canal", "riverbank": "River",
    "dam": "Dam",
}


def _water_label(item):
    return WATER_KIND_LABEL.get(str((item or {}).get("kind") or "").lower(), "Water body")


# Points added for a storm-water drain near the plot: (within metres, points).
DRAIN_POINTS = [(30.0, 25), (100.0, 15), (200.0, 8)]


def flood_risk(terrain, water, drains=None):
    reasons = []
    score = 0
    nearest = water[0] if water else None
    nearest_water = nearest["distance_m"] if nearest else None
    label = _water_label(nearest)
    if nearest_water is not None:
        if nearest_water <= 50:
            score += 45
            reasons.append(f"{label} within {nearest_water:.0f} m of the plot boundary")
        elif nearest_water <= 150:
            score += 30
            reasons.append(f"{label} {nearest_water:.0f} m from the plot")
        elif nearest_water <= 400:
            score += 15
            reasons.append(f"{label} {nearest_water:.0f} m away")
    nearest_drain = (drains or [None])[0]
    nearest_drain_m = nearest_drain["distance_m"] if nearest_drain else None
    if nearest_drain_m is not None:
        for limit, pts in DRAIN_POINTS:
            if nearest_drain_m <= limit:
                score += pts
                name = (nearest_drain.get("name") or "").strip()
                what = f"Storm-water drain{f' ({name})' if name else ''}"
                reasons.append(f"{what} {'runs along the plot' if nearest_drain_m == 0 else f'{nearest_drain_m:.0f} m away'} "
                               "— drains like Bengaluru’s rajakaluves overflow in heavy rain")
                break
    if terrain.get("available") and terrain.get("ring_mean_m") is not None:
        delta = round(terrain["mean_m"] - terrain["ring_mean_m"], 2)
        if delta <= -2.0:
            score += 40
            reasons.append(f"Plot sits {abs(delta):.1f} m below surrounding ground (low-lying)")
        elif delta <= -0.5:
            score += 22
            reasons.append(f"Plot sits {abs(delta):.1f} m below surrounding ground")
        elif delta >= 1.0:
            reasons.append(f"Plot sits {delta:.1f} m above surrounding ground — good natural drainage")
        if (terrain.get("avg_slope_pct") or 0) < 1.0:
            score += 10
            reasons.append("Very flat terrain (<1% slope) — surface drainage must be engineered")
    else:
        reasons.append("Elevation data unavailable — flood assessment based on water proximity only")
    score = min(score, 100)
    level = "low" if score < 25 else "moderate" if score < 55 else "high"

    # Design response: what the score means for the building, keyed off the same inputs
    # the score was computed from, so guidance and score can never disagree. 0.45 m is the
    # NBC 2016 Part 3 Cl. 12.1.1 minimum plinth above surrounding ground; the 0.6 / 0.9 m
    # raises for moderate / high flood risk are Aptimizer's own margin.
    if level == "high":
        plinth = 0.9 if (terrain.get("available") and (terrain.get("avg_slope_pct") or 0) < 1) else 0.6
    elif level == "moderate":
        plinth = 0.6
    else:
        plinth = 0.45
    response = [
        f"Set finished floor (plinth) at least {plinth:.2f} m above surrounding ground — NBC 2016 "
        "Part 3 Cl. 12.1.1 sets 0.45 m; the extra height is Aptimizer's margin for flood-prone sites"
        if level != "low"
        else f"Standard plinth of {plinth:.2f} m above surrounding ground (NBC 2016 Part 3 Cl. 12.1.1)",
        "Basements need pumped sump + standby pump; keep electricals above design flood level"
        if level == "high" else
        "Provide storm-water drains sized for local cloudburst intensity before monsoon"
        if level == "moderate" else
        "Conventional storm-water disposal is adequate",
        "Rainwater harvesting recharge pits double as flood detention — size for the design storm"
        if level != "low" else
        "Harvest pits sized for annual rainfall give the required recharge without detention",
    ]
    if nearest_drain_m is not None and nearest_drain_m <= 200:
        response.append("Keep the statutory buffer from the storm-water drain free of construction (check the "
                        "local BBMP/NGT buffer rule) and raise the entrance and basement ramp above the drain’s "
                        "overflow level")
    return {"score": score, "level": level, "reasons": reasons,
            "nearest_drain_m": nearest_drain_m,
            "nearest_drain_name": ((nearest_drain or {}).get("name") or None) if nearest_drain else None,
            "nearest_water_m": nearest_water,
            "nearest_water_kind": (nearest or {}).get("kind"),
            "nearest_water_label": label if nearest_water is not None else None,
            "elevation_delta_m": (round(terrain["mean_m"] - terrain["ring_mean_m"], 2)
                                  if terrain.get("available") and terrain.get("ring_mean_m") is not None else None),
            "plinth_height_m": plinth,
            "design_response": response}


# ------------------------------------------------------------------ wind
WIND_REGIONS = [
    # (lat_min, lat_max, lng_min, lng_max, label, prevailing, summer, winter, speed)
    (8, 21, 68, 78, "Peninsular west India", "W", "SW (monsoon)", "NE", 3.6),
    (8, 21, 78, 88, "Peninsular east India", "SW", "SW (monsoon)", "NE", 3.2),
    (21, 31, 68, 80, "North-west India", "NW", "SW (monsoon)", "NW", 3.0),
    (21, 31, 80, 90, "Indo-Gangetic plain", "E", "SE (monsoon)", "NW", 2.6),
    (21, 31, 90, 98, "North-east India", "SE", "SW (monsoon)", "NE", 2.4),
    (-10, 8, 60, 100, "Equatorial belt", "SW", "SW", "NE", 3.4),
]


# IS 875-3 Cl. 6.3.3.1: topography matters when the upwind slope is more than about 3 degrees.
K3_SLOPE_LIMIT_PCT = round(100 * math.tan(math.radians(3.0)), 2)     # 5.24 %


def wind_profile(lat, lng, city_ref=None, building_height_m=0, climate=None, terrain=None):
    for a, b, c, d, label, prevailing, summer, winter, speed in WIND_REGIONS:
        if a <= lat <= b and c <= lng <= d:
            region, prev, sm, wt, sp = label, prevailing, summer, winter, speed
            break
    else:
        if lat > 30:
            region, prev, sm, wt, sp = "Northern mid-latitude westerlies", "W", "SW", "NW", 4.2
        elif lat < -30:
            region, prev, sm, wt, sp = "Southern mid-latitude westerlies", "W", "NW", "SW", 4.5
        else:
            region, prev, sm, wt, sp = "Tropical trade-wind belt", "E", "SE", "NE", 3.8
    rose = {"N": 6, "NE": 9, "E": 12, "SE": 14, "S": 12, "SW": 18, "W": 16, "NW": 13}
    rose[prev.split()[0]] = 24

    # IS 875 (Part 3):2015 design wind — the number structures are actually designed for,
    # from the same city reference the engineering module uses (Cl. 6.2 basic wind speed
    # Vb by city; Cl. 7.2 design wind pressure pz = 0.6 Vz², k2 terrain/height factor).
    design = None
    if city_ref and city_ref.get("wind_speed"):
        vb = float(city_ref["wind_speed"])
        # k2 (terrain & height factor, Table 2, Cl. 6.3.2.2). Terrain 2 = obstructions
        # scattered (<10 m) — the suburban/town case this app targets; terrain 1 = open.
        # Interpolated from Table 2 at the heights that matter for mid-rise (same terrain 2
        # values as iscodes.WIND_K2 used by the engineering module).
        k2_t2 = {10: 1.00, 15: 1.05, 20: 1.07, 30: 1.12, 50: 1.17}
        k2_t1 = {10: 1.05, 15: 1.09, 20: 1.12, 30: 1.15, 50: 1.20}
        h = max(float(building_height_m or 0), 10.0)
        heights = sorted(k2_t2)
        # clamp to the table range; below 10 m the code takes the 10 m value
        hc = min(max(h, 10.0), 50.0)
        def interp(tbl):
            for h0, h1 in zip(heights, heights[1:]):
                if h0 <= hc <= h1:
                    f = (hc - h0) / (h1 - h0)
                    return tbl[h0] + f * (tbl[h1] - tbl[h0])
            return tbl[heights[-1]]
        k2 = round(interp(k2_t2), 3)
        k2_open = round(interp(k2_t1), 3)
        k1_risk = 1.0          # general structures, Cl. 6.3.1
        k3 = 1.0               # flat terrain, Cl. 6.3.3
        # Cl. 6.3.3.1: k3 = 1.0 only when the upwind slope is under about 3 degrees. The plot's
        # own mean slope is the screening proxy; above the limit k3 may reach 1.36 (Annex C).
        slope = (terrain or {}).get("avg_slope_pct") if (terrain or {}).get("available") else None
        k3_check = bool(slope is not None and slope > K3_SLOPE_LIMIT_PCT)
        k4 = 1.0               # 'all other structures' incl. housing, Cl. 6.3.4 (1.0 everywhere)
        # wind directionality, Cl. 7.2.1: 0.90 for buildings, 1.0 in the cyclone belt
        cyclone = bool(city_ref.get("cyclone_belt"))
        kd = 1.0 if cyclone else 0.90
        vz_t2 = round(vb * k1_risk * k2 * k3 * k4, 2)
        vz_t1 = round(vb * k1_risk * k2_open * k3 * k4, 2)
        # Cl. 7.2: pz = 0.6 Vz² (N/m²) — wind pressure at height z
        pz_t2 = round(0.6 * vz_t2 ** 2)
        pz_t1 = round(0.6 * vz_t1 ** 2)
        design = {
            "code": "IS 875 (Part 3):2015",
            "basic_wind_speed_vb_ms": vb,
            "terrain_category": 2,
            "design_height_m": round(hc, 1),
            "k2_terrain2": k2, "k2_terrain1": k2_open,
            "kd": kd, "k1": k1_risk, "k3": k3, "k4": k4, "cyclone_belt": cyclone,
            "k3_check_required": k3_check,
            "k3_note": (f"Mean slope {slope:.1f}% is above 3 degrees ({K3_SLOPE_LIMIT_PCT}%): k3 can exceed 1.0 "
                        "(up to 1.36) on hills and ridges -- evaluate it per IS 875-3 Annex C." if k3_check
                        else None),
            "design_wind_speed_terrain2_ms": vz_t2,
            "design_wind_speed_terrain1_ms": vz_t1,
            "design_wind_pressure_terrain2_nm2": pz_t2,
            "design_wind_pressure_terrain1_nm2": pz_t1,
            "design_wind_pressure_terrain2_knm2": round(pz_t2 / 1000, 3),
            "note": (f"Vb {vb:.0f} m/s ({city_ref.get('city', 'site')}, IS 875-3 Annex A) → Vz "
                     f"{vz_t2:.1f} m/s at {hc:.0f} m in terrain 2, pz {pz_t2 / 1000:.2f} kN/m². "
                     "Open coastal/flat sites (terrain 1) see more — structural module carries the check."
                     + (" Cyclone belt: Kd = 1.0 (IS 875-3 Cl. 7.2.1)." if cyclone else "")),
        }
        if k3_check:
            design["note"] += " " + design["k3_note"]

    # Site mean wind from NASA POWER's 10 m climatology when available; the regional
    # figure above is the fallback and still supplies the prevailing directions.
    nasa_ws = (climate or {}).get("wind_10m_ms")
    return {"region": region, "prevailing": prev, "summer": sm, "winter": wt,
            "mean_speed_ms": round(nasa_ws, 1) if nasa_ws is not None else sp,
            "mean_speed_source": "NASA POWER WS10M climatology" if nasa_ws is not None
            else "regional table (NASA POWER unavailable)",
            "rose": [{"direction": k, "frequency_pct": v} for k, v in rose.items()],
            "guidance": f"Orient living-room and balcony openings toward {sm.split()[0]} for monsoon cross-ventilation; "
                        f"shelter service cores on the {wt} face.",
            "is875_design": design}


# ------------------------------------------------------------------ sun path
# Several zones this app targets are NOT whole-hour offsets, so deriving the offset as
# round(lng/15) puts every solar result half an hour out. India is the worst case: IST is
# +5:30 on the 82.5E meridian, but round(77/15) = 5 for most Indian cities. Boxes are
# generous bounding boxes, checked before the whole-hour fallback.
# Ordered most specific first: the India box overlaps Nepal and Myanmar, so those must be
# tested before it or they inherit IST.
HALF_HOUR_ZONES = [
    (26.3, 30.5, 80.0, 88.3, 5.75),  # Nepal
    (9.0, 28.6, 92.0, 101.2, 6.5),   # Myanmar
    (29.3, 38.5, 60.5, 75.0, 4.5),   # Afghanistan
    (25.0, 40.0, 44.0, 63.4, 3.5),   # Iran
    (6.5, 37.5, 68.0, 97.5, 5.5),    # India + Sri Lanka (IST)
]


def utc_offset_hours(lat, lng):
    """Standard-time UTC offset for a location, honouring half-hour zones."""
    for s, n, w, e, off in HALF_HOUR_ZONES:
        if s <= lat <= n and w <= lng <= e:
            return off
    return float(round(lng / 15.0))


def _julian_day(doy, hour_utc, year=None):
    """Julian day for day-of-year `doy` (1 = 1 Jan) of `year` at `hour_utc`."""
    year = year or _dt.date.today().year
    d = _dt.date(year, 1, 1) + _dt.timedelta(days=doy - 1)
    return d.toordinal() + 1721424.5 + hour_utc / 24.0


def _solar_terms(jd):
    """Declination (rad) and equation of time (min) -- the full NOAA algorithm (Meeus), the
    one behind the NOAA Solar Calculator. The short Fourier series used before was only good
    to about a minute; this agrees with the calculator to a few seconds."""
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * t) + math.sin(3 * mr) * 0.000289)
    omega = math.radians(125.04 - 1934.136 * t)
    app_long = math.radians(l0 + c - 0.00569 - 0.00478 * math.sin(omega))
    obliq0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    obliq = math.radians(obliq0 + 0.00256 * math.cos(omega))
    decl = math.asin(math.sin(obliq) * math.sin(app_long))
    y = math.tan(obliq / 2) ** 2
    l0r = math.radians(l0)
    eqtime = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr)
                              + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                              - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    return decl, eqtime


def solar_position(lat, lng, doy, hour_local):
    tz_offset = utc_offset_hours(lat, lng)
    decl, eqtime = _solar_terms(_julian_day(doy, hour_local - tz_offset))
    time_offset = eqtime + 4 * lng - 60 * tz_offset
    tst = hour_local * 60 + time_offset
    ha = math.radians(tst / 4.0 - 180.0)
    latr = math.radians(lat)
    cos_zen = math.sin(latr) * math.sin(decl) + math.cos(latr) * math.cos(decl) * math.cos(ha)
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zen = math.acos(cos_zen)
    elev = 90 - math.degrees(zen)
    denom = math.sin(zen) * math.cos(latr)
    if abs(denom) < 1e-6:
        az = 180.0
    else:
        sin_az = -math.sin(ha) * math.cos(decl) / math.sin(zen)
        cos_az = (math.sin(decl) - math.sin(latr) * math.cos(zen)) / denom
        az = math.degrees(math.atan2(sin_az, cos_az)) % 360
    return round(az, 2), round(elev, 2)


SUN_DATES = [("summer_solstice", 172, "21 Jun"), ("equinox", 80, "21 Mar"), ("winter_solstice", 355, "21 Dec")]


def sun_events(lat, lng, doy):
    """Sunrise, sunset and daylight length in local clock hours (NOAA solar equations).

    Solved from the sunrise hour angle rather than read off the 30-minute sampling grid
    used to draw the path, which could only ever be right to the nearest half hour.
    Includes the standard -0.833 deg refraction/semi-diameter correction. The sun's
    declination and the equation of time are evaluated at the event itself (two passes),
    as the NOAA calculator does, not once for the whole day.
    """
    tz = utc_offset_hours(lat, lng)
    latr = math.radians(lat)

    def event(sign, guess_hour):
        hour = guess_hour
        for _ in range(2):
            decl, eqtime = _solar_terms(_julian_day(doy, hour - tz))
            cos_ha = ((math.cos(math.radians(90.833)) / (math.cos(latr) * math.cos(decl)))
                      - math.tan(latr) * math.tan(decl))
            if cos_ha > 1:
                return "never_rises"
            if cos_ha < -1:
                return "never_sets"
            ha = math.degrees(math.acos(cos_ha))
            hour = (720 - 4 * (lng + sign * ha) - eqtime) / 60.0 + tz
        return hour

    rise, seti = event(1, 6.0), event(-1, 18.0)
    if "never_rises" in (rise, seti):
        return {"sunrise_hour": None, "sunset_hour": None, "daylight_hours": 0.0}
    if "never_sets" in (rise, seti):
        return {"sunrise_hour": None, "sunset_hour": None, "daylight_hours": 24.0}
    return {"sunrise_hour": round(rise, 4), "sunset_hour": round(seti, 4),
            "daylight_hours": round(seti - rise, 2)}


def sun_path(lat, lng, orientation_deg):
    paths = []
    for key, doy, label in SUN_DATES:
        points = []
        for h in [x * 0.5 for x in range(8, 40)]:  # 04:00 -> 19:30
            az, el = solar_position(lat, lng, doy, h)
            if el > 0:
                points.append({"hour": round(h, 1), "azimuth": az, "elevation": el})
        peak = max(points, key=lambda p: p["elevation"]) if points else None
        paths.append({
            "key": key, "label": label,
            "points": points,
            **sun_events(lat, lng, doy),
            "peak_elevation": peak["elevation"] if peak else None,
            "peak_azimuth": peak["azimuth"] if peak else None,
        })
    o = ((orientation_deg or 0) % 360 + 360) % 360
    facades = []
    for name, bearing in [("Front (as drawn)", o), ("Right", (o + 90) % 360),
                          ("Rear", (o + 180) % 360), ("Left", (o + 270) % 360)]:
        eq = next((p for p in paths if p["key"] == "equinox"), None)
        hours = 0
        if eq:
            hours = sum(0.5 for pt in eq["points"] if abs(((pt["azimuth"] - bearing + 180) % 360) - 180) < 90)
        facades.append({
            "facade": name, "bearing_deg": round(bearing, 1),
            "sun_hours_equinox": hours,
            "recommendation": ("Prime daylight — place living rooms and balconies here" if 90 <= bearing <= 180
                               else "Harsh afternoon heat gain — deep shading or service rooms" if 225 <= bearing <= 300
                               else "Soft morning sun — bedrooms work well" if 45 <= bearing < 90
                               else "Low direct gain — good for stairs, cores and utility"),
        })
    return {"latitude": lat, "longitude": lng, "orientation_deg": o, "paths": paths, "facades": facades}


# ------------------------------------------------------------------ solar yield
# Rooftop PV defaults for India. A flat roof needs tilt frames with row spacing to avoid
# self-shading, which is why the area per kWp is roughly 10 m2 and not the ~5 m2 the bare
# module area would suggest.
SOLAR_DEFAULTS = {
    "roof_usable_pct": 60.0,      # lifts, tanks, stairs, AC plant and access paths take the rest
    "sqm_per_kwp": 10.0,          # tilted rows on a flat terrace
    "performance_ratio": 0.78,    # soiling, heat derate, inverter and cable losses
    "cost_per_kwp": 50000.0,      # INR, installed, grid-tied without battery
    "tariff_per_kwh": 8.0,        # INR, displaced residential/common-area tariff
    "degradation_pct_yr": 0.7,
    "life_years": 25,
}

# Clear-sky beam transmittance in the ASHRAE/Meinel air-mass model. The clearness factor
# scales that ideal down to what Indian sites actually see once monsoon cloud and dust are
# in: clear-sky integration alone lands near 2400 kWh/m2/yr, while measured GHI across
# most of India is 1700-2000.
SOLAR_CLEARNESS = 0.80
SOLAR_CONSTANT = 1353.0
DIFFUSE_FRACTION = 0.14           # of the beam component on a horizontal plane

# One representative day per month (the 15th), weighted by that month's length.
_MONTH_DOY = [15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349]
_MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"
NASA_TIMEOUT_S = 10
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def fetch_climate(lat, lng):
    """Long-term site climatology from NASA POWER (satellite + reanalysis, ~0.5 deg grid):
    daily global horizontal irradiation by month, the gain an optimally tilted panel gets
    over a flat one, and the mean wind speed at 10 m. Cached on disk like the map data;
    None when the service cannot be reached, and the callers fall back to the built-in
    clear-sky model and regional wind table."""
    key = f"nasa-power:{lat:.3f},{lng:.3f}"
    cached = _cache_read(key)
    if cached:
        return cached
    try:
        r = _http.get(NASA_POWER_URL, timeout=NASA_TIMEOUT_S, params={
            "parameters": "ALLSKY_SFC_SW_DWN,SI_EF_TILTED_SURFACE,WS10M", "community": "RE",
            "latitude": round(lat, 4), "longitude": round(lng, 4), "format": "JSON"})
        if r.status_code != 200:
            return None
        p = r.json()["properties"]["parameter"]
        ghi = p["ALLSKY_SFC_SW_DWN"]
        monthly = [float(ghi[m]) for m in _MONTHS]
        if any(v is None or v < 0 for v in monthly):        # -999 marks missing data
            return None
        def ann(name):                       # annual value of a monthly table, or None
            v = (p.get(name) or {}).get("ANN")
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return v if v >= 0 else None     # -999 marks missing data

        flat, best = ann("SI_TILTED_AVG_HORIZONTAL"), ann("SI_TILTED_AVG_OPTIMAL")
        tilt_gain = best / flat if flat and best else 1.0
        ws = ann("WS10M")
        out = {"source": "NASA POWER climatology", "ghi_monthly_kwh_day": monthly,
               "ghi_annual_kwh_day": float(ghi["ANN"]),
               "tilt_gain": round(max(1.0, min(tilt_gain, 1.25)), 4),
               "optimal_tilt_deg": ann("SI_TILTED_AVG_OPTIMAL_ANG"),
               "wind_10m_ms": ws}
        _cache_write(key, out)
        return out
    except Exception as exc:
        logger.warning("NASA POWER climatology unavailable for %.3f,%.3f: %s", lat, lng, exc)
        return None


def annual_insolation(lat, lng, climate=None):
    """Annual global horizontal irradiation, kWh/m2/yr, from the same solar geometry the
    sun path uses.

    Integrated hourly over twelve representative days rather than all 8760 hours: the
    declination barely moves within a month, so the extra 700-odd position calls buy
    nothing a rooftop estimate can use.
    """
    if climate and climate.get("ghi_monthly_kwh_day"):
        monthly = [{"days": days, "kwh_per_sqm_day": round(v, 2), "kwh_per_sqm_month": round(v * days, 1)}
                   for v, days in zip(climate["ghi_monthly_kwh_day"], _MONTH_DAYS)]
        annual = sum(m["kwh_per_sqm_month"] for m in monthly)
        return {"annual_kwh_per_sqm": round(annual, 1),
                "daily_average_kwh_per_sqm": round(annual / 365.0, 2),
                "monthly": monthly, "source": climate.get("source"),
                "tilt_gain": climate.get("tilt_gain", 1.0),
                "optimal_tilt_deg": climate.get("optimal_tilt_deg")}
    monthly = []
    for doy, days in zip(_MONTH_DOY, _MONTH_DAYS):
        wh = 0.0
        for step in range(48):                       # half-hourly, 00:00 -> 23:30
            hour = step * 0.5
            _, elev = solar_position(lat, lng, doy, hour)
            if elev <= 3:                            # below this, air mass makes it noise
                continue
            am = 1.0 / math.sin(math.radians(elev))
            beam = SOLAR_CONSTANT * (0.7 ** (am ** 0.678))
            horiz = beam * math.sin(math.radians(elev))
            wh += (horiz * (1 + DIFFUSE_FRACTION)) * 0.5    # W/m2 over half an hour
        day_kwh = wh / 1000.0 * SOLAR_CLEARNESS
        monthly.append({"days": days, "kwh_per_sqm_day": round(day_kwh, 2),
                        "kwh_per_sqm_month": round(day_kwh * days, 1)})
    annual = sum(m["kwh_per_sqm_month"] for m in monthly)
    return {"annual_kwh_per_sqm": round(annual, 1),
            "daily_average_kwh_per_sqm": round(annual / 365.0, 2),
            "monthly": monthly, "source": "clear-sky model (NASA POWER unavailable)",
            "tilt_gain": 1.0, "optimal_tilt_deg": None}


def solar_potential(lat, lng, roof_area_sqm, config=None, climate=None):
    """Installable rooftop PV, annual yield and simple payback.

    Payback is against the tariff the generation displaces, undiscounted, and ignores any
    subsidy or net-metering export price -- both vary by state and neither is knowable
    from the project data.
    """
    cfg = {**SOLAR_DEFAULTS, **{k: v for k, v in (config or {}).items() if v is not None}}
    ins = annual_insolation(lat, lng, climate)
    roof = max(float(roof_area_sqm or 0), 0.0)
    usable = roof * cfg["roof_usable_pct"] / 100.0
    kwp = usable / cfg["sqm_per_kwp"] if cfg["sqm_per_kwp"] else 0.0
    # A 1 kWp array is rated at 1000 W/m2, so annual yield is simply the site's kWh/m2
    # times the rating times the performance ratio.
    # Panels are mounted at the optimum tilt, which collects more than a flat surface; the
    # gain is NASA POWER's own optimal-tilt / horizontal ratio for the site (1.0 when the
    # built-in model is in use, which already runs a little high).
    yield_kwh = kwp * ins["annual_kwh_per_sqm"] * ins.get("tilt_gain", 1.0) * cfg["performance_ratio"]
    capex = kwp * cfg["cost_per_kwp"]
    saving = yield_kwh * cfg["tariff_per_kwh"]
    payback = (capex / saving) if saving > 0 else None

    # Straight-line degradation over the panel life.
    life = int(cfg["life_years"])
    deg = cfg["degradation_pct_yr"] / 100.0
    lifetime_kwh = sum(yield_kwh * max(0.0, 1 - deg * y) for y in range(life))

    return {
        "insolation": ins,
        "roof_area_sqm": round(roof, 2),
        "usable_area_sqm": round(usable, 2),
        "installable_kwp": round(kwp, 2),
        "annual_yield_kwh": round(yield_kwh, 0),
        "specific_yield_kwh_per_kwp": round(yield_kwh / kwp, 0) if kwp else 0,
        "capex_inr": round(capex, 0),
        "annual_saving_inr": round(saving, 0),
        "payback_years": round(payback, 1) if payback else None,
        "lifetime_kwh": round(lifetime_kwh, 0),
        "lifetime_saving_inr": round(lifetime_kwh * cfg["tariff_per_kwh"], 0),
        "co2_avoided_tonnes_per_yr": round(yield_kwh * 0.71 / 1000.0, 1),
        "config": cfg,
    }


# ------------------------------------------------------------------ accessibility
PRIVATE_ROAD_KINDS = {"service", "track"}
PRIVATE_ACCESS = {"private", "no", "customers", "delivery", "permit"}


def is_public_road(road):
    """A road the plot could take its legal access from: not a driveway, parking aisle or
    campus service road, and not tagged private."""
    return (road.get("kind") not in PRIVATE_ROAD_KINDS
            and str(road.get("access") or "").lower() not in PRIVATE_ACCESS)


def accessibility(roads, transit, coords, road_edges):
    internal = roads[0] if roads else None
    roads = [r for r in roads if is_public_road(r)]
    nearest = roads[0] if roads else None
    widest = max(roads, key=lambda r: r.get("road_width_m") or 0) if roads else None
    within_100 = [r for r in roads if r["distance_m"] <= 100]
    score = 0
    notes = []
    if nearest:
        if nearest["distance_m"] <= 10:
            score += 50
            road_name = f" ({nearest['name']})" if nearest.get("name") else ""
            notes.append(f"Plot abuts a public {nearest['kind']} road{road_name} ({nearest['distance_m']:.0f} m)")
        elif nearest["distance_m"] <= 50:
            score += 38
            notes.append(f"Nearest road {nearest['distance_m']:.0f} m away")
        elif nearest["distance_m"] <= 150:
            score += 22
            notes.append(f"Nearest road {nearest['distance_m']:.0f} m away — access road required")
        else:
            notes.append(f"Nearest mapped road is {nearest['distance_m']:.0f} m away — no direct access")
    else:
        notes.append("No mapped public road detected within the search radius")
    if widest and (widest.get("road_width_m") or 0) >= 12:
        score += 20
        notes.append(f"{widest.get('road_width_m')} m wide {widest['kind']} road nearby supports fire-tender access")
    elif widest:
        score += 10
    score += min(len(within_100) * 2, 12)
    nearest_transit = transit[0] if transit else None
    if nearest_transit:
        if nearest_transit["distance_m"] <= 500:
            score += 18
        elif nearest_transit["distance_m"] <= 1000:
            score += 10
        notes.append(f"Nearest transit stop ({nearest_transit['kind']}) {nearest_transit['distance_m']:.0f} m away")
    else:
        notes.append("No transit stop mapped nearby")
    if road_edges:
        score += 6
        notes.append(f"{len(road_edges)} plot edge(s) marked as road-facing in Plot & Setbacks")
    score = min(score, 100)
    return {"score": score, "roads_within_100m": len(within_100),
            "nearest_road_m": nearest["distance_m"] if nearest else None,
            "nearest_road_kind": nearest["kind"] if nearest else None,
            "nearest_road_name": (nearest.get("name") or None) if nearest else None,
            # The closest road of any kind, including private driveways and service roads.
            "nearest_internal_road_m": internal["distance_m"] if internal else None,
            "widest_road_m": (widest.get("road_width_m") if widest else None),
            "nearest_transit_m": nearest_transit["distance_m"] if nearest_transit else None,
            "notes": notes}


# ------------------------------------------------------------------ site seismic hazard
# IS 1893 (Part 1):2016 Table 3 zone factor Z by seismic zone; the zone itself comes from
# the city reference (Annex E). Z is the peak ground acceleration the code considers for
# design (Cl. 3.28). Soil type I/II/III (Table 4) changes the design spectrum (Cl. 6.4.2):
# the same 2.5 plateau, then 1.00/T, 1.36/T and 1.67/T -- so at longer periods soft soil
# sees 1.36x / 1.67x the demand of rock. That ratio is the soil factor reported here.
ZONE_FACTOR = {"II": 0.10, "III": 0.16, "IV": 0.24, "V": 0.36}
ZONE_LABEL = {"II": "Low damage risk", "III": "Moderate damage risk", "IV": "High damage risk", "V": "Very severe"}
SOIL_FACTOR = {"I": 1.0, "II": 1.36, "III": 1.67}
LIQUEFACTION_SOILS = {"loose sand", "medium sand"}


def seismic_hazard(city_ref, terrain, soil_type="medium clay"):
    """Site-level earthquake hazard for the GIS report.

    The engineering module answers 'what load does MY building see?' (IS 1893 base shear);
    this answers 'what does the GROUND here do?' — zone, expected shaking, liquefaction
    potential and what the site class means for foundation choice. Both read the same
    city zone so the two screens can never disagree.
    """
    zone = city_ref.get("zone") or "III"
    z = ZONE_FACTOR.get(zone, 0.16)
    site_class = {"I": "I", "II": "II", "III": "III"}.get(str(soil_type_class(soil_type)), "II")
    s_factor = SOIL_FACTOR.get(site_class, 1.36)
    slope = terrain.get("avg_slope_pct") if terrain.get("available") else None

    # Slope stability: steep slopes in zone IV/V need slope-stability review before
    # siting tall blocks. Thresholds are Aptimizer's own screening values; IS 1893 has no
    # slope clause (Cl. 6.3.5 is about bearing pressure on soils).
    slope_flag = None
    if slope is not None and zone in ("IV", "V") and slope >= 10:
        slope_flag = ("critical", f"Average slope {slope}% in seismic zone {zone} — slope stability "
                      "analysis required before siting buildings near the boundary")
    elif slope is not None and zone in ("IV", "V") and slope >= 5:
        slope_flag = ("warning", f"Slope {slope}% in zone {zone} — geotechnical investigation "
                      "should confirm stability under seismic loading (Aptimizer screening check)")

    # Liquefaction: IS 1893 Cl. 6.3.5.3 -- submerged loose sands (SP) with corrected N below
    # 15 in zones III-V and below 10 in zone II. Without bore data this is a flag to
    # investigate, not a verdict, so it is raised in every zone for sandy soil.
    liq_risk = soil_type.lower() in LIQUEFACTION_SOILS

    # PGA = Z (IS 1893 Cl. 3.28). The surface figure is a SCREENING estimate, Z x the soil
    # spectral ratio above -- the number a non-engineer can compare across sites. Design
    # uses Ah = (Z/2)(I/R)(Sa/g) in the engineering module, not this.
    pga_rock_g = round(z, 3)
    pga_surface_g = round(z * s_factor, 3)

    risks = []
    if liq_risk:
        risks.append({"id": "liquefaction", "severity": "critical",
                      "title": "Liquefaction-susceptible soil in a seismic zone",
                      "detail": f"{soil_type.title()} in zone {zone} can lose strength during shaking "
                                f"if submerged and loose (corrected N < {10 if zone == 'II' else 15}, "
                                "IS 1893 Cl. 6.3.5.3). Confirm with bore data. If confirmed: ground improvement (stone columns / compaction piles) or deep piles to "
                                "competent strata before foundations are designed."})
    if slope_flag:
        sev, msg = slope_flag
        risks.append({"id": "slope_stability", "severity": sev,
                      "title": "Seismic slope stability",
                      "detail": msg})
    if zone == "V":
        risks.append({"id": "zone_v", "severity": "warning",
                      "title": "Seismic zone V",
                      "detail": "Very severe zone — IS 13920 ductile detailing is mandatory and a "
                                "special seismic design review is required (engineering module applies this)."})
    if not risks:
        risks.append({"id": "standard", "severity": "ok",
                      "title": f"Zone {zone} — standard seismic design",
                      "detail": "Conventional IS 456/IS 13920 design through the engineering module covers "
                                "this hazard level; no site-specific study flagged."})

    return {
        "zone": zone,
        "zone_factor_z": z,
        "zone_label": ZONE_LABEL.get(zone, ""),
        "site_class": site_class,
        "soil_factor": s_factor,
        "pga_rock_g": pga_rock_g,
        "pga_surface_g": pga_surface_g,
        "liquefaction_risk": liq_risk,
        "risk_flags": risks,
        "guidance": (f"Zone {zone} ({ZONE_LABEL.get(zone, '')}), site class {site_class}: structures here "
                     f"see a screening surface shaking of about {pga_surface_g:.2f} g (PGA Z = {z:.2f} g, "
                     f"IS 1893). {len([r for r in risks if r['severity'] == 'critical'])} "
                     "critical item(s) flagged."),
    }


def soil_type_class(soil_type):
    """Map the descriptive soil to the IS 1893 site class I/II/III."""
    s = (soil_type or "").lower()
    if "rock" in s:
        return "I"
    if any(k in s for k in ("sand", "gravel")):
        return "II" if "dense" in s or "medium" in s else "III"
    if "soft" in s:
        return "III"
    return "II"          # stiff/medium clay and unknowns


# ------------------------------------------------------------------ scoring
def suitability(terrain, flood, access, sun, seismic=None, wind=None):
    slope = terrain.get("avg_slope_pct")
    if slope is None:
        slope_score = 60.0
        slope_note = "Elevation data unavailable — neutral slope score applied"
    else:
        slope_score = max(0.0, min(100.0, 100 - slope * 7))
        slope_note = f"Average slope {slope}% ({terrain.get('slope_class')})"
    flood_score = 100 - flood["score"]
    access_score = float(access["score"])
    best_facade = max(sun["facades"], key=lambda f: f["sun_hours_equinox"]) if sun["facades"] else None
    orientation_score = min(100.0, 55 + (best_facade["sun_hours_equinox"] * 4 if best_facade else 0))

    # Natural-hazard factors, when the caller ran them. A site can be flat, dry and
    # well-connected and still be a poor place for a tall building because the ground
    # shakes — the score has to be able to say that.
    if seismic:
        # Zone V (Z 0.36) is unbuildable without special measures; II is routine.
        zone_scores = {"II": 95.0, "III": 78.0, "IV": 55.0, "V": 30.0}
        seis_score = zone_scores.get(seismic.get("zone"), 78.0)
        crit = [r for r in seismic.get("risk_flags", []) if r.get("severity") == "critical"]
        if crit:
            seis_score = min(seis_score, 35.0)
        seis_note = f"IS 1893 zone {seismic['zone']} ({seismic.get('zone_label', '')})"
        if crit:
            seis_note += f" — {len(crit)} critical flag(s)"
    else:
        seis_score, seis_note = 70.0, "Seismic hazard not yet assessed"
    if wind and wind.get("is875_design"):
        d = wind["is875_design"]
        # 33 m/s (Bengaluru) is benign; 50 m/s (Chennai coast) is cyclonic.
        vb = d.get("basic_wind_speed_vb_ms", 39)
        wind_score = max(20.0, min(100.0, 100 - (vb - 33) * 3.7))
        wind_note = f"IS 875-3 basic wind speed {vb:.0f} m/s at {d.get('design_height_m', 10):.0f} m"
    else:
        wind_score, wind_note = 70.0, "Design wind speed not yet assessed"

    weights = [("Slope & terrain", slope_score, 0.22, slope_note),
               ("Flood risk", flood_score, 0.20, f"Flood risk {flood['level']} ({flood['score']}/100)"),
               ("Road access", access_score, 0.18,
                f"Access score {access['score']} — nearest road {access['nearest_road_m']} m"),
               ("Orientation & solar", orientation_score, 0.15,
                f"Best facade: {best_facade['facade'] if best_facade else '—'} "
                f"({best_facade['sun_hours_equinox'] if best_facade else 0} sun-hours at equinox)"),
               ("Seismic hazard", seis_score, 0.15, seis_note),
               ("Wind climate", wind_score, 0.10, wind_note)]
    total = round(sum(v * w for _, v, w, _ in weights), 1)
    return {
        "score": total,
        "grade": "excellent" if total >= 80 else "good" if total >= 65 else "fair" if total >= 50 else "poor",
        "breakdown": [{"factor": n, "score": round(v, 1), "weight_pct": round(w * 100),
                       "contribution": round(v * w, 1), "note": note} for n, v, w, note in weights],
    }


def buildability(terrain, flood, access, features):
    flags = []
    slope = terrain.get("avg_slope_pct")
    if slope is not None and slope >= 10:
        flags.append({"id": "steep_slope", "severity": "critical", "title": "Steep slope",
                      "detail": f"Average slope {slope}% requires terracing, retaining walls and cut-fill balancing."})
    elif slope is not None and slope >= 5:
        flags.append({"id": "moderate_slope", "severity": "warning", "title": "Moderate slope",
                      "detail": f"Average slope {slope}% — stepped foundations and site levelling cost allowance needed."})
    if flood["level"] == "high":
        flags.append({"id": "flood_zone", "severity": "critical", "title": "Elevated flood risk",
                      "detail": "; ".join(flood["reasons"]) + ". Raise plinth and design storm-water retention."})
    elif flood["level"] == "moderate":
        flags.append({"id": "flood_watch", "severity": "warning", "title": "Moderate flood risk",
                      "detail": "; ".join(flood["reasons"])})
    if access["nearest_road_m"] is None or access["nearest_road_m"] > 150:
        flags.append({"id": "no_road_access", "severity": "critical", "title": "No direct road access",
                      "detail": "No mapped road within 150 m — an approach road / right of way is required before construction."})
    elif (access["widest_road_m"] or 0) < 9:
        flags.append({"id": "narrow_road", "severity": "warning", "title": "Narrow access road",
                      "detail": f"Widest nearby road is {access['widest_road_m']} m — check fire-tender and setback bye-laws."})
    on_plot = [b for b in features.get("buildings", []) if b["on_plot"]]
    if on_plot:
        flags.append({"id": "existing_structures", "severity": "warning", "title": f"{len(on_plot)} existing structure(s) on plot",
                      "detail": "Demolition / clearance allowance required before construction."})
    if not flags:
        flags.append({"id": "clear", "severity": "ok", "title": "No hard constraints detected",
                      "detail": "Slope, flood exposure and road access are all within workable limits."})
    return {"flags": flags,
            "buildable": not any(f["severity"] == "critical" for f in flags),
            "critical_count": sum(1 for f in flags if f["severity"] == "critical"),
            "warning_count": sum(1 for f in flags if f["severity"] == "warning")}


# ------------------------------------------------------------------ orchestrator
async def analyse_site(project, radius_m=500):
    plot = project.get("plot") or {}
    coords = plot.get("coordinates") or []
    if len(coords) < 3:
        raise ValueError("Draw a plot polygon with at least 3 vertices in Plot & Setbacks first")
    radius_m = max(100, min(int(radius_m or 500), 2000))
    c = centroid(coords)

    (features, ov_status), (terrain, el_status), climate = await asyncio.gather(
        asyncio.to_thread(fetch_overpass, coords, radius_m),
        asyncio.to_thread(terrain_analysis, coords),
        asyncio.to_thread(fetch_climate, round(c[0], 4), round(c[1], 4)),
    )
    # The public mirrors rate-limit bursts; one short pause and a second race usually gets
    # an answer, and a result missing every road and lake is worse than a slower one.
    if not ov_status.get("ok"):
        await asyncio.sleep(4)
        features, ov_status = await asyncio.to_thread(fetch_overpass, coords, radius_m)
    capped = ov_status.pop("capped", {}) if isinstance(ov_status, dict) else {}
    flood = flood_risk(terrain, features["water"], features.get("drains"))
    access = accessibility(features["roads"], features["transit"], coords, plot.get("road_edges") or [])
    sun = sun_path(round(c[0], 6), round(c[1], 6), plot.get("orientation_deg") or 0)
    # Roof available for PV is the towers' combined footprint -- the terrace is the
    # footprint, one storey up.
    roof_sqm = sum(float(t.get("footprint_area") or 0) for t in (project.get("towers") or []))
    solar = solar_potential(round(c[0], 6), round(c[1], 6), roof_sqm,
                            (project.get("solar") or {}), climate)
    # The city reference carries the IS 1893 zone and IS 875-3 Vb for this location, so
    # the GIS hazard screens and the structural module always quote the same clause data.
    city_ref = iscodes.city_reference(project.get("location") or "")
    max_floors = max((int(t.get("floors") or 0) for t in (project.get("towers") or [])), default=0)
    floor_h = 3.0
    max_height = max_floors * floor_h if max_floors else 0
    soil = (project.get("engineering") or {}).get("soil_type") or "medium clay"
    seismic = seismic_hazard(city_ref, terrain, soil)
    wind = wind_profile(c[0], c[1], city_ref, max_height, climate, terrain)
    suit = suitability(terrain, flood, access, sun, seismic, wind)
    build = buildability(terrain, flood, access, features)
    # Seismic flags are buildability flags too — a liquefaction-susceptible site is as
    # hard a constraint as a flood zone, and both belong in the same checklist.
    for r in seismic.get("risk_flags", []):
        if r.get("severity") in ("critical", "warning"):
            build["flags"].append({"id": f"seismic_{r['id']}", "severity": r["severity"],
                                   "title": r["title"], "detail": r["detail"]})
    build["buildable"] = not any(f["severity"] == "critical" for f in build["flags"])
    build["critical_count"] = sum(1 for f in build["flags"] if f["severity"] == "critical")
    build["warning_count"] = sum(1 for f in build["flags"] if f["severity"] == "warning")
    # A clear-site flag is no longer true once real flags exist.
    if len(build["flags"]) > 1:
        build["flags"] = [f for f in build["flags"] if f["id"] != "clear"] or build["flags"][:1]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rules_version": ANALYSIS_RULES_VERSION,
        "radius_m": radius_m,
        "centroid": [round(c[0], 6), round(c[1], 6)],
        "polygon_signature": _signature(coords),
        "vertices": len(coords),
        "features": features,
        "feature_counts": {k: len(v) for k, v in features.items()},
        # True where OpenStreetMap had more of that feature than the query returns, so the
        # count is a lower bound ("1,200+") rather than a total.
        "feature_counts_capped": capped,
        "terrain": terrain,
        "flood": flood,
        "seismic": seismic,
        "wind": wind,
        "sun": sun,
        "solar": solar,
        "accessibility": access,
        "suitability": suit,
        "buildability": build,
        "sources": {"overpass": ov_status, "elevation": el_status},
        # The stored narrative describes numbers computed under specific rules. Carrying
        # it across a re-run is only safe while the boundary AND the rules are unchanged;
        # otherwise the report quotes a flood level the engine no longer reports.
        "ai_summary": (project.get("gis") or {}).get("ai_summary")
        if staleness(project.get("gis"), coords) is None else None,
    }


def _signature(coords):
    return "|".join(f"{round(c[0], 6)},{round(c[1], 6)}" for c in coords)


def staleness(stored, coords, rules_version=ANALYSIS_RULES_VERSION):
    """Why a stored analysis can no longer be trusted, or None while it still holds.

    Two independent things invalidate one: the plot boundary it was measured against has
    moved (`polygon`), or the rules that produced its numbers have changed (`rules`). An
    analysis written before this stamp existed carries no version and is therefore
    treated as out of date, which is the honest reading — nobody knows what rules made it.
    """
    if not stored:
        return None
    if stored.get("polygon_signature") != _signature(coords):
        return "polygon"
    if int(stored.get("rules_version") or 0) != int(rules_version):
        return "rules"
    return None


# The only clause citations the AI may use. Each was checked against the code text in the
# Stage 1 code & bye-law study (Aptimizer_Stage1_Code_Byelaw_Study_v2.pdf). Without this
# list the model "cites" plausible clause numbers that do not say what it claims.
VERIFIED_CLAUSES = [
    "IS 1893 (Part 1):2016 Annex E -- seismic zone by town",
    "IS 1893 (Part 1):2016 Table 3 -- zone factor Z (II 0.10, III 0.16, IV 0.24, V 0.36)",
    "IS 1893 (Part 1):2016 Cl. 3.28 -- Z is the peak ground acceleration considered for design",
    "IS 1893 (Part 1):2016 Cl. 6.4.2 and Table 4 -- design spectra by soil type I/II/III",
    "IS 1893 (Part 1):2016 Cl. 6.3.5.3 and Annex F -- liquefaction of submerged loose sands",
    "IS 875 (Part 3):2015 Annex A as substituted by Amendment No. 2 (2020) -- basic wind speed Vb",
    "IS 875 (Part 3):2015 Cl. 6.3 -- Vz = Vb k1 k2 k3 k4",
    "IS 875 (Part 3):2015 Table 1 -- risk coefficient k1",
    "IS 875 (Part 3):2015 Table 2 -- terrain and height factor k2",
    "IS 875 (Part 3):2015 Cl. 6.3.3 -- topography factor k3",
    "IS 875 (Part 3):2015 Cl. 6.3.4 -- importance factor k4 (cyclonic region)",
    "IS 875 (Part 3):2015 Cl. 7.2 -- design wind pressure pz = 0.6 Vz^2",
    "IS 875 (Part 3):2015 Cl. 7.2.1 -- wind directionality factor Kd",
    "NBC 2016 Part 3 Cl. 12.1.1 -- plinth at least 450 mm above surrounding ground",
    "NBC 2016 Part 3 Table 4 (Cl. 8.2.3.1) -- side and rear open space by building height",
    "NBC 2016 Part 3 Cl. 9.4.1(a) -- height limit from abutting road width and front open space",
    "NBC 2016 Part 3 Cl. 4.6 -- access for high-rise buildings: 12 m road, 6 m fire-tender access",
    "NBC 2016 Part 4 Cl. 2.38 -- high-rise building is 15 m or above",
]


def ai_context(project, gis):
    t, f, a, s = gis["terrain"], gis["flood"], gis["accessibility"], gis["suitability"]
    return {
        "project": {"name": project.get("name"), "location": project.get("location"),
                    "client": project.get("client")},
        "plot": {"area_sqm": (project.get("plot") or {}).get("area_hint"),
                 "orientation_deg": (project.get("plot") or {}).get("orientation_deg"),
                 "vertices": gis["vertices"], "centroid": gis["centroid"]},
        "terrain": {k: t.get(k) for k in ("available", "min_m", "max_m", "mean_m", "relief_m",
                                          "avg_slope_pct", "slope_class", "ring_mean_m")},
        "flood": {"level": f["level"], "score": f["score"], "reasons": f["reasons"],
                  "nearest_water_m": f["nearest_water_m"]},
        "accessibility": {k: a.get(k) for k in ("score", "nearest_road_m", "nearest_road_kind",
                                                "widest_road_m", "nearest_transit_m", "roads_within_100m")},
        "wind": {k: gis["wind"].get(k) for k in ("region", "prevailing", "summer", "winter", "mean_speed_ms")},
        "sun": {"facades": gis["sun"]["facades"],
                "daylight_hours": {p["key"]: p["daylight_hours"] for p in gis["sun"]["paths"]}},
        "nearby": gis["feature_counts"],
        "suitability": {"score": s["score"], "grade": s["grade"],
                        "breakdown": [{"factor": b["factor"], "score": b["score"],
                                       "contribution": b["contribution"]} for b in s["breakdown"]]},
        "buildability": gis["buildability"],
        "verified_clauses": VERIFIED_CLAUSES,
    }
