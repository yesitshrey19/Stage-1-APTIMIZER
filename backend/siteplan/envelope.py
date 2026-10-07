"""STAGE 1 — the buildable envelope.

plot polygon (lat/lng) -> repaired planar polygon -> per-edge inward setback -> envelope.

Everything the later stages place must be contained in this envelope; it is the one hard
constraint that is never traded off, so it is computed once here and handed down.

Why the inset is a boolean difference rather than `polygon.buffer(-d)`
---------------------------------------------------------------------
A single negative buffer can only apply one distance to the whole boundary, but front,
rear and side setbacks differ. The exact variable-offset inset is

    envelope = P \\ union( buffer(edge_i, d_i) )   ==   { p in P : dist(p, edge_i) >= d_i }

which degenerates to `P.buffer(-d)` when every d_i is equal (round caps make the buffer a
true distance field, including around corners). It needs no special-casing for concave
boundaries, and when a narrow waist is eaten through it simply yields a MultiPolygon —
which is the geometrically correct answer, not an error.
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shapely import make_valid
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .config import SiteLayoutConfig
from .errors import LayoutError
from .frame import LocalFrame, geom_to_latlng, geom_to_local, polygons_of

EDGE_CLASSES = ("front", "rear", "side")


# ---------------------------------------------------------------- ring hygiene
def clean_ring(coords: Sequence[Sequence[float]],
               tol: float = 1e-9) -> Tuple[List[Tuple[float, float]], List[int]]:
    """Drop consecutive duplicates and an explicit closing vertex.

    Returns (ring, original_indices) so an edge in the cleaned ring can still be traced
    back to the vertex index the user's `road_edges` refer to.
    """
    ring: List[Tuple[float, float]] = []
    origin_idx: List[int] = []
    for i, c in enumerate(coords):
        pt = (float(c[0]), float(c[1]))
        if ring and abs(pt[0] - ring[-1][0]) < tol and abs(pt[1] - ring[-1][1]) < tol:
            continue
        ring.append(pt)
        origin_idx.append(i)
    while len(ring) > 1 and abs(ring[0][0] - ring[-1][0]) < tol and abs(ring[0][1] - ring[-1][1]) < tol:
        ring.pop()
        origin_idx.pop()
    return ring, origin_idx


def signed_area(ring: Sequence[Sequence[float]]) -> float:
    """Shoelace. Positive = counter-clockwise in a +x east / +y north frame."""
    total = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def repair_polygon(ring: Sequence[Sequence[float]]) -> Tuple[Polygon, List[str]]:
    """Build a valid polygon from a raw ring, healing self-intersection.

    A bow-tie resolves into several lobes; we keep the largest and say so rather than
    silently planning on a shape the user did not draw.
    """
    warnings: List[str] = []
    poly = Polygon(ring)
    if poly.is_valid:
        return poly, warnings

    healed = make_valid(poly)
    parts = polygons_of(healed)
    if not parts:
        raise LayoutError(
            "invalid_polygon",
            "The plot boundary is self-intersecting and could not be repaired into an "
            "area. Check for crossed edges and redraw the boundary.",
        )
    kept = parts[0]
    if len(parts) > 1:
        dropped = sum(p.area for p in parts[1:])
        warnings.append(
            f"Plot boundary self-intersects: repaired into {len(parts)} separate lobes. "
            f"Kept the largest ({kept.area:.0f} m2) and discarded {dropped:.0f} m2. "
            "Redraw the boundary without crossed edges for an exact result."
        )
    else:
        warnings.append("Plot boundary was self-intersecting and has been repaired.")
    return kept, warnings


# ---------------------------------------------------------------- edge classification
def _outward_normals(ring: Sequence[Sequence[float]]) -> List[Tuple[float, float]]:
    """Unit outward normal per edge, correct for either winding direction.

    The ring is deliberately NOT re-oriented: `road_edges` index into the user's own
    vertex order, and reorienting would silently renumber the edges under them.
    """
    ccw = signed_area(ring) > 0
    normals: List[Tuple[float, float]] = []
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy) or 1.0
        # CCW ring: outward = (dy, -dx). CW ring: the mirror.
        nx, ny = (dy / length, -dx / length) if ccw else (-dy / length, dx / length)
        normals.append((nx, ny))
    return normals


def classify_edges(ring: Sequence[Sequence[float]],
                   origin_idx: Sequence[int],
                   road_edge_indices: Sequence[int]) -> List[str]:
    """Label every edge front / rear / side.

    Front  — flagged road-facing by the user.
    Rear   — the non-front edge whose outward normal most opposes the mean front normal.
    Side   — everything else.

    With no road edges declared there is no meaningful front, so every edge is a side and
    picks up the default setback.
    """
    n = len(ring)
    road = {int(i) for i in road_edge_indices}
    is_front = [origin_idx[i] in road for i in range(n)]
    if not any(is_front):
        return ["side"] * n

    normals = _outward_normals(ring)
    lengths = [math.dist(ring[i], ring[(i + 1) % n]) for i in range(n)]

    fx = sum(normals[i][0] * lengths[i] for i in range(n) if is_front[i])
    fy = sum(normals[i][1] * lengths[i] for i in range(n) if is_front[i])
    mag = math.hypot(fx, fy)

    classes = ["front" if is_front[i] else "side" for i in range(n)]
    if mag < 1e-9:
        # Front edges face opposite ways (plot fronts two roads) — no single rear.
        return classes

    fx, fy = fx / mag, fy / mag
    rear, best = None, 0.0
    for i in range(n):
        if is_front[i]:
            continue
        dot = normals[i][0] * fx + normals[i][1] * fy
        if dot < best:
            best, rear = dot, i
    if rear is not None:
        classes[rear] = "rear"
    return classes


# ---------------------------------------------------------------- envelope
@dataclass
class EnvelopeResult:
    frame: LocalFrame
    plot: Polygon                    # repaired plot, local metres
    envelope: BaseGeometry           # Polygon or MultiPolygon, local metres
    edges: List[Dict[str, Any]]
    config: SiteLayoutConfig
    warnings: List[str] = field(default_factory=list)

    @property
    def parts(self) -> List[Polygon]:
        return polygons_of(self.envelope)

    def to_dict(self) -> Dict[str, Any]:
        plot_area = self.plot.area
        env_area = self.envelope.area
        return {
            "ok": True,
            "stage": "envelope",
            "frame": {"origin": self.frame.origin},
            "plot": {
                "area_sqm": round(plot_area, 2),
                "perimeter_m": round(self.plot.exterior.length, 2),
                "vertices": len(self.plot.exterior.coords) - 1,
                "polygons": geom_to_latlng(self.plot, self.frame),
            },
            "envelope": {
                "area_sqm": round(env_area, 2),
                "pct_of_plot": round(env_area / plot_area * 100, 2) if plot_area else 0.0,
                "part_count": len(self.parts),
                "polygons": geom_to_latlng(self.envelope, self.frame),
                "polygons_local": geom_to_local(self.envelope),
            },
            "edges": self.edges,
            "config": self.config.to_dict(),
            "warnings": self.warnings,
        }


def _inset(poly: Polygon, ring: Sequence[Sequence[float]], setbacks: Sequence[float],
           cfg: SiteLayoutConfig) -> BaseGeometry:
    """Variable-offset inward inset — see the module docstring for the identity used.

    Each offset carries `cfg.setback_safety` on top of the nominal setback so that
    discretisation of the round offset (and `simplify`, if enabled) can only ever make
    the envelope more conservative, never encroach on the setback.
    """
    strips = []
    n = len(ring)
    for i in range(n):
        d = float(setbacks[i])
        if d <= 0:
            continue
        edge = LineString([ring[i], ring[(i + 1) % n]])
        strips.append(edge.buffer(d + cfg.setback_safety, quad_segs=cfg.buffer_quad_segs,
                                  cap_style="round", join_style="round"))
    if not strips:
        return poly
    return poly.difference(unary_union(strips))


def _verify_setbacks(envelope: BaseGeometry, ring: Sequence[Sequence[float]],
                     setbacks: Sequence[float]) -> Optional[Tuple[int, float, float]]:
    """The hard constraint, enforced rather than assumed.

    Returns (edge index, required, actual) for the first edge the envelope encroaches on,
    or None when every clearance holds.
    """
    n = len(ring)
    for i in range(n):
        required = float(setbacks[i])
        if required <= 0:
            continue
        edge = LineString([ring[i], ring[(i + 1) % n]])
        actual = edge.distance(envelope)
        if actual < required:
            return i, required, actual
    return None


def _clean_regions(geom: BaseGeometry, cfg: SiteLayoutConfig) -> BaseGeometry:
    """Drop slivers left by the boolean op and smooth buffer chatter."""
    parts = [p for p in polygons_of(geom) if p.area >= cfg.min_region_area]
    if not parts:
        return Polygon()
    cleaned = []
    for p in parts:
        q = p.buffer(0)
        if cfg.simplify_tolerance > 0:
            q = q.simplify(cfg.simplify_tolerance, preserve_topology=True)
        cleaned += [g for g in polygons_of(q) if g.area >= cfg.min_region_area]
    return unary_union(cleaned) if cleaned else Polygon()


def build_envelope(coordinates: Sequence[Sequence[float]],
                   road_edges: Optional[Sequence[Dict[str, Any]]] = None,
                   config: Optional[SiteLayoutConfig] = None) -> EnvelopeResult:
    """Plot polygon in [lat, lng] -> buildable envelope. Raises LayoutError when unusable."""
    cfg = config or SiteLayoutConfig()
    coords = list(coordinates or [])
    if len(coords) < 3:
        raise LayoutError(
            "no_polygon",
            "Draw a plot boundary with at least 3 vertices in Plot & Site before "
            "generating a site layout.",
            vertices=len(coords),
        )

    frame = LocalFrame.from_coords(coords)
    raw_ring, origin_idx = clean_ring(frame.ring_to_local(coords))
    if len(raw_ring) < 3:
        raise LayoutError(
            "degenerate_polygon",
            "The plot boundary collapses to fewer than 3 distinct points. Check for "
            "duplicated vertices.",
            vertices=len(raw_ring),
        )

    plot, warnings = repair_polygon(raw_ring)

    # Repair can reshape the ring (a healed bow-tie has different vertices), in which case
    # the user's edge indices no longer describe it — fall back to a uniform setback.
    healed = len(plot.exterior.coords) - 1 != len(raw_ring)
    ring = [(x, y) for x, y in plot.exterior.coords[:-1]]
    if healed:
        origin_idx = list(range(len(ring)))
        road_indices: List[int] = []
        if road_edges:
            warnings.append("Road-facing edge flags were dropped because the boundary had "
                            "to be repaired; the default setback is applied to all edges.")
    else:
        road_indices = [int(r.get("edge_index", -1)) for r in (road_edges or [])
                        if isinstance(r, dict)]

    classes = classify_edges(ring, origin_idx, road_indices)
    setbacks = [cfg.setbacks.for_class(c) for c in classes]

    envelope = _clean_regions(_inset(plot, ring, setbacks, cfg), cfg)

    if envelope.is_empty:
        raise LayoutError(
            "envelope_collapsed",
            f"The setbacks consume the whole plot: a {min(setbacks):.1f}-{max(setbacks):.1f} m "
            f"inset leaves no buildable area inside {plot.area:.0f} m2. Reduce the setbacks "
            "or enlarge the plot boundary.",
            plot_area_sqm=round(plot.area, 2),
            setbacks_m=sorted({round(s, 2) for s in setbacks}),
        )

    # The two guarantees every later stage depends on, checked rather than assumed. A hair
    # of tolerance absorbs floating-point noise from the boolean op; anything larger is a
    # real defect and must surface here, not as a tower sitting outside the boundary.
    if not plot.buffer(1e-6).contains(envelope):
        raise LayoutError(
            "containment_failed",
            "Internal error: the computed envelope is not contained in the plot boundary.",
        )
    breach = _verify_setbacks(envelope, ring, setbacks)
    if breach is not None:
        index, required, actual = breach
        raise LayoutError(
            "setback_breached",
            f"Internal error: the envelope clears edge {origin_idx[index]} by only "
            f"{actual:.3f} m against a required setback of {required:.2f} m.",
        )

    edges = [
        {
            "index": origin_idx[i],
            "class": classes[i],
            "setback_m": round(setbacks[i], 2),
            "length_m": round(math.dist(ring[i], ring[(i + 1) % len(ring)]), 2),
        }
        for i in range(len(ring))
    ]

    if len(polygons_of(envelope)) > 1:
        warnings.append(
            f"The setbacks split the buildable area into {len(polygons_of(envelope))} "
            "disjoint regions. Each is packed independently."
        )

    return EnvelopeResult(frame=frame, plot=plot, envelope=envelope,
                          edges=edges, config=cfg, warnings=warnings)
