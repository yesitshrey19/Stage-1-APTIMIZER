"""Machine-readable design rules distilled from the user-supplied planning guide.

This module is the shared rule source for the floor-plan prompt, deterministic planner,
and post-generation audit. Project-specific tower inputs still take precedence over the
guide's default tower cycle.
"""

GUIDE_TITLE = "Universal Architectural & Vastu Master Planning Guide"

ROOM_ZONE_PREFERENCES = {
    "pooja": ("NE",),
    "kitchen": ("SE",),
    "master": ("SW",),
    "living": ("N", "E"),
    "dining": ("W", "N"),
    "bedroom": ("NW", "W", "S", "E"),
    "bathroom": ("NW", "W", "S"),
    "utility": ("SE", "NW"),
    "shaft": ("W", "NW", "SW"),
    "office": ("S", "W"),
    "balcony": ("NE", "N", "E", "NW", "W"),
    "terrace": ("NE", "N", "E", "W", "S"),
    "passage": ("CENTER", "S", "N"),
    "entrance": ("N", "NE", "E", "CENTER"),
}

# These rooms must never occupy the northeast or the open central Brahmasthan.
FORBIDDEN_ROOM_TYPES_BY_ZONE = {
    # The master bedroom belongs in the SW; the NE (Ishanya) is the one sector it may never take.
    "NE": {"bathroom", "kitchen", "shaft", "utility", "storage", "servant", "master"},
    "CENTER": {"bedroom", "bathroom", "kitchen", "shaft", "closet", "storage", "utility", "servant"},
}

WET_ROOM_NAME_MARKERS = ("bath", "toilet", "wc", "powder")

DOOR_STANDARDS_MM = {
    "main_entrance": {"width_min": 1050, "width_max": 1200, "height": 2400},
    "bedroom": {"width": 900, "height": 2100},
    "kitchen": {"width": 800, "height": 2100},
    "bathroom": {"width": 750, "height": 2100},
    "other": {"width": 900, "height": 2100},
    "hinge_offset_min": 100,
    "hinge_offset_max": 150,
    "swing": "inward_clockwise",
}

BALCONY_RULES = {
    "living_north_east_depth_min_mm": 1800,
    "living_north_east_depth_max_mm": 2400,
    "secondary_depth_min_mm": 1200,
    "secondary_depth_max_mm": 1500,
    "master_depth_mm": 1200,
    "north_east_slab_drop_min_mm": 12,
    "north_east_slab_drop_max_mm": 25,
    "south_west_masonry_parapet_height_mm": 1000,
}

ENTRY_PADA_RULES = {
    "N": (3, 4, 5),
    "E": (3, 4),
    "S": (4,),
}


def mandala_zone_of(room, box):
    """Return a room centroid's 3x3 Mandala zone (north at the top, east at right)."""
    width = float(box.get("w") or 0)
    height = float(box.get("h") or 0)
    if width <= 0 or height <= 0:
        return None
    cx = float(room.get("x") or 0) + float(room.get("w") or 0) / 2
    cy = float(room.get("y") or 0) + float(room.get("h") or 0) / 2
    col = min(2, max(0, int((cx - float(box.get("x") or 0)) / width * 3)))
    row = min(2, max(0, int((cy - float(box.get("y") or 0)) / height * 3)))
    return (
        ("NW", "N", "NE"),
        ("W", "CENTER", "E"),
        ("SW", "S", "SE"),
    )[row][col]


def room_zone_kind(room):
    """Map generator-specific room labels to the guide's planning categories."""
    room_type = str(room.get("type") or "").strip().lower()
    name = str(room.get("name") or "").strip().lower()
    room_id = str(room.get("id") or "").strip().lower()
    if room_type == "pooja" or "pooja" in name or "puja" in name:
        return "pooja"
    if room_type == "kitchen" or "kitchen" in name:
        return "kitchen"
    if room_type == "bathroom" or any(marker in name for marker in WET_ROOM_NAME_MARKERS):
        return "bathroom"
    if room_type == "bedroom":
        if "master" in name or "grand master" in name or room_id.endswith("-mbed"):
            return "master"
        return "bedroom"
    if room_type in ("closet", "pantry") or any(word in name for word in ("storage", "closet", "pantry")):
        return "storage"
    if room_type == "utility" or "utility" in name:
        return "utility"
    if room_type in ("shaft", "duct") or "shaft" in name or "duct" in name:
        return "shaft"
    if room_type in ("office", "study"):
        return "office"
    if room_type == "servant" or "servant" in name:
        return "servant"
    if room_type in ("balcony", "terrace"):
        return "balcony"
    if room_type in ("living", "dining", "family"):
        return "living" if room_type != "dining" else "dining"
    if room_type in ("passage", "entrance", "foyer", "common"):
        return "passage" if room_type != "entrance" else "entrance"
    return room_type


def audit_room_zones(rooms, box):
    """Report forbidden-zone breaches and preferred 3x3 Mandala placements."""
    placements = []
    violations = []
    preference_misses = 0
    for room in rooms:
        kind = room_zone_kind(room)
        zone = mandala_zone_of(room, box)
        if not zone:
            continue
        forbidden = sorted(FORBIDDEN_ROOM_TYPES_BY_ZONE.get(zone, set()))
        if kind in forbidden:
            violations.append(
                f"{room.get('id', room.get('name', 'room'))}: {kind} is not allowed in {zone}"
            )
        targets = ROOM_ZONE_PREFERENCES.get(kind, ())
        met = zone in targets if targets else None
        if targets and not met:
            preference_misses += 1
        placements.append({
            "id": room.get("id"), "name": room.get("name"), "kind": kind,
            "zone": zone, "preferred_zones": targets, "met": met,
        })
    return {
        "placements": placements,
        "violations": violations,
        "preference_misses": preference_misses,
    }

MASTER_PLANNING_SYSTEM_RULES = f"""\
Design every tower floor using the user's project data and the {GUIDE_TITLE}.

PROJECT TOWER AND PARKING DEFAULTS
- The default tower cycle repeats one type per tower: Tower A/F 1 BHK at 8 homes per
  floor; B/G 2 BHK at 6; C/H 3 BHK at 4; D/I 4 BHK at 2; E/J 5 BHK at 1. Continue the
  same five-type cycle for later tower letters. The project's saved tower unit mix is
  authoritative when it differs. Never mix apartment types inside a tower and never
  silently turn a 3 or 4 BHK top floor into a different unit type.
- Parking belongs underground in the tower basement and must not be drawn in a residential
  floor plate. Defaults are: 1 BHK has no included reserved bay and may use optional
  purchased pool spaces; 2 BHK has 1 car; 3 BHK has 1 car and 1 two-wheeler; 4 BHK has
  2 cars and 1 two-wheeler; 5 BHK has equivalent parking area for either 3 cars + 2 bikes
  or 4 cars + 0 bikes. Use the project's saved parking choices if edited.

THE 3 BY 3 VASTU PURUSHA MANDALA (NORTH AT THE TOP, EAST AT THE RIGHT)
- NW / Vayu: guest or secondary bedrooms, children study, secondary toilets and utility.
- N / Kubera: main foyer, living and lounge; keep the zone bright and open.
- NE / Ishanya: pooja or meditation nook and an open corner balcony. No toilet, bathroom,
  kitchen, cooking hob, or heavy/service room may occupy the NE zone.
- W / Varuna: family dining, children's bedroom, shared plumbing duct and service risers.
- CENTER / Brahmasthan: keep open for circulation. Do not place columns, shafts, toilets,
  kitchens, bedrooms, storage or heavy masonry here.
- E / Surya: breakfast, living, sit-outs and the main daylight balcony.
- SW / Nairutya: primary master suite and wardrobes; this is the heaviest residential zone.
- S / Yama: secondary master suite, office/library, heavy storage and service passage.
- SE / Agni: kitchen and hob, with the cook facing East; keep the fixed kitchen wet core in
  this sector on every floor. Put its dry utility beside it.

UNIT SCALING AND STACKS
- Preserve the project unit type, count per floor, carpet area and tower tier exactly.
- 1 BHK: living/dining, one master bedroom and ensuite, optional guest powder room, living
  balcony, kitchen dry utility, and pooja niche.
- 2 BHK: living/dining, SW master with ensuite, NW secondary bedroom and common bath,
  living balcony, kitchen utility and a pooja room/niche.
- 3 BHK: SW master with ensuite, NW second bedroom, third bedroom toward South/West,
  common bath, living and master balconies, large utility and dedicated pooja room.
- 4 BHK: SW grand master/dresser, secondary bedrooms toward NW/S/N, three ensuites plus a
  guest common bath, servant room and bath with service access, living/master/sub-master
  balconies, large utility and dedicated pooja.
- 5 BHK: full-floor home with SW presidential suite, bedrooms toward S/W/NW/E, four
  ensuites, common and powder rooms, servant quarters, family lounge, pantry, office and
  wrap-around terraces. Keep each floor's wet cores aligned vertically. Kitchen stacks
  stay SE; shared risers stay on W, with 5 BHK perimeter shafts at SW/NW.
- Put the heaviest masonry on South and West exterior edges. Keep the central hall free of
  structural columns and heavy walls. Do not invent impossible frontage: flag any requested
  room or Vastu target that cannot fit the provided envelope.

DOORS, WINDOWS AND BALCONIES
- Put the main entry on N, NE or E where the actual corridor/access permits. North entries
  use padas 3, 4 or 5; East uses 3 or 4; an unavoidable South entry uses Pada 4 only and
  never SW. Add a vestibule buffer at a South entry.
- Main entry door: 1050-1200 mm wide x 2400 mm high. Bedroom door: 900 x 2100 mm;
  kitchen: 800 x 2100 mm; bathroom: 750 x 2100 mm. Hinges sit 100-150 mm from the nearest
  perpendicular wall. All room doors swing inward clockwise. Do not expose bed pillows in
  the sightline from an opened bedroom door. Shield bathroom doors from living and kitchen.
- Keep habitable rooms daylit and naturally ventilated. Give rooms on two exterior walls
  openings on adjacent/opposing sides.
- Living balcony on N/E: 1800-2400 mm deep, light frameless glass, slab dropped 12-25 mm.
  Master sit-out: 1200 mm deep and enclosed/lightly screened. Secondary balconies: 1200-
  1500 mm deep with shade/fins. South/West balconies need a 1000 mm solid masonry parapet.
  Kitchen dry utility: 1200-1500 mm wide, semi-enclosed and ventilated.

OUTPUT CONTRACT
- Use metres for x/y/w/h and return one JSON object with a `rooms` array only.
- Every room needs a stable id, name, type, unit_id, unit_type, unit_index and non-overlapping
  positive x/y/w/h. Include `main_entrance`, `entry_edge`, `entry_pada`, `door_to` or
  `door_child_of`, `door_width_mm`, `door_height_mm`, `hinge_offset_mm`, `door_swing`,
  `has_window`, `balcony_attached`, `balcony_depth_mm`, `parapet_type`, and `slab_drop_mm`
  where they apply. Coordinate origin is the upper-left; x increases East and y increases
  South. Do not claim Vastu compliance: the geometry is checked after you return it.
"""
