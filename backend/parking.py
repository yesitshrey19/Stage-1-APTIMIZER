"""Per-building parking demand and supply.

Two things were wrong with the previous calculation, and both came from the same place:
it treated parking as a single site-wide number derived from a flat ratio.

1. DEMAND IGNORED UNIT SIZE. A blanket `1.5 slots per unit` charges a 30 m2 studio the
   same as a 150 m2 penthouse. No Indian bye-law works that way -- every one of them
   scales the requirement with dwelling size, and the difference is not marginal: under
   UDCPR 2020 a 30 m2 tenement needs NO car space while a 150 m2 one needs two. A tower
   of small units was being told to build roughly three times the parking it owes, and a
   tower of large ones about a quarter too little.

2. DEMAND WAS NOT PER BUILDING. Approvals are granted per building. A site total hides a
   tower that is short while another is in surplus, and the shortfall only surfaces at
   scrutiny.

So demand is computed per tower from that tower's own unit mix against the state's rule,
and supply is modelled the way podium developments are actually built: slots that belong
to a tower (stilt, podium) plus a shared site pool (common basement, open surface) that is
allocated across towers in proportion to what each still needs.

REGULATORY STATUS
-----------------
Only the Maharashtra table below was checked against source this session. Every other
state entry is a documented placeholder carrying `verified: False`, and the payload says
so, because parking norms are set per urban local body and change often. They are meant
to be edited to the bye-law that actually governs the project -- shipping a confident
wrong number to someone filing for sanction would be worse than shipping an obvious gap.
"""
import math
from typing import Any, Dict, List, Optional

INF = float("inf")

# Bands are (min_carpet_sqm inclusive, max_carpet_sqm exclusive, cars, scooters) per
# tenement. A dwelling falls in the band whose range contains its carpet area.
STATE_NORMS: Dict[str, Dict[str, Any]] = {
    "Maharashtra": {
        "authority": "UDCPR 2020 (Maharashtra), residential car and two-wheeler parking table",
        "basis": "carpet_band",
        "bands": [(0.0, 30.0, 0.0, 1.0), (30.0, 40.0, 0.5, 1.0), (40.0, 80.0, 0.5, 1.0),
                  (80.0, 150.0, 1.0, 1.0), (150.0, INF, 2.0, 1.0)],
        "visitor_pct": 5.0,
        "verified": True,
        "note": "Carpet area per tenement. Congested-area and authority factors are NOT "
                "applied here -- check Table 8-C for the relevant area classification.",
    },
    "Delhi": {
        "authority": "MPD-2021 (Delhi) -- 2 ECS per 100 m2 built-up area",
        "basis": "builtup_per_100", "ecs_per_100_sqm": 2.0, "scooters_per_unit": 1.0,
        "visitor_pct": 10.0, "verified": False,
    },
    "Karnataka": {
        "authority": "Karnataka / BBMP zoning regulations -- approx. 1 ECS per 100 m2 built-up",
        "basis": "builtup_per_100", "ecs_per_100_sqm": 1.0, "scooters_per_unit": 1.0,
        "visitor_pct": 10.0, "verified": False,
    },
    "Telangana": {
        "authority": "Telangana / GHMC building rules -- approx. 1 ECS per 100 m2 built-up",
        "basis": "builtup_per_100", "ecs_per_100_sqm": 1.0, "scooters_per_unit": 1.0,
        "visitor_pct": 10.0, "verified": False,
    },
    "Tamil Nadu": {
        "authority": "TN Combined Development and Building Rules -- approx. 1 ECS per 100 m2",
        "basis": "builtup_per_100", "ecs_per_100_sqm": 1.0, "scooters_per_unit": 1.0,
        "visitor_pct": 10.0, "verified": False,
    },
    "_default": {
        "authority": "NBC 2016 Part 3 style fallback -- 1 ECS per 100 m2 built-up. "
                     "REPLACE with the bye-law governing this project.",
        "basis": "builtup_per_100", "ecs_per_100_sqm": 1.0, "scooters_per_unit": 1.0,
        "visitor_pct": 10.0, "verified": False,
    },
}

# One ECS is a car space plus its share of aisle. IS/NBC practice puts an equivalent car
# space near 23 m2; anything above that is design slack, not a code requirement.
ECS_BENCHMARK_SQM = 23.0
SCOOTERS_PER_ECS = 3.0
ACCESSIBLE_PER = 50           # 1 accessible bay per 50 car spaces
DEFAULT_EV_PCT = 20.0


def norm_for(state: Optional[str], override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve the parking rule for a state, with a per-project override taking priority."""
    base = dict(STATE_NORMS.get((state or "").strip(), STATE_NORMS["_default"]))
    if override:
        base = {**base, **{k: v for k, v in override.items() if v is not None}}
        base["authority"] = override.get("authority") or (base["authority"] + " (edited)")
    return base


def _band(norm: Dict[str, Any], carpet: float):
    for lo, hi, cars, scoot in norm.get("bands") or []:
        if lo <= carpet < hi:
            return cars, scoot
    return 0.0, 0.0


from residential_defaults import storeys_of


def tower_demand(tower: Dict[str, Any], norm: Dict[str, Any],
                 builtup_sqm: float) -> Dict[str, Any]:
    """Car and two-wheeler demand for ONE building, itemised by unit type.

    Returned per unit type so the UI can show why a tower owes what it owes -- an
    unexplained total is what made the old number feel arbitrary.
    """
    floors = max(int(tower.get("floors") or 0), 0)
    lines: List[Dict[str, Any]] = []
    cars = scooters = 0.0
    units_total = 0

    if norm.get("basis") == "carpet_band":
        for u in (tower.get("units") or []):
            per_floor = int(u.get("count") or 0)
            n = per_floor * storeys_of(u, floors)
            carpet = float(u.get("carpet_area") or 0)
            c, s = _band(norm, carpet)
            cars += c * n
            scooters += s * n
            units_total += n
            lines.append({"type": u.get("type") or "unit", "carpet_sqm": round(carpet, 2),
                          "units": n, "cars_per_unit": c, "scooters_per_unit": s,
                          "cars": round(c * n, 2), "scooters": round(s * n, 2),
                          "band": _band_label(norm, carpet)})
    else:
        per_100 = float(norm["ecs_per_100_sqm"]) if norm.get("ecs_per_100_sqm") is not None else 1.0
        cars = builtup_sqm / 100.0 * per_100
        for u in (tower.get("units") or []):
            units_total += int(u.get("count") or 0) * storeys_of(u, floors)
        scooter_rate = float(norm["scooters_per_unit"]) if norm.get("scooters_per_unit") is not None else 1.0
        scooters = units_total * scooter_rate
        lines.append({"type": "built-up area basis", "carpet_sqm": None, "units": units_total,
                      "cars_per_unit": None, "scooters_per_unit": norm.get("scooters_per_unit"),
                      "cars": round(cars, 2), "scooters": round(scooters, 2),
                      "band": f"{per_100:g} ECS per 100 m2 of {round(builtup_sqm)} m2 built-up"})

    return {"units": units_total, "cars_raw": cars, "scooters_raw": scooters,
            "cars_required": math.ceil(cars), "scooters_required": math.ceil(scooters),
            "lines": lines}


def _band_label(norm, carpet):
    for lo, hi, cars, _ in norm.get("bands") or []:
        if lo <= carpet < hi:
            hi_txt = "and above" if hi == INF else f"to under {hi:g} m2"
            return f"{lo:g} {hi_txt}: {cars:g} car(s) per tenement"
    return "outside the norm table"


def _allocate(pool: int, needs: List[float]) -> List[int]:
    """Hand out a shared pool in proportion to need, largest-remainder, never over-issuing.

    Plain rounding either loses slots or invents them; largest remainder distributes the
    pool exactly, so the per-tower figures always add back to the site total.
    """
    total = sum(needs)
    if pool <= 0 or total <= 0:
        return [0] * len(needs)
    if pool >= total:
        return [int(math.ceil(n)) if n else 0 for n in needs]
    exact = [pool * n / total for n in needs]
    base = [int(math.floor(x)) for x in exact]
    left = pool - sum(base)
    order = sorted(range(len(needs)), key=lambda i: exact[i] - base[i], reverse=True)
    for i in order[:left]:
        base[i] += 1
    return base


def plan(project: Dict[str, Any], areas: Dict[str, Any]) -> Dict[str, Any]:
    """Full parking picture: per-tower demand, per-tower and shared supply, compliance.

    Keeps every key the old flat calculation returned so existing consumers (compliance,
    the IS/NBC parking module, scheme comparison, reports) keep working unchanged, and
    adds `towers`, `norm` and `checks` on top.
    """
    p = project.get("parking") or {}
    eng = project.get("engineering") or {}
    state = eng.get("state") or ""
    norm = norm_for(state, p.get("norm_override"))

    towers_in = areas.get("towers") or []
    area_per_slot = float(p.get("area_per_slot") or 30) or 30.0
    basement_levels = int(p.get("basement_levels") or 0)
    basement_area = float(p.get("basement_area_per_level") or 0)
    ground_area = float(p.get("ground_area") or 0)

    # A custom residential program records two-wheeler bays inside the basement area.
    # Subtract their floor area before translating the remaining area into car ECS, so a
    # bike bay cannot also be counted as an extra car space by the site-wide calculation.
    allocations = p.get("tower_allocations") or []
    bike_area = float((project.get("residential_policy") or {}).get("bike_space_area_sqm") or (23.0 / 3.0))
    allocated_bike_area = sum(int(t.get("basement_bike_spaces") or 0) for t in allocations) * bike_area
    basement_car_area = max(0.0, basement_levels * basement_area - allocated_bike_area)
    basement_slots = int(basement_car_area / area_per_slot)
    ground_slots = int(ground_area / area_per_slot)
    shared_pool = basement_slots + ground_slots

    # ---- demand and own supply, per building ------------------------------------
    by_tower, own_supply, residual = [], [], []
    src = {t.get("id"): t for t in (project.get("towers") or [])}
    for t in towers_in:
        raw = src.get(t.get("id")) or {}
        d = tower_demand(raw, norm, float(t.get("builtup_sqm") or 0))
        tp = raw.get("parking") or {}
        own = int(tp.get("stilt_slots") or 0) + int(tp.get("podium_slots") or 0)
        by_tower.append({
            "id": t.get("id"), "name": t.get("name"), "floors": t.get("floors"),
            "builtup_sqm": round(float(t.get("builtup_sqm") or 0), 2),
            "units": d["units"], "cars_required": d["cars_required"],
            "scooters_required": d["scooters_required"], "breakdown": d["lines"],
            "own_slots": own,
            "stilt_slots": int(tp.get("stilt_slots") or 0),
            "podium_slots": int(tp.get("podium_slots") or 0),
            "residential_basement": ({
                "car_spaces": int(tp.get("basement_car_spaces") or 0),
                "bike_spaces": int(tp.get("basement_bike_spaces") or 0),
                "optional_car_pool": int(tp.get("optional_car_pool_capacity") or 0),
                "flexible_space_use": bool(tp.get("flexible_space_use")),
                "area_sqm": round(float(tp.get("basement_area_sqm") or 0), 2),
            } if tp.get("basement_only") else None),
        })
        own_supply.append(own)
        residual.append(max(d["cars_required"] - own, 0))

    share = _allocate(shared_pool, residual)
    for i, row in enumerate(by_tower):
        row["shared_slots"] = share[i]
        row["provided_slots"] = row["own_slots"] + share[i]
        row["deficit"] = max(row["cars_required"] - row["provided_slots"], 0)
        row["surplus"] = max(row["provided_slots"] - row["cars_required"], 0)
        row["compliant"] = row["deficit"] == 0

    required = sum(r["cars_required"] for r in by_tower)
    scooters_required = sum(r["scooters_required"] for r in by_tower)
    provided = sum(own_supply) + shared_pool
    units = sum(r["units"] for r in by_tower) or int(areas.get("total_units") or 0)

    # ---- category requirements ---------------------------------------------------
    visitor_pct = float(p["visitor_pct"] if p.get("visitor_pct") is not None
                        else (norm["visitor_pct"] if norm.get("visitor_pct") is not None else 10.0))
    ev_pct = float(p.get("ev_pct") if p.get("ev_pct") is not None else DEFAULT_EV_PCT)
    accessible_pct = p.get("accessible_pct")
    visitor_required = math.ceil(required * visitor_pct / 100.0)
    ev_required = math.ceil(required * ev_pct / 100.0)
    accessible_required = (math.ceil(required * float(accessible_pct) / 100.0)
                           if accessible_pct is not None else math.ceil(required / ACCESSIBLE_PER))
    visitor_provided = int(p.get("visitor_provided") or 0)
    ev_provided = int(p.get("ev_provided") or 0)
    accessible_provided = int(p.get("accessible_provided") or 0)

    total_area = basement_levels * basement_area + ground_area
    # The old "layout efficiency" divided the parking area by a slot count that was itself
    # derived from that same area, so it could only ever report ~100%. Measured against the
    # code-equivalent car space it becomes a real number: below 100% means the layout
    # allows more area per car than an efficient one needs.
    efficiency = round(ECS_BENCHMARK_SQM / area_per_slot * 100.0, 1) if area_per_slot else 0.0

    ramp = p.get("ramp") or {}
    slope = float(ramp.get("slope_pct") or 0)
    ramp_width = float(ramp.get("width") or 0)
    turning_radius = float(ramp.get("turning_radius") or 0)
    ramp_checks = [
        {"label": "Ramp slope <= 12.5%", "pass": slope <= 12.5, "value": f"{slope}%"},
        {"label": "Ramp width >= 3.6 m (two-way 6.0 m)", "pass": ramp_width >= 3.6, "value": f"{ramp_width} m"},
        {"label": "Turning radius >= 6.0 m", "pass": turning_radius >= 6.0, "value": f"{turning_radius} m"},
    ]

    short = [r["name"] for r in by_tower if r["deficit"] > 0]
    checks = [
        {"label": "Every building meets its own car-parking demand", "pass": not short,
         "value": "all compliant" if not short else f"short: {', '.join(short)}",
         "detail": "Sanction is granted per building, so a site-wide surplus does not "
                   "excuse a tower that is short."},
        {"label": f"Visitor parking >= {visitor_pct:g}% of car spaces",
         "pass": visitor_provided >= visitor_required,
         "value": f"{visitor_provided} of {visitor_required}"},
        {"label": f"Accessible bays >= 1 per {ACCESSIBLE_PER}",
         "pass": accessible_provided >= accessible_required,
         "value": f"{accessible_provided} of {accessible_required}"},
        {"label": f"EV-ready >= {ev_pct:g}%", "pass": ev_provided >= ev_required,
         "value": f"{ev_provided} of {ev_required}"},
        {"label": "Reserved categories fit inside the slots provided",
         "pass": visitor_provided + ev_provided + accessible_provided <= provided,
         "value": f"{visitor_provided + ev_provided + accessible_provided} reserved of {provided} built"},
    ]

    warnings = []
    if not norm.get("verified"):
        warnings.append({
            "severity": "warning",
            "text": f"The parking rule in use ({norm['authority']}) is an unverified default. "
                    "Parking norms are set by the local authority and change often -- confirm "
                    "the bands against the bye-law governing this project before filing.",
        })
    if not towers_in:
        warnings.append({"severity": "warning", "text": "No towers defined, so demand is zero."})

    return {
        # ---- keys the previous calculation returned, preserved ----
        "units": units,
        "ratio_per_unit": round(required / units, 3) if units else 0,
        "required_slots": required,
        "provided_slots": provided,
        "basement_slots": basement_slots,
        "ground_slots": ground_slots,
        "deficit": max(required - provided, 0),
        "surplus": max(provided - required, 0),
        "visitor_required": visitor_required,
        "ev_required": ev_required,
        "accessible_required": accessible_required,
        "visitor_provided": visitor_provided,
        "ev_provided": ev_provided,
        "accessible_provided": accessible_provided,
        "total_parking_area_sqm": round(total_area, 2),
        "area_per_slot_actual": round(total_area / provided, 2) if provided else 0,
        "efficiency_pct": efficiency,
        "ramp_checks": ramp_checks,
        "ramp_pass": all(c["pass"] for c in ramp_checks),
        # ---- new ----
        "norm": {"state": state or "(not set)", "authority": norm["authority"],
                 "basis": norm.get("basis"), "verified": bool(norm.get("verified")),
                 "note": norm.get("note", ""), "visitor_pct": visitor_pct},
        "towers": by_tower,
        "own_slots_total": sum(own_supply),
        "shared_pool": shared_pool,
        "shared_allocated": sum(share),
        "scooters_required": scooters_required,
        "scooter_ecs_equivalent": round(scooters_required / SCOOTERS_PER_ECS, 1),
        "buildings_short": short,
        "checks": checks,
        "all_checks_pass": all(c["pass"] for c in checks),
        "warnings": warnings,
        "benchmark_area_per_ecs_sqm": ECS_BENCHMARK_SQM,
    }
