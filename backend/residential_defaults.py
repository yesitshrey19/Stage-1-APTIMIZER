"""Project-scoped residential unit and basement parking defaults."""
from __future__ import annotations

import copy
import math
import re
import uuid
from typing import Any, Dict, Iterable


UNIT_SPECS = {
    "1bhk": {"type": "1bhk", "units_per_floor": 8, "carpet_area": 48.0, "balcony_area": 5.0},
    "2bhk": {"type": "2bhk", "units_per_floor": 6, "carpet_area": 72.0, "balcony_area": 8.0},
    "3bhk": {"type": "3bhk", "units_per_floor": 4, "carpet_area": 120.0, "balcony_area": 14.0},
    "4bhk": {"type": "4bhk", "units_per_floor": 2, "carpet_area": 160.0, "balcony_area": 20.0},
    "5bhk": {"type": "5bhk", "units_per_floor": 1, "carpet_area": 210.0, "balcony_area": 28.0},
}
# A full-storey penthouse is not a per-floor density tier: it is one home occupying the top
# storey. It is deliberately kept out of UNIT_CYCLE and out of the stored residential policy
# (the project validator accepts only the five BHK tiers there) and carried on the tower
# itself, with `storeys: 1` so areas, parking and the floor generator count it once.
PENTHOUSE_SPEC = {
    "type": "Penthouse", "units_per_floor": 1, "carpet_area": 420.0,
    "balcony_area": 60.0, "storeys": 1,
}
SPEC_BY_KEY = {**UNIT_SPECS, "penthouse": PENTHOUSE_SPEC}

UNIT_CYCLE = list(UNIT_SPECS)
CAR_SPACE_AREA_SQM = 23.0
BIKE_SPACE_AREA_SQM = CAR_SPACE_AREA_SQM / 3.0

PENTHOUSE_PARKING = {
    "reserved_cars_per_unit": 4, "reserved_bikes_per_unit": 0,
    "optional_car_spaces_per_unit": 0, "flexible_space_use": True,
}

PARKING_BY_TYPE = {
    "1bhk": {"reserved_cars_per_unit": 0, "reserved_bikes_per_unit": 0,
             "optional_car_spaces_per_unit": 1.0},
    "2bhk": {"reserved_cars_per_unit": 1, "reserved_bikes_per_unit": 0,
             "optional_car_spaces_per_unit": 0},
    "3bhk": {"reserved_cars_per_unit": 1, "reserved_bikes_per_unit": 1,
             "optional_car_spaces_per_unit": 0},
    "4bhk": {"reserved_cars_per_unit": 2, "reserved_bikes_per_unit": 1,
             "optional_car_spaces_per_unit": 0},
    "5bhk": {"reserved_cars_per_unit": 4, "reserved_bikes_per_unit": 0,
             "optional_car_spaces_per_unit": 0, "flexible_space_use": True},
}

PARKING_STANDARDS = {**PARKING_BY_TYPE, "penthouse": PENTHOUSE_PARKING}

# The default project's one tower carries the whole mix on a single plate: four 1BHK, one
# 2BHK and one 3BHK per floor. That is the same 384 m2 of carpet per floor the all-1BHK
# plate held (4x48 + 72 + 120), so the building's structure, FAR and parking do not move --
# only how the plate is divided into homes.
DEFAULT_MIXED_PROGRAMME = (("1bhk", 4), ("2bhk", 1), ("3bhk", 1))


def new_residential_policy() -> Dict[str, Any]:
    """Return a project-owned copy so each project can change its own defaults."""
    return {
        "version": 1,
        "unit_type_cycle": list(UNIT_CYCLE),
        "units_per_floor": {k: v["units_per_floor"] for k, v in UNIT_SPECS.items()},
        "parking_by_type": copy.deepcopy(PARKING_BY_TYPE),
        "five_bhk_parking_option": "4 car spaces, no dedicated bike bays; flexible use",
        "car_space_area_sqm": CAR_SPACE_AREA_SQM,
        "bike_space_area_sqm": round(BIKE_SPACE_AREA_SQM, 2),
    }


def tower_index(name: Any, fallback: int = 0) -> int:
    match = re.search(r"\btower\s+([a-z])\b", str(name or ""), re.I)
    if match:
        return ord(match.group(1).upper()) - ord("A")
    match = re.search(r"\btower\s+(\d+)\b", str(name or ""), re.I)
    if match:
        return max(int(match.group(1)) - 1, 0)
    return max(fallback, 0)


def unit_key(value: Any) -> str:
    text = str(value or "")
    if "penthouse" in text.lower():
        return "penthouse"
    match = re.search(r"([1-5])\s*bhk", text, re.I)
    return f"{match.group(1)}bhk" if match else ""


def storeys_of(unit: Dict[str, Any], floors: int) -> int:
    """How many storeys one unit entry actually occupies.

    A tower's programme is written per typical storey, so an entry with no `storeys` key
    describes every floor. A home that exists on the top storey only -- a full-floor
    penthouse -- carries `storeys: 1` so every reader counts it once instead of once per
    floor.
    """
    total = max(int(floors or 1), 1)
    try:
        value = int(unit.get("storeys") or total)
    except (TypeError, ValueError):
        value = total
    return max(1, min(value, total))


def parking_rules(policy: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    overrides = policy.get("parking_by_type") or {}
    return {
        tier: {**defaults, **(overrides.get(tier) or {})}
        for tier, defaults in PARKING_STANDARDS.items()
    }


def default_programme(programme=DEFAULT_MIXED_PROGRAMME) -> list[Dict[str, Any]]:
    """Build the mixed per-floor programme the project's first tower carries.

    The counts are fixed by the programme -- a mixed plate is laid out by hand, not by the
    A-E cycle -- and the areas come from the tier specs so the plate matches the rates,
    parking rules and room programmes every other part of the app reads.
    """
    units = []
    for key, count in programme:
        spec = SPEC_BY_KEY.get(key)
        if not spec:
            continue
        units.append({
            "type": spec["type"],
            "count": max(1, int(count)),
            "carpet_area": float(spec["carpet_area"]),
            "balcony_area": float(spec["balcony_area"]),
        })
    return units


def add_penthouse(tower: Dict[str, Any], policy: Dict[str, Any] | None = None,
                  count: int = 1, carpet_area: float | None = None,
                  balcony_area: float | None = None) -> Dict[str, Any]:
    """Put a full-storey penthouse on a tower's top storey.

    The entry lives in the tower's own unit list so every reader -- areas, parking, the mix
    summary, the floor generator -- sees it, with `storeys: 1` so only the top floor counts
    it. Any penthouse already on the tower is replaced, not duplicated.
    """
    spec = PENTHOUSE_SPEC
    entry = {
        "id": str(uuid.uuid4())[:8],
        "type": spec["type"],
        "count": max(1, int(count)),
        "carpet_area": float(carpet_area if carpet_area is not None else spec["carpet_area"]),
        "balcony_area": float(balcony_area if balcony_area is not None else spec["balcony_area"]),
        "storeys": 1,
        "top_storey_only": True,
    }
    tower["units"] = [u for u in (tower.get("units") or [])
                      if unit_key(u.get("type")) != "penthouse"] + [entry]
    return update_tower_parking(tower, policy)


def configure_tower(tower: Dict[str, Any], index: int, kind: str | None = None,
                    policy: Dict[str, Any] | None = None,
                    programme: list[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """Set one tower's unit programme and custom basement allocation.

    `kind` pins the tower to a single unit type -- the one-type-per-tower strategy the A-E
    cycle describes. A `programme` (a list of unit entries) is used as given, which is how
    the default single-tower project carries a whole mix on one plate. With neither, the
    tower gets the cycle's type for its index.
    """
    policy = policy or new_residential_policy()
    if programme:
        tower["units"] = [dict(unit) for unit in programme]
        tower["units_per_floor"] = sum(max(0, int(u.get("count") or 0)) for u in tower["units"])
        return update_tower_parking(tower, policy)
    cycle = policy.get("unit_type_cycle") or UNIT_CYCLE
    selected = kind or cycle[index % len(cycle)]
    selected = unit_key(selected) or selected.lower()
    spec = SPEC_BY_KEY.get(selected, UNIT_SPECS[cycle[index % len(cycle)]])
    count = max(1, int((policy.get("units_per_floor") or {}).get(selected, spec["units_per_floor"])))
    previous_units = tower.get("units") or []
    existing = next((u for u in previous_units if unit_key(u.get("type")) == selected), {})
    tower["units"] = [{
        "id": existing.get("id") or str(uuid.uuid4())[:8],
        "type": spec["type"],
        "count": count,
        "carpet_area": float(existing.get("carpet_area") or spec["carpet_area"]),
        "balcony_area": float(existing.get("balcony_area") or spec["balcony_area"]),
    }]
    tower["units_per_floor"] = count

    return update_tower_parking(tower, policy)


def update_tower_parking(tower: Dict[str, Any], policy: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Recalculate tower parking after a floor or unit-count edit, preserving its unit mix."""
    policy = policy or new_residential_policy()
    parking_by_type = parking_rules(policy)
    floors = max(1, int(tower.get("floors") or 1))
    reserved_cars = reserved_bikes = optional_cars = 0
    for unit in tower.get("units") or []:
        key = unit_key(unit.get("type"))
        if key not in parking_by_type:
            continue
        dwelling_count = max(0, int(unit.get("count") or 0)) * storeys_of(unit, floors)
        rule = parking_by_type[key]
        reserved_cars += int(rule.get("reserved_cars_per_unit") or 0) * dwelling_count
        reserved_bikes += int(rule.get("reserved_bikes_per_unit") or 0) * dwelling_count
        optional_cars += math.ceil(float(rule.get("optional_car_spaces_per_unit") or 0) * dwelling_count)
    first_key = unit_key((tower.get("units") or [{}])[0].get("type"))
    optional_ratio = float((parking_by_type.get(first_key) or {}).get("optional_car_spaces_per_unit") or 0)
    flexible_space_use = any(
        (parking_by_type.get(unit_key(u.get("type"))) or {}).get("flexible_space_use", False)
        for u in tower.get("units") or []
    )

    tower["parking"] = {
        **(tower.get("parking") or {}),
        "basement_only": True,
        "reserved_car_spaces": reserved_cars,
        "reserved_bike_spaces": reserved_bikes,
        "optional_car_pool_capacity": optional_cars,
        "optional_car_spaces_per_unit": optional_ratio,
        "flexible_space_use": flexible_space_use,
        "basement_car_spaces": reserved_cars + optional_cars,
        "basement_bike_spaces": reserved_bikes,
        "basement_area_sqm": round(
            (reserved_cars + optional_cars) * float(policy.get("car_space_area_sqm") or CAR_SPACE_AREA_SQM)
            + reserved_bikes * float(policy.get("bike_space_area_sqm") or BIKE_SPACE_AREA_SQM), 2
        ),
    }
    return tower


def summarize_units(towers: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    totals: Dict[str, int] = {}
    templates: Dict[str, Dict[str, Any]] = {}
    for tower in towers:
        floors = max(1, int(tower.get("floors") or 1))
        for unit in tower.get("units") or []:
            key = unit_key(unit.get("type"))
            if not key:
                continue
            totals[key] = (totals.get(key, 0)
                           + max(0, int(unit.get("count") or 0)) * storeys_of(unit, floors))
            templates[key] = unit
    total = sum(totals.values())
    if total <= 0:
        return []
    rows = []
    for key in [*UNIT_CYCLE, "penthouse"]:
        count = totals.get(key, 0)
        if not count:
            continue
        template = templates[key]
        spec = SPEC_BY_KEY.get(key) or UNIT_SPECS[key]
        rows.append({
            "type": template.get("type") or key,
            "carpet_area_sqm": float(template.get("carpet_area") or spec["carpet_area"]),
            "balcony_sqm": float(template.get("balcony_area") or spec["balcony_area"]),
            "share_pct": round(count * 100.0 / total, 1),
        })
    if rows:
        rows[-1]["share_pct"] = round(100.0 - sum(row["share_pct"] for row in rows[:-1]), 1)
    return rows


def parking_summary(towers: Iterable[Dict[str, Any]], basement_levels: int = 2) -> Dict[str, Any]:
    towers = list(towers)
    area = sum(float((t.get("parking") or {}).get("basement_area_sqm") or 0) for t in towers)
    car_spaces = sum(int((t.get("parking") or {}).get("basement_car_spaces") or 0) for t in towers)
    bikes = sum(int((t.get("parking") or {}).get("basement_bike_spaces") or 0) for t in towers)
    levels = max(int(basement_levels or 1), 1)
    return {
        "basement_only": True,
        "basement_levels": levels,
        "basement_area_per_level": round(area / levels, 2),
        "ground_area": 0.0,
        "area_per_slot": CAR_SPACE_AREA_SQM,
        "slots_required": car_spaces,
        "slots_provided": car_spaces,
        "reserved_car_spaces": sum(int((t.get("parking") or {}).get("reserved_car_spaces") or 0) for t in towers),
        "reserved_bike_spaces": bikes,
        "optional_car_pool_capacity": sum(int((t.get("parking") or {}).get("optional_car_pool_capacity") or 0) for t in towers),
        "tower_allocations": [
            {"tower_id": t.get("id"), "tower_name": t.get("name"), **(t.get("parking") or {})}
            for t in towers
        ],
    }
