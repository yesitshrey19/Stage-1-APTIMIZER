"""Autonomous AI Engineering & Intelligent Planning Engine.

Implements:
1. One-Click Project Generation: Synthesizes a complete, compliant scheme from basic parameters.
2. Conversational Project Design: Natural-language project mutations with atomic updates.
3. Multi-Agent Engineering Teams: 5-agent collaborative review (Architect, Structural,
   MEP & Environmental, Quantity Surveyor / Cost, Compliance & Safety Officer).
4. Autonomous Compliance Checking: Deep code scanner against NBC 2016 and local bye-laws
   with clause citations and auto-remediation patches.
5. Autonomous BOQ & Costing: Self-driving takeoff, DSR rate application, and benchmark variance.
6. AI Township & Mixed-Use Planner: Multi-sector master planning and mixed-use zoning.
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import engine
from siteplan.frame import LocalFrame
from siteplan.devcontrols import recommend, setback_minimums
from residential_defaults import (
    new_residential_policy, configure_tower, update_tower_parking, add_penthouse,
    parking_summary, summarize_units, tower_index,
)


# --------------------------------------------------------------------------- 1. One-Click Generation

def _engine_tower_fields(tower: Dict[str, Any]) -> None:
    """Give a generated tower the fields the engine, compliance and take-off read."""
    import uuid
    from defaults import floor_layout_entry
    floors = int(tower.get("floors") or 1)
    footprint = float(tower.get("footprint_sqm") or 0)
    tower["footprint_area"] = footprint
    tower["floor_height"] = float(tower.get("floor_height_m") or 3.0)
    tower.setdefault("common_area", 240.0)
    tower.setdefault("corridor_width", 1.8)
    tower.setdefault("corridor_length", round(math.sqrt(footprint * 1.5), 1) if footprint else 32.0)
    tower.setdefault("exits_per_floor", 2)
    tower.setdefault("max_travel_distance", 24.0)
    tower.setdefault("staircases", [{"id": str(uuid.uuid4())[:8], "count": 2, "width": 1.5,
                                     "type": "dog-legged", "location": "core"}])
    # One lift per 8 floors, never fewer than two (one can be out of service).
    tower.setdefault("lifts", [{"id": str(uuid.uuid4())[:8], "count": max(2, math.ceil(floors / 8.0)),
                                "capacity": 8, "location": "core"}])
    tower.setdefault("common_spaces", [{"id": str(uuid.uuid4())[:8], "name": "Entrance Lobby",
                                        "type": "lobby", "area": 90.0}])
    if "floor_layouts" not in tower:
        try:
            ground = floor_layout_entry(tower, 1)
            tower["floor_layouts"] = {"1": ground}
            tower["rooms"] = ground["rooms"]
        except Exception:
            pass


def one_click_generate(params: Dict[str, Any]) -> Dict[str, Any]:
    """Synthesizes a complete, compliant project from high-level parameters.

    Expected params:
      - name: str (default: "Aptimizer Autonomous Scheme")
      - plot_area_sqm: float (default: 10000.0)
      - target_tier: "affordable" | "mid" | "luxury" (default: "mid")
      - city: str (default: "Bengaluru")
      - target_far: Optional[float] (default: 2.5)
      - floors: Optional[int] (default: 12)
    """
    name = params.get("name") or "Aptimizer Autonomous Scheme"
    area_sqm = float(params.get("plot_area_sqm") or 10000.0)
    tier = (params.get("target_tier") or "mid").lower()
    city = params.get("city") or "Bengaluru"
    floors = int(params.get("floors") or (10 if tier == "affordable" else 14 if tier == "mid" else 18))
    target_far = float(params.get("target_far") or (2.0 if tier == "affordable" else 2.75 if tier == "mid" else 3.5))

    # Determine plot dimensions (assume a 4:3 aspect ratio)
    width = math.sqrt(area_sqm * (4.0 / 3.0))
    depth = area_sqm / width
    half_w, half_d = width / 2.0, depth / 2.0

    # Default origin: Bengaluru or given coords
    lat0, lng0 = float(params.get("latitude") or 12.9716), float(params.get("longitude") or 77.5946)
    frame = LocalFrame(lat0, lng0)
    local_ring = [
        [-half_w, -half_d],
        [half_w, -half_d],
        [half_w, half_d],
        [-half_w, half_d],
        [-half_w, -half_d],
    ]
    coords = frame.ring_to_latlng(local_ring)

    plot = {
        "coordinates": coords,
        "area_sqm": round(area_sqm, 2),
        "width_m": round(width, 1),
        "length_m": round(depth, 1),
        "road_width_m": 18.0,
        "road_edge": "front",
    }

    # Development controls & statutory setbacks
    min_setbacks = setback_minimums(area_sqm, road_width=plot.get("road_width_m", 18.0), height_m=floors * 3.0)
    front_sb = min_setbacks.get("front", {}).get("minimum_m", 12.0) if isinstance(min_setbacks.get("front"), dict) else 12.0
    rear_sb = min_setbacks.get("rear", {}).get("minimum_m", 9.0) if isinstance(min_setbacks.get("rear"), dict) else 9.0
    side_sb = min_setbacks.get("side", {}).get("minimum_m", 9.0) if isinstance(min_setbacks.get("side"), dict) else 9.0
    default_sb = min_setbacks.get("default", {}).get("minimum_m", 9.0) if isinstance(min_setbacks.get("default"), dict) else 9.0

    dev_controls = {
        "permissible_fsi": target_far,
        "max_ground_coverage_pct": 35.0 if tier == "luxury" else 40.0,
        "max_height_m": floors * 3.0 + 3.0,
        "setbacks": {
            "front": front_sb,
            "rear": rear_sb,
            "side": side_sb,
            "side1": side_sb,
            "side2": side_sb,
            "default": default_sb,
        },
        "setback_rules": min_setbacks,
    }

    # Number of towers & footprint sizing based on plot area & target FAR
    total_builtup_target = area_sqm * target_far
    num_towers = max(1, min(4, int(area_sqm // 3500)))
    tower_builtup = total_builtup_target / num_towers
    footprint_per_tower = round((tower_builtup / floors), 1)

    # Place towers spaced along X axis
    towers = []
    spacing = width / (num_towers + 1)
    for i in range(num_towers):
        tx = -half_w + (i + 1) * spacing
        ty = 0.0
        t_w = round(math.sqrt(footprint_per_tower * 1.5), 1)
        t_d = round(footprint_per_tower / t_w, 1)
        hw, hd = t_w / 2.0, t_d / 2.0
        poly_local = [[tx - hw, ty - hd], [tx + hw, ty - hd], [tx + hw, ty + hd], [tx - hw, ty + hd]]

        towers.append({
            "id": f"T{i+1}",
            "name": f"Tower {chr(65 + i)}",
            "floors": floors,
            "floor_height_m": 3.0,
            "footprint_sqm": footprint_per_tower,
            "centre_local": [round(tx, 2), round(ty, 2)],
            "polygons_local": [poly_local],
            "units_per_floor": 4 if tier == "luxury" else 6 if tier == "mid" else 8,
            "structural_system": "RCC Shear Wall" if floors > 12 else "RCC Frame",
        })

    # Assign each building one tier in the repeating A–E residential program. A luxury
    # scheme crowns every tower with one full-storey penthouse, so the mix a buyer is shown
    # includes the top-of-the-building home the tier promises.
    residential_policy = new_residential_policy()
    for index, tower in enumerate(towers):
        configure_tower(tower, index, policy=residential_policy)
        if tier == "luxury":
            add_penthouse(tower, residential_policy)
    # The engine reads its own tower schema (footprint_area, floor_height, cores, egress).
    # Without these a saved one-click project had a zero footprint -- no concrete or steel in
    # the take-off -- and no staircases, lifts or exits, so it failed its own compliance.
    for tower in towers:
        _engine_tower_fields(tower)
    unit_mix = summarize_units(towers)

    # Parking (units come from the engine below, not units_per_floor x floors)
    parking = {
        **parking_summary(towers, basement_levels=2 if floors > 12 else 1),
        "podium_levels": 1 if tier == "luxury" else 0,
        "surface_slots": 0,
        "ev_charging_slots": 0,
    }
    parking["ev_charging_slots"] = int(parking["slots_required"] * 0.20)

    # Headline figures from the engine, so the scheme reports the same FAR, units and cost
    # the rest of the platform will compute once it is saved.
    probe = {"plot": plot, "towers": towers, "parking": parking, "residential_policy": residential_policy,
             "config": {}, "society_amenities": []}
    areas = engine.area_metrics(probe)
    # Parking policy the engine checks against, then the basement sized until it meets the
    # engine's own requirement -- a generated scheme should not open with a deficit.
    parking.update({"ratio_per_unit": 1.0, "visitor_pct": 10.0, "ev_pct": 20.0, "accessible_pct": 2.0,
                    "ramp": {"slope_pct": 10.0, "width": 3.6, "turning_radius": 6.0}})
    pm = engine.parking_metrics(probe, areas)
    levels = max(int(parking.get("basement_levels") or 1), 1)
    if pm["deficit"] > 0:
        slot = float(parking.get("area_per_slot") or 23.0)
        parking["basement_area_per_level"] = round(
            float(parking.get("basement_area_per_level") or 0) + math.ceil(pm["deficit"] * slot / levels) + slot, 2)
        pm = engine.parking_metrics(probe, areas)
    parking["visitor_provided"] = pm["visitor_required"]
    parking["ev_provided"] = pm["ev_required"]
    parking["accessible_provided"] = pm["accessible_required"]
    parking["ev_charging_slots"] = pm["ev_required"]
    achieved_builtup = float(areas["builtup_area_sqm"])
    try:
        estimated_cost = float(engine.analyse(probe)["cost"]["total"])
    except Exception:
        estimated_cost = None

    return {
        "name": name,
        "client": f"{tier.capitalize()} Housing Corp",
        "location": city,
        "tier": tier,
        "plot": plot,
        "dev_controls": dev_controls,
        "towers": towers,
        "unit_mix": unit_mix,
        "residential_policy": residential_policy,
        "parking": parking,
        "achieved_metrics": {
            "total_builtup_sqm": round(achieved_builtup, 1),
            "achieved_far": round(areas["far"], 2),
            "ground_coverage_pct": round(areas["ground_coverage_pct"], 1),
            "total_units": int(areas["total_units"]),
            "estimated_cost_inr": round(estimated_cost, 0) if estimated_cost is not None else None,
        },
        "generation_log": [
            f"Synthesized {tier} scheme for {area_sqm:,.0f} m² plot in {city}",
            f"Configured {num_towers} towers @ {floors} storeys each",
            f"Achieved FAR: {areas['far']:.2f} (Target: {target_far:.2f})"
            + ("" if areas["far"] >= target_far * 0.95 else
               " - the unit programme does not fill the tower plates; add units or floors to reach the target"),
            f"Calculated statutory NBC setbacks: Front {front_sb}m, Rear {rear_sb}m, Sides {side_sb}m",
        ]
    }


# --------------------------------------------------------------------------- 2. Conversational Project Design

def unit_type_key(value: Any) -> str:
    """Normalise a unit label ("3 BHK", "3bhk", "Penthouse") to its type key.

    Module level because three separate instruction branches need it; it used to live
    inside one of them, and the other two called a name that did not exist.
    """
    value = str(value or "").lower()
    if "penthouse" in value:
        return "penthouse"
    if re.search(r"\bstudio\b", value):
        return "studio"
    match = re.search(r"([1-5](?:\.5)?)\s*bhk", value)
    return f"{match.group(1)}bhk" if match else re.sub(r"[^a-z0-9]+", "", value)


def conversational_design(project: Dict[str, Any], instruction: str) -> Dict[str, Any]:
    """Parses natural-language instructions and applies atomic, validated updates.

    Supported mutations:
      - Change floor count: "make tower 1 16 floors", "add 2 floors"
      - Adjust unit mix: "increase 3BHK to 60%", "set 2BHK to 40%"
      - Assign one unit type per tower: "Tower A consists only of 2BHK units"
      - Modify parking: "add 20 EV slots", "add basement level", "convert surface parking to park"
      - Adjust setbacks: "increase front setback to 12m"
      - Scale footprint: "reduce tower footprint by 10%"
    """
    text = (instruction or "").lower().strip()
    mutations_applied = []
    updated_project = dict(project)

    # 1. Floor adjustment
    floor_match = re.search(r"(?:set|make|increase|change)?\s*(?:tower\s*(\w+))?\s*(?:to\s*)?(\d+)\s*(?:floors?|storeys?)", text)
    if floor_match:
        target_tower = floor_match.group(1)
        new_floors = int(floor_match.group(2))
        towers = [dict(t) for t in updated_project.get("towers") or []]
        for t in towers:
            tid = t.get("id", "").upper()
            tname = t.get("name", "").upper()
            match = False
            if not target_tower:
                match = True
            else:
                tt = target_tower.upper()
                if tt in (tid, tname) or f"TOWER {tt}" == tname or f"T{tt}" == tid or tt in tname:
                    match = True
            if match:
                old_f = t.get("floors", 12)
                t["floors"] = new_floors
                if (t.get("parking") or {}).get("basement_only"):
                    update_tower_parking(t, updated_project.get("residential_policy"))
                mutations_applied.append(f"Updated {t.get('name', 'Tower')} from {old_f} to {new_floors} floors")
        updated_project["towers"] = towers
        # Same figures the engine reports, so the chat result matches every other page.
        am = engine.area_metrics(updated_project)
        metrics = dict(updated_project.get("achieved_metrics") or {})
        metrics["total_builtup_sqm"] = round(float(am["builtup_area_sqm"]), 1)
        metrics["achieved_far"] = round(float(am["far"]), 2)
        metrics["total_units"] = int(am["total_units"])
        updated_project["achieved_metrics"] = metrics
        if towers and all((t.get("parking") or {}).get("basement_only") for t in towers):
            old_parking = dict(updated_project.get("parking") or {})
            updated_project["parking"] = {
                **old_parking,
                **parking_summary(towers, old_parking.get("basement_levels") or 2),
            }

    # 2. Unit mix adjustment
    mix_match = re.search(r"(?:set|increase|change|make)?\s*([1-4]\s*bhk|penthouse)\s*(?:to|by)?\s*(\d+)%", text)
    if mix_match:
        target_type = mix_match.group(1).replace(" ", "").upper()
        new_pct = float(mix_match.group(2))
        unit_mix = [dict(u) for u in updated_project.get("unit_mix") or []]
        found = False
        for u in unit_mix:
            if target_type in u.get("type", "").replace(" ", "").upper():
                u["share_pct"] = new_pct
                found = True
                mutations_applied.append(f"Set {u['type']} share to {new_pct}%")
        # Normalize others if changed
        if found and len(unit_mix) > 1:
            remaining = 100.0 - new_pct
            others = [u for u in unit_mix if target_type not in u.get("type", "").replace(" ", "").upper()]
            total_other = sum(u.get("share_pct", 0.0) for u in others) or 1.0
            for u in others:
                u["share_pct"] = round((u.get("share_pct", 0.0) / total_other) * remaining, 1)
        updated_project["unit_mix"] = unit_mix

    # Tower-specific unit assignment. Only activate for instructions that describe a
    # single/exclusive type per tower; this keeps global share instructions such as
    # "set Tower A 16 floors and 3BHK share to 60%" from changing a tower's whole mix.
    assignment_intent = bool(re.search(
        r"\b(?:each|every|all)\s+towers?\b.{0,100}\b(?:only|single|specific|exclusive|houses?|consists?|comprises?)\b"
        r"|\b(?:one|single|specific)\s+(?:and\s+only\s+)?unit\s+type\s+per\s+tower\b"
        r"|\btower\s+[a-z0-9]+\b.{0,80}\b(?:only|entirely|exclusively|dedicated)\b",
        text,
    ))
    tower_marks = list(re.finditer(r"\btower\s+([a-z]|\d+)\b", text))
    unit_type_re = re.compile(r"\b(?P<kind>[1-5](?:\.5)?\s*bhk|penthouse|studio)\b", re.I)
    assignments = []
    if assignment_intent:
        for mark_index, mark in enumerate(tower_marks):
            clause_end = tower_marks[mark_index + 1].start() if mark_index + 1 < len(tower_marks) else len(text)
            clause = text[mark.end():clause_end]
            type_match = unit_type_re.search(clause)
            if type_match:
                assignments.append((mark.group(1), type_match.group("kind")))

    repeating_default_intent = assignment_intent and bool(re.search(
        r"\b(?:repeating\s+cycle|pattern\s+restarts?|continuing\s+sequentially)\b", text
    ))
    if repeating_default_intent:
        towers = [dict(t) for t in updated_project.get("towers") or []]
        policy = new_residential_policy()
        for index, tower in enumerate(towers):
            configure_tower(tower, tower_index(tower.get("name"), index), policy=policy)
        if towers:
            updated_project["towers"] = towers
            updated_project["residential_policy"] = policy
            updated_project["unit_mix"] = summarize_units(towers)
            old_parking = dict(updated_project.get("parking") or {})
            updated_project["parking"] = {
                **old_parking,
                **parking_summary(towers, old_parking.get("basement_levels") or 2),
            }
            metrics = dict(updated_project.get("achieved_metrics") or {})
            metrics["total_units"] = sum(
                sum(int(u.get("count") or 0) for u in (tower.get("units") or []))
                * max(1, int(tower.get("floors") or 1)) for tower in towers
            )
            updated_project["achieved_metrics"] = metrics
            mutations_applied.append(
                f"Applied the repeating 1–5 BHK tower pattern, floor densities and underground parking to {len(towers)} tower(s)"
            )
        assignments = []

    if assignments:
        towers = [dict(t) for t in updated_project.get("towers") or []]
        resolved = []
        missing_targets = []

        def find_tower(label: str):
            wanted = label.lower()
            for index, tower in enumerate(towers):
                name = str(tower.get("name") or "").lower()
                tower_id = str(tower.get("id") or "").lower()
                if re.search(rf"\btower\s+{re.escape(wanted)}\b", name):
                    return tower
                if tower_id in {wanted, f"t{wanted}"}:
                    return tower
                if wanted.isalpha() and len(wanted) == 1 and index == ord(wanted) - ord("a"):
                    return tower
                if wanted.isdigit() and re.search(rf"\btower\s+{re.escape(wanted)}\b", name):
                    return tower
            return None

        for label, raw_kind in assignments:
            tower = find_tower(label)
            if tower is None:
                missing_targets.append(f"Tower {label.upper()}")
                continue
            type_key = unit_type_key(raw_kind)
            resolved.append((tower, raw_kind, type_key))

        if missing_targets:
            return {
                "ok": False,
                "instruction": instruction,
                "mutations_applied": [],
                "message": f"Couldn't find {', '.join(missing_targets)} in this project.",
            }

        old_mix = updated_project.get("unit_mix") or []
        policy = updated_project.get("residential_policy") or new_residential_policy()
        all_tower_units = [u for t in towers for u in (t.get("units") or [])]
        default_areas = {
            "studio": (35.0, 3.0), "1bhk": (48.0, 5.0), "2bhk": (72.0, 8.0),
            "2.5bhk": (92.0, 10.0), "3bhk": (120.0, 14.0), "4bhk": (160.0, 20.0),
            "5bhk": (210.0, 28.0), "penthouse": (280.0, 36.0),
        }

        for tower, raw_kind, type_key in resolved:
            if type_key in policy.get("units_per_floor", {}):
                configure_tower(
                    tower,
                    tower_index(tower.get("name"), towers.index(tower)),
                    kind=type_key,
                    policy=policy,
                )
                updated_project["residential_policy"] = policy
                mutations_applied.append(
                    f"Set {tower.get('name') or 'Tower'} to {type_key.upper()} only "
                    f"({tower['units_per_floor']} per floor)"
                )
                continue
            current_units = [dict(u) for u in tower.get("units") or []]
            per_floor_count = sum(max(0, int(float(u.get("count") or 0))) for u in current_units)
            if per_floor_count <= 0:
                per_floor_count = max(1, int(tower.get("units_per_floor") or 4))

            source = next((u for u in current_units if unit_type_key(u.get("type")) == type_key), None)
            if source is None:
                source = next((u for u in old_mix if unit_type_key(u.get("type")) == type_key), None)
            if source is None:
                source = next((u for u in all_tower_units if unit_type_key(u.get("type")) == type_key), None)
            default_carpet, default_balcony = default_areas.get(type_key, (72.0, 8.0))
            carpet_area = float(
                (source or {}).get("carpet_area")
                or (source or {}).get("carpet_area_sqm")
                or default_carpet
            )
            balcony_area = float(
                (source or {}).get("balcony_area")
                or (source or {}).get("balcony_sqm")
                or default_balcony
            )
            display_type = str((source or {}).get("type") or raw_kind.upper().replace(" ", ""))
            tower_id = re.sub(r"[^a-z0-9]+", "-", str(tower.get("id") or tower.get("name") or "tower").lower()).strip("-")
            tower["units"] = [{
                "id": (source or {}).get("id") or f"{tower_id}-{type_key}",
                "type": display_type,
                "count": per_floor_count,
                "carpet_area": carpet_area,
                "balcony_area": balcony_area,
            }]
            tower["units_per_floor"] = per_floor_count
            mutations_applied.append(
                f"Set {tower.get('name') or 'Tower ' + tower_id} to {display_type} only ({per_floor_count} per floor)"
            )

        updated_project["towers"] = towers
        updated_project["unit_mix"] = summarize_units(towers) or updated_project.get("unit_mix")
        old_parking = dict(updated_project.get("parking") or {})
        updated_project["parking"] = {
            **old_parking,
            **parking_summary(towers, old_parking.get("basement_levels") or 2),
        }

        # Keep the project-wide unit mix aligned with the per-tower mixes when every
        # tower has explicit unit rows. Existing labels and area assumptions are retained.
        if towers and all(t.get("units") for t in towers):
            totals: Dict[str, int] = {}
            templates: Dict[str, Dict[str, Any]] = {}
            for item in old_mix:
                templates.setdefault(unit_type_key(item.get("type")), dict(item))
            for tower in towers:
                floors = max(1, int(tower.get("floors") or 1))
                for unit in tower.get("units") or []:
                    key = unit_type_key(unit.get("type"))
                    count = max(0, int(float(unit.get("count") or 0))) * floors
                    if count:
                        totals[key] = totals.get(key, 0) + count
                        if key not in templates:
                            templates[key] = {
                                "type": unit.get("type"),
                                "carpet_area_sqm": unit.get("carpet_area"),
                                "balcony_sqm": unit.get("balcony_area"),
                            }
            total_units = sum(totals.values())
            if total_units:
                mix = []
                for key, count in sorted(totals.items()):
                    item = dict(templates.get(key) or {})
                    item["type"] = item.get("type") or key.upper()
                    item["share_pct"] = round(count * 100.0 / total_units, 1)
                    mix.append(item)
                if mix:
                    mix[-1]["share_pct"] = round(100.0 - sum(u["share_pct"] for u in mix[:-1]), 1)
                updated_project["unit_mix"] = mix

    # 3. Parking mutations
    five_bhk_choice = None
    four_car_option = re.search(
        r"\b(?:4|four)\s+cars?\b.{0,30}\b(?:0|zero|no)\s+(?:two[- ]wheelers?|bikes?|scooters?)\b", text
    )
    three_car_option = re.search(
        r"\b(?:3|three)\s+cars?\b.{0,30}\b(?:2|two)\s+(?:two[- ]wheelers?|bikes?|scooters?)\b", text
    )
    flexible_four_car_option = bool(
        re.search(r"\b(?:4|four)\s+cars?\b", text)
        and re.search(r"\b(?:no|zero|without|don['’]?t\s+make\s+any)\b.{0,35}\b(?:dedicated\s+)?(?:two[- ]wheelers?|bikes?|scooters?)\b", text)
    )
    if flexible_four_car_option:
        four_car_option = four_car_option or re.search(r"\b(?:4|four)\s+cars?\b", text)
    if four_car_option and not three_car_option:
        five_bhk_choice = (4, 0, "4 car spaces, no dedicated bike bays; flexible use", True)
    elif three_car_option and not four_car_option:
        five_bhk_choice = (3, 2, "3 cars + 2 bikes", False)
    elif four_car_option and re.search(r"\b(?:choose|select|use|set|default)\b.{0,50}\b4\s+cars?\b", text):
        five_bhk_choice = (4, 0, "4 cars, no bikes", False)
    elif three_car_option and re.search(r"\b(?:choose|select|use|set|default)\b.{0,50}\b3\s+cars?\b", text):
        five_bhk_choice = (3, 2, "3 cars + 2 bikes", False)
    if five_bhk_choice:
        policy = dict(updated_project.get("residential_policy") or new_residential_policy())
        by_type = {k: dict(v) for k, v in (policy.get("parking_by_type") or {}).items()}
        cars, bikes, label, flexible = five_bhk_choice
        by_type["5bhk"] = {
            "reserved_cars_per_unit": cars,
            "reserved_bikes_per_unit": bikes,
            "optional_car_spaces_per_unit": 0,
            "flexible_space_use": flexible,
        }
        policy["parking_by_type"] = by_type
        policy["five_bhk_parking_option"] = label
        towers = [dict(t) for t in updated_project.get("towers") or []]
        for index, tower in enumerate(towers):
            if any(unit_type_key(u.get("type")) == "5bhk" for u in tower.get("units") or []):
                configure_tower(tower, tower_index(tower.get("name"), index), kind="5bhk", policy=policy)
        updated_project["towers"] = towers
        updated_project["residential_policy"] = policy
        old_parking = dict(updated_project.get("parking") or {})
        updated_project["parking"] = {
            **old_parking,
            **parking_summary(towers, old_parking.get("basement_levels") or 2),
        }
        mutations_applied.append(f"Set 5BHK basement parking to {label}")

    optional_pool_match = re.search(
        r"\b1\s*bhk\b.{0,60}\b(?:optional|purchase)\b.{0,35}\b(\d+(?:\.\d+)?)\s+(?:car\s+)?spaces?\s+per\s+unit\b",
        text,
    )
    if optional_pool_match:
        spaces = float(optional_pool_match.group(1))
        policy = dict(updated_project.get("residential_policy") or new_residential_policy())
        by_type = {k: dict(v) for k, v in (policy.get("parking_by_type") or {}).items()}
        one_bhk = dict(by_type.get("1bhk") or {})
        one_bhk["reserved_cars_per_unit"] = 0
        one_bhk["reserved_bikes_per_unit"] = 0
        one_bhk["optional_car_spaces_per_unit"] = spaces
        by_type["1bhk"] = one_bhk
        policy["parking_by_type"] = by_type
        towers = [dict(t) for t in updated_project.get("towers") or []]
        for index, tower in enumerate(towers):
            if any(unit_type_key(u.get("type")) == "1bhk" for u in tower.get("units") or []):
                configure_tower(tower, tower_index(tower.get("name"), index), kind="1bhk", policy=policy)
        updated_project["towers"] = towers
        updated_project["residential_policy"] = policy
        old_parking = dict(updated_project.get("parking") or {})
        updated_project["parking"] = {
            **old_parking,
            **parking_summary(towers, old_parking.get("basement_levels") or 2),
        }
        mutations_applied.append(f"Set the 1BHK optional parking pool to {spaces:g} spaces per apartment")

    ev_match = re.search(r"(?:add|set|increase)\s*(\d+)\s*(?:ev|electric)(?:\s+charging)?\s*(?:slots?|bays?|points?)", text)
    if ev_match:
        ev_slots = int(ev_match.group(1))
        parking = dict(updated_project.get("parking") or {})
        parking["ev_charging_slots"] = (parking.get("ev_charging_slots") or 0) + ev_slots
        updated_project["parking"] = parking
        mutations_applied.append(f"Added {ev_slots} EV charging slots (total: {parking['ev_charging_slots']})")

    basement_match = re.search(r"(?:add|increase)\s*(?:a\s*)?basement\s*(?:level|tier)?", text)
    if basement_match:
        parking = dict(updated_project.get("parking") or {})
        b_levels = (parking.get("basement_levels") or 1) + 1
        parking["basement_levels"] = b_levels
        updated_project["parking"] = parking
        mutations_applied.append(f"Increased basement levels to {b_levels}")

    # 4. Setback mutations
    setback_match = re.search(r"(?:increase|set)\s*(front|rear|side)\s*setback\s*(?:to)?\s*(\d+(?:\.\d+)?)\s*m", text)
    if setback_match:
        edge = setback_match.group(1)
        val = float(setback_match.group(2))
        dc = dict(updated_project.get("dev_controls") or {})
        sb = dict(dc.get("setbacks") or {})
        if edge == "front":
            sb["front"] = val
        elif edge == "rear":
            sb["rear"] = val
        else:
            sb["side1"] = val
            sb["side2"] = val
        dc["setbacks"] = sb
        updated_project["dev_controls"] = dc
        mutations_applied.append(f"Set {edge} setback to {val}m")

    if not mutations_applied:
        return {
            "ok": False,
            "instruction": instruction,
            "mutations_applied": [],
            "message": (
                "I couldn't match a supported change. Try naming a tower and floor count, "
                "a BHK share percentage, per-tower unit type, EV slots, basement levels, or a setback."
            ),
        }

    return {
        "ok": True,
        "instruction": instruction,
        "mutations_applied": mutations_applied,
        "updated_project": updated_project,
    }


# --------------------------------------------------------------------------- 3. Multi-Agent Engineering Teams

def _project_area_metrics(project: Dict[str, Any]) -> Dict[str, float]:
    """Plot, built-up, footprint and units from the engine -- one set of numbers app-wide."""
    metrics = engine.area_metrics(project)
    return {
        "plot_area": float(metrics.get("plot_area_sqm") or 0.0),
        "builtup": float(metrics.get("builtup_area_sqm") or 0.0),
        "ground_cov": float(metrics.get("ground_footprint_sqm") or 0.0),
        "units_est": int(metrics.get("total_units") or 0),
    }


def _review_inputs(project: Dict[str, Any]):
    import engineering
    a = engine.analyse(project)
    e = engineering.analyse_engineering(project, a)
    return a, e


def _mod_out(module: Dict[str, Any], prefix: str):
    return next((o.get("value") for o in module.get("outputs") or [] if str(o.get("label", "")).startswith(prefix)), None)


def _agent(role, avatar, checks, findings, recommendations, verdict_ok, verdict_bad):
    """An agent's status and score follow from its checks: each failed check costs points."""
    failed = [c for c in checks if not c[1]]
    score = max(0, 100 - 15 * len(failed))
    return {
        "role": role, "avatar": avatar,
        "status": "approved" if not failed else "needs_revision",
        "score": score,
        "verdict": verdict_ok if not failed else verdict_bad + " " + "; ".join(c[0] for c in failed) + ".",
        "findings": findings,
        "recommendations": recommendations + [f"Resolve: {c[0]}" for c in failed],
    }


def multi_agent_review(project: Dict[str, Any]) -> Dict[str, Any]:
    """Five discipline reviews, each one reading the platform's own computed results.

    Every finding is a computed figure and every verdict follows from checks: compliance
    rules, engineering-module warnings and the take-off's sanity bands. Nothing is approved
    by default, and the result is an automated review -- it certifies nothing.
    """
    try:
        a, e = _review_inputs(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    mods = e["modules"]
    ar = a["areas"]
    results = {r["param"]: r for r in a["compliance"]["results"]}

    def rule_ok(param):
        r = results.get(param)
        return r is None or r.get("status") == "pass"

    def warnings_of(*ids, severities=("critical", "warning")):
        return [w for i in ids for w in (mods.get(i, {}).get("warnings") or []) if w.get("severity") in severities]

    def as_checks(ws):
        return [(w.get("text", "").rstrip("."), False) for w in ws]

    far_rule = results.get("far") or {}
    arch = _agent(
        "Chief Architect Agent", "compass",
        [("FAR within the permissible limit", rule_ok("far")), ("Ground coverage within limit", rule_ok("ground_coverage_pct")),
         ("Open space meets the minimum", rule_ok("open_space_pct"))],
        [f"FAR {ar['far']:.2f} against a limit of {far_rule.get('threshold', '-')}",
         f"Ground coverage {ar['ground_coverage_pct']:.1f}%, open space {ar['open_space_pct']:.1f}%",
         f"{len(ar['towers'])} tower(s), tallest {ar['max_height_m']:.1f} m, {ar['total_units']} homes"],
        [], "Massing is within the development controls.", "Massing breaks a development control:")

    seis = mods.get("seismic", {})
    tk = a["quantities"].get("takeoff") or {}
    struct_ws = warnings_of("loads", "seismic", "foundation")
    struct = _agent(
        "Lead Structural Engineer Agent", "ruler",
        as_checks([w for w in struct_ws if w.get("severity") == "critical"]),
        [f"Seismic zone {_mod_out(seis, 'Seismic zone')}, importance factor {_mod_out(seis, 'Importance factor')}",
         f"Design base shear {_mod_out(seis, 'Design base shear')} kN (Ah {_mod_out(seis, 'Design horizontal coefficient')})",
         f"Take-off columns {', '.join(t['column_section_mm'] for t in tk.get('towers', [])) or '-'} mm; "
         f"footings {', '.join(str(t['footing_size_m']) for t in tk.get('towers', [])) or '-'} m square",
         *[w.get("text") for w in struct_ws if w.get("severity") != "critical"]],
        [], "No critical structural warnings at scheme stage.", "Critical structural warnings:")

    water = mods.get("water", {}).get("derived") or {}
    storm = mods.get("storm", {}).get("derived") or {}
    mep_ws = warnings_of("water", "storm", "fire")
    mep = _agent(
        "MEP & Sustainability Director Agent", "zap",
        as_checks([w for w in mep_ws if w.get("severity") == "critical"]),
        [f"Water demand {water.get('total_lpd', 0) / 1000:.1f} KLD for {water.get('persons', 0)} persons (IS 1172)",
         f"STP {water.get('stp_kld', 0)} KLD; rainwater harvesting {storm.get('rwh_annual_l', 0) / 1000:.0f} m³/year",
         *[w.get("text") for w in mep_ws if w.get("severity") != "critical"]],
        [], "Utility demand and services sizing are consistent.", "Critical services warnings:")

    cost = a["cost"]
    vs = tk.get("vs_thumb_rule") or {}
    qs = _agent(
        "Chief Quantity Surveyor & Cost Agent", "calculator",
        [(w.get("text", "").rstrip("."), False) for w in (tk.get("warnings") or [])],
        [f"Estimated project construction cost ₹{cost['total'] / 1e7:,.2f} Cr (₹{cost['per_sqm']:,.0f}/m² built-up)",
         f"Steel {vs.get('steel_kg_per_sqm', '-')} kg/m², concrete {vs.get('concrete_m3_per_sqm', '-')} m³/m² from the structural take-off"],
        [], "Take-off ratios are within their typical bands.", "Take-off outside typical bands:")

    fails = [r for r in a["compliance"]["results"] if r.get("status") == "fail"]
    comp = _agent(
        "Statutory Compliance & Safety Officer Agent", "shield-check",
        [(f"{r['label']}: {r.get('actual')} vs {r.get('threshold')} {r.get('unit', '')}".strip(), False) for r in fails],
        [f"{a['compliance']['passed']} of {a['compliance']['total']} compliance rules pass"],
        [], "All configured compliance rules pass.", "Failing compliance rules:")

    agents = [arch, struct, mep, qs, comp]
    all_ok = all(x["status"] == "approved" for x in agents)
    digest_src = "|".join([str(project.get("_id") or project.get("name") or ""), f"{ar['builtup_area_sqm']:.2f}",
                           f"{cost['total']:.0f}", *[f"{x['role']}:{x['status']}" for x in agents]])
    return {
        "ok": True,
        "team_consensus": "NO BLOCKING ISSUES" if all_ok else "REVISION REQUIRED",
        "overall_engineering_score": round(sum(x["score"] for x in agents) / len(agents), 1),
        "agents": agents,
        "conflicts_detected": [f"{x['role']}: {x['verdict']}" for x in agents if x["status"] != "approved"],
        "sign_off_certificate": {
            "issued_by": "Aptimizer automated review (not a professional certification)",
            "verdict": ("No blocking issues found by the automated checks. A licensed engineer must review and certify "
                        "before any statutory submission." if all_ok else
                        "Automated checks found issues to resolve before statutory submission."),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # Digest of the reviewed figures: it changes whenever the design or the verdict does.
            "certificate_hash": "APT-REVIEW-" + hashlib.sha256(digest_src.encode("utf-8")).hexdigest()[:12].upper(),
        },
    }


# --------------------------------------------------------------------------- 4. Autonomous Compliance Checking

def autonomous_compliance_audit(project: Dict[str, Any]) -> Dict[str, Any]:
    """Every configured compliance rule plus the setback and height-to-road checks.

    The rule results are the engine's own (the same ones the Compliance module shows), so
    the two pages cannot disagree; fire egress travel distance is measured, not assumed.
    """
    try:
        a = engine.analyse(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    plot = project.get("plot") or {}
    ar = a["areas"]
    height_m = float(ar.get("max_height_m") or 0.0)
    road_w = float(plot.get("road_width_m") or (project.get("dev_controls") or {}).get("road_width_m") or 0.0)

    checks = []
    for r in a["compliance"]["results"]:
        ok = r.get("status") == "pass"
        thr, act = r.get("threshold"), r.get("actual")
        margin = None
        if isinstance(thr, (int, float)) and isinstance(act, (int, float)):
            margin = round((thr - act) if r.get("operator") == "max" else (act - thr), 2)
        # The FAR check keeps the id API consumers already key on.
        cid = "far_check" if r.get("param") == "far" else r.get("id")
        checks.append({"id": cid, "rule": r.get("label"), "clause": r.get("code"),
                       "permissible": thr, "achieved": round(act, 2) if isinstance(act, float) else act,
                       "unit": r.get("unit"), "status": "pass" if ok else "fail", "margin": margin,
                       "remediation": None if ok else r.get("message")})

    sb_rules = setback_minimums(ar["plot_area_sqm"], road_width=road_w or 12.0, height_m=height_m)
    sb = (project.get("dev_controls") or {}).get("setbacks") or {}

    def minimum(key, default):
        v = sb_rules.get(key)
        return float(v.get("minimum_m", default)) if isinstance(v, dict) else float(v or default)

    def provided(key):
        v = sb.get(key)
        if isinstance(v, dict):
            v = v.get("minimum_m")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    for key, label in (("front", "Front Setback Clearance"), ("rear", "Rear Setback Clearance")):
        need, have = minimum(key, 0.0), provided(key)
        if have is None:
            checks.append({"id": f"setback_{key}", "rule": label, "clause": "NBC 2016 Part 3", "permissible": need,
                           "achieved": None, "unit": "metres", "status": "fail", "margin": None,
                           "remediation": f"Set the {key} setback in Development Controls (minimum {need} m)."})
        else:
            ok = have >= need - 1e-9
            checks.append({"id": f"setback_{key}", "rule": label, "clause": "NBC 2016 Part 3", "permissible": need,
                           "achieved": have, "unit": "metres", "status": "pass" if ok else "fail",
                           "margin": round(have - need, 2),
                           "remediation": None if ok else f"Increase the {key} setback to at least {need} m."})
    if road_w > 0:
        front = provided("front") or 0.0
        limit = 1.5 * (road_w + front)
        ok = height_m <= limit + 1e-9
        checks.append({"id": "height_road_check", "rule": "Building Height vs Road Width", "clause": "NBC 2016 Part 3",
                       "permissible": round(limit, 1), "achieved": round(height_m, 1), "unit": "metres",
                       "status": "pass" if ok else "fail", "margin": round(limit - height_m, 1),
                       "remediation": None if ok else "Increase the front setback or reduce the tower height."})

    passed_count = sum(1 for c in checks if c["status"] == "pass")
    return {
        "ok": True,
        "score_pct": round(passed_count / len(checks) * 100.0, 1) if checks else 0.0,
        "total_checks": len(checks),
        "passed": passed_count,
        "failed": len(checks) - passed_count,
        "checks": checks,
        "auto_remediations_available": [c["remediation"] for c in checks if c["remediation"]],
    }


# --------------------------------------------------------------------------- 5. Autonomous BOQ & Costing

def autonomous_boq_engine(project: Dict[str, Any]) -> Dict[str, Any]:
    """The platform's BOQ (structural take-off + rates + adders), presented item by item.

    It used to price its own per-m² thumb rules, which disagreed with the BOQ module for the
    same project. It now IS the BOQ module's bill.
    """
    try:
        a = engine.analyse(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    b = a["boq"]
    builtup = float(a["areas"]["builtup_area_sqm"] or 0)
    items = [{"item": m["label"], "quantity": round(float(m.get("quantity_ordered", m["quantity"])), 1),
              "unit": m["unit"], "rate_inr": m["rate"], "amount_inr": round(m["amount"])}
             for m in b["materials"]]
    items += [{"item": f"Labour - {l['label']}", "quantity": round(l["quantity"], 1), "unit": l["unit"],
               "rate_inr": l["rate"], "amount_inr": round(l["amount"])} for l in b["labour"]]
    items += [{"item": f"Equipment - {q['label']}", "quantity": round(q["quantity"], 1), "unit": q["unit"],
               "rate_inr": q["rate"], "amount_inr": round(q["amount"])} for q in b["equipment"]]
    mep = sum(m["amount"] for m in b["materials"] if m["key"] in ("plumbing_fixtures", "electrical_points"))
    adders = {x["key"]: x["amount"] for x in b["adders"]}
    return {
        "ok": True,
        "data_source": "BOQ module",
        "builtup_area_sqm": round(builtup, 1),
        "items": items,
        "summary": {
            "civil_works_cost_inr": round(b["works_total"] - mep),
            "mep_services_cost_inr": round(mep),
            "preliminaries_inr": round(adders.get("preliminaries_pct", 0)),
            "contingency_inr": round(adders.get("contingency_pct", 0)),
            "total_project_cost_inr": round(b["grand_total"]),
            "cost_per_sqm_inr": round(b["cost_per_sqm"], 1),
        },
        "variance_vs_benchmark": {
            "benchmark_per_sqm": (project.get("config") or {}).get("cost_benchmark_per_sqm"),
            "variance_pct": None,
            "status": "Same bill as the BOQ module; set config.cost_benchmark_per_sqm to compare against a benchmark",
        },
    }


# --------------------------------------------------------------------------- 6. AI Township & Mixed-Use Planner

# Planning-stage assumptions, stated rather than buried in the numbers.
TOWNSHIP_TOWER_GFA_SQM = 700.0 * 14       # a typical 700 m² plate on 14 floors
TOWNSHIP_PERSONS_PER_HOME = 4.5
TOWNSHIP_DEFAULT_HOME_SQM = 110.0         # built-up per home when the project has none yet


def township_mixed_use_plan(project: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Land-use split for a township, with homes and towers derived from the split.

    The shares are planning-norm assumptions (URDPFI-style). Dwelling numbers follow from
    residential land x FSI / built-up per home, using this project's own home size when it
    has one; they used to be fixed at 800 whatever the land area.
    """
    am = _project_area_metrics(project)
    total_area_sqm = float(params.get("township_area_sqm") or am["plot_area"] or 50000.0)
    is_mixed_use = bool(params.get("is_mixed_use", True))
    home_sqm = (am["builtup"] / am["units_est"]) if am["units_est"] else TOWNSHIP_DEFAULT_HOME_SQM

    if is_mixed_use:
        zoning = [
            {"zone": "Residential Sectors (Towers)", "share_pct": 52.0, "permissible_fsi": 2.75},
            {"zone": "Commercial High-Street & Retail Podium", "share_pct": 18.0, "permissible_fsi": 3.25},
            {"zone": "Central Park & Recreational Spine", "share_pct": 15.0, "permissible_fsi": 0.05},
            {"zone": "Civic & Community Infrastructure (School, Clinic)", "share_pct": 8.0, "permissible_fsi": 1.50},
            {"zone": "Internal Arterial & Spine Road Network", "share_pct": 7.0, "permissible_fsi": 0.0},
        ]
    else:
        zoning = [
            {"zone": "Residential Sectors (Phased Clusters)", "share_pct": 60.0, "permissible_fsi": 2.50},
            {"zone": "Neighbourhood Retail & Convenience", "share_pct": 8.0, "permissible_fsi": 2.00},
            {"zone": "Parks, Playgrounds & Green Buffers", "share_pct": 18.0, "permissible_fsi": 0.0},
            {"zone": "Civic Amenities & Clubhouses", "share_pct": 6.0, "permissible_fsi": 1.20},
            {"zone": "Primary & Secondary Road Hierarchy", "share_pct": 8.0, "permissible_fsi": 0.0},
        ]
    for z in zoning:
        z["area_sqm"] = round(total_area_sqm * z["share_pct"] / 100.0)

    res, com, green, civic = zoning[0], zoning[1], zoning[2], zoning[3]
    res_builtup = res["area_sqm"] * res["permissible_fsi"]
    homes = int(res_builtup / home_sqm) if home_sqm else 0
    commercial_gla = com["area_sqm"] * com["permissible_fsi"]
    # Two residential sectors share the residential land 54:46, as the sector plan draws it.
    split = (0.54, 0.46)
    sectors = []
    for i, (sid, name) in enumerate((("SEC-A", "Sector 1: Residential Enclave"), ("SEC-B", "Sector 2: Urban Living"))):
        area = res["area_sqm"] * split[i]
        gfa = area * res["permissible_fsi"]
        sectors.append({"sector_id": sid, "name": name, "area_sqm": round(area),
                        "units": int(gfa / home_sqm) if home_sqm else 0,
                        "towers": max(1, math.ceil(gfa / TOWNSHIP_TOWER_GFA_SQM))})
    sectors.append({"sector_id": "SEC-C", "name": "Sector 3: Commercial & Offices", "area_sqm": com["area_sqm"],
                    "gla_sqm": round(commercial_gla), "towers": max(1, math.ceil(commercial_gla / TOWNSHIP_TOWER_GFA_SQM))})
    sectors.append({"sector_id": "SEC-D", "name": "Sector 4: Commons & Civic Hub",
                    "area_sqm": round(green["area_sqm"] + civic["area_sqm"]),
                    "amenities": ["School", "Primary Health Centre", "Clubhouse", "Sports Court"]})

    total_potential_builtup = sum(z["area_sqm"] * z["permissible_fsi"] for z in zoning)
    return {
        "ok": True,
        "township_area_sqm": total_area_sqm,
        "is_mixed_use": is_mixed_use,
        "zoning_distribution": zoning,
        "sectors": sectors,
        "assumptions": {"built_up_per_home_sqm": round(home_sqm, 1),
                        "home_size_source": "this project" if am["units_est"] else "planning default",
                        "tower_gfa_sqm": TOWNSHIP_TOWER_GFA_SQM, "persons_per_home": TOWNSHIP_PERSONS_PER_HOME},
        "master_plan_metrics": {
            "total_potential_builtup_sqm": round(total_potential_builtup, 1),
            "blended_far": round(total_potential_builtup / total_area_sqm, 2) if total_area_sqm else 0.0,
            "estimated_dwelling_units": homes,
            "estimated_population": int(homes * TOWNSHIP_PERSONS_PER_HOME),
            "commercial_leasable_sqm": round(commercial_gla, 0),
            "open_space_ratio_pct": green["share_pct"],
        },
        "circulation_strategy": {
            "segregation": "Pedestrian network separated from service and vehicular loops.",
            "access_points": "Separate residential, commercial/visitor and emergency/service gates.",
            "internal_road_width_m": 12.0,
        },
    }
