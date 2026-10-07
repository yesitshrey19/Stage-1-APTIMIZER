"""STAGE 3a — greedy grid packing of towers over the residual region.

For every packable region the packer sweeps candidate footprints, rotations and floor
counts, lays a regular grid at the pitch each combination demands, and keeps the variant
that yields the most buildable floor area. Containment and non-overlap hold by
construction — a candidate is only emitted if the region *contains* its rectangle and the
grid pitch already includes the required light-and-ventilation gap.

Floors interact with packing rather than being chosen afterwards: taller towers demand a
wider spacing, so fewer of them fit. Sweeping the floor count is therefore part of the
optimisation, not a post-step. Packings are memoised on the spacing they induce, so the
whole floor range costs only as many grid layouts as it has distinct pitches.

This is the seed layout. Stage 3b (GA) refines it off the grid; the two share `fitness`
so the seed is never scored more generously than its own refinement.
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

from .config import SiteLayoutConfig
from .fitness import (FitnessResult, PackContext, TowerPlacement, evaluate,
                      required_spacing)
from .frame import polygons_of
from .reserve import _principal_axis


def _candidate_footprints(cfg: SiteLayoutConfig) -> List[Tuple[float, float]]:
    """Footprint sizes inside the configured area and aspect bands, largest first.

    The aspect filter is what keeps the output reading as apartment bars rather than
    cubes — see TowerConfig for why that matters beyond appearance.
    """
    t = cfg.towers
    out = []
    for w in t.candidate_widths:
        for d in t.candidate_depths:
            w, d = float(w), float(d)
            area = w * d
            if not (t.min_footprint <= area <= t.max_footprint):
                continue
            long_side, short_side = max(w, d), min(w, d)
            aspect = long_side / short_side if short_side else 0.0
            if not (t.min_aspect <= aspect <= t.max_aspect):
                continue
            out.append((w, d))
    return sorted(set(out), key=lambda wd: wd[0] * wd[1], reverse=True)


def _grid_in_local(local_prep, bounds: Tuple[float, float, float, float],
                   w: float, d: float, gap: float) -> List[Tuple[float, float]]:
    """Centres of an axis-aligned grid of w x d rectangles inside an already-rotated region.

    Containment is tested in the rotated frame against an axis-aligned `box`, which is far
    cheaper than rebuilding a rotated rectangle for every cell: the rotation is applied
    once to the region per angle, not once per candidate.
    """
    minx, miny, maxx, maxy = bounds
    pitch_x, pitch_y = w + gap, d + gap
    ncols = int((maxx - minx + gap) // pitch_x)
    nrows = int((maxy - miny + gap) // pitch_y)
    if ncols < 1 or nrows < 1:
        return []

    # Centre the grid in the region's oriented extent rather than jamming it into a corner.
    used_w, used_h = ncols * pitch_x - gap, nrows * pitch_y - gap
    x0 = minx + ((maxx - minx) - used_w) / 2.0 + w / 2.0
    y0 = miny + ((maxy - miny) - used_h) / 2.0 + d / 2.0
    hw, hd = w / 2.0, d / 2.0

    centres: List[Tuple[float, float]] = []
    for r in range(nrows):
        cy = y0 + r * pitch_y
        for c in range(ncols):
            cx = x0 + c * pitch_x
            if local_prep.contains(box(cx - hw, cy - hd, cx + hw, cy + hd)):
                centres.append((cx, cy))
    return centres


def _rotations(region: BaseGeometry, cfg: SiteLayoutConfig) -> List[float]:
    """Configured rotations, plus the region's own principal axis.

    A site whose long axis runs at 23 degrees packs far better along 23 than along any
    fixed list, so the region's own orientation is always a candidate.
    """
    angle, _, _ = _principal_axis(region)
    out = list(cfg.towers.rotations_deg) + [angle, angle + 90.0]
    return sorted({round(a % 180.0, 2) for a in out})


@dataclass
class PackResult:
    towers: List[TowerPlacement]
    fitness: FitnessResult
    evaluated: int


def pack_region(region: Polygon, ctx: PackContext) -> List[TowerPlacement]:
    """Best greedy grid for one packable region.

    Loops are ordered angle -> footprint -> floors so the region is rotated and prepared
    once per angle. Footprints run largest-first and floors tallest-first so the running
    best is high early, which lets the upper bound below prune most of the sweep.
    """
    cfg = ctx.cfg
    best: List[TowerPlacement] = []
    best_area = 0.0

    floors_step = 2 if cfg.fast_preview else 1
    floor_values = list(range(cfg.towers.floors_max, cfg.towers.floors_min - 1, -floors_step))
    footprints = _candidate_footprints(cfg)
    angles = _rotations(region, cfg)
    cx0, cy0 = region.centroid.x, region.centroid.y
    region_area = region.area

    # Two packings that yield the same floor area are not equally good places to live. A
    # bar whose long axis runs east-west presents its two long facades to north and
    # south, so every flat on it gets sun and none of them look straight into the next
    # block's flank. The weight is small on purpose: this decides ties, it does not buy
    # orientation with floor area.
    def solar_bonus(angle_deg: float) -> float:
        return 1.0 + cfg.towers.solar_orientation_weight * math.cos(math.radians(angle_deg)) ** 2

    best_score = 0.0

    for angle in angles:
        local = rotate(region, -angle, origin=(cx0, cy0))
        local_prep = prep(local)
        bounds = local.bounds
        span_x, span_y = bounds[2] - bounds[0], bounds[3] - bounds[1]
        theta = math.radians(angle)
        cos_t, sin_t = math.cos(theta), math.sin(theta)

        for w, d in footprints:
            if w > span_x or d > span_y:
                continue
            # Nothing this footprint can achieve, even packed perfectly at full height,
            # beats what we already have — skip the whole floor sweep for it.
            if (region_area // (w * d)) * w * d * cfg.towers.floors_max <= best_area:
                continue

            memo: Dict[float, List[Tuple[float, float]]] = {}
            for floors in floor_values:
                gap = round(max(cfg.towers.spacing_min,
                                cfg.towers.spacing_height_factor * floors * cfg.towers.floor_height), 1)
                if gap not in memo:
                    memo[gap] = _grid_in_local(local_prep, bounds, w, d, gap)
                centres = memo[gap]
                if not centres:
                    continue
                total = len(centres) * w * d * floors
                if total <= best_area:
                    continue
                best_area = total
                unit = rotate(box(-w / 2, -d / 2, w / 2, d / 2), angle, origin=(0, 0))
                best = []
                for lx, ly in centres:
                    dx, dy = lx - cx0, ly - cy0
                    gx = cx0 + dx * cos_t - dy * sin_t
                    gy = cy0 + dx * sin_t + dy * cos_t
                    best.append(TowerPlacement(
                        polygon=translate(unit, gx, gy), cx=gx, cy=gy,
                        width=w, depth=d, rotation_deg=angle,
                        floors=floors, floor_height=cfg.towers.floor_height))
    return best


def _apply_caps(towers: List[TowerPlacement], ctx: PackContext) -> Tuple[List[TowerPlacement], List[str]]:
    """Trim the layout until it satisfies the FAR and ground-coverage caps.

    Floors come off first — losing a storey everywhere is a smaller loss than deleting a
    whole tower — and only when the layout is already at its minimum height does a tower
    get dropped.
    """
    cfg = ctx.cfg
    notes: List[str] = []
    if not towers or not ctx.plot_area:
        return towers, notes

    cap_area = cfg.far_cap * ctx.plot_area
    while towers and sum(t.floor_area for t in towers) > cap_area:
        tallest = max(towers, key=lambda t: t.floors)
        if tallest.floors > cfg.towers.floors_min:
            for t in towers:
                if t.floors == tallest.floors:
                    t.floors -= 1
        else:
            towers.pop()
    if towers and sum(t.floor_area for t in towers) <= cap_area:
        far = sum(t.floor_area for t in towers) / ctx.plot_area
        if far >= cfg.far_cap * 0.999:
            notes.append(f"Layout is limited by the FAR cap of {cfg.far_cap}, not by land.")

    cover_cap = cfg.ground_coverage_cap_pct / 100.0 * ctx.plot_area
    dropped = 0
    while towers and sum(t.footprint_area for t in towers) > cover_cap:
        towers.pop()
        dropped += 1
    if dropped:
        notes.append(f"Dropped {dropped} tower(s) to stay within the "
                     f"{cfg.ground_coverage_cap_pct}% ground-coverage cap.")
    return towers, notes


def _enforce_spacing(towers: List[TowerPlacement],
                     cfg: SiteLayoutConfig) -> Tuple[List[TowerPlacement], List[str]]:
    """Resolve light-and-ventilation gaps that the per-region grids cannot see.

    Each region is packed independently, so its grid pitch guarantees spacing *within* a
    region but says nothing about two towers facing each other across a driveway. Rather
    than delete a building, trim a storey off the taller of the offending pair — the
    requirement scales with height, so losing one floor often clears the breach — and drop
    a tower only once the pair is already at the minimum height.
    """
    notes: List[str] = []
    trimmed = dropped = 0

    while True:
        worst, deficit = None, 1e-9
        for i in range(len(towers)):
            for j in range(i + 1, len(towers)):
                need = required_spacing(towers[i], towers[j], cfg)
                short = need - towers[i].polygon.distance(towers[j].polygon)
                if short > deficit:
                    worst, deficit = (i, j), short
        if worst is None:
            break

        i, j = worst
        a, b = towers[i], towers[j]
        taller = a if a.floors >= b.floors else b
        if taller.floors > cfg.towers.floors_min:
            taller.floors -= 1
            trimmed += 1
        else:
            towers.pop(i if a.floor_area <= b.floor_area else j)
            dropped += 1

    if trimmed:
        notes.append(f"Trimmed {trimmed} storey(s) so every pair of towers keeps its "
                     "light-and-ventilation gap across the internal roads.")
    if dropped:
        notes.append(f"Dropped {dropped} tower(s) that could not hold the minimum "
                     "inter-tower spacing.")
    return towers, notes


def _apply_tower_cap(towers: List[TowerPlacement],
                     cfg: SiteLayoutConfig) -> Tuple[List[TowerPlacement], List[str]]:
    """Honour `towers.max_towers`, keeping the highest-yield buildings."""
    cap = cfg.towers.max_towers
    if not cap or cap <= 0 or len(towers) <= cap:
        return towers, []
    removed = len(towers) - cap
    kept = sorted(towers, key=lambda t: t.floor_area, reverse=True)[:cap]
    return kept, [f"Capped the layout at {cap} tower(s) as configured; dropped {removed} "
                  "lower-yield tower(s)."]


def _name(towers: List[TowerPlacement]) -> None:
    towers.sort(key=lambda t: (-t.cy, t.cx))     # north-to-south, west-to-east
    for i, t in enumerate(towers):
        t.name = f"Tower {chr(65 + i)}" if i < 26 else f"Tower {i + 1}"


def greedy_pack(ctx: PackContext) -> Tuple[List[TowerPlacement], List[str]]:
    """Pack every residual region, then apply the cross-region and plot-wide rules."""
    towers: List[TowerPlacement] = []
    for part in polygons_of(ctx.region):
        towers += pack_region(part, ctx)

    notes: List[str] = []
    towers, n = _enforce_spacing(towers, ctx.cfg)
    notes += n
    towers, n = _apply_tower_cap(towers, ctx.cfg)
    notes += n
    towers, n = _apply_caps(towers, ctx)
    notes += n

    _name(towers)
    return towers, notes
