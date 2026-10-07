"""Vastu-anchored unit packing, from the Generative Architecture & Vastu Logic Manual.

The manual states two kinds of rule and it matters which is which, because on a
double-loaded corridor they can pull against each other:

  HARD — geometry that is wrong if broken, and is therefore enforced by construction and
  then re-checked. A balcony that does not project from an exterior wall is not a balcony.
  A pooja room reached through a bathroom wall, or with no door off the living room, is
  not the room the manual describes. Rooms may not overlap, and floor area that belongs to
  no room is not "spare" — it is unbuilt plan.

  SOFT — the sector anchors (master SW, kitchen SE, pooja NE) and the entrance rotation
  matrix. These are targeted by construction, but a flat with one facade cannot always
  give every anchor its sector. Where a target cannot be met, this module says so in the
  report rather than relabelling the result compliant.

Everything is packed by guillotine subdivision: each split consumes a rectangle exactly
and hands back its parts. Overlap is impossible and coverage is total, so "extra space
between the rooms" cannot arise — whatever no room claims is emitted as passage, which is
also what the manual's privacy gradient wants: bedroom doors route through a transitional
corridor rather than opening into the living room.

Axes match the floor plate the rest of the app draws: +x is East, -y is North, so the top
of the plan is North and y increases going South.
"""
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from floorplan.design_guide import (
    BALCONY_RULES,
    DOOR_STANDARDS_MM,
    ENTRY_PADA_RULES,
    audit_room_zones,
    mandala_zone_of,
    room_zone_kind,
)

EPS = 0.03

# Manual §1 — Universal Vastu Anchors. Sector targets, in preference order.
ANCHOR_SECTORS = {
    "pooja": ("NE",),
    "kitchen": ("SE",),
    "master": ("SW",),
}
# Sectors a room must never occupy.
ANCHOR_FORBIDDEN = {
    "pooja": ("S", "SW"),
    "kitchen": ("NE",),
    "master": ("NE",),
}

# Manual §1 — Entrance-specific routing. Keyed by the wall the flat is entered from.
ROTATION_MATRIX = {
    "S": {"kitchen": "SE", "living": "NE", "note": "South facing — vestibule buffer at the door; keep the fixed kitchen wet core in SE."},
    "N": {"kitchen": "SE", "living": "NE", "note": "North facing — living flows to N/E; keep the fixed kitchen wet core in SE."},
    "E": {"kitchen": "SE", "living": "NE", "note": "East facing — the ideal case: living flows N/NE, kitchen slots straight into SE."},
    "W": {"kitchen": "SE", "living": "N", "note": "West facing — living occupies N/E where possible; kitchen remains in SE."},
}

# Manual §2 — Configuration & Scaling Protocol. Per BHK: how many bedrooms carry an
# en-suite, how many common baths, whether the pooja is a room or a niche, and the
# balconies and service spaces the tier is entitled to.
SCALING_PROTOCOL = {
    1: {"beds": 1, "ensuites": 1, "common_baths": 0, "pooja": "niche",
        "balconies": ["living"], "utility": "compact", "servant": False, "extras": ["powder_optional"]},
    2: {"beds": 2, "ensuites": 1, "common_baths": 1, "pooja": "room",
        "balconies": ["living"], "utility": "room", "servant": False, "extras": []},
    3: {"beds": 3, "ensuites": 2, "common_baths": 1, "pooja": "room",
        "balconies": ["living", "master"], "utility": "large", "servant": False, "extras": []},
    4: {"beds": 4, "ensuites": 3, "common_baths": 1, "pooja": "room",
        "balconies": ["living", "master", "submaster"], "utility": "large", "servant": True,
        "extras": []},
    5: {"beds": 5, "ensuites": 4, "common_baths": 1, "pooja": "room",
        "balconies": ["living", "master", "submaster"], "utility": "large", "servant": True,
        "extras": ["powder", "lounge", "pantry", "office", "closet"]},
}

BALCONY_DEPTH = 1.5          # projection depth of a balcony off its exterior wall
PASSAGE_MIN_W = 1.05         # a passage narrower than this is not a passage
FOYER_DEPTH = 1.6            # entry vestibule against the entrance wall
MIN_SERVICE_W = 1.2          # a bathroom narrower than this is not usable
MIN_COLUMN_W = 2.6           # a habitable room narrower than this is a corridor, not a room


# ---------------------------------------------------------------- geometry primitives
def _rect(x, y, w, h):
    return {"x": round(x, 2), "y": round(y, 2), "w": round(w, 2), "h": round(h, 2)}


def area(r) -> float:
    return float(r["w"]) * float(r["h"])


def touches(a, b) -> bool:
    """Share a wall segment of non-zero length (not merely a corner)."""
    ax, ay, aw, ah = a["x"], a["y"], a["w"], a["h"]
    bx, by, bw, bh = b["x"], b["y"], b["w"], b["h"]
    vert = (abs((ax + aw) - bx) < EPS or abs((bx + bw) - ax) < EPS) and (ay < by + bh - EPS and by < ay + ah - EPS)
    horiz = (abs((ay + ah) - by) < EPS or abs((by + bh) - ay) < EPS) and (ax < bx + bw - EPS and bx < ax + aw - EPS)
    return vert or horiz


def overlaps(a, b) -> bool:
    return not (a["x"] + a["w"] <= b["x"] + EPS or b["x"] + b["w"] <= a["x"] + EPS
                or a["y"] + a["h"] <= b["y"] + EPS or b["y"] + b["h"] <= a["y"] + EPS)


def sector_of(r, box) -> str:
    """Which Vastu quadrant a room's centroid falls in, within its own flat's box."""
    cx = r["x"] + r["w"] / 2.0
    cy = r["y"] + r["h"] / 2.0
    mx = box["x"] + box["w"] / 2.0
    my = box["y"] + box["h"] / 2.0
    ns = "N" if cy <= my else "S"
    ew = "E" if cx >= mx else "W"
    return ns + ew


def on_edge(r, box, edge) -> bool:
    """Does the room run along one named edge of its flat's box?"""
    if edge == "N":
        return abs(r["y"] - box["y"]) < EPS
    if edge == "S":
        return abs((r["y"] + r["h"]) - (box["y"] + box["h"])) < EPS
    if edge == "W":
        return abs(r["x"] - box["x"]) < EPS
    if edge == "E":
        return abs((r["x"] + r["w"]) - (box["x"] + box["w"])) < EPS
    return False


def on_any_edge(r, box, edges: Sequence[str]) -> bool:
    return any(on_edge(r, box, e) for e in edges)


# ---------------------------------------------------------------- guillotine splitting
def split_h(rect, top_h) -> Tuple[dict, dict]:
    """Cut a horizontal band `top_h` deep off the top. Both parts are exact."""
    top_h = max(0.0, min(top_h, rect["h"]))
    return (_rect(rect["x"], rect["y"], rect["w"], top_h),
            _rect(rect["x"], rect["y"] + top_h, rect["w"], rect["h"] - top_h))


def split_v(rect, left_w) -> Tuple[dict, dict]:
    """Cut a vertical band `left_w` wide off the left. Both parts are exact."""
    left_w = max(0.0, min(left_w, rect["w"]))
    return (_rect(rect["x"], rect["y"], left_w, rect["h"]),
            _rect(rect["x"] + left_w, rect["y"], rect["w"] - left_w, rect["h"]))


def split_v_many(rect, weights: Sequence[float]) -> List[dict]:
    """Slice a rectangle into columns by weight. The last column takes the rounding
    remainder so the parts always re-add to the whole — a column set that sums to
    slightly less than the parent is exactly how a sliver of unowned plan appears."""
    total = sum(weights) or 1.0
    out, cursor = [], rect["x"]
    for i, wgt in enumerate(weights):
        w = (rect["w"] * wgt / total) if i < len(weights) - 1 else (rect["x"] + rect["w"] - cursor)
        out.append(_rect(cursor, rect["y"], w, rect["h"]))
        cursor += w
    return out


# ---------------------------------------------------------------- the programme
def bhk_of(unit_type: str, carpet: float) -> int:
    t = (unit_type or "").lower().replace(" ", "")
    for n in (5, 4, 3, 2, 1):
        if f"{n}bhk" in t:
            return n
    if "penthouse" in t:
        return 5
    if "studio" in t:
        return 1
    return max(1, min(5, int(round((carpet - 30) / 25)) + 1))


def unit_programme(unit_type: str, carpet: float) -> Dict[str, Any]:
    """The manual's scaling protocol resolved for one flat."""
    n = bhk_of(unit_type, carpet)
    spec = dict(SCALING_PROTOCOL[n])
    spec["bhk"] = n
    spec["is_penthouse"] = "penthouse" in (unit_type or "").lower() or n >= 5
    return spec


# ---------------------------------------------------------------- packing one flat
def pack_unit(box: dict, unit_type: str, carpet: float, entry_edge: str,
              exterior_edges: Sequence[str], uid: str, unit_index: int = 0) -> Tuple[List[dict], List[str]]:
    """Pack one flat into `box`, entered from `entry_edge`, with `exterior_edges` open to air.

    The flat is bands parallel to the corridor, then one column per habitable room:

        entry wall  |  foyer strip
                    |  passage spine
                    |  col      | col      | col      |   each column, corridor end first:
                    |  svc|stub | svc|stub | svc|stub |     a bath or utility beside a stub
                    |  room     | room     | room     |     of passage, then the room,
                    |  balcony  |          | balcony  |     then its balcony if entitled
        facade      |

    The stub is what lets a bedroom door open onto the passage rather than onto the living
    room, with its bath still adjacent. The balcony is cut from the room's own facade end
    rather than from a band across the whole flat, so a room without one still reaches the
    facade and stays daylit — a full-width balcony band walls every other room off the air.

    Column order left to right places the sector anchors. West is -x. On the row whose
    facade faces south the master lands in a true SW; on the row facing north it cannot,
    because that flat's south half is its corridor wall, and `notes` records the miss
    rather than the report claiming otherwise.

    Returns (rooms, notes).
    """
    prog = unit_programme(unit_type, carpet)
    rot = ROTATION_MATRIX.get(entry_edge, ROTATION_MATRIX["S"])
    notes = [rot["note"]]
    rooms: List[dict] = []

    def emit(key, name, rtype, rect, **extra):
        if rect is None or rect["w"] < 0.5 or rect["h"] < 0.5:
            return None
        r = {"id": f"{uid}-{key}", "name": name, "type": rtype,
             "unit_id": uid, "unit_type": unit_type, "unit_index": unit_index,
             **rect, **extra}
        rooms.append(r)
        return r

    facade = [e for e in exterior_edges if e != entry_edge]
    opposite = {"S": "N", "N": "S", "E": "W", "W": "E"}[entry_edge]
    if not facade:
        notes.append("This flat has no wall open to air other than its entrance — no balcony can "
                     "project and no room can be daylit. Widen the block or put fewer flats on the row.")
        facade_edge = None
    else:
        # The long wall opposite the corridor is the facade; an end flat's side wall is a
        # bonus, not the wall the plan is organised around.
        facade_edge = opposite if opposite in facade else facade[0]

    facade_is_south = facade_edge == "S"
    work = _rect(box["x"], box["y"], box["w"], box["h"])

    # 1. Entry vestibule against the entrance wall. The manual asks for one explicitly at a
    #    south door; every orientation gets it because it is also the head of the passage.
    foyer_depth = min(FOYER_DEPTH, work["h"] * 0.15)
    if entry_edge == "S":
        work, entry_strip = split_h(work, work["h"] - foyer_depth)
    else:
        entry_strip, work = split_h(work, foyer_depth)

    # 2. Passage spine, next to the foyer. Bedroom doors take off this, never off the living
    #    room — the manual's privacy gradient.
    passage_depth = max(PASSAGE_MIN_W, min(1.35, work["h"] * 0.13))
    if entry_edge == "S":
        work, passage_strip = split_h(work, work["h"] - passage_depth)
    else:
        passage_strip, work = split_h(work, passage_depth)

    # 2b. On the row whose facade faces south, the flat's north-east corner is its corridor
    #     end — so the pooja's NE anchor is reached by taking the east end of the passage
    #     rather than a column of facade. It needs no daylight, and keeping it off the
    #     facade is what leaves the bedrooms columns wide enough to hold a bath and still
    #     open onto the passage.
    pooja_cell = None
    if facade_is_south and prog["pooja"] == "room":
        pooja_w = min(max(1.5, passage_strip["w"] * 0.16), 2.4)
        if passage_strip["w"] - pooja_w >= 2.5:
            passage_strip, pooja_cell = split_v(passage_strip, passage_strip["w"] - pooja_w)

    # 3. One column per habitable room, ordered so the anchors land as near their sector as
    #    a single facade allows.
    beds = prog["beds"]
    slots: List[Tuple[str, float]] = []
    if facade_is_south:
        # Facade south: the west end of the lit band is a true SW, so the master belongs
        # there, and the kitchen's SE target is the east half. The living room ends the row
        # so the pooja — carved from the east end of the passage, where it needs no facade
        # of its own — has a full wall of it to take its door from.
        slots.append(("master", 1.45))
        for i in range(2, beds + 1):
            slots.append((f"bed{i}", 1.0))
        slots.append(("kitchen", 1.5))
        if beds >= 5:
            slots.append(("pantry", 0.55))
        if prog["is_penthouse"]:
            slots.append(("office", 1.0))
            slots.append(("family", 1.0))
        slots.append(("living", 1.6))
    else:
        # Facade north: the corridor occupies the south side, so this one-facade envelope
        # cannot reach every preferred zone. Place the kitchen near the east end and report
        # any SE miss rather than treating NW as an alternate kitchen anchor.
        slots.append(("kitchen", 1.5))
        if beds >= 5:
            slots.append(("pantry", 0.55))
        slots.append(("master", 1.45))
        for i in range(2, beds + 1):
            slots.append((f"bed{i}", 1.0))
        # The living room ends the row on both rows, so whatever sits east of it — the pooja
        # strip here, the pooja cell in the passage on the other row — always has a full
        # wall of living to take its door from. The office goes before it, not after.
        if prog["is_penthouse"]:
            slots.append(("office", 1.0))
            slots.append(("family", 1.0))
        slots.append(("living", 1.6))
        if prog["pooja"] == "room":
            pooja_w = min(max(1.5, work["w"] * 0.1), 2.2)
            if work["w"] - pooja_w >= MIN_COLUMN_W * 2:
                work, pooja_cell = split_v(work, work["w"] - pooja_w)
        notes.append("Entered from the south with its only facade to the north, this flat cannot put "
                     "the master bedroom in a daylit SW — that half of it is the corridor wall. The "
                     "master takes the west end of the lit band and the anchor is reported unmet.")

    # A flat only has so much frontage, and every habitable room here takes a slice of it.
    # Past a certain point the columns stop being rooms: five bedrooms plus a living room,
    # kitchen, pooja and office want about 27 m of facade, and a 17 m flat cannot give it.
    # Rather than produce a plan of 1.8 m "bedrooms", the optional slots are dropped in
    # order of how little the manual insists on them, and the reader is told.
    # Dropped in this order: the office is the one room here the manual never insists on.
    # The pooja is never dropped — it is one of the non-negotiable anchors — it moves off
    # the facade into the passage band, which costs it daylight it does not need.
    pooja_to_passage = False
    OPTIONAL = ["office", "pooja"]
    while len(slots) > 2:
        total_slot_w = sum(x for _, x in slots)
        narrowest = min(work["w"] * w / total_slot_w for k, w in slots if k != "pantry")
        if narrowest >= MIN_COLUMN_W:
            break
        drop = next((k for k in OPTIONAL if any(k == n for n, _ in slots)), None)
        if drop is None:
            notes.append(f"This flat's programme needs more frontage than its {work['w']:.1f} m "
                         f"gives it — its rooms are packed to {narrowest:.1f} m wide. Widen the "
                         f"flat or move a bedroom to the tier below.")
            break
        slots = [(n, w) for n, w in slots if n != drop]
        if drop == "pooja":
            pooja_to_passage = True
            notes.append("Pooja moved off the facade into the passage — it needs no daylight, and "
                         "at this frontage a column for it would have squeezed every room that does.")
        else:
            notes.append(f"{drop.title()} omitted — at this frontage it would have squeezed every "
                         f"other room below {MIN_COLUMN_W} m.")

    if pooja_to_passage and pooja_cell is None and prog["pooja"] == "room":
        pooja_w = min(max(1.5, passage_strip["w"] * 0.16), 2.4)
        if passage_strip["w"] - pooja_w >= 2.5:
            passage_strip, pooja_cell = split_v(passage_strip, passage_strip["w"] - pooja_w)

    cells = dict(zip([k for k, _ in slots], split_v_many(work, [w for _, w in slots])))
    leftovers: List[dict] = []
    deferred: List[Tuple[str, str]] = []      # services that did not fit their own column

    def corridor_end(cell, depth):
        """Split `depth` off the end of a column that faces the corridor."""
        if facade_is_south:
            return split_h(cell, depth)                       # corridor is north: take the top
        near, far = split_h(cell, cell["h"] - depth)          # corridor is south: take the bottom
        return far, near

    def facade_end(cell, depth):
        """Split `depth` off the end of a column that faces the air."""
        if facade_is_south:
            near, far = split_h(cell, cell["h"] - depth)      # facade is south: take the bottom
            return far, near
        return split_h(cell, depth)                           # facade is north: take the top

    def fill_column(cell, key, name, rtype, service=None, balcony=None, closet=False,
                    common_bath=False, **extra):
        """Lay out one column: service + passage stub at the corridor end, the room in the
        middle, its balcony at the facade end. Returns nothing; emits as it goes."""
        body = cell
        if balcony and facade_edge:
            target_depth = (1.8 if balcony == "balcony_living" else
                            1.2 if balcony == "balcony_master" else 1.5)
            depth = target_depth if body["h"] - target_depth >= 2.2 else min(target_depth, body["h"] * 0.28)
            bal, body = facade_end(body, depth)
            if bal["w"] >= 1.2:
                is_terrace = prog["is_penthouse"]
                emit(balcony, "Wrap-around Terrace" if is_terrace else "Balcony",
                     "terrace" if is_terrace else "balcony", bal, projects_from=f"{uid}-{key}")
            else:
                notes.append(f"{name} balcony omitted — the column is narrower than a usable balcony.")
                body = cell
        if service:
            svc_key, svc_name, svc_frac = service
            if svc_key == "utility":
                depth = min(max(1.25, body["h"] * 0.18), 1.45, max(1.25, body["h"] - 2.2))
            else:
                depth = min(body["h"] * svc_frac, body["h"] - 2.2)
            # The stub is the strip of passage that keeps the room's own door on the spine.
            # Without it the service sits across the whole column and the only way into the
            # room is through another habitable room.
            stub_w = max(PASSAGE_MIN_W * 0.85, body["w"] * 0.3)
            if svc_key != "utility" and depth >= 1.3 and body["w"] - stub_w >= MIN_SERVICE_W:
                band, body = corridor_end(body, depth)
                svc, stub = split_v(band, band["w"] - stub_w)
                # The walk-in closet belongs between the bedroom and its en-suite, so it is
                # taken out of the same band rather than stacked in front of the bedroom —
                # stacking it there would put it across the room's only door to the passage.
                if closet and svc["w"] >= MIN_SERVICE_W + 1.4:
                    svc, cl = split_v(svc, svc["w"] - 1.4)
                    emit("mcloset", "Walk-in Closet", "closet", cl, door_child_of=key)
                    closet = False
                # An en-suite is a strict child of its bedroom: its only door is on the
                # bedroom wall. It may share a wall with the passage — walls are not
                # doors — but it must never be reachable from one, which is the bypass
                # route the manual calls out.
                door_route = {"door_from": ["passage"]} if common_bath else {"door_child_of": key}
                emit(svc_key, svc_name, "bathroom" if "bath" in svc_key else "utility", svc,
                     has_window=False, **door_route)
                leftovers.append(stub)
            elif "bath" in svc_key:
                # Too narrow to hold a usable bath and still leave the room a door onto the
                # passage. The manual wants baths clustered around a duct shaft anyway, so
                # it joins the cluster instead of being squeezed in at an unusable width.
                deferred.append((svc_key, svc_name))
            elif depth >= 1.0:
                # A utility is never deferred — the manual snaps it to the kitchen. But it
                # must not wall the kitchen off from the rest of the flat either: a kitchen
                # that touches nothing but its own utility has no door. A dry balcony works
                # at 1.0 m, so the stub is cut first and the utility takes what is left.
                band, body = corridor_end(body, depth)
                stub_w2 = max(PASSAGE_MIN_W * 0.85, band["w"] * 0.28)
                if band["w"] - stub_w2 >= 1.2:
                    util, stub = split_v(band, band["w"] - stub_w2)
                    emit(svc_key, svc_name, "utility", util, has_window=False, door_from=["kitchen"])
                    leftovers.append(stub)
                else:
                    emit(svc_key, svc_name, "utility", band, has_window=False, door_from=["kitchen"])
        emit(key, name, rtype, body, **extra)

    if "master" in cells:
        fill_column(cells["master"], "mbed",
                    "Grand Master Suite" if prog["is_penthouse"] else "Master Bedroom", "bedroom",
                    service=("mbath", "Master Ensuite Bath", 0.3),
                    balcony="balcony_master" if "master" in prog["balconies"] else None,
                    closet="closet" in prog["extras"], has_window=True,
                    door_from=["passage", "living"], headboard="South or West wall")

    if "kitchen" in cells:
        fill_column(cells["kitchen"], "kitchen", "Kitchen", "kitchen",
                    service=("utility", "Utility / Dry Balcony", 0.28),
                    has_window=True, hob_faces="East", door_from=["living", "passage"])

    if "pantry" in cells:
        fill_column(cells["pantry"], "pantry", "Pantry / Dry Storage", "pantry",
                    has_window=False, door_from=["kitchen"])

    for i in range(2, beds + 1):
        slot = f"bed{i}"
        if slot not in cells:
            continue
        ensuite = prog["ensuites"] >= i
        fill_column(cells[slot], slot,
                    "Sub-master Bedroom" if ensuite else f"Bedroom {i}", "bedroom",
                    service=(f"{slot}bath",
                             "Sub-master Ensuite" if ensuite else f"Common Bathroom {i - 1}", 0.28),
                    balcony=f"balcony_bed{i}" if (i == 2 and "submaster" in prog["balconies"]) else None,
                    common_bath=not ensuite,
                    has_window=True, door_from=["passage", "living"])

    if "living" in cells:
        fill_column(cells["living"], "living",
                    "Family Lounge / Living" if prog["is_penthouse"] else "Living / Dining", "living",
                    balcony="balcony_living" if "living" in prog["balconies"] else None,
                    has_window=True, door_from=["entrance", "passage"])
    if "office" in cells:
        fill_column(cells["office"], "office", "Dedicated Home Office", "office",
                    balcony="terrace_office", has_window=True, door_from=["passage", "living"])
    if "family" in cells:
        fill_column(cells["family"], "family", "Family Lounge", "family",
                    has_window=True, door_from=["passage", "living"])

    # The pooja takes a whole column, so it runs from the passage to the facade and shares a
    # full wall with the living room beside it — that wall is where its door goes. Nothing is
    # stacked behind it, which is also what keeps every bathroom off its walls.
    if "pooja" in cells:
        emit("pooja", "Pooja Room", "pooja", cells["pooja"], faces="East", door_from=["living"])
    elif pooja_cell is not None:
        emit("pooja", "Pooja Room", "pooja", pooja_cell, faces="East", door_from=["living"])
    elif prog["pooja"] == "niche":
        # Emit a compact pooja niche adjoining the living room so 1BHK programmes have a real pooja element
        if "living" in cells:
            niche_w = min(1.2, passage_strip["w"] * 0.18)
            if passage_strip["w"] - niche_w >= 2.0:
                passage_strip, niche_cell = split_v(passage_strip, passage_strip["w"] - niche_w)
                emit("pooja", "Pooja Niche", "pooja", niche_cell, faces="East", door_from=["living"])

    # Services that did not fit their column are clustered together against the passage,
    # which is what the manual's plumbing-stack rule asks for: baths back to back around one
    # or two duct shafts rather than scattered one per room.
    if True:
        # A duct shaft is emitted whether or not a bath was deferred: the manual asks for the
        # ensuite, common bath and kitchen utility to be stacked around one or two of them,
        # and a plan with no shaft has nowhere for those runs to go.
        cluster_items = list(deferred) + [("shaft", "MEP Duct Shaft")]
        cluster_w = min(entry_strip["w"] * 0.42, 1.9 * len(deferred) + 0.9)
        # The cluster comes out of the foyer band, not the passage: taking it from the
        # passage shortens the one strip every bedroom door has to reach, and the rooms at
        # that end of the flat end up opening onto a bathroom instead.
        if entry_strip["w"] - cluster_w >= 2.0:
            cluster, entry_strip = split_v(entry_strip, cluster_w)
            weights = [1.0] * len(deferred) + [0.5]
            for (svc_key, svc_name), rect in zip(cluster_items, split_v_many(cluster, weights)):
                rtype = "shaft" if svc_key == "shaft" else ("bathroom" if "bath" in svc_key else "utility")
                emit(svc_key, svc_name, rtype, rect, has_window=False, clustered=True,
                     door_from=["passage", "entrance"])
        elif deferred:
            notes.append("No room on the passage for the " + ", ".join(n for _, n in deferred)
                         + " — the flat is too shallow for its bedroom count.")

    # Carve the 4/5 BHK's service rooms and guest powder room from the entry band. This
    # keeps them beside the foyer/service access without borrowing bedroom or kitchen area.
    support_width = (5.5 if prog["servant"] else 0.0) + (1.5 if "powder" in prog["extras"] else 0.0)
    if support_width and entry_strip["w"] - support_width >= 2.2 and entry_strip["h"] >= 1.5:
        service_band, entry_strip = split_v(entry_strip, support_width)
        support_cursor = service_band
        if prog["servant"]:
            servant_bath, support_cursor = split_v(support_cursor, 1.3)
            servant_room, support_cursor = split_v(support_cursor, 4.2)
            emit("servant", "Servant Room", "servant", servant_room,
                 has_window=False, service_access=True, entry_edge=entry_edge)
            emit("servantbath", "Servant Bath", "bathroom", servant_bath,
                 has_window=False, door_child_of="servant")
        if "powder" in prog["extras"]:
            powder, support_cursor = split_v(support_cursor, 1.5)
            emit("powder", "Guest Powder Room", "bathroom", powder,
                 has_window=False, door_from=["entrance"])
    elif support_width:
        notes.append("Servant/powder service rooms could not fit beside the foyer in this unit envelope.")

    emit("foyer", "Entrance / Foyer", "entrance", entry_strip, main_entrance=True)
    emit("passage", "Passage", "passage", passage_strip, door_from=["entrance"])
    for i, rect in enumerate(leftovers, start=2):
        emit(f"passage{i}", f"Passage {i}", "passage", rect)

    # Stamp each anchor with the sector it is ACTUALLY in. The previous generator wrote a
    # fixed "SW (Nairutya)" onto the master whatever it had done with it, which made the
    # label useless: it agreed with the intent, never with the plan.
    sector_names = {"NE": "NE (Ishanya)", "SE": "SE (Agni)", "SW": "SW (Nairutya)",
                    "NW": "NW (Vayu)", "CENTER": "CENTER (Brahmasthan)"}
    for r in rooms:
        if r["type"] in ("pooja", "kitchen") or r["id"].endswith("-mbed"):
            sec = mandala_zone_of(r, box)
            r["vastu"] = sector_names.get(sec, sec)

    return rooms, notes


def _residual_of(strip: dict, taken: Sequence[dict]) -> List[dict]:
    """The parts of a strip no room claimed, as rectangles. The strip is 1-D (one band),
    so the residual is the gaps along its long axis — no general decomposition needed."""
    horizontal = strip["w"] >= strip["h"]
    inside = [t for t in taken if overlaps(t, strip)]
    if not inside:
        return [strip]
    out = []
    if horizontal:
        spans = sorted((t["x"], t["x"] + t["w"]) for t in inside)
        cursor = strip["x"]
        for a, b in spans:
            if a - cursor > EPS:
                out.append(_rect(cursor, strip["y"], a - cursor, strip["h"]))
            cursor = max(cursor, b)
        if strip["x"] + strip["w"] - cursor > EPS:
            out.append(_rect(cursor, strip["y"], strip["x"] + strip["w"] - cursor, strip["h"]))
    else:
        spans = sorted((t["y"], t["y"] + t["h"]) for t in inside)
        cursor = strip["y"]
        for a, b in spans:
            if a - cursor > EPS:
                out.append(_rect(strip["x"], cursor, strip["w"], a - cursor))
            cursor = max(cursor, b)
        if strip["y"] + strip["h"] - cursor > EPS:
            out.append(_rect(strip["x"], cursor, strip["w"], strip["y"] + strip["h"] - cursor))
    return out


# ---------------------------------------------------------------- the strict check
def check_unit(rooms: Sequence[dict], box: dict, exterior_edges: Sequence[str],
               entry_edge: str) -> Dict[str, Any]:
    """Hard rules, checked geometrically on the packed result.

    A violation here is a defect in the plan, not a preference that was not met — the soft
    sector targets are reported separately by `sector_report`.
    """
    v: List[str] = []
    by_type: Dict[str, List[dict]] = {}
    for r in rooms:
        by_type.setdefault(r["type"], []).append(r)

    for kitchen in by_type.get("kitchen", []):
        if str(kitchen.get("hob_faces") or "").strip().lower() != "east":
            v.append(f"{kitchen['id']}: cooking hob must face East")

    # Entry location and door construction are checked from the same metadata the AI prompt
    # and deterministic generator use.
    entrances = [r for r in rooms if r.get("main_entrance")]
    if not entrances:
        v.append("unit has no marked main entrance")
    for entry in entrances:
        edge = str(entry.get("entry_edge") or entry_edge or "").upper()
        pada = entry.get("entry_pada")
        allowed = ENTRY_PADA_RULES.get(edge, ())
        if not allowed or pada not in allowed:
            v.append(f"{entry.get('id', 'main entrance')}: entry Pada {pada} is not allowed on {edge or 'unknown'}")

    for room in rooms:
        has_door = bool(
            room.get("main_entrance") or room.get("door_to") or room.get("door_from")
            or room.get("door_child_of") or room.get("service_access")
        )
        if not has_door:
            continue
        kind = room_zone_kind(room)
        if room.get("main_entrance"):
            standard = DOOR_STANDARDS_MM["main_entrance"]
            width = room.get("door_width_mm")
            if width is None or not standard["width_min"] <= float(width) <= standard["width_max"]:
                v.append(f"{room.get('id', 'main entrance')}: entrance door must be 1050-1200 mm wide")
            expected_height = standard["height"]
        else:
            door_kind = (
                "bedroom" if kind in ("master", "bedroom") else
                "kitchen" if kind == "kitchen" else
                "bathroom" if kind == "bathroom" else "other"
            )
            standard = DOOR_STANDARDS_MM[door_kind]
            if room.get("door_width_mm") != standard["width"]:
                v.append(f"{room.get('id', 'room')}: {door_kind} door width must be {standard['width']} mm")
            expected_height = standard["height"]
        if room.get("door_height_mm") != expected_height:
            v.append(f"{room.get('id', 'room')}: door height must be {expected_height} mm")
        hinge = room.get("hinge_offset_mm")
        if hinge is None or not DOOR_STANDARDS_MM["hinge_offset_min"] <= float(hinge) <= DOOR_STANDARDS_MM["hinge_offset_max"]:
            v.append(f"{room.get('id', 'room')}: hinge offset must be 100-150 mm")
        if room.get("door_swing") != DOOR_STANDARDS_MM["swing"]:
            v.append(f"{room.get('id', 'room')}: door must swing inward clockwise")

    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            if overlaps(rooms[i], rooms[j]):
                v.append(f"overlap: {rooms[i]['id']} × {rooms[j]['id']}")

    # Balconies: only on a wall open to air, and only projecting from a real room.
    facade = [e for e in exterior_edges if e != entry_edge]
    for b in by_type.get("balcony", []) + by_type.get("terrace", []):
        if not on_any_edge(b, box, facade):
            v.append(f"{b['id']}: balcony is not on an exterior wall")
        anchor_id = b.get("projects_from")
        anchor = next((r for r in rooms if r["id"] == anchor_id), None)
        if anchor is None or not touches(b, anchor):
            v.append(f"{b['id']}: balcony does not project from the room it belongs to")
        depth = b.get("balcony_depth_mm")
        source_kind = room_zone_kind(anchor) if anchor else ""
        faces_north_east = on_edge(b, box, "N") or on_edge(b, box, "E")
        if source_kind == "living" and faces_north_east:
            if depth is None or float(depth) < BALCONY_RULES["living_north_east_depth_min_mm"]:
                v.append(f"{b['id']}: north/east living balcony must be at least 1800 mm deep")
            if depth is not None and float(depth) > BALCONY_RULES["living_north_east_depth_max_mm"]:
                v.append(f"{b['id']}: north/east living balcony must not exceed 2400 mm deep")
            if b.get("slab_drop_mm") is None or not (
                BALCONY_RULES["north_east_slab_drop_min_mm"] <= float(b["slab_drop_mm"])
                <= BALCONY_RULES["north_east_slab_drop_max_mm"]
            ):
                v.append(f"{b['id']}: north/east living balcony slab drop must be 12-25 mm")
        elif source_kind == "master" and depth != BALCONY_RULES["master_depth_mm"]:
            v.append(f"{b['id']}: master balcony must be 1200 mm deep")
        elif source_kind == "bedroom" and (depth is None or not (
            BALCONY_RULES["secondary_depth_min_mm"] <= float(depth)
            <= BALCONY_RULES["secondary_depth_max_mm"]
        )):
            v.append(f"{b['id']}: secondary balcony must be 1200-1500 mm deep")
        if (on_edge(b, box, "S") or on_edge(b, box, "W")) and b.get("parapet_type") != "1000mm masonry":
            v.append(f"{b['id']}: south/west balcony needs a 1000 mm masonry parapet")

    living_rooms = by_type.get("living", [])
    living_room = next((r for r in living_rooms
                        if "family lounge" not in str(r.get("name", "")).lower()),
                       living_rooms[0] if living_rooms else None)
    balcony_sources = {b.get("projects_from") for b in by_type.get("balcony", []) + by_type.get("terrace", [])}
    if living_room and living_room.get("id") not in balcony_sources:
        v.append("living room has no attached main balcony")
    unit_type = str((rooms[0].get("unit_type") if rooms else "") or "")
    bedrooms = bhk_of(unit_type, area(box))
    if bedrooms >= 3:
        master = next((r for r in by_type.get("bedroom", [])
                       if r["id"].endswith("-mbed") or "master" in str(r.get("name", "")).lower()), None)
        if master and master.get("id") not in balcony_sources:
            v.append("3+ BHK master bedroom has no attached balcony")
    if bedrooms >= 4 and not any(
        room_zone_kind(by_id) == "bedroom" and by_id.get("id") in balcony_sources
        for by_id in by_type.get("bedroom", [])
    ):
        v.append("4+ BHK has no attached secondary bedroom balcony")

    # Pooja: a door off the living room, and never a shared wall with a bathroom.
    living = next((r for r in by_type.get("living", [])
                   if "family lounge" not in str(r.get("name", "")).lower()),
                  next(iter(by_type.get("living", [])), None))
    for p in by_type.get("pooja", []):
        if living is None or not touches(p, living):
            v.append(f"{p['id']}: pooja room has no wall on the living room to take its door from")
        for b in by_type.get("bathroom", []):
            if touches(p, b):
                v.append(f"{p['id']}: pooja room shares a wall with {b['id']}")

    # Privacy gradient: a bedroom door may not open onto the living room, so a bedroom must
    # have a passage or foyer to open onto instead.
    transitional = by_type.get("passage", []) + by_type.get("entrance", [])
    for bed in by_type.get("bedroom", []):
        if not any(touches(bed, t) for t in transitional):
            v.append(f"{bed['id']}: no passage or foyer to open onto — its door would give onto a habitable room")

    # ---- Door routing (manual §1). A door is a route, not a wall: two rooms may share a
    # wall freely, and what the manual constrains is which wall the opening is cut in. Each
    # room therefore records the ROLE its door is taken from — "a passage or the living
    # room" — and an en-suite records the one bedroom it is a child of.
    by_id = {r["id"]: r for r in rooms}
    prefix = (rooms[0].get("unit_id") + "-") if rooms and rooms[0].get("unit_id") else ""

    def adjacent_of_type(room, types):
        return [o for o in rooms if o is not room and o["type"] in types and touches(room, o)]

    for r in rooms:
        child_of = r.get("door_child_of")
        if child_of:
            parent = by_id.get(prefix + child_of)
            if parent is None or not touches(r, parent):
                v.append(f"{r['id']}: door is meant to open from {child_of}, but they share no wall")
            continue
        roles = r.get("door_from")
        if roles and not adjacent_of_type(r, set(roles)):
            v.append(f"{r['id']}: nothing to take its door from — it adjoins no "
                     f"{' or '.join(roles)}")

    circulation = {"passage", "entrance"}
    for bed in by_type.get("bedroom", []):
        # Never off the foyer or the kitchen, even where one happens to be adjacent.
        if not adjacent_of_type(bed, {"passage", "living"}):
            off = {o["type"] for o in adjacent_of_type(bed, {"entrance", "kitchen"})}
            v.append(f"{bed['id']}: no living room or passage to take its door from"
                     + (f" — only a {' and '.join(sorted(off))}" if off else ""))

    for bath in by_type.get("bathroom", []):
        if bath.get("door_child_of"):
            # A strict child of its bedroom. A second opening onto circulation is the
            # bypass route the manual prohibits outright.
            if bath.get("secondary_door"):
                v.append(f"{bath['id']}: en-suite has a second door onto a passage")
            continue
        if not adjacent_of_type(bath, circulation):
            v.append(f"{bath['id']}: unattached bathroom must open onto a passage or vestibule")
        for other in adjacent_of_type(bath, {"living", "kitchen"}):
            v.append(f"{bath['id']}: common bathroom is against the {other['type']} — the manual "
                     f"shields it from that sightline")

    for serv in by_type.get("servant", []):
        if serv.get("service_access") and on_edge(serv, box, entry_edge):
            continue
        if not adjacent_of_type(serv, {"utility", "entrance", "passage"}):
            v.append(f"{serv['id']}: servant quarters need their own service access, "
                     f"not a route through the living room")

    # Utility snaps to the kitchen.
    kitchen = next(iter(by_type.get("kitchen", [])), None)
    for u in by_type.get("utility", []):
        if kitchen is None or not touches(u, kitchen):
            v.append(f"{u['id']}: utility is not snapped to the kitchen")
        width_mm = u.get("utility_width_mm")
        if width_mm is None:
            width_mm = min(float(u.get("w") or 0), float(u.get("h") or 0)) * 1000
        if not 1200 <= float(width_mm) <= 1500:
            v.append(f"{u['id']}: kitchen utility width must be 1200-1500 mm")

    # Daylight: every habitable room wants a wall on air, directly or through its balcony.
    balconies = by_type.get("balcony", []) + by_type.get("terrace", [])
    for r in rooms:
        if r["type"] not in ("living", "family", "bedroom", "kitchen", "study", "office"):
            continue
        if on_any_edge(r, box, exterior_edges) or any(touches(r, b) for b in balconies):
            continue
        v.append(f"{r['id']}: habitable room has no wall open to air")

    # Coverage: the flat's rooms must tile its box. Anything else is unbuilt plan.
    covered = sum(area(r) for r in rooms)
    box_area = area(box)
    coverage = covered / box_area if box_area else 0.0
    if coverage < 0.985:
        v.append(f"unbuilt plan: rooms cover {coverage * 100:.1f}% of the flat — "
                 f"{box_area - covered:.1f} m² belongs to no room")

    return {"violations": v, "coverage_pct": round(coverage * 100, 1), "ok": not v}


def sector_report(rooms: Sequence[dict], box: dict) -> Dict[str, Any]:
    """Which anchors landed in their 3x3 Mandala zone, stated as found."""
    out = {}
    picks = {
        "pooja": next((r for r in rooms if r["type"] == "pooja"), None),
        "kitchen": next((r for r in rooms if r["type"] == "kitchen"), None),
        "master": next((r for r in rooms if r["id"].endswith("-mbed")), None),
    }
    for key, room in picks.items():
        if room is None:
            out[key] = {"placed": False, "sector": None, "target": ANCHOR_SECTORS.get(key, ()),
                        "met": None, "detail": "not present in this unit's programme"}
            continue
        sec = mandala_zone_of(room, box)
        quadrant = sector_of(room, box)
        targets = ANCHOR_SECTORS.get(key, ())
        forbidden = ANCHOR_FORBIDDEN.get(key, ())
        met = sec in targets
        out[key] = {
            "placed": True, "sector": sec, "mandala_zone": sec, "quadrant": quadrant,
            "target": targets, "met": met,
            "forbidden_hit": sec in forbidden,
            "detail": (f"in {sec}, its primary sector" if sec == (targets[0] if targets else None)
                       else f"in {sec}, an accepted secondary sector" if met
                       else f"in {sec} — the target is {'/'.join(targets)}"),
        }
    return out


def _bounding_box_of(rooms: Sequence[dict]) -> dict:
    """A flat's box recovered from its rooms — for callers checking a stored plan."""
    xs = [r["x"] for r in rooms]
    ys = [r["y"] for r in rooms]
    x2 = [r["x"] + r["w"] for r in rooms]
    y2 = [r["y"] + r["h"] for r in rooms]
    return _rect(min(xs), min(ys), max(x2) - min(xs), max(y2) - min(ys))
