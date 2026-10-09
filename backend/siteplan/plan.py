"""Full pipeline: envelope -> reservation -> tower packing -> serialisable layout."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from shapely.geometry.base import BaseGeometry

from .config import SiteLayoutConfig
from .envelope import build_envelope
from .errors import LayoutError
from .fitness import FitnessResult, PackContext, TowerPlacement, evaluate
from .frame import geom_to_latlng, geom_to_local, polygons_of
from .ga import refine
from .pack import _name, greedy_pack
from .parking import ParkingBay, bays_geometry, generate_bays
from .reserve import ReserveResult, reserve
from .version import ENGINE_VERSION, polygon_signature


def _pedestrian_links(res: ReserveResult, towers: Sequence[TowerPlacement], frame) -> List[dict]:
    """Connect each building and the shared green to the common road network.

    These are at-grade access paths, not links between tower roofs. Candidate paths are
    sampled around each footprint so the shortest unobstructed connection is selected.
    """
    from shapely.geometry import LineString
    from shapely.ops import nearest_points

    roads = res.roads
    if roads is None or roads.is_empty:
        return []

    sources = [(tower.name, tower.polygon) for tower in towers]
    sources.extend((amenity.name, amenity.polygon) for amenity in res.amenities)
    if res.green is not None and not res.green.is_empty:
        sources.append(("Community green", res.green))

    allowed = res.envelope.envelope.buffer(1e-6)
    links: List[dict] = []
    for source_index, (label, geom) in enumerate(sources):
        obstacles = [other for i, (_, other) in enumerate(sources) if i != source_index]
        best = None
        for part in polygons_of(geom):
            boundary = part.exterior
            length = boundary.length
            if length <= 0:
                continue
            count = max(8, min(48, int(length / 8.0)))
            for sample in range(count):
                start = boundary.interpolate(length * sample / count)
                _, end = nearest_points(start, roads)
                path = LineString([(start.x, start.y), (end.x, end.y)])
                if path.length < 0.5 or not allowed.covers(path):
                    continue
                if any(path.intersection(obstacle).length > 0.05 for obstacle in obstacles):
                    continue
                if best is None or path.length < best.length:
                    best = path

        if best is not None:
            links.append({
                "label": f"{label} pedestrian access",
                "length_m": round(best.length, 2),
                "path": [frame.to_latlng(x, y) for x, y in best.coords],
                "path_local": [[round(x, 3), round(y, 3)] for x, y in best.coords],
            })
    return links


@dataclass
class LayoutResult:
    reservation: ReserveResult
    towers: List[TowerPlacement]
    fitness: FitnessResult
    bays: List[ParkingBay] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    method: str = "greedy"
    # What stage 3b did, or None when it did not run. Carried so a reader can see
    # whether refinement was attempted and what it bought, rather than being handed a
    # different layout with no account of where it came from.
    refinement: Optional[Dict[str, Any]] = None
    signature: str = ""

    def to_dict(self) -> Dict[str, Any]:
        res = self.reservation
        frame = res.envelope.frame
        cfg = res.envelope.config
        plot_area = res.envelope.plot.area

        floor_area = sum(t.floor_area for t in self.towers)
        footprint = sum(t.footprint_area for t in self.towers)
        units = sum(t.units(cfg) for t in self.towers)
        open_space = plot_area - footprint

        bay_area = sum(b.polygon.area for b in self.bays)
        base = res.to_dict()
        base.update({
            "stage": "layout",
            # Stamped so a consumer can tell a layout apart from the polygon it was
            # generated for. Editing the boundary used to leave a stale layout on the
            # project, which is how towers ended up drawn outside a plot they never
            # belonged to.
            "engine_version": ENGINE_VERSION,
            "polygon_signature": self.signature,
            "surface_parking": {
                "bay_count": len(self.bays),
                "area_sqm": round(bay_area, 2),
                "stall_size_m": [cfg.parking.stall_width, cfg.parking.stall_depth],
                "polygons": geom_to_latlng(bays_geometry(self.bays), frame),
                "polygons_local": geom_to_local(bays_geometry(self.bays)),
            },
            "method": self.method,
            "refinement": self.refinement,
            "pedestrian_links": _pedestrian_links(res, self.towers, frame),
            # `ga_report` is the alias the site-layout UI reads for the telemetry banner.
            "ga_report": self.refinement,
            "towers": [
                {
                    "name": t.name,
                    "polygons": geom_to_latlng(t.polygon, frame),
                    "polygons_local": geom_to_local(t.polygon),
                    "centre_local": [round(t.cx, 3), round(t.cy, 3)],
                    "centre_latlng": frame.to_latlng(t.cx, t.cy),
                    "width_m": round(t.width, 2),
                    "depth_m": round(t.depth, 2),
                    "rotation_deg": round(t.rotation_deg, 2),
                    "floors": t.floors,
                    "floor_height_m": t.floor_height,
                    "height_m": round(t.height_m, 2),
                    "footprint_sqm": round(t.footprint_area, 2),
                    "floor_area_sqm": round(t.floor_area, 2),
                    "units": t.units(cfg),
                }
                for t in self.towers
            ],
            "layout_metrics": {
                "tower_count": len(self.towers),
                "total_buildable_area_sqm": round(floor_area, 2),
                "total_footprint_sqm": round(footprint, 2),
                "achieved_far": round(floor_area / plot_area, 3) if plot_area else 0.0,
                "far_cap": cfg.far_cap,
                "ground_coverage_pct": round(footprint / plot_area * 100, 2) if plot_area else 0.0,
                "open_space_pct": round(open_space / plot_area * 100, 2) if plot_area else 0.0,
                "unit_count": units,
                "surface_bays": len(self.bays),
                "feasible": self.fitness.feasible,
                "score": round(self.fitness.score, 2) if self.fitness.feasible else None,
                "penalties": {k: round(v, 2) for k, v in self.fitness.penalties.items()},
            },
        })
        base["warnings"] = base.get("warnings", []) + self.warnings
        return base


def plan(coordinates: Sequence[Sequence[float]],
         road_edges: Optional[Sequence[Dict[str, Any]]] = None,
         config: Optional[SiteLayoutConfig] = None) -> LayoutResult:
    cfg = config or SiteLayoutConfig()
    env = build_envelope(coordinates, road_edges, cfg)
    res = reserve(env, cfg)

    ctx = PackContext(region=res.residual, roads=res.roads,
                      plot_area=env.plot.area, cfg=cfg, envelope=env.envelope)
    towers, notes = greedy_pack(ctx)
    method, ga_report = "greedy", None

    # Stage 3b. The grid is a seed, not an answer: it cannot slide a tower to clear a
    # spacing shortfall, rotate one block against the others, or trade a storey for a
    # footprint. `refine` is a ratchet -- it returns the seed unless it beat it -- so this
    # can only leave the layout the same or better.
    if cfg.ga.enabled and towers:
        refined, ga_report = refine(towers, ctx)
        if ga_report.get("improved"):
            towers = refined
            _name(towers)          # positions moved, so the north-to-south order did too
            method = "genetic"

    fit = evaluate(towers, ctx)

    # Bays go into whatever open land is left beside a road once the towers are down, so
    # they can never overlap a building by construction.
    occupied = [t.polygon for t in towers] + [a.polygon for a in res.amenities]
    free = res.residual
    if occupied:
        from shapely.ops import unary_union as _uu
        free = res.residual.difference(_uu(occupied).buffer(0.5))
    bays = generate_bays(res.roads, free, cfg)

    warnings = list(notes)
    if not towers:
        warnings.append("No tower footprint fits the packable land at the configured sizes. "
                        "Reduce the setbacks, the ring width or the minimum footprint.")
    if not fit.feasible:
        warnings.append("Layout failed a hard constraint: " + "; ".join(fit.hard_violations[:3]))

    return LayoutResult(reservation=res, towers=towers, fitness=fit, bays=bays,
                        warnings=warnings, method=method, refinement=ga_report,
                        signature=polygon_signature(coordinates))


def plan_site(project: Dict[str, Any],
              overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Stage 3 entry point: project document -> full optimised site layout."""
    plot = project.get("plot") or {}
    raw_coords = plot.get("coordinates") or []
    coords = [[c["lat"], c["lng"]] if isinstance(c, dict) and "lat" in c else c for c in raw_coords]

    overrides = dict(overrides or {})
    if "amenities" not in overrides:
        society_amenities = project.get("society_amenities")
        if society_amenities is not None:
            if len(society_amenities) == 0:
                overrides["amenities"] = {"enabled": False, "blocks": []}
            else:
                blocks = []
                for idx, am in enumerate(society_amenities):
                    key = f"amenity_{idx}"
                    name = am.get("name") or f"Amenity {idx+1}"
                    area = float(am.get("area") or 200.0)
                    blocks.append({
                        "key": key,
                        "name": name,
                        "area_sqm": area,
                        "height_m": 4.5,
                        "floors": 1,
                    })
                overrides["amenities"] = {"enabled": True, "blocks": blocks}

    cfg = SiteLayoutConfig.from_dict(overrides)
    try:
        result = plan(coords, plot.get("road_edges") or [], cfg).to_dict()
    except LayoutError as exc:
        return exc.to_dict()
    if result.get("towers"):
        return result

    # Small-plot fallback. The standard slabs (36 m and longer) are sized for large sites;
    # on a plot of an acre or two the land left after setbacks, the access ring and the
    # amenity blocks is too narrow for any of them, and the layout came back empty. Retry
    # with compact bars, then with the amenities carried in the tower podium instead of
    # standalone blocks. Setbacks and the fire-access ring are never relaxed here -- those
    # are statutory and stay the user's decision.
    compact = {"candidate_widths": [24.0, 27.0, 30.0, 33.0, 36.0, 45.0],
               "min_footprint": 300.0, "min_aspect": 1.8}
    towers_override = dict(overrides.get("towers") or {})
    attempts = [
        ({**overrides, "towers": {**compact, **towers_override}},
         "No standard slab fitted, so compact tower bars (24 m and longer) were used."),
        ({**overrides, "towers": {**compact, **towers_override},
          "amenities": {"enabled": False, "blocks": []}},
         "No standard slab fitted, so compact tower bars were used and the society "
         "amenities are carried in the tower podium instead of standalone blocks."),
    ]
    for attempt, note in attempts:
        try:
            retry = plan(coords, plot.get("road_edges") or [],
                         SiteLayoutConfig.from_dict(attempt)).to_dict()
        except LayoutError:
            continue
        if retry.get("towers"):
            retry["warnings"] = [note] + list(retry.get("warnings") or [])
            retry["small_plot_fallback"] = note
            return retry
    return result
