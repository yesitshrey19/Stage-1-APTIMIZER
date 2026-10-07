"""STAGE 2 — road and amenity reservation.

Carves circulation and standalone amenity footprints out of the buildable envelope
*before* any tower is placed, so stage 3 only ever sees land it may actually build on.

Order is deliberate and mirrors how a site plan is actually drawn:

  1. Development blocks — the largest straight-sided rectangles the envelope can hold,
                        laid out on the site's own grid. Roads are cut from these, which
                        is what makes every corridor straight by construction rather than
                        a curve that happens to be within tolerance. See `blocks.py`.
  2. Perimeter ring   — the fire-tender lifeline, four straight bands around each block.
  3. Internal drives  — full-span straight spines, added where the core is deep enough to
                        strand land beyond `road.max_distance_to_road` from a road, or
                        where a core deep enough for two rows of flats has no drive at all.
  4. Amenity blocks   — the integrated clubhouse and anything else configured, placed
                        against the circulation network the way a clubhouse sits on the
                        approach road, rather than dropped in the middle where it would
                        split the core.

Whatever survives is the residual packable region handed to stage 3. Land inside the
envelope that no block could cover is landscape, and is reported as such.
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shapely.affinity import rotate, translate
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .blocks import BlockNetwork, RoadCorridor, build_network, corridors_to_dict
from .config import AmenityBlock, SiteLayoutConfig
from .envelope import EnvelopeResult, build_envelope
from .errors import LayoutError
from .frame import geom_to_latlng, geom_to_local, polygons_of

EMPTY = Polygon()


# ---------------------------------------------------------------- helpers
def _clean(geom: BaseGeometry, cfg: SiteLayoutConfig) -> BaseGeometry:
    parts = [p for p in polygons_of(geom) if p.area >= cfg.min_region_area]
    return unary_union(parts) if parts else EMPTY


def _area(geom: Optional[BaseGeometry]) -> float:
    return float(geom.area) if geom is not None and not geom.is_empty else 0.0


# ---------------------------------------------------------------- 1-3. straight network
def road_network(envelope: BaseGeometry,
                 cfg: SiteLayoutConfig) -> Tuple[Optional[BlockNetwork], List[str]]:
    """Build an orthogonal network for rectangular sites, adaptive roads otherwise.

    Returns (network, warnings). A network of None means roads are disabled or no usable
    development area remains; the caller falls back to the envelope for packing.
    """
    if not cfg.road.enabled:
        return None, []

    network = build_network(
        envelope,
        ring_width=max(cfg.road.ring_width, 0.0),
        ring_offset=max(cfg.road.ring_offset, 0.0),
        driveway_width=max(cfg.road.driveway_width, 0.0),
        max_distance_to_road=max(cfg.road.max_distance_to_road, 0.0),
        central_spine_min_core=max(cfg.road.central_spine_min_core, 0.0),
        safety=cfg.setback_safety,
        max_blocks=max(int(cfg.road.max_blocks), 1),
        min_block_area=max(cfg.road.min_block_area, 0.0),
        min_region_area=cfg.min_region_area,
        quad_segs=cfg.buffer_quad_segs,
    )
    if network is None:
        return None, ["No usable development area fits inside the buildable envelope, "
                      "so no circulation was reserved."]
    return network, list(network.warnings)


# ---------------------------------------------------------------- shared helpers
def _principal_axis(part: Polygon) -> Tuple[float, float, float]:
    """(angle of the long axis in degrees, long extent, short extent)."""
    rect = part.minimum_rotated_rectangle
    pts = list(rect.exterior.coords[:-1])
    if len(pts) < 4:
        minx, miny, maxx, maxy = part.bounds
        return 0.0, maxx - minx, maxy - miny
    e1 = math.dist(pts[0], pts[1])
    e2 = math.dist(pts[1], pts[2])
    if e1 >= e2:
        a, b, long_extent, short_extent = pts[0], pts[1], e1, e2
    else:
        a, b, long_extent, short_extent = pts[1], pts[2], e2, e1
    angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    return angle, long_extent, short_extent


# ---------------------------------------------------------------- 3. amenities
@dataclass
class AmenityPlacement:
    key: str
    name: str
    polygon: Polygon
    width_m: float
    depth_m: float
    rotation_deg: float
    height_m: float
    floors: int
    requested_area_sqm: float
    programme: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def area_sqm(self) -> float:
        return self.polygon.area

    @property
    def gross_floor_area_sqm(self) -> float:
        """Accommodation the block actually delivers — the point of stacking it."""
        return self.area_sqm * max(int(self.floors), 1)


def resolve_amenity_size(block: AmenityBlock, plot_area: float) -> Optional[Tuple[float, float, float]]:
    """(width, depth, requested area) from whichever sizing mode the block declares.

    Precedence is explicit dimensions -> explicit area -> share of plot area, so a user
    who types real dimensions is never overridden by a leftover percentage.
    """
    if block.dimensions and len(block.dimensions) == 2:
        w, d = float(block.dimensions[0]), float(block.dimensions[1])
        if w > 0 and d > 0:
            return w, d, w * d
    area = None
    if block.area_sqm and block.area_sqm > 0:
        area = float(block.area_sqm)
    elif block.plot_area_pct and block.plot_area_pct > 0:
        area = plot_area * float(block.plot_area_pct) / 100.0
    if not area or area <= 0:
        return None
    if block.min_area_sqm and area < float(block.min_area_sqm):
        area = float(block.min_area_sqm)
    if block.max_area_sqm and area > float(block.max_area_sqm):
        area = float(block.max_area_sqm)
    aspect = max(float(block.aspect or 1.0), 0.1)
    w = math.sqrt(area * aspect)
    return w, area / w, area


def _boundary_candidates(region: BaseGeometry, step: float, cap: int = 96) -> List[Tuple[float, float, float]]:
    """(x, y, inward-normal angle in degrees) sampled around a region's outer boundary."""
    out: List[Tuple[float, float, float]] = []
    for part in polygons_of(region):
        ring = part.exterior
        length = ring.length
        if length <= 0:
            continue
        n = max(4, min(cap, int(length / max(step, 0.5))))
        for i in range(n):
            s = length * i / n
            p = ring.interpolate(s)
            q = ring.interpolate((s + max(length / n, 0.5)) % length)
            tx, ty = q.x - p.x, q.y - p.y
            mag = math.hypot(tx, ty)
            if mag < 1e-9:
                continue
            tx, ty = tx / mag, ty / mag
            for nx, ny in ((-ty, tx), (ty, -tx)):
                if part.contains(Point(p.x + nx * 0.5, p.y + ny * 0.5)):
                    out.append((p.x, p.y, math.degrees(math.atan2(ny, nx))))
                    break
    return out


def _interior_candidates(region: BaseGeometry, step: float, cap: int = 320):
    """Candidate amenity centres sampled across each usable parcel, centre-first.

    The previous edge-only search made every standalone building compete for a corner
    position. Sampling the interior lets a clubhouse sit beside the shared green while
    retaining the road-distance and fragmentation checks in `_place_one`.
    """
    out = []
    spacing = max(float(step), 3.0)
    angles = []
    for part in polygons_of(region):
        axis, _, _ = _principal_axis(part)
        angles = list(dict.fromkeys((round(axis, 2), round((axis + 90) % 180, 2), 0.0)))
        minx, miny, maxx, maxy = part.bounds
        nx = max(1, int(math.ceil((maxx - minx) / spacing)))
        ny = max(1, int(math.ceil((maxy - miny) / spacing)))
        stride = max(1, int(math.ceil(math.sqrt(nx * ny / max(cap, 1)))))
        target = part.centroid if part.contains(part.centroid) else part.representative_point()
        radius = max(math.hypot(maxx - minx, maxy - miny) / 2.0, 1.0)

        # Always include a point in the actual parcel, even when a narrow parcel's grid
        # happens to place every sample between cells.
        centres = [(target.x, target.y)]
        for row in range(0, ny, stride):
            cy = miny + min((row + 0.5) * spacing, maxy - miny)
            for col in range(0, nx, stride):
                cx = minx + min((col + 0.5) * spacing, maxx - minx)
                centres.append((cx, cy))

        for cx, cy in centres:
            if not part.contains(Point(cx, cy)):
                continue
            for angle in angles:
                out.append((cx, cy, angle, target.x, target.y, radius))
    return out


def _rect(cx: float, cy: float, w: float, d: float, angle_deg: float) -> Polygon:
    r = box(-w / 2.0, -d / 2.0, w / 2.0, d / 2.0)
    return translate(rotate(r, angle_deg, origin=(0, 0)), cx, cy)


# An amenity further than this from the circulation network has no way in: a clubhouse in
# the middle of the packable land serves nobody. Road access is therefore a constraint on
# placement, not one more preference to trade off against spread or centrality.
AMENITY_ROAD_REACH_M = 2.0


def _place_one(region: BaseGeometry, roads: BaseGeometry, placed: Sequence[Polygon],
               w: float, d: float, target_sep: float,
               cfg: SiteLayoutConfig,
               green: Optional[BaseGeometry] = None) -> Optional[Polygon]:
    """Best position for one w x d block inside `region`.

    Interior positions are scored for centrality, road access, low fragmentation,
    separation from other amenities, and proximity to the shared green. Positions further
    than `AMENITY_ROAD_REACH_M` from the road network are tried only as a fallback, after
    every road-served position has been rejected -- dispersion must never win by exiling a
    block into the middle of the land.
    """
    step = max(3.0, min(w, d) / 2.0)
    candidates = _interior_candidates(region, step)
    if not candidates:
        return None

    a = cfg.amenities
    region_area = region.area or 1.0
    sep_scale = max(target_sep, 1.0)
    have_roads = roads is not None and not roads.is_empty

    best: Optional[Polygon] = None
    passes = (True, False) if have_roads else (False,)
    for require_road in passes:
        best = None
        best_score = float("-inf")
        for cx, cy, angle, target_x, target_y, centrality_radius in candidates:
            for ww, dd in ((w, d), (d, w)):
                rect = _rect(cx, cy, ww, dd, angle)
                if not region.contains(rect):
                    continue
                if have_roads and require_road and rect.distance(roads) > AMENITY_ROAD_REACH_M:
                    continue

                parts = polygons_of(region.difference(rect.buffer(a.clearance)))
                compactness = (parts[0].area / region_area) if parts else 0.0

                if placed:
                    nearest = min(rect.distance(p) for p in placed)
                    spread = min(nearest / sep_scale, 1.0)
                else:
                    spread = 1.0

                if have_roads:
                    road = 1.0 - min(rect.distance(roads) / sep_scale, 1.0)
                else:
                    road = 1.0

                centre = rect.centroid
                centrality = 1.0 - min(math.hypot(centre.x - target_x, centre.y - target_y)
                                       / centrality_radius, 1.0)
                green_access = (1.0 - min(rect.distance(green) / sep_scale, 1.0)
                                if green is not None and not green.is_empty else 0.0)
                score = (a.compactness_weight * compactness
                         + a.spread_weight * spread
                         + a.road_weight * road
                         + a.centrality_weight * centrality
                         + a.green_weight * green_access)
                if score > best_score:
                    best_score, best = score, rect
        if best is not None:
            return best
    return best


def place_amenities(region: BaseGeometry, roads: BaseGeometry, plot_area: float,
                    cfg: SiteLayoutConfig,
                    green: Optional[BaseGeometry] = None
                    ) -> Tuple[List[AmenityPlacement], BaseGeometry, List[str]]:
    """Place every configured amenity block, largest first. Returns (placed, region, warnings)."""
    warnings: List[str] = []
    if not cfg.amenities.enabled or not cfg.amenities.blocks:
        return [], region, warnings

    sized: List[Tuple[AmenityBlock, float, float, float]] = []
    for block in cfg.amenities.blocks:
        resolved = resolve_amenity_size(block, plot_area)
        if resolved is None:
            warnings.append(f"Amenity '{block.name}' has no size (needs dimensions, an area "
                            "or a plot-area percentage) and was skipped.")
            continue
        w, d, requested = resolved
        sized.append((block, w, d, requested))

    if not sized:
        return [], region, warnings

    # Honour the overall cap by shrinking everything proportionally rather than dropping
    # blocks — a half-size gym still communicates the intent; a missing one does not.
    cap_area = plot_area * cfg.amenities.total_cap_pct / 100.0
    total = sum(s[3] for s in sized)
    if cap_area > 0 and total > cap_area:
        scale = math.sqrt(cap_area / total)
        sized = [(b, w * scale, d * scale, r * scale * scale) for b, w, d, r in sized]
        warnings.append(
            f"Amenity footprints totalled {total:.0f} m2, above the "
            f"{cfg.amenities.total_cap_pct}% cap ({cap_area:.0f} m2); every block was "
            f"scaled to {scale * 100:.0f}% of its requested size.")

    # Separation at which the spread term saturates. Derived from the packable region so
    # it scales with the site rather than being a fixed metre value that means "far apart"
    # on a plot and "touching" on a township.
    target_sep = cfg.amenities.target_separation or (
        math.sqrt(max(region.area, 1.0)) * cfg.amenities.separation_scale)

    placed: List[AmenityPlacement] = []
    footprints: List[Polygon] = []
    for block, w, d, requested in sorted(sized, key=lambda s: s[3], reverse=True):
        if region.is_empty:
            warnings.append(f"No room left for amenity '{block.name}'.")
            continue
        rect = _place_one(region, roads, footprints, w, d, target_sep, cfg, green)
        if rect is None:
            warnings.append(
                f"Amenity '{block.name}' ({w:.1f} x {d:.1f} m) does not fit in the "
                "remaining land and was skipped.")
            continue
        footprints.append(rect)
        placed.append(AmenityPlacement(
            key=block.key, name=block.name, polygon=rect,
            width_m=round(w, 2), depth_m=round(d, 2),
            rotation_deg=0.0, height_m=block.height_m, floors=block.floors,
            requested_area_sqm=round(requested, 2),
            programme=[dict(p) for p in (block.programme or []) if isinstance(p, dict)]))
        region = _clean(region.difference(rect.buffer(cfg.amenities.clearance)), cfg)

    return placed, region, warnings


# ---------------------------------------------------------------- 4. community green
def reserve_green(region: BaseGeometry, plot_area: float,
                  cfg: SiteLayoutConfig) -> Tuple[BaseGeometry, BaseGeometry, List[str]]:
    """Carve a central landscaped green out of the packable land.

    Taken from the deepest part of the largest region — the point furthest from any road,
    which is both the least useful land for a road-served building and where a shared
    green actually belongs. Reserved before packing so it cannot be quietly built over.
    """
    warnings: List[str] = []
    o = cfg.open_space
    if not o.enabled or o.green_pct_of_plot <= 0 or region.is_empty:
        return EMPTY, region, warnings

    target = plot_area * o.green_pct_of_plot / 100.0
    if target < o.min_area:
        return EMPTY, region, warnings

    parts = polygons_of(region)
    if not parts:
        return EMPTY, region, warnings
    host = parts[0]

    # Grow a disc at the region's pole of inaccessibility until it reaches the target
    # area, clipped to the region so the green never escapes the packable land.
    centre = host.representative_point()
    try:
        from shapely import centroid as _c  # noqa: F401
        centre = host.buffer(-min(host.bounds[2] - host.bounds[0],
                                  host.bounds[3] - host.bounds[1]) / 4.0).representative_point()
    except Exception:
        pass
    if not host.contains(centre):
        centre = host.representative_point()

    radius = math.sqrt(target / math.pi)
    green = _clean(centre.buffer(radius, quad_segs=cfg.buffer_quad_segs).intersection(host), cfg)
    if green.is_empty or green.area < o.min_area:
        return EMPTY, region, warnings

    remaining = _clean(region.difference(green.buffer(o.clearance)), cfg)
    warnings.append(f"Reserved a {green.area:.0f} m2 community green "
                    f"({green.area / plot_area * 100:.1f}% of the plot).")
    return green, remaining, warnings


# ---------------------------------------------------------------- orchestrator
@dataclass
class ReserveResult:
    envelope: EnvelopeResult
    ring: BaseGeometry
    driveways: BaseGeometry
    amenities: List[AmenityPlacement]
    residual: BaseGeometry
    green: BaseGeometry = EMPTY
    network: Optional[BlockNetwork] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def roads(self) -> BaseGeometry:
        parts = [g for g in (self.ring, self.driveways) if g is not None and not g.is_empty]
        return unary_union(parts) if parts else EMPTY

    @property
    def corridors(self) -> List[RoadCorridor]:
        return self.network.corridors if self.network else []

    @property
    def blocks(self) -> List[Polygon]:
        return self.network.blocks if self.network else []

    def to_dict(self) -> Dict[str, Any]:
        frame = self.envelope.frame
        plot_area = self.envelope.plot.area
        env_area = self.envelope.envelope.area
        amenity_area = sum(a.area_sqm for a in self.amenities)
        residual_area = _area(self.residual)

        base = self.envelope.to_dict()
        base.update({
            "stage": "reserve",
            "roads": {
                "ring_area_sqm": round(_area(self.ring), 2),
                "driveway_area_sqm": round(_area(self.driveways), 2),
                "total_area_sqm": round(_area(self.roads), 2),
                "ring_width_m": self.envelope.config.road.ring_width,
                "driveway_width_m": self.envelope.config.road.driveway_width,
                "ring_polygons": geom_to_latlng(self.ring, frame),
                "driveway_polygons": geom_to_latlng(self.driveways, frame),
                "ring_polygons_local": geom_to_local(self.ring),
                "driveway_polygons_local": geom_to_local(self.driveways),
                # Every corridor as its own straight rectangle plus the centreline it was
                # set out from. A renderer that has to infer a centreline from a polygon
                # gets it wrong the moment the polygon is not the shape it assumed, which
                # is why the engine states it rather than leaving it to be guessed.
                "corridors": corridors_to_dict(self.corridors, frame),
                "centrelines_local": [
                    [[round(x, 3), round(y, 3)] for x, y in c.centreline.coords]
                    for c in self.corridors
                ],
                "grid_angle_deg": self.network.angle_deg if self.network else 0.0,
            },
            "blocks": {
                "count": len(self.blocks),
                "area_sqm": round(sum(b.area for b in self.blocks), 2),
                "polygons": geom_to_latlng(unary_union(self.blocks) if self.blocks else EMPTY, frame),
                "polygons_local": geom_to_local(unary_union(self.blocks) if self.blocks else EMPTY),
            },
            "amenities": [
                {
                    "key": a.key, "name": a.name,
                    "area_sqm": round(a.area_sqm, 2),
                    "requested_area_sqm": a.requested_area_sqm,
                    "width_m": a.width_m, "depth_m": a.depth_m,
                    "height_m": a.height_m, "floors": a.floors,
                    "gross_floor_area_sqm": round(a.gross_floor_area_sqm, 2),
                    "programme": a.programme,
                    "polygons": geom_to_latlng(a.polygon, frame),
                    "polygons_local": geom_to_local(a.polygon),
                }
                for a in self.amenities
            ],
            "green": {
                "area_sqm": round(_area(self.green), 2),
                "pct_of_plot": round(_area(self.green) / plot_area * 100, 2) if plot_area else 0.0,
                "polygons": geom_to_latlng(self.green, frame),
                "polygons_local": geom_to_local(self.green),
            },
            "residual": {
                "area_sqm": round(residual_area, 2),
                "pct_of_envelope": round(residual_area / env_area * 100, 2) if env_area else 0.0,
                "pct_of_plot": round(residual_area / plot_area * 100, 2) if plot_area else 0.0,
                "part_count": len(polygons_of(self.residual)),
                "polygons": geom_to_latlng(self.residual, frame),
                "polygons_local": geom_to_local(self.residual),
            },
            "reservation_summary": {
                "plot_area_sqm": round(plot_area, 2),
                "envelope_area_sqm": round(env_area, 2),
                "road_area_sqm": round(_area(self.roads), 2),
                "amenity_area_sqm": round(amenity_area, 2),
                "amenity_pct_of_plot": round(amenity_area / plot_area * 100, 2) if plot_area else 0.0,
                "packable_area_sqm": round(residual_area, 2),
            },
        })
        base["warnings"] = self.envelope.warnings + self.warnings
        return base


def reserve(env: EnvelopeResult, cfg: Optional[SiteLayoutConfig] = None) -> ReserveResult:
    """Envelope -> circulation + amenities carved out -> residual packable region."""
    cfg = cfg or env.config
    warnings: List[str] = []

    network, w = road_network(env.envelope, cfg)
    warnings += w

    if network is None:
        ring = drives = EMPTY
        residual = env.envelope
        leftover: BaseGeometry = EMPTY
    else:
        ring = _clean(network.ring_geometry, cfg)
        drives = _clean(network.spine_geometry, cfg)
        residual = _clean(network.residual, cfg)
        leftover = _clean(network.leftover, cfg)
        if residual.is_empty and not network.residual.is_empty:
            # The ring fitted, so _serve_block had nothing to complain about, but what it
            # left inside is too small to be a region and _clean drops it. Saying nothing
            # here returns an empty packable area with every warning pointing somewhere
            # else -- the reader concludes the packer failed, when the truth is the site
            # never had room for one.
            warnings.append(
                f"The land inside the {cfg.road.ring_width:g} m access ring is only "
                f"{network.residual.area:.0f} m2, below the {cfg.min_region_area:g} m2 "
                f"minimum usable region, so nothing remains for towers. Reduce the "
                f"setbacks or the ring width.")

    roads = unary_union([g for g in (ring, drives) if not g.is_empty]) if (
        not ring.is_empty or not drives.is_empty) else EMPTY

    # Reserve green first so it has an actual site position; amenities can be placed
    # beside it while retaining centrality and road access.
    green, residual, w = reserve_green(residual, env.plot.area, cfg)
    warnings += w

    amenities, residual, w = place_amenities(residual, roads, env.plot.area, cfg, green)
    warnings += w

    # Offset land is intentionally reserved as landscaped buffer, so it joins the green.
    if not leftover.is_empty:
        green = _clean(unary_union([g for g in (green, leftover) if not g.is_empty]), cfg)

    result = ReserveResult(envelope=env, ring=ring, driveways=drives,
                           amenities=amenities, residual=residual, green=green,
                           network=network, warnings=warnings)

    # Stage 1's containment guarantee must survive stage 2: everything reserved, and the
    # residual handed to packing, still lives inside the envelope.
    guard = env.envelope.buffer(1e-6)
    for name, geom in (("ring", ring), ("driveways", drives), ("residual", residual),
                       ("green", green)):
        if not geom.is_empty and not guard.contains(geom):
            raise LayoutError("containment_failed",
                              f"Internal error: reserved {name} geometry escapes the buildable envelope.")
    for a in amenities:
        if not guard.contains(a.polygon):
            raise LayoutError("containment_failed",
                              f"Internal error: amenity '{a.name}' escapes the buildable envelope.")
    return result


def reserve_from_coordinates(coordinates: Sequence[Sequence[float]],
                             road_edges: Optional[Sequence[Dict[str, Any]]] = None,
                             config: Optional[SiteLayoutConfig] = None) -> ReserveResult:
    cfg = config or SiteLayoutConfig()
    return reserve(build_envelope(coordinates, road_edges, cfg), cfg)
