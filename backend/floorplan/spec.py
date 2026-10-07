"""The single constant surface for the floor-plan package.

Every other module in ``floorplan/`` reads its numbers from here and declares none of its
own. That is the whole point: the rule book states several of its thresholds twice with
different values (facade per habitable room is 3000 in rule 1.1 and 2500/3000 in rule 8.5;
circulation is 8-12% in one sentence of rule 4.2 and >15% in the next), and if two modules
each pick a reading, the engine enforces two different rule books and neither is the one in
the document. Resolving those once, here, with the reasoning written down, is the only way
a later reader can tell a decision from an accident.

Three things this file is careful about, because they are the ways a spec module lies:

1. IT NEVER INVENTS A CODE NUMBER. Section 5.2 of the rule book is marked ``[verify all]``.
   The four figures this platform already publishes as NBC/IS numbers live in
   ``iscodes._ROOM_NBC_2016``; everything else there is ``UNREAD``. When a resolver meets an
   UNREAD code value it substitutes the rule book's design target and labels the result
   ``binding="design", verified=False`` with a note saying so in prose. A design target is
   an architect's opinion; a code minimum is a legal floor; a number that cannot say which
   it is has no business in a compliance report.

2. IT NEVER MERGES A HARD RULE WITH A SOFT ONE. ``HARD_RULE_TEXT`` (the fifteen 11.1
   rejects) and ``SOFT_WEIGHTS`` (the nine 11.2 fitness terms) are separate objects with no
   arithmetic between them, and the vastu tiebreak sits outside both.

3. IT WORKS IN INTEGER MILLIMETRES. ``vastu.py`` needs ``EPS = 0.03`` (30 mm) to make
   float-metre comparisons behave, and that tolerance is LARGER than the rule book's
   100-300 mm door corner offset (7.3), its 150 mm wall-to-grid tolerance (10.1) and its
   300 mm facing-door offset (7.8). A tolerance that swallows the rule cannot check the
   rule. Metres appear only at the public API boundary.

Import-time self-checks at the bottom of the file are real rejections, not decoration: a
bad edit fails collection rather than a request.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Mapping, Optional, Sequence

import iscodes  # backend/ is the import root; aifloorplan does `import vastu` the same way


# =====================================================================================
# A. The unit boundary
# =====================================================================================
# Internal unit is the integer millimetre; metres exist only at the public API boundary.
# The conversion helpers come in signed pairs because of one rule that is easy to state and
# fatal to forget:
#
#     SUPPLY QUANTITIES FLOOR. DEMAND QUANTITIES CEIL.
#
# `facade_available` and `envelope_area` are supply and round DOWN; `facade_required` and
# `required_area` are demand and round UP. A sub-millimetre rounding artefact must never be
# able to turn a fail into a pass, and the gate is the one place where it silently could.

MM_PER_M: int = 1000
MM2_PER_M2: int = 1_000_000


def m_to_mm(v: float) -> int:
    """Metres to millimetres, rounding half AWAY FROM ZERO.

    Python's ``round`` is banker's rounding, so ``round(2.5)`` is 2 and ``round(3.5)`` is 4.
    Two dimensions that differ by nothing would then convert to values that differ by one,
    which is exactly the kind of asymmetry that makes a tiling fail to close.
    """
    x = float(v) * MM_PER_M
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def m_to_mm_floor(v: float) -> int:
    """Metres to millimetres for a SUPPLY quantity — never claims more than exists."""
    return int(math.floor(float(v) * MM_PER_M))


def m_to_mm_ceil(v: float) -> int:
    """Metres to millimetres for a DEMAND quantity — never asks for less than required."""
    return int(math.ceil(float(v) * MM_PER_M))


def mm_to_m(v: int) -> float:
    """Millimetres back to metres for the API boundary. Three decimals is exact for mm."""
    return round(int(v) / float(MM_PER_M), 3)


def sqm_to_mm2(v: float) -> int:
    return int(round(float(v) * MM2_PER_M2))


def sqm_to_mm2_ceil(v: float) -> int:
    """Square metres to mm2 for a DEMAND quantity (a minimum area a room must reach)."""
    return int(math.ceil(float(v) * MM2_PER_M2))


def mm2_to_sqm(v: int) -> float:
    return round(int(v) / float(MM2_PER_M2), 3)


# An engine choice, NOT a code value. IS 3861 has a basic module of its own which this
# repository has not read, so this is not it and must not be reported as it. It exists so
# the genetic search cannot emit a 2437.3 mm room: a setting-out dimension a site engineer
# cannot mark is a drawing error whatever its fitness score says.
SETTING_OUT_MODULE_MM: int = 25


def snap(v: int, module: int = SETTING_OUT_MODULE_MM) -> int:
    """Nearest module. Use where the value is neither a supply nor a demand quantity."""
    return int(round(int(v) / module)) * module


def snap_up(v: int, module: int = SETTING_OUT_MODULE_MM) -> int:
    """Round up to the module — for anything that must not shrink below its own minimum."""
    return int(math.ceil(int(v) / module)) * module


def snap_down(v: int, module: int = SETTING_OUT_MODULE_MM) -> int:
    """Round down to the module — for anything that must not exceed a cap."""
    return int(math.floor(int(v) / module)) * module


# =====================================================================================
# A2. Shared geometry primitive
# =====================================================================================
# Axes match vastu.py and FloorPlate.jsx: +x is East, -y is North, y grows going South.
Edge = Literal["N", "S", "E", "W"]
EDGES: tuple[Edge, ...] = ("N", "E", "S", "W")
OPPOSITE_EDGE: dict[Edge, Edge] = {"N": "S", "S": "N", "E": "W", "W": "E"}


@dataclass(frozen=True, slots=True)
class RectMM:
    """Axis-aligned rectangle in integer millimetres. THE geometry primitive of the package.

    Every floorplan module imports this rather than defining its own. If two modules define
    two rectangle types they will silently coexist, every cross-module call will convert,
    and the conversions are where the millimetre discipline leaks back into floats.

    Zero-extent rectangles are rejected in ``__post_init__`` rather than tolerated. A
    zero-width room is not a degenerate room, it is a bug that has travelled: it passes an
    area check with area 0 only if the check is written wrong, and it produces an infinite
    aspect ratio that a max() will happily propagate to the top of a report.
    """

    x: int
    y: int
    w: int
    h: int

    def __post_init__(self) -> None:
        for name in ("x", "y", "w", "h"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError(
                    f"RectMM.{name} must be an int in millimetres, got {type(v).__name__}. "
                    "Convert at the API boundary with rect_from_metres(), not here.")
        if self.w <= 0 or self.h <= 0:
            raise ValueError(f"RectMM must have positive extents, got w={self.w} h={self.h}")

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def area_mm2(self) -> int:
        return self.w * self.h

    @property
    def long_mm(self) -> int:
        return max(self.w, self.h)

    @property
    def short_mm(self) -> int:
        return min(self.w, self.h)

    @property
    def aspect(self) -> float:
        """Rule 5.1: longer clear dimension divided by shorter. Always >= 1.0."""
        return self.long_mm / self.short_mm

    def edge_length_mm(self, edge: Edge) -> int:
        """Length of one named edge. N and S run east-west, E and W run north-south."""
        if edge in ("N", "S"):
            return self.w
        if edge in ("E", "W"):
            return self.h
        raise ValueError(f"unknown edge {edge!r}; expected one of {EDGES}")

    def to_metres(self) -> dict[str, float]:
        """API boundary only. Never round-trip through this mid-algorithm."""
        return {"x": mm_to_m(self.x), "y": mm_to_m(self.y),
                "w": mm_to_m(self.w), "h": mm_to_m(self.h)}


def rect_from_metres(x: float, y: float, w: float, h: float) -> RectMM:
    """Metres in, millimetres out. Extents floor (supply); origins round to nearest.

    The extents are a supply quantity — the space a rectangle actually offers — so they
    floor. The origins are positions, not quantities, so they take the nearest millimetre.
    """
    return RectMM(m_to_mm(x), m_to_mm(y), m_to_mm_floor(w), m_to_mm_floor(h))


# =====================================================================================
# A3. Room vocabulary
# =====================================================================================
class RoomType(StrEnum):
    FOYER = "foyer"
    LIVING = "living"
    DINING = "dining"
    LIVING_DINING = "living_dining"      # open-plan; counts as ONE facade-needing room
    KITCHEN = "kitchen"
    UTILITY = "utility"
    MASTER_BEDROOM = "master_bedroom"
    BEDROOM = "bedroom"
    ATTACHED_TOILET = "attached_toilet"
    COMMON_TOILET = "common_toilet"
    BATH = "bath"                        # bath only, no WC
    WC = "wc"                            # WC only
    DRESSING = "dressing"
    PUJA = "puja"
    BALCONY = "balcony"
    CORRIDOR = "corridor"
    SHAFT = "shaft"
    STORE = "store"


# Rule 8.1 — the rooms that must have an external wall or a code-compliant shaft. Toilets
# are NOT here: rule 8.3 governs them with a ventilation opening, which is a different and
# weaker requirement, and folding the two together would either over-constrain toilets or
# let a bedroom pass on a 0.3 m2 vent.
HABITABLE: frozenset[RoomType] = frozenset({
    RoomType.LIVING, RoomType.DINING, RoomType.LIVING_DINING, RoomType.KITCHEN,
    RoomType.MASTER_BEDROOM, RoomType.BEDROOM,
})
SLEEPING: frozenset[RoomType] = frozenset({RoomType.MASTER_BEDROOM, RoomType.BEDROOM})
WET: frozenset[RoomType] = frozenset({
    RoomType.ATTACHED_TOILET, RoomType.COMMON_TOILET, RoomType.BATH, RoomType.WC,
    RoomType.KITCHEN, RoomType.UTILITY,
})
TOILETS: frozenset[RoomType] = frozenset({
    RoomType.ATTACHED_TOILET, RoomType.COMMON_TOILET, RoomType.BATH, RoomType.WC,
})
# Rule 7.1 — one door per room, except these. A bedroom with two doors is H14.
THROUGH_SPACES: frozenset[RoomType] = frozenset({
    RoomType.LIVING, RoomType.DINING, RoomType.LIVING_DINING, RoomType.FOYER,
    RoomType.CORRIDOR,
})
# Rule 9.5 — the kitchen, and ONLY the kitchen, may take a dedicated exhaust shaft in place
# of an external wall. No bedroom is ever talked out of its window: this frozenset is what
# stops a future "relief valve" from quietly extending the exemption to one.
FACADE_EXEMPT_WITH_SHAFT: frozenset[RoomType] = frozenset({RoomType.KITCHEN})

# The renderer (FloorPlate.jsx) colours by `type` from a fixed COLORS map and knows only the
# legacy vocabulary. Conversion happens once, on the way out, in floorplan/__init__.py.
# Every value here is a real key of that COLORS map — a value that is not falls back to
# white and looks like a rendering bug rather than a naming one.
LEGACY_TYPE: dict[RoomType, str] = {
    RoomType.FOYER: "entrance",
    RoomType.LIVING: "living",
    RoomType.DINING: "living",
    RoomType.LIVING_DINING: "living",
    RoomType.KITCHEN: "kitchen",
    RoomType.UTILITY: "utility",
    RoomType.MASTER_BEDROOM: "bedroom",
    RoomType.BEDROOM: "bedroom",
    RoomType.ATTACHED_TOILET: "bathroom",
    RoomType.COMMON_TOILET: "bathroom",
    RoomType.BATH: "bathroom",
    RoomType.WC: "bathroom",
    RoomType.DRESSING: "closet",
    RoomType.PUJA: "pooja",
    RoomType.BALCONY: "balcony",
    RoomType.CORRIDOR: "passage",
    # FloorPlate.jsx has no "store" colour. "pantry" is the closest key it does have, and a
    # store in these programmes IS the penthouse pantry, so the swatch is honest.
    RoomType.STORE: "pantry",
    RoomType.SHAFT: "shaft",
}


def legacy_type(rt: RoomType) -> str:
    return LEGACY_TYPE[RoomType(rt)]


# =====================================================================================
# B. Zoning (rule book section 2)
# =====================================================================================
class Zone(StrEnum):
    PUBLIC = "public"
    SEMI_PRIVATE = "semi_private"
    PRIVATE = "private"
    SERVICE = "service"


ZONE_OF_ROOM: dict[RoomType, Zone] = {
    RoomType.FOYER: Zone.PUBLIC,
    RoomType.LIVING: Zone.PUBLIC,
    RoomType.LIVING_DINING: Zone.PUBLIC,
    RoomType.DINING: Zone.SEMI_PRIVATE,
    RoomType.COMMON_TOILET: Zone.SEMI_PRIVATE,
    RoomType.BATH: Zone.SEMI_PRIVATE,
    RoomType.WC: Zone.SEMI_PRIVATE,
    RoomType.PUJA: Zone.SEMI_PRIVATE,
    RoomType.CORRIDOR: Zone.SEMI_PRIVATE,
    RoomType.MASTER_BEDROOM: Zone.PRIVATE,
    RoomType.BEDROOM: Zone.PRIVATE,
    RoomType.ATTACHED_TOILET: Zone.PRIVATE,
    RoomType.DRESSING: Zone.PRIVATE,
    RoomType.KITCHEN: Zone.SERVICE,
    RoomType.UTILITY: Zone.SERVICE,
    # The rule book's zone table names a "drying balcony" under Service and gives no zone
    # for a living balcony. Service is the right home for both, because it is the one zone
    # rule 2.3's monotonic ordering excludes — and a balcony sits at whatever depth its
    # parent room does, so a master-bedroom balcony would otherwise break the ordering
    # merely by existing. Same argument for the shaft and the store.
    RoomType.BALCONY: Zone.SERVICE,
    RoomType.SHAFT: Zone.SERVICE,
    RoomType.STORE: Zone.SERVICE,
}

# Rule 2.3 — the H11 axis. SERVICE is deliberately not in the sequence: rule 2.2 constrains
# it by adjacency (must touch semi-private, must not touch public at the entrance), not by
# position along the entry axis, and wedging it into the ordering would invent a rule.
ZONE_ORDER: tuple[Zone, ...] = (Zone.PUBLIC, Zone.SEMI_PRIVATE, Zone.PRIVATE)
# The corridor is the one room whose whole job is to span the gradient, so it cannot be
# tested for position within it. Named here so validate.py's H11 cannot quietly decide the
# same thing on its own and circulation.py decide the opposite.
ZONE_ORDER_EXEMPT: frozenset[RoomType] = frozenset({RoomType.CORRIDOR})
PUBLIC_ZONE_DEPTH_FRACTION: float = 0.30      # rule 2, "first 30% of unit depth"
SERVICE_MUST_TOUCH: frozenset[Zone] = frozenset({Zone.SEMI_PRIVATE})      # rule 2.2
SERVICE_MUST_NOT_TOUCH: frozenset[Zone] = frozenset({Zone.PUBLIC})        # rule 2.2


def zone_of(room_type: RoomType) -> Zone:
    return ZONE_OF_ROOM[RoomType(room_type)]


# =====================================================================================
# C. Design targets (rule book 5.2), and the code/design resolver
# =====================================================================================
# Rule book section 5.2 is marked [verify all], so it is split in two rather than adopted
# whole. Anything a building approval is actually checked against is a CODE MINIMUM and
# lives in iscodes; anything that is the rule book's own architectural opinion is a DESIGN
# TARGET and lives here. The split is not arbitrary: no Indian code distinguishes a master
# bedroom from a secondary bedroom, so those two rows cannot be code by construction.

@dataclass(frozen=True, slots=True)
class DesignTarget:
    """One room type's rule-book targets. `None` means the rule book states no figure."""

    min_width_mm: Optional[int]
    min_area_mm2: Optional[int]
    typical_area_mm2: Optional[tuple[int, int]]
    note: str = ""


# Rule 5.4.3: "No room may have a clear dimension below 900 anywhere in its polygon." That
# is a rule-book figure applying to every room, so a room the 5.2 table omits still has a
# defensible width target and min_width() never has to refuse. It is not a substitute for
# an area, which is why several rows below carry a width and no area.
MIN_CLEAR_DIMENSION_MM: int = 900

# Rule 3.3's foyer minimum width. Bound here rather than in the section 3 block below so the
# foyer's design-target row and the constant the foyer placer reads cannot drift apart.
FOYER_MIN_WIDTH_MM: int = 1200

ROOM_DESIGN_TARGETS: dict[RoomType, DesignTarget] = {
    RoomType.MASTER_BEDROOM: DesignTarget(3000, 11_000_000, (12_000_000, 16_000_000)),
    RoomType.BEDROOM: DesignTarget(2700, 8_500_000, (9_000_000, 12_000_000)),
    RoomType.LIVING: DesignTarget(3300, 12_000_000, (14_000_000, 20_000_000)),
    RoomType.LIVING_DINING: DesignTarget(
        3300, 19_000_000, (20_000_000, 28_000_000),
        note=("Engine-derived: the rule book has no open-plan row, so the target is the "
              "living minimum (12.0) plus the separate-dining minimum (7.0). It is a "
              "design target only and is flagged as such wherever it is reported.")),
    RoomType.DINING: DesignTarget(2400, 7_000_000, (8_000_000, 11_000_000)),
    RoomType.KITCHEN: DesignTarget(1800, 5_000_000, (6_000_000, 9_000_000)),
    RoomType.BATH: DesignTarget(1200, 1_800_000, (2_200_000, 2_200_000)),
    RoomType.WC: DesignTarget(900, 1_100_000, (1_400_000, 1_400_000)),
    RoomType.ATTACHED_TOILET: DesignTarget(1200, 2_800_000, (3_200_000, 4_000_000)),
    RoomType.COMMON_TOILET: DesignTarget(1200, 2_800_000, (3_200_000, 4_000_000)),
    RoomType.UTILITY: DesignTarget(1200, 2_000_000, (2_500_000, 2_500_000)),
    RoomType.BALCONY: DesignTarget(900, None, None,
                                   note="Rule 5.2 gives a width and deliberately no area."),
    RoomType.FOYER: DesignTarget(FOYER_MIN_WIDTH_MM, 1_500_000, (2_500_000, 2_500_000),
                                 note="From rule 3.3, not the 5.2 table."),
    RoomType.CORRIDOR: DesignTarget(900, None, None,
                                    note="Rule 4.1 corridor clear width; area is residual."),
    # Rooms the rule book names but never dimensions. The width comes from rule 5.4.3, which
    # applies to every room; the area is None because inventing one would be exactly the
    # fabrication this module exists to prevent.
    RoomType.PUJA: DesignTarget(MIN_CLEAR_DIMENSION_MM, None, None,
                                note="Rule book states no puja dimension; width is rule 5.4.3."),
    RoomType.DRESSING: DesignTarget(MIN_CLEAR_DIMENSION_MM, None, None,
                                    note="Rule book states no dressing dimension; width is rule 5.4.3."),
    RoomType.STORE: DesignTarget(MIN_CLEAR_DIMENSION_MM, None, None,
                                 note="Rule book states no store dimension; width is rule 5.4.3."),
    # Rule 9.4 — a shaft is not a room and rule 5.4.3 does not reach it.
    RoomType.SHAFT: DesignTarget(600, None, None, note="Rule 9.4 minimum clear 600 x 600."),
}

# The kitchen's second row in table 5.2. Selected when the unit's programme contains no
# dining space of its own, so the kitchen has to hold one. On this project every programme
# carries LIVING_DINING, so this row is currently never selected — kept because a custom
# programme can select it and silently getting 5.0 for a kitchen that eats would be wrong.
KITCHEN_WITH_DINING_TARGET = DesignTarget(2400, 7_500_000, (9_000_000, 12_000_000))

# Clear heights (rule 5.2 footnote, all [verify]). Design targets, never reported as code.
CLEAR_HEIGHT_DESIGN_MM: dict[str, int] = {"habitable": 2750, "service": 2400}

# Room types for which neither the code nor the rule book states a minimum AREA. Declared
# explicitly rather than discovered, so a newly added room type cannot slip through
# min_carpet_area_mm2() contributing a silent zero.
NO_AREA_MINIMUM: frozenset[RoomType] = frozenset({
    RoomType.BALCONY, RoomType.CORRIDOR, RoomType.PUJA, RoomType.DRESSING,
    RoomType.STORE, RoomType.SHAFT,
})


class SpecUnavailable(RuntimeError):
    """Neither a code minimum nor a design target exists for this key. Refuse, never guess."""


@dataclass(frozen=True, slots=True)
class Minimum:
    """One resolved threshold with its provenance attached. Never a bare number.

    A caller that prints ``value`` without ``binding`` and ``verified`` beside it is
    presenting an architect's preference as statute, which is the failure mode this whole
    dataclass exists to make awkward.
    """

    key: str
    # mm and mm2 values are always int; a "ratio" is a float. The union is why the field is
    # not annotated int: a window-area ratio of 1/10 has no honest integer form and encoding
    # it as parts-per-million to keep the annotation tidy would be worse than the union.
    value: int | float
    unit: Literal["mm", "mm2", "ratio"]
    binding: Literal["code", "design", "both"]
    code_value: Optional[int | float]
    design_value: Optional[int | float]
    code_version: str
    clause_key: Optional[str]
    verified: bool
    note: str

    @property
    def clause(self) -> Optional[dict[str, Any]]:
        """The iscodes clause card this figure cites, or None when it cites nothing."""
        return iscodes.clause(self.clause_key) if self.clause_key else None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "key": self.key, "value": self.value, "unit": self.unit,
            "binding": self.binding, "code_value": self.code_value,
            "design_value": self.design_value, "code_version": self.code_version,
            "code_version_label": iscodes.version_label(self.code_version),
            "clause_key": self.clause_key, "verified": self.verified, "note": self.note,
        }
        if self.unit == "mm2":
            d["value_sqm"] = mm2_to_sqm(int(self.value))
        return d


# Built at import by walking every (room_type, field) pair once, so the registry is complete
# before any caller asks. Populated by _register_conflict, which is idempotent on `key`.
_CONFLICTS: dict[str, dict[str, Any]] = {}


def _register_conflict(key: str, room_type: RoomType, field_name: str, unit: str,
                       code_value: int | float, design_value: int | float, kind: str) -> None:
    if key in _CONFLICTS:
        return
    if kind == "code_vs_rulebook":
        reason = ("The rule book's figure is looser than the code minimum this platform "
                  "already publishes, so it cannot govern. Both numbers are reported; "
                  "neither is hidden.")
    else:
        reason = ("The two-or-more-room code figure is unread, so the stricter single-room "
                  "figure stands in. It is looser than nothing and stricter than the rule "
                  "book's target, so it governs — but it is a substitution, not a reading, "
                  "and a real approval may well pass the rule book's number.")
    entry: dict[str, Any] = {
        "room_type": str(room_type), "field": field_name, "unit": unit,
        "code_value": code_value, "design_value": design_value,
        "code_source": f"iscodes._ROOM_NBC_2016 via room_minima() [{key}]",
        "rulebook_source": "floor-plan-rulebook.md section 5.2",
        "governing": "code", "governing_value": code_value,
        "kind": kind, "reason": reason,
    }
    if unit == "mm2":
        entry["code_sqm"] = mm2_to_sqm(int(code_value))
        entry["rulebook_sqm"] = mm2_to_sqm(int(design_value))
        entry["governing_value_mm2"] = int(code_value)
    _CONFLICTS[key] = entry


def _convert_code(raw: Any, unit: str) -> int | float:
    """A code table entry in its own unit, into this module's unit.

    The iscodes room table names its keys by unit (``*_sqm``, ``*_mm``), so the target unit
    determines the conversion with no extra parameter. Areas CEIL: a minimum area is a
    demand quantity and 5.5 m2 must not become 5 499 999 mm2.
    """
    if unit == "mm2":
        return sqm_to_mm2_ceil(raw)
    if unit == "mm":
        return int(raw)
    return float(raw)


def _resolve(key: str, code_raw: Any, design_value: Optional[int | float],
             unit: Literal["mm", "mm2", "ratio"], clause_key: Optional[str],
             version: Optional[str], *, room_type: Optional[RoomType] = None,
             field_name: str = "", conflict_kind: str = "code_vs_rulebook",
             extra_note: str = "") -> Minimum:
    """The disagreement rule, in one place so it cannot be resolved twice differently.

    1. Code read AND design target present -> the LARGER governs. On a tie, binding "both".
    2. Design target LOOSER than the code -> the code wins and the pair is recorded in
       SPEC_CONFLICTS. A rule book is an opinion; a code is a legal floor, and a looser
       opinion cannot lower it. The two numbers are never silently collapsed to one.
    3. Code UNREAD, design present -> the design target stands in, labelled binding="design"
       and verified=False. Note that the UNREAD test happens BEFORE any arithmetic:
       ``max(UNREAD, x)`` raises CodeValueUnread, which is right for a compliance check and
       wrong here, where an honest labelled substitution exists.
    4. Code read, no design target -> the code.
    5. Neither -> SpecUnavailable. The import-time self-check proves this is unreachable for
       every room in every programme, so it only fires on a genuinely undimensioned room.
    """
    ver = iscodes.code_version(version)
    # Identity first. Any comparison or arithmetic on UNREAD raises CodeValueUnread.
    code_present = code_raw is not None and code_raw is not iscodes.UNREAD
    code_value: Optional[int | float] = _convert_code(code_raw, unit) if code_present else None

    if code_present and design_value is not None:
        if design_value > code_value:
            binding, value = "design", design_value
            note = ("The rule book's design target is stricter than the code minimum, so it "
                    "governs. The code figure is reported alongside it.")
        elif design_value < code_value:
            binding, value = "code", code_value
            if room_type is not None:
                _register_conflict(key, room_type, field_name or key.split(".")[-1], unit,
                                   code_value, design_value, conflict_kind)
            note = ("The code minimum is stricter than the rule book's design target, so it "
                    "governs. See SPEC_CONFLICTS for the pair.")
        else:
            binding, value = "both", code_value
            note = "The code minimum and the rule book's design target agree."
    elif code_present:
        binding, value = "code", code_value
        note = "Read from the code table; the rule book states no target for this figure."
    elif design_value is not None:
        binding, value = "design", design_value
        note = (f"No code minimum has been read from {iscodes.version_label(ver)} for this "
                "figure. The value shown is the rule book's design target and is NOT a "
                "statutory floor.")
    else:
        raise SpecUnavailable(
            f"{key}: neither {iscodes.version_label(ver)} nor the rule book states a value. "
            "Nothing can be checked against it and nothing will be invented.")

    verified = bool(code_present and binding in ("code", "both")
                    and iscodes.ROOM_MINIMA_VERIFIED)
    if extra_note:
        note = f"{note} {extra_note}".strip()
    return Minimum(key=key, value=value, unit=unit, binding=binding, code_value=code_value,
                   design_value=design_value, code_version=ver, clause_key=clause_key,
                   verified=verified, note=note)


# RoomType -> the iscodes room-table key that governs its AREA. None means the code table
# says nothing about this room type and only the design target exists.
_CODE_AREA_KEY: dict[RoomType, Optional[str]] = {
    RoomType.LIVING: "habitable", RoomType.DINING: "habitable",
    RoomType.LIVING_DINING: "habitable", RoomType.MASTER_BEDROOM: "habitable",
    RoomType.BEDROOM: "habitable",
    RoomType.KITCHEN: "kitchen_min_area_sqm",
    RoomType.BATH: "bath_min_area_sqm",
    RoomType.WC: "wc_min_area_sqm",
    RoomType.ATTACHED_TOILET: "combined_toilet_min_area_sqm",
    RoomType.COMMON_TOILET: "combined_toilet_min_area_sqm",
}
_CODE_WIDTH_KEY: dict[RoomType, Optional[str]] = {
    RoomType.LIVING: "habitable_min_width_mm", RoomType.DINING: "habitable_min_width_mm",
    RoomType.LIVING_DINING: "habitable_min_width_mm",
    RoomType.MASTER_BEDROOM: "habitable_min_width_mm",
    RoomType.BEDROOM: "habitable_min_width_mm",
    RoomType.KITCHEN: "kitchen_min_width_mm",
    RoomType.BATH: "bath_min_width_mm",
    RoomType.WC: "wc_min_width_mm",
    # The code table carries no combined-toilet width; the rule book's 1200 stands alone.
}


def _habitable_area_raw(table: Mapping[str, Any], single_room: bool) -> tuple[Any, str]:
    """The habitable-room area figure, with the multi-room fallback made explicit.

    ``habitable_min_area_multi_sqm`` is UNREAD, so a 2BHK bedroom has no multi-room code
    floor. Rather than refuse, fall back to the SINGLE-room figure (9.5 m2), which is the
    stricter direction. Substituting a stricter known value is not fabrication; substituting
    a looser guess would be. The note records the substitution so nobody reads 9.5 as the
    figure the standard gives for this case.
    """
    if single_room:
        return table["habitable_min_area_sqm"], ""
    multi = table.get("habitable_min_area_multi_sqm")
    if multi is not None and multi is not iscodes.UNREAD:
        return multi, ""
    return (table["habitable_min_area_sqm"],
            "Fallback: the single-room minimum is applied because the two-or-more-room "
            "figure is unread. It is stricter than the rule book's target, so it is safe "
            "in the conservative direction, but it is a substitution and may reject a room "
            "a real approval would pass.")


def min_area(room_type: RoomType, version: Optional[str] = None, *,
             unit_type: str = "2bhk") -> Minimum:
    """Minimum floor area for a room type, in mm2, with provenance. H04's sole source."""
    rt = RoomType(room_type)
    target = ROOM_DESIGN_TARGETS[rt]
    table = iscodes.room_minima(version)
    ut = normalise_unit_type(unit_type)
    code_key = _CODE_AREA_KEY.get(rt)
    extra = ""
    design = target.min_area_mm2

    if code_key == "habitable":
        raw, extra = _habitable_area_raw(table, _is_single_room_dwelling(ut))
    elif rt is RoomType.KITCHEN and _kitchen_holds_dining(ut):
        raw = table.get("kitchen_with_dining_min_area_sqm")
        design = KITCHEN_WITH_DINING_TARGET.min_area_mm2
        extra = ("The kitchen-with-dining row of table 5.2 is used because this unit's "
                 "programme carries no separate dining space.")
    else:
        raw = table.get(code_key) if code_key else None

    return _resolve(f"{rt}.min_area", raw, design, "mm2", "room_min_area", version,
                    room_type=rt, field_name="min_area",
                    conflict_kind=("fallback_vs_rulebook" if extra.startswith("Fallback")
                                   else "code_vs_rulebook"),
                    extra_note=extra)


def min_width(room_type: RoomType, version: Optional[str] = None) -> Minimum:
    """Minimum clear width for a room type, in mm, with provenance. H05's sole source."""
    rt = RoomType(room_type)
    table = iscodes.room_minima(version)
    raw = table.get(_CODE_WIDTH_KEY.get(rt)) if _CODE_WIDTH_KEY.get(rt) else None
    design = ROOM_DESIGN_TARGETS[rt].min_width_mm
    return _resolve(f"{rt}.min_width", raw, design, "mm", "room_min_width", version,
                    room_type=rt, field_name="min_width")


def min_height(room_type: RoomType, version: Optional[str] = None) -> Minimum:
    """Minimum clear height for a room type, in mm, with provenance.

    Both code keys are UNREAD, so every result today is a labelled design target. The two
    tiers (2750 habitable, 2400 service) are the rule book's own split.
    """
    rt = RoomType(room_type)
    table = iscodes.room_minima(version)
    habitable = rt in HABITABLE
    code_key = "habitable_min_height_mm" if habitable else "toilet_min_height_mm"
    design = CLEAR_HEIGHT_DESIGN_MM["habitable" if habitable else "service"]
    return _resolve(f"{rt}.min_height", table.get(code_key), design, "mm",
                    "room_min_height", version, room_type=rt, field_name="min_height")


def window_area_ratio(version: Optional[str] = None, *,
                      climate: str = "default") -> Minimum:
    """Rule 8.2 — aggregate window area as a fraction of room floor area.

    ``climate`` accepts "default" and "hot_humid"; "warm_humid" is accepted as the same
    thing because that is the name openings.py uses for the NBC climate zone, and two
    spellings of one zone deciding two different ratios is precisely the class of bug this
    module exists to prevent.
    """
    hot = str(climate).lower() in ("hot_humid", "warm_humid", "hot-humid", "warm-humid")
    design = WINDOW_AREA_RATIO_HOT_HUMID if hot else WINDOW_AREA_RATIO_DEFAULT
    table = iscodes.room_minima(version)
    return _resolve(f"window_area_ratio.{'hot_humid' if hot else 'default'}",
                    table.get("window_area_ratio"), design, "ratio", "room_light_vent",
                    version)


def window_openable_fraction(version: Optional[str] = None) -> Minimum:
    """Rule 8.2 — the fraction of the required window area that must actually open."""
    table = iscodes.room_minima(version)
    return _resolve("window_openable_fraction", table.get("window_openable_fraction"),
                    WINDOW_OPENABLE_FRACTION, "ratio", "room_light_vent", version)


def toilet_vent_area(version: Optional[str] = None) -> Minimum:
    """Rule 8.3 — toilet ventilation opening, in mm2 (or mechanical extract to a shaft)."""
    table = iscodes.room_minima(version)
    return _resolve("toilet.vent_area", table.get("toilet_vent_min_area_sqm"),
                    TOILET_VENT_MIN_AREA_MM2, "mm2", "room_light_vent", version)


def corridor_min_width(version: Optional[str] = None, *, accessible: bool = False) -> Minimum:
    """Rule 4.1 — in-unit corridor clear width.

    Nothing in NBC governs a passage INSIDE a dwelling, so the 900 mm figure is a design
    constant and says so. The barrier-free width is different: iscodes already carries it as
    ACCESS["corridor_min_mm"] under NBC Part 3 Cl. 13.6, so an accessible unit reads a real
    clause. This mirrors the note iscodes already writes distinguishing FIRE["corridor_min_m"]
    (egress) from ACCESS (barrier-free) — three corridor rules, three numbers, never merged.
    """
    if accessible:
        return _resolve("corridor.min_width.accessible",
                        iscodes.ACCESS["corridor_min_mm"], None, "mm", "acc_corridor",
                        version)
    return _resolve("corridor.min_width", None, CORRIDOR_MIN_WIDTH_MM, "mm", None, version)


def spec_conflicts(version: Optional[str] = None) -> list[dict[str, Any]]:
    """Every recorded code-versus-rule-book disagreement, for the report."""
    _materialise_conflicts(version)
    return [dict(v) for v in _CONFLICTS.values()]


def unread_room_keys(version: Optional[str] = None) -> list[str]:
    """Which room minima have not been read from the standard for this code version."""
    return iscodes.unread_keys(iscodes.room_minima(version))


# =====================================================================================
# D. Aspect caps (rule book 5.1)
# =====================================================================================
@dataclass(frozen=True, slots=True)
class AspectCap:
    ideal: float
    warn: float
    reject: float          # H06 fires strictly above this


_TOILET_CAP = AspectCap(1.5, 1.8, 2.2)
ASPECT_CAPS: dict[RoomType, AspectCap] = {
    RoomType.LIVING: AspectCap(1.3, 1.5, 1.8),
    RoomType.LIVING_DINING: AspectCap(1.3, 1.5, 1.8),
    RoomType.MASTER_BEDROOM: AspectCap(1.2, 1.4, 1.6),
    RoomType.BEDROOM: AspectCap(1.25, 1.45, 1.7),
    RoomType.DINING: AspectCap(1.2, 1.4, 1.7),
    RoomType.KITCHEN: AspectCap(1.6, 2.0, 2.4),
    RoomType.ATTACHED_TOILET: _TOILET_CAP,
    RoomType.COMMON_TOILET: _TOILET_CAP,
    RoomType.BATH: _TOILET_CAP,
    RoomType.WC: _TOILET_CAP,
    RoomType.UTILITY: AspectCap(2.0, 2.5, 3.0),
    # ENGINE CHOICE, the rule book is silent. A foyer is a small transitional box and a
    # 1200 x 3000 slot is a passage the plan is calling a foyer, which rule 3.4's fixed
    # sequence would then quietly break. The toilet band is borrowed as the nearest
    # comparable small room. Flagged so nobody cites it as the rule book's.
    RoomType.FOYER: AspectCap(1.5, 1.8, 2.2),
}

# Rule 5.1 lists the corridor as "linear by definition". The rest are exempt for the same
# reason: a balcony is a strip by construction, a shaft is a duct, and a dressing or store
# is a walk-in whose proportion nothing in the rule book governs. Named so an implementer
# reaching for a plausible cap finds a decision instead of a blank.
ASPECT_EXEMPT: frozenset[RoomType] = frozenset({
    RoomType.CORRIDOR, RoomType.BALCONY, RoomType.SHAFT, RoomType.DRESSING,
    RoomType.STORE, RoomType.PUJA,
})


def aspect_cap(room_type: RoomType) -> Optional[AspectCap]:
    """The 5.1 caps for a room type, or None when the room type is exempt."""
    rt = RoomType(room_type)
    if rt in ASPECT_EXEMPT:
        return None
    return ASPECT_CAPS[rt]


# =====================================================================================
# E. Furniture programme (rule book 5.3)
# =====================================================================================
# The area check alone passes a 2400 x 5500 "bedroom". This table is what actually kills it.
# Dimensions are transcribed verbatim from 5.3; nothing is added for a room the rule book
# does not furnish, because a fixture size nobody stated is a fixture size nobody can check.

@dataclass(frozen=True, slots=True)
class FurniturePiece:
    key: str
    w_mm: int                    # extent along the piece's local +x
    d_mm: int                    # extent along local +y; the BACK face is at local y = 0
    clear_back_mm: int
    clear_front_mm: int
    clear_left_mm: int
    clear_right_mm: int
    placement: Literal["against_wall", "free"]
    mirrorable: bool             # True when left/right clearances differ; packer may reflect
    required: bool = True


@dataclass(frozen=True, slots=True)
class LinearRunRequirement:
    key: str
    min_run_mm: int
    depth_mm: int
    clear_front_mm: int
    allow_L: bool
    corner_deduction_mm: int     # the overlap at an L corner, counted once


@dataclass(frozen=True, slots=True)
class FacingPair:
    a: str                       # FurniturePiece.key
    b: str
    min_gap_mm: int              # clear distance between the two footprints
    min_overlap_mm: int          # length over which their projections must overlap


@dataclass(frozen=True, slots=True)
class FurnitureProgramme:
    pieces: tuple[FurniturePiece, ...]
    runs: tuple[LinearRunRequirement, ...] = ()
    pairs: tuple[FacingPair, ...] = ()


_MASTER_BED = FurniturePiece("bed", 1830, 2000, 0, 900, 600, 600, "against_wall", False)
_MASTER_WARDROBE = FurniturePiece("wardrobe", 2100, 600, 0, 900, 0, 0, "against_wall", False)
_SECOND_BED = FurniturePiece("bed", 1370, 1900, 0, 750, 600, 0, "against_wall", True)
_SECOND_WARDROBE = FurniturePiece("wardrobe", 1500, 600, 0, 750, 0, 0, "against_wall", False)
_SOFA = FurniturePiece("sofa", 2100, 900, 0, 1200, 0, 0, "against_wall", False)
_TV_UNIT = FurniturePiece("tv_unit", 2400, 450, 0, 0, 0, 0, "against_wall", False)
_DINING_TABLE = FurniturePiece("table", 1500, 900, 900, 900, 900, 900, "free", False)
_KITCHEN_COUNTER = LinearRunRequirement("counter", 3000, 600, 1050, True, 600)
_SOFA_TV = FacingPair("sofa", "tv_unit", 1200, 1500)

FURNITURE: dict[RoomType, FurnitureProgramme] = {
    RoomType.MASTER_BEDROOM: FurnitureProgramme((_MASTER_BED, _MASTER_WARDROBE)),
    RoomType.BEDROOM: FurnitureProgramme((_SECOND_BED, _SECOND_WARDROBE)),
    RoomType.LIVING: FurnitureProgramme((_SOFA, _TV_UNIT), pairs=(_SOFA_TV,)),
    # An open-plan living-dining must hold both programmes at once, which is the whole
    # reason it counts as one facade consumer and not two: it is one room doing two jobs.
    RoomType.LIVING_DINING: FurnitureProgramme((_SOFA, _TV_UNIT, _DINING_TABLE),
                                               pairs=(_SOFA_TV,)),
    RoomType.DINING: FurnitureProgramme((_DINING_TABLE,)),
    RoomType.KITCHEN: FurnitureProgramme((), runs=(_KITCHEN_COUNTER,)),
}


def furniture_for(room_type: RoomType) -> Optional[FurnitureProgramme]:
    """The 5.3 packing programme, or None when area plus clear width IS the whole test.

    Toilets, foyer, corridor, balcony, puja, utility, store, dressing and shaft return None.
    The rule book gives no fixtures for them and this module will not supply any.
    """
    return FURNITURE.get(RoomType(room_type))


# The packing contract, stated here because geometry.py implements it and validate.py
# re-checks it, and the two must not diverge:
#   1. Axis-aligned, rotations 0/90/180/270; a `mirrorable` piece may also reflect.
#   2. `against_wall` -> the back face lies within WALL_CONTACT_TOL_MM of a room wall.
#   3. Footprints may not overlap each other or any obstacle.
#   4. A halo may not overlap another PIECE'S FOOTPRINT or any obstacle, but halos MAY
#      overlap EACH OTHER. The 600 beside a bed and the 900 in front of a wardrobe are the
#      same square metre of floor. Without this clause no real bedroom passes, and the first
#      person to run the test disables it — which is how a hard rule becomes decoration.
#   5. Footprint plus halo must lie inside the room rect.
#   6. Search pieces largest-first, wall-anchored candidates on FURNITURE_SEARCH_STEP_MM
#      plus corner snaps, backtracking, capped at FURNITURE_SEARCH_MAX_NODES. The result is
#      "fits" | "no_fit" | "undecided", and H07 treats "undecided" as a FAILURE, labelled
#      distinctly: a search giving up is not evidence the furniture fits.
#   7. Door swing rectangles are obstacles on the H07 re-check only. The step-7 feasibility
#      call runs without them, because doors do not exist yet at step 7.
WALL_CONTACT_TOL_MM: int = 50
FURNITURE_SEARCH_STEP_MM: int = 50
FURNITURE_SEARCH_MAX_NODES: int = 20_000


# =====================================================================================
# F. Shape rules (rule book 5.4)
# =====================================================================================
MAX_ROOM_VERTICES: int = 6            # rule 5.4.1
MIN_INTERNAL_ANGLE_DEG: float = 75.0  # rule 5.4.1
L_LEG_MIN_WIDTH_MM: int = 1800        # rule 5.4.2
L_LEG_MIN_AREA_FRACTION: float = 0.25  # rule 5.4.2
# MIN_CLEAR_DIMENSION_MM (rule 5.4.3) is defined above, beside ROOM_DESIGN_TARGETS, because
# several of those rows derive their width from it.


# =====================================================================================
# G. Entrance and foyer (rule book section 3)
# =====================================================================================
MAIN_ENTRANCE_COUNT: int = 1                 # rule 3.1, H01
# FOYER_MIN_WIDTH_MM is bound above, beside ROOM_DESIGN_TARGETS.
FOYER_PREF_WIDTH_MM: int = 1500
FOYER_MIN_DEPTH_MM: int = 1200
FOYER_PREF_DEPTH_MM: int = 1800
FOYER_MIN_AREA_MM2: int = 1_500_000          # 1.5 m2
FOYER_PREF_AREA_MM2: int = 2_500_000         # 2.5 m2

# Rule 3.4: "The foyer must connect to the living room and to nothing else except optionally
# the circulation spine and a shoe niche." The section 6 adjacency matrix is looser — it
# allows Foyer-Dining "A" and Foyer-CommonToilet "A". The rule book contradicts itself and
# 3.4 is the more specific statement, so 3.4 governs what the topology builder may CONNECT
# while ADJACENCY keeps the matrix's own letters for what is merely permitted. Two different
# questions; the looser answer must not be the one that builds the plan.
FOYER_MAY_CONNECT_TO: frozenset[RoomType] = frozenset({
    RoomType.LIVING, RoomType.LIVING_DINING, RoomType.CORRIDOR, RoomType.STORE,
})
MAIN_DOOR_OPENS_INTO: frozenset[RoomType] = frozenset({RoomType.FOYER})   # rule 3.2, H02
ENTRY_SEQUENCE: tuple[str, ...] = ("common_lobby", "main_door", "foyer", "living",
                                   "dining|corridor", "private_zone")
ENTRY_SIGHTLINE_FORBIDDEN: frozenset[str] = frozenset({
    "toilet_door", "bedroom_interior", "kitchen_counter", "dining_table",
})
PREFERRED_ENTRY_ORIENTATIONS: tuple[str, ...] = ("N", "NE", "E")   # rule 3.7, soft

# Rule 3.7 and the vastu sector anchors are NOT one of the nine 11.2 terms — the rule book's
# fitness table has no vastu row. This weight is applied AFTER the 11.2 score as a separate
# tiebreak and is never summed into it, on the same principle that keeps hard violations out
# of the soft list: a preference that can move a score is a rule, and this is not one.
VASTU_TIEBREAK_WEIGHT: float = 0.01

# House values for the rule 3.5 sightline cast. The rule book names four targets and no
# geometry, and its own test T10 grades a visible toilet as a SOFT penalty, so nothing here
# can reject — these numbers shape a penalty, not a verdict.
SIGHTLINE_EYE_OFFSET_MM: int = 300
SIGHTLINE_RAY_COUNT: int = 61            # 3-degree spacing over 180 degrees
SIGHTLINE_SPAN_DEG: float = 180.0
SIGHTLINE_MAX_MM: int = 12_000
SIGHTLINE_TARGET_WEIGHT: dict[str, float] = {
    "toilet_door": 1.0, "bedroom_interior": 1.0,
    "kitchen_counter": 0.6, "dining_table": 0.4,
}


# =====================================================================================
# H. Circulation (rule book section 4)
# =====================================================================================
CORRIDOR_MIN_WIDTH_MM: int = 900
CORRIDOR_PREF_WIDTH_MM: int = 1050
CORRIDOR_ACCESSIBLE_WIDTH_MM: int = 1200     # == iscodes.ACCESS["corridor_min_mm"]

# RULE 4.2 CONFLICT, RESOLVED ONCE. The rule says the band is 8-12% and then says above 15%
# is a hard reject, leaving 12-15% undefined. Resolution: 8-12% is the soft target band,
# 12-15% is a soft penalty growing with distance from 12, and ONLY above 15% is H10. Stated
# here so circulation.py and validate.py cannot resolve it two different ways — which, on a
# figure that decides both a score and a rejection, would be invisible until it mattered.
CIRCULATION_MIN_PCT: float = 8.0
CIRCULATION_TARGET_PCT: float = 10.0
CIRCULATION_SOFT_MAX_PCT: float = 12.0
CIRCULATION_HARD_MAX_PCT: float = 15.0       # H10

CORRIDOR_MIN_DOORS_SERVED: int = 2           # rule 4.3
CORRIDOR_DEAD_END_MAX_MM: int = 600          # rule 4.4
MAX_DIRECTION_CHANGES: int = 2               # rule 4.5
CORRIDOR_SHAPES: frozenset[str] = frozenset({"straight", "L"})   # rule 4.7

# Rule 4.6 — the ONLY permitted pass-throughs, as (crossed, reached) pairs. Anything else is
# H08. BATH and WC appear because they are the bath-only and WC-only forms of an attached
# toilet; omitting them would let a master suite with a separate bath fail a rule that was
# never about the fixture count.
PASS_THROUGH_ALLOWED: frozenset[tuple[RoomType, RoomType]] = frozenset({
    (RoomType.MASTER_BEDROOM, RoomType.ATTACHED_TOILET),
    (RoomType.BEDROOM, RoomType.ATTACHED_TOILET),
    (RoomType.MASTER_BEDROOM, RoomType.BATH),
    (RoomType.BEDROOM, RoomType.BATH),
    (RoomType.MASTER_BEDROOM, RoomType.WC),
    (RoomType.BEDROOM, RoomType.WC),
    (RoomType.MASTER_BEDROOM, RoomType.DRESSING),
    (RoomType.BEDROOM, RoomType.DRESSING),
    (RoomType.KITCHEN, RoomType.UTILITY),
    (RoomType.LIVING, RoomType.DINING),
})


# =====================================================================================
# I. Adjacency (rule book section 6)
# =====================================================================================
Adjacency = Literal["R", "P", "A", "F"]

# The matrix transcribed from section 6 exactly as printed, row by row, with "—" and blank
# cells omitted. It is kept in this shape rather than pre-flattened so the import-time check
# can re-derive both directions of every pair and raise on any future asymmetry: a door
# connects both ways, and a matrix that says R one way and F the other is a bug that would
# otherwise be found by a plan that mysteriously will not close.
_ADJACENCY_TABLE: dict[RoomType, dict[RoomType, str]] = {
    RoomType.FOYER: {
        RoomType.LIVING: "R", RoomType.DINING: "A", RoomType.KITCHEN: "F",
        RoomType.UTILITY: "F", RoomType.MASTER_BEDROOM: "F", RoomType.BEDROOM: "F",
        RoomType.ATTACHED_TOILET: "F", RoomType.COMMON_TOILET: "A", RoomType.BALCONY: "F",
    },
    RoomType.LIVING: {
        RoomType.FOYER: "R", RoomType.DINING: "R", RoomType.KITCHEN: "F",
        RoomType.UTILITY: "F", RoomType.MASTER_BEDROOM: "P", RoomType.BEDROOM: "P",
        RoomType.ATTACHED_TOILET: "F", RoomType.COMMON_TOILET: "A", RoomType.BALCONY: "R",
    },
    RoomType.DINING: {
        RoomType.FOYER: "A", RoomType.LIVING: "R", RoomType.KITCHEN: "R",
        RoomType.UTILITY: "F", RoomType.MASTER_BEDROOM: "F", RoomType.BEDROOM: "F",
        RoomType.ATTACHED_TOILET: "F", RoomType.COMMON_TOILET: "A", RoomType.BALCONY: "P",
    },
    RoomType.KITCHEN: {
        RoomType.FOYER: "F", RoomType.LIVING: "F", RoomType.DINING: "R",
        RoomType.UTILITY: "R", RoomType.MASTER_BEDROOM: "F", RoomType.BEDROOM: "F",
        RoomType.ATTACHED_TOILET: "F", RoomType.COMMON_TOILET: "F", RoomType.BALCONY: "P",
    },
    RoomType.MASTER_BEDROOM: {
        RoomType.FOYER: "F", RoomType.LIVING: "P", RoomType.DINING: "F",
        RoomType.KITCHEN: "F", RoomType.UTILITY: "F", RoomType.BEDROOM: "F",
        RoomType.ATTACHED_TOILET: "R", RoomType.COMMON_TOILET: "F", RoomType.BALCONY: "P",
    },
    RoomType.BEDROOM: {
        RoomType.FOYER: "F", RoomType.LIVING: "P", RoomType.DINING: "F",
        RoomType.KITCHEN: "F", RoomType.UTILITY: "F", RoomType.MASTER_BEDROOM: "F",
        RoomType.ATTACHED_TOILET: "A", RoomType.COMMON_TOILET: "P", RoomType.BALCONY: "A",
    },
    RoomType.COMMON_TOILET: {
        RoomType.FOYER: "A", RoomType.LIVING: "A", RoomType.DINING: "A",
        RoomType.KITCHEN: "F", RoomType.UTILITY: "F", RoomType.MASTER_BEDROOM: "F",
        RoomType.BEDROOM: "P", RoomType.BALCONY: "F",
    },
}

# Rows the rule book's matrix omits, derived from rules 6.1-6.5, from the open-plan decision,
# and from what vastu.check_unit already enforces. Every one is flagged derived=True in
# ADJACENCY_SOURCE so a reader can tell a transcription from an inference.
_ADJACENCY_DERIVED: dict[frozenset[RoomType], tuple[str, str]] = {}


def _derive(a: RoomType, b: RoomType, value: str, why: str) -> None:
    _ADJACENCY_DERIVED[frozenset({a, b})] = (value, why)


# LIVING_DINING is one room doing the jobs of two, so it takes the STRONGER of the living and
# dining relations with each partner. Weaker would let the open-plan option quietly drop the
# required kitchen-dining door, which is the connection that keeps the kitchen out of the
# foyer (rule 6.1).
_STRENGTH = {"R": 3, "P": 2, "A": 1, "F": 0}
for _partner in (RoomType.FOYER, RoomType.KITCHEN, RoomType.UTILITY,
                 RoomType.MASTER_BEDROOM, RoomType.BEDROOM, RoomType.ATTACHED_TOILET,
                 RoomType.COMMON_TOILET, RoomType.BALCONY):
    _lv = _ADJACENCY_TABLE[RoomType.LIVING].get(_partner, "F")
    _dn = _ADJACENCY_TABLE[RoomType.DINING].get(_partner, "F")
    _derive(RoomType.LIVING_DINING, _partner,
            _lv if _STRENGTH[_lv] >= _STRENGTH[_dn] else _dn,
            "open-plan living_dining takes the stronger of the living and dining relations")

# BATH and WC are the bath-only and WC-only forms of a toilet. They inherit the common
# toilet's row rather than defaulting to "F", which would forbid the only door a WC can have.
for _partner, _val in _ADJACENCY_TABLE[RoomType.COMMON_TOILET].items():
    for _variant in (RoomType.BATH, RoomType.WC):
        _derive(_variant, _partner, _val, "bath/WC inherit the common toilet's row")
for _variant in (RoomType.BATH, RoomType.WC):
    _derive(_variant, RoomType.LIVING_DINING,
            _ADJACENCY_DERIVED[frozenset({RoomType.LIVING_DINING, RoomType.COMMON_TOILET})][0],
            "bath/WC inherit the common toilet's row")

# Rule 6.5 — the puja space. The rule book's matrix has no puja row at all, but rule 6.5 and
# vastu.check_unit both already govern it: a door off the living room, never off a toilet or
# the kitchen.
_derive(RoomType.PUJA, RoomType.LIVING, "R", "rule 6.5 / vastu.check_unit: puja opens off the living room")
_derive(RoomType.PUJA, RoomType.LIVING_DINING, "R", "rule 6.5 / vastu.check_unit")
_derive(RoomType.PUJA, RoomType.FOYER, "A", "a puja reached from the entry hall is common practice")
_derive(RoomType.PUJA, RoomType.KITCHEN, "F", "rule 6.5")
for _t in (RoomType.ATTACHED_TOILET, RoomType.COMMON_TOILET, RoomType.BATH, RoomType.WC):
    _derive(RoomType.PUJA, _t, "F", "rule 6.5: no toilet connection to a puja space")

# Dressing rooms — rule 4.6 permits reaching one only through its bedroom, so those are the
# only two doors it may have.
_derive(RoomType.DRESSING, RoomType.MASTER_BEDROOM, "R", "rule 4.6 permitted pass-through")
_derive(RoomType.DRESSING, RoomType.BEDROOM, "A", "rule 4.6 permitted pass-through")

# The corridor. Rule 4.6 wants every habitable room reachable from the foyer without crossing
# another habitable room, and the spine is how that happens. The kitchen is deliberately NOT
# on this list: rule 6.1 keeps it off the foyer, and its required door is from the dining.
for _r in (RoomType.FOYER, RoomType.LIVING, RoomType.DINING, RoomType.LIVING_DINING,
           RoomType.MASTER_BEDROOM, RoomType.BEDROOM, RoomType.COMMON_TOILET,
           RoomType.BATH, RoomType.WC, RoomType.STORE, RoomType.UTILITY, RoomType.PUJA):
    _derive(RoomType.CORRIDOR, _r, "A", "rule 4.6: the spine is how rooms are reached")

# A store is a shoe niche at the foyer or a pantry at the kitchen (rule 3.4 names the niche).
_derive(RoomType.STORE, RoomType.FOYER, "A", "rule 3.4 shoe niche")
_derive(RoomType.STORE, RoomType.KITCHEN, "A", "pantry off the kitchen, SCALING_PROTOCOL tier 5")

# Rule 9.4 — a shaft must be maintainable from a common area or the utility, NEVER only from
# inside a bedroom. The "A" pair here is what gives a validator something to check.
_derive(RoomType.SHAFT, RoomType.UTILITY, "A", "rule 9.4 maintenance access")
_derive(RoomType.SHAFT, RoomType.CORRIDOR, "A", "rule 9.4 maintenance access")

# Canonical store. Symmetric by construction: the key is a frozenset, so there is no second
# direction to disagree with.
ADJACENCY: dict[frozenset[RoomType], Adjacency] = {}
ADJACENCY_SOURCE: dict[frozenset[RoomType], dict[str, Any]] = {}

for _row, _cells in _ADJACENCY_TABLE.items():
    for _col, _val in _cells.items():
        _pair = frozenset({_row, _col})
        _prev = ADJACENCY.get(_pair)
        if _prev is not None and _prev != _val:
            raise AssertionError(
                f"rule book section 6 disagrees with itself on {sorted(str(r) for r in _pair)}: "
                f"the table gives {_prev!r} in one direction and {_val!r} in the other. "
                "Fix the transcription against the document; do not pick one.")
        ADJACENCY[_pair] = _val  # type: ignore[assignment]
        ADJACENCY_SOURCE[_pair] = {"derived": False, "source": "rulebook section 6 matrix"}

for _pair, (_val, _why) in _ADJACENCY_DERIVED.items():
    if _pair in ADJACENCY:
        continue          # a transcribed cell always beats a derived one
    ADJACENCY[_pair] = _val  # type: ignore[assignment]
    ADJACENCY_SOURCE[_pair] = {"derived": True, "source": _why}


def adjacency(a: RoomType, b: RoomType) -> Adjacency:
    """The permitted door relation between two room types. Symmetric.

    The default for an unlisted pair is "F". A permissive default is how a toilet door ends
    up on the dining room: nobody writes that rule down, it just never gets forbidden.
    """
    return ADJACENCY.get(frozenset({RoomType(a), RoomType(b)}), "F")


def adjacency_source(a: RoomType, b: RoomType) -> dict[str, Any]:
    return ADJACENCY_SOURCE.get(frozenset({RoomType(a), RoomType(b)}),
                                {"derived": True, "source": "default: unlisted pairs are F"})


# Rule 6.5 — a STRONGER relation than "F". "F" forbids a door; this forbids a shared wall,
# floor or ceiling. vastu.check_unit already enforces exactly this pairing, so the distinction
# is the codebase's own precedent rather than a new idea.
WALL_FORBIDDEN_PAIRS: frozenset[frozenset[RoomType]] = frozenset({
    frozenset({RoomType.PUJA, RoomType.ATTACHED_TOILET}),
    frozenset({RoomType.PUJA, RoomType.COMMON_TOILET}),
    frozenset({RoomType.PUJA, RoomType.BATH}),
    frozenset({RoomType.PUJA, RoomType.WC}),
})

ADJACENCY_MISS_PENALTY: dict[str, float] = {"R": 10.0, "P": 3.0, "A": 0.0, "F": 0.0}


# =====================================================================================
# J. Doors (rule book section 7)
# =====================================================================================
@dataclass(frozen=True, slots=True)
class DoorSpec:
    key: str
    width_mm: int
    height_mm: int
    min_width_mm: int            # used where 7.2 gives a range (utility/balcony 750-900)


# Rule 7.2 is marked [verify]. A door leaf size is a joinery dimension, not a code minimum,
# so these are plain spec constants and must never be reported as code. The one genuine code
# figure in the neighbourhood is the barrier-free 900 mm clear width, which iscodes already
# carries as ACCESS["door_width_mm"] under NBC Part 3 Cl. 13.5 — door_spec(accessible=True)
# reads it from there rather than restating it.
DOORS: dict[str, DoorSpec] = {
    "main": DoorSpec("main", 1000, 2100, 1000),        # rules 3.6 and 7.2
    "bedroom": DoorSpec("bedroom", 900, 2100, 900),
    "kitchen": DoorSpec("kitchen", 900, 2100, 900),
    "toilet": DoorSpec("toilet", 750, 2000, 750),
    # 7.2 gives "750 to 900" for utility and balcony. The wider end is taken as the nominal
    # with 750 recorded as the floor: a 750 utility leaf will not pass a washing machine.
    "utility": DoorSpec("utility", 900, 2100, 750),
    "balcony": DoorSpec("balcony", 900, 2100, 750),
    # Roles the rule book's table does not list. House defaults, marked as such: a puja and a
    # servant room take the toilet leaf width, a study takes the bedroom leaf, and a shaft
    # gets a maintenance panel sized from rule 9.4's 600 clear.
    "puja": DoorSpec("puja", 750, 2100, 750),
    "study": DoorSpec("study", 900, 2100, 900),
    "servant": DoorSpec("servant", 750, 2000, 750),
    "shaft": DoorSpec("shaft", 600, 1200, 600),
}
DOORS_VERIFIED: bool = False        # 7.2 carries [verify]; surfaced beside every door code

# The same table in the plain-dict shape openings.py imports. Built from DOORS rather than
# retyped, because two literal copies of a joinery schedule is two schedules.
DOOR_SIZES_MM: dict[str, dict[str, int]] = {
    k: {"clear_w": d.width_mm, "clear_h": d.height_mm, "min_w": d.min_width_mm}
    for k, d in DOORS.items()
}
DOOR_SIZES_VERIFIED: bool = DOORS_VERIFIED

_DOOR_ROLE_OF_ROOM: dict[RoomType, str] = {
    RoomType.MASTER_BEDROOM: "bedroom", RoomType.BEDROOM: "bedroom",
    RoomType.KITCHEN: "kitchen",
    RoomType.ATTACHED_TOILET: "toilet", RoomType.COMMON_TOILET: "toilet",
    RoomType.BATH: "toilet", RoomType.WC: "toilet",
    RoomType.UTILITY: "utility", RoomType.BALCONY: "balcony",
    RoomType.PUJA: "puja", RoomType.DRESSING: "bedroom", RoomType.STORE: "utility",
    RoomType.SHAFT: "shaft",
    # Through-spaces have no leaf of their own; a cased opening is not a door. They resolve
    # to the bedroom leaf only so a caller that asks anyway gets a sane size rather than a
    # KeyError halfway through a schedule.
    RoomType.FOYER: "bedroom", RoomType.LIVING: "bedroom", RoomType.DINING: "bedroom",
    RoomType.LIVING_DINING: "bedroom", RoomType.CORRIDOR: "bedroom",
}


def door_spec(room_type: RoomType, *, is_main: bool = False,
              accessible: bool = False) -> DoorSpec:
    """The leaf for the door into `room_type`. `accessible` reads the real NBC clause.

    In an accessible unit every leaf is raised to iscodes.ACCESS["door_width_mm"], which
    promotes the 750 toilet leaf to 900. A non-accessible unit keeps 750, because the 900 is
    a barrier-free requirement and applying it everywhere would report a code figure where
    none applies.
    """
    base = DOORS["main"] if is_main else DOORS[_DOOR_ROLE_OF_ROOM[RoomType(room_type)]]
    if not accessible:
        return base
    clear = int(iscodes.ACCESS["door_width_mm"])
    if base.width_mm >= clear:
        return base
    return DoorSpec(base.key, clear, base.height_mm, clear)


DOOR_CORNER_OFFSET_MIN_MM: int = 100         # rule 7.3, exact
DOOR_CORNER_OFFSET_MAX_MM: int = 300         # rule 7.3, exact
DOOR_CORNER_OFFSET_PREF_MM: int = 150        # house preference inside the 7.3 band
DOOR_CORNER_OFFSET_CANDIDATES_MM: tuple[int, ...] = (150, 100, 200, 250, 300)
DOOR_SWING_ANGLE_DEG: float = 90.0           # rule 7.5 sweep
TOILET_OUTWARD_SWING_BELOW_MM2: int = 1_500_000   # rule 7.4: below 1.5 m2 the leaf goes out
FACING_DOOR_MIN_OFFSET_MM: int = 300         # rule 7.8, exact
FACING_DOOR_ALIGN_TOL_MM: int = 25           # how exact "aligned exactly" is; house value
MAX_DOORS_PER_ROOM: int = 1                  # rule 7.1, H14; THROUGH_SPACES exempt

# House values for door placement. None is a rule-book figure and none may reject on its own.
JAMB_MIN_MM: int = 100                       # pier a jamb needs
OPENING_GAP_MIN_MM: int = 150                # pier between two openings on one wall
SLIDING_POCKET_FACTOR: float = 2.0           # a pocket needs 2x leaf width of clear wall
BALCONY_SLIDING_DEFAULT: bool = True         # sliding balcony leaves delete a whole class of
                                             # 7.5 clashes in the living room
LEAF_WINDOW_CLEAR_MM: int = 100              # rule 7.6 clearance, open leaf to window
CASED_OPENING_MIN_MM: int = 1200
CASED_OPENING_PREF_MM: int = 1800
CASED_OPENING_MAX_MM: int = 2400
DOOR_LEAF_THICKNESS_MM: int = 40             # drawing and 3D only, not a rule


# =====================================================================================
# K. Light and ventilation (rule book section 8)
# =====================================================================================
WINDOW_AREA_RATIO_DEFAULT: float = 1.0 / 10.0      # rule 8.2 [verify]
WINDOW_AREA_RATIO_HOT_HUMID: float = 1.0 / 6.0     # rule 8.2 [verify]
WINDOW_OPENABLE_FRACTION: float = 0.5              # rule 8.2 [verify]
WINDOW_RATIO_VERIFIED: bool = False
TOILET_VENT_MIN_AREA_MM2: int = 300_000            # rule 8.3, 0.3 m2 [verify]
TOILET_VENT_VERIFIED: bool = False
WINDOW_HEAD_HEIGHT_MM: int = 2100
DAYLIGHT_DEPTH_FACTOR: float = 2.5                 # rule 8.4 -> 5250 depth cap at a 2100 head

# RULE 1.1 / 8.5 FACADE CONFLICT, RESOLVED ONCE. Rule 1.1's gate uses 3000 per habitable
# room; rule 8.5 gives 2500 minimum and 3000 preferred. Three uses, three numbers, all named:
#   - the section 1.1 GATE (envelope.py)          -> 3000, the preferred figure. Being
#     generous at the gate is the gate's entire purpose; it is what eliminates the
#     long-rectangle output the rule book's section 0 diagnoses.
#   - the per-room HARD check (validate.py)       -> 2500, the 8.5 minimum.
#   - the 11.2 "facade utilisation" SOFT term     -> counts rooms under 3000.
MIN_FACADE_PER_HABITABLE_MM: int = 2500
PREF_FACADE_PER_HABITABLE_MM: int = 3000

SILL_HABITABLE_MM: int = 900
SILL_KITCHEN_MM: int = 1200        # clears a 900 counter with a 300 splashback
SILL_TOILET_MM: int = 1500
SILL_FRENCH_MM: int = 0
HEAD_HABITABLE_MM: int = WINDOW_HEAD_HEIGHT_MM
HEAD_TOILET_MM: int = 2100
WINDOW_JAMB_MIN_MM: int = 300      # pier each side of a window; house value
WINDOW_MIN_WIDTH_MM: int = 600
WINDOW_MAX_WIDTH_MM: int = 3000
MAX_WINDOWS_PER_ROOM: int = 3

CLIMATE_ZONES: tuple[str, ...] = ("hot_dry", "warm_humid", "composite", "temperate", "cold")
WARM_HUMID_ZONES: frozenset[str] = frozenset({"warm_humid"})
# A soft Indian-context preference of the same character as vastu.py's sector anchors.
# Weight it low; it must never move a window off the wall rule 8.2 needs.
CARDINAL_GLAZING_PENALTY: dict[str, float] = {
    "N": 0.00, "NE": 0.05, "E": 0.10, "SE": 0.25,
    "S": 0.30, "SW": 0.55, "W": 0.60, "NW": 0.35,
}


# =====================================================================================
# L. Services and structure (rule book sections 9 and 10)
# =====================================================================================
WC_TRAP_TO_STACK_MAX_MM: int = 2000      # rule 9.2: beyond this the 1:40 fall will not fit
SHAFT_MIN_CLEAR_MM: int = 600            # rule 9.4, 600 x 600
WALL_TO_GRID_TOLERANCE_MM: int = 150     # rule 10.1
MAX_BEAM_SPAN_MM: int = 6000             # rule 10.2


# =====================================================================================
# M. The unit programme
# =====================================================================================
# The selectable unit types are exactly what the UI offers (frontend PlanningModule.jsx
# UNIT_TYPES). No new tier is invented here: a programme the user cannot select is a
# programme nobody will ever review.
UnitTypeKey = str
UNIT_TYPES: tuple[UnitTypeKey, ...] = (
    "studio", "1bhk", "2bhk", "3bhk", "4bhk", "penthouse", "custom",
)

# The tiers follow vastu.SCALING_PROTOCOL (the Generative Architecture & Vastu Logic Manual
# section 2), which is this codebase's existing statement of what a tier is entitled to:
# bedroom count, how many carry an en-suite, one common bath, a puja NICHE at tier 1 and a
# puja ROOM from tier 2, balconies, utility, servant and extras. Two consequences of reading
# it literally, both deliberate:
#   - a 1BHK gets an en-suite AND a common bath, because tier 1 says ensuites 1, baths 1;
#   - a puja niche is not a room, so PUJA appears from 2BHK up and not before.
#
# LIVING_DINING is the default at EVERY tier, per the project-wide decision that an open-plan
# living-dining counts as ONE facade consumer. That is not a stylistic preference: this
# site's plate depths give flats 5.6-8.6 m deep and a 2BHK about 11.8 m wide, and the naive
# reading of rule 1.1 (3000 mm of facade per habitable room, separate living AND dining) asks
# a 3BHK for 18 m and rejects nearly every footprint the site planner can produce. The
# open-plan reading asks 12 m and passes. It is measured, not assumed.
#
# What the RoomType vocabulary cannot express is recorded in UNIT_PROGRAMME_NOTES rather than
# approximated, with the direction of the resulting error stated.
UNIT_PROGRAMME: dict[UnitTypeKey, tuple[RoomType, ...]] = {
    # A studio is a single-room dwelling: one habitable room that lives, dines and sleeps.
    "studio": (
        RoomType.FOYER, RoomType.LIVING_DINING, RoomType.KITCHEN,
        RoomType.COMMON_TOILET, RoomType.BALCONY,
    ),
    # Tier 1: 1 bed, 1 en-suite, 1 common bath, puja niche (no room), compact utility
    # (absorbed into the kitchen), one living balcony.
    "1bhk": (
        RoomType.FOYER, RoomType.CORRIDOR, RoomType.LIVING_DINING, RoomType.KITCHEN,
        RoomType.MASTER_BEDROOM, RoomType.ATTACHED_TOILET, RoomType.COMMON_TOILET,
        RoomType.BALCONY,
    ),
    # Tier 2: 2 beds, 1 en-suite, 1 common bath, puja ROOM, utility room, one balcony.
    "2bhk": (
        RoomType.FOYER, RoomType.CORRIDOR, RoomType.LIVING_DINING, RoomType.KITCHEN,
        RoomType.UTILITY, RoomType.PUJA,
        RoomType.MASTER_BEDROOM, RoomType.ATTACHED_TOILET,
        RoomType.BEDROOM, RoomType.COMMON_TOILET,
        RoomType.BALCONY,
    ),
    # Tier 3: 3 beds, 2 en-suites, 1 common bath, large utility, living + master balconies.
    "3bhk": (
        RoomType.FOYER, RoomType.CORRIDOR, RoomType.LIVING_DINING, RoomType.KITCHEN,
        RoomType.UTILITY, RoomType.PUJA,
        RoomType.MASTER_BEDROOM, RoomType.ATTACHED_TOILET,
        RoomType.BEDROOM, RoomType.ATTACHED_TOILET,
        RoomType.BEDROOM, RoomType.COMMON_TOILET,
        RoomType.BALCONY, RoomType.BALCONY,
    ),
    # Tier 4: 4 beds, 3 en-suites, 1 common bath, powder room, servant, three balconies.
    # The servant room is carried as a BEDROOM: it is habitable, it needs light and
    # ventilation, and counting it is the conservative direction for the facade gate. The
    # label is wrong and UNIT_PROGRAMME_NOTES says so.
    "4bhk": (
        RoomType.FOYER, RoomType.CORRIDOR, RoomType.LIVING_DINING, RoomType.KITCHEN,
        RoomType.UTILITY, RoomType.PUJA,
        RoomType.MASTER_BEDROOM, RoomType.ATTACHED_TOILET,
        RoomType.BEDROOM, RoomType.ATTACHED_TOILET,
        RoomType.BEDROOM, RoomType.ATTACHED_TOILET,
        RoomType.BEDROOM,
        RoomType.BEDROOM,                       # servant accommodation
        RoomType.COMMON_TOILET, RoomType.WC,    # WC = the tier-4 "powder" extra
        RoomType.BALCONY, RoomType.BALCONY, RoomType.BALCONY,
    ),
    # Tier 5: 5 beds, 4 en-suites, 1 common bath, powder, pantry, closet, servant.
    "penthouse": (
        RoomType.FOYER, RoomType.CORRIDOR, RoomType.LIVING_DINING, RoomType.KITCHEN,
        RoomType.UTILITY, RoomType.PUJA, RoomType.STORE,      # STORE = the "pantry" extra
        RoomType.MASTER_BEDROOM, RoomType.ATTACHED_TOILET, RoomType.DRESSING,
        RoomType.BEDROOM, RoomType.ATTACHED_TOILET,
        RoomType.BEDROOM, RoomType.ATTACHED_TOILET,
        RoomType.BEDROOM, RoomType.ATTACHED_TOILET,
        RoomType.BEDROOM,
        RoomType.BEDROOM,                       # servant accommodation
        RoomType.COMMON_TOILET, RoomType.WC,
        RoomType.BALCONY, RoomType.BALCONY, RoomType.BALCONY,
    ),
    # "custom" is deliberately absent. See programme().
}

UNIT_PROGRAMME_NOTES: tuple[str, ...] = (
    "vastu.SCALING_PROTOCOL entitles tiers 4 and 5 to servant accommodation. The RoomType "
    "vocabulary has no servant room, so it is carried as a BEDROOM. That overstates the "
    "bedroom count in every report, and it is the conservative direction for the rule 1.1 "
    "facade gate — a servant room genuinely needs light and ventilation. Mapping it to "
    "STORE instead would have understated the demand, which is the error that turns a fail "
    "into a pass.",
    "The tier-5 extras 'lounge' and 'office' have no RoomType and are NOT in the penthouse "
    "programme. This UNDERSTATES the penthouse's facade and area demand, so the gate is "
    "permissive for that tier alone. Adding them means extending RoomType, not relabelling "
    "an existing one.",
    "A tier-1 puja is a NICHE, not a room, so PUJA appears from 2BHK up. A niche has no "
    "floor area of its own and giving it one would inflate every 1BHK's minimum carpet.",
    "The tier-1 'compact' utility is absorbed into the kitchen; UTILITY appears as a room "
    "from 2BHK up, where SCALING_PROTOCOL calls it a room.",
    "LIVING_DINING is the default at every tier and counts as ONE facade consumer. A "
    "programme wanting separate LIVING and DINING counts two, and the rule 1.1 gate will "
    "size itself accordingly with no change to this module.",
)


def normalise_unit_type(raw: str) -> UnitTypeKey:
    """Map a free-text unit label onto one of the seven selectable keys.

    Unrecognised input becomes "2bhk" rather than raising, because this is called on data
    that has travelled through a UI and a JSON payload, and a KeyError there would take down
    a whole floor plate over a label. The aliases below are the ones this codebase actually
    produces: vastu.bhk_of parses "2 BHK"/"2bhk", and the older rule-book drafts speak of
    "1rk" and "2.5bhk".
    """
    t = str(raw or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    if not t:
        return "2bhk"
    if t in UNIT_TYPES:
        return t
    if t in ("1rk", "rk", "0bhk", "efficiency", "bachelor"):
        return "studio"
    if "penthouse" in t:
        return "penthouse"
    if "studio" in t:
        return "studio"
    if t in ("2.5bhk", "25bhk"):
        # A 2.5BHK is a 2BHK plus a study. There is no study RoomType and no 2.5 tier in
        # SCALING_PROTOCOL, so it resolves DOWN to 2bhk rather than up: understating the
        # programme by half a room is safer at a gate than claiming a room that is not there.
        return "2bhk"
    for n, key in ((5, "penthouse"), (4, "4bhk"), (3, "3bhk"), (2, "2bhk"), (1, "1bhk")):
        if f"{n}bhk" in t:
            return key
    return "2bhk"


def programme(unit_type: str, *, rooms: Optional[Sequence[RoomType]] = None
              ) -> tuple[RoomType, ...]:
    """The room list for a unit type.

    "custom" has no fixed programme by definition, so it REFUSES unless the caller supplies
    the room list. Quietly substituting the 2BHK programme would size the rule 1.1 gate for a
    unit nobody asked for, and the resulting pass would look exactly like a real one.
    """
    if rooms is not None:
        return tuple(RoomType(r) for r in rooms)
    ut = normalise_unit_type(unit_type)
    try:
        return UNIT_PROGRAMME[ut]
    except KeyError:
        raise SpecUnavailable(
            f"unit type {ut!r} has no fixed programme. Pass the room list explicitly, e.g. "
            "programme('custom', rooms=[RoomType.FOYER, ...]). Nothing will be assumed.") from None


def habitable_count(unit_type: str, *, rooms: Optional[Sequence[RoomType]] = None) -> int:
    """How many rooms in this programme need a facade or a shaft (rule 8.1).

    This is the multiplicand of the rule 1.1 facade budget, so an error here moves the gate
    for every unit on the site.
    """
    return sum(1 for r in programme(unit_type, rooms=rooms) if r in HABITABLE)


def _is_single_room_dwelling(unit_type: UnitTypeKey) -> bool:
    """NBC distinguishes a single-room dwelling (9.5 m2) from a multi-room one.

    Only the studio qualifies: one habitable room that lives, dines and sleeps. Everything
    else, including a 1BHK, has two or more.
    """
    return unit_type == "studio"


def _kitchen_holds_dining(unit_type: UnitTypeKey) -> bool:
    """True when the programme has no dining space, so the kitchen must contain one."""
    try:
        prog = UNIT_PROGRAMME[unit_type]
    except KeyError:
        return False
    return not any(r in (RoomType.DINING, RoomType.LIVING_DINING) for r in prog)


def min_carpet_area_mm2(unit_type: str, version: Optional[str] = None, *,
                        rooms: Optional[Sequence[RoomType]] = None) -> int:
    """Sum of every room's minimum area, in mm2. The multiplicand of the rule 1.1 area gate.

    CORRIDOR and FOYER are excluded on purpose: rule 1.1 multiplies this by 1.18 "for walls
    & circulation", so counting circulation here would charge for it twice. Rooms in
    NO_AREA_MINIMUM contribute nothing, which makes the result a LOWER BOUND — see
    min_carpet_area_breakdown() for the list of what was skipped, so a report can say so
    instead of presenting the sum as complete.
    """
    return int(min_carpet_area_breakdown(unit_type, version, rooms=rooms)["total_mm2"])


def min_carpet_area_breakdown(unit_type: str, version: Optional[str] = None, *,
                              rooms: Optional[Sequence[RoomType]] = None) -> dict[str, Any]:
    """min_carpet_area_mm2 with its working shown, including what it could not count."""
    ut = normalise_unit_type(unit_type)
    prog = programme(ut, rooms=rooms)
    per_room: list[dict[str, Any]] = []
    skipped: list[str] = []
    total = 0
    for rt in prog:
        if rt in (RoomType.CORRIDOR, RoomType.FOYER):
            continue
        if rt in NO_AREA_MINIMUM:
            skipped.append(str(rt))
            continue
        m = min_area(rt, version, unit_type=ut)
        total += int(m.value)
        per_room.append({"room_type": str(rt), "min_area_mm2": int(m.value),
                         "binding": m.binding, "verified": m.verified})
    return {
        "unit_type": ut, "total_mm2": total, "total_sqm": mm2_to_sqm(total),
        "rooms": per_room,
        "excluded_circulation": [str(RoomType.FOYER), str(RoomType.CORRIDOR)],
        "no_minimum_stated": sorted(set(skipped)),
        "is_lower_bound": bool(skipped),
        "note": ("The total is a LOWER bound: neither the code nor the rule book states a "
                 "minimum area for " + ", ".join(sorted(set(skipped))) + "."
                 ) if skipped else "Every room in this programme has a stated minimum area.",
    }


# =====================================================================================
# N. Soft score (rule book 11.2) and hard rules (11.1)
# =====================================================================================
# Verbatim from the 11.2 table. The sum is asserted at import to be 1.0: a weight table that
# does not sum to one produces scores that cannot be compared between unit types, and the
# comparison is the only thing a fitness number is for.
SOFT_WEIGHTS: dict[str, float] = {
    "aspect_deviation": 0.20,
    "circulation_efficiency": 0.18,
    "adjacency_satisfaction": 0.16,
    "entry_privacy": 0.12,
    "facade_utilisation": 0.10,
    "wet_core_compactness": 0.08,
    "door_placement_quality": 0.08,
    "structural_alignment": 0.05,
    "carpet_efficiency": 0.03,
}
CARPET_EFFICIENCY_TARGET: float = 0.78       # rule 11.2, "target above 0.78"

# Rule 11.3 — what the genetic search may and may not touch. Held as data so search.py
# cannot quietly widen it: mutating room count or the adjacency graph is how a rule-first
# engine turns back into a geometry-first one.
GA_MUTABLE: frozenset[str] = frozenset({
    "room_depth", "room_width", "corridor_position_within_zone", "door_offset_along_wall",
})
GA_FROZEN: frozenset[str] = frozenset({
    "room_count", "adjacency_graph", "entrance_position", "zone_assignment",
})

# Section 11.1, verbatim. Hard violations are NEVER merged into the soft list: a plan with a
# violation is not a low-scoring plan, it is not a plan.
HARD_RULE_TEXT: dict[str, str] = {
    "H01": "entrance_count != 1",
    "H02": "main door opens into a room other than foyer",
    "H03": "any habitable room with no external wall or shaft",
    "H04": "any room area < code minimum",
    "H05": "any room clear width < code minimum",
    "H06": "aspect ratio > hard reject cap (table 5.1)",
    "H07": "furniture packing test failed",
    "H08": "any room unreachable from foyer per rule 4.6",
    "H09": "any door swing arc collision",
    "H10": "circulation area > 15% of carpet area",
    "H11": "zone order along entry axis not monotonic",
    "H12": "wet area stacked over habitable room",
    "H13": "toilet door opens into kitchen or dining",
    "H14": "bedroom with more than one door",
    "H15": "staggered party wall",
}
HARD_CODES: tuple[str, ...] = tuple(sorted(HARD_RULE_TEXT))


# =====================================================================================
# O. Conflict registry, materialised at import
# =====================================================================================
def _materialise_conflicts(version: Optional[str] = None) -> None:
    """Walk every (room type, field) pair once so the registry is complete before any read.

    A conflict discovered lazily, on the first caller who happens to ask about a kitchen, is
    a conflict that is absent from the report of every run that did not ask.
    """
    for rt in ROOM_DESIGN_TARGETS:
        for fn in (min_area, min_width, min_height):
            try:
                fn(rt, version)              # type: ignore[operator]
            except SpecUnavailable:
                continue                      # rooms with no stated area; expected


SPEC_CONFLICTS: tuple[dict[str, Any], ...] = ()


# =====================================================================================
# P. Import-time self-checks
# =====================================================================================
# These are real rejections. A bad edit fails collection rather than a request, which is the
# difference between a broken build and a wrong floor plan shipped to a client.
def _self_check() -> None:
    total = sum(SOFT_WEIGHTS.values())
    if abs(total - 1.0) >= 1e-9:
        raise AssertionError(
            f"SOFT_WEIGHTS sum to {total!r}, not 1.0. Scores from different unit types "
            "would not be comparable, which is the only thing a fitness number is for.")

    missing_zone = [str(rt) for rt in RoomType if rt not in ZONE_OF_ROOM]
    if missing_zone:
        raise AssertionError(f"RoomType(s) with no zone: {missing_zone}. Rule 2.3's "
                             "monotonicity check cannot classify them.")

    missing_legacy = [str(rt) for rt in RoomType if rt not in LEGACY_TYPE]
    if missing_legacy:
        raise AssertionError(f"RoomType(s) with no legacy renderer type: {missing_legacy}. "
                             "They would render as untyped white boxes.")

    missing_target = [str(rt) for rt in RoomType if rt not in ROOM_DESIGN_TARGETS]
    if missing_target:
        raise AssertionError(f"RoomType(s) with no design target row: {missing_target}.")

    capped = [str(rt) for rt in RoomType
              if rt not in ASPECT_EXEMPT and rt not in ASPECT_CAPS]
    if capped:
        raise AssertionError(
            f"RoomType(s) with neither an aspect cap nor an exemption: {capped}. H06 would "
            "silently skip them, which is worse than either answer.")

    if set(HARD_RULE_TEXT) != {f"H{n:02d}" for n in range(1, 16)}:
        raise AssertionError("HARD_RULE_TEXT must carry exactly H01..H15 from section 11.1.")

    # Every room in every programme must resolve a width, and must resolve an area unless it
    # is explicitly declared to have none. This is the check that stops a newly added room
    # type contributing a silent zero to the rule 1.1 area gate.
    for ut, prog in UNIT_PROGRAMME.items():
        for rt in set(prog):
            min_width(rt)
            if rt in NO_AREA_MINIMUM:
                continue
            min_area(rt, unit_type=ut)

    # Every programme must contain exactly one foyer and at least one habitable room, or
    # rules 3.1/3.2 and 8.1 have nothing to attach to.
    for ut, prog in UNIT_PROGRAMME.items():
        if prog.count(RoomType.FOYER) != 1:
            raise AssertionError(f"programme {ut!r} must contain exactly one foyer (rule 3.2).")
        if not any(r in HABITABLE for r in prog):
            raise AssertionError(f"programme {ut!r} has no habitable room.")

    # Rule 6.5's wall relation must be strictly stronger than adjacency "F": every pair that
    # may not share a wall must also be forbidden a door.
    for pair in WALL_FORBIDDEN_PAIRS:
        a, b = tuple(pair)
        if adjacency(a, b) != "F":
            raise AssertionError(
                f"{a} and {b} may not share a wall (rule 6.5) but adjacency says "
                f"{adjacency(a, b)!r}. The wall relation must be the stronger of the two.")

    # The facade figures must stay ordered, or the gate becomes looser than the hard check.
    if not MIN_FACADE_PER_HABITABLE_MM <= PREF_FACADE_PER_HABITABLE_MM:
        raise AssertionError("rule 8.5 minimum must not exceed the preferred figure.")
    if not (CIRCULATION_MIN_PCT < CIRCULATION_TARGET_PCT < CIRCULATION_SOFT_MAX_PCT
            < CIRCULATION_HARD_MAX_PCT):
        raise AssertionError("rule 4.2 circulation band is out of order.")


_self_check()
_materialise_conflicts()
SPEC_CONFLICTS = tuple(dict(v) for v in _CONFLICTS.values())
