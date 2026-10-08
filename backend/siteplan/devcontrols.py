"""Development controls — setbacks, height, FAR and yield recommended from the plot.

WHAT THIS IS AND IS NOT
-----------------------
The height-driven open-space table and the height-vs-road-width rule below are the
generally applied NBC 2016 Part 3 provisions and are stable across most Indian
jurisdictions. FAR and ground coverage are NOT: they are set by each municipal
corporation's own bye-laws and development control regulations, are revised regularly,
and differ by zone, road width, plot size and premium-FSI purchase. The values here are
INDICATIVE STARTING POINTS ONLY.

Every recommendation carries a `source` and a `confidence`, and nothing in this module
decides anything by itself — it proposes values the user must confirm against the
sanctioning authority for the specific plot. `requires_verification` is set on anything
that must be checked before it is relied on.
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import iscodes as C

# NBC 2016 Part 3, exterior open space (Table 4, Cl. 8.2.3.1): minimum side and rear open
# space grows with the height of the building. This is the provision that actually governs
# setbacks on a multi-storey residential plot — plot-size minimums are usually the lesser
# requirement. (height in metres up to, required open space in metres)
HEIGHT_OPEN_SPACE = [
    (10.0, 3.0), (15.0, 5.0), (18.0, 6.0), (21.0, 7.0), (24.0, 8.0),
    (27.0, 9.0), (30.0, 10.0), (35.0, 11.0), (40.0, 12.0), (45.0, 13.0),
    (50.0, 14.0), (55.0, 16.0), (70.0, 17.0), (120.0, 18.0),
]
HEIGHT_OPEN_SPACE_MAX = 20.0    # Table 4 Sl xv: above 120 m

# Plot-size based minimum front setback, applied when it exceeds the height-driven figure.
# APTIMIZER ASSUMPTION, not an NBC clause: NBC 2016 Part 3 sets the front open space by
# street width (Cl. 8.2.1.1) and, above 10 m, by Cl. 9.4.1. This conservative plot-size
# floor stands in until the local bye-law table (BDA RMP-2015) is entered.
# (plot area in m2 up to, front setback in metres)
PLOT_FRONT_SETBACK = [
    (250.0, 3.0), (500.0, 4.5), (1000.0, 6.0), (2500.0, 9.0), (float("inf"), 12.0),
]

# NBC 2016 Part 3 Cl. 9.4.1(a): the height of a building shall not exceed 1.5 x the width of
# the abutting road plus the front open space, the front open space counted up to 16 m.
# A plot with no adequate road frontage cannot support a tall building however large it is.
HEIGHT_ROAD_MULTIPLIER = 1.5
FRONT_OPEN_SPACE_CAP_M = 16.0
# NBC 2016 Part 3 Cl. 4.6(a): a high-rise building (15 m and above, Part 4 Cl. 2.38) needs a
# main street of at least 12 m, one end of which joins another street of at least 12 m.
MIN_ROAD_FOR_HIGHRISE = 12.0
HIGHRISE_HEIGHT_M = 15.0

# Indicative FAR by city tier. Municipal bye-laws override these in every real case.
FAR_BY_CITY = {
    "Mumbai": 3.0, "Delhi": 2.0, "New Delhi": 2.0, "Bengaluru": 2.25, "Chennai": 2.0,
    "Hyderabad": 3.0, "Pune": 2.0, "Kolkata": 2.0, "Ahmedabad": 2.7, "Surat": 2.4,
    "Jaipur": 2.25, "Lucknow": 2.5, "Kochi": 3.0, "Chandigarh": 2.0,
}
FAR_DEFAULT = 2.0
GROUND_COVERAGE_DEFAULT_PCT = 40.0


def open_space_for_height(height_m: float) -> float:
    """Minimum side / rear open space for a building of this height (NBC Part 3)."""
    for limit, space in HEIGHT_OPEN_SPACE:
        if height_m <= limit:
            return space
    return HEIGHT_OPEN_SPACE_MAX


def front_setback_for_plot(plot_area: float) -> float:
    for limit, setback in PLOT_FRONT_SETBACK:
        if plot_area <= limit:
            return setback
    return PLOT_FRONT_SETBACK[-1][1]


def max_height_from_road(road_width: float, front_setback: float) -> float:
    if road_width <= 0:
        return 0.0
    return HEIGHT_ROAD_MULTIPLIER * road_width + min(max(front_setback, 0.0), FRONT_OPEN_SPACE_CAP_M)


def setback_minimums(plot_area: float, road_width: float = 0.0,
                     height_m: float = 0.0) -> Dict[str, Any]:
    """The statutory minimum for each edge, with the rule that produced it.

    One place decides what "too small" means, so the module that edits setbacks and the
    engine that builds the envelope cannot disagree about it. Each entry names the rule
    rather than just returning a number -- a value rejected without a reason is a value the
    user will simply override.

    Front is the larger of the plot-size minimum and the height-driven open space: a large
    plot and a tall building each set a floor, and the binding one is whichever is higher.
    """
    by_plot = front_setback_for_plot(plot_area)
    by_height = open_space_for_height(height_m) if height_m else 0.0
    front = max(by_plot, by_height)
    front_rule = ("plot area" if by_plot >= by_height else "building height")

    sides = by_height or open_space_for_height(0.0)

    out = {
        "front": {
            "minimum_m": round(front, 2),
            "rule": (f"Governed by {front_rule}: {by_plot} m for a {plot_area:,.0f} m2 plot "
                     f"(Aptimizer assumption), {by_height} m for {height_m:g} m of height "
                     f"(NBC 2016 Part 3 Table 4); the larger governs"),
            "clause": "NBC 2016 Part 3, Cl. 8",
        },
        "rear": {
            "minimum_m": round(sides, 2),
            "rule": f"NBC 2016 Part 3 — open space for {height_m:g} m of building height",
            "clause": "NBC 2016 Part 3, Cl. 8",
        },
        "side": {
            "minimum_m": round(sides, 2),
            "rule": f"NBC 2016 Part 3 — open space for {height_m:g} m of building height",
            "clause": "NBC 2016 Part 3, Cl. 8",
        },
        "default": {
            "minimum_m": round(sides, 2),
            "rule": "Applied to any edge not classified as front, rear or side",
            "clause": "NBC 2016 Part 3, Cl. 8",
        },
    }
    if road_width and road_width < MIN_ROAD_FOR_HIGHRISE and height_m >= HIGHRISE_HEIGHT_M:
        out["_note"] = (f"The abutting road is {road_width:g} m. NBC 2016 Part 3 Cl. 4.6(a) "
                        f"needs a road of at least {MIN_ROAD_FOR_HIGHRISE:g} m for a high-rise "
                        "building (15 m and above), whatever the setbacks are.")
    return out


def validate_setbacks(applied: Dict[str, Any], plot_area: float,
                      road_width: float = 0.0, height_m: float = 0.0) -> Dict[str, Any]:
    """Check applied setbacks against the statutory minimums.

    Returns one row per edge carrying the applied value, the minimum, whether it clears it
    and by how much. The caller decides what to do about a failure -- this only reports.
    """
    mins = setback_minimums(plot_area, road_width, height_m)
    rows = []
    for edge in ("front", "rear", "side", "default"):
        rule = mins[edge]
        try:
            value = float(applied.get(edge) or 0)
        except (TypeError, ValueError):
            value = 0.0
        minimum = rule["minimum_m"]
        rows.append({
            "edge": edge, "applied_m": round(value, 2), "minimum_m": minimum,
            "ok": value + 1e-9 >= minimum,
            "shortfall_m": round(max(minimum - value, 0), 2),
            "rule": rule["rule"], "clause": rule["clause"],
        })
    return {"edges": rows, "ok": all(r["ok"] for r in rows),
            "note": mins.get("_note", "")}


@dataclass
class Recommendation:
    key: str
    label: str
    value: Any
    unit: str = ""
    source: str = ""
    confidence: str = "indicative"      # "code" | "indicative"
    requires_verification: bool = False
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "value": self.value, "unit": self.unit,
            "source": self.source, "confidence": self.confidence,
            "requires_verification": self.requires_verification, "note": self.note,
        }


def recommend(plot_area: float,
              road_width: float = 0.0,
              city: str = "",
              state: str = "",
              floor_height: float = 3.0,
              area_per_unit: float = 95.0,
              carpet_efficiency: float = 0.78,
              far_override: Optional[float] = None) -> Dict[str, Any]:
    """Recommend setbacks, height, floors and unit yield for a plot.

    The height calculation is circular by nature — taller buildings demand deeper
    setbacks, which shrink the footprint, which changes how many floors are needed to
    reach the permitted FAR. It is resolved by iterating to a fixed point rather than
    guessing once.
    """
    plot_area = max(float(plot_area or 0.0), 0.0)
    if plot_area <= 0:
        return {"ok": False, "error": "Plot area is required before controls can be recommended."}

    far = float(far_override) if far_override else FAR_BY_CITY.get((city or "").strip().title(), FAR_DEFAULT)
    far_is_override = bool(far_override)
    coverage_pct = GROUND_COVERAGE_DEFAULT_PCT

    front = front_setback_for_plot(plot_area)
    permitted_floor_area = plot_area * far
    footprint_cap = plot_area * coverage_pct / 100.0

    # Fixed point: assume a height, derive the setback it demands, re-derive the height
    # that the resulting footprint needs to deliver the permitted floor area.
    height = 15.0
    side = open_space_for_height(height)
    for _ in range(25):
        side = open_space_for_height(height)
        front = max(front_setback_for_plot(plot_area), side)
        # Square-ish plot approximation for the envelope the setbacks leave behind.
        span = math.sqrt(plot_area)
        usable = max(span - 2 * side, 0.0) * max(span - front - side, 0.0)
        footprint = min(usable * 0.55, footprint_cap)   # 55% of envelope is realistically built
        if footprint <= 0:
            break
        floors_needed = permitted_floor_area / footprint
        new_height = floors_needed * floor_height
        if abs(new_height - height) < 0.05:
            height = new_height
            break
        height = (height + new_height) / 2.0            # damped, to converge not oscillate

    road_cap = max_height_from_road(road_width, front) if road_width else 0.0
    height_capped_by_road = bool(road_cap and height > road_cap)
    if height_capped_by_road:
        # Open space is a function of the height actually built. Leaving the setback at
        # the pre-cap figure demands a deeper margin than the capped building needs, and
        # on a small plot that difference swallows the whole buildable width. A lower
        # building needs a shallower front, which lowers the road cap again (the front
        # open space is part of it), so step down until height and front agree.
        for _ in range(20):
            height = road_cap
            side = open_space_for_height(height)
            front = max(front_setback_for_plot(plot_area), side)
            road_cap = max_height_from_road(road_width, front)
            if height <= road_cap + 1e-9:
                break

    floors = max(int(height // floor_height), 1)

    # Sanity: a setback regime that leaves no usable width is a signal the plot cannot
    # carry this height, not a layout to hand onward.
    span = math.sqrt(plot_area)
    usable_width = span - 2 * side
    usable_depth = span - front - side
    achievable_floor_area = min(permitted_floor_area, footprint_cap * floors)
    units = int((achievable_floor_area * carpet_efficiency) // max(area_per_unit, 1.0))

    items: List[Recommendation] = [
        Recommendation("front_setback", "Front setback", round(front, 1), "m",
                       "NBC 2016 Part 3 Table 4 / Aptimizer plot-size assumption", "code",
                       note="Greater of the plot-size minimum (Aptimizer assumption, not an NBC "
                            "clause) and the height-driven open space (NBC Table 4)."),
        Recommendation("side_setback", "Side setback", round(side, 1), "m",
                       "NBC 2016 Part 3 Table — open space by building height", "code",
                       note=f"Driven by a building height of {height:.1f} m."),
        Recommendation("rear_setback", "Rear setback", round(side, 1), "m",
                       "NBC 2016 Part 3 Table — open space by building height", "code"),
        Recommendation("far", "Floor Area Ratio (FAR)", round(far, 2), "",
                       "User override" if far_is_override else
                       f"Indicative for {city or 'this location'}",
                       "code" if far_is_override else "indicative",
                       requires_verification=not far_is_override,
                       note="FAR is set by the local development control regulations and "
                            "is revised periodically. Confirm with the sanctioning authority."),
        Recommendation("ground_coverage_pct", "Maximum ground coverage", coverage_pct, "%",
                       "Indicative municipal bye-law", "indicative", requires_verification=True),
        Recommendation("max_height", "Recommended building height", round(height, 1), "m",
                       "Derived from FAR, coverage and setbacks", "indicative",
                       note=("Capped by the abutting road width" if height_capped_by_road
                             else "Not limited by road width at this frontage.")),
        Recommendation("floors", "Recommended floors", floors, "floors",
                       f"{round(height, 1)} m at {floor_height} m floor-to-floor", "indicative"),
        Recommendation("units", "Estimated dwelling units", units, "units",
                       f"{carpet_efficiency:.0%} carpet efficiency, {area_per_unit:g} m2 per unit",
                       "indicative",
                       note="A yield estimate for feasibility, not a unit schedule."),
    ]

    warnings: List[str] = []
    if road_width and road_width < MIN_ROAD_FOR_HIGHRISE and height >= HIGHRISE_HEIGHT_M:
        warnings.append(
            f"The abutting road is {road_width:g} m wide. NBC 2016 Part 3 Cl. 4.6(a) does not "
            f"permit high-rise buildings (15 m and above) on roads narrower than "
            f"{MIN_ROAD_FOR_HIGHRISE:g} m, regardless of plot size or FAR.")
    if not road_width:
        warnings.append("No abutting road width recorded, so the height-versus-road-width "
                        "limit could not be applied. Mark a road-facing edge in Plot & Site.")
    if height > 24:
        warnings.append("Above 24 m: refuge areas are required (NBC 2016 Part 4, Annex E-4; "
                        "apartments with balconies are exempt up to 60 m) and a minimum front "
                        "open space of 6 m applies (Part 3, Table 4 Note 1).")
    if height >= HIGHRISE_HEIGHT_M:
        warnings.append("15 m and above is a high-rise building (NBC 2016 Part 4, Cl. 2.38): "
                        "a fireman's lift (Part 8 Sec 5A, Cl. 7.1.1) and 6 m fire-tender access "
                        "around the building (Part 3, Cl. 4.6) are required.")
    if usable_width <= 12.0 or usable_depth <= 12.0:
        warnings.append(
            f"At {height:.0f} m the required open space leaves only about "
            f"{max(usable_width, 0):.0f} x {max(usable_depth, 0):.0f} m inside the setbacks — "
            "too narrow for a viable block. This plot realistically supports a lower "
            "building than its FAR alone suggests.")

    return {
        "ok": True,
        "plot_area_sqm": round(plot_area, 2),
        "road_width_m": road_width,
        "recommendations": [i.to_dict() for i in items],
        "setbacks": {"front": round(front, 1), "rear": round(side, 1), "side": round(side, 1),
                     "default": round(side, 1)},
        "far_cap": round(far, 2),
        "ground_coverage_cap_pct": coverage_pct,
        "floors": floors,
        "units": units,
        "height_m": round(height, 1),
        "height_capped_by_road": height_capped_by_road,
        "warnings": warnings,
        "disclaimer": (
            "Indicative preliminary figures for feasibility only. FAR, ground coverage and "
            "local setback amendments are set by the sanctioning authority and must be "
            "verified for this specific plot before any design decision is taken."
        ),
    }
