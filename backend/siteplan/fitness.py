"""Shared tower model, hard constraints and scoring.

Greedy packing (stage 3a) and the genetic refinement (stage 3b) score through this one
module on purpose: if they scored differently, the GA could rate its own greedy seed
worse than greedy did and "improve" it into something objectively worse.

Hard constraints reject a layout outright (they are never traded against floor area):
  * geometry outside the buildable envelope / residual packable region
  * tower-to-tower overlap
  * overlap with reserved road or amenity geometry
Soft constraints carry a penalty proportional to how badly they are missed:
  * minimum inter-tower spacing for light and ventilation (scales with height)
  * a tower stranded further than `road.max_distance_to_road` from the network
  * FAR / ground-coverage caps for the plot
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

from .config import SiteLayoutConfig


@dataclass
class TowerPlacement:
    polygon: Polygon
    cx: float
    cy: float
    width: float
    depth: float
    rotation_deg: float
    floors: int
    floor_height: float
    name: str = ""

    @property
    def footprint_area(self) -> float:
        return self.width * self.depth

    @property
    def height_m(self) -> float:
        return self.floors * self.floor_height

    @property
    def floor_area(self) -> float:
        return self.footprint_area * self.floors

    def units(self, cfg: SiteLayoutConfig) -> int:
        carpet = self.floor_area * cfg.towers.carpet_efficiency
        return int(carpet // max(cfg.towers.area_per_unit, 1.0))


@dataclass
class PackContext:
    """Everything a layout is judged against. Built once, reused across evaluations."""
    region: BaseGeometry          # residual packable land
    roads: BaseGeometry
    plot_area: float
    cfg: SiteLayoutConfig
    _prepared: Any = field(default=None, repr=False)

    def __post_init__(self):
        self._prepared = prep(self.region) if not self.region.is_empty else None

    def contains(self, geom: BaseGeometry) -> bool:
        return bool(self._prepared and self._prepared.contains(geom))


def required_spacing(a: TowerPlacement, b: TowerPlacement, cfg: SiteLayoutConfig) -> float:
    """Light-and-ventilation gap: scales with the mean height of the pair."""
    mean_height = (a.height_m + b.height_m) / 2.0
    return max(cfg.towers.spacing_min, cfg.towers.spacing_height_factor * mean_height)


@dataclass
class FitnessResult:
    feasible: bool
    score: float
    hard_violations: List[str] = field(default_factory=list)
    penalties: Dict[str, float] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)


def evaluate(towers: Sequence[TowerPlacement], ctx: PackContext) -> FitnessResult:
    """Total buildable floor area, minus penalties; infeasible layouts score -inf."""
    cfg = ctx.cfg
    hard: List[str] = []

    for i, t in enumerate(towers):
        if not ctx.contains(t.polygon):
            hard.append(f"tower {i} is outside the packable region")
        if not ctx.roads.is_empty and t.polygon.intersects(ctx.roads):
            inter = t.polygon.intersection(ctx.roads).area
            if inter > 1e-6:
                hard.append(f"tower {i} overlaps reserved circulation")

    for i in range(len(towers)):
        for j in range(i + 1, len(towers)):
            if towers[i].polygon.intersection(towers[j].polygon).area > 1e-6:
                hard.append(f"towers {i} and {j} overlap")

    floor_area = sum(t.floor_area for t in towers)
    footprint = sum(t.footprint_area for t in towers)

    if hard:
        return FitnessResult(False, float("-inf"), hard, {},
                             {"floor_area": floor_area, "footprint": footprint})

    penalties: Dict[str, float] = {}

    spacing_shortfall = 0.0
    for i in range(len(towers)):
        for j in range(i + 1, len(towers)):
            need = required_spacing(towers[i], towers[j], cfg)
            gap = towers[i].polygon.distance(towers[j].polygon)
            if gap < need:
                spacing_shortfall += (need - gap) ** 2
    if spacing_shortfall:
        penalties["spacing"] = spacing_shortfall * 40.0

    if not ctx.roads.is_empty:
        stranded = sum(max(0.0, t.polygon.distance(ctx.roads) - cfg.road.max_distance_to_road)
                       for t in towers)
        if stranded:
            penalties["unreachable"] = stranded * 200.0

    far = floor_area / ctx.plot_area if ctx.plot_area else 0.0
    if far > cfg.far_cap:
        penalties["far"] = (far - cfg.far_cap) * ctx.plot_area * 4.0

    coverage_pct = footprint / ctx.plot_area * 100 if ctx.plot_area else 0.0
    if coverage_pct > cfg.ground_coverage_cap_pct:
        penalties["ground_coverage"] = (coverage_pct - cfg.ground_coverage_cap_pct) * ctx.plot_area * 0.4

    return FitnessResult(
        feasible=True,
        score=floor_area - sum(penalties.values()),
        hard_violations=[],
        penalties=penalties,
        metrics={"floor_area": floor_area, "footprint": footprint,
                 "far": far, "ground_coverage_pct": coverage_pct,
                 "towers": len(towers)},
    )
