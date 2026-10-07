"""Realistic apartment planner: the Indian slab-block flat, sized from a room brief.

The previous planners filled a unit envelope by stretching rooms: the Vastu packer cut it
into full-depth columns, so a large flat came out as 3 m x 17 m "bedrooms" and a 50 m²
pooja room. This planner works the other way round. Every room has a target size from the
brief for its BHK tier, scaled to the flat's carpet area within limits, and the flat's
envelope is whatever those rooms add up to. Nothing is stretched to fill space.

The arrangement is the one Indian residential slabs actually use (canonical frame: facade
at y = 0, the corridor/entrance wall at y = H, x running along the facade):

    facade  |UTIL |        | balcony |balcony |balcony |          |   habitable rooms on air
    row R1  |-----|KITCHEN | LIVING  |MASTER  |BED 2   |(SERVANT) |   (balconies cut from
            |STORE|        |         |        |        |          |    the front)
    row R2  |BREAKFAST     |DIN |POO |LOB|ENS |LOB|BTH |LOB|S.BTH |   dining, pooja, lobbies,
    row R3  |FOYER |          PASSAGE          |POWDER|SHAFT      |   wet core; entrance and
              ^ main door on the corridor wall                         circulation

Each choice answers a rule of the planning guide rather than a habit:
  * Every habitable room -- kitchen and servant room included -- sits on the facade.
  * The utility is a dry balcony on the facade beside the kitchen it serves.
  * The pooja room takes its door off the living room and shares no wall with a bathroom
    (the bedroom lobbies, not the baths, face it).
  * Bedrooms open onto their own lobby off the private passage, never the living room;
    en-suites are entered only from their bedroom; common baths open onto the passage.
  * Wet services (baths, powder room, shaft) stack at the bedroom end, away from the
    kitchen-end corner that becomes NE when the flat faces south -- the guide forbids wet
    and service rooms there.
  * The rows tile the envelope exactly, so there is no unbuilt plan.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple

import vastu

# ---------------------------------------------------------------- the room brief (k = 1)
KITCHEN_W = {1: 2.5, 2: 2.6, 3: 2.8, 4: 3.0, 5: 3.2}
LIVING_W = {1: 3.9, 2: 4.6, 3: 5.4, 4: 6.0, 5: 6.6}
MASTER_W = {1: 3.6, 2: 3.8, 3: 4.0, 4: 4.2, 5: 4.5}
BED_W = {1: 3.3, 2: 3.4, 3: 3.4, 4: 3.5, 5: 3.6}
FAMILY_W = 4.4
OFFICE_W = 3.3
SERVANT_COL_W = 2.7
FACADE_ROW = {1: 5.0, 2: 5.4, 3: 5.7, 4: 6.0, 5: 6.3}     # R1 depth incl. balconies
SERVICE_ROW = {1: 2.4, 2: 2.4, 3: 2.6, 4: 2.7, 5: 2.8}    # R2: lobbies, baths, dining, pooja
ENTRY_ROW = {1: 1.5, 2: 1.5, 3: 1.6, 4: 1.6, 5: 1.7}      # R3: foyer, passage, wet stack

LIVING_BALCONY = 1.8        # guide: N/E living balcony 1.8-2.4 m
MASTER_BALCONY = 1.2        # guide: exactly 1.2 m
SECONDARY_BALCONY = 1.2     # guide: 1.2-1.5 m
UTILITY_W = 1.35            # guide: 1.2-1.5 m
UTILITY_MIN_D = 2.4
SHAFT_W = 0.9
POWDER_W = 1.8
POOJA_W = {1: 1.1, 2: 1.4, 3: 1.5, 4: 1.6, 5: 1.8}
BATH_SLOT_W = 2.1           # en-suite / common bath width behind a bedroom
MIN_LOBBY_W = 1.05          # a bedroom door needs a lobby at least a door-and-frame wide
SERVANT_LOBBY_W = 1.2
MAX_STORE_RATIO = 3.1       # a store deeper than this x its width reads as a strip

GROSS_PER_CARPET = 1.22     # envelope (incl. walls, circulation, balconies) per m² carpet
SCALE_LIMITS = (0.85, 1.35)


def _tier(unit_type: str, carpet: float) -> Tuple[int, bool, Dict[str, Any]]:
    prog = vastu.unit_programme(unit_type, carpet)
    beds = max(1, min(int(prog["beds"]), 5))
    return beds, bool(prog["is_penthouse"]), prog


def _layout_widths(beds: int, penthouse: bool, f: float) -> Dict[str, Any]:
    big = beds >= 5 or penthouse
    tier = min(beds, 5)
    servant = bool(vastu.SCALING_PROTOCOL[tier].get("servant"))
    return {
        "utility": UTILITY_W,
        "kitchen": max(round(KITCHEN_W[tier] * f, 2), 2.4),
        # Wide enough for the pooja room and a 2.6 m dining area behind it.
        "living": max(round(LIVING_W[tier] * f, 2), POOJA_W[tier] + 2.6),
        "family": round(FAMILY_W * f, 2) if big else 0.0,
        "office": round(OFFICE_W * f, 2) if big else 0.0,
        "beds": [round((MASTER_W[tier] if i == 0 else BED_W[tier]) * f, 2) for i in range(beds)],
        "servant": SERVANT_COL_W if servant else 0.0,
    }


def _natural_size(beds: int, penthouse: bool, f: float) -> Tuple[float, float, Dict[str, Any]]:
    cols = _layout_widths(beds, penthouse, f)
    tier = min(beds, 5)
    # At least a 3.6 m deep living room behind its 1.8 m balcony.
    d1 = max(round(FACADE_ROW[tier] * min(f, 1.2), 2), LIVING_BALCONY + 3.6)
    width = (cols["utility"] + cols["kitchen"] + cols["living"] + cols["family"] + cols["office"]
             + sum(cols["beds"]) + cols["servant"])
    depth = d1 + SERVICE_ROW[tier] + ENTRY_ROW[tier]
    return round(width, 2), round(depth, 2), {**cols, "d1": d1}


def scale_for(unit_type: str, carpet: float) -> float:
    beds, penthouse, _ = _tier(unit_type, carpet)
    w0, h0, _ = _natural_size(beds, penthouse, 1.0)
    target = max(float(carpet or 0), 20.0) * GROSS_PER_CARPET
    f = math.sqrt(target / (w0 * h0))
    return max(SCALE_LIMITS[0], min(SCALE_LIMITS[1], f))


def envelope(unit_type: str, carpet: float) -> Tuple[float, float]:
    """(width along the corridor, depth) of the flat this brief produces."""
    beds, penthouse, _ = _tier(unit_type, carpet)
    w, h, _ = _natural_size(beds, penthouse, scale_for(unit_type, carpet))
    return w, h


def _rect(x: float, y: float, w: float, h: float) -> Dict[str, float]:
    return {"x": round(x, 3), "y": round(y, 3), "w": round(w, 3), "h": round(h, 3)}


def _place(rect: Dict[str, float], plan_w: float, plan_h: float, entry_edge: str, mirror: bool) -> Dict[str, float]:
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    if mirror:
        x = plan_w - x - w
    if entry_edge == "S":          # facade north, door south: canonical
        return _rect(x, y, w, h)
    if entry_edge == "N":          # facade south: flip front to back
        return _rect(x, plan_h - y - h, w, h)
    if entry_edge == "E":          # facade west, door east
        return _rect(y, plan_w - x - w, h, w)
    return _rect(plan_h - y - h, x, h, w)   # entry W: facade east


def plan_unit(box: Dict[str, float], unit_type: str, carpet: float, entry_edge: str,
              uid: str, unit_index: int, exterior_edges: Sequence[str],
              mirror: bool = False, master_last: bool = False, split: int = 0,
              servant_first: bool = False) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Plan one flat inside `box` (normally sized by `envelope`). Returns (rooms, notes).

    `mirror` flips the plan end for end; `master_last` puts the master bedroom at the far
    end of its wing; `split` puts that many bedrooms before the public core; `servant_first`
    moves the servant column to the other end. `variants()` enumerates the combinations and
    the caller keeps whichever best meets the guide's zones for this orientation.
    """
    beds, penthouse, prog = _tier(unit_type, carpet)
    tier = min(beds, 5)
    big = beds >= 5 or penthouse
    servant = bool(prog.get("servant"))
    f = scale_for(unit_type, carpet)
    nat_w, nat_h, cols = _natural_size(beds, penthouse, f)

    canonical = entry_edge in ("S", "N")
    plan_w, plan_h = (float(box["w"]), float(box["h"])) if canonical else (float(box["h"]), float(box["w"]))
    x0, y0 = float(box["x"]), float(box["y"])
    if plan_w + 0.05 < nat_w or plan_h + 0.05 < nat_h * 0.98:
        return [], [f"Envelope {plan_w:.1f} x {plan_h:.1f} m is smaller than the {nat_w:.1f} x {nat_h:.1f} m "
                    "the room brief needs."]

    # Any extra envelope (a caller that sized the box differently) goes to the living room
    # and the facade row, never to a strip of leftover space.
    cols["living"] = round(cols["living"] + (plan_w - nat_w), 3)
    d1 = round(cols["d1"] + (plan_h - nat_h), 3)
    d2, d3 = SERVICE_ROW[tier], ENTRY_ROW[tier]
    y2, y3 = d1, d1 + d2

    rooms: List[Dict[str, Any]] = []

    def emit(key: str, name: str, rtype: str, x: float, y: float, w: float, h: float, **extra: Any):
        if w < 0.3 or h < 0.3:
            return None
        placed = _place(_rect(x, y, w, h), plan_w, plan_h, entry_edge, mirror)
        placed["x"] = round(placed["x"] + x0, 2)
        placed["y"] = round(placed["y"] + y0, 2)
        placed["w"] = round(placed["w"], 2)
        placed["h"] = round(placed["h"], 2)
        room = {"id": f"{uid}-{key}", "name": name, "type": rtype, "unit_id": uid,
                "unit_type": unit_type, "unit_index": unit_index, **placed, **extra}
        rooms.append(room)
        return room

    terrace = "Terrace" if penthouse else "Balcony"
    uw, kw, lw = cols["utility"], cols["kitchen"], cols["living"]


    # ------------------------------------------------------------ column sequence
    # Left to right along the facade. The public core is [utility | kitchen | living |
    # lounge | office]; bedrooms go after it, or `split` of them before it so the core sits
    # in the middle of a wide flat (the guide keeps bedrooms out of the central zone); the
    # servant column goes at either end.
    order = list(range(len(cols["beds"])))
    if master_last:
        order = order[1:] + order[:1]
    split = max(0, min(int(split), len(order) - 1)) if len(order) > 1 else 0
    seq: List[Tuple[str, Any]] = []
    if servant and servant_first:
        seq.append(("servant", None))
    seq += [("bed", i) for i in order[:split]]
    seq += [("utility", None), ("kitchen", None), ("living", None)]
    if cols["family"]:
        seq.append(("family", None))
    if cols["office"]:
        seq.append(("office", None))
    seq += [("bed", i) for i in order[split:]]
    if servant and not servant_first:
        seq.append(("servant", None))

    width_of = {"utility": uw, "kitchen": kw, "living": lw, "family": cols["family"],
                "office": cols["office"], "servant": cols["servant"]}
    xs: Dict[Tuple[str, Any], float] = {}
    x = 0.0
    for col in seq:
        xs[col] = x
        x += cols["beds"][col[1]] if col[0] == "bed" else width_of[col[0]]
    plan_end = x
    x_util, x_living = xs[("utility", None)], xs[("living", None)]
    x_office = xs.get(("office", None))
    x_servant = xs.get(("servant", None))

    # ------------------------------------------------------------ R1: the facade row
    # Utility: a dry balcony on the facade beside the kitchen; the store or pantry behind it.
    ud = round(max(UTILITY_MIN_D, d1 - MAX_STORE_RATIO * uw), 3)
    emit("utility", "Utility / Dry Balcony", "utility", x_util, 0, uw, ud, door_to="kitchen", has_window=True)
    if big:
        emit("pantry", "Pantry & Dry Store", "pantry", x_util, ud, uw, d1 - ud, door_to="kitchen")
    else:
        emit("store", "Kitchen Store", "storage", x_util, ud, uw, d1 - ud, door_to="kitchen")
    emit("kitchen", "Kitchen", "kitchen", x_util + uw, 0, kw, d1, has_window=True, door_to="breakfast",
         hob_faces="East")
    emit("lbalcony", f"Living {terrace}", "terrace" if penthouse else "balcony", x_living, 0, lw, LIVING_BALCONY,
         has_window=True, projects_from=f"{uid}-living")
    emit("living", "Living Room", "living", x_living, LIVING_BALCONY, lw, d1 - LIVING_BALCONY,
         has_window=True, door_to="dining")
    if cols["family"]:
        fx, fw = xs[("family", None)], cols["family"]
        emit("fbalcony", f"Family Lounge {terrace}", "terrace" if penthouse else "balcony", fx, 0, fw,
             LIVING_BALCONY, has_window=True, projects_from=f"{uid}-family")
        # The lounge takes its column through the service row too, so dining stays a
        # room-shaped space behind the living room instead of a long strip.
        emit("family", "Family Lounge", "living", fx, LIVING_BALCONY, fw, d1 + d2 - LIVING_BALCONY,
             has_window=True, door_to="dining")
    if x_office is not None:
        ow = cols["office"]
        emit("obalcony", "Office Balcony", "balcony", x_office, 0, ow, SECONDARY_BALCONY,
             has_window=True, projects_from=f"{uid}-office")
        emit("office", "Home Office", "office", x_office, SECONDARY_BALCONY, ow, d1 - SECONDARY_BALCONY,
             has_window=True, door_to="passage")
    bed_xs = []
    for i in order:
        bx, bw = xs[("bed", i)], cols["beds"][i]
        key = "mbed" if i == 0 else f"bed{i + 1}"
        name = "Master Bedroom" if i == 0 else f"Bedroom {i + 1}"
        depth = MASTER_BALCONY if i == 0 else SECONDARY_BALCONY
        emit(f"{key}balcony", f"{name} {terrace}", "balcony", bx, 0, bw, depth,
             has_window=True, projects_from=f"{uid}-{key}")
        # The door's role is the private passage; physically it opens into the bedroom's
        # own lobby, which is part of that passage (`door_via`, used by the renderer).
        emit(key, name, "bedroom", bx, depth, bw, d1 - depth, has_window=True, door_to="passage",
             door_via=f"{key}lobby", **({"headboard": "South or West wall"} if i == 0 else {}))
        bed_xs.append((i, key, name, bx, bw))
    bed_xs.sort()
    if x_servant is not None:
        # Staff room with its own window, at one end of the flat.
        emit("servant", "Servant Room", "servant", x_servant, 0, cols["servant"], d1, has_window=True,
             door_to="passage", door_via="servantlobby")

    # ------------------------------------------------------------ R2: the service row
    emit("breakfast", "Breakfast & Crockery", "dining", x_util, y2, uw + kw, d2, door_to="kitchen")
    pw = POOJA_W[tier]
    excess = (lw - pw) - 2.2 * d2
    if 0 < excess <= 1.2:
        pw = round(pw + excess, 3)       # a little too long for dining: the pooja room takes it
    dining_w = lw - pw
    max_dining = round(2.2 * d2, 3)          # beyond this a dining room reads as a corridor
    if dining_w > max_dining + 1.2:
        emit("dining", "Dining", "dining", x_living, y2, max_dining, d2, has_window=False, door_to="living")
        emit("crockery", "Crockery & Bar", "storage", x_living + max_dining, y2, dining_w - max_dining, d2,
             door_to="dining")
    else:
        emit("dining", "Dining", "dining", x_living, y2, dining_w, d2, has_window=False, door_to="living")
    # Pooja at the far end of the living room from the kitchen: its other neighbour is a
    # lounge or a lobby, never a bath.
    emit("pooja", "Pooja Niche" if tier == 1 else "Pooja Room", "pooja", x_living + lw - pw, y2, pw, d2,
         door_to="living", faces="East")
    if x_office is not None:
        # Store on the lounge side, powder room on the bedroom side: a powder room may not
        # share a wall with a living space.
        pr_w = min(POWDER_W, cols["office"])
        st_w = cols["office"] - pr_w
        if st_w >= 0.8:
            emit("officestore", "Office Store", "storage", x_office, y2, st_w, d2, door_to="office")
        else:
            pr_w, st_w = cols["office"], 0.0
        emit("powder", "Powder Room", "bathroom", x_office + st_w, y2, pr_w, d2, door_to="passage", has_window=False)

    ensuites = int(prog.get("ensuites") or 1)
    common = int(prog.get("common_baths") or 0)
    for i, key, name, bx, bw in bed_xs:
        slot = round(min(BATH_SLOT_W, bw - MIN_LOBBY_W), 3)
        lobby_w = round(bw - slot, 3)
        closet_w = 0.0
        if i == 0 and big and lobby_w - MIN_LOBBY_W >= 1.4:
            closet_w = round(lobby_w - MIN_LOBBY_W, 3)
            lobby_w = round(lobby_w - closet_w, 3)
        # Lobby first: it is what the room on this column's left (the pooja room, a lounge)
        # touches, so no bath ever shares a wall with the pooja room.
        emit(f"{key}lobby", f"{name} Lobby", "passage", bx, y2, lobby_w, d2, door_to="passage")
        cx = bx + lobby_w
        if closet_w:
            emit("mcloset", "Walk-in Closet", "closet", cx, y2, closet_w, d2, door_child_of=key)
            cx += closet_w
        if i < ensuites:
            emit(f"{key}bath", f"{name} Ensuite", "bathroom", cx, y2, slot, d2,
                 door_to=key, door_child_of=key, has_window=False)
        elif common > 0:
            common -= 1
            emit(f"{key}cbath", "Common Bathroom", "bathroom", cx, y2, slot, d2,
                 door_to="passage", has_window=False)
        else:
            emit(f"{key}wardrobe", f"{name} Wardrobe", "closet", cx, y2, slot, d2, door_child_of=key)
    if x_servant is not None:
        emit("servantlobby", "Service Lobby", "passage", x_servant, y2, SERVANT_LOBBY_W, d2, door_to="passage")
        emit("servantbath", "Servant Bath", "servant", x_servant + SERVANT_LOBBY_W, y2,
             cols["servant"] - SERVANT_LOBBY_W, d2, door_child_of="servant")

    # ------------------------------------------------------------ R3: the entry row
    # Foyer under the kitchen block; private passages run from it to both ends; the wet
    # stack (powder room, shaft) closes the end away from the core.
    foyer_x = x_util
    foyer_w = round(min(3.0, max(2.0, uw + kw)), 3)
    emit("foyer", "Entrance Foyer", "entrance", foyer_x, y3, foyer_w, d3, door_to="breakfast",
         main_entrance=True, entry_edge=entry_edge)
    if foyer_x > 0.05:
        emit("passage2", "Private Passage", "passage", 0, y3, foyer_x, d3, door_to="foyer")
    has_powder_tail = beds >= 4 and x_office is None
    tail = SHAFT_W + (POWDER_W if has_powder_tail else 0.0)
    emit("passage", "Private Passage", "passage", foyer_x + foyer_w, y3, plan_end - tail - foyer_x - foyer_w, d3,
         door_to="foyer")
    x = plan_end - tail
    if has_powder_tail:
        # Guest powder room on the wet stack, off the passage.
        emit("powder", "Powder Room", "bathroom", x, y3, POWDER_W, d3, door_to="passage", has_window=False)
        x += POWDER_W
    emit("shaft", "MEP Shaft", "shaft", x, y3, SHAFT_W, d3, has_window=False)

    notes = [
        "Rooms sized from the brief for this BHK tier and scaled to the carpet area "
        f"(x{f:.2f}); the envelope follows the rooms, nothing is stretched to fill space.",
        "Main door -> foyer -> breakfast and dining -> living; bedrooms open onto lobbies off the private passage.",
        "Every habitable room, kitchen included, sits on the facade; the wet stack is at the bedroom end.",
    ]
    return rooms, notes


def variants(unit_type: str, carpet: float) -> List[Tuple[str, Any]]:
    """Every arrangement worth trying for this brief, as (label, planner) pairs with the
    planner signature the floor-plate generator calls."""
    beds, penthouse, prog = _tier(unit_type, carpet)
    servant = bool(prog.get("servant"))
    splits = range(0, min(beds - 1, 2) + 1) if beds >= 3 else (0,)
    out = []
    for mirror in (False, True):
        for master_last in ((False, True) if beds > 1 else (False,)):
            for split in splits:
                for servant_first in ((False, True) if servant else (False,)):
                    label = "realistic planner" + "".join(
                        tag for on, tag in ((mirror, " / mirrored"), (master_last, " / master last"),
                                            (split, f" / {split} bed(s) before core"),
                                            (servant_first, " / servant first")) if on)

                    def planner(box, ut, c, entry, uid, idx, ext, _m=mirror, _ml=master_last,
                                _s=split, _sf=servant_first):
                        return plan_unit(box, ut, c, entry, uid, idx, ext, mirror=_m, master_last=_ml,
                                         split=_s, servant_first=_sf)
                    out.append((label, planner))
    return out


def plan_unit_mirrored(box, unit_type, carpet, entry_edge, uid, unit_index, exterior_edges):
    return plan_unit(box, unit_type, carpet, entry_edge, uid, unit_index, exterior_edges, mirror=True)


# ---------------------------------------------------------------- livability score
HABITABLE = {"bedroom", "living", "kitchen", "dining", "office", "family", "study"}


def livability_defects(rooms: Sequence[Dict[str, Any]]) -> List[str]:
    """Rooms no-one could furnish: corridor-shaped or too narrow. Ranked ahead of the
    soft Vastu placements when choosing between planners."""
    out = []
    for r in rooms:
        w, h = float(r.get("w") or 0), float(r.get("h") or 0)
        if w <= 0 or h <= 0:
            continue
        short, long_ = min(w, h), max(w, h)
        rtype = str(r.get("type"))
        if rtype in HABITABLE:
            min_short = 2.2 if rtype == "kitchen" else 2.4
            if short < min_short:
                out.append(f"{r.get('id')}: {short:.2f} m wide is too narrow for a {rtype}")
            if long_ / short > 2.3:
                out.append(f"{r.get('id')}: {long_:.1f} x {short:.1f} m is corridor-shaped")
        elif rtype in ("bathroom", "pooja", "servant", "storage", "pantry", "closet") and long_ / short > 3.2:
            out.append(f"{r.get('id')}: {long_:.1f} x {short:.1f} m is a strip, not a room")
    return out
