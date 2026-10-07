"""Rule 1.1 — the envelope plannability gate, and the feedback it sends back to siteplan.

This is step 1 of the mandatory generation order, and it is the only step that runs before
any geometry exists. Its job is to answer one question — *can this footprint physically hold
this unit at all?* — and to answer it before a single room is placed, because the failure the
rule book diagnoses in section 0 is a footprint failure wearing a layout's clothes. The old
packer took a 6.6 m-deep flat with 11 m of frontage, was asked for six habitable rooms, and
did the only thing a slicer can do: it cut six full-depth columns 2.6-3.0 m wide, every one
of them a hard reject under rule 5.1. No amount of downstream cleverness fixes that, because
the frontage was never there. Rule 1.1 says so out loud and rejects the envelope instead of
producing a plan that has to lie about it.

Four things this module is careful about:

1. IT NEVER SHORT-CIRCUITS. ``check_envelope`` runs every test and reports every failure,
   plus the derived ``EnvelopeRequirements`` — max depth, min facade, min width, min area —
   that an envelope would have to meet. A gate that stops at the first failure sends the
   caller round a loop of one fix per iteration, and the caller here is a tower packer that
   has to re-shape a block: it needs the whole target, not the first complaint.

2. IT ROUNDS THE SAFE WAY. Supply quantities (facade available, envelope area) floor; demand
   quantities (facade required, required area) ceil. The two ratio tests are evaluated in
   exact integer arithmetic through ``fractions.Fraction`` rather than against a float 2.2,
   so a boundary envelope cannot decide differently on two runs of the same input. This is
   the one place in the package where a rounding artefact could silently turn a fail into a
   pass, which is why the rule is stated rather than assumed.

3. ONLY THE KITCHEN MAY BE TALKED OUT OF ITS WINDOW. Rule 9.5 lets a kitchen be served by a
   code-compliant exhaust shaft instead of an external wall, and ``spec.FACADE_EXEMPT_WITH_SHAFT``
   names exactly that one room type. Any other room passed in ``shaft_served`` is IGNORED and
   named in a warning. A shaft-served bedroom is a landlocked bedroom (rule 8.1, H03), and a
   gate that accepted the argument would let a caller dissolve the gate.

4. IT PRODUCES NO SOFT SCORE. An envelope is plannable or it is not. The advisory warnings
   this module attaches — daylight depth beyond 5250 (rule 8.4), thresholds resolved from the
   rule book rather than from a code, recorded spec conflicts — are prose for the report and
   are never summed into anything. Hard and soft stay apart here as everywhere else.

The rule book's closing instruction for a rejection is "feed the failure back to the tower
packer in siteplan/ and make it re-shape or re-split the block". There is no per-block reshape
entry point in ``siteplan/`` — ``plan_site(project, overrides)`` re-plans the whole site from a
``SiteLayoutConfig`` — so ``siteplan_feedback`` shapes the failure as the override dict that
API already accepts and hands it back as data. It never calls ``plan_site`` itself: re-planning
moves every tower, including the ones whose units were fine, and this module is not entitled to
make that call for the caller.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Iterable, Literal, Mapping, Optional, Sequence

import iscodes  # backend/ is the import root, exactly as aifloorplan does `import vastu`
from siteplan.errors import LayoutError   # errors.py itself imports nothing: no cycle

from . import spec
from .spec import (EDGES, OPPOSITE_EDGE, Edge, RectMM, RoomType, UnitTypeKey,
                   mm2_to_sqm, mm_to_m)

__all__ = [
    "DEPTH_TO_FACADE_CAP", "CARPET_TO_ENVELOPE_FACTOR", "FACADE_PER_HABITABLE_MM",
    "UnitEnvelope", "envelope_from_metres",
    "EnvelopeFinding", "EnvelopeMetrics", "EnvelopeRequirements", "PlannabilityResult",
    "EnvelopeRejected", "check_envelope", "require_plannable",
    "siteplan_feedback", "merge_overrides",
]


# =====================================================================================
# A. The three rule 1.1 constants
# =====================================================================================
DEPTH_TO_FACADE_CAP: float = 2.2
CARPET_TO_ENVELOPE_FACTOR: float = 1.18       # rule 1.1: +18% for walls & circulation

# Rule 1.1 asks for 3000 mm of external wall per habitable room. Rule 8.5 asks for 2500
# minimum and 3000 preferred. Both are in the book and they are not the same number, so the
# project resolved the collision once and named all three uses:
#
#   the GATE (here)                    3000  — being generous at the gate is the gate's
#                                              entire purpose; it is what eliminates the
#                                              long-rectangle output rule 1.1 targets
#   the per-room HARD check (validate) 2500  — spec.MIN_FACADE_PER_HABITABLE_MM, rule 8.5 min
#   the soft "facade utilisation" term 3000  — spec.PREF_FACADE_PER_HABITABLE_MM, rule 8.5 pref
#
# This constant is bound to spec's preferred figure rather than restating 3000, so the two
# cannot drift apart in a later edit and leave the gate enforcing a rule book of its own.
FACADE_PER_HABITABLE_MM: int = spec.PREF_FACADE_PER_HABITABLE_MM

# Both rule 1.1 ratios are evaluated as exact rationals. A float 2.2 is 2.20000000000000017…
# in binary, so `depth > 2.2 * width` on an envelope that sits exactly on the cap can answer
# differently depending on how the two sides were reached. Fraction(str(x)) reads the decimal
# literal the constant was written as: 2.2 -> 11/5, 1.18 -> 59/50. For the values above this
# reduces to the integer forms the design spec wrote out by hand (depth*10 > 22*width), and
# it keeps doing the right thing if someone re-tunes the constants.
_CAP = Fraction(str(DEPTH_TO_FACADE_CAP))
_AREA_FACTOR = Fraction(str(CARPET_TO_ENVELOPE_FACTOR))


def _ceil_div(numerator: int, denominator: int) -> int:
    """Integer ceiling division. Demand quantities round up; that is the whole reason."""
    return -(-numerator // denominator)


def _exceeds_depth_cap(depth_mm: int, facade_width_mm: int) -> bool:
    """rule 1.1: REJECT if envelope_depth > 2.2 x facade_width, in exact integers."""
    return depth_mm * _CAP.denominator > facade_width_mm * _CAP.numerator


def _max_depth_for(facade_width_mm: int) -> int:
    """Deepest envelope this frontage may carry. A supply figure, so it floors."""
    return (facade_width_mm * _CAP.numerator) // _CAP.denominator


def _min_facade_width_for(depth_mm: int) -> int:
    """Narrowest frontage this depth may sit on. A demand figure, so it ceils."""
    return _ceil_div(depth_mm * _CAP.denominator, _CAP.numerator)


def _required_area_for(min_carpet_mm2: int) -> int:
    """min_carpet x 1.18, ceiled. Demand: a square millimetre short is still short."""
    return _ceil_div(min_carpet_mm2 * _AREA_FACTOR.numerator, _AREA_FACTOR.denominator)


# =====================================================================================
# B. The envelope
# =====================================================================================
# Edge naming aliases. The site planner, the renderer and hand-written test fixtures all
# spell a compass edge differently; a typo that silently drops a facade edge would shrink
# the facade budget and reject a footprint that was fine, so unknown spellings raise rather
# than being skipped.
_EDGE_ALIASES: dict[str, Edge] = {
    "n": "N", "north": "N", "top": "N",
    "s": "S", "south": "S", "bottom": "S",
    "e": "E", "east": "E", "right": "E",
    "w": "W", "west": "W", "left": "W",
}


def _as_edge(value: Any) -> Edge:
    """Normalise one edge label, loudly."""
    key = str(value or "").strip().lower()
    try:
        return _EDGE_ALIASES[key]
    except KeyError:
        raise ValueError(
            f"unknown envelope edge {value!r}; expected one of {EDGES} "
            f"(or a full compass name). A silently dropped edge is a silently shrunk "
            f"facade budget, so this refuses rather than guessing.") from None


def _as_edge_set(values: Optional[Iterable[Any]]) -> frozenset[Edge]:
    return frozenset(_as_edge(v) for v in (values or ()))


@dataclass(frozen=True, slots=True)
class UnitEnvelope:
    """One dwelling unit's footprint on a floor plate, with which walls face what.

    ``open_edges`` are the edges that face open air. ``party_edges`` are shared with the
    mirrored neighbour across a continuous party wall (rule 10.3) and can never carry a
    window. The entry edge faces the common corridor in the normal double-loaded case, and
    it is excluded from the facade budget even when it happens to face open space, because
    the wall it sits on is the one the common lobby is on: a ground-floor garden entry is
    legal but its frontage is not free for a bedroom window.

    The rect is in integer millimetres in the plate's own local frame; ``spec.RectMM`` is the
    package's single geometry primitive and nothing here redefines it.
    """

    rect: RectMM
    entry_edge: Edge
    open_edges: frozenset[Edge]
    party_edges: frozenset[Edge] = frozenset()
    shaft_point: Optional[tuple[int, int]] = None
    unit_id: str = ""
    tower_id: str = ""
    floor: int = 0

    def __post_init__(self) -> None:
        # Normalising in __post_init__ rather than demanding a frozenset from the caller is
        # deliberate: every caller of this class is converting from a JSON payload, and a
        # list that silently became a set-of-one-string would produce an envelope whose
        # open edges were {"N","o","r","t","h"}.
        object.__setattr__(self, "entry_edge", _as_edge(self.entry_edge))
        object.__setattr__(self, "open_edges", _as_edge_set(self.open_edges))
        object.__setattr__(self, "party_edges", _as_edge_set(self.party_edges))

        if not isinstance(self.rect, RectMM):
            raise ValueError(
                f"UnitEnvelope.rect must be a spec.RectMM in millimetres, got "
                f"{type(self.rect).__name__}. Convert once, at the API boundary, with "
                f"spec.rect_from_metres() or envelope_from_metres().")

        both = self.open_edges & self.party_edges
        if both:
            # A wall cannot simultaneously face open air and be shared with the flat next
            # door. Tolerating it would let the same millimetres be counted as facade and as
            # party wall, which is exactly the double-count the facade budget exists to stop.
            raise ValueError(
                f"edges {sorted(both)} are listed as both open and party walls. A party wall "
                f"is shared with the mirrored neighbour and can never face open air.")

        if self.shaft_point is not None:
            pt = tuple(self.shaft_point)
            if len(pt) != 2 or not all(isinstance(v, int) and not isinstance(v, bool)
                                       for v in pt):
                raise ValueError(
                    f"UnitEnvelope.shaft_point must be a two-integer (x, y) in millimetres, "
                    f"got {self.shaft_point!r}.")
            x, y = pt
            if not (self.rect.x <= x <= self.rect.x2 and self.rect.y <= y <= self.rect.y2):
                # Rule 9.1 puts the wet core around ONE shaft belonging to this unit or
                # shared across its party wall. A shaft outside the footprint is a data
                # error, and letting it through would put every pipe run in rule 9.2 on the
                # wrong side of a wall.
                raise ValueError(
                    f"shaft_point {pt} lies outside the envelope "
                    f"({self.rect.x},{self.rect.y})-({self.rect.x2},{self.rect.y2}).")
            object.__setattr__(self, "shaft_point", pt)

        if not isinstance(self.floor, int) or isinstance(self.floor, bool):
            raise ValueError(f"UnitEnvelope.floor must be an int, got {self.floor!r}")

    # ------------------------------------------------------------------ derived geometry
    @property
    def facade_edges(self) -> frozenset[Edge]:
        """Edges whose length may be spent on the rule 1.1 facade budget."""
        return self.open_edges - {self.entry_edge} - self.party_edges

    @property
    def primary_facade_edge(self) -> Optional[Edge]:
        """The edge the rooms grow inward from, or None when the unit has no facade.

        Preference order, and why: the edge OPPOSITE the entry is taken first because that
        is the frontage of a double-loaded bar — the corridor is on one long side and the
        windows on the other — and it is the same choice ``vastu.pack_unit`` already makes,
        so the two engines read the same plate the same way. Failing that, the longest
        remaining facade edge wins, with N, E, S, W breaking a tie so the answer is stable
        across runs rather than depending on set iteration order.
        """
        candidates = self.facade_edges
        if not candidates:
            return None
        opposite = OPPOSITE_EDGE[self.entry_edge]
        if opposite in candidates:
            return opposite
        return max((e for e in EDGES if e in candidates),
                   key=lambda e: (self.rect.edge_length_mm(e), -EDGES.index(e)))

    @property
    def facade_width_mm(self) -> int:
        """Length of the primary facade. Zero when there is no facade at all (E01)."""
        edge = self.primary_facade_edge
        return self.rect.edge_length_mm(edge) if edge else 0

    @property
    def depth_mm(self) -> int:
        """Extent measured perpendicular to the primary facade — the rule 1.1 depth.

        With no facade edge there is no perpendicular to measure, so this reports the
        envelope's LONGER side: E01 has already fired, and the requirements block still has
        to name a frontage that would carry this footprint. Taking the longer side is the
        conservative direction — it asks for more frontage, never less.
        """
        edge = self.primary_facade_edge
        if edge is None:
            return self.rect.long_mm
        return self.rect.h if edge in ("N", "S") else self.rect.w

    def to_dict(self) -> dict[str, Any]:
        """Report payload. Metres for humans, millimetres kept alongside for machines."""
        return {
            "unit_id": self.unit_id, "tower_id": self.tower_id, "floor": self.floor,
            "rect_m": self.rect.to_metres(),
            "rect_mm": {"x": self.rect.x, "y": self.rect.y,
                        "w": self.rect.w, "h": self.rect.h},
            "entry_edge": self.entry_edge,
            "open_edges": sorted(self.open_edges),
            "party_edges": sorted(self.party_edges),
            "facade_edges": sorted(self.facade_edges),
            "primary_facade_edge": self.primary_facade_edge,
            "facade_width_m": mm_to_m(self.facade_width_mm),
            "depth_m": mm_to_m(self.depth_mm),
            "shaft_point_mm": list(self.shaft_point) if self.shaft_point else None,
        }


def envelope_from_metres(box: Mapping[str, float], entry_edge: Edge,
                         exterior_edges: Sequence[str], **kw: Any) -> UnitEnvelope:
    """Build an envelope from the metre-unit box the rest of the platform speaks.

    This is one of the two places in the package where metres are allowed (the other is
    ``RectMM.to_metres`` on the way out). ``box`` accepts the ``{"x","y","w","h"}`` shape
    ``RectMM.to_metres`` emits, with ``width``/``depth``/``height`` accepted as aliases for
    ``w``/``h`` because that is what the tower payload calls them.

    Extents floor and origins round to nearest, per ``spec.rect_from_metres``: the extent is
    a supply quantity and a flat that measures 6.6004 m deep offers 6600 mm, not 6601.
    """
    def _pick(*names: str) -> float:
        for n in names:
            if n in box and box[n] is not None:
                return float(box[n])
        raise ValueError(
            f"envelope box is missing {names[0]!r}; got keys {sorted(box)}. "
            f"Expected the {{'x','y','w','h'}} shape RectMM.to_metres() produces.")

    rect = spec.rect_from_metres(_pick("x"), _pick("y"),
                                 _pick("w", "width"), _pick("h", "depth", "height"))
    return UnitEnvelope(rect=rect, entry_edge=_as_edge(entry_edge),
                        open_edges=_as_edge_set(exterior_edges), **kw)


# =====================================================================================
# C. Findings, metrics, requirements, result
# =====================================================================================
FindingCode = Literal["E01", "E02", "E03", "E04", "E05", "E06"]

# What each code means, in one line, so a report never has to guess. E01-E04 are rule 1.1's
# own four tests; E05 and E06 are named "engine" and "input" in the finding's `rule` field
# precisely so a reader can tell the rule book's rejections from this module's additions.
FINDING_RULE: dict[str, str] = {
    "E01": "1.1", "E02": "1.1", "E03": "1.1", "E04": "1.1",
    "E05": "engine", "E06": "input",
}
FINDING_TITLE: dict[str, str] = {
    "E01": "no wall open to air",
    "E02": "facade shorter than the habitable room budget",
    "E03": "envelope deeper than 2.2 x its facade width",
    "E04": "envelope area below minimum carpet x 1.18",
    "E05": "envelope short dimension below the widest room minimum",
    "E06": "envelope description is not self-consistent",
}


@dataclass(frozen=True, slots=True)
class EnvelopeFinding:
    """One failed test. ``measured`` and ``required`` are in ``unit``, never mixed."""

    code: FindingCode
    rule: str
    message: str
    measured: float
    required: float
    unit: Literal["mm", "mm2", "ratio", "count"]

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "rule": self.rule, "title":
                             FINDING_TITLE.get(self.code, ""), "message": self.message,
                             "measured": self.measured, "required": self.required,
                             "unit": self.unit}
        # A human reading "measured 6600, required 24200" in millimetres has to do the
        # arithmetic to see it is a 6.6 m flat wanting 24.2 m of wall. Both are carried.
        if self.unit == "mm":
            d["measured_m"] = mm_to_m(int(self.measured))
            d["required_m"] = mm_to_m(int(self.required))
        elif self.unit == "mm2":
            d["measured_sqm"] = mm2_to_sqm(int(self.measured))
            d["required_sqm"] = mm2_to_sqm(int(self.required))
        return d


@dataclass(frozen=True, slots=True)
class EnvelopeMetrics:
    """Everything the four tests measured, pass or fail. Integer mm/mm2 throughout."""

    habitable_count: int
    shaft_served: tuple[RoomType, ...]
    facade_required_mm: int
    facade_available_mm: int
    facade_width_mm: int
    depth_mm: int
    depth_ratio: float
    area_mm2: int
    min_carpet_mm2: int
    required_area_mm2: int
    primary_facade_edge: Optional[Edge]
    widest_room_min_width_mm: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "habitable_count": self.habitable_count,
            "shaft_served": [str(r) for r in self.shaft_served],
            "facade_required_mm": self.facade_required_mm,
            "facade_required_m": mm_to_m(self.facade_required_mm),
            "facade_available_mm": self.facade_available_mm,
            "facade_available_m": mm_to_m(self.facade_available_mm),
            "facade_width_mm": self.facade_width_mm,
            "facade_width_m": mm_to_m(self.facade_width_mm),
            "depth_mm": self.depth_mm, "depth_m": mm_to_m(self.depth_mm),
            "depth_ratio": self.depth_ratio,
            "depth_ratio_cap": DEPTH_TO_FACADE_CAP,
            "area_mm2": self.area_mm2, "area_sqm": mm2_to_sqm(self.area_mm2),
            "min_carpet_mm2": self.min_carpet_mm2,
            "min_carpet_sqm": mm2_to_sqm(self.min_carpet_mm2),
            "required_area_mm2": self.required_area_mm2,
            "required_area_sqm": mm2_to_sqm(self.required_area_mm2),
            "primary_facade_edge": self.primary_facade_edge,
            "widest_room_min_width_mm": self.widest_room_min_width_mm,
        }


@dataclass(frozen=True, slots=True)
class EnvelopeRequirements:
    """What this envelope would have to become. Computed on every run, pass or fail.

    Computed even on a pass because the caller that re-shapes a block needs the target for
    the tests that DID fail, and a caller re-planning a whole tower needs the binding figure
    across a mix of units where some passed. A requirements block that only appeared on
    failure would make the second case impossible to assemble.
    """

    min_facade_mm: int
    max_depth_mm: int
    min_facade_width_mm: int
    min_area_mm2: int
    min_short_dimension_mm: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_facade_mm": self.min_facade_mm,
            "min_facade_m": mm_to_m(self.min_facade_mm),
            "max_depth_mm": self.max_depth_mm, "max_depth_m": mm_to_m(self.max_depth_mm),
            "min_facade_width_mm": self.min_facade_width_mm,
            "min_facade_width_m": mm_to_m(self.min_facade_width_mm),
            "min_area_mm2": self.min_area_mm2,
            "min_area_sqm": mm2_to_sqm(self.min_area_mm2),
            "min_short_dimension_mm": self.min_short_dimension_mm,
            "min_short_dimension_m": mm_to_m(self.min_short_dimension_mm),
        }


@dataclass(frozen=True, slots=True)
class PlannabilityResult:
    """The complete answer: every failure, every measurement, and the target to hit."""

    ok: bool
    unit_type: UnitTypeKey
    envelope: UnitEnvelope
    findings: tuple[EnvelopeFinding, ...]
    warnings: tuple[str, ...]
    metrics: EnvelopeMetrics
    requirements: EnvelopeRequirements
    code_version: str

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(f.code for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rule": "1.1",
            "gate": "envelope_plannability",
            "unit_type": self.unit_type,
            "code_version": self.code_version,
            "code_version_label": iscodes.version_label(self.code_version),
            "envelope": self.envelope.to_dict(),
            "codes": list(self.codes),
            "findings": [f.to_dict() for f in self.findings],
            # Warnings are advisory prose and are deliberately a SEPARATE list from findings.
            # A warning has never rejected anything and must never look as though it could.
            "warnings": list(self.warnings),
            "metrics": self.metrics.to_dict(),
            "requirements": self.requirements.to_dict(),
        }


class EnvelopeRejected(LayoutError):
    """Rule 1.1: this footprint cannot hold this unit.

    Subclasses ``siteplan.errors.LayoutError`` so ``server.py`` serialises it through the
    path it already has for site layout failures — the client shows the message inline and
    the whole ``PlannabilityResult`` travels with it as context, so the UI can say which of
    the four tests failed and by how much rather than "layout failed".
    """

    code_id = "ENVELOPE_UNPLANNABLE"

    def __init__(self, result: PlannabilityResult):
        payload = result.to_dict()
        first = result.findings[0].message if result.findings else "envelope is not plannable"
        extra = len(result.findings) - 1
        message = first + (f" (+{extra} more)" if extra > 0 else "")
        super().__init__(self.code_id, message, **payload)
        self.result = result


# =====================================================================================
# D. The gate
# =====================================================================================
def check_envelope(env: UnitEnvelope, unit_type: str, *,
                   version: Optional[str] = None,
                   shaft_served: Sequence[RoomType] = (),
                   rooms: Optional[Sequence[RoomType]] = None) -> PlannabilityResult:
    """Run rule 1.1 against one envelope. Never raises for a plannability failure.

    ``rooms`` is an addition to the frozen signature and exists because ``spec.programme``
    refuses to invent a programme for the "custom" unit tier: without it, gating a custom
    unit would raise ``SpecUnavailable`` out of a function whose entire contract is to return
    findings. Passing the room list explicitly is the only honest way to gate a unit whose
    programme nobody has declared.

    ``shaft_served`` may name only ``RoomType.KITCHEN`` (rule 9.5 / ``spec.FACADE_EXEMPT_WITH_SHAFT``).
    Anything else is ignored with a warning that names it, because a shaft-served bedroom is
    a landlocked bedroom and rule 8.1 rejects those.
    """
    ut = spec.normalise_unit_type(unit_type)
    prog = spec.programme(ut, rooms=rooms)
    version = iscodes.code_version(version)

    findings: list[EnvelopeFinding] = []
    warnings: list[str] = []

    # ---------------------------------------------------------------- 1. input validity (E06)
    # RectMM's own __post_init__ already rejects a non-positive extent, so the geometry
    # cannot arrive degenerate. What it cannot catch is a self-contradictory DESCRIPTION of
    # an otherwise valid rectangle, which is what E06 is for.
    if env.entry_edge in env.party_edges:
        findings.append(EnvelopeFinding(
            "E06", FINDING_RULE["E06"],
            f"The main entrance is on the {env.entry_edge} edge, which is also listed as a "
            f"party wall. Rule 10.3 makes a party wall continuous and straight for the full "
            f"unit depth, and rule 3.1 allows exactly one main entrance — it cannot be cut "
            f"through the wall shared with the neighbouring flat.",
            measured=1, required=0, unit="count"))

    if env.rect.w <= 0 or env.rect.h <= 0:      # pragma: no cover - RectMM guards this
        findings.append(EnvelopeFinding(
            "E06", FINDING_RULE["E06"],
            f"Envelope has a non-positive extent ({env.rect.w} x {env.rect.h} mm).",
            measured=float(min(env.rect.w, env.rect.h)), required=1.0, unit="mm"))

    if env.entry_edge in env.open_edges:
        # Legal — a ground-floor flat can be entered off a garden — but the entry edge is
        # still struck out of the facade budget, because the common approach is on it. Say
        # so, because the alternative is a caller wondering where the frontage went.
        warnings.append(
            f"The {env.entry_edge} edge faces open space and also carries the main entrance. "
            f"It is excluded from the facade budget: the common approach sits on that wall, "
            f"so its length is not free for habitable-room windows.")

    # ---------------------------------------------------------------- 2. facade budget (E01, E02)
    exempt = spec.FACADE_EXEMPT_WITH_SHAFT
    accepted_shaft: list[RoomType] = []
    for raw in shaft_served:
        try:
            rt = RoomType(raw)
        except ValueError:
            warnings.append(f"shaft_served names {raw!r}, which is not a RoomType. Ignored.")
            continue
        if rt not in exempt:
            # This is the one place a caller could dissolve the gate, so it is refused by
            # name rather than quietly filtered.
            warnings.append(
                f"shaft_served names {rt}, which may NOT be served by a shaft. Rule 9.5 "
                f"exempts the kitchen alone (it has an extract duct for the chimney); every "
                f"other habitable room needs an external wall under rule 8.1, and a "
                f"landlocked one is hard reject H03. The request was ignored and {rt} is "
                f"still counted against the facade budget.")
            continue
        if rt not in prog:
            warnings.append(
                f"shaft_served names {rt}, which is not in the {ut} programme. Ignored.")
            continue
        accepted_shaft.append(rt)

    exempted = set(accepted_shaft)
    habitable = [r for r in prog if r in spec.HABITABLE and r not in exempted]
    if accepted_shaft:
        warnings.append(
            f"{', '.join(str(r) for r in sorted(set(accepted_shaft)))} is served by a "
            f"ventilation shaft (rule 9.5) and consumes no facade. The shaft must still be "
            f"proved code-compliant downstream; this gate only stops charging for the wall.")

    facade_required = len(habitable) * FACADE_PER_HABITABLE_MM       # DEMAND, exact int
    facade_available = sum(env.rect.edge_length_mm(e) for e in env.facade_edges)  # SUPPLY

    if facade_available == 0:
        findings.append(EnvelopeFinding(
            "E01", FINDING_RULE["E01"],
            f"No wall of this envelope faces open space once the entry edge "
            f"({env.entry_edge}) and the party walls "
            f"({', '.join(sorted(env.party_edges)) or 'none'}) are excluded. Every habitable "
            f"room would be landlocked, which is hard reject H03 under rule 8.1.",
            measured=0.0, required=float(facade_required), unit="mm"))
    elif facade_available < facade_required:
        findings.append(EnvelopeFinding(
            "E02", FINDING_RULE["E02"],
            f"{len(habitable)} habitable rooms need "
            f"{mm_to_m(facade_required)} m of external wall at "
            f"{FACADE_PER_HABITABLE_MM} mm each (rule 1.1); this envelope offers "
            f"{mm_to_m(facade_available)} m on edges "
            f"{', '.join(sorted(env.facade_edges))}. Short by "
            f"{mm_to_m(facade_required - facade_available)} m.",
            measured=float(facade_available), required=float(facade_required), unit="mm"))

    # ---------------------------------------------------------------- 3. depth vs width (E03)
    primary = env.primary_facade_edge
    facade_width = env.facade_width_mm
    depth = env.depth_mm
    depth_ratio = round(depth / facade_width, 4) if facade_width else 0.0

    if primary is None:
        # No facade means no perpendicular, so the ratio is undefined rather than infinite.
        # E01 has already recorded the real failure; inventing a ratio here would add a
        # second rejection for the same fact and inflate the count in every report.
        depth_ratio = 0.0
    elif _exceeds_depth_cap(depth, facade_width):
        findings.append(EnvelopeFinding(
            "E03", FINDING_RULE["E03"],
            f"The envelope is {mm_to_m(depth)} m deep on a {mm_to_m(facade_width)} m "
            f"{primary} facade — a ratio of {depth_ratio}, above the rule 1.1 cap of "
            f"{DEPTH_TO_FACADE_CAP}. Either the depth comes down to "
            f"{mm_to_m(_max_depth_for(facade_width))} m or the frontage goes up to "
            f"{mm_to_m(_min_facade_width_for(depth))} m.",
            measured=depth_ratio, required=DEPTH_TO_FACADE_CAP, unit="ratio"))

    # ---------------------------------------------------------------- 4. area (E04)
    # min_carpet excludes FOYER and CORRIDOR by construction in spec: rule 1.1 multiplies by
    # 1.18 "for walls & circulation", so counting circulation in the base would charge for it
    # twice and reject envelopes that are actually big enough.
    min_carpet = spec.min_carpet_area_mm2(ut, version, rooms=rooms)
    required_area = _required_area_for(min_carpet)
    area = env.rect.area_mm2

    if area < required_area:
        findings.append(EnvelopeFinding(
            "E04", FINDING_RULE["E04"],
            f"The envelope encloses {mm2_to_sqm(area)} m2. A {ut} needs "
            f"{mm2_to_sqm(min_carpet)} m2 of carpet at the governing minima, which rule 1.1 "
            f"grosses up by {CARPET_TO_ENVELOPE_FACTOR} for walls and circulation to "
            f"{mm2_to_sqm(required_area)} m2. Short by "
            f"{mm2_to_sqm(required_area - area)} m2.",
            measured=float(area), required=float(required_area), unit="mm2"))

    # ---------------------------------------------------------------- 5. short dimension (E05)
    # An engine addition, not one of rule 1.1's four, and labelled "engine" so a reader can
    # tell. A 2400 mm-deep envelope cannot hold a 3000 mm-wide master bedroom in ANY
    # orientation, and finding that out at step 7 wastes the whole pipeline. The check is one
    # max() and it is honest about being ours.
    widths = [int(spec.min_width(r, version).value) for r in prog if r in spec.HABITABLE]
    widest = max(widths) if widths else 0
    if widest and env.rect.short_mm < widest:
        narrow = max((r for r in prog if r in spec.HABITABLE),
                     key=lambda r: int(spec.min_width(r, version).value))
        findings.append(EnvelopeFinding(
            "E05", FINDING_RULE["E05"],
            f"The envelope's shorter side is {mm_to_m(env.rect.short_mm)} m. The widest "
            f"room in the programme ({narrow}) needs a clear width of "
            f"{mm_to_m(widest)} m, which this footprint cannot give it in either "
            f"orientation. (Engine addition, not one of rule 1.1's four tests.)",
            measured=float(env.rect.short_mm), required=float(widest), unit="mm"))

    # ---------------------------------------------------------------- 6. advisory warnings
    daylight_cap = int(spec.DAYLIGHT_DEPTH_FACTOR * spec.WINDOW_HEAD_HEIGHT_MM)
    if depth > daylight_cap:
        warnings.append(
            f"At {mm_to_m(depth)} m the envelope is deeper than daylight reaches from a "
            f"{spec.WINDOW_HEAD_HEIGHT_MM} mm window head "
            f"({spec.DAYLIGHT_DEPTH_FACTOR} x head = {mm_to_m(daylight_cap)} m, rule 8.4). "
            f"Rooms grown the full depth will be dark at the back; the plan will need a "
            f"second rank of rooms or a light well.")

    unverified = sorted({
        str(r) for r in prog
        if r not in spec.NO_AREA_MINIMUM
        and spec.min_area(r, version, unit_type=ut).binding == "design"})
    if unverified:
        warnings.append(
            f"The minimum area used for {', '.join(unverified)} is the rule book's design "
            f"target, not a code figure: no minimum has been read from "
            f"{iscodes.version_label(version)} for those rooms. The gate still applied it, "
            f"but it is an architect's opinion and is not a statutory floor.")

    for conflict in spec.spec_conflicts(version):
        warnings.append(
            f"Spec conflict on {conflict.get('room_type')}.{conflict.get('field')}: the code "
            f"figure governs over the rule book's looser one "
            f"({conflict.get('governing', 'code')}). A rule book is an opinion; a code is a "
            f"legal floor.")

    # ---------------------------------------------------------------- 7. requirements, always
    requirements = EnvelopeRequirements(
        min_facade_mm=facade_required,
        max_depth_mm=_max_depth_for(facade_width),
        min_facade_width_mm=_min_facade_width_for(depth),
        min_area_mm2=required_area,
        min_short_dimension_mm=widest,
    )

    metrics = EnvelopeMetrics(
        habitable_count=len(habitable),
        shaft_served=tuple(accepted_shaft),
        facade_required_mm=facade_required,
        facade_available_mm=facade_available,
        facade_width_mm=facade_width,
        depth_mm=depth,
        depth_ratio=depth_ratio,
        area_mm2=area,
        min_carpet_mm2=min_carpet,
        required_area_mm2=required_area,
        primary_facade_edge=primary,
        widest_room_min_width_mm=widest,
    )

    return PlannabilityResult(
        ok=not findings, unit_type=ut, envelope=env,
        findings=tuple(findings), warnings=tuple(warnings),
        metrics=metrics, requirements=requirements, code_version=version)


def require_plannable(env: UnitEnvelope, unit_type: str, **kw: Any) -> PlannabilityResult:
    """``check_envelope`` that raises. The first call in the generation order.

    The raising form exists so the pipeline cannot accidentally continue past a rejected
    envelope: rule 1.1 is a gate, and a gate that returns a boolean nobody checks is a
    comment. ``EnvelopeRejected`` carries the whole result, so nothing is lost by raising.
    """
    result = check_envelope(env, unit_type, **kw)
    if not result.ok:
        raise EnvelopeRejected(result)
    return result


# =====================================================================================
# E. Feedback to the site planner
# =====================================================================================
# Rule 1.1: "If a tower footprint fails it, the footprint is wrong, not the layout. Feed the
# failure back to the tower packer in siteplan/ and make it re-shape or re-split the block."
#
# There is no per-block reshape API in siteplan/. `plan()` re-plans the whole site from a
# SiteLayoutConfig, and `plan_site(project, overrides)` accepts a partial override dict that
# SiteLayoutConfig.from_dict merges onto the defaults. `siteplan.pack._candidate_footprints`
# reads towers.candidate_widths and towers.candidate_depths to build its search space, so
# those two lists ARE the reshape lever. The feedback is therefore shaped as that override
# dict and nothing else is invented. What cannot be expressed as a config change comes back
# as prose in `manual_actions` rather than as an override that would not work.
_DEPTH_LADDER_STEP_M: float = 2.0
# Below this a double-loaded plate cannot hold a corridor plus a flat either side, so a
# shallower candidate depth is not a smaller tower, it is no tower.
_MIN_VIABLE_PLATE_DEPTH_M: float = 9.0
_WIDTH_LADDER_STEP_M: float = 9.0
_WIDTH_LADDER_ENTRIES: int = 3


def _floor_half(v: float) -> float:
    """Round down to 0.5 m. Candidate depths are a supply figure, so they floor."""
    return math.floor(v * 2.0) / 2.0


def _ceil_half(v: float) -> float:
    """Round up to 0.5 m. Candidate widths are a demand figure, so they ceil."""
    return math.ceil(v * 2.0) / 2.0


def _tower_unit_count(tower: Mapping[str, Any], fallback: int) -> int:
    """How many flats this tower puts on a floor.

    ``siteplan.LayoutResult.to_dict`` writes ``units`` as an integer count; the project
    document and ``aifloorplan.generate_architectural_template`` write it as a list of
    ``{"type", "count"}`` rows. Both shapes are read, and when neither yields a number the
    count falls back to the number of units actually gated — which is the honest figure,
    because those are the units this feedback is about.
    """
    raw = tower.get("units")
    if isinstance(raw, bool):
        total = 0
    elif isinstance(raw, int):
        total = max(raw, 0)
    elif isinstance(raw, (list, tuple)):
        total = 0
        for u in raw:
            total += max(int(u.get("count") or 1), 1) if isinstance(u, Mapping) else 1
    else:
        try:
            total = max(int(raw or 0), 0)
        except (TypeError, ValueError):
            total = 0
    return total or max(fallback, 1)


def siteplan_feedback(results: Sequence[PlannabilityResult],
                      tower: Mapping[str, Any],
                      config: Optional[Mapping[str, Any]] = None,
                      *, rows: int = 2) -> dict[str, Any]:
    """Turn a set of rejected envelopes into an override the site planner can actually apply.

    Returns DATA. It never calls ``plan_site``: re-planning the site moves every tower,
    including the ones whose units were fine, and invalidates every stored floor layout. That
    is a decision with consequences beyond this module, so it belongs to the caller. The
    ``invalidates`` key names the collateral damage so it cannot be forgotten silently.

    ``rows`` is the number of flat rows the plate carries — 2 for the double-loaded corridor
    ``aifloorplan.generate_architectural_template`` lays out, 1 for a single-loaded plate.
    Nothing in the tower payload records the loading type, so the default of 2 is an
    assumption the caller must override for a point block.
    """
    failed = [r for r in results if not r.ok]
    tower_id = str(tower.get("id") or tower.get("tower_id") or tower.get("name") or "")
    tower_name = str(tower.get("name") or tower.get("tower_name") or tower_id)

    if not failed:
        return {"ok": True, "reason": "all_units_plannable", "rule": "1.1",
                "tower_id": tower_id, "tower_name": tower_name, "units_rejected": []}

    # ---------------------------------------------------------------- binding requirements
    # min() on the depth cap and max() on everything else: the override has to satisfy EVERY
    # rejected unit at once, so the tightest constraint in each direction is the one that
    # binds. Taking a mean here would produce a plate that fixes the average unit and none of
    # the real ones.
    max_unit_depth_mm = min(r.requirements.max_depth_mm for r in failed)
    min_unit_facade_mm = max(r.requirements.min_facade_mm for r in failed)
    min_unit_width_mm = max(r.requirements.min_facade_width_mm for r in failed)
    min_unit_area_mm2 = max(r.requirements.min_area_mm2 for r in failed)
    min_short_dim_mm = max(r.requirements.min_short_dimension_mm for r in failed)
    # A unit must be at least as wide as its widest room; if the frontage requirement came
    # out below that, the room minimum is the binding one.
    min_unit_width_mm = max(min_unit_width_mm, min_short_dim_mm)

    # ---------------------------------------------------------------- tower geometry
    corridor_m = max(float(tower.get("corridor_width") or 2.0), 1.8)   # aifloorplan's own rule
    width_m = tower.get("width_m")
    depth_m = tower.get("depth_m")
    confidence = "direct"
    cfg = _tower_config(config)

    if width_m is None or depth_m is None:
        # A tower dict that predates stage 3, or one assembled by hand. The footprint area is
        # the only size it carries, so the plate is reconstructed at the middle of the
        # configured aspect band and the answer is marked advisory — a derived width that is
        # wrong by 20% would move every override built on it.
        area = tower.get("footprint_sqm") or tower.get("footprint_area") or 0.0
        try:
            area = float(area)
        except (TypeError, ValueError):
            area = 0.0
        aspect = max((float(cfg.min_aspect) + float(cfg.max_aspect)) / 2.0, 1.0)
        if area > 0:
            width_m = math.sqrt(area * aspect)
            depth_m = area / width_m
        confidence = "advisory"

    width_m = float(width_m or 0.0)
    depth_m = float(depth_m or 0.0)

    # ---------------------------------------------------------------- unit -> tower
    unit_count = _tower_unit_count(tower, fallback=len(results))
    rows = max(int(rows or 1), 1)
    units_per_row = _ceil_div(unit_count, rows)

    max_tower_depth_m = rows * mm_to_m(max_unit_depth_mm) + corridor_m
    min_tower_width_m = units_per_row * mm_to_m(min_unit_width_mm)

    # ---------------------------------------------------------------- config overrides
    base_depths = [float(d) for d in cfg.candidate_depths]
    base_widths = [float(w) for w in cfg.candidate_widths]
    manual_actions: list[str] = []

    depths = [d for d in base_depths if d <= max_tower_depth_m]
    if not depths:
        # Nothing in the configured search space is shallow enough, so a ladder is
        # synthesised down from the required depth. It stops at the floor below which a
        # double-loaded plate has no room for a corridor and two flats.
        top = _floor_half(max_tower_depth_m)
        d = top
        while d >= _MIN_VIABLE_PLATE_DEPTH_M and len(depths) < 4:
            depths.append(round(d, 2))
            d -= _DEPTH_LADDER_STEP_M

    widths = [w for w in base_widths if w >= min_tower_width_m]
    synthesised_widths = False
    if not widths:
        start = _ceil_half(min_tower_width_m)
        widths = [round(start + i * _WIDTH_LADDER_STEP_M, 2)
                  for i in range(_WIDTH_LADDER_ENTRIES)]
        synthesised_widths = True

    towers_override: dict[str, Any] = {}
    if depths and depths != base_depths:
        towers_override["candidate_depths"] = depths
    if widths and widths != base_widths:
        towers_override["candidate_widths"] = widths

    if not depths:
        # Saying "set candidate_depths to []" would be a fix that does nothing: the packer
        # would find no footprint at all. The real lever is the unit mix or the corridor
        # loading, and both are project data, not SiteLayoutConfig.
        manual_actions.append(
            f"No viable plate depth exists at this unit mix: {rows} rows of "
            f"{mm_to_m(max_unit_depth_mm)} m flats plus a {corridor_m} m corridor comes to "
            f"{round(max_tower_depth_m, 2)} m, below the {_MIN_VIABLE_PLATE_DEPTH_M} m "
            f"minimum for a double-loaded plate. Fix the unit mix (fewer or smaller flats "
            f"per floor) or switch this tower to a single-loaded corridor (rows=1). Neither "
            f"is a SiteLayoutConfig change, so no override is offered for it.")

    if synthesised_widths and base_widths:
        largest = max(base_widths)
        fit_per_row = int(largest // mm_to_m(min_unit_width_mm)) if min_unit_width_mm else 0
        manual_actions.append(
            f"Every configured candidate width is below the {round(min_tower_width_m, 2)} m "
            f"this floor needs ({units_per_row} units per row x "
            f"{mm_to_m(min_unit_width_mm)} m). Widening the tower is offered as an override; "
            f"the alternative is to reduce units per floor from {unit_count} to "
            f"{max(fit_per_row * rows, 1)}, which keeps the largest configured plate "
            f"({largest} m).")

    has_road_config = isinstance(config, Mapping) and isinstance(config.get("road"), Mapping)
    manual_actions.append(
        ("Blocks are the binding constraint: raise road.max_blocks or lower "
         "road.min_block_area so the envelope re-splits into narrower blocks that can carry "
         "a wider tower." if has_road_config else
         "If the re-plan still cannot place a wide enough tower, raising road.max_blocks or "
         "lowering road.min_block_area re-splits the envelope into narrower blocks. Offered "
         "as a suggestion: this call was given no road config, so whether blocks are the "
         "binding constraint has not been measured."))

    return {
        "ok": False,
        "reason": "unit_envelope_unplannable",
        "rule": "1.1",
        "tower_id": tower_id,
        "tower_name": tower_name,
        "units_rejected": [
            {"unit_id": r.envelope.unit_id, "unit_type": r.unit_type,
             "codes": list(r.codes), "messages": [f.message for f in r.findings]}
            for r in failed
        ],
        "required": {
            "max_unit_depth_mm": max_unit_depth_mm,
            "min_unit_facade_mm": min_unit_facade_mm,
            "min_unit_width_mm": min_unit_width_mm,
            "min_unit_area_mm2": min_unit_area_mm2,
            "max_tower_depth_m": round(max_tower_depth_m, 3),
            "min_tower_width_m": round(min_tower_width_m, 3),
        },
        "config_overrides": ({"towers": towers_override} if towers_override else {}),
        "apply_with": ("siteplan.plan.plan_site(project, merge_overrides(current, "
                       "feedback['config_overrides']))"),
        "manual_actions": manual_actions,
        "confidence": confidence,
        # A re-plan moves every tower, so every floor layout stamped against the old
        # positions is stale. siteplan/version.py's polygon_signature exists because exactly
        # this went wrong once already at the site level.
        "invalidates": ["towers[*].floor_layouts"],
        "measured": {
            "tower_width_m": round(width_m, 3), "tower_depth_m": round(depth_m, 3),
            "corridor_width_m": corridor_m, "rows": rows,
            "units_on_floor": unit_count, "units_per_row": units_per_row,
        },
    }


def _tower_config(config: Optional[Mapping[str, Any]]) -> Any:
    """The TowerConfig this feedback is measured against — the caller's, or the defaults.

    Imported inside the function rather than at module scope so the coupling stays where it
    is used: only ``siteplan_feedback`` reads siteplan's tower defaults, and only to get two
    lists of candidate dimensions. It is a convenience, not a requirement — a caller can pass
    the same two lists in through ``config`` and this import never happens.
    """
    from siteplan.config import SiteLayoutConfig
    return SiteLayoutConfig.from_dict(dict(config) if config else None).towers


def merge_overrides(base: Optional[Mapping[str, Any]],
                    overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Two-level merge producing something ``SiteLayoutConfig.from_dict`` accepts verbatim.

    Two levels, not deep, because that is exactly how far ``SiteLayoutConfig.from_dict``
    looks: it rebuilds each section from the keys present in that section's dict and takes
    the dataclass default for every key that is absent. So a caller who passed
    ``{"towers": {"candidate_depths": [...]}}`` without merging would silently reset
    ``min_aspect``, ``floors_max`` and every other tower setting to its default. Merging key
    by key inside the section is what stops the feedback from quietly undoing the user's
    configuration while it fixes their plate.
    """
    merged: dict[str, Any] = {k: (dict(v) if isinstance(v, Mapping) else v)
                              for k, v in (base or {}).items()}
    for section, value in (overrides or {}).items():
        if isinstance(value, Mapping) and isinstance(merged.get(section), Mapping):
            merged[section] = {**merged[section], **value}
        elif isinstance(value, Mapping):
            merged[section] = dict(value)
        else:
            merged[section] = value
    return merged
