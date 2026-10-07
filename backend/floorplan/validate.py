"""Rule book section 11: the fifteen hard rejects (11.1) and the nine-term soft score (11.2).

WHAT THIS MODULE IS FOR
-----------------------
Step 10 of the mandatory generation order rejects a candidate plan; step 11 ranks the
survivors. Those are two different questions and this file never lets them become one.
``HardViolation`` and ``SoftTerm`` are separate types, they live in separate fields of
``ValidationReport``, and no arithmetic connects them. A plan with a hard violation is not a
low-scoring plan — it is not a plan, and averaging it into a fitness number is how a
rejected layout gets shipped because it happened to score well on the other eight terms.

There is a third list, ``unchecked``. A rule this module could not test — no floor below was
supplied for rule 9.3, no column grid for rule 10.1, no furniture result for rule 5.3 — is
recorded as unchecked and NEVER silently counted as a pass. The worst outcome available to a
validator is a compliant-looking verdict on something it did not measure, so every check
that needs data it was not given says so by name.

HOW THIS COEXISTS WITH THE TWO VALIDATORS ALREADY IN THE TREE
-------------------------------------------------------------
Nothing here replaces or modifies either of them. Both keep running, unchanged, until the
integration phase decides otherwise:

* ``vastu.check_unit(rooms, box, exterior_edges, entry_edge)`` — the CURRENT packer's own
  geometric re-check, working in float metres with ``EPS = 0.03``. It checks a different and
  smaller set of things (room overlap, balcony anchoring, the puja/bathroom wall rule, the
  bedroom privacy gradient) against the legacy room-dict vocabulary that ``vastu.pack_unit``
  emits. It is the authority for plans produced by that packer, and this module never sees
  those plans: they have no frozen topology, no door objects and no access graph, so twelve
  of the fifteen checks below would have nothing to read. The 30 mm float tolerance is also
  larger than three of the rule book's own thresholds (the 100-300 mm corner offset of 7.3,
  the 150 mm grid tolerance of 10.1, the 300 mm facing-door offset of 7.8), which is exactly
  why this module works in integer millimetres and does not extend ``check_unit``.

* ``aifloorplan.audit_vastu_and_mep(rooms, floor, total_floors, boxes, meta)`` — the report
  the UI's audit card renders, and the subject of the five tests in
  ``backend/tests/test_aifloorplan.py``. It aggregates ``vastu.check_unit`` and
  ``vastu.sector_report`` per unit into ``{"score", "status", "anchors", "violations",
  "unit_audits"}``. Its shape is a UI contract and is not touched here. When integration
  wires this engine in, the intended relationship is ADDITIVE: ``audit_vastu_and_mep`` keeps
  its two lists and gains a third key carrying ``ValidationReport.to_dict()``. It must not
  gain a merged list — an H06 aspect reject and a missed north-east kitchen anchor are not
  comparable findings, and the audit card's whole value is that it already refuses to merge
  them.

WHERE THE VASTU SECTOR PREFERENCE GOES
--------------------------------------
Nowhere inside the score. The 11.2 table has nine rows and none of them is vastu. The sector
anchors enter as ``SoftScore.vastu_tiebreak``, weighted ``spec.VASTU_TIEBREAK_WEIGHT``
(0.01) and added AFTER the weighted sum to produce ``ranking_score``. ``SoftScore.score`` is
the rule book's number and stays the rule book's number. A preference that can move the
score is a rule, and this one is not.

RULE BOOK CONFLICTS THIS FILE INHERITS RATHER THAN RE-RESOLVES
---------------------------------------------------------------
All three come from ``spec.py``, which resolved them once so two modules could not resolve
them differently:

1. Circulation (4.2). 8-12% is the soft target band, 12-15% is a soft penalty growing with
   distance from 12, and ONLY above ``spec.CIRCULATION_HARD_MAX_PCT`` (15%) is H10.
2. Facade, three uses and three numbers. The rule 1.1 GATE uses 3000 mm per habitable room
   and lives in ``envelope.py``. The per-room HARD check here uses
   ``spec.MIN_FACADE_PER_HABITABLE_MM`` (2500, rule 8.5's minimum) and is a second limb of
   H03 — see ``check_h03``. The soft "facade utilisation" term counts rooms under
   ``spec.PREF_FACADE_PER_HABITABLE_MM`` (3000, rule 8.5's preferred).
3. Kitchen minimum area. ``spec.min_area`` returns the code figure when the rule book is
   looser, with the pair recorded in ``spec.SPEC_CONFLICTS``. This module reads the resolved
   ``Minimum`` and reports its provenance; it never compares against a literal.

Two conflicts the rule book contains that ``spec.py`` did not have to resolve are resolved
HERE, because only a validator meets them. Both are documented at the check that decides
them: rule 4.6 versus rule 3.4 (see ``check_h08``) and rule 6.4 versus the section 6 matrix
(see ``check_h13``).

INPUT
-----
``PlanView`` and its parts are defined in this file rather than imported. The sibling
modules that will produce a plan (``geometry.py``, ``openings.py``, ``services.py``) are
being written in parallel, and a validator that imports them cannot be run against a stored
plan, a hand-edited plan or a deliberately bad fixture — which is exactly the population it
has to be trusted on. Everything ``PlanView`` holds is a plain value; the integration phase
writes one adapter from the engine's own types and one from the stored payload. The field
names deliberately match the ``openings.py`` design spec (``room_a``, ``room_b``,
``serves_room``, ``swing_room``, ``furniture_fit``) so that adapter is a rename-free copy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Mapping, Optional, Sequence

from floorplan import spec
from floorplan.spec import RectMM, RoomType, Zone


# =====================================================================================
# A. Input vocabulary
# =====================================================================================
# Every optional field below has an "absent" value that is DISTINCT from a benign one, so a
# missing input can be reported as unchecked instead of passing. `party_walls=None` means
# "nobody told me about the party walls" and `party_walls=()` means "this unit has none";
# collapsing those two into an empty tuple is how H15 would quietly stop firing.

FitResult = Literal["fits", "no_fit", "undecided"]
Point = tuple[int, int]


@dataclass(frozen=True, slots=True)
class PlanRoom:
    """One realised room. Integer millimetres, plan-absolute, matching ``spec.RectMM``.

    ``rect`` is the bounding box and is the whole shape for the rectangular case. ``polygon``
    carries the true outline when the room is L-shaped (rule 5.4.1 permits up to six
    vertices); when it is supplied it governs area and clear width, because a bounding box
    OVERSTATES both for an L and overstating a dimension is the direction that turns a fail
    into a pass.
    """

    id: str
    room_type: RoomType
    rect: RectMM
    polygon: tuple[Point, ...] = ()
    # Rule 8.5: the run of external wall this room actually owns. Not the length of its
    # wall that happens to sit on the envelope — the run that is open to air.
    facade_mm: int = 0
    # Rule 9.4/9.5: clear area of the ventilation shaft this room opens onto, 0 for none.
    shaft_clear_mm2: int = 0
    # Rule 5.3, from geometry.py's packer. None means the test was never run, which H07
    # reports as unchecked; "undecided" means the search gave up, which H07 reports as a
    # FAILURE per the packing contract in spec.py section E.
    furniture_fit: Optional[FitResult] = None
    # Set only when the producer already computed the narrowest clear dimension of a
    # non-rectangular room. Leave None and this module computes it from `polygon`.
    min_clear_mm: Optional[int] = None

    @property
    def outline(self) -> tuple[Point, ...]:
        if self.polygon:
            return self.polygon
        r = self.rect
        return ((r.x, r.y), (r.x2, r.y), (r.x2, r.y2), (r.x, r.y2))

    @property
    def area_mm2(self) -> int:
        return polygon_area_mm2(self.outline) if self.polygon else self.rect.area_mm2

    @property
    def aspect(self) -> float:
        """Rule 5.1. The bounding box governs even for an L: the rule caps the room's
        proportion, and an L that fits in a 2400 x 5600 box is the long thin room 5.1
        exists to reject however its notch is arranged."""
        return self.rect.aspect

    def clear_width_mm(self) -> Optional[int]:
        """Narrowest clear dimension anywhere in the polygon (rules 5.4.3 and H05).

        Returns None when the shape is not axis-aligned rectilinear and no ``min_clear_mm``
        was supplied. None is not a pass — ``check_h05`` records it as unchecked.
        """
        if self.min_clear_mm is not None:
            return int(self.min_clear_mm)
        if not self.polygon:
            return self.rect.short_mm
        return rectilinear_min_clear_mm(self.polygon)


@dataclass(frozen=True, slots=True)
class PlanDoor:
    """One opening. Field names match the ``openings.py`` ``Door`` design spec exactly.

    ``room_a == ""`` means the far side is outside the dwelling: that is how an entrance is
    told from an internal door, and H01 counts nothing else.
    """

    id: str
    room_a: str
    room_b: str
    # Rule 7.1's one-door budget: the id of the room this door's existence is charged to.
    # "" for through-spaces and the main door, exactly as openings.py defines it.
    serves_room: str = ""
    role: str = "room"           # main|room|ensuite|utility|balcony|service|through|panel
    kind: str = "swing"          # swing|sliding|opening|panel
    swing_room: str = ""
    # The 90 degree swept leaf as a convex polygon. openings.py circumscribes the true
    # sector so a clash is never under-reported; this module re-checks whatever it is given
    # and reports H09 as unchecked for any leaf that arrives without one.
    swing_polygon: tuple[Point, ...] = ()
    corner_offset_mm: Optional[int] = None
    wall_length_mm: Optional[int] = None
    clear_width_mm: Optional[int] = None

    @property
    def is_main(self) -> bool:
        return self.role == "main"

    @property
    def is_external(self) -> bool:
        return self.room_a == "" or self.room_b == ""

    @property
    def inner_room(self) -> str:
        """The room an external door opens into; "" for an internal door."""
        if self.room_a == "" and self.room_b != "":
            return self.room_b
        if self.room_b == "" and self.room_a != "":
            return self.room_a
        return ""

    @property
    def has_leaf(self) -> bool:
        return self.kind == "swing"

    def other_side(self, room_id: str) -> str:
        return self.room_b if self.room_a == room_id else self.room_a


@dataclass(frozen=True, slots=True)
class PlanFurniture:
    """A packed fixture. ``fixed`` is load-bearing for rule 7.5: a leaf sweeping over a WC
    or a wardrobe is a clash, over a movable armchair it is a preference."""

    room_id: str
    role: str
    rect: RectMM
    fixed: bool = True


@dataclass(frozen=True, slots=True)
class PartyWall:
    """Rule 10.3. ``segments`` are the (fixed, lo, hi) runs of wall between this unit and its
    neighbour; ``required_lo``/``required_hi`` are the span the wall must cover — the full
    unit depth. A wall that jogs has two distinct ``fixed`` values; a wall that stops short
    leaves a gap in the union. H15 fires on either."""

    id: str
    axis: Literal["v", "h"]
    segments: tuple[tuple[int, int, int], ...]
    required_lo: int
    required_hi: int


@dataclass(frozen=True, slots=True)
class PlanView:
    """Everything section 11 needs to reach a verdict, and nothing it does not.

    Optional fields are optional because a caller may legitimately not have them (a stored
    plan has no column grid; the bottom floor has no floor below). Each one has a named
    consequence in the ``unchecked`` list rather than a default that would let the rule pass.
    """

    unit_id: str
    unit_type: str
    rooms: tuple[PlanRoom, ...]
    doors: tuple[PlanDoor, ...] = ()
    furniture: tuple[PlanFurniture, ...] = ()

    envelope: Optional[RectMM] = None
    # Rule 2.3's axis: where the main door is, and the unit normal pointing INTO the unit.
    entry_point: Optional[Point] = None
    entry_inward: Optional[Point] = None

    carpet_area_mm2: Optional[int] = None
    built_up_area_mm2: Optional[int] = None

    # Rule 9.2's datum: the soil stack / shaft the wet core is measured from.
    shaft_point: Optional[Point] = None

    # Rule 10.1's column grid lines, plan-absolute.
    grid_x_mm: tuple[int, ...] = ()
    grid_y_mm: tuple[int, ...] = ()

    # None = not supplied (H15 unchecked). () = this unit has no party wall (H15 passes).
    party_walls: Optional[tuple[PartyWall, ...]] = None

    # Rule 9.3. None = not supplied (H12 unchecked) unless is_lowest_floor is True.
    floor_below_rooms: Optional[tuple[PlanRoom, ...]] = None
    is_lowest_floor: bool = False

    # Raw 0..1 soft terms computed upstream, chiefly by openings.py, which knows things this
    # module cannot recompute (the sightline cast, the per-door placement cost). Keys accept
    # both this module's names and openings.py's: see _soft_input.
    soft_inputs: Mapping[str, float] = field(default_factory=dict)

    # The 0.01 tiebreak, 0..1, from vastu.sector_report. NEVER summed into the 11.2 score.
    vastu_penalty: Optional[float] = None

    code_version: Optional[str] = None

    def room(self, room_id: str) -> Optional[PlanRoom]:
        for r in self.rooms:
            if r.id == room_id:
                return r
        return None

    def rooms_of(self, *types: RoomType) -> tuple[PlanRoom, ...]:
        want = frozenset(types)
        return tuple(r for r in self.rooms if r.room_type in want)


# =====================================================================================
# B. Result vocabulary
# =====================================================================================
@dataclass(frozen=True, slots=True)
class HardViolation:
    """One 11.1 reject. Carries the measurement, not just the verdict.

    ``measured`` and ``required`` are the point of the type. "H04 kitchen area below
    minimum" is unactionable; "5.31 m2 measured against 5.50 m2 required, NBC 2016
    Cl. 4.3.2" tells a planner how much to grow the room and tells a reviewer where the
    number came from.
    """

    code: str                    # "H01" .. "H15"
    rule: str                    # rule book rule number, e.g. "8.5"
    message: str
    measured: Any = None
    required: Any = None
    units: str = ""              # "mm" | "mm2" | "ratio" | "%" | "count" | ""
    room_ids: tuple[str, ...] = ()
    door_ids: tuple[str, ...] = ()
    provenance: Optional[dict[str, Any]] = None   # spec.Minimum.to_dict() where one applies

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "rule": self.rule, "message": self.message,
            "measured": self.measured, "required": self.required, "units": self.units,
            "room_ids": list(self.room_ids), "door_ids": list(self.door_ids),
            "rule_text": spec.HARD_RULE_TEXT.get(self.code, ""),
            "provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class UncheckedRule:
    """A rule that could not be tested, and exactly what was missing.

    This is the third list. It is neither a pass nor a fail, and a caller that renders it as
    either is misreporting. ``accepted`` on the report is True only when there are no hard
    violations, so an unchecked rule does not block a plan — it blocks the CLAIM that the
    plan was fully checked, which is what ``fully_checked`` is for.
    """

    code: str
    rule: str
    reason: str
    room_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "rule": self.rule, "reason": self.reason,
                "room_ids": list(self.room_ids),
                "rule_text": spec.HARD_RULE_TEXT.get(self.code, "")}


@dataclass(frozen=True, slots=True)
class HardCheck:
    """The result of one named 11.1 check, individually callable and individually reportable."""

    code: str
    rule: str
    violations: tuple[HardViolation, ...] = ()
    unchecked: tuple[UncheckedRule, ...] = ()
    examined: int = 0            # how many subjects the check actually looked at

    @property
    def passed(self) -> bool:
        """True only when the check ran and found nothing. An unchecked rule is not a pass,
        which is why ``fully_checked`` is a separate question below."""
        return not self.violations

    @property
    def fully_checked(self) -> bool:
        return not self.unchecked

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "rule": self.rule,
            "rule_text": spec.HARD_RULE_TEXT.get(self.code, ""),
            "passed": self.passed, "fully_checked": self.fully_checked,
            "examined": self.examined,
            "violations": [v.to_dict() for v in self.violations],
            "unchecked": [u.to_dict() for u in self.unchecked],
        }


@dataclass(frozen=True, slots=True)
class SoftTerm:
    """One of the nine 11.2 rows. ``raw`` is 0..1, ``weighted`` is raw * weight.

    ``measured`` is False when the input the term needs was absent. Such a term contributes
    0 — the only neutral choice — and its name appears in ``SoftScore.unmeasured`` so a
    report can say the score is incomplete rather than presenting 0.31 as a full fitness.
    """

    key: str
    weight: float
    raw: float
    measured: bool = True
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def weighted(self) -> float:
        return self.weight * self.raw

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "weight": self.weight, "raw": round(self.raw, 6),
                "weighted": round(self.weighted, 6), "measured": self.measured,
                "detail": dict(self.detail)}


@dataclass(frozen=True, slots=True)
class SoftScore:
    """The 11.2 fitness. Lower is better.

    ``score`` is the weighted sum of the nine rule book terms and nothing else.
    ``ranking_score`` is ``score`` plus the vastu tiebreak, and is what a comparator should
    sort on. Keeping them as two fields rather than one is the whole mechanism by which a
    0.01 preference can break a tie without ever being able to move the rule book's number.
    """

    terms: tuple[SoftTerm, ...]
    vastu_penalty: float = 0.0
    vastu_measured: bool = False

    @property
    def score(self) -> float:
        return sum(t.weighted for t in self.terms)

    @property
    def vastu_tiebreak(self) -> float:
        return spec.VASTU_TIEBREAK_WEIGHT * self.vastu_penalty

    @property
    def ranking_score(self) -> float:
        return self.score + self.vastu_tiebreak

    @property
    def unmeasured(self) -> tuple[str, ...]:
        return tuple(t.key for t in self.terms if not t.measured)

    def term(self, key: str) -> Optional[SoftTerm]:
        for t in self.terms:
            if t.key == key:
                return t
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 6),
            "vastu_penalty": round(self.vastu_penalty, 6),
            "vastu_weight": spec.VASTU_TIEBREAK_WEIGHT,
            "vastu_tiebreak": round(self.vastu_tiebreak, 6),
            "vastu_measured": self.vastu_measured,
            "ranking_score": round(self.ranking_score, 6),
            "unmeasured": list(self.unmeasured),
            "terms": [t.to_dict() for t in self.terms],
            "note": ("`score` is the rule book 11.2 weighted sum and contains no vastu "
                     "term. The sector preference is applied afterwards as `vastu_tiebreak` "
                     "and only `ranking_score` carries it."),
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Hard, soft and unchecked, in three fields that never merge.

    ``accepted`` answers step 10 and reads ONLY the hard list. ``soft`` answers step 11 and
    is meaningful only for an accepted plan — a rejected plan's score is computed anyway,
    because a search wants to know how close a reject came, but it must never be the reason
    the plan is kept.
    """

    unit_id: str
    unit_type: str
    checks: tuple[HardCheck, ...]
    soft: SoftScore
    notes: tuple[str, ...] = ()

    @property
    def hard(self) -> tuple[HardViolation, ...]:
        return tuple(v for c in self.checks for v in c.violations)

    @property
    def unchecked(self) -> tuple[UncheckedRule, ...]:
        return tuple(u for c in self.checks for u in c.unchecked)

    @property
    def accepted(self) -> bool:
        return not self.hard

    @property
    def fully_checked(self) -> bool:
        return not self.unchecked

    @property
    def codes_fired(self) -> tuple[str, ...]:
        seen: list[str] = []
        for v in self.hard:
            if v.code not in seen:
                seen.append(v.code)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id, "unit_type": self.unit_type,
            "accepted": self.accepted, "fully_checked": self.fully_checked,
            "codes_fired": list(self.codes_fired),
            "hard": [v.to_dict() for v in self.hard],
            "unchecked": [u.to_dict() for u in self.unchecked],
            "soft": self.soft.to_dict(),
            "checks": [c.to_dict() for c in self.checks],
            "notes": list(self.notes),
            "note": ("Three lists, never merged: `hard` rejects the plan, `soft` ranks it, "
                     "`unchecked` names the rules that could not be tested at all."),
        }


# =====================================================================================
# C. Geometry helpers
# =====================================================================================
# Integer millimetres throughout. The only floats are ratios and the point-in-polygon
# midpoint test, which decides topology rather than a dimension.

def polygon_area_mm2(poly: Sequence[Point]) -> int:
    """Shoelace. Exact for the axis-aligned polygons rule 5.4.1 permits."""
    n = len(poly)
    if n < 3:
        return 0
    total = 0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) // 2


def _point_in_polygon(px: float, py: float, poly: Sequence[Point]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            xint = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < xint:
                inside = not inside
    return inside


def rectilinear_min_clear_mm(poly: Sequence[Point]) -> Optional[int]:
    """Narrowest clear dimension anywhere inside an axis-aligned polygon, in mm.

    Rule 5.4.3 says "no room may have a clear dimension below 900 ANYWHERE in its polygon",
    which the bounding box cannot answer: the short side of an L's bounding box is the wide
    leg, not the narrow one, and reporting it would pass exactly the notch 5.4.2 exists to
    reject. So the polygon is cut into cells on its own coordinate breaks, each cell is
    tested for being inside, and the answer is the shortest maximal run of inside cells in
    either direction — which for an L is the smaller leg's width, and for a rectangle is its
    short side.

    Returns None for a polygon that is not axis-aligned, because a diagonal wall has no
    "clear dimension" this method can honestly report.
    """
    if len(poly) < 4:
        return None
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if x1 != x2 and y1 != y2:
            return None                      # a diagonal edge; not rectilinear
    xs = sorted({p[0] for p in poly})
    ys = sorted({p[1] for p in poly})
    if len(xs) < 2 or len(ys) < 2:
        return None
    inside = [[_point_in_polygon((xs[i] + xs[i + 1]) / 2.0, (ys[j] + ys[j + 1]) / 2.0, poly)
               for j in range(len(ys) - 1)] for i in range(len(xs) - 1)]

    best: Optional[int] = None
    for i in range(len(xs) - 1):             # vertical runs: how tall is this column of cells
        j = 0
        while j < len(ys) - 1:
            if not inside[i][j]:
                j += 1
                continue
            k = j
            while k + 1 < len(ys) - 1 and inside[i][k + 1]:
                k += 1
            span = ys[k + 1] - ys[j]
            best = span if best is None else min(best, span)
            j = k + 1
    for j in range(len(ys) - 1):             # horizontal runs: how wide is this row of cells
        i = 0
        while i < len(xs) - 1:
            if not inside[i][j]:
                i += 1
                continue
            k = i
            while k + 1 < len(xs) - 1 and inside[k + 1][j]:
                k += 1
            span = xs[k + 1] - xs[i]
            best = span if best is None else min(best, span)
            i = k + 1
    return best


def polygons_overlap(p: Sequence[Point], q: Sequence[Point], slack_mm: int = 0) -> bool:
    """Separating-axis test on two convex polygons. Touching is not overlapping.

    ``slack_mm`` shrinks both shapes before testing, so a leaf that merely grazes a wardrobe
    by a millimetre of rounding is not reported as a rule 7.5 clash. It defaults to 0: the
    caller decides how much reality to allow, and this function will not decide it silently.
    """
    if len(p) < 3 or len(q) < 3:
        return False
    for a, b in ((p, q), (q, p)):
        n = len(a)
        for i in range(n):
            x1, y1 = a[i]
            x2, y2 = a[(i + 1) % n]
            ax, ay = -(y2 - y1), (x2 - x1)
            length = math.hypot(ax, ay)
            if length == 0:
                continue
            ax, ay = ax / length, ay / length
            amin = min(ax * px + ay * py for px, py in a)
            amax = max(ax * px + ay * py for px, py in a)
            bmin = min(ax * px + ay * py for px, py in b)
            bmax = max(ax * px + ay * py for px, py in b)
            if amax - slack_mm <= bmin or bmax <= amin + slack_mm:
                return False
    return True


def rect_points(r: RectMM) -> tuple[Point, ...]:
    return ((r.x, r.y), (r.x2, r.y), (r.x2, r.y2), (r.x, r.y2))


def rects_overlap_mm2(a: RectMM, b: RectMM) -> int:
    """Area of the intersection. Zero when they merely touch."""
    dx = min(a.x2, b.x2) - max(a.x, b.x)
    dy = min(a.y2, b.y2) - max(a.y, b.y)
    return dx * dy if dx > 0 and dy > 0 else 0


def _project(point: Point, origin: Point, direction: Point) -> float:
    """Signed distance of ``point`` from ``origin`` along ``direction`` (need not be unit)."""
    dx, dy = direction
    length = math.hypot(dx, dy) or 1.0
    return ((point[0] - origin[0]) * dx + (point[1] - origin[1]) * dy) / length


def _axis_span(room: PlanRoom, origin: Point, direction: Point) -> tuple[float, float]:
    """(near edge, far edge) of a room along the entry axis."""
    values = [_project(p, origin, direction) for p in room.outline]
    return min(values), max(values)


# =====================================================================================
# C2. Derived plan quantities
# =====================================================================================
def circulation_area_mm2(plan: PlanView) -> int:
    """Rule 4.2: "foyer + corridors, EXCLUDING living and dining".

    The exclusion is the whole reason this is not just "space with no furniture in it". An
    open-plan living-dining does a great deal of circulating and none of it counts here.
    """
    return sum(r.area_mm2 for r in plan.rooms
               if r.room_type in (RoomType.FOYER, RoomType.CORRIDOR))


def carpet_area_mm2(plan: PlanView) -> tuple[int, bool]:
    """(carpet area, was_derived).

    A supplied figure always wins. The derivation — every room except balconies and shafts —
    is stated rather than assumed because a balcony counted as carpet inflates the
    denominator of H10 and of the carpet-efficiency term, both in the permissive direction.
    """
    if plan.carpet_area_mm2 is not None:
        return int(plan.carpet_area_mm2), False
    derived = sum(r.area_mm2 for r in plan.rooms
                  if r.room_type not in (RoomType.BALCONY, RoomType.SHAFT))
    return derived, True


def door_graph(plan: PlanView) -> dict[str, set[str]]:
    """Room-to-room adjacency from the door schedule. External doors contribute the id ""."""
    graph: dict[str, set[str]] = {r.id: set() for r in plan.rooms}
    for d in plan.doors:
        if d.kind == "panel":
            continue                       # a shaft maintenance panel is not a way through
        if d.room_a in graph:
            graph[d.room_a].add(d.room_b)
        if d.room_b in graph:
            graph[d.room_b].add(d.room_a)
    return graph


def _shaft_qualifies(room: PlanRoom) -> bool:
    """Rule 9.4: a shaft counts only at 600 x 600 clear or better."""
    return room.shaft_clear_mm2 >= spec.SHAFT_MIN_CLEAR_MM * spec.SHAFT_MIN_CLEAR_MM


# =====================================================================================
# D. Rule book 11.1 — the fifteen hard rejects
# =====================================================================================
# Each check is a standalone function taking the plan and returning a HardCheck. They are
# individually callable on purpose: a search loop that has just mutated a door offset wants
# to re-run H09 and nothing else, and a test wants to assert that one code fires on one
# fixture without building a plan that satisfies the other fourteen.

def check_h01(plan: PlanView) -> HardCheck:
    """H01 — exactly one main entrance (rule 3.1).

    Rule 3.1's only exception is a SERVICE door from the kitchen or utility onto a dedicated
    service corridor. It is enforced narrowly: an external door is exempt only when its role
    says service AND the room it opens into is the kitchen or the utility. An external door
    into anything else is an entrance whatever it calls itself, which is precisely the
    failure the rule book diagnoses in section 0 — "doors placed wherever a wall happens to
    touch a corridor".
    """
    external = [d for d in plan.doors if d.is_external]
    entrances: list[PlanDoor] = []
    service: list[PlanDoor] = []
    for d in external:
        inner = plan.room(d.inner_room)
        if (d.role == "service" and inner is not None
                and inner.room_type in (RoomType.KITCHEN, RoomType.UTILITY)):
            service.append(d)
        else:
            entrances.append(d)

    violations: list[HardViolation] = []
    if len(entrances) != spec.MAIN_ENTRANCE_COUNT:
        violations.append(HardViolation(
            code="H01", rule="3.1",
            message=(f"{len(entrances)} external door(s) open into habitable space; rule 3.1 "
                     f"allows exactly {spec.MAIN_ENTRANCE_COUNT}. "
                     f"{len(service)} further external door(s) were accepted as service "
                     "doors from the kitchen or utility."),
            measured=len(entrances), required=spec.MAIN_ENTRANCE_COUNT, units="count",
            door_ids=tuple(d.id for d in entrances)))
    return HardCheck("H01", "3.1", tuple(violations), (), len(external))


def check_h02(plan: PlanView) -> HardCheck:
    """H02 — the main door opens into a foyer and nothing else (rule 3.2).

    ``spec.MAIN_DOOR_OPENS_INTO`` is the sole authority for the permitted set, so this check
    and the topology builder cannot disagree about whether a main door into a living room is
    acceptable. It is not: rule 3.2 names living, bedroom, kitchen, dining and toilet as
    forbidden, which leaves the foyer.
    """
    mains = [d for d in plan.doors if d.is_main]
    violations: list[HardViolation] = []
    unchecked: list[UncheckedRule] = []
    if not mains:
        unchecked.append(UncheckedRule(
            "H02", "3.2", "no door is marked role='main', so there is nothing to test. "
                          "H01 reports the entrance count separately."))
    for d in mains:
        inner_id = d.inner_room or d.room_b
        inner = plan.room(inner_id)
        if inner is None:
            unchecked.append(UncheckedRule(
                "H02", "3.2",
                f"main door {d.id} names room {inner_id!r}, which is not in the plan"))
            continue
        if inner.room_type not in spec.MAIN_DOOR_OPENS_INTO:
            violations.append(HardViolation(
                code="H02", rule="3.2",
                message=(f"the main door opens into a {inner.room_type}; rule 3.2 requires a "
                         "foyer, never directly into living, bedroom, kitchen, dining or "
                         "toilet"),
                measured=str(inner.room_type),
                required=sorted(str(t) for t in spec.MAIN_DOOR_OPENS_INTO),
                room_ids=(inner.id,), door_ids=(d.id,)))
    return HardCheck("H02", "3.2", tuple(violations), tuple(unchecked), len(mains))


def check_h03(plan: PlanView) -> HardCheck:
    """H03 — no landlocked habitable room, and none starved of facade (rules 8.1 and 8.5).

    TWO LIMBS, ONE CODE. The 11.1 list has one entry for light and ventilation, and the
    project's binding decision puts the per-room 2500 mm facade minimum (rule 8.5) in
    ``validate.py`` as a HARD check. There is no H16 to put it under and inventing one would
    put a code in the report that the rule book does not define, so it is the second limb of
    H03 and each violation carries its own rule number — "8.1" for landlocked, "8.5" for
    starved. A habitable room with 900 mm of external wall is not landlocked in the literal
    sense and is still a room nobody can put a window in.

    THE SHAFT SUBSTITUTION IS KITCHEN-ONLY. Rule 8.1 reads as though any habitable room may
    take a shaft instead of a facade. ``spec.FACADE_EXEMPT_WITH_SHAFT`` narrows that to the
    kitchen, on rule 9.5's authority and on the project decision that no bedroom is ever
    talked out of its window. This is STRICTER than the rule book. It is stated here rather
    than left to be discovered, because a check that is quietly stricter than its own rule is
    as much a defect as one that is quietly looser.
    """
    violations: list[HardViolation] = []
    habitable = [r for r in plan.rooms if r.room_type in spec.HABITABLE]
    for r in habitable:
        shafted = _shaft_qualifies(r) and r.room_type in spec.FACADE_EXEMPT_WITH_SHAFT
        if shafted:
            continue
        if r.facade_mm <= 0:
            why = ""
            if r.shaft_clear_mm2 > 0 and r.room_type not in spec.FACADE_EXEMPT_WITH_SHAFT:
                why = (" It opens onto a shaft, but rule 9.5 grants the shaft substitution "
                       "to the kitchen only.")
            elif r.shaft_clear_mm2 > 0:
                why = (f" Its shaft is {r.shaft_clear_mm2 / spec.MM2_PER_M2:.2f} m2 clear, "
                       f"below the {spec.SHAFT_MIN_CLEAR_MM} x {spec.SHAFT_MIN_CLEAR_MM} "
                       "rule 9.4 requires.")
            violations.append(HardViolation(
                code="H03", rule="8.1",
                message=(f"{r.room_type} {r.id} is landlocked: no external wall and no "
                         f"code-compliant shaft.{why}"),
                measured=0, required=spec.MIN_FACADE_PER_HABITABLE_MM, units="mm",
                room_ids=(r.id,)))
        elif r.facade_mm < spec.MIN_FACADE_PER_HABITABLE_MM:
            violations.append(HardViolation(
                code="H03", rule="8.5",
                message=(f"{r.room_type} {r.id} has {r.facade_mm} mm of external wall; rule "
                         f"8.5 requires at least {spec.MIN_FACADE_PER_HABITABLE_MM} mm per "
                         f"habitable room ({spec.PREF_FACADE_PER_HABITABLE_MM} preferred)"),
                measured=r.facade_mm, required=spec.MIN_FACADE_PER_HABITABLE_MM, units="mm",
                room_ids=(r.id,)))
    return HardCheck("H03", "8.1/8.5", tuple(violations), (), len(habitable))


def check_h04(plan: PlanView) -> HardCheck:
    """H04 — every room at or above its minimum area (rule 5.2, resolved against the code).

    The threshold is never a literal here. ``spec.min_area`` resolves the rule book's design
    target against the iscodes figure and returns a ``Minimum`` carrying which of the two
    governed and whether it was actually read from a standard; that provenance rides on the
    violation so a report can distinguish "below the NBC floor" from "below an architect's
    preference". Rooms in ``spec.NO_AREA_MINIMUM`` are not failures and not passes — they are
    reported as unchecked in one aggregated entry, because neither the code nor the rule book
    states an area for them and this module will not supply one.
    """
    violations: list[HardViolation] = []
    no_minimum: list[str] = []
    examined = 0
    for r in plan.rooms:
        if r.room_type in spec.NO_AREA_MINIMUM:
            no_minimum.append(r.id)
            continue
        try:
            m = spec.min_area(r.room_type, plan.code_version, unit_type=plan.unit_type)
        except spec.SpecUnavailable as exc:
            no_minimum.append(f"{r.id} ({exc})")
            continue
        examined += 1
        required = int(m.value)
        if r.area_mm2 < required:
            violations.append(HardViolation(
                code="H04", rule="5.2",
                message=(f"{r.room_type} {r.id} is {r.area_mm2 / spec.MM2_PER_M2:.2f} m2, "
                         f"below the {required / spec.MM2_PER_M2:.2f} m2 minimum "
                         f"({m.binding}, {'verified' if m.verified else 'unverified'})"),
                measured=r.area_mm2, required=required, units="mm2",
                room_ids=(r.id,), provenance=m.to_dict()))
    unchecked: tuple[UncheckedRule, ...] = ()
    if no_minimum:
        unchecked = (UncheckedRule(
            "H04", "5.2",
            "neither the code nor the rule book states a minimum area for these rooms, so "
            "none was checked and none was invented",
            tuple(no_minimum)),)
    return HardCheck("H04", "5.2", tuple(violations), unchecked, examined)


def check_h05(plan: PlanView) -> HardCheck:
    """H05 — every room at or above its minimum clear width (rules 5.2 and 5.4.3).

    "Clear width" is the narrowest dimension ANYWHERE in the polygon, not the short side of
    the bounding box. For a rectangle those are the same number; for an L they are not, and
    the bounding box is the larger of the two — the direction that turns a fail into a pass.
    A room whose shape this module cannot measure is reported as unchecked rather than
    measured with the wrong tool.
    """
    violations: list[HardViolation] = []
    unmeasurable: list[str] = []
    examined = 0
    for r in plan.rooms:
        try:
            m = spec.min_width(r.room_type, plan.code_version)
        except spec.SpecUnavailable:
            unmeasurable.append(f"{r.id} (no stated minimum width)")
            continue
        clear = r.clear_width_mm()
        if clear is None:
            unmeasurable.append(f"{r.id} (polygon is not axis-aligned; supply min_clear_mm)")
            continue
        examined += 1
        required = int(m.value)
        if clear < required:
            violations.append(HardViolation(
                code="H05", rule="5.2",
                message=(f"{r.room_type} {r.id} has a clear width of {clear} mm, below the "
                         f"{required} mm minimum ({m.binding}, "
                         f"{'verified' if m.verified else 'unverified'})"),
                measured=clear, required=required, units="mm",
                room_ids=(r.id,), provenance=m.to_dict()))
        elif clear < spec.MIN_CLEAR_DIMENSION_MM:
            # Unreachable while every design target is >= 900, and kept because rule 5.4.3 is
            # a floor under every room type including any added later with a smaller target.
            violations.append(HardViolation(
                code="H05", rule="5.4.3",
                message=(f"{r.room_type} {r.id} narrows to {clear} mm somewhere in its "
                         f"polygon; rule 5.4.3 sets an absolute floor of "
                         f"{spec.MIN_CLEAR_DIMENSION_MM} mm"),
                measured=clear, required=spec.MIN_CLEAR_DIMENSION_MM, units="mm",
                room_ids=(r.id,)))
    unchecked: tuple[UncheckedRule, ...] = ()
    if unmeasurable:
        unchecked = (UncheckedRule("H05", "5.2", "clear width could not be measured",
                                   tuple(unmeasurable)),)
    return HardCheck("H05", "5.2", tuple(violations), unchecked, examined)


def check_h06(plan: PlanView) -> HardCheck:
    """H06 — aspect ratio at or below the table 5.1 hard cap.

    Strictly above the cap fails, which is why ``AspectCap.reject`` is documented in spec.py
    as "H06 fires strictly above this": a bedroom at exactly 1.70 is at the published limit,
    not past it, and rejecting it would enforce a cap the table does not state. Room types in
    ``spec.ASPECT_EXEMPT`` are skipped — the corridor is linear by definition and a balcony
    is a strip by construction.

    This is the check the whole rewrite exists to make pass. The old packer's full-depth
    column gives a 2.6 m room on a 6.6 m plate: aspect 2.5, over every cap in the table.
    """
    violations: list[HardViolation] = []
    examined = 0
    for r in plan.rooms:
        cap = spec.aspect_cap(r.room_type)
        if cap is None:
            continue
        examined += 1
        if r.aspect > cap.reject:
            violations.append(HardViolation(
                code="H06", rule="5.1",
                message=(f"{r.room_type} {r.id} is {r.rect.long_mm} x {r.rect.short_mm} mm, "
                         f"aspect 1:{r.aspect:.2f}, above the 1:{cap.reject:.2f} hard cap "
                         f"(ideal 1:{cap.ideal:.2f}, warn 1:{cap.warn:.2f})"),
                measured=round(r.aspect, 3), required=cap.reject, units="ratio",
                room_ids=(r.id,)))
    return HardCheck("H06", "5.1", tuple(violations), (), examined)


def check_h07(plan: PlanView) -> HardCheck:
    """H07 — the rule 5.3 furniture packing test.

    This module does not re-run the packer; it reads the result geometry.py recorded. Three
    outcomes, from the packing contract in spec.py section E:

      "fits"      -> pass
      "no_fit"    -> H07
      "undecided" -> H07, labelled distinctly. A search that hit
                     ``spec.FURNITURE_SEARCH_MAX_NODES`` and gave up is not evidence the
                     furniture fits, and treating it as one would let the single test that
                     actually kills a 2400 x 5500 "bedroom" be defeated by a node budget.
      None        -> unchecked. The test was never run.
    """
    violations: list[HardViolation] = []
    never_run: list[str] = []
    examined = 0
    for r in plan.rooms:
        programme = spec.furniture_for(r.room_type)
        if programme is None:
            continue                        # area plus clear width IS the whole test here
        if r.furniture_fit is None:
            never_run.append(r.id)
            continue
        examined += 1
        if r.furniture_fit == "no_fit":
            violations.append(HardViolation(
                code="H07", rule="5.3",
                message=(f"{r.room_type} {r.id} ({r.rect.w} x {r.rect.h} mm) cannot pack its "
                         "rule 5.3 programme: "
                         + ", ".join(p.key for p in programme.pieces)
                         + (", " + ", ".join(x.key for x in programme.runs)
                            if programme.runs else "")),
                measured="no_fit", required="fits",
                room_ids=(r.id,)))
        elif r.furniture_fit == "undecided":
            violations.append(HardViolation(
                code="H07", rule="5.3",
                message=(f"{r.room_type} {r.id}: the packing search gave up before deciding "
                         f"(node cap {spec.FURNITURE_SEARCH_MAX_NODES}). An undecided search "
                         "is not a pass, so H07 fires."),
                measured="undecided", required="fits",
                room_ids=(r.id,)))
    unchecked: tuple[UncheckedRule, ...] = ()
    if never_run:
        unchecked = (UncheckedRule(
            "H07", "5.3", "no packing result was supplied for these rooms; the rule 5.3 test "
                          "was never run", tuple(never_run)),)
    return HardCheck("H07", "5.3", tuple(violations), unchecked, examined)


def check_h08(plan: PlanView) -> HardCheck:
    """H08 — every room reachable from the foyer per rule 4.6.

    A RULE BOOK CONFLICT RESOLVED HERE. Rule 4.6 forbids reaching a habitable room by passing
    through another habitable room. Rule 3.4 mandates the sequence
    ``foyer -> living -> (dining | corridor) -> private zone``, which passes through the
    living room — a habitable room. Read literally, 4.6 rejects the plan 3.4 requires.

    Resolution: "another habitable room" means one that is not a THROUGH-SPACE. Rule 7.1
    already names living, dining, foyer and corridor as through-spaces and exempts them from
    the one-door rule for exactly this reason, and ``spec.THROUGH_SPACES`` is that list. So a
    path may cross a through-space freely, may cross any non-habitable room, and may cross a
    habitable non-through-space room only via a pair in ``spec.PASS_THROUGH_ALLOWED``
    (an en-suite through its bedroom, a utility through the kitchen, and so on). Anything
    else is H08. This resolution is stated because it is a choice: the alternative reading
    rejects every plan the rule book's own section 3 describes.
    """
    foyers = [r for r in plan.rooms if r.room_type is RoomType.FOYER]
    if not foyers:
        return HardCheck("H08", "4.6", (), (UncheckedRule(
            "H08", "4.6", "the plan has no foyer, so reachability has no origin. H02 reports "
                          "the entry sequence separately."),), 0)

    by_id = {r.id: r for r in plan.rooms}
    graph = door_graph(plan)
    start = foyers[0].id

    def crossable(room_id: str, target: PlanRoom) -> bool:
        room = by_id.get(room_id)
        if room is None:
            return False
        if room.room_type in spec.THROUGH_SPACES:
            return True
        if room.room_type not in spec.HABITABLE:
            return True
        return (room.room_type, target.room_type) in spec.PASS_THROUGH_ALLOWED

    violations: list[HardViolation] = []
    for target in plan.rooms:
        if target.id == start:
            continue
        # Breadth-first over rooms that may be CROSSED to reach this particular target. The
        # legality of an intermediate depends on the destination (rule 4.6's exceptions are
        # stated as (crossed, reached) pairs), so the traversal is re-run per target rather
        # than computed once — fifteen rooms make this free and sharing it would be wrong.
        seen = {start}
        queue = [start]
        found = False
        while queue and not found:
            here = queue.pop(0)
            for nxt in sorted(graph.get(here, ())):
                if nxt == "" or nxt in seen:
                    continue
                if nxt == target.id:
                    found = True
                    break
                if crossable(nxt, target):
                    seen.add(nxt)
                    queue.append(nxt)
        if not found:
            reachable_at_all = target.id in _flood(graph, start)
            reason = ("is not connected to the foyer by any door"
                      if not reachable_at_all else
                      "can only be reached by passing through another habitable room, which "
                      "rule 4.6 permits only for an attached toilet or dressing off its "
                      "bedroom, a utility off the kitchen, or a dining off the living")
            violations.append(HardViolation(
                code="H08", rule="4.6",
                message=f"{target.room_type} {target.id} {reason}",
                measured="unreachable", required="reachable from the foyer",
                room_ids=(target.id,)))
    return HardCheck("H08", "4.6", tuple(violations), (), len(plan.rooms) - 1)


def _flood(graph: Mapping[str, set[str]], start: str) -> set[str]:
    """Plain connectivity, ignoring rule 4.6. Used only to word an H08 message correctly."""
    seen = {start}
    queue = [start]
    while queue:
        here = queue.pop(0)
        for nxt in graph.get(here, ()):
            if nxt and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def check_h09(plan: PlanView) -> HardCheck:
    """H09 — no door swing arc collision (rule 7.5).

    Two subjects, both named by the rule: leaf against leaf, and leaf against FIXED furniture.
    ``PlanFurniture.fixed`` decides the second: a leaf sweeping over a WC or a wardrobe is a
    door that cannot open, while a leaf over a movable chair is a preference and belongs in
    the soft door-placement term, not here.

    A swing-kind door that arrives with no ``swing_polygon`` is reported as unchecked, not as
    clear. openings.py circumscribes the true sector so a clash is never under-reported; if
    the caller built its own polygon it owns that guarantee.
    """
    leaves = [d for d in plan.doors if d.has_leaf]
    missing = [d.id for d in leaves if not d.swing_polygon]
    testable = [d for d in leaves if d.swing_polygon]

    violations: list[HardViolation] = []
    for i in range(len(testable)):
        for j in range(i + 1, len(testable)):
            a, b = testable[i], testable[j]
            if polygons_overlap(a.swing_polygon, b.swing_polygon):
                violations.append(HardViolation(
                    code="H09", rule="7.5",
                    message=(f"the leaves of doors {a.id} and {b.id} sweep the same floor; "
                             "rule 7.5 forbids any overlap of two 90 degree arcs"),
                    measured="overlapping arcs", required="disjoint arcs",
                    door_ids=(a.id, b.id)))
    for d in testable:
        for f in plan.furniture:
            if not f.fixed:
                continue
            if d.swing_room and f.room_id != d.swing_room:
                continue
            if polygons_overlap(d.swing_polygon, rect_points(f.rect)):
                violations.append(HardViolation(
                    code="H09", rule="7.5",
                    message=(f"door {d.id} sweeps over fixed furniture ({f.role}) in "
                             f"{f.room_id}; rule 7.5 checks every leaf against fixed "
                             "furniture as well as against other leaves"),
                    measured=f"clash with {f.role}", required="clear sweep",
                    door_ids=(d.id,), room_ids=(f.room_id,)))
    unchecked: tuple[UncheckedRule, ...] = ()
    if missing:
        unchecked = (UncheckedRule(
            "H09", "7.5", "these swing doors carry no swept-leaf polygon, so their arcs were "
                          "not tested against anything", tuple(missing)),)
    return HardCheck("H09", "7.5", tuple(violations), unchecked, len(testable))


def check_h10(plan: PlanView) -> HardCheck:
    """H10 — circulation area above 15% of carpet area (rule 4.2).

    The 8/12/15 ambiguity in rule 4.2 was resolved once in spec.py and is NOT re-resolved
    here: only ``spec.CIRCULATION_HARD_MAX_PCT`` rejects. Between 12 and 15 the plan is
    accepted and pays a growing soft penalty in ``soft_circulation_efficiency``; below 8 it
    is accepted and pays a penalty for rooms opening off each other. A validator that
    rejected at 12 would be enforcing a rule book nobody wrote.

    The comparison cross-multiplies rather than dividing, so a plan at exactly 15.000% is
    accepted and a floating-point remainder cannot push it either way.
    """
    circ = circulation_area_mm2(plan)
    carpet, derived = carpet_area_mm2(plan)
    if carpet <= 0:
        return HardCheck("H10", "4.2", (), (UncheckedRule(
            "H10", "4.2", "carpet area is zero or unknown, so a percentage cannot be "
                          "computed"),), 0)
    pct = 100.0 * circ / carpet
    violations: list[HardViolation] = []
    if circ * 100.0 > spec.CIRCULATION_HARD_MAX_PCT * carpet:
        violations.append(HardViolation(
            code="H10", rule="4.2",
            message=(f"circulation (foyer + corridor) is {pct:.1f}% of carpet area, above "
                     f"the {spec.CIRCULATION_HARD_MAX_PCT:.0f}% hard limit"
                     + (" (carpet area was derived from the room areas, not supplied)"
                        if derived else "")),
            measured=round(pct, 2), required=spec.CIRCULATION_HARD_MAX_PCT, units="%"))
    return HardCheck("H10", "4.2", tuple(violations), (), 1)


def check_h11(plan: PlanView) -> HardCheck:
    """H11 — zone order along the entry axis is monotonic (rule 2.3).

    The test is the rule book's own example: "a bedroom between the foyer and the living room
    is a hard reject". So the geometric statement is — a room of a LATER zone may not lie
    ENTIRELY nearer the entrance than a room of an EARLIER zone. Comparing centroids instead
    would reject a deep living room that merely overlaps a bedroom's band, which is normal
    and not what 2.3 forbids.

    ``Zone.SERVICE`` takes no part: spec.py deliberately leaves it out of ``ZONE_ORDER``
    because rule 2.2 constrains the service zone by adjacency, not by position, and a
    master-bedroom balcony would otherwise break the ordering merely by existing. The
    corridor is exempt via ``spec.ZONE_ORDER_EXEMPT`` — its whole job is to span the gradient.
    """
    if plan.entry_point is None or plan.entry_inward is None:
        return HardCheck("H11", "2.3", (), (UncheckedRule(
            "H11", "2.3", "no entry point or inward direction was supplied, so there is no "
                          "axis to measure zone order along"),), 0)

    bands: dict[Zone, list[tuple[PlanRoom, float, float]]] = {z: [] for z in spec.ZONE_ORDER}
    for r in plan.rooms:
        if r.room_type in spec.ZONE_ORDER_EXEMPT:
            continue
        z = spec.zone_of(r.room_type)
        if z not in bands:
            continue                     # SERVICE, by design
        near, far = _axis_span(r, plan.entry_point, plan.entry_inward)
        bands[z].append((r, near, far))

    violations: list[HardViolation] = []
    order = list(spec.ZONE_ORDER)
    for i, earlier in enumerate(order):
        for later in order[i + 1:]:
            for lr, lnear, lfar in bands[later]:
                for er, enear, efar in bands[earlier]:
                    if lfar <= enear:
                        violations.append(HardViolation(
                            code="H11", rule="2.3",
                            message=(f"{later} room {lr.id} ({lr.room_type}) lies entirely "
                                     f"nearer the entrance than {earlier} room {er.id} "
                                     f"({er.room_type}); rule 2.3 requires "
                                     + " -> ".join(str(z) for z in spec.ZONE_ORDER)
                                     + " along the entry axis"),
                            measured=round(lfar, 1), required=round(enear, 1), units="mm",
                            room_ids=(lr.id, er.id)))
    examined = sum(len(v) for v in bands.values())
    return HardCheck("H11", "2.3", tuple(violations), (), examined)


def check_h12(plan: PlanView) -> HardCheck:
    """H12 — no wet area stacked over a habitable room (rule 9.3).

    ``spec.WET`` is the authority for what counts as wet, and it includes the kitchen and the
    utility as well as the toilets: rule 9.1 clusters all three around one shaft and 9.3 says
    wet areas must stack. That is stricter than the rule book's own worked example, which
    names only "a bedroom directly under a toilet", and it is spec.py's classification rather
    than a decision taken here.

    Overlap is measured by area, not by corner containment: two rooms that share a wall line
    overlap by zero and are not stacked.
    """
    if plan.is_lowest_floor:
        return HardCheck("H12", "9.3", (), (), 0)
    if plan.floor_below_rooms is None:
        return HardCheck("H12", "9.3", (), (UncheckedRule(
            "H12", "9.3", "no floor below was supplied and the plan is not marked as the "
                          "lowest floor, so vertical stacking was not tested"),), 0)

    wet = [r for r in plan.rooms if r.room_type in spec.WET]
    violations: list[HardViolation] = []
    for w in wet:
        for below in plan.floor_below_rooms:
            if below.room_type not in spec.HABITABLE:
                continue
            if below.room_type in spec.WET:
                continue                  # a kitchen under a kitchen is a stack, not a clash
            overlap = rects_overlap_mm2(w.rect, below.rect)
            if overlap > 0:
                violations.append(HardViolation(
                    code="H12", rule="9.3",
                    message=(f"{w.room_type} {w.id} sits over {below.room_type} {below.id} on "
                             f"the floor below, overlapping "
                             f"{overlap / spec.MM2_PER_M2:.2f} m2; rule 9.3 requires wet "
                             "areas to stack over wet or non-habitable space"),
                    measured=overlap, required=0, units="mm2",
                    room_ids=(w.id, below.id)))
    return HardCheck("H12", "9.3", tuple(violations), (), len(wet))


def check_h13(plan: PlanView) -> HardCheck:
    """H13 — no toilet door into the kitchen or the dining room (rule 6.4).

    A RULE BOOK CONFLICT RESOLVED HERE. The section 6 matrix grades Dining-CommonToilet as
    "A", allowed; rule 6.4 says no toilet door opens directly onto the dining area; and 11.1
    lists the pairing as a hard reject. 11.1 and 6.4 agree against the matrix, and a hard
    reject is the more specific statement, so the door is forbidden.

    Scope: ``RoomType.DINING`` and ``RoomType.KITCHEN`` only. ``LIVING_DINING`` is NOT
    included, and that is a decision. The rule is about a toilet door beside a dining table,
    and an open-plan great room is a different object — on this project it is the default
    programme at every tier, so folding it in would reject the studio, whose common toilet has
    no other room to open off. A toilet on a living-dining is instead visible in the entry
    privacy term (rule 3.5, weight 0.12), which is where a sightline to a toilet door belongs.
    """
    by_id = {r.id: r for r in plan.rooms}
    forbidden = (RoomType.KITCHEN, RoomType.DINING)
    violations: list[HardViolation] = []
    examined = 0
    for d in plan.doors:
        a, b = by_id.get(d.room_a), by_id.get(d.room_b)
        if a is None or b is None:
            continue
        examined += 1
        for toilet, other in ((a, b), (b, a)):
            if toilet.room_type in spec.TOILETS and other.room_type in forbidden:
                violations.append(HardViolation(
                    code="H13", rule="6.4",
                    message=(f"door {d.id} opens from {toilet.room_type} {toilet.id} directly "
                             f"into {other.room_type} {other.id}; rule 6.4 forbids it"),
                    measured=f"{toilet.room_type} -> {other.room_type}",
                    required="no direct toilet door onto a kitchen or dining room",
                    room_ids=(toilet.id, other.id), door_ids=(d.id,)))
    return HardCheck("H13", "6.4", tuple(violations), (), examined)


def check_h14(plan: PlanView) -> HardCheck:
    """H14 — one door per room (rule 7.1); a bedroom with two is the named case.

    The count is over the ONE-DOOR BUDGET, not over incident doors. A master bedroom with a
    corridor door and an en-suite door has two doors touching it and consumes one budget: the
    en-suite door is charged to the en-suite, which is what ``serves_room`` records and what
    openings.py's schedule assigns. Counting incident doors instead would reject every
    attached toilet in the programme.

    Through-spaces are exempt per rule 7.1 and ``spec.THROUGH_SPACES``. The check reports H14
    for any non-through-space room over budget, naming the room type in the message: rule 7.1
    states the rule for every room and singles the bedroom out as the reject, and firing a
    different code for a toilet with two doors would put a code in the report that 11.1 does
    not define.
    """
    if plan.doors and not any(d.serves_room for d in plan.doors):
        return HardCheck("H14", "7.1", (), (UncheckedRule(
            "H14", "7.1", "no door records serves_room, so the rule 7.1 one-door budget "
                          "cannot be attributed and no count was made"),), 0)

    tally: dict[str, list[str]] = {}
    for d in plan.doors:
        if d.serves_room:
            tally.setdefault(d.serves_room, []).append(d.id)

    violations: list[HardViolation] = []
    examined = 0
    for r in plan.rooms:
        if r.room_type in spec.THROUGH_SPACES:
            continue
        examined += 1
        ids = tally.get(r.id, [])
        if len(ids) > spec.MAX_DOORS_PER_ROOM:
            violations.append(HardViolation(
                code="H14", rule="7.1",
                message=(f"{r.room_type} {r.id} is served by {len(ids)} doors; rule 7.1 "
                         f"allows {spec.MAX_DOORS_PER_ROOM} for every room that is not a "
                         "through-space"),
                measured=len(ids), required=spec.MAX_DOORS_PER_ROOM, units="count",
                room_ids=(r.id,), door_ids=tuple(ids)))
    return HardCheck("H14", "7.1", tuple(violations), (), examined)


def check_h15(plan: PlanView) -> HardCheck:
    """H15 — party walls continuous and straight for the full unit depth (rule 10.3).

    Two ways to fail, and the rule names both. STAGGERED: the segments do not share one
    ``fixed`` coordinate, so the wall jogs and the acoustic and structural line is broken.
    DISCONTINUOUS: the segments share a line but leave a gap, so the wall does not run the
    full depth. ``party_walls=None`` means nobody supplied them and is reported as unchecked;
    ``party_walls=()`` means the unit genuinely has none, which passes.
    """
    if plan.party_walls is None:
        return HardCheck("H15", "10.3", (), (UncheckedRule(
            "H15", "10.3", "no party wall geometry was supplied, so continuity and "
                           "straightness were not tested"),), 0)

    violations: list[HardViolation] = []
    for w in plan.party_walls:
        fixed_values = sorted({s[0] for s in w.segments})
        if len(fixed_values) > 1:
            violations.append(HardViolation(
                code="H15", rule="10.3",
                message=(f"party wall {w.id} is staggered: its segments sit on "
                         f"{len(fixed_values)} different lines ({fixed_values}). Rule 10.3 "
                         "requires one continuous straight wall for the full unit depth."),
                measured=fixed_values, required="a single line", units="mm"))
            continue
        runs = sorted((min(s[1], s[2]), max(s[1], s[2])) for s in w.segments)
        cursor = w.required_lo
        gaps: list[tuple[int, int]] = []
        for lo, hi in runs:
            if lo > cursor:
                gaps.append((cursor, lo))
            cursor = max(cursor, hi)
        if cursor < w.required_hi:
            gaps.append((cursor, w.required_hi))
        if gaps:
            missing = sum(hi - lo for lo, hi in gaps)
            violations.append(HardViolation(
                code="H15", rule="10.3",
                message=(f"party wall {w.id} leaves {missing} mm of the "
                         f"{w.required_hi - w.required_lo} mm unit depth open "
                         f"(gaps at {gaps}); rule 10.3 requires it to be continuous"),
                measured=missing, required=0, units="mm"))
    return HardCheck("H15", "10.3", tuple(violations), (), len(plan.party_walls))


# The registry. Ordered H01..H15 so a report reads in the rule book's own order, and held as
# data so a caller can run one check, a subset, or all fifteen without this module deciding
# for it. `search.py` re-runs only the checks a dimension mutation can break.
HARD_CHECKS: dict[str, Callable[[PlanView], HardCheck]] = {
    "H01": check_h01, "H02": check_h02, "H03": check_h03, "H04": check_h04,
    "H05": check_h05, "H06": check_h06, "H07": check_h07, "H08": check_h08,
    "H09": check_h09, "H10": check_h10, "H11": check_h11, "H12": check_h12,
    "H13": check_h13, "H14": check_h14, "H15": check_h15,
}

if tuple(HARD_CHECKS) != spec.HARD_CODES:
    raise AssertionError(
        "validate.HARD_CHECKS does not cover exactly the fifteen codes in "
        f"spec.HARD_RULE_TEXT: {sorted(set(spec.HARD_CODES) ^ set(HARD_CHECKS))}. "
        "The 11.1 list is closed; a missing code is a rule nobody checks and an extra one "
        "is a code no rule defines.")


def run_hard_checks(plan: PlanView,
                    codes: Optional[Sequence[str]] = None) -> tuple[HardCheck, ...]:
    """Run all fifteen, or the named subset, in 11.1 order."""
    wanted = tuple(codes) if codes is not None else spec.HARD_CODES
    return tuple(HARD_CHECKS[c](plan) for c in wanted)


def hard_violations(plan: PlanView) -> tuple[HardViolation, ...]:
    """The rule book pseudocode's ``hard_violations(plan)``. Empty means step 10 accepts."""
    return tuple(v for c in run_hard_checks(plan) for v in c.violations)


# =====================================================================================
# E. Rule book 11.2 — the nine-term soft score
# =====================================================================================
# Every term is normalised to 0..1 before weighting, so the published weights mean what they
# say. A term whose raw value ran from 0 to 40 would carry forty times its stated weight, and
# the table's whole purpose is that aspect deviation is worth 0.20 of the answer and carpet
# efficiency 0.03.
#
# A term that cannot be measured returns raw 0.0 with measured=False. Zero is the only
# neutral value available and it is optimistic, which is why the term's name is published in
# SoftScore.unmeasured rather than being quietly absorbed.

def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else float(v))


def _soft_input(plan: PlanView, *keys: str) -> Optional[float]:
    """Read a raw term computed upstream. Accepts this module's key and openings.py's."""
    for k in keys:
        if k in plan.soft_inputs:
            try:
                return float(plan.soft_inputs[k])
            except (TypeError, ValueError):
                return None
    return None


def soft_aspect_deviation(plan: PlanView) -> SoftTerm:
    """0.20 — "Sigma per room, abs(actual - ideal) weighted by room area".

    Normalised by the worst legal plan: every room sitting exactly on its hard cap. So 0.0 is
    every room at its ideal proportion and 1.0 is a plan that only just escaped H06, and the
    number is comparable between a studio and a penthouse.

    Rule 8.4's daylight depth is folded in here rather than given a weight of its own,
    because 8.4 says in terms that it "reinforces the aspect caps in section 5.1" and the
    11.2 table has no row for it. It is 15% of the term when supplied, and both parts stay
    visible in ``detail`` so a report never has to guess which one moved.
    """
    num = 0.0
    den = 0.0
    per_room: list[dict[str, Any]] = []
    for r in plan.rooms:
        cap = spec.aspect_cap(r.room_type)
        if cap is None:
            continue
        area = r.area_mm2
        dev = abs(r.aspect - cap.ideal)
        worst = max(cap.reject - cap.ideal, 1e-9)
        num += area * dev
        den += area * worst
        per_room.append({"room": r.id, "type": str(r.room_type),
                         "aspect": round(r.aspect, 3), "ideal": cap.ideal,
                         "deviation": round(dev, 3),
                         "over_warn": r.aspect > cap.warn})
    if den <= 0:
        return SoftTerm("aspect_deviation", spec.SOFT_WEIGHTS["aspect_deviation"], 0.0,
                        measured=False,
                        detail={"reason": "no room in this plan has an aspect cap"})
    aspect_part = _clamp01(num / den)

    daylight = _soft_input(plan, "daylight_depth")
    if daylight is None:
        raw = aspect_part
    else:
        raw = _clamp01(0.85 * aspect_part + 0.15 * _clamp01(daylight))
    return SoftTerm("aspect_deviation", spec.SOFT_WEIGHTS["aspect_deviation"], raw,
                    detail={"aspect_part": round(aspect_part, 4),
                            "daylight_depth_part": (None if daylight is None
                                                    else round(_clamp01(daylight), 4)),
                            "rooms": per_room,
                            "note": ("Rule 8.4 daylight depth is folded in at 15% because "
                                     "8.4 states it reinforces the 5.1 caps and 11.2 gives "
                                     "it no weight of its own.")})


def soft_circulation_efficiency(plan: PlanView) -> SoftTerm:
    """0.18 — penalty as circulation departs from the target band.

    Shared decision, taken in spec.py and applied unchanged: 8-12% costs nothing, 12-15%
    grows linearly to 1.0 at the H10 boundary, and below 8% grows to 1.0 at 0%. The rule
    book's "departs from 10%" is the band's midpoint, not a knife edge — penalising a plan at
    9% for not being at 10% would fight the very rule that calls 8-12 acceptable.
    """
    circ = circulation_area_mm2(plan)
    carpet, derived = carpet_area_mm2(plan)
    if carpet <= 0:
        return SoftTerm("circulation_efficiency",
                        spec.SOFT_WEIGHTS["circulation_efficiency"], 0.0, measured=False,
                        detail={"reason": "carpet area is zero or unknown"})
    pct = 100.0 * circ / carpet
    if pct < spec.CIRCULATION_MIN_PCT:
        raw = _clamp01((spec.CIRCULATION_MIN_PCT - pct) / spec.CIRCULATION_MIN_PCT)
        band = "below the 8% floor: rooms are opening off each other"
    elif pct <= spec.CIRCULATION_SOFT_MAX_PCT:
        raw = 0.0
        band = "inside the 8-12% target band"
    else:
        span = spec.CIRCULATION_HARD_MAX_PCT - spec.CIRCULATION_SOFT_MAX_PCT
        raw = _clamp01((pct - spec.CIRCULATION_SOFT_MAX_PCT) / span)
        band = "between 12% and the 15% H10 limit: saleable area is being spent on passage"
    return SoftTerm("circulation_efficiency", spec.SOFT_WEIGHTS["circulation_efficiency"],
                    raw, detail={"circulation_pct": round(pct, 2), "band": band,
                                 "circulation_mm2": circ, "carpet_mm2": carpet,
                                 "carpet_derived": derived})


def soft_adjacency_satisfaction(plan: PlanView) -> SoftTerm:
    """0.16 — missed "R" costs 10 points, missed "P" costs 3 (``spec.ADJACENCY_MISS_PENALTY``).

    Counted per ROOM INSTANCE, not per type pair: a 3BHK with three bedrooms and one that
    cannot reach a toilet should score worse than one where every bedroom can, and a
    type-level count cannot see the difference. A relation is only counted when a partner of
    that type actually exists in the plan — charging a studio 10 points for having no
    separate dining room would penalise the programme, not the layout.

    The denominator is the same sum with every relation missed, so 1.0 means a plan with no
    required door satisfied at all.
    """
    present = {r.room_type for r in plan.rooms}
    by_id = {r.id: r for r in plan.rooms}
    graph = door_graph(plan)

    points = 0.0
    worst = 0.0
    missed: list[dict[str, Any]] = []
    for r in plan.rooms:
        neighbour_types = {by_id[n].room_type for n in graph.get(r.id, ()) if n in by_id}
        for other in present:
            if other == r.room_type and len([x for x in plan.rooms
                                             if x.room_type == other]) < 2:
                continue
            rel = spec.adjacency(r.room_type, other)
            penalty = spec.ADJACENCY_MISS_PENALTY.get(rel, 0.0)
            if penalty <= 0:
                continue
            worst += penalty
            if other not in neighbour_types:
                points += penalty
                missed.append({"room": r.id, "type": str(r.room_type),
                               "wanted": str(other), "relation": rel,
                               "penalty": penalty})
    if worst <= 0:
        return SoftTerm("adjacency_satisfaction",
                        spec.SOFT_WEIGHTS["adjacency_satisfaction"], 0.0, measured=False,
                        detail={"reason": "this programme has no R or P relation to satisfy"})
    return SoftTerm("adjacency_satisfaction", spec.SOFT_WEIGHTS["adjacency_satisfaction"],
                    _clamp01(points / worst),
                    detail={"points": points, "worst_case_points": worst,
                            "missed": missed,
                            "penalties": dict(spec.ADJACENCY_MISS_PENALTY)})


def soft_entry_privacy(plan: PlanView) -> SoftTerm:
    """0.12 — rule 3.5 sightline violations from the open main door.

    This term is NOT recomputed here. The ray cast belongs to openings.py, which owns the
    door geometry, the swing arc the rays are cast across and the furniture the rays hit; a
    second implementation reading only room rectangles would produce a different number for
    the same plan, and two numbers for one rule is worse than one number from the right
    place. When openings.py did not run, the term is unmeasured and says so.
    """
    raw = _soft_input(plan, "entry_privacy", "sightline")
    if raw is None:
        return SoftTerm("entry_privacy", spec.SOFT_WEIGHTS["entry_privacy"], 0.0,
                        measured=False,
                        detail={"reason": "openings.entry_sightline() supplied no value; "
                                          "rule 3.5 was not evaluated",
                                "targets": sorted(spec.ENTRY_SIGHTLINE_FORBIDDEN)})
    return SoftTerm("entry_privacy", spec.SOFT_WEIGHTS["entry_privacy"], _clamp01(raw),
                    detail={"source": "openings.entry_sightline",
                            "target_weights": dict(spec.SIGHTLINE_TARGET_WEIGHT)})


def soft_facade_utilisation(plan: PlanView) -> SoftTerm:
    """0.10 — habitable rooms with under 3000 mm of facade (rule 8.5's PREFERRED figure).

    The third of the three facade numbers. The 1.1 gate used 3000 as a budget, H03 uses 2500
    as a floor, and this term uses 3000 as a preference — three uses, three numbers, none
    guessed.

    ``soft_inputs["facade_utilisation"]`` wins when present. openings.py knows the facade run
    each room ended up with after windows were placed; recomputing from ``PlanRoom.facade_mm``
    as well would double-count the term, which the openings design spec warns about by name.
    """
    supplied = _soft_input(plan, "facade_utilisation")
    habitable = [r for r in plan.rooms if r.room_type in spec.HABITABLE]
    if supplied is not None:
        return SoftTerm("facade_utilisation", spec.SOFT_WEIGHTS["facade_utilisation"],
                        _clamp01(supplied),
                        detail={"source": "openings.place_windows",
                                "preferred_mm": spec.PREF_FACADE_PER_HABITABLE_MM})
    if not habitable:
        return SoftTerm("facade_utilisation", spec.SOFT_WEIGHTS["facade_utilisation"], 0.0,
                        measured=False, detail={"reason": "no habitable rooms"})
    under_pref = 0.0
    detail_rooms: list[dict[str, Any]] = []
    for r in habitable:
        if r.facade_mm < spec.MIN_FACADE_PER_HABITABLE_MM:
            under_pref += 2.0             # normally unreachable: H03 already rejected it
        elif r.facade_mm < spec.PREF_FACADE_PER_HABITABLE_MM:
            under_pref += 1.0
        detail_rooms.append({"room": r.id, "facade_mm": r.facade_mm})
    return SoftTerm("facade_utilisation", spec.SOFT_WEIGHTS["facade_utilisation"],
                    _clamp01(under_pref / len(habitable)),
                    detail={"source": "PlanRoom.facade_mm",
                            "min_mm": spec.MIN_FACADE_PER_HABITABLE_MM,
                            "preferred_mm": spec.PREF_FACADE_PER_HABITABLE_MM,
                            "rooms": detail_rooms})


def soft_wet_core_compactness(plan: PlanView) -> SoftTerm:
    """0.08 — sum of pipe run lengths (rules 9.1 and 9.2).

    Runs are MANHATTAN, not straight-line: a drain is chased along the slab in two
    directions, and a diagonal would flatter every plan by about 40%. Each run is normalised
    against ``spec.WC_TRAP_TO_STACK_MAX_MM`` (2000), so a toilet at the limit scores 1.0.

    A NOTE ON A GAP IN 11.1. Rule 9.2's 2000 mm limit is written as an absolute — beyond it
    the 1:40 fall does not fit in the sunk slab — but the 11.1 list has no code for it. This
    term therefore penalises an over-long run and cannot reject it. That is the rule book's
    omission, recorded rather than repaired: adding an H16 would put a code in the report
    that section 11.1 does not define. The over-limit rooms are named in ``detail`` so the
    finding is at least visible.
    """
    if plan.shaft_point is None:
        return SoftTerm("wet_core_compactness", spec.SOFT_WEIGHTS["wet_core_compactness"],
                        0.0, measured=False,
                        detail={"reason": "no shaft point supplied; rule 9.2 pipe runs "
                                          "cannot be measured"})
    wet = [r for r in plan.rooms if r.room_type in spec.WET]
    if not wet:
        return SoftTerm("wet_core_compactness", spec.SOFT_WEIGHTS["wet_core_compactness"],
                        0.0, measured=False, detail={"reason": "this plan has no wet rooms"})
    sx, sy = plan.shaft_point
    total = 0.0
    runs: list[dict[str, Any]] = []
    over_limit: list[str] = []
    for r in wet:
        cx = r.rect.x + r.rect.w // 2
        cy = r.rect.y + r.rect.h // 2
        run = abs(cx - sx) + abs(cy - sy)
        total += min(1.0, run / spec.WC_TRAP_TO_STACK_MAX_MM)
        runs.append({"room": r.id, "type": str(r.room_type), "run_mm": run})
        if r.room_type in spec.TOILETS and run > spec.WC_TRAP_TO_STACK_MAX_MM:
            over_limit.append(r.id)
    return SoftTerm("wet_core_compactness", spec.SOFT_WEIGHTS["wet_core_compactness"],
                    _clamp01(total / len(wet)),
                    detail={"shaft_point": list(plan.shaft_point), "runs": runs,
                            "max_run_mm": spec.WC_TRAP_TO_STACK_MAX_MM,
                            "over_rule_9_2_limit": over_limit,
                            "note": ("Rule 9.2's 2000 mm limit has no code in the 11.1 list, "
                                     "so an over-long run is penalised here and cannot "
                                     "reject. Section 11.1 is treated as closed.")})


def soft_door_placement_quality(plan: PlanView) -> SoftTerm:
    """0.08 — "centred doors, short corner offsets, faced leaves" (rules 7.3, 7.7, 7.8).

    Prefers openings.py's own composite, which weighs the longest-furniture-wall and
    facing-leaf parts this module cannot see. Falling back to corner offset alone would
    silently narrow a three-rule term to one rule, so the fallback says so in ``detail``:
    rule 7.3 is the part that is checkable from a door's own record, and a centred door — the
    thing 7.3 calls a soft reject — scores 1.0.
    """
    supplied = _soft_input(plan, "door_placement_quality", "door_placement")
    if supplied is not None:
        return SoftTerm("door_placement_quality",
                        spec.SOFT_WEIGHTS["door_placement_quality"], _clamp01(supplied),
                        detail={"source": "openings.check_doors",
                                "covers": ["7.3 corner offset", "7.7 placement side",
                                           "7.8 facing leaves"]})
    scored = [d for d in plan.doors if d.corner_offset_mm is not None]
    if not scored:
        return SoftTerm("door_placement_quality",
                        spec.SOFT_WEIGHTS["door_placement_quality"], 0.0, measured=False,
                        detail={"reason": "no door carries a corner offset and openings.py "
                                          "supplied no composite"})
    lo = spec.DOOR_CORNER_OFFSET_MIN_MM
    hi = spec.DOOR_CORNER_OFFSET_MAX_MM
    total = 0.0
    per_door: list[dict[str, Any]] = []
    for d in scored:
        off = int(d.corner_offset_mm or 0)
        if lo <= off <= hi:
            cost = 0.0
        elif off < lo:
            cost = _clamp01((lo - off) / lo)          # too tight: the leaf fouls the return
        else:
            reach = max((d.wall_length_mm or 0) / 2.0 - hi, 1.0)
            cost = _clamp01((off - hi) / reach)       # 1.0 exactly at the centre of the wall
        total += cost
        per_door.append({"door": d.id, "corner_offset_mm": off, "cost": round(cost, 3)})
    return SoftTerm("door_placement_quality", spec.SOFT_WEIGHTS["door_placement_quality"],
                    _clamp01(total / len(scored)),
                    detail={"source": "PlanDoor.corner_offset_mm (rule 7.3 only)",
                            "band_mm": [lo, hi], "doors": per_door,
                            "note": ("openings.py supplied no composite, so rules 7.7 and "
                                     "7.8 are NOT represented in this term.")})


def soft_structural_alignment(plan: PlanView) -> SoftTerm:
    """0.05 — partition walls off the column grid (rule 10.1).

    A wall line within ``spec.WALL_TO_GRID_TOLERANCE_MM`` (150) of a grid line is on grid;
    anything else floats mid-span and needs a secondary beam. The term is the fraction of
    distinct wall coordinates that float, so a plan whose every partition lands on a column
    scores 0.
    """
    if not plan.grid_x_mm and not plan.grid_y_mm:
        return SoftTerm("structural_alignment", spec.SOFT_WEIGHTS["structural_alignment"],
                        0.0, measured=False,
                        detail={"reason": "no column grid supplied; rule 10.1 alignment "
                                          "cannot be measured"})
    tol = spec.WALL_TO_GRID_TOLERANCE_MM
    xs = {p[0] for r in plan.rooms for p in r.outline}
    ys = {p[1] for r in plan.rooms for p in r.outline}
    off: list[dict[str, Any]] = []
    total = 0

    def nearest(v: int, lines: Sequence[int]) -> Optional[int]:
        return min((abs(v - g) for g in lines), default=None)

    for axis, values, lines in (("x", sorted(xs), plan.grid_x_mm),
                                ("y", sorted(ys), plan.grid_y_mm)):
        if not lines:
            continue
        for v in values:
            total += 1
            d = nearest(v, lines)
            if d is not None and d > tol:
                off.append({"axis": axis, "mm": v, "nearest_grid_mm": d})
    if total == 0:
        return SoftTerm("structural_alignment", spec.SOFT_WEIGHTS["structural_alignment"],
                        0.0, measured=False, detail={"reason": "no wall lines to test"})
    return SoftTerm("structural_alignment", spec.SOFT_WEIGHTS["structural_alignment"],
                    _clamp01(len(off) / total),
                    detail={"tolerance_mm": tol, "wall_lines": total,
                            "off_grid": off,
                            "note": ("Rule 10.2's 6000 mm beam span limit is not scored: it "
                                     "is a instruction to re-run the column grid optimiser, "
                                     "not a property of this plan.")})


def soft_carpet_efficiency(plan: PlanView) -> SoftTerm:
    """0.03 — carpet divided by built-up, target above 0.78.

    At or above the target the term is 0; below it the shortfall is scaled by the target, so
    a plan at 0.39 — half the target — scores 0.5. A built-up area was either supplied or it
    was not; deriving one by inflating the carpet area would make the term measure its own
    assumption.
    """
    carpet, derived = carpet_area_mm2(plan)
    if plan.built_up_area_mm2 is None or plan.built_up_area_mm2 <= 0 or carpet <= 0:
        return SoftTerm("carpet_efficiency", spec.SOFT_WEIGHTS["carpet_efficiency"], 0.0,
                        measured=False,
                        detail={"reason": "no built-up area supplied; the ratio cannot be "
                                          "formed and will not be assumed"})
    ratio = carpet / float(plan.built_up_area_mm2)
    target = spec.CARPET_EFFICIENCY_TARGET
    raw = 0.0 if ratio >= target else _clamp01((target - ratio) / target)
    return SoftTerm("carpet_efficiency", spec.SOFT_WEIGHTS["carpet_efficiency"], raw,
                    detail={"carpet_mm2": carpet, "built_up_mm2": plan.built_up_area_mm2,
                            "ratio": round(ratio, 4), "target": target,
                            "carpet_derived": derived})


SOFT_TERMS: dict[str, Callable[[PlanView], SoftTerm]] = {
    "aspect_deviation": soft_aspect_deviation,
    "circulation_efficiency": soft_circulation_efficiency,
    "adjacency_satisfaction": soft_adjacency_satisfaction,
    "entry_privacy": soft_entry_privacy,
    "facade_utilisation": soft_facade_utilisation,
    "wet_core_compactness": soft_wet_core_compactness,
    "door_placement_quality": soft_door_placement_quality,
    "structural_alignment": soft_structural_alignment,
    "carpet_efficiency": soft_carpet_efficiency,
}

if set(SOFT_TERMS) != set(spec.SOFT_WEIGHTS):
    raise AssertionError(
        "validate.SOFT_TERMS does not match the nine rows of spec.SOFT_WEIGHTS: "
        f"{sorted(set(SOFT_TERMS) ^ set(spec.SOFT_WEIGHTS))}. A term with no weight cannot "
        "be scored and a weight with no term is 11.2 arithmetic that never happens.")


def soft_score(plan: PlanView) -> SoftScore:
    """The 11.2 fitness. Lower is better. Contains no vastu term — see the class docstring."""
    terms = tuple(SOFT_TERMS[k](plan) for k in spec.SOFT_WEIGHTS)
    penalty = 0.0 if plan.vastu_penalty is None else _clamp01(plan.vastu_penalty)
    return SoftScore(terms, penalty, plan.vastu_penalty is not None)


# =====================================================================================
# F. The whole of section 11
# =====================================================================================
def validate_plan(plan: PlanView) -> ValidationReport:
    """Step 10 and step 11 together, with their answers kept apart.

    The soft score is computed even for a rejected plan, because a search wants to know how
    near a reject came and a report wants to show what else was wrong. It is never a reason
    to keep one: ``accepted`` reads the hard list and only the hard list.
    """
    checks = run_hard_checks(plan)
    score = soft_score(plan)

    notes: list[str] = []
    carpet, derived = carpet_area_mm2(plan)
    if derived:
        notes.append(
            "Carpet area was not supplied and was derived as the sum of every room except "
            "balconies and shafts. H10 and the carpet-efficiency term both depend on it.")
    if score.unmeasured:
        notes.append(
            "Soft terms that could not be measured, each contributing 0.0 and therefore "
            "making the score OPTIMISTIC: " + ", ".join(score.unmeasured) + ".")
    unchecked = tuple(u for c in checks for u in c.unchecked)
    if unchecked:
        notes.append(
            "Hard rules that could not be tested: "
            + ", ".join(sorted({u.code for u in unchecked}))
            + ". These are neither passes nor failures; `accepted` reflects only the rules "
              "that actually ran.")
    conflicts = spec.spec_conflicts(plan.code_version)
    if conflicts:
        notes.append(
            f"{len(conflicts)} code-versus-rule-book conflict(s) governed a threshold used "
            "here; see spec.SPEC_CONFLICTS. The code minimum won each one.")
    return ValidationReport(plan.unit_id, plan.unit_type, checks, score, tuple(notes))


__all__ = [
    "FitResult", "Point",
    "PlanRoom", "PlanDoor", "PlanFurniture", "PartyWall", "PlanView",
    "HardViolation", "UncheckedRule", "HardCheck", "SoftTerm", "SoftScore",
    "ValidationReport",
    "polygon_area_mm2", "rectilinear_min_clear_mm", "polygons_overlap", "rect_points",
    "rects_overlap_mm2", "circulation_area_mm2", "carpet_area_mm2", "door_graph",
    "check_h01", "check_h02", "check_h03", "check_h04", "check_h05", "check_h06",
    "check_h07", "check_h08", "check_h09", "check_h10", "check_h11", "check_h12",
    "check_h13", "check_h14", "check_h15",
    "HARD_CHECKS", "run_hard_checks", "hard_violations",
    "soft_aspect_deviation", "soft_circulation_efficiency", "soft_adjacency_satisfaction",
    "soft_entry_privacy", "soft_facade_utilisation", "soft_wet_core_compactness",
    "soft_door_placement_quality", "soft_structural_alignment", "soft_carpet_efficiency",
    "SOFT_TERMS", "soft_score", "validate_plan",
]
