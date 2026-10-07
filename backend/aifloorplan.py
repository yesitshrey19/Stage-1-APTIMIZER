"""AI-powered and architectural floor plan generator.

Dynamically creates realistic, code-compliant, Vastu-aligned residential floor plates
with proper circulation, exterior windows, attached balconies, door connections, and
room proportions.
"""
import asyncio
import json
import logging
import math
import os
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import vastu
from floorplan.design_guide import (
    BALCONY_RULES,
    DOOR_STANDARDS_MM,
    ENTRY_PADA_RULES,
    MASTER_PLANNING_SYSTEM_RULES,
    audit_room_zones,
    room_zone_kind,
)
from floorplan.realistic import generate_unit as generate_realistic_unit
from floorplan import unit_planner

logger = logging.getLogger(__name__)


def _unit_spec(unit_type: str, carpet: float) -> Dict[str, Any]:
    """Derive standard room proportions and program for a unit type based on Section 2 Scaling Protocol."""
    t = (unit_type or "2bhk").lower().strip()
    c = float(carpet or 85.0)

    if "5bhk" in t or "penthouse" in t:
        # 5BHK / Penthouse: 4-5 Ensuite Bedrooms (including Grand Master Suite with Walk-in Closet),
        # Powder Room, Servant Quarters, Massive wrap-around terraces, Family Lounge, Pantry, Dedicated Home Office.
        return {
            "rooms": [
                {"name": "Living & Dining", "type": "living", "pct": 0.22, "exterior": True, "balcony": True},
                {"name": "Family Lounge", "type": "living", "pct": 0.10, "exterior": True},
                {"name": "Grand Master Suite", "type": "bedroom", "pct": 0.16, "exterior": True, "balcony": True, "vastu": "SW"},
                {"name": "Walk-in Closet", "type": "closet", "pct": 0.04, "exterior": False},
                {"name": "Master Ensuite Bath", "type": "bathroom", "pct": 0.05, "exterior": False},
                {"name": "Bedroom 2 (Ensuite)", "type": "bedroom", "pct": 0.11, "exterior": True, "balcony": True},
                {"name": "Ensuite Bath 2", "type": "bathroom", "pct": 0.04, "exterior": False},
                {"name": "Bedroom 3 (Ensuite)", "type": "bedroom", "pct": 0.09, "exterior": True},
                {"name": "Ensuite Bath 3", "type": "bathroom", "pct": 0.035, "exterior": False},
                {"name": "Bedroom 4 (Guest Ensuite)", "type": "bedroom", "pct": 0.08, "exterior": True},
                {"name": "Ensuite Bath 4", "type": "bathroom", "pct": 0.035, "exterior": False},
                {"name": "Bedroom 5", "type": "bedroom", "pct": 0.07, "exterior": True},
                {"name": "Common Bathroom", "type": "bathroom", "pct": 0.04, "exterior": False},
                {"name": "Dedicated Home Office", "type": "office", "pct": 0.06, "exterior": True},
                {"name": "Kitchen", "type": "kitchen", "pct": 0.07, "exterior": True, "utility": True, "vastu": "SE"},
                {"name": "Pantry & Dry Storage", "type": "pantry", "pct": 0.03, "exterior": False},
                {"name": "Utility & Wash Area", "type": "utility", "pct": 0.03, "exterior": True},
                {"name": "Pooja Room (Ishanya)", "type": "pooja", "pct": 0.025, "exterior": True, "vastu": "NE"},
                {"name": "Powder Room", "type": "bathroom", "pct": 0.02, "exterior": False},
                {"name": "Servant Room", "type": "servant", "pct": 0.04, "exterior": True},
                {"name": "Servant Bath", "type": "servant", "pct": 0.02, "exterior": False},
                {"name": "MEP Duct Shaft", "type": "shaft", "pct": 0.015, "exterior": False},
            ]
        }
    elif "4bhk" in t:
        # 4BHK: 3 Bedrooms with Ensuite Baths, 1 Guest Bed + Common Bath/Powder, 1 Servant Room + Bath,
        # Balconies (Living, Master, Sub-Master), Expansive Utility & Pooja, Dedicated Service Entry.
        return {
            "rooms": [
                {"name": "Living & Dining", "type": "living", "pct": 0.26, "exterior": True, "balcony": True},
                {"name": "Master Bedroom", "type": "bedroom", "pct": 0.17, "exterior": True, "balcony": True, "vastu": "SW"},
                {"name": "Master Ensuite Bath", "type": "bathroom", "pct": 0.05, "exterior": False},
                {"name": "Sub-Master Bed (Ensuite)", "type": "bedroom", "pct": 0.14, "exterior": True, "balcony": True},
                {"name": "Ensuite Bath 2", "type": "bathroom", "pct": 0.04, "exterior": False},
                {"name": "Bedroom 3 (Ensuite)", "type": "bedroom", "pct": 0.12, "exterior": True},
                {"name": "Ensuite Bath 3", "type": "bathroom", "pct": 0.04, "exterior": False},
                {"name": "Guest Bed", "type": "bedroom", "pct": 0.10, "exterior": True},
                {"name": "Common Bath / Powder", "type": "bathroom", "pct": 0.035, "exterior": False},
                {"name": "Kitchen", "type": "kitchen", "pct": 0.08, "exterior": True, "utility": True, "vastu": "SE"},
                {"name": "Expansive Utility Area", "type": "utility", "pct": 0.035, "exterior": True},
                {"name": "Pooja Room (Ishanya)", "type": "pooja", "pct": 0.03, "exterior": True, "vastu": "NE"},
                {"name": "Servant Room", "type": "servant", "pct": 0.045, "exterior": True},
                {"name": "Servant Bath", "type": "servant", "pct": 0.02, "exterior": False},
                {"name": "MEP Duct Shaft", "type": "shaft", "pct": 0.015, "exterior": False},
            ]
        }
    elif "3bhk" in t:
        # 3BHK: 1 Master Bed with Ensuite, 1 Sub-Master Bed with Ensuite, 1 Guest/Kids Bed with Common Bath,
        # Balconies (Living + Master Bed), Large Utility, Dedicated Vastu Pooja Room.
        return {
            "rooms": [
                {"name": "Living & Dining", "type": "living", "pct": 0.30, "exterior": True, "balcony": True},
                {"name": "Master Bedroom", "type": "bedroom", "pct": 0.19, "exterior": True, "balcony": True, "vastu": "SW"},
                {"name": "Master Ensuite Bath", "type": "bathroom", "pct": 0.05, "exterior": False},
                {"name": "Sub-Master Bedroom", "type": "bedroom", "pct": 0.15, "exterior": True},
                {"name": "Sub-Master Ensuite Bath", "type": "bathroom", "pct": 0.045, "exterior": False},
                {"name": "Guest / Kids Bedroom", "type": "bedroom", "pct": 0.13, "exterior": True},
                {"name": "Common Bathroom", "type": "bathroom", "pct": 0.04, "exterior": False},
                {"name": "Kitchen", "type": "kitchen", "pct": 0.09, "exterior": True, "utility": True, "vastu": "SE"},
                {"name": "Large Utility Area", "type": "utility", "pct": 0.04, "exterior": True},
                {"name": "Pooja Room (Ishanya)", "type": "pooja", "pct": 0.03, "exterior": True, "vastu": "NE"},
                {"name": "MEP Duct Shaft", "type": "shaft", "pct": 0.015, "exterior": False},
            ]
        }
    elif "1bhk" in t or "studio" in t:
        # 1BHK: 1 Master Bed with Ensuite Bath, 1 Powder Room (optional/guest),
        # Main Balcony attached to Living, compact dry balcony attached to Kitchen, Pooja Niche.
        return {
            "rooms": [
                {"name": "Living & Dining", "type": "living", "pct": 0.40, "exterior": True, "balcony": True},
                {"name": "Master Bedroom", "type": "bedroom", "pct": 0.27, "exterior": True, "vastu": "SW"},
                {"name": "Ensuite Bathroom", "type": "bathroom", "pct": 0.08, "exterior": False},
                {"name": "Guest Powder Room", "type": "bathroom", "pct": 0.04, "exterior": False},
                {"name": "Kitchen", "type": "kitchen", "pct": 0.14, "exterior": True, "utility": True, "vastu": "SE"},
                {"name": "Dry Balcony / Utility", "type": "utility", "pct": 0.04, "exterior": True},
                {"name": "Pooja Niche", "type": "pooja", "pct": 0.02, "exterior": False, "vastu": "NE"},
                {"name": "MEP Duct Shaft", "type": "shaft", "pct": 0.01, "exterior": False},
            ]
        }
    else:
        # 2BHK (Default): 1 Master Bedroom with Ensuite Bath, 1 Secondary Bed with 1 Common Bath,
        # Main Balcony, Dedicated Utility off Kitchen, Small Dedicated Pooja Niche / Room.
        return {
            "rooms": [
                {"name": "Living & Dining", "type": "living", "pct": 0.34, "exterior": True, "balcony": True},
                {"name": "Master Bedroom", "type": "bedroom", "pct": 0.22, "exterior": True, "balcony": True, "vastu": "SW"},
                {"name": "Master Ensuite Bath", "type": "bathroom", "pct": 0.05, "exterior": False},
                {"name": "Bedroom 2", "type": "bedroom", "pct": 0.17, "exterior": True},
                {"name": "Common Bathroom", "type": "bathroom", "pct": 0.045, "exterior": False},
                {"name": "Kitchen", "type": "kitchen", "pct": 0.10, "exterior": True, "utility": True, "vastu": "SE"},
                {"name": "Utility Room", "type": "utility", "pct": 0.04, "exterior": True},
                {"name": "Pooja Niche / Room", "type": "pooja", "pct": 0.025, "exterior": True, "vastu": "NE"},
                {"name": "MEP Duct Shaft", "type": "shaft", "pct": 0.01, "exterior": False},
            ]
        }


def audit_vastu_and_mep(rooms: List[Dict[str, Any]], floor: int = 1, total_floors: int = 1,
                        boxes: Optional[Dict[str, Any]] = None,
                        meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What the plan actually achieves against the manual, stated as found.

    Two lists, never merged. `violations` are hard rules broken — a balcony off an internal
    wall, a pooja room with no door onto the living room or a wall shared with a bathroom,
    overlapping rooms, plan that belongs to no room. `anchors` reports the soft sector
    targets one by one, each carrying the sector the room is actually in.

    The previous version of this function returned a fixed dictionary of "Compliant"
    strings regardless of what the checks found, so a plan with a logged VIOLATION still
    reported every anchor as met. An audit that cannot say no is not an audit.
    """
    if not rooms:
        return {"score": 0, "status": "No rooms defined", "anchors": {}, "violations": [],
                "unit_audits": {}}

    boxes = boxes or {}
    meta = meta or {}

    units_rooms: Dict[str, List[Dict[str, Any]]] = {}
    for r in rooms:
        uid = r.get("unit_id")
        if uid:
            units_rooms.setdefault(uid, []).append(r)

    unit_audits: Dict[str, Any] = {}
    all_violations: List[str] = []
    anchor_tally = {"pooja": [0, 0], "kitchen": [0, 0], "master": [0, 0]}   # [met, placed]
    total_score = 0.0

    for uid, urooms in units_rooms.items():
        box = boxes.get(uid) or _bounding_box(urooms)
        info = meta.get(uid, {})
        entrance = next((r for r in urooms if r.get("main_entrance")), None)
        entry = (info.get("entry_edge") or (entrance or {}).get("entry_edge") or "S").upper()
        exterior = info.get("exterior_edges") or [edge for edge in ("N", "S", "E", "W")
                                                   if edge != entry]

        hard = vastu.check_unit(urooms, box, exterior, entry)
        unit_type = info.get("unit_type") or (urooms[0].get("unit_type") if urooms else "2bhk")
        program_violations = _room_program_violations(urooms, unit_type)
        hard["violations"].extend(program_violations)
        sectors = vastu.sector_report(urooms, box)
        mandala = audit_room_zones(urooms, box)
        for key, rep in sectors.items():
            if rep.get("placed"):
                anchor_tally[key][1] += 1
                if rep.get("met"):
                    anchor_tally[key][0] += 1

        # Score is the share of the manual's rules this flat actually meets: hard rules
        # carry three quarters of it because a broken one is a defect, not a preference.
        placed = [k for k, r in sectors.items() if r.get("placed")]
        anchor_share = (sum(1 for k in placed if sectors[k].get("met")) / len(placed)) if placed else 1.0
        guide_rooms = [r for r in mandala["placements"] if r.get("preferred_zones")]
        guide_share = (sum(1 for r in guide_rooms if r.get("met")) / len(guide_rooms)) if guide_rooms else 1.0
        soft = (anchor_share + guide_share) / 2
        hard_share = 0.0 if hard["violations"] else 1.0
        score = round((hard_share * 0.75 + soft * 0.25) * 100, 1)
        total_score += score

        all_violations.extend(f"{uid}: {v}" for v in hard["violations"])
        unit_audits[uid] = {
            "score": score,
            "unit_type": info.get("unit_type"),
            "entry_edge": entry,
            "facing": {"S": "South facing", "N": "North facing",
                       "E": "East facing", "W": "West facing"}.get(entry, entry),
            "coverage_pct": hard["coverage_pct"],
            "violations": hard["violations"],
            "program_violations": program_violations,
            "anchors": sectors,
            "mandala": mandala,
            "notes": info.get("notes", []),
            "checks": [f"{k}: {v['detail']}" for k, v in sectors.items() if v.get("placed")],
        }

    n_units = max(len(units_rooms), 1)
    avg_score = round(total_score / n_units, 1)

    def anchor_line(key, label):
        met, placed = anchor_tally[key]
        if not placed:
            return f"{label}: not in this floor's programme"
        if met == placed:
            return f"{label}: in sector in all {placed} flat(s)"
        return f"{label}: in sector in {met} of {placed} flat(s)"

    is_penthouse_tier = (
        (total_floors >= 8 and floor == total_floors)
        or any("penthouse" in str(r.get("unit_type") or "").lower() for r in rooms)
    )
    tier_label = "Penthouse Level" if is_penthouse_tier else "Residential Level"

    return {
        "score": avg_score,
        # Wording follows the violations, not the score: any hard breach is "Non-compliant"
        # however well the flat scores on sectors.
        "status": ("Non-compliant — hard rules broken" if all_violations
                   else "Fully Compliant" if avg_score >= 88
                   else "Compliant, some sector targets unmet"),
        "floor_tier": f"Floor {floor} of {total_floors} ({tier_label})",
        "violations": all_violations,
        "anchors": {
            "ishanya_ne_pooja": anchor_line("pooja", "Pooja (Ishanya, NE)"),
            "agni_se_kitchen": anchor_line("kitchen", "Kitchen (Agni, SE)"),
            "nairutya_sw_master": anchor_line("master", "Master bedroom (Nairutya, SW)"),
            "balconies_on_air": ("every balcony projects from an exterior wall"
                                 if not any("balcony" in v for v in all_violations)
                                 else "a balcony is not on an exterior wall — see violations"),
            "pooja_door_from_living": ("every pooja room takes its door off the living room"
                                       if not any("pooja" in v for v in all_violations)
                                       else "a pooja room fails its door or bathroom rule — see violations"),
            "privacy_gradients": ("bedroom doors route through a passage or foyer"
                                  if not any("passage or foyer" in v for v in all_violations)
                                  else "a bedroom has no transitional space to open onto"),
        },
        "unit_audits": unit_audits,
    }


def _bounding_box(rooms: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The flat's box, recovered from its rooms — used when the caller did not supply one
    (an audit of a stored plan, or of one an LLM returned)."""
    xs = [float(r.get("x", 0)) for r in rooms]
    ys = [float(r.get("y", 0)) for r in rooms]
    x2 = [float(r.get("x", 0)) + float(r.get("w", 0)) for r in rooms]
    y2 = [float(r.get("y", 0)) + float(r.get("h", 0)) for r in rooms]
    return {"x": min(xs), "y": min(ys), "w": max(x2) - min(xs), "h": max(y2) - min(ys)}


def _unit_mix_violations(rooms: List[Dict[str, Any]], tower: Dict[str, Any]) -> List[str]:
    """Ensure a model response preserves this tower's saved apartment mix."""
    expected_units = tower.get("units") or [{"type": "2bhk", "count": 2}]
    expected = Counter()
    for unit in expected_units:
        kind = str(unit.get("type") or "2bhk").lower().replace(" ", "")
        expected[kind] += max(1, int(unit.get("count") or 1))

    by_id: Dict[str, List[Dict[str, Any]]] = {}
    missing_id = []
    for room in rooms:
        if room.get("type") == "common" and not room.get("unit_id"):
            continue
        uid = room.get("unit_id")
        if not uid:
            missing_id.append(str(room.get("id") or room.get("name") or "room"))
            continue
        by_id.setdefault(str(uid), []).append(room)

    actual = Counter()
    issues = []
    for uid, unit_rooms in by_id.items():
        kinds = {str(room.get("unit_type") or "").lower().replace(" ", "")
                 for room in unit_rooms}
        kinds.discard("")
        if len(kinds) != 1:
            issues.append(f"{uid}: apartment rooms have mixed or missing unit_type values")
        elif kinds:
            actual[next(iter(kinds))] += 1
    if missing_id:
        issues.append(f"rooms missing unit_id: {', '.join(missing_id[:4])}")
    if actual != expected:
        issues.append(f"unit mix mismatch: expected {dict(expected)}, got {dict(actual)}")
    return issues


def _vertical_stack_violations(rooms: List[Dict[str, Any]], tower: Dict[str, Any], floor: int) -> List[str]:
    """Require AI-generated kitchens and wet/service cores to align with the saved first floor."""
    if floor <= 1:
        return []
    floor_layouts = tower.get("floor_layouts") or {}
    first = floor_layouts.get("1") or floor_layouts.get(1) or {}
    reference = (first.get("rooms") or [])
    core_types = {"kitchen", "bathroom", "shaft", "utility"}
    reference_cores = [r for r in reference if r.get("unit_id") and r.get("type") in core_types]
    if not reference_cores:
        return []

    def key(room):
        return (str(room.get("unit_id")), str(room.get("type")),
                str(room.get("name") or "").strip().lower())

    actual_by_key = {key(room): room for room in rooms if room.get("unit_id") and room.get("type") in core_types}
    issues = []
    for expected in reference_cores:
        actual = actual_by_key.get(key(expected))
        if actual is None:
            issues.append(f"{expected.get('unit_id')}: missing stacked {expected.get('name') or expected.get('type')}")
            continue
        for dimension in ("x", "y", "w", "h"):
            try:
                delta = abs(float(actual[dimension]) - float(expected[dimension]))
            except (KeyError, TypeError, ValueError):
                delta = float("inf")
            if delta > 0.1:
                issues.append(
                    f"{expected.get('unit_id')}: {expected.get('name') or expected.get('type')} "
                    f"does not align with Floor 1 ({dimension})"
                )
                break
    return issues


def _room_program_violations(rooms: List[Dict[str, Any]], unit_type: str) -> List[str]:
    """Check the guide's minimum room program for a saved apartment tier."""
    programme = vastu.unit_programme(unit_type, 85.0)
    beds = programme["beds"]
    kinds = [room_zone_kind(room) for room in rooms]
    issues = []
    actual_beds = sum(1 for room in rooms if room.get("type") == "bedroom")
    if actual_beds != beds:
        issues.append(f"expected {beds} bedrooms for {unit_type}, found {actual_beds}")
    for room_type, label in (("living", "living/dining room"), ("kitchen", "kitchen")):
        if room_type not in [room.get("type") for room in rooms]:
            issues.append(f"required {label} is missing for {unit_type}")
    minimum_baths = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}.get(beds, 1)
    bath_count = kinds.count("bathroom")
    if bath_count < minimum_baths:
        issues.append(f"{unit_type} needs at least {minimum_baths} bathrooms; found {bath_count}")
    if "pooja" not in kinds:
        issues.append(f"required pooja room/niche is missing for {unit_type}")
    if "utility" not in kinds:
        issues.append(f"required kitchen utility is missing for {unit_type}")

    balcony_sources = {
        str(room.get("projects_from"))
        for room in rooms if room.get("type") in ("balcony", "terrace")
    }
    living = next((room for room in rooms if room.get("type") == "living"), None)
    if living and str(living.get("id")) not in balcony_sources:
        issues.append("living/dining balcony is missing")
    if beds >= 3:
        master = next((room for room in rooms if room.get("type") == "bedroom" and
                       (str(room.get("id", "")).endswith("-mbed") or
                        "master" in str(room.get("name", "")).lower())), None)
        if master and str(master.get("id")) not in balcony_sources:
            issues.append("master bedroom balcony is missing")
    if beds >= 4 and not any(
        room.get("type") == "bedroom" and str(room.get("id")) in balcony_sources
        and not (str(room.get("id", "")).endswith("-mbed") or
                 "master" in str(room.get("name", "")).lower())
        for room in rooms
    ):
        issues.append("secondary bedroom balcony is missing")
    if beds >= 4 and "servant" not in kinds:
        issues.append("servant room is missing")
    if beds >= 5 and "penthouse" not in str(unit_type or "").lower():
        names = [str(room.get("name") or "").lower() for room in rooms]
        if not any("pantry" in name for name in names):
            issues.append("5 BHK pantry is missing")
        if "office" not in kinds:
            issues.append("5 BHK home office is missing")
    return issues


def _apply_guide_metadata(rooms: List[Dict[str, Any]], box: Dict[str, Any], entry_edge: str) -> None:
    """Attach the guide's door and balcony standards to generated geometry."""
    by_id = {str(room.get("id")): room for room in rooms}
    edge = (entry_edge or "S").upper()
    allowed_padas = ENTRY_PADA_RULES.get(edge, ())
    for room in rooms:
        room_type = room_zone_kind(room)
        if room_type == "utility":
            raw_mm = round(min(float(room.get("w") or 0), float(room.get("h") or 0)) * 1000)
            room["utility_width_mm"] = min(1500, max(1200, raw_mm))
        has_door = bool(
            room.get("main_entrance") or room.get("door_to") or room.get("door_from")
            or room.get("door_child_of") or room.get("service_access")
        )
        if has_door:
            if room.get("main_entrance"):
                standard = DOOR_STANDARDS_MM["main_entrance"]
                room["entry_edge"] = edge
                room["entry_pada"] = 4 if 4 in allowed_padas else (allowed_padas[0] if allowed_padas else None)
                room["door_width_mm"] = 1100
                room["door_height_mm"] = standard["height"]
            else:
                if room.get("service_access"):
                    room["entry_edge"] = str(room.get("entry_edge") or edge).upper()
                door_kind = (
                    "bedroom" if room_type in ("master", "bedroom") else
                    "kitchen" if room_type == "kitchen" else
                    "bathroom" if room_type == "bathroom" else "other"
                )
                standard = DOOR_STANDARDS_MM[door_kind]
                room["door_width_mm"] = standard["width"]
                room["door_height_mm"] = standard["height"]
            room["hinge_offset_mm"] = 125
            room["door_swing"] = "inward_clockwise"

        if room.get("type") not in ("balcony", "terrace"):
            continue
        source = by_id.get(str(room.get("projects_from") or ""))
        if not source:
            continue
        depth_mm = round(min(float(room.get("w") or 0), float(room.get("h") or 0)) * 1000)
        room["balcony_attached"] = True
        room["balcony_depth_mm"] = depth_mm
        source["balcony_attached"] = True
        source["balcony_depth_mm"] = depth_mm
        eps = 0.03
        exposed = []
        if abs(float(room.get("y") or 0) - float(box.get("y") or 0)) < eps:
            exposed.append("N")
        if abs(float(room.get("x") or 0) - float(box.get("x") or 0)) < eps:
            exposed.append("W")
        if abs(float(room.get("x") or 0) + float(room.get("w") or 0)
               - (float(box.get("x") or 0) + float(box.get("w") or 0))) < eps:
            exposed.append("E")
        if abs(float(room.get("y") or 0) + float(room.get("h") or 0)
               - (float(box.get("y") or 0) + float(box.get("h") or 0))) < eps:
            exposed.append("S")
        room["parapet_type"] = "1000mm masonry" if any(e in exposed for e in ("S", "W")) else "light glass"
        if room_zone_kind(source) == "living" and any(e in exposed for e in ("N", "E")):
            room["slab_drop_mm"] = 20
            source["slab_drop_mm"] = 20


# Bumped whenever the deterministic planner changes what it draws, so stored layouts made by an
# older planner are reported stale (and offered for regeneration) rather than silently replaced
# -- a stored layout may carry the user's own room edits.
PLANNER_VERSION = 2


def generate_architectural_template(tower: Dict[str, Any], floor: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """A floor plate packed to the Generative Architecture & Vastu Logic Manual."""
    total_floors = max(int(tower.get("floors") or 1), 1)
    is_top_penthouse = total_floors >= 8 and floor == total_floors

    raw_units = tower.get("units") or [{"type": "2bhk", "count": 2, "carpet_area": 85.0}]
    expanded_units = []
    for u in raw_units:
        count = max(int(u.get("count") or 1), 1)
        u_type = u.get("type", "2bhk")
        if is_top_penthouse and "penthouse" not in str(u_type).lower():
            u_type = f"{u_type} penthouse"
        carpet = float(u.get("carpet_area") or 85.0)
        for _ in range(count):
            expanded_units.append({"type": u_type, "carpet": carpet})
    if not expanded_units:
        expanded_units = [{"type": "2bhk", "carpet": 85.0}, {"type": "3bhk", "carpet": 115.0}]

    corridor_w = max(float(tower.get("corridor_width") or 2.0), 1.8)
    n = len(expanded_units)
    north_units = expanded_units[:(n + 1) // 2]
    south_units = expanded_units[(n + 1) // 2:]

    def dims(u):
        # The flat's envelope is what its room brief adds up to (see unit_planner), so no
        # planner has to stretch rooms to fill a box sized by a square root of the area.
        return unit_planner.envelope(u["type"], u["carpet"])

    north_dims = [dims(u) for u in north_units]
    south_dims = [dims(u) for u in south_units]
    north_h = max([h for _, h in north_dims], default=0.0)
    south_h = max([h for _, h in south_dims], default=0.0)
    corridor_y = north_h

    rooms: List[Dict[str, Any]] = []
    unit_boxes: Dict[str, Dict[str, Any]] = {}
    unit_meta: Dict[str, Dict[str, Any]] = {}

    def place_row(units, dimensions, is_north):
        """Lay one row of flats along the corridor.

        The corridor is the wall each flat is entered from, so it is also the one wall that
        is NOT open to air. A north-row flat is entered from its south wall and is therefore
        South-facing in the manual's rotation matrix; a south-row flat is North-facing. End
        flats gain their outer side wall as a second facade.
        """
        cursor = 0.0
        entry_edge = "S" if is_north else "N"
        last = len(units) - 1
        for idx, (u, (uw, uh)) in enumerate(zip(units, dimensions)):
            uid = f"unit-{'N' if is_north else 'S'}{idx + 1}"
            box_y = (corridor_y - uh) if is_north else (corridor_y + corridor_w)
            box = {"x": round(cursor, 2), "y": round(box_y, 2), "w": uw, "h": uh}
            exterior = ["N"] if is_north else ["S"]
            if idx == 0:
                exterior.append("W")
            if idx == last:
                exterior.append("E")
            candidates = []
            planners = tuple(unit_planner.variants(u["type"], u["carpet"])) + (
                ("room planner", generate_realistic_unit),
                ("Vastu guide packer", vastu.pack_unit),
            )
            for planner_order, (source, planner) in enumerate(planners):
                try:
                    if source != "Vastu guide packer":
                        candidate, candidate_notes = planner(
                            box, u["type"], u["carpet"], entry_edge, uid, idx, exterior
                        )
                    else:
                        candidate, candidate_notes = planner(
                            box, u["type"], u["carpet"], entry_edge, exterior, uid, idx
                        )
                    if not candidate:
                        continue
                    _apply_guide_metadata(candidate, box, entry_edge)
                    hard = vastu.check_unit(candidate, box, exterior, entry_edge)
                    program_violations = _room_program_violations(candidate, u["type"])
                    zones = audit_room_zones(candidate, box)
                    anchors = vastu.sector_report(candidate, box)
                    anchor_misses = sum(
                        1 for report in anchors.values()
                        if report.get("placed") and not report.get("met")
                    )
                    expected_beds = vastu.unit_programme(u["type"], u["carpet"])["beds"]
                    actual_beds = sum(1 for room in candidate if room.get("type") == "bedroom")
                    bedroom_misses = abs(expected_beds - actual_beds)
                    # Livability first: a plan with corridor-shaped rooms loses to any plan
                    # without them, however many soft Vastu placements it ticks.
                    quality = (
                        len(unit_planner.livability_defects(candidate)),
                        len(hard["violations"]),
                        len(program_violations),
                        # A rule-clean realistic plan wins; the soft Vastu placements below
                        # only choose between its two mirror images.
                        0 if source.startswith("realistic planner") else 1,
                        # Forbidden zones (kitchen in NE...), plus a master bedroom in the NE
                        # quadrant: the guide's anchors are judged by quadrant, and a master
                        # there is the one placement Vastu rules out outright.
                        len(zones.get("violations") or []) + sum(
                            1 for room in candidate
                            if str(room.get("id", "")).endswith("-mbed") and vastu.sector_of(room, box) == "NE"),
                        anchor_misses,          # the guide's primary anchors: kitchen SE, master SW, pooja NE
                        zones["preference_misses"],
                        bedroom_misses,
                        planner_order,
                    )
                    candidates.append((quality, candidate, candidate_notes, source))
                except Exception as exc:
                    logger.debug("%s failed for %s: %s", source, uid, exc)

            if candidates:
                _, packed, notes, selected_source = min(candidates, key=lambda item: item[0])
                notes = list(notes) + [f"Selected {selected_source} using the shared guide-rule audit."]
                notes.extend(_room_program_violations(packed, u["type"]))
            else:
                packed, notes = [], ["Neither deterministic room planner could fit this unit envelope."]
            rooms.extend(packed)
            unit_boxes[uid] = box
            unit_meta[uid] = {"entry_edge": entry_edge, "exterior_edges": exterior,
                              "notes": notes, "unit_type": u["type"], "carpet": u["carpet"]}
            cursor += uw + 0.5      # party wall between flats
        return max(cursor - 0.5, 0.0)

    north_w = place_row(north_units, north_dims, True)
    south_w = place_row(south_units, south_dims, False)

    total_floor_w = max(north_w, south_w, 12.0)
    rooms.append({
        "id": f"corridor-{floor}",
        "name": f"Central Spine Corridor (Floor {floor})",
        "type": "common",
        "x": 0.0, "y": round(corridor_y, 2),
        "w": round(total_floor_w, 2), "h": round(corridor_w, 2),
        "has_window": True, "exterior": True,
    })

    validation = {"vastu": audit_vastu_and_mep(rooms, floor, total_floors,
                                               boxes=unit_boxes, meta=unit_meta)}
    return rooms, validation


async def generate_ai_floor_layout(tower: Dict[str, Any], floor: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Generate with the shared planning guide; audit and fall back to that same guide."""
    import ai

    has_key = bool(
        os.environ.get("GEMINI_API_KEY", "").strip() or
        os.environ.get("GROK_API_KEY", "").strip() or
        os.environ.get("GROQ_API_KEY", "").strip()
    )

    if not has_key:
        logger.info("No AI key configured; using Generative Architecture & Vastu Template engine.")
        rooms, validation = generate_architectural_template(tower, floor)
        validation["vastu"]["source"] = "guide-based deterministic planner (no model key configured)"
        return rooms, validation

    total_floors = max(int(tower.get("floors") or 1), 1)

    units_summary = ", ".join(
        f"{u.get('count', 1)}x {u.get('type', '2bhk')} ({u.get('carpet_area', 85)} m²)"
        for u in (tower.get("units") or [])
    )
    floor_layouts = tower.get("floor_layouts") or {}
    floor_one = floor_layouts.get("1") or floor_layouts.get(1) or {}
    vertical_stack_reference = [
        {key: room.get(key) for key in ("id", "unit_id", "unit_type", "unit_index", "type", "name", "x", "y", "w", "h")}
        for room in (floor_one.get("rooms") or [])
        if room.get("unit_id") and room.get("type") in ("kitchen", "bathroom", "shaft", "utility")
    ]

    system_prompt = MASTER_PLANNING_SYSTEM_RULES + """
You are the Aptimizer residential floor-plan architect. Treat the guide and the supplied
project configuration as constraints, return a feasible schematic, and never claim that a
layout passes the audit. The supplied project configuration overrides default unit mix and
parking rules. Output only valid JSON, without Markdown.
"""
    user_prompt = f"""
Design the complete residential floor plate for Tower '{tower.get('name', 'Tower A')}',
Floor {floor} of {total_floors}. This is a residential level. Keep the exact saved unit type,
unit count and carpet area on every floor, including the top floor. Do not create a penthouse
unless the saved unit mix explicitly contains a 5 BHK or penthouse unit.

Authoritative tower unit configuration (preserve exactly):
{json.dumps(tower.get('units') or [], ensure_ascii=False)}
Unit mix summary: {units_summary or 'No units saved; use the guide defaults for this tower.'}
Saved tower basement parking configuration (parking stays underground and outside this plan):
{json.dumps(tower.get('parking') or {}, ensure_ascii=False)}
Floor 1 kitchen, bathroom, shaft and utility positions to stack vertically (preserve the
same apartment ids and x/y/w/h where a reference exists):
{json.dumps(vertical_stack_reference, ensure_ascii=False)}
Central corridor width: {tower.get('corridor_width', 2.0)} m.

Apply the attached guide's full 3x3 Mandala, room scaling, vertical wet-core, entry Pada,
door, privacy, daylight, balcony, and utility rules. Use one independent bounding box for
each apartment, place the kitchen/hob and fixed wet core in SE on every floor, keep NE free
of kitchens, toilets and service rooms, and keep the central Brahmasthan open to circulation.
Keep adjacent apartment boundaries and the central corridor clear of apartment rooms.

Return one JSON object with a top-level `rooms` array. Give each room a stable `id`, `name`,
`type`, `unit_id`, exact `unit_type`, zero-based `unit_index`, and positive non-overlapping
`x`, `y`, `w`, `h` coordinates in metres. Include a central corridor as `type: "common"`
without a `unit_id`. For applicable rooms include `main_entrance`, `entry_edge`,
`entry_pada`, `service_access`, `door_to` or `door_child_of`, `door_width_mm`, `door_height_mm`,
`hinge_offset_mm`, `door_swing`, `has_window`, `balcony_attached`, `balcony_depth_mm`,
`parapet_type`, and `slab_drop_mm`. Coordinates use origin at upper-left, x increases East,
and y increases South. Represent every configured apartment exactly once per count; do not
invent units, change unit tiers, omit required rooms, or place parking on this floor.
"""
    try:
        res = await asyncio.wait_for(
            ai.generate_markdown(system_prompt, user_prompt, session_hint="floorplan", prefer_fast=True),
            timeout=14.0
        )
        text = res.get("text", "").strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join([l for l in lines if not l.startswith("```")])
        data = json.loads(text)
        rooms = data.get("rooms", [])
        if rooms and len(rooms) >= 6:
            # The model's plan is checked against the same hard rules the packer is held to,
            # and is used only if it passes. A layout is geometry: overlapping rooms, a
            # balcony off an internal wall or a pooja room walled against a bathroom are
            # wrong whoever drew them, and a plausible-looking plan that fails them is worse
            # than the deterministic one, because it looks considered.
            vastu_report = audit_vastu_and_mep(rooms, floor, total_floors)
            mix_violations = _unit_mix_violations(rooms, tower)
            stack_violations = _vertical_stack_violations(rooms, tower, floor)
            if not vastu_report.get("violations") and not mix_violations and not stack_violations:
                vastu_report["source"] = "model"
                return rooms, {"vastu": vastu_report}
            rejected_violations = (list(vastu_report.get("violations") or []) + mix_violations
                                   + stack_violations)
            logger.info("AI floor plan rejected on %d hard rule(s): %s",
                        len(rejected_violations), "; ".join(rejected_violations[:3]))
            rejected = rejected_violations[:6]
            rooms, validation = generate_architectural_template(tower, floor)
            validation["vastu"]["source"] = "guide-based deterministic planner (model plan rejected)"
            validation["vastu"]["rejected_model_plan"] = rejected
            return rooms, validation
    except Exception as e:
        logger.warning(f"AI floor plan generation failed or timed out ({e}); using Generative Vastu Template engine.")

    rooms, validation = generate_architectural_template(tower, floor)
    validation["vastu"]["source"] = "guide-based deterministic planner"
    return rooms, validation
