"""Compact, human-readable residential unit layouts.

This is deliberately a deterministic layout engine, not a language-model drawing.  It
creates one front door, an entry foyer that opens into the living room, and a separate
private passage for bedrooms.  Coordinates are returned in the legacy metre payload so
the existing plan renderer and storage format continue to work.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple


def _programme(unit_type: str) -> int:
    text = (unit_type or "2bhk").lower().replace(" ", "")
    if "penthouse" in text:
        return 5
    for beds in (5, 4, 3, 2, 1):
        if f"{beds}bhk" in text:
            return beds
    return 2


def _rect(x: float, y: float, w: float, h: float) -> Dict[str, float]:
    return {"x": round(x, 2), "y": round(y, 2), "w": round(w, 2), "h": round(h, 2)}


def _orient(rect: Dict[str, float], width: float, height: float, entry_edge: str) -> Dict[str, float]:
    """Rotate the canonical south-entry plan without changing room proportions."""
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    if entry_edge == "S":
        return _rect(x, y, w, h)
    if entry_edge == "N":
        return _rect(x, height - y - h, w, h)
    if entry_edge == "E":
        return _rect(height - y - h, x, h, w)
    # West entry: a quarter turn in the other direction.
    return _rect(y, width - x - w, h, w)


def generate_unit(box: Dict[str, float], unit_type: str, carpet: float, entry_edge: str,
                  uid: str, unit_index: int, exterior_edges: Sequence[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Generate a practical unit layout that tiles ``box``.

    The room sequence is intentionally fixed: external main door -> foyer -> living;
    bedrooms are reached from a private hallway and bathrooms are never represented as
    extra entrances.  Larger programmes keep their additional rooms in the same zoned
    pattern rather than squeezing more full-depth columns into the envelope.
    """
    beds = _programme(unit_type)
    width, height = float(box["w"]), float(box["h"])
    canonical = entry_edge in ("S", "N")
    plan_w, plan_h = (width, height) if canonical else (height, width)
    x0, y0 = (float(box["x"]), float(box["y"])) if canonical else (float(box["y"]), float(box["x"]))

    # A unit with less depth than this cannot hold a foyer, living room and private hall
    # without making at least one habitable room a corridor-shaped rectangle.  The caller
    # keeps the existing packer for those envelopes and reports the constraint upstream.
    if plan_w < 9.0 or plan_h < 8.0:
        return [], ["Envelope is too constrained for the realistic unit planner; use a wider/deeper unit envelope."]

    north_h = min(3.4, plan_h * 0.30)
    hall_h = round(max(0.95, min(1.30, plan_h * 0.08)), 2)
    entry_h = round(max(1.85, min(2.60, plan_h * 0.14)), 2)
    usable_depth = round(plan_h - hall_h - entry_h, 2)
    if usable_depth < 4.0:
        return [], ["Envelope leaves insufficient depth after required circulation."]

    # Allocate depth proportionally between private bedrooms and public/social living zone
    if usable_depth >= 11.0:
        north_h = round(min(7.2, max(5.0, usable_depth * 0.42)), 2)
    else:
        north_h = round(max(2.85, min(5.0, usable_depth * 0.50)), 2)
    middle_h = round(usable_depth - north_h, 2)

    rooms: List[Dict[str, Any]] = []

    def emit(key: str, name: str, room_type: str, rect: Dict[str, float], **extra: Any) -> Dict[str, Any]:
        placed = _orient(rect, plan_w, plan_h, entry_edge)
        # _orient produces local coordinates. Move its origin back to the unit location.
        placed["x"] = round(placed["x"] + x0, 2)
        placed["y"] = round(placed["y"] + y0, 2)
        room: Dict[str, Any] = {
            "id": f"{uid}-{key}", "name": name, "type": room_type,
            "unit_id": uid, "unit_type": unit_type, "unit_index": unit_index,
            **placed, **extra,
        }
        rooms.append(room)
        return room

    # Private bedroom suites along quiet facade (y = 0 to north_h)
    balcony_h = (
        1.6 if north_h >= 5.5
        else (1.3 if north_h >= 4.2
              else (0.9 if (beds >= 2 and north_h >= 3.4) else 0.0))
    )
    bed_h = round(north_h - balcony_h, 2)
    suite_w = round(plan_w / beds, 2)

    for index in range(beds):
        key = "mbed" if index == 0 else f"bed{index + 1}"
        bath_key = "mbath" if index == 0 else f"bath{index + 1}"
        bath_w = round(min(max(1.5, suite_w * 0.28), 2.8), 2)
        bed_w = round(suite_w - bath_w, 2)
        x = round(index * suite_w, 2)
        bed_name = "Master Bedroom" if index == 0 else f"Bedroom {index + 1}"
        bath_name = "Master Ensuite Bath" if index == 0 else f"Bedroom {index + 1} Ensuite Bath"

        if balcony_h > 0 and (index == 0 or beds >= 3 or (beds == 2 and index == 1)):
            balcony_key = "mbalcony" if index == 0 else f"balcony{index + 1}"
            balcony_name = "Wrap-around Terrace" if beds >= 5 else f"{bed_name} Balcony"
            balcony_type = "terrace" if beds >= 5 else "balcony"
            emit(balcony_key, balcony_name, balcony_type, _rect(x, 0, bed_w, balcony_h),
                 has_window=True, projects_from=f"{uid}-{key}")
            emit(key, bed_name, "bedroom", _rect(x, balcony_h, bed_w, bed_h),
                 has_window=True, door_to="passage",
                 **({"headboard": "South or West wall"} if index == 0 else {}))
        else:
            emit(key, bed_name, "bedroom", _rect(x, 0, bed_w, north_h),
                 has_window=True, door_to="passage",
                 **({"headboard": "South or West wall"} if index == 0 else {}))

        emit(bath_key, bath_name, "bathroom", _rect(x + bed_w, 0, bath_w, north_h),
             has_window=False, door_to=key, door_child_of=key)

    hall_y = north_h
    emit("passage", "Private Passage", "passage", _rect(0, hall_y, plan_w, hall_h),
         door_to="living")

    middle_y = round(hall_y + hall_h, 2)

    # Social and Living Zone:
    # If middle_h is deep (>= 5.5m in large/luxury homes), divide into Upper Social (Family Lounge / Office)
    # and Lower Social (Living / Dining / Kitchen) so no single room becomes excessively oversized.
    if middle_h >= 5.5:
        upper_h = round(middle_h * 0.44, 2)
        social_h = round(middle_h - upper_h, 2)

        # Upper tier: Family Lounge + Home Office / Study
        family_w = round(min(max(4.5, plan_w * 0.52), 12.0), 2)
        office_w = round(plan_w - family_w, 2)
        emit("family", "Family Lounge", "living", _rect(0, middle_y, family_w, upper_h),
             has_window=True, door_to="passage")
        emit("office", "Dedicated Home Office", "office", _rect(family_w, middle_y, office_w, upper_h),
             has_window=True, door_to="family")

        social_y = round(middle_y + upper_h, 2)
    else:
        social_h = middle_h
        social_y = middle_y

    kitchen_w = round(max(2.6, min(5.5, plan_w * 0.22)), 2)
    non_kitchen_w = round(plan_w - kitchen_w, 2)
    if beds >= 2:
        living_w = round(min(max(3.8, non_kitchen_w * 0.52), 9.0), 2)
        dining_w = round(min(max(2.5, non_kitchen_w * 0.32), 6.0), 2)
        flexible_w = round(non_kitchen_w - living_w - dining_w, 2)
    else:
        living_w = non_kitchen_w
        dining_w = 0.0
        flexible_w = 0.0

    emit("living", "Living Room", "living", _rect(0, social_y, living_w, social_h),
         has_window=True, door_to="foyer")

    if dining_w > 0:
        emit("dining", "Dining Area", "dining", _rect(living_w, social_y, dining_w, social_h),
             has_window=True, door_to="living")

    if flexible_w > 0.5 and middle_h < 5.5:
        extra_name = "Family Lounge" if beds >= 4 else "Family / Living Nook"
        emit("family", extra_name, "family", _rect(living_w + dining_w, social_y, flexible_w, social_h),
             has_window=True, door_to="dining" if dining_w else "living")
    elif flexible_w > 0.5:
        # Extra pantry / breakfast space beside dining in large units
        emit("pantry", "Pantry & Breakfast Nook", "kitchen",
             _rect(living_w + dining_w, social_y, flexible_w, social_h),
             has_window=True, door_to="kitchen")

    emit("kitchen", "Kitchen", "kitchen", _rect(plan_w - kitchen_w, social_y, kitchen_w, social_h),
         has_window=True, door_to="dining" if dining_w else "living", hob_faces="East")

    entry_y = round(middle_y + middle_h, 2)
    foyer_w = round(min(3.8, max(2.4, living_w * 0.42)), 2)
    foyer_x = round(max(0.0, (living_w - foyer_w) / 2.0), 2)
    left_w = foyer_x
    right_x = round(foyer_x + foyer_w, 2)
    right_w = round(plan_w - right_x, 2)

    # Left service strip (Shaft + Pooja + Entry Passage)
    if left_w >= 2.45:
        shaft_w = 0.85
        pooja_w = round(min(2.2, max(1.5, (left_w - shaft_w) * 0.6)), 2)
        emit("shaft", "MEP Duct Shaft", "shaft", _rect(0, entry_y, shaft_w, entry_h),
             has_window=False)
        emit("pooja", "Pooja Room", "pooja", _rect(shaft_w, entry_y, pooja_w, entry_h),
             door_to="living", faces="East")
        rem_left = round(left_w - shaft_w - pooja_w, 2)
        if rem_left > 0.4:
            emit("entrypassage_w", "Entry Passage", "passage",
                 _rect(shaft_w + pooja_w, entry_y, rem_left, entry_h), door_to="foyer")
    elif left_w > 0:
        shaft_w = round(min(0.85, left_w), 2)
        pooja_w = round(left_w - shaft_w, 2)
        emit("shaft", "MEP Duct Shaft", "shaft", _rect(0, entry_y, shaft_w, entry_h),
             has_window=False)
        if pooja_w > 0.4:
            emit("pooja", "Pooja Room", "pooja", _rect(shaft_w, entry_y, pooja_w, entry_h),
                 door_to="living", faces="East")

    # Central Entrance Foyer
    emit("foyer", "Entrance Foyer", "entrance", _rect(foyer_x, entry_y, foyer_w, entry_h),
         door_to="living", main_entrance=True, entry_edge=entry_edge)

    # Right service strip: Utility (directly under Kitchen) + optional Powder Room / Gallery
    if right_w > 0:
        util_w = round(min(kitchen_w, max(2.4, min(4.0, right_w * 0.45))), 2)
        util_x = round(plan_w - util_w, 2)
        emit("utility", "Utility / Wash Area", "utility", _rect(util_x, entry_y, util_w, entry_h),
             door_to="kitchen", has_window=False)
        rem_right = round(util_x - right_x, 2)
        if rem_right >= 2.0:
            powder_w = round(min(2.2, max(1.5, rem_right * 0.5)), 2)
            emit("powder", "Guest Powder Room", "bathroom", _rect(right_x, entry_y, powder_w, entry_h),
                 door_to="foyer", has_window=False)
            if rem_right - powder_w > 0.4:
                emit("entrypassage_e", "Entry Gallery", "passage",
                     _rect(right_x + powder_w, entry_y, round(rem_right - powder_w, 2), entry_h),
                     door_to="foyer")
        elif rem_right > 0.4:
            emit("entrypassage_e", "Entry Gallery", "passage",
                 _rect(right_x, entry_y, rem_right, entry_h), door_to="foyer")

    return rooms, [
        "One main entrance is placed on the unit entry wall.",
        "Arrival sequence is main entrance -> foyer -> living and dining.",
        "Bedroom and bathroom doors are served by the private passage; they do not open into the living room.",
        "Living and social areas are balanced with realistic human-scale dimensions and functional zones.",
    ]
