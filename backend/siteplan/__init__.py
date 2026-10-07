"""Site layout engine.

Pure, testable geometry + optimisation over a plot polygon. Knows nothing about Leaflet,
FastAPI or Mongo: the public functions take a project dict (or raw coordinates) and a
config override, and return plain serialisable dicts.

Stages
  1. envelope  — plot polygon -> setback-inset buildable envelope        [implemented]
  2. reserve   — carve perimeter road ring, driveways and amenity blocks [implemented]
  3. pack      — greedy grid seeding of tower placement                  [implemented]
     ga        — genetic refinement off the grid                  [implemented]
"""
from typing import Any, Dict, Optional, Sequence

from .devcontrols import recommend as recommend_controls
from .devcontrols import setback_minimums, validate_setbacks
from .version import ENGINE_VERSION, polygon_signature
from .config import (AmenityBlock, AmenityConfig, GaConfig, RoadConfig, SetbackConfig,
                     SiteLayoutConfig, TowerConfig)
from .envelope import EnvelopeResult, build_envelope
from .errors import LayoutError
from .frame import LocalFrame
from .fitness import FitnessResult, PackContext, TowerPlacement, evaluate
from .ga import refine
from .pack import greedy_pack, pack_region
from .plan import LayoutResult, plan, plan_site
from .reserve import (AmenityPlacement, ReserveResult, reserve,
                      reserve_from_coordinates, resolve_amenity_size)

__all__ = [
    "AmenityBlock", "AmenityConfig", "GaConfig", "RoadConfig", "SetbackConfig",
    "SiteLayoutConfig", "TowerConfig", "EnvelopeResult", "LayoutError", "LocalFrame",
    "AmenityPlacement", "ReserveResult", "LayoutResult",
    "FitnessResult", "PackContext", "TowerPlacement", "evaluate",
    "greedy_pack", "pack_region", "refine", "plan", "plan_site",
    "ENGINE_VERSION", "polygon_signature", "recommend_controls",
    "setback_minimums",
    "validate_setbacks",
    "build_envelope", "buildable_envelope", "reserve", "reserve_from_coordinates",
    "reserve_site", "resolve_amenity_size",
]


def buildable_envelope(project: Dict[str, Any],
                       overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Stage 1 entry point used by the API: project document -> serialisable envelope.

    Never raises for user-fixable problems — a LayoutError comes back as
    {"ok": False, "error": {...}} so the client can show the message inline.
    """
    plot = project.get("plot") or {}
    cfg = SiteLayoutConfig.from_dict(overrides)
    try:
        return build_envelope(plot.get("coordinates") or [],
                              plot.get("road_edges") or [], cfg).to_dict()
    except LayoutError as exc:
        return exc.to_dict()


def reserve_site(project: Dict[str, Any],
                 overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Stage 2 entry point: project document -> envelope + roads + amenities + residual.

    The payload is a superset of stage 1's, so a client can render both from one call.
    """
    plot = project.get("plot") or {}
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
        return reserve_from_coordinates(plot.get("coordinates") or [],
                                        plot.get("road_edges") or [], cfg).to_dict()
    except LayoutError as exc:
        return exc.to_dict()
