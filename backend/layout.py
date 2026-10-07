"""Procedural room-layout generator.

Every room type is governed by a schema (min/max area, min dimension, exterior-wall
requirement, privacy tier). Generation packs a unit into two bands against a modelled
exterior wall (unit-local y=0): a "front" band holding every room that requires an
exterior wall (bedrooms, living, kitchen, study) — each getting its own full-depth slice
of that wall, with an attached balcony (if any) sitting directly on the wall in front of
it — and a "rear" band holding everything else (bathrooms, closets, utility, entrance),
grouped next to whichever front room they're paired with (master bedroom's en-suite +
walk-in closet, kitchen's utility, living's entrance) so they land adjacent to it.

Because the packer is a heuristic, not a certified adjacency solver, every generated unit
is run through `validate_unit_layout` and regenerated (new seed) up to `max_attempts`
times until it satisfies every rule, or the closest attempt is returned with its
violations listed — "reject and regenerate failing layouts" from a bounded retry loop
rather than an unbounded search.

The seed is derived from (tower id, floor number, unit id, instance index, regeneration
nonce) via a stable sha256 hash, so two unit types never share a layout, the same unit
type looks different on different floors/towers, and a floor only changes when the user
explicitly clicks "Regenerate".
"""
import hashlib
import math
import random
from dataclasses import dataclass

# ---------------------------------------------------------------- room type schema
# min_area/max_area (sqm), min_dim (m, shortest side), exterior (needs an exterior wall
# — directly, or via an attached balcony that itself sits on one), privacy_tier.
ROOM_SCHEMA = {
    "living":   {"min_area": 9.5, "max_area": 32.0, "min_dim": 2.75, "exterior": True,  "privacy": "semi-private"},
    "bedroom":  {"min_area": 9.5, "max_area": 30.0, "min_dim": 2.4,  "exterior": True,  "privacy": "private"},
    "kitchen":  {"min_area": 5.5, "max_area": 14.0, "min_dim": 1.8,  "exterior": True,  "privacy": "semi-private"},
    "bathroom": {"min_area": 1.8, "max_area": 6.0,  "min_dim": 1.2,  "exterior": False, "privacy": "private"},
    "closet":   {"min_area": 2.0, "max_area": 6.0,  "min_dim": 1.2,  "exterior": False, "privacy": "private"},
    "balcony":  {"min_area": 2.2, "max_area": 10.0, "min_dim": 1.05, "exterior": True,  "privacy": "semi-private"},
    "utility":  {"min_area": 3.0, "max_area": 7.0,  "min_dim": 1.2,  "exterior": False, "privacy": "service"},
    "study":    {"min_area": 6.0, "max_area": 14.0, "min_dim": 2.1,  "exterior": False, "privacy": "private"},
    "entrance": {"min_area": 2.0, "max_area": 5.0,  "min_dim": 1.2,  "exterior": False, "privacy": "public"},
    "common":   {"min_area": 1.5, "max_area": 8.0,  "min_dim": 1.2,  "exterior": False, "privacy": "public"},
}
# Types placed against the modelled exterior wall (unit-local y=0), each on its own
# full-depth slice. Everything else (bathrooms, closets, utility, entrance) is "rear".
FRONT_TYPES = {"bedroom", "living", "kitchen"}
# Support-space types stay pinned to their schema minimum and never draw from the "extra"
# carpet-area pool — a bathroom doesn't balloon just because the unit has spare area, and
# leaving them at their minimum is what makes the master-bedroom-vs-en-suite area ratio
# (and every column's min-dimension budget) actually achievable during packing.
ATTACH_TYPES = {"bathroom", "closet", "entrance", "utility"}
MASTER_RATIO_RANGE = (1.3, 1.6)         # master bedroom vs. avg. common bedroom (validated)
COMMON_BATH_COUNT = {1: 0, 2: 1, 3: 1, 4: 2, 5: 2}   # non-master bedrooms -> shared baths


@dataclass
class RoomSpec:
    key: str
    name: str
    type: str
    weight: float
    pair_with: str = None    # anchor this room attaches to: bedroom<->closet/en-suite,
                              # kitchen<->utility, living<->entrance, any front room<->its balcony


def _seed(*parts) -> int:
    """Stable seed independent of Python's per-process hash() randomisation."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:12], 16)


def unit_mix_hash(tower: dict) -> str:
    """Fingerprint of a tower's unit mix, used to flag layouts that have gone stale."""
    units = tower.get("units") or []
    key = "|".join(f"{u.get('type')}:{u.get('count')}:{u.get('carpet_area')}" for u in units)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def reference_rooms(tower: dict) -> list:
    """The room set engineering calculations (room-area metric, column-grid clash check)
    treat as canonical for this tower — the generated floor-1 layout, falling back to
    whatever is on tower['rooms'] for older documents that predate floor_layouts."""
    fl = tower.get("floor_layouts") or {}
    ref = fl.get("1")
    if ref and ref.get("rooms"):
        return ref["rooms"]
    return tower.get("rooms") or []


def _bedroom_count(unit_type: str, carpet_area: float) -> int:
    import re
    m = re.match(r"(\d+)\s*bhk", unit_type)
    if m:
        return max(1, min(5, int(m.group(1))))
    if unit_type == "studio":
        return 0
    if unit_type == "penthouse":
        return 4
    return max(1, min(5, round((carpet_area - 28) / 22) + 1))


def room_program(unit_type: str, carpet_area: float) -> list:
    """Room list for one unit. beds >= 1 always gets a master bedroom with its own
    en-suite + walk-in closet; every other bedroom shares one or more common bathrooms
    (a different, unattached layout — never an en-suite). Balconies only ever attach to
    the master bedroom, living/dining, and (for 3BHK+) the kitchen."""
    unit_type = (unit_type or "custom").lower()
    beds = _bedroom_count(unit_type, carpet_area)
    is_penthouse = unit_type == "penthouse"
    specs = []

    if beds == 0:  # studio: no private wing, everything sits on the exterior wall
        specs.append(RoomSpec("living", "Studio Living", "living", 2.0))
        specs.append(RoomSpec("kitchen", "Kitchenette", "kitchen", 0.55))
        specs.append(RoomSpec("bath1", "Bathroom", "bathroom", 0.35, pair_with="kitchen"))
        return specs

    specs.append(RoomSpec("entrance", "Entrance / Foyer", "entrance", 0.18, pair_with="living"))
    specs.append(RoomSpec("living", "Living / Dining", "living", 1.6 if beds <= 1 else 1.35))
    specs.append(RoomSpec("kitchen", "Kitchen", "kitchen", 0.7))

    bed_names = ["Master Bedroom"] + [f"Bedroom {i}" for i in range(2, beds + 1)]
    for i, name in enumerate(bed_names, start=1):
        specs.append(RoomSpec(f"bed{i}", name, "bedroom", 1.05 if i == 1 else 0.9))

    # master's own en-suite, always; a walk-in closet between it and the bedroom too once
    # there's a second bedroom to compare it against (a dedicated closet room doesn't fit
    # a compact 1BHK's master zone alongside its en-suite without violating min widths)
    if beds >= 2:
        specs.append(RoomSpec("closet1", "Walk-in Closet", "closet", 0.15, pair_with="bed1"))
    specs.append(RoomSpec("bath1", "En-suite Bathroom", "bathroom", 0.42, pair_with="bed1"))

    # every rear item is paired to a front room's column — an unpaired item would become
    # its own lone, full-unit-depth column, which blows its area far past its schema max
    # once its width gets clamped up to a minimum. Common bathrooms round-robin across the
    # non-master bedrooms they actually serve, same as a real plan would place them.
    common_beds = [f"bed{i}" for i in range(2, beds + 1)]
    n_common = COMMON_BATH_COUNT.get(beds, COMMON_BATH_COUNT[5])
    for i in range(1, n_common + 1):
        name = "Common Bathroom" if n_common == 1 else f"Common Bathroom {i}"
        anchor = common_beds[(i - 1) % len(common_beds)] if common_beds else "bed1"
        specs.append(RoomSpec(f"cbath{i}", name, "bathroom", 0.28, pair_with=anchor))

    if beds >= 3 or is_penthouse:
        specs.append(RoomSpec("utility", "Utility", "utility", 0.3, pair_with="kitchen"))
    if is_penthouse:
        specs.append(RoomSpec("study", "Study", "study", 0.5, pair_with=common_beds[-1] if common_beds else "bed1"))

    specs.append(RoomSpec("balcony_living", "Balcony", "balcony", 0.22, pair_with="living"))
    specs.append(RoomSpec("balcony_bed1", "Balcony", "balcony", 0.16, pair_with="bed1"))
    if beds >= 3 or is_penthouse:
        specs.append(RoomSpec("balcony_kitchen", "Balcony", "balcony", 0.12, pair_with="kitchen"))

    return specs


def _allocate_areas(specs: list, carpet_area: float, rng: random.Random, ratio_boost: float = 1.0) -> dict:
    """`ratio_boost` compensates for the master bedroom's column packing several attached
    items (balcony/closet/en-suite) that a common bedroom's column doesn't carry — their
    min-dimension floors eat into its own share, so the *allocated* target has to run
    hotter than MASTER_RATIO_RANGE for the *packed* room to land inside it. The caller
    (generate_unit_layout) measures the actual packed ratio and adapts this from attempt
    to attempt rather than guessing a fixed constant."""
    by_key = {s.key: s for s in specs}
    base = sum(ROOM_SCHEMA[s.type]["min_area"] for s in specs)
    scale = min(1.0, carpet_area / base) if base > 0 else 1.0
    extra = max(0.0, carpet_area - base)
    growable = [s for s in specs if s.type not in ATTACH_TYPES]
    total_weight = sum(s.weight for s in growable) or 1.0

    # support spaces (bath/closet/entrance/utility) sit at their schema minimum; every bit
    # of "extra" carpet area goes to bedrooms/living/kitchen/balconies instead
    areas = {}
    for k, s in by_key.items():
        min_a = ROOM_SCHEMA[s.type]["min_area"] * scale
        areas[k] = min_a if s.type in ATTACH_TYPES else min_a + extra * (s.weight / total_weight)

    if "bed1" in by_key:
        common_beds = [k for k, s in by_key.items() if s.type == "bedroom" and k != "bed1"]
        if common_beds:
            avg_common = sum(areas[k] for k in common_beds) / len(common_beds)
            # cap before computing delta: otherwise a boosted-but-uncapped target claws
            # back far more area from other rooms than master can ever actually use once
            # it's clamped to its schema max, silently shrinking the whole unit.
            new_master = min(avg_common * rng.uniform(*MASTER_RATIO_RANGE) * ratio_boost,
                              ROOM_SCHEMA["bedroom"]["max_area"])
            delta = new_master - areas["bed1"]
            areas["bed1"] = new_master
            # re-derive the ratio override from the shared pool rather than adding it on
            # top: claw the delta back from (or return it to) other growable rooms,
            # proportional to their own weight, floored at their own schema minimum.
            others = [k for k, s in by_key.items() if k != "bed1" and s.type not in ATTACH_TYPES]
            other_weight = sum(by_key[k].weight for k in others) or 1.0
            for k in others:
                floor = ROOM_SCHEMA[by_key[k].type]["min_area"] * scale
                areas[k] = max(areas[k] - delta * (by_key[k].weight / other_weight), floor)

    return {k: round(min(a, ROOM_SCHEMA[by_key[k].type]["max_area"]), 2) for k, a in areas.items()}


def _clamp_spans(part_area: dict, span: float, min_dims: dict) -> dict:
    """Proportional 1-D slicing gives a small room a sliver-thin extent whenever it shares
    an axis (width OR height — this is used for both) with much larger rooms. Give every
    part at least its schema min dimension first (stealing proportionally from parts with
    slack), then split the remainder by area — the total span is preserved exactly either
    way."""
    extents = {k: span * a / (sum(part_area.values()) or 1.0) for k, a in part_area.items()}
    under = {k for k in extents if extents[k] < min_dims.get(k, 0)}
    if not under:
        return extents
    fixed = {k: min_dims[k] for k in under}
    remaining_span = span - sum(fixed.values())
    remaining_keys = [k for k in part_area if k not in under]
    remaining_area = sum(part_area[k] for k in remaining_keys) or 1.0
    if remaining_span <= 0:
        # not enough span to satisfy every minimum at once — scale every minimum down by
        # the same factor so each part gets a fair share of the shortfall instead of the
        # raw proportional split above, which can leave the smallest items near zero.
        total_min = sum(min_dims.get(k, 0) for k in part_area) or 1.0
        return {k: span * min_dims.get(k, 0) / total_min for k in part_area}
    for k in remaining_keys:
        extents[k] = remaining_span * part_area[k] / remaining_area
    extents.update(fixed)
    return extents


def _rear_group_order(anchor: str, members: list) -> list:
    """Closet sits between the bedroom and its en-suite, so it must come first (adjacent
    to the front-band boundary) with the bathroom behind it."""
    if anchor == "bed1":
        pref = [k for k in ("closet1", "bath1") if k in members]
        return pref + [k for k in members if k not in pref]
    return members


def _build_unit_layout(unit_type: str, carpet_area: float, seed: int, ratio_boost: float = 1.0):
    rng = random.Random(seed)
    carpet_area = max(float(carpet_area or 0), 18.0)
    specs = room_program(unit_type, carpet_area)
    by_key = {s.key: s for s in specs}
    areas = _allocate_areas(specs, carpet_area, rng, ratio_boost)
    total_area = sum(areas.values())

    bbox_ratio = rng.uniform(0.7, 2.0)  # wide range so retries can find a workable width for column-heavy units
    W = max(round(math.sqrt(total_area * bbox_ratio), 2), 1.0)
    H = max(round(total_area / W, 2), 1.0)

    front_keys = [s.key for s in specs if s.type in FRONT_TYPES]
    if not front_keys:  # degenerate fallback: nothing needs a private wing
        front_keys = [s.key for s in specs if s.type != "balcony"]
    balcony_of = {s.pair_with: s.key for s in specs if s.type == "balcony" and s.pair_with}
    rear_by_anchor = {}
    for s in specs:
        if s.type != "balcony" and s.key not in front_keys and s.pair_with:
            rear_by_anchor.setdefault(s.pair_with, []).append(s.key)
    unattached = [s.key for s in specs
                  if s.type != "balcony" and s.key not in front_keys and not s.pair_with]

    # One column per front room, spanning the unit's full depth H: the room (+ its
    # balcony, touching the exterior wall at y=0) stacked with whatever's paired to it
    # (en-suite/closet, utility, entrance) — guaranteeing they're adjacent by construction,
    # not by hoping two independently-sized bands happen to line up. Rooms with nothing
    # paired to them (e.g. a non-master bedroom) simply get the column's full depth.
    # Unpaired items (shared/common bathrooms) become their own single-member columns.
    columns = {}
    for key in front_keys:
        members = ([(balcony_of[key], areas[balcony_of[key]])] if key in balcony_of else []) + [(key, areas[key])]
        members += [(k, areas[k]) for k in _rear_group_order(key, rear_by_anchor.get(key, []))]
        columns[key] = members
    for key in unattached:
        columns[key] = [(key, areas[key])]

    col_area = {k: sum(a for _, a in m) for k, m in columns.items()}
    col_min_w = {k: max(ROOM_SCHEMA[by_key[sk].type]["min_dim"] for sk, _ in m) for k, m in columns.items()}
    col_widths = _clamp_spans(col_area, W, col_min_w)

    order = list(columns)
    rng.shuffle(order)

    rooms = []

    def emit(key, rect):
        s = by_key[key]
        rx, ry, rw, rh = rect
        # Round the four edges (not width/height independently) so two rooms sharing a
        # boundary always round to the identical value on both sides.
        x0, y0 = round(rx, 2), round(ry, 2)
        x1, y1 = round(rx + rw, 2), round(ry + rh, 2)
        rooms.append({"id": key, "name": s.name, "type": s.type,
                       "x": x0, "y": y0, "w": round(x1 - x0, 2), "h": round(y1 - y0, 2)})

    cursor = 0.0
    for key in order:
        w_i = col_widths[key]
        members = columns[key]
        sub_area = dict(members)
        sub_min_h = {sk: ROOM_SCHEMA[by_key[sk].type]["min_dim"] for sk, _ in members}
        sub_heights = _clamp_spans(sub_area, H, sub_min_h)
        y = 0.0
        for subkey, _ in members:  # keep the fixed stacking order (balcony, room, closet, bath...)
            sh = sub_heights[subkey]
            emit(subkey, (cursor, y, w_i, sh))
            y += sh
        cursor += w_i

    return rooms, W, H


def validate_unit_layout(rooms: list) -> list:
    """Every rule from the room-type schema and adjacency spec, checked geometrically.
    Returns a list of human-readable violations (empty = fully valid)."""
    violations = []
    by_id = {r["id"]: r for r in rooms}
    EPS = 0.03

    def touches(a, b):
        ax, ay, aw, ah = a["x"], a["y"], a["w"], a["h"]
        bx, by_, bw, bh = b["x"], b["y"], b["w"], b["h"]
        vert_touch = abs((ax + aw) - bx) < EPS or abs((bx + bw) - ax) < EPS
        vert_overlap = ay < by_ + bh - EPS and by_ < ay + ah - EPS
        horiz_touch = abs((ay + ah) - by_) < EPS or abs((by_ + bh) - ay) < EPS
        horiz_overlap = ax < bx + bw - EPS and bx < ax + aw - EPS
        return (vert_touch and vert_overlap) or (horiz_touch and horiz_overlap)

    def on_exterior(r):
        return r["y"] < EPS  # unit-local y=0 is the modelled exterior wall

    ids = list(by_id)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = by_id[ids[i]], by_id[ids[j]]
            ax, ay, aw, ah = a["x"], a["y"], a["w"], a["h"]
            bx, by_, bw, bh = b["x"], b["y"], b["w"], b["h"]
            if not (ax + aw <= bx + 1e-6 or bx + bw <= ax + 1e-6 or ay + ah <= by_ + 1e-6 or by_ + bh <= ay + 1e-6):
                violations.append(f"overlap: {a['id']} x {b['id']}")

    for r in rooms:
        schema = ROOM_SCHEMA.get(r["type"])
        if not schema:
            continue
        area = r["w"] * r["h"]
        if area < schema["min_area"] - 0.3:
            violations.append(f"{r['id']}: {area:.1f} sqm below schema minimum {schema['min_area']}")
        elif area > schema["max_area"] + 0.3:
            violations.append(f"{r['id']}: {area:.1f} sqm above schema maximum {schema['max_area']}")
        if min(r["w"], r["h"]) < schema["min_dim"] - 0.15:
            violations.append(f"{r['id']}: narrowest side below minimum dimension {schema['min_dim']} m")

    balcony_ext = {r["id"] for r in rooms if r["type"] == "balcony" and on_exterior(r)}
    for r in rooms:
        if not ROOM_SCHEMA.get(r["type"], {}).get("exterior"):
            continue
        if on_exterior(r) or any(touches(r, by_id[bid]) for bid in balcony_ext):
            continue
        violations.append(f"{r['id']}: habitable room has no exterior wall")

    for r in rooms:
        if r["type"] != "balcony":
            continue
        if not on_exterior(r):
            violations.append(f"{r['id']}: balcony is not on a verified exterior wall")

    master, ensuite, closet = by_id.get("bed1"), by_id.get("bath1"), by_id.get("closet1")
    common_beds = [r for r in rooms if r["type"] == "bedroom" and r["id"] != "bed1"]
    if master and common_beds:
        avg = sum(r["w"] * r["h"] for r in common_beds) / len(common_beds)
        ratio = (master["w"] * master["h"]) / avg if avg else 0
        if not (MASTER_RATIO_RANGE[0] - 0.05 <= ratio <= MASTER_RATIO_RANGE[1] + 0.05):
            violations.append(f"master bedroom is {ratio:.2f}x common bedrooms, outside 1.3-1.6x")
    if master and ensuite:
        # "Adjacent" allows the walk-in closet to sit between them (that's the spec, not
        # a violation of it) — a direct touch is only required when there's no closet.
        via_closet = bool(closet) and touches(master, closet) and touches(closet, ensuite)
        if not (touches(master, ensuite) or via_closet):
            violations.append("master bedroom is not adjacent to its en-suite")
    common_baths = [r for r in rooms if r["type"] == "bathroom" and r["id"] != "bath1"]
    if ensuite and common_baths and ensuite["w"] * ensuite["h"] <= min(r["w"] * r["h"] for r in common_baths):
        violations.append("en-suite bathroom is not larger than the common bathrooms")
    if master and closet and ensuite and not (touches(master, closet) and touches(closet, ensuite)):
        violations.append("walk-in closet is not between the master bedroom and its en-suite")

    kitchen, living, entrance, utility = by_id.get("kitchen"), by_id.get("living"), by_id.get("entrance"), by_id.get("utility")
    if kitchen and living and not touches(kitchen, living):
        violations.append("kitchen is not adjacent to living/dining")
    if living and entrance and not touches(living, entrance):
        violations.append("living room is not adjacent to the entrance")
    if utility and kitchen and not touches(utility, kitchen):
        violations.append("utility is not adjacent to the kitchen")
    for r in rooms:
        if r["type"] != "bedroom":
            continue
        if kitchen and touches(r, kitchen):
            violations.append(f"{r['id']} is adjacent to the kitchen (bedrooms must cluster away from it)")
        if entrance and touches(r, entrance):
            violations.append(f"{r['id']} is adjacent to the entrance (bedrooms must cluster away from it)")

    return violations


def generate_unit_layout(unit_type: str, carpet_area: float, seed: int, max_attempts: int = 25):
    """Returns (rooms, width, height, violations). Retries with a reseeded layout up to
    `max_attempts` times, keeping the closest attempt if none validate cleanly. `boost`
    is a simple proportional controller: if the previous attempt's packed master-bedroom
    ratio missed MASTER_RATIO_RANGE, scale the next attempt's allocation target by how far
    off it was, rather than re-rolling blind and hoping a new seed happens to land closer."""
    best = None
    boost = 1.0
    target_mid = MASTER_RATIO_RANGE[1]  # aim high: packing tends to undershoot the target, not overshoot it
    for attempt in range(max_attempts):
        rooms, w, h = _build_unit_layout(unit_type, carpet_area, seed + attempt * 104729, boost)
        violations = validate_unit_layout(rooms)
        if not violations:
            return rooms, w, h, []

        by_id = {r["id"]: r for r in rooms}
        master = by_id.get("bed1")
        common = [r for r in rooms if r["type"] == "bedroom" and r["id"] != "bed1"]
        if master and common:
            avg = sum(r["w"] * r["h"] for r in common) / len(common)
            actual_ratio = (master["w"] * master["h"]) / avg if avg else 0
            if actual_ratio > 0:
                boost *= max(0.5, min(2.5, target_mid / actual_ratio))

        if best is None or len(violations) < len(best[3]):
            best = (rooms, w, h, violations)
    return best


def generate_floor_layout(tower: dict, floor: int, nonce: int = 0):
    """Assembles a typical floor plate: every unit on the floor (per the tower's unit mix)
    gets its own generated + validated layout, placed in two rows either side of a
    corridor sized from the tower's own corridor_width — each row's exterior wall facing
    outward, away from the corridor. Different floors/towers reseed via `floor`/tower id.
    Returns (rooms, validation) where validation maps unit_id -> remaining violations for
    any unit that didn't fully clear generate_unit_layout's retry budget."""
    units = tower.get("units") or []
    tower_id = tower.get("id", "tower")
    corridor_w = max(float(tower.get("corridor_width") or 1.8), 1.2)
    gap = 0.25

    instances, idx = [], 0
    for u in units:
        u_type = str(u.get("type") or "custom").lower()
        carpet = float(u.get("carpet_area") or 60)
        for _ in range(max(int(u.get("count") or 0), 0)):
            # unit_id must be unique per *instance*, not per unit-type row — a "count: 2"
            # row otherwise hands both copies the same id, colliding their room ids and
            # merging two separate units under one dashed outline/label in the renderer.
            instances.append({"unit_id": f"{u.get('id') or 'u'}-{idx}", "type": u_type, "carpet": carpet, "index": idx})
            idx += 1
    if not instances:
        return [], {}

    generated, validation = [], {}
    for inst in instances:
        seed = _seed(tower_id, floor, inst["unit_id"], inst["index"], nonce)
        rooms, w, h, violations = generate_unit_layout(inst["type"], inst["carpet"], seed)
        generated.append({**inst, "rooms": rooms, "w": w, "h": h})
        if violations:
            validation[inst["unit_id"]] = violations

    mid = (len(generated) + 1) // 2
    top_row, bottom_row = generated[:mid], generated[mid:]
    rooms_out = []

    def place_row(row, y0, flip):
        # flip=True mirrors the row's local y-axis so its exterior wall (local y=0) faces
        # outward, away from the corridor, instead of toward it.
        x = 0.0
        for g in row:
            for r in g["rooms"]:
                ry = r["y"] if not flip else (g["h"] - r["y"] - r["h"])
                rooms_out.append({
                    "id": f"{g['unit_id']}-{r['id']}", "name": r["name"], "type": r["type"],
                    "x": round(x + r["x"], 2), "y": round(y0 + ry, 2), "w": r["w"], "h": r["h"],
                    "unit_id": g["unit_id"], "unit_type": g["type"], "unit_index": g["index"],
                })
            x += g["w"] + gap
        return max(x - gap, 0.0)

    top_w = place_row(top_row, 0.0, flip=False)
    top_h = max((g["h"] for g in top_row), default=0.0)
    corridor_y = top_h
    bottom_w = place_row(bottom_row, corridor_y + corridor_w, flip=True) if bottom_row else 0.0

    if bottom_row:
        floor_w = round(max(top_w, bottom_w), 2)
        rooms_out.append({"id": f"corridor-{floor}", "name": "Corridor", "type": "common",
                           "x": 0.0, "y": round(corridor_y, 2), "w": floor_w, "h": round(corridor_w, 2)})

    return rooms_out, validation
