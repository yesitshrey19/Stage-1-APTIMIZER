"""Rule book section 4 — circulation as a designed object with its own geometry.

The single sentence this module exists to enforce is the first line of section 4:
"Circulation is a designed element with its own geometry, not residual space." The packer
being replaced (``backend/vastu.py``) does the opposite. It slices columns for the rooms it
knows about and emits whatever is left over as ``Passage 2``, ``Passage 3`` — rectangles
that were never designed, serve one door or none, dead-end against an external wall, and
collectively land wherever the arithmetic dropped them. Section 0 of the rule book names
that as one of the three visible failures, and it is the one with the cleanest fix: decide
the corridor's geometry BEFORE the rooms exist, from the doors it has to serve, and let the
rooms grow around it. Nothing here reads a room rectangle, because at step 6 of the
mandatory order there are none.

Four things this module is careful about.

1. THE SPINE IS A BOX, NOT A BAND. A corridor drawn as a full-width strip across the plan
   forces every room on either side of it to span the whole remaining depth, which is the
   long-thin-rectangle failure arriving by a different route. ``lay_spine`` sizes the run
   from the door schedule and stops there; the length is door-driven and then trimmed back
   to the last opening, never stretched to meet an edge.

2. LENGTH IS NEVER PADDED TO HIT A PERCENTAGE. Rule 4.2's lower bound (8%) is a diagnosis,
   not a target to build to: circulation below 8% means rooms open off each other, which is
   a TOPOLOGY fault. Padding the corridor to reach 8% would satisfy the number by creating a
   rule 4.4 dead end, so a low percentage is reported and the corridor stays honest.

3. HARD AND SOFT STAY APART. ``CirculationResult.hard`` carries only H08 and H10 — the two
   section 11.1 codes this module can prove. Everything else it finds (a segment serving one
   door, a run that cannot reach a room, a corridor at the rule 4.5 limit) goes in ``soft``.
   Rule 4.3's remedy, rule 4.4's trim and rule 4.7's shape are enforced by CONSTRUCTION in
   ``lay_spine`` and then re-checked geometrically on the emitted object, because a rule
   enforced only by the code path that produced the geometry is not enforced at all once a
   second code path (the GA) can move the geometry.

4. RULE 4.3'S REMEDY IS PERFORMED, NOT ANNOUNCED. "Absorb it into the room it serves" is an
   instruction to change the plan. A segment that ends up serving fewer than
   ``CORRIDOR_MIN_DOORS_SERVED`` openings is REMOVED from the spine and its rectangle is
   handed back as an ``Absorption`` naming the room that must swallow it, so geometry.py can
   actually merge it. Detecting an alcove and shipping it anyway is how the current engine
   ends up with a ``Passage 3``.

Sibling modules (topology.py, zones.py, geometry.py) are being written in parallel, so every
input here is taken structurally — an object with the right attributes, a mapping, or a
plain tuple — rather than by importing a type that may not exist yet. The adaptors are in
section A and are the only place that guessing happens; everything downstream of them works
on this module's own frozen dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Literal, Mapping, Optional, Sequence

from .spec import (
    CIRCULATION_HARD_MAX_PCT,
    CIRCULATION_MIN_PCT,
    CIRCULATION_SOFT_MAX_PCT,
    CIRCULATION_TARGET_PCT,
    CORRIDOR_ACCESSIBLE_WIDTH_MM,
    CORRIDOR_DEAD_END_MAX_MM,
    CORRIDOR_MIN_DOORS_SERVED,
    CORRIDOR_MIN_WIDTH_MM,
    CORRIDOR_PREF_WIDTH_MM,
    CORRIDOR_SHAPES,
    EDGES,
    HABITABLE,
    JAMB_MIN_MM,
    MAX_DIRECTION_CHANGES,
    OPENING_GAP_MIN_MM,
    PASS_THROUGH_ALLOWED,
    PUBLIC_ZONE_DEPTH_FRACTION,
    THROUGH_SPACES,
    ZONE_ORDER,
    Edge,
    RectMM,
    RoomType,
    Zone,
    corridor_min_width,
    door_spec,
    min_width,
    mm2_to_sqm,
    mm_to_m,
    zone_of,
)

__all__ = [
    "Axis", "SpineShape", "OpeningSide",
    "CirculationFinding", "SpineOpening", "SpineSegment", "Absorption", "Spine",
    "lay_spine",
    "carpet_area_mm2", "circulation_area_mm2", "circulation_pct",
    "CirculationVerdict", "classify_circulation", "circulation_efficiency_penalty",
    "check_width", "check_shape", "check_segments_serve", "absorb_short_segments",
    "dead_end_lengths", "check_dead_ends",
    "route_to_opening", "direction_changes", "check_direction_changes",
    "ReachStep", "Reachability", "reachable",
    "CirculationResult", "check_circulation",
]


# =====================================================================================
# A. Vocabulary, and the structural adaptors for inputs owned by other modules
# =====================================================================================
# Axes match spec.RectMM and openings.md: "h" runs east-west (x varies, y constant), "v"
# runs north-south (y varies, x constant). +x is East and y grows going South, so North is
# the LOW y edge — the one place where a sign error silently mirrors a whole plan.
Axis = Literal["h", "v"]
SpineShape = Literal["straight", "L", "none"]
# Which face of the run an opening is cut in. "low"/"high" are the two long walls, named by
# perpendicular coordinate rather than by "left"/"right" because left depends on which way
# you are walking and the run direction is not fixed. "end" is the run's far end wall — the
# terminal door a corridor stops at, which is what makes a dead end of zero possible.
OpeningSide = Literal["low", "high", "end"]

# The inward normal of each entry edge, as (dx, dy). Reading these off the edge letter every
# time is how the North/low-y inversion gets written down wrong in one branch of four.
_INWARD_OF_EDGE: dict[Edge, tuple[int, int]] = {
    "N": (0, 1), "S": (0, -1), "W": (1, 0), "E": (-1, 0),
}

# Carpet area is the denominator of rule 4.2, so what it excludes decides whether a plan
# passes H10. RERA reports balcony area separately from carpet area and a shaft is a service
# void nobody walks on, so neither is carpet here. Both exclusions make the percentage
# LARGER (smaller denominator), which is the conservative direction for a hard reject.
CARPET_EXCLUDED: frozenset[RoomType] = frozenset({RoomType.BALCONY, RoomType.SHAFT})
# Rule 4.2 names the numerator exactly: "foyer + corridors, excluding living and dining".
CIRCULATION_TYPES: frozenset[RoomType] = frozenset({RoomType.FOYER, RoomType.CORRIDOR})

# A segment shorter than it is wide is a doorway, not a corridor; it cannot hold two
# openings on one wall and rule 4.3 would delete it on the next pass anyway. House value,
# derived from the geometry rather than stated by the rule book.
MIN_SEGMENT_LENGTH_FACTOR: float = 1.0
# Rank used to order the doors along the run. The privacy gradient of section 2 is the only
# ordering the rule book gives, so the corridor picks up the semi-private openings first and
# ends at the private ones — you pass the common toilet on the way to the bedrooms, not the
# other way round.
_ZONE_RANK: dict[Zone, int] = {z: i for i, z in enumerate(ZONE_ORDER)}
_ZONE_RANK[Zone.SERVICE] = len(ZONE_ORDER)


@dataclass(frozen=True, slots=True)
class CirculationFinding:
    """One measured statement about the circulation. Shape matches envelope.EnvelopeFinding.

    ``severity`` exists so a renderer can style the row, NOT so the two lists can be merged:
    ``CirculationResult`` keeps hard and soft in separate tuples and no arithmetic crosses
    between them. ``code`` is a section 11.1 code only when severity is "hard"; soft codes
    are this module's own snake_case term names, which is how a reader can tell at a glance
    that nothing invented a sixteenth hard rule.
    """

    code: str
    rule: str
    severity: Literal["hard", "soft", "note"]
    message: str
    measured: float
    required: float
    unit: Literal["mm", "mm2", "pct", "count", "ratio"]

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "rule": self.rule, "severity": self.severity,
                "message": self.message, "measured": self.measured,
                "required": self.required, "unit": self.unit}


@dataclass(frozen=True, slots=True)
class _EdgeView:
    """One access-graph edge, normalised. Mirrors openings.AccessEdge field for field."""

    a: str
    b: str
    role: str = "room"
    serves: str = ""

    def other(self, node: str) -> Optional[str]:
        if node == self.a:
            return self.b
        if node == self.b:
            return self.a
        return None


@dataclass(frozen=True, slots=True)
class _GraphView:
    """The frozen access graph reduced to what section 4 needs: edges and room types."""

    edges: tuple[_EdgeView, ...]
    room_types: Mapping[str, RoomType]

    def node_type(self, node: str) -> Optional[RoomType]:
        rt = self.room_types.get(node)
        return RoomType(rt) if rt is not None else None

    def find(self, room_type: RoomType) -> Optional[str]:
        """First node of a type, in insertion order. Used for the foyer and the corridor."""
        for node, rt in self.room_types.items():
            if RoomType(rt) is room_type:
                return node
        return None

    def neighbours(self, node: str) -> tuple[tuple[str, _EdgeView], ...]:
        out: list[tuple[str, _EdgeView]] = []
        for e in self.edges:
            o = e.other(node)
            if o is not None:
                out.append((o, e))
        return tuple(out)


@dataclass(frozen=True, slots=True)
class _RoomView:
    """One realised room, normalised, for the rule 4.2 area arithmetic only."""

    room_id: str
    room_type: RoomType
    area_mm2: int


def _as_edge(obj: Any) -> _EdgeView:
    """One graph edge from whatever topology.py hands over."""
    if isinstance(obj, _EdgeView):
        return obj
    if isinstance(obj, Mapping):
        return _EdgeView(str(obj["a"]), str(obj["b"]),
                         str(obj.get("role", "room")), str(obj.get("serves", "")))
    a = getattr(obj, "a", None)
    b = getattr(obj, "b", None)
    if a is not None and b is not None:
        return _EdgeView(str(a), str(b), str(getattr(obj, "role", "room") or "room"),
                         str(getattr(obj, "serves", "") or ""))
    seq = tuple(obj)
    if len(seq) < 2:
        raise ValueError(f"cannot read an access edge from {obj!r}: need at least (a, b)")
    return _EdgeView(str(seq[0]), str(seq[1]),
                     str(seq[2]) if len(seq) > 2 else "room",
                     str(seq[3]) if len(seq) > 3 else "")


def _as_graph(graph: Any, room_types: Optional[Mapping[str, Any]] = None) -> _GraphView:
    """Normalise an access graph. topology.py does not exist yet, so this reads structurally.

    Accepted: an object with ``.edges`` (and optionally ``.room_types`` or ``.rooms``), a
    mapping with those keys, or a bare sequence of edges plus an explicit ``room_types``.
    A graph that cannot name the type of a node it connects is rejected rather than
    defaulted, because rule 4.6's whole content is which room TYPE may be crossed: a
    traversal over untyped nodes would answer a different question and look like it passed.
    """
    if isinstance(graph, _GraphView) and room_types is None:
        return graph

    raw_edges: Any
    types: dict[str, RoomType] = {}

    if isinstance(graph, Mapping):
        raw_edges = graph.get("edges", ())
        src_types = graph.get("room_types") or graph.get("types") or {}
        src_rooms = graph.get("rooms") or ()
    else:
        raw_edges = getattr(graph, "edges", None)
        src_types = getattr(graph, "room_types", None) or getattr(graph, "types", None) or {}
        src_rooms = getattr(graph, "rooms", None) or ()
        if raw_edges is None:
            raw_edges = graph                      # a bare sequence of edges

    for k, v in dict(src_types).items():
        types[str(k)] = RoomType(v)
    for r in src_rooms:
        if isinstance(r, Mapping):
            rid, rt = r.get("id"), r.get("room_type", r.get("type"))
        else:
            rid = getattr(r, "id", getattr(r, "room_id", None))
            rt = getattr(r, "room_type", getattr(r, "type", None))
        if rid is not None and rt is not None:
            types.setdefault(str(rid), RoomType(rt))
    for k, v in dict(room_types or {}).items():
        types[str(k)] = RoomType(v)

    edges = tuple(_as_edge(e) for e in raw_edges)
    unknown = sorted({n for e in edges for n in (e.a, e.b) if n and n not in types})
    if unknown:
        raise ValueError(
            f"access graph nodes with no RoomType: {unknown}. Rule 4.6 asks which room type "
            "may be crossed, so an untyped node cannot be traversed — pass room_types= or "
            "give the graph a .room_types mapping.")
    return _GraphView(edges, types)


def _as_rooms(rooms: Any) -> tuple[_RoomView, ...]:
    """Normalise realised rooms for rule 4.2. Accepts geometry.py objects, dicts or pairs."""
    out: list[_RoomView] = []
    for i, r in enumerate(rooms or ()):
        if isinstance(r, _RoomView):
            out.append(r)
            continue
        if isinstance(r, Mapping):
            rid = str(r.get("id", r.get("room_id", f"r{i}")))
            rt = RoomType(r.get("room_type", r.get("type")))
            rect = r.get("rect")
            area = r.get("area_mm2")
        elif isinstance(r, tuple) and len(r) == 2:
            rid = f"r{i}"
            rt = RoomType(r[0])
            rect, area = r[1], None
        else:
            rid = str(getattr(r, "id", getattr(r, "room_id", f"r{i}")))
            rt = RoomType(getattr(r, "room_type", getattr(r, "type")))
            rect = getattr(r, "rect", None)
            area = getattr(r, "area_mm2", None)
        if area is None:
            if isinstance(rect, RectMM):
                area = rect.area_mm2
            elif rect is not None:
                area = int(rect["w"]) * int(rect["h"])
            else:
                raise ValueError(f"room {rid!r} carries neither a rect nor an area_mm2; "
                                 "rule 4.2 cannot be measured from a label.")
        out.append(_RoomView(rid, rt, int(area)))
    return tuple(out)


def _as_rect(obj: Any) -> RectMM:
    if isinstance(obj, RectMM):
        return obj
    if isinstance(obj, Mapping):
        return RectMM(int(obj["x"]), int(obj["y"]), int(obj["w"]), int(obj["h"]))
    rect = getattr(obj, "rect", None)
    if isinstance(rect, RectMM):
        return rect
    raise ValueError(f"cannot read a RectMM from {obj!r}")


def _envelope_view(envelope: Any, entry_edge: Optional[str]) -> tuple[RectMM, Edge]:
    """The unit rectangle and its entry edge, from envelope.UnitEnvelope or a bare rect."""
    rect = _as_rect(envelope)
    edge = entry_edge or getattr(envelope, "entry_edge", None)
    if edge is None and isinstance(envelope, Mapping):
        edge = envelope.get("entry_edge")
    if edge is None:
        raise ValueError("lay_spine needs the entry edge: the privacy gradient of section 2 "
                         "runs away from it and the spine is laid along that gradient. Pass "
                         "a UnitEnvelope or entry_edge=.")
    edge = str(edge).upper()
    if edge not in EDGES:
        raise ValueError(f"entry_edge {edge!r} is not one of {EDGES}")
    return rect, edge  # type: ignore[return-value]


def _zone_band(zones: Any, zone: Zone) -> Optional[RectMM]:
    """One zone band rectangle from zones.py, or None when this caller has no band for it.

    zones.py is being written in parallel, so four shapes are accepted and a miss is a miss
    rather than an error: the fallback below (rule 2's own "first 30% of unit depth") is a
    rule-book statement, not a guess, so a spine laid without zones is still laid to a rule.
    """
    if zones is None:
        return None
    key: Any = zone
    try:
        if isinstance(zones, Mapping):
            band = zones.get(zone, zones.get(str(zone)))
        elif callable(getattr(zones, "band", None)):
            band = zones.band(key)
        else:
            bands = getattr(zones, "bands", None)
            if isinstance(bands, Mapping):
                band = bands.get(zone, bands.get(str(zone)))
            else:
                band = getattr(zones, str(zone), None)
    except (KeyError, TypeError, ValueError):
        return None
    if band is None:
        return None
    try:
        return _as_rect(band)
    except (KeyError, TypeError, ValueError):
        return None


# =====================================================================================
# B. Rule 4.2 — the circulation share, and the band resolved once in spec.py
# =====================================================================================
def carpet_area_mm2(rooms: Sequence[Any]) -> int:
    """Carpet area: every room except the balconies and shafts (see CARPET_EXCLUDED)."""
    return sum(r.area_mm2 for r in _as_rooms(rooms) if r.room_type not in CARPET_EXCLUDED)


def circulation_area_mm2(rooms: Sequence[Any]) -> int:
    """Rule 4.2 numerator: foyer + corridors. Living and dining are excluded by name.

    They are excluded because in this project's programme they are one open-plan room that a
    person walks across; counting a living-dining as circulation would put every plan over
    15% and reject the programme the rest of the engine is built on.
    """
    return sum(r.area_mm2 for r in _as_rooms(rooms) if r.room_type in CIRCULATION_TYPES)


def circulation_pct(rooms: Sequence[Any]) -> float:
    """Rule 4.2 — circulation as a percentage of carpet area.

    Returns 0.0 for an empty carpet rather than raising: a plan with no rooms has already
    failed something louder than section 4, and a ZeroDivisionError from here would mask it.
    """
    carpet = carpet_area_mm2(rooms)
    if carpet <= 0:
        return 0.0
    return 100.0 * circulation_area_mm2(rooms) / carpet


def circulation_efficiency_penalty(pct: float) -> float:
    """The 11.2 "circulation efficiency" term (weight 0.18), unweighted.

    The rule book defines it as the departure from 10%, and spec.py resolves the 12-15%
    gap the rule book leaves as a penalty growing with distance from 12. Both parts are
    here: 0.0 at exactly 10%, 1.0 at 0% or 20%, plus a second ramp reaching +1.0 at 15%.
    Deliberately not clamped — above 15% is H10 and no surviving plan reaches that far, so a
    clamp would only flatten the gradient the GA descends.
    """
    penalty = abs(pct - CIRCULATION_TARGET_PCT) / CIRCULATION_TARGET_PCT
    if pct > CIRCULATION_SOFT_MAX_PCT:
        span = CIRCULATION_HARD_MAX_PCT - CIRCULATION_SOFT_MAX_PCT
        penalty += (pct - CIRCULATION_SOFT_MAX_PCT) / span
    return penalty


@dataclass(frozen=True, slots=True)
class CirculationVerdict:
    """Rule 4.2 in one object: the measurement, the band it lands in, and the consequence."""

    pct: float
    circulation_mm2: int
    carpet_mm2: int
    band: Literal["below_target", "on_target", "soft_over", "hard_over"]
    penalty: float
    hard: tuple[CirculationFinding, ...]
    soft: tuple[CirculationFinding, ...]


def classify_circulation(pct: float, *, circulation_mm2: int = 0,
                         carpet_mm2: int = 0) -> CirculationVerdict:
    """Apply the rule 4.2 band exactly as spec.py resolved it. See spec's section H comment.

    8-12% is the target, 12-15% a soft penalty growing from 12, and ONLY above 15% is H10.
    Below 8% is a soft finding and never a reject: the rule book's own words are "below 8%
    means rooms open off each other", which is a statement about the access graph. Rejecting
    the geometry for it would punish the wrong module and invite the fix that pads the
    corridor into a dead end.
    """
    penalty = circulation_efficiency_penalty(pct)
    hard: list[CirculationFinding] = []
    soft: list[CirculationFinding] = []

    if pct > CIRCULATION_HARD_MAX_PCT:
        band = "hard_over"
        hard.append(CirculationFinding(
            "H10", "4.2", "hard",
            f"circulation is {pct:.1f}% of carpet area, over the {CIRCULATION_HARD_MAX_PCT:.0f}% "
            "hard limit — that is saleable area spent on walking",
            round(pct, 2), CIRCULATION_HARD_MAX_PCT, "pct"))
    elif pct > CIRCULATION_SOFT_MAX_PCT:
        band = "soft_over"
        soft.append(CirculationFinding(
            "circulation_over_band", "4.2", "soft",
            f"circulation is {pct:.1f}%, above the {CIRCULATION_SOFT_MAX_PCT:.0f}% target band "
            f"but below the {CIRCULATION_HARD_MAX_PCT:.0f}% reject",
            round(pct, 2), CIRCULATION_SOFT_MAX_PCT, "pct"))
    elif pct < CIRCULATION_MIN_PCT:
        band = "below_target"
        soft.append(CirculationFinding(
            "circulation_under_band", "4.2", "soft",
            f"circulation is {pct:.1f}%, below the {CIRCULATION_MIN_PCT:.0f}% floor — rule 4.2 "
            "reads this as rooms opening off each other, which is a topology fault and is "
            "not fixed by lengthening the corridor",
            round(pct, 2), CIRCULATION_MIN_PCT, "pct"))
    else:
        band = "on_target"

    return CirculationVerdict(round(pct, 4), int(circulation_mm2), int(carpet_mm2),
                              band, penalty, tuple(hard), tuple(soft))


# =====================================================================================
# C. The spine — an explicit polyline with a width, and the rectangles it occupies
# =====================================================================================
@dataclass(frozen=True, slots=True)
class SpineOpening:
    """One door the corridor serves, positioned along the run before any room exists.

    ``t_mm`` is measured from the segment's ANCHOR end (the foyer junction for the first
    segment, the corner for the second), not from the rectangle's origin, so it survives the
    run being trimmed or the whole spine being mirrored onto the other entry edge.
    """

    room_id: str
    room_type: RoomType
    side: OpeningSide
    t_mm: int
    leaf_mm: int

    @property
    def end_mm(self) -> int:
        """Far jamb of the leaf, measured from the anchor end. What rule 4.4 trims back to."""
        return self.t_mm + self.leaf_mm


@dataclass(frozen=True, slots=True)
class SpineSegment:
    """One straight run of the corridor: a rectangle, an axis, and the doors it serves."""

    rect: RectMM
    axis: Axis
    anchor_end: Literal["lo", "hi"]
    openings: tuple[SpineOpening, ...] = ()

    @property
    def width_mm(self) -> int:
        """Clear width — the rule 4.1 dimension, across the run."""
        return self.rect.h if self.axis == "h" else self.rect.w

    @property
    def length_mm(self) -> int:
        return self.rect.w if self.axis == "h" else self.rect.h

    @property
    def anchor_point(self) -> tuple[int, int]:
        """Centre of the anchored end, on the centreline. Where the polyline starts."""
        return self._centreline_point(0)

    @property
    def free_point(self) -> tuple[int, int]:
        """Centre of the free end. Where a dead end, if any, is measured to."""
        return self._centreline_point(self.length_mm)

    def _centreline_point(self, t_mm: int) -> tuple[int, int]:
        sign = 1 if self.anchor_end == "lo" else -1
        if self.axis == "h":
            base = self.rect.x if self.anchor_end == "lo" else self.rect.x2
            return (base + sign * t_mm, self.rect.y + self.rect.h // 2)
        base = self.rect.y if self.anchor_end == "lo" else self.rect.y2
        return (self.rect.x + self.rect.w // 2, base + sign * t_mm)

    def point_at(self, t_mm: int) -> tuple[int, int]:
        """Centreline point ``t_mm`` along the run from the anchor end."""
        return self._centreline_point(max(0, min(int(t_mm), self.length_mm)))

    def serves_count(self) -> int:
        """Rule 4.3's number: door openings into rooms this segment serves."""
        return len(self.openings)


@dataclass(frozen=True, slots=True)
class Absorption:
    """A corridor rectangle rule 4.3 removed, and the room that must swallow it.

    This is the remedy, not a complaint. geometry.py is expected to grow ``into_room_id`` by
    ``rect``; ``reroute_required`` is True when there is no room to absorb into (a segment
    serving nothing at all), which is a topology fault the spine cannot repair on its own
    because rule 11.3 freezes the access graph before geometry exists.
    """

    rect: RectMM
    into_room_id: str
    served: tuple[str, ...]
    reason: str
    reroute_required: bool = False


@dataclass(frozen=True, slots=True)
class Spine:
    """The circulation spine: an explicit polyline of one or two runs, with a clear width.

    Rule 4.7 allows straight or L only, so ``segments`` is never longer than two and that is
    checked on construction rather than trusted. ``shape == "none"`` is a legitimate answer —
    a studio has no corridor, and so does a plan whose only run rule 4.3 absorbed — and is
    not the same thing as a failure; the absorptions say what happened to the area.
    """

    segments: tuple[SpineSegment, ...]
    width_mm: int
    shape: SpineShape
    foyer_rect: Optional[RectMM] = None
    absorbed: tuple[Absorption, ...] = ()
    unserved: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.segments) > 2:
            raise ValueError(
                f"rule 4.7 permits a straight or L-shaped corridor only; got "
                f"{len(self.segments)} segments. Three runs is a branching corridor, which "
                "the rule book forbids inside a single dwelling at 1BHK-3BHK scale.")
        expected: SpineShape = ("none", "straight", "L")[len(self.segments)]
        if self.shape != expected:
            raise ValueError(f"spine shape {self.shape!r} disagrees with "
                             f"{len(self.segments)} segment(s) (expected {expected!r})")
        if self.shape != "none" and self.shape not in CORRIDOR_SHAPES:
            raise ValueError(f"shape {self.shape!r} is not one of {sorted(CORRIDOR_SHAPES)}")
        if len(self.segments) == 2 and self.segments[0].axis == self.segments[1].axis:
            raise ValueError("an L has two perpendicular runs; two parallel runs are two "
                             "corridors, which rule 4.7 forbids.")

    @property
    def rects(self) -> tuple[RectMM, ...]:
        """The rectangles the spine occupies. Disjoint: the corner square belongs to run 1."""
        return tuple(s.rect for s in self.segments)

    @property
    def area_mm2(self) -> int:
        return sum(s.rect.area_mm2 for s in self.segments)

    @property
    def length_mm(self) -> int:
        return sum(s.length_mm for s in self.segments)

    @property
    def polyline(self) -> tuple[tuple[int, int], ...]:
        """The centreline, as points. The explicit polyline rule 4.7 is stated about."""
        if not self.segments:
            return ()
        pts = [self.segments[0].anchor_point]
        for s in self.segments:
            pts.append(s.free_point)
        return tuple(pts)

    def openings(self) -> tuple[SpineOpening, ...]:
        return tuple(o for s in self.segments for o in s.openings)

    def served_room_ids(self) -> tuple[str, ...]:
        return tuple(o.room_id for o in self.openings())

    def to_dict(self) -> dict[str, Any]:
        """API boundary: metres out, millimetres kept alongside so nothing has to convert back."""
        return {
            "shape": self.shape,
            "width_m": mm_to_m(self.width_mm),
            "width_mm": self.width_mm,
            "area_sqm": mm2_to_sqm(self.area_mm2),
            "area_mm2": self.area_mm2,
            "segments": [
                {"axis": s.axis, "anchor_end": s.anchor_end,
                 "length_mm": s.length_mm, "rect_mm": {"x": s.rect.x, "y": s.rect.y,
                                                       "w": s.rect.w, "h": s.rect.h},
                 **s.rect.to_metres(),
                 "serves": [o.room_id for o in s.openings]}
                for s in self.segments],
            "polyline_mm": [list(p) for p in self.polyline],
            "absorbed": [{"into": a.into_room_id, "served": list(a.served),
                          "reason": a.reason, "reroute_required": a.reroute_required,
                          "area_sqm": mm2_to_sqm(a.rect.area_mm2)} for a in self.absorbed],
            "unserved": list(self.unserved),
            "notes": list(self.notes),
        }


# =====================================================================================
# D. Rule 4.1 — the clear width
# =====================================================================================
def spine_width_mm(*, accessible: bool = False, version: Optional[str] = None) -> int:
    """Rule 4.1: 900 minimum, 1050 preferred, 1200 accessible.

    The accessible figure is NOT restated here — it is read through spec.corridor_min_width,
    which reads iscodes.ACCESS["corridor_min_mm"] under NBC Part 3 Cl. 13.6. That matters
    because 1200 is the only one of the three that a building approval is actually checked
    against; the other two are design constants and spec.py labels them as such.
    """
    floor_mm = int(corridor_min_width(version, accessible=accessible).value)
    return max(floor_mm, CORRIDOR_PREF_WIDTH_MM)


def check_width(spine: Spine, *, accessible: bool = False,
                version: Optional[str] = None) -> tuple[list[CirculationFinding],
                                                        list[CirculationFinding]]:
    """Re-check rule 4.1 on the emitted geometry. Returns (hard, soft) — never one list.

    The minimum is a hard architectural floor but is NOT one of the fifteen 11.1 codes, so a
    sub-900 corridor is reported as a soft finding with the rule number attached rather than
    given an invented H-code. validate.py decides what to do with it; this module does not
    get to legislate a sixteenth reject.
    """
    hard: list[CirculationFinding] = []
    soft: list[CirculationFinding] = []
    floor_mm = int(corridor_min_width(version, accessible=accessible).value)
    pref_mm = spine_width_mm(accessible=accessible, version=version)
    for i, seg in enumerate(spine.segments):
        if seg.width_mm < floor_mm:
            soft.append(CirculationFinding(
                "corridor_below_min_width", "4.1", "soft",
                f"corridor run {i + 1} is {seg.width_mm} mm clear, below the {floor_mm} mm "
                f"{'accessible' if accessible else 'in-unit'} minimum",
                seg.width_mm, floor_mm, "mm"))
        elif seg.width_mm < pref_mm:
            soft.append(CirculationFinding(
                "corridor_below_pref_width", "4.1", "soft",
                f"corridor run {i + 1} is {seg.width_mm} mm clear, under the {pref_mm} mm "
                "preferred width",
                seg.width_mm, pref_mm, "mm"))
    return hard, soft


# =====================================================================================
# E. Rule 4.7 — shape, re-checked geometrically
# =====================================================================================
def check_shape(spine: Spine) -> tuple[list[CirculationFinding], list[CirculationFinding]]:
    """Straight or L, the two runs perpendicular and actually touching. (hard, soft).

    ``Spine.__post_init__`` already refuses three runs and two parallel ones, so this is the
    geometric half: after the GA has moved dimensions, the corner of an L can come apart, and
    a spine in two disconnected pieces is two corridors wearing one name.
    """
    hard: list[CirculationFinding] = []
    soft: list[CirculationFinding] = []
    if len(spine.segments) != 2:
        return hard, soft
    a, b = spine.segments
    if not _rects_touch(a.rect, b.rect):
        soft.append(CirculationFinding(
            "spine_disconnected", "4.7", "soft",
            "the two corridor runs do not touch — the L has come apart and the plan has two "
            "corridors, not one",
            0.0, 1.0, "count"))
    if _rects_overlap_area(a.rect, b.rect) > 0:
        soft.append(CirculationFinding(
            "spine_self_overlap", "4.7", "soft",
            "the corridor runs overlap, so the spine area is double counted in rule 4.2",
            mm2_to_sqm(_rects_overlap_area(a.rect, b.rect)), 0.0, "mm2"))
    return hard, soft


def _rects_touch(a: RectMM, b: RectMM) -> bool:
    """Share a boundary of positive length (edge contact), not merely a corner."""
    x_overlap = min(a.x2, b.x2) - max(a.x, b.x)
    y_overlap = min(a.y2, b.y2) - max(a.y, b.y)
    if x_overlap > 0 and (a.y2 == b.y or b.y2 == a.y):
        return True
    if y_overlap > 0 and (a.x2 == b.x or b.x2 == a.x):
        return True
    return x_overlap > 0 and y_overlap > 0


def _rects_overlap_area(a: RectMM, b: RectMM) -> int:
    return (max(0, min(a.x2, b.x2) - max(a.x, b.x))
            * max(0, min(a.y2, b.y2) - max(a.y, b.y)))


# =====================================================================================
# F. Rule 4.3 — a segment serves two openings, and the absorb remedy
# =====================================================================================
def check_segments_serve(spine: Spine) -> tuple[list[CirculationFinding],
                                                list[CirculationFinding]]:
    """Rule 4.3, re-checked. A run under CORRIDOR_MIN_DOORS_SERVED is an alcove.

    The foyer junction is deliberately NOT counted as one of the two. A corridor that runs
    from the foyer to a single bedroom door has two openings by the letter and is an alcove
    by the rule book's own next sentence ("a corridor serving one door is not a corridor").
    Counting only the doors into served rooms is the reading that makes the sentence true.
    """
    hard: list[CirculationFinding] = []
    soft: list[CirculationFinding] = []
    for i, seg in enumerate(spine.segments):
        n = seg.serves_count()
        if n < CORRIDOR_MIN_DOORS_SERVED:
            soft.append(CirculationFinding(
                "corridor_serves_too_few", "4.3", "soft",
                f"corridor run {i + 1} serves {n} door opening(s); rule 4.3 wants at least "
                f"{CORRIDOR_MIN_DOORS_SERVED} or the run should be absorbed into the room it "
                "serves",
                float(n), float(CORRIDOR_MIN_DOORS_SERVED), "count"))
    return hard, soft


def absorb_short_segments(spine: Spine) -> Spine:
    """Perform rule 4.3's remedy: delete alcove runs and hand their area to a room.

    The order matters. The SECOND run of an L is absorbed first, because absorbing it can
    only shorten the spine and never orphan the first; absorbing the first run of an L would
    leave the second floating with no route to the foyer, so when the first run is the short
    one the whole spine is absorbed and the openings it carried are marked
    ``reroute_required``. That is a topology change, and rule 11.3 says topology is not this
    module's to change — so it is reported for topology.py, not performed here.
    """
    if not spine.segments:
        return spine

    segments = list(spine.segments)
    absorbed = list(spine.absorbed)
    notes = list(spine.notes)

    # Second run first (see docstring).
    if len(segments) == 2 and segments[1].serves_count() < CORRIDOR_MIN_DOORS_SERVED:
        dropped = segments.pop(1)
        absorbed.append(_absorption_for(dropped))
        notes.append("rule 4.3: the L's second run served "
                     f"{dropped.serves_count()} opening(s) and was absorbed; the spine is now "
                     "straight.")

    if segments and segments[0].serves_count() < CORRIDOR_MIN_DOORS_SERVED:
        dropped = segments.pop(0)
        absorbed.append(_absorption_for(dropped))
        notes.append("rule 4.3: the only run served "
                     f"{dropped.serves_count()} opening(s) and was absorbed. This unit has no "
                     "corridor: the doors it would have carried must open off the foyer or "
                     "the living-dining, which is a topology decision (rule 11.3).")
        if segments:  # cannot happen given the order above, but a silent orphan would be worse
            raise AssertionError("absorbing run 1 left run 2 with no route to the foyer")

    if len(segments) == len(spine.segments):
        return spine
    shape: SpineShape = ("none", "straight", "L")[len(segments)]
    unserved = tuple(spine.unserved) + tuple(
        o.room_id for a in absorbed[len(spine.absorbed):] if a.reroute_required
        for o in ()  # placeholder; ids are carried on the Absorption itself
    )
    return replace(spine, segments=tuple(segments), shape=shape,
                   absorbed=tuple(absorbed), unserved=unserved, notes=tuple(notes))


def _absorption_for(seg: SpineSegment) -> Absorption:
    """Name the room a deleted run is absorbed into: the one door it served, if any."""
    served = tuple(o.room_id for o in seg.openings)
    if len(served) == 1:
        return Absorption(seg.rect, served[0], served,
                          "rule 4.3: a run serving one door is an alcove, not a corridor",
                          reroute_required=False)
    return Absorption(seg.rect, "", served,
                      "rule 4.3: a run serving no door is residual space — exactly the "
                      "'Passage 3' the current engine emits",
                      reroute_required=True)


# =====================================================================================
# G. Rule 4.4 — dead ends
# =====================================================================================
def dead_end_lengths(spine: Spine) -> dict[int, int]:
    """Millimetres of run beyond the last opening, per segment index, at the FREE end only.

    The anchored end is never a dead end: run 1 is anchored on the foyer and run 2 on the
    corner of run 1, and both are openings you can walk through. Only the far end of the last
    run can dead-end, plus the far end of run 1 when the L's corner does not reach it — which
    cannot happen by construction, but is measured anyway because the GA moves geometry.
    """
    out: dict[int, int] = {}
    for i, seg in enumerate(spine.segments):
        last = max((o.end_mm for o in seg.openings), default=0)
        is_last_run = (i == len(spine.segments) - 1)
        if not is_last_run:
            # The corner is an opening: run 2 leaves from the far end of run 1.
            out[i] = 0
            continue
        out[i] = max(0, seg.length_mm - last)
    return out


def check_dead_ends(spine: Spine) -> tuple[list[CirculationFinding],
                                           list[CirculationFinding]]:
    """Rule 4.4 re-check: no dead end longer than 600 past the last door. (hard, soft)."""
    hard: list[CirculationFinding] = []
    soft: list[CirculationFinding] = []
    for i, dead in dead_end_lengths(spine).items():
        if dead > CORRIDOR_DEAD_END_MAX_MM:
            soft.append(CirculationFinding(
                "corridor_dead_end", "4.4", "soft",
                f"corridor run {i + 1} continues {dead} mm past its last door; rule 4.4 caps "
                f"a dead end at {CORRIDOR_DEAD_END_MAX_MM} mm",
                float(dead), float(CORRIDOR_DEAD_END_MAX_MM), "mm"))
    return hard, soft


def _trim_dead_end(seg: SpineSegment) -> SpineSegment:
    """Cut a run back to its last jamb plus a pier. Rule 4.4, enforced by construction.

    The trim goes to the last opening plus JAMB_MIN_MM, not to the 600 mm the rule allows:
    600 mm of blank corridor at the end of a run is the wasted alcove of rule 4.3 wearing a
    different name, and the jamb pier is the smallest length that is genuinely needed (a leaf
    flush with the end wall has nothing to hang off).
    """
    last = max((o.end_mm for o in seg.openings), default=0)
    if last <= 0:
        return seg
    target = min(seg.length_mm, last + JAMB_MIN_MM)
    if target >= seg.length_mm or target <= 0:
        return seg
    return replace(seg, rect=_shrink_from_free_end(seg, target))


def _shrink_from_free_end(seg: SpineSegment, new_length: int) -> RectMM:
    """Shorten a run, keeping the anchored end where it is."""
    r = seg.rect
    if seg.axis == "h":
        x = r.x if seg.anchor_end == "lo" else r.x2 - new_length
        return RectMM(x, r.y, new_length, r.h)
    y = r.y if seg.anchor_end == "lo" else r.y2 - new_length
    return RectMM(r.x, y, r.w, new_length)


# =====================================================================================
# H. Rule 4.5 — direction changes between the main door and any room door
# =====================================================================================
def route_to_opening(spine: Spine, foyer_rect: RectMM, entry_point: tuple[int, int],
                     opening: SpineOpening) -> tuple[tuple[int, int], ...]:
    """The walked route from the open main door to one corridor door, as a polyline.

    Modelled on centrelines rather than on real doorway coordinates because at step 6 the
    rooms do not exist: what rule 4.5 counts is the number of TURNS a person makes, and that
    is a property of the spine's shape and the foyer's position, both of which are already
    fixed. Re-running this after geometry with real door points would give the same count.
    """
    pts: list[tuple[int, int]] = [tuple(entry_point)]  # type: ignore[list-item]
    foyer_centre = (foyer_rect.x + foyer_rect.w // 2, foyer_rect.y + foyer_rect.h // 2)
    pts.append(foyer_centre)

    for seg in spine.segments:
        holds = any(o is opening or (o.room_id == opening.room_id and o.t_mm == opening.t_mm)
                    for o in seg.openings)
        anchor = seg.anchor_point
        if holds:
            # Step onto the run's centreline, walk to the leaf centre, then turn into the room.
            pts.append(anchor)
            centre_t = opening.t_mm + opening.leaf_mm // 2
            on_run = seg.point_at(centre_t)
            pts.append(on_run)
            pts.append(_into_room(seg, on_run, opening))
            return _dedupe(pts)
        pts.append(anchor)
        pts.append(seg.free_point)
    return _dedupe(pts)


def _into_room(seg: SpineSegment, on_run: tuple[int, int],
               opening: SpineOpening) -> tuple[int, int]:
    """One step off the centreline through the leaf, so the final turn is counted."""
    half = seg.width_mm // 2
    step = half + 1                     # just past the wall face; direction is what matters
    x, y = on_run
    if opening.side == "end":
        sign = 1 if seg.anchor_end == "lo" else -1
        return (x + sign * step, y) if seg.axis == "h" else (x, y + sign * step)
    sign = -1 if opening.side == "low" else 1
    return (x, y + sign * step) if seg.axis == "h" else (x + sign * step, y)


def _dedupe(pts: Sequence[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    out: list[tuple[int, int]] = []
    for p in pts:
        if not out or p != out[-1]:
            out.append(tuple(p))  # type: ignore[arg-type]
    return tuple(out)


def direction_changes(route: Sequence[tuple[int, int]]) -> int:
    """Turns in a polyline: consecutive travel steps whose axis differs.

    A diagonal step cannot occur (every run is axis-aligned) and a zero-length step is
    dropped by ``_dedupe``, so this is exact rather than an approximation of a turn.
    """
    axes: list[Axis] = []
    for p, q in zip(route, route[1:]):
        dx, dy = q[0] - p[0], q[1] - p[1]
        if dx == 0 and dy == 0:
            continue
        axes.append("h" if abs(dx) >= abs(dy) else "v")
    return sum(1 for a, b in zip(axes, axes[1:]) if a != b)


def check_direction_changes(spine: Spine, foyer_rect: RectMM,
                            entry_point: tuple[int, int]
                            ) -> tuple[list[CirculationFinding], list[CirculationFinding],
                                       dict[str, int]]:
    """Rule 4.5 for every door off the spine. Returns (hard, soft, per-room counts).

    Three or more turns is, in the rule book's words, "the topology is wrong, not the
    geometry" — which is precisely why it is NOT a hard reject here: H01-H15 contains no code
    for it, and the fix belongs to topology.py. Reported as a soft finding with the count.
    """
    hard: list[CirculationFinding] = []
    soft: list[CirculationFinding] = []
    counts: dict[str, int] = {}
    for opening in spine.openings():
        route = route_to_opening(spine, foyer_rect, entry_point, opening)
        n = direction_changes(route)
        counts[opening.room_id] = n
        if n > MAX_DIRECTION_CHANGES:
            soft.append(CirculationFinding(
                "too_many_direction_changes", "4.5", "soft",
                f"reaching {opening.room_id} takes {n} changes of direction from the main "
                f"door; rule 4.5 allows {MAX_DIRECTION_CHANGES}, and reads a third as a "
                "topology fault rather than a geometry one",
                float(n), float(MAX_DIRECTION_CHANGES), "count"))
    return hard, soft, counts


# =====================================================================================
# I. Rule 4.6 — reachability, as a real graph traversal
# =====================================================================================
@dataclass(frozen=True, slots=True)
class ReachStep:
    """One traversal from ``crossed`` into ``reached``, and why it was or was not allowed."""

    crossed: str
    reached: str
    crossed_type: RoomType
    reached_type: RoomType
    kind: Literal["origin", "non_habitable", "through_space", "exception", "forbidden"]


@dataclass(frozen=True, slots=True)
class Reachability:
    """The rule 4.6 verdict, with the path that proves it for every room that has one."""

    ok: bool
    foyer_id: str
    paths: Mapping[str, tuple[str, ...]]
    unreachable: tuple[str, ...]
    steps: tuple[ReachStep, ...]
    through_space_crossings: tuple[ReachStep, ...]
    exceptions_used: tuple[ReachStep, ...]
    forbidden: tuple[ReachStep, ...]
    hard: tuple[CirculationFinding, ...]
    soft: tuple[CirculationFinding, ...]
    strict: bool


def _may_cross(crossed: RoomType, reached: RoomType, *, strict: bool
               ) -> tuple[bool, Literal["non_habitable", "through_space",
                                        "exception", "forbidden"]]:
    """May a route pass THROUGH ``crossed`` to arrive at ``reached``? Rule 4.6.

    RULE 4.6 CONFLICT, RESOLVED HERE AND STATED. Read literally, the rule permits exactly
    four pass-throughs and forbids all others — but the adjacency matrix of section 6 makes
    Dining-Kitchen an "R" (required) and Foyer-Kitchen and Living-Kitchen "F", so the kitchen
    can ONLY be reached by crossing the dining, which the four exceptions do not list. Under
    the literal reading every rule-conforming plan is H08, so the literal reading is
    self-contradictory and cannot be the intended one.

    The resolution taken: rule 7.1 already names living, dining, foyer and corridor
    "through-spaces", and rule 3.4's mandatory entry sequence routes every plan through the
    living. So a THROUGH_SPACE is crossable by definition, every other room blocks, and the
    four PASS_THROUGH_ALLOWED pairs are the only crossings of a blocking room. Nothing is
    hidden by this: every through-space crossing is recorded in
    ``Reachability.through_space_crossings`` so a reviewer can see the route the plan takes.

    ``strict=True`` gives the literal reading — through-spaces stop being free and only the
    four pairs and non-habitable rooms are crossable — so validate.py can show both answers
    without this module having to pick for it.
    """
    if (crossed, reached) in PASS_THROUGH_ALLOWED:
        return True, "exception"
    if crossed not in HABITABLE:
        # A foyer, corridor, toilet or store is not a habitable room and rule 4.6 says
        # nothing against crossing one. In practice the adjacency matrix forbids the door
        # that would make it possible, so this branch is permissive about a route the graph
        # will not contain.
        return True, "non_habitable"
    if not strict and crossed in THROUGH_SPACES:
        return True, "through_space"
    return False, "forbidden"


def reachable(graph: Any, *, foyer_id: Optional[str] = None,
              room_types: Optional[Mapping[str, Any]] = None,
              strict: bool = False,
              require: Optional[Iterable[str]] = None) -> Reachability:
    """Rule 4.6 — breadth-first from the foyer over the frozen access graph.

    Breadth-first rather than depth-first because the path this returns is the one a resident
    actually walks: the shortest one. A depth-first path would satisfy the rule with a route
    nobody takes, and the path is the evidence the finding is judged on.

    Every room the graph names must be reached, not only the habitable ones — an unreachable
    toilet is still an unreachable room and H08 does not restrict itself to habitable space —
    but only a habitable room's unreachability is reported with the rule 8.1 language, since
    that is the one the rule book's own sentence is about.
    """
    g = _as_graph(graph, room_types)
    start = foyer_id or g.find(RoomType.FOYER)
    if start is None:
        raise ValueError("rule 4.6 traverses from the foyer and this graph has none. Rule "
                         "3.2 makes exactly one foyer mandatory, so a graph without one has "
                         "already failed H02 and reachability cannot be measured.")

    paths: dict[str, tuple[str, ...]] = {start: (start,)}
    steps: list[ReachStep] = []
    forbidden: list[ReachStep] = []
    queue: list[str] = [start]
    while queue:
        node = queue.pop(0)
        node_t = g.node_type(node)
        assert node_t is not None                       # _as_graph rejects untyped nodes
        for nxt, _edge in g.neighbours(node):
            nxt_t = g.node_type(nxt)
            assert nxt_t is not None
            if node == start:
                allowed, kind = True, "non_habitable"
            else:
                allowed, kind = _may_cross(node_t, nxt_t, strict=strict)
            step = ReachStep(node, nxt, node_t, nxt_t,
                             "origin" if node == start else kind)  # type: ignore[arg-type]
            if not allowed:
                forbidden.append(step)
                continue
            if nxt in paths:
                continue
            steps.append(step)
            paths[nxt] = paths[node] + (nxt,)
            queue.append(nxt)

    wanted = set(require) if require is not None else set(g.room_types)
    unreachable = tuple(sorted(n for n in wanted if n not in paths))

    hard: list[CirculationFinding] = []
    soft: list[CirculationFinding] = []
    for room in unreachable:
        rt = g.node_type(room)
        habitable = rt in HABITABLE if rt is not None else False
        hard.append(CirculationFinding(
            "H08", "4.6", "hard",
            f"{room} ({rt}) cannot be reached from the foyer without crossing a room rule "
            f"4.6 does not permit"
            + (" — and it is a habitable room, so this is not a routing inconvenience but a "
               "dwelling with a room you get to through someone's bedroom" if habitable else ""),
            0.0, 1.0, "count"))

    for step in forbidden:
        soft.append(CirculationFinding(
            "pass_through_blocked", "4.6", "note",
            f"route {step.crossed} -> {step.reached} was refused: crossing a "
            f"{step.crossed_type} is not one of the four permitted exceptions",
            0.0, 0.0, "count"))

    through = tuple(s for s in steps if s.kind == "through_space")
    used = tuple(s for s in steps if s.kind == "exception")
    return Reachability(
        ok=not unreachable, foyer_id=start, paths=paths, unreachable=unreachable,
        steps=tuple(steps), through_space_crossings=through, exceptions_used=used,
        forbidden=tuple(forbidden), hard=tuple(hard), soft=tuple(soft), strict=strict)


# =====================================================================================
# J. lay_spine — the designed object
# =====================================================================================
@dataclass(frozen=True, slots=True)
class _Door:
    """One opening the spine has to carry, before any of it has a position."""

    room_id: str
    room_type: RoomType
    leaf_mm: int
    min_room_width_mm: int


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One placement of the first run, before doors are packed onto it."""

    axis: Axis
    run_dir: int                 # +1 grows toward higher coordinate, -1 toward lower
    run_start: int               # run coordinate of the anchored end
    run_avail: int               # how far the run may grow before leaving the envelope
    off: int                     # perpendicular coordinate of the run's low face
    width: int
    space_low: int               # depth available on the low-coordinate side
    space_high: int
    kind: Literal["inward", "lateral"]
    reach_needed: int            # run length that would touch the private band


def lay_spine(graph: Any, zones: Any, envelope: Any, foyer_rect: Any, *,
              entry_edge: Optional[str] = None,
              room_types: Optional[Mapping[str, Any]] = None,
              accessible: bool = False,
              version: Optional[str] = None,
              carpet_area_hint_mm2: Optional[int] = None) -> Spine:
    """Lay the circulation spine: step 6 of the mandatory order, before any room exists.

    The spine is a BOX placed within the plan, never a band across it. A full-width band
    would force every room on both sides to span the remaining depth, which is the long-thin
    rectangle of section 0 arriving by a second route — the corridor would have solved the
    corridor problem and caused the room problem.

    Order of decisions, each of which is a rule:

      1. Width from rule 4.1 (preferred 1050, or the real NBC accessible 1200).
      2. The doors the run has to carry, read off the FROZEN access graph — every edge
         incident on the corridor except the one to the foyer, which is the junction the run
         starts from and not one of rule 4.3's two.
      3. Two placements are costed: the run driven INWARD from the foyer along the privacy
         gradient, and the run laid LATERALLY across it. On this project's plates (5.6-8.6 m
         deep, a 2BHK about 11.8 m wide) the inward run has too little depth to load both
         sides and the lateral run has too little on the far side — which one wins is a
         property of the footprint, so it is measured per unit rather than assumed.
      4. Doors are packed onto the run's two long faces and, at most once, onto its end wall,
         each only where the room it serves could actually fit on that side.
      5. Rule 4.4 trims the run back to its last jamb.
      6. Rule 4.3 absorbs any run left serving fewer than two doors.

    ``carpet_area_hint_mm2`` is optional and advisory: when the caller knows the carpet area
    it lets the spine warn that it is about to spend more than rule 4.2's band. It never
    shortens a run — a run trimmed below its door schedule produces a room you cannot enter,
    and a visible H10 is a better failure than a silent one.
    """
    env_rect, edge = _envelope_view(envelope, entry_edge)
    foyer = _as_rect(foyer_rect)
    g = _as_graph(graph, room_types)
    width = spine_width_mm(accessible=accessible, version=version)
    notes: list[str] = []

    corridor_id = g.find(RoomType.CORRIDOR)
    if corridor_id is None:
        return Spine((), width, "none", foyer, (), (),
                     ("this programme has no corridor: every door opens off the foyer or the "
                      "living-dining, which is the correct answer for a studio and for any "
                      "plan whose access graph never names one.",))

    doors = _doors_for(g, corridor_id, accessible=accessible, version=version)
    if not doors:
        return Spine((), width, "none", foyer, (), (),
                     ("the access graph gives the corridor no doors to serve; rule 4.3 would "
                      "absorb any run laid for it, so none is laid.",))

    candidates = _candidates(env_rect, edge, foyer, width, zones)
    if not candidates:
        return Spine((), width, "none", foyer, (), tuple(d.room_id for d in doors),
                     ("no corridor run fits between the foyer and the envelope edge at "
                      f"{width} mm clear; the doors below must be re-routed by topology.",))

    best: Optional[tuple[tuple, Spine]] = None
    for cand in candidates:
        spine = _build(cand, doors, foyer, notes_seed=())
        key = (len(spine.unserved),                       # serving every door beats everything
               0 if spine.shape == "straight" else 1,     # rule 4.7 prefers the simpler shape
               spine.area_mm2)                            # then rule 4.2 prefers the cheaper
        if best is None or key < best[0]:
            best = (key, spine)
    assert best is not None
    spine = best[1]

    spine = absorb_short_segments(spine)

    if carpet_area_hint_mm2 and carpet_area_hint_mm2 > 0:
        pct = 100.0 * (spine.area_mm2 + foyer.area_mm2) / carpet_area_hint_mm2
        if pct > CIRCULATION_SOFT_MAX_PCT:
            notes.append(
                f"foyer + spine is {pct:.1f}% of the {mm2_to_sqm(carpet_area_hint_mm2):.1f} m2 "
                f"carpet hint, above the rule 4.2 band. The run was NOT shortened for it: a "
                "run cut below its door schedule produces a room with no door.")
        elif pct < CIRCULATION_MIN_PCT:
            notes.append(
                f"foyer + spine is {pct:.1f}% of the carpet hint, below rule 4.2's 8% floor. "
                "The run was not padded: padding it would create the dead end rule 4.4 "
                "forbids. A low figure here is a statement about the access graph.")

    if spine.unserved:
        notes.append(
            "the run could not reach " + ", ".join(spine.unserved) + " — no side of the "
            "corridor has the depth those rooms need. Their doors have to come off another "
            "space, which is topology.py's decision under rule 11.3, not this module's.")

    return replace(spine, notes=tuple(spine.notes) + tuple(notes))


def _doors_for(g: _GraphView, corridor_id: str, *, accessible: bool,
               version: Optional[str]) -> tuple[_Door, ...]:
    """The doors the corridor carries, in privacy order along the run.

    The edge to the foyer is dropped: it is the junction the run starts from, and counting it
    toward rule 4.3's two would let a corridor-to-one-bedroom alcove pass the rule whose own
    next sentence describes exactly that alcove.
    """
    out: list[_Door] = []
    for nxt, _edge in g.neighbours(corridor_id):
        rt = g.node_type(nxt)
        if rt is None or rt is RoomType.FOYER:
            continue
        leaf = door_spec(rt, accessible=accessible).width_mm
        out.append(_Door(nxt, rt, leaf, int(min_width(rt, version).value)))
    out.sort(key=lambda d: (_ZONE_RANK.get(zone_of(d.room_type), 9), str(d.room_type),
                            d.room_id))
    return tuple(out)


def _candidates(env: RectMM, edge: Edge, foyer: RectMM, width: int,
                zones: Any) -> tuple[_Candidate, ...]:
    """Both placements of the first run: driven inward from the foyer, or laid laterally.

    Every candidate anchors on the foyer's inner face and overlaps the foyer over at least
    the corridor width, so the foyer-to-corridor connection rule 3.4 permits is a real shared
    edge and not a corner touch.
    """
    dx, dy = _INWARD_OF_EDGE[edge]
    depth_axis: Axis = "v" if dx == 0 else "h"
    lat_axis: Axis = "h" if depth_axis == "v" else "v"
    inward_sign = dy if depth_axis == "v" else dx

    # Depth coordinate of the foyer's inner face, and how much depth is left beyond it.
    if depth_axis == "v":
        inner_face = foyer.y2 if inward_sign > 0 else foyer.y
        depth_left = (env.y2 - inner_face) if inward_sign > 0 else (inner_face - env.y)
        lat_lo, lat_hi = env.x, env.x2
        foyer_lat_lo, foyer_lat_hi = foyer.x, foyer.x2
    else:
        inner_face = foyer.x2 if inward_sign > 0 else foyer.x
        depth_left = (env.x2 - inner_face) if inward_sign > 0 else (inner_face - env.x)
        lat_lo, lat_hi = env.y, env.y2
        foyer_lat_lo, foyer_lat_hi = foyer.y, foyer.y2

    private = _zone_band(zones, Zone.PRIVATE)
    out: list[_Candidate] = []

    # ---- candidate 1: INWARD. The run follows the privacy gradient away from the entry.
    if depth_left >= width * MIN_SEGMENT_LENGTH_FACTOR:
        off = max(lat_lo, min(foyer_lat_lo, lat_hi - width))
        if foyer_lat_hi - foyer_lat_lo >= width:
            off = max(lat_lo, min(off, foyer_lat_hi - width))
        reach = _reach_to_band(private, depth_axis, inner_face, inward_sign)
        out.append(_Candidate(
            axis=depth_axis, run_dir=inward_sign, run_start=inner_face,
            run_avail=depth_left, off=off, width=width,
            space_low=off - lat_lo, space_high=lat_hi - (off + width),
            kind="inward", reach_needed=reach))

    # ---- candidate 2: LATERAL, in each direction the foyer has room to run.
    # The band sits immediately inside the foyer's inner face, which is at or about the
    # public zone's 30% boundary — so the run separates the public side from the private one
    # instead of cutting through either.
    if depth_left >= width:
        band_lo = inner_face if inward_sign > 0 else inner_face - width
        for run_dir in (1, -1):
            start = foyer_lat_lo if run_dir > 0 else foyer_lat_hi
            avail = (lat_hi - start) if run_dir > 0 else (start - lat_lo)
            if avail < width * MIN_SEGMENT_LENGTH_FACTOR:
                continue
            near = band_lo - (env.y if depth_axis == "v" else env.x)
            far = depth_left - width
            out.append(_Candidate(
                axis=lat_axis, run_dir=run_dir, run_start=start, run_avail=avail,
                off=band_lo, width=width,
                space_low=near if inward_sign > 0 else far,
                space_high=far if inward_sign > 0 else near,
                kind="lateral", reach_needed=0))
    return tuple(out)


def _reach_to_band(band: Optional[RectMM], axis: Axis, from_coord: int, sign: int) -> int:
    """How far an inward run must travel to touch the private band. 0 when unknown.

    zones.py may not have produced bands yet, in which case this returns 0 and the run is
    sized purely by its door schedule — which is the honest answer, not a guessed distance.
    """
    if band is None:
        return 0
    near = (band.y if sign > 0 else band.y2) if axis == "v" else (band.x if sign > 0 else band.x2)
    return max(0, (near - from_coord) * sign)


def _build(cand: _Candidate, doors: Sequence[_Door], foyer: RectMM,
           notes_seed: Sequence[str]) -> Spine:
    """Pack the doors onto one candidate, trim it, and return the resulting spine.

    Packing is greedy on the two long faces plus, at most once, the end wall. A door is only
    placed on a face whose available depth is at least the served room's own minimum clear
    width: a bedroom cannot open off a corridor with 1.2 m behind it, and placing the door
    there anyway would produce a plan that only fails later, in geometry, with a worse
    message.
    """
    cur = {"low": OPENING_GAP_MIN_MM, "high": OPENING_GAP_MIN_MM}
    placed: list[SpineOpening] = []
    end_used = False
    leftovers: list[_Door] = []

    space = {"low": cand.space_low, "high": cand.space_high}
    for door in doors:
        sides = [s for s in ("low", "high") if space[s] >= door.min_room_width_mm]
        if sides:
            side = min(sides, key=lambda s: (cur[s], s))
            t = cur[side]
            if t + door.leaf_mm + JAMB_MIN_MM > cand.run_avail:
                leftovers.append(door)
                continue
            placed.append(SpineOpening(door.room_id, door.room_type,
                                       side, t, door.leaf_mm))  # type: ignore[arg-type]
            cur[side] = t + door.leaf_mm + OPENING_GAP_MIN_MM
            continue
        leftovers.append(door)

    # The end wall takes one door, and only if the room beyond it fits in what is left of the
    # run direction. It is what makes a zero-length dead end possible (rule 4.4).
    if leftovers and not end_used:
        need = max((o.end_mm for o in placed), default=0) + JAMB_MIN_MM
        for door in list(leftovers):
            if need + door.min_room_width_mm <= cand.run_avail:
                placed.append(SpineOpening(door.room_id, door.room_type, "end",
                                           max(0, need - door.leaf_mm), door.leaf_mm))
                leftovers.remove(door)
                end_used = True
                break

    length = max((o.end_mm for o in placed), default=0) + JAMB_MIN_MM
    length = max(length, cand.reach_needed, cand.width)
    length = min(length, cand.run_avail)
    if length <= 0:
        return Spine((), cand.width, "none", foyer, (), tuple(d.room_id for d in doors),
                     tuple(notes_seed))

    seg = SpineSegment(_run_rect(cand, length), cand.axis,
                       "lo" if cand.run_dir > 0 else "hi", tuple(placed))
    seg = _trim_dead_end(seg)
    segments = [seg]
    notes = list(notes_seed)

    # ---- rule 4.7: one perpendicular leg, and only one, for whatever run 1 could not hold.
    if leftovers:
        leg = _second_run(cand, seg, leftovers)
        if leg is not None:
            leg_seg, leg_left = leg
            segments.append(_trim_dead_end(leg_seg))
            leftovers = leg_left
            notes.append("rule 4.7: run 1 could not carry every door, so the spine turns "
                         "once. A second turn would be a branching corridor, which rule 4.7 "
                         "forbids at this scale.")

    shape: SpineShape = ("none", "straight", "L")[len(segments)]
    return Spine(tuple(segments), cand.width, shape, foyer, (),
                 tuple(d.room_id for d in leftovers), tuple(notes))


def _run_rect(cand: _Candidate, length: int) -> RectMM:
    if cand.axis == "h":
        x = cand.run_start if cand.run_dir > 0 else cand.run_start - length
        return RectMM(x, cand.off, length, cand.width)
    y = cand.run_start if cand.run_dir > 0 else cand.run_start - length
    return RectMM(cand.off, y, cand.width, length)


def _second_run(cand: _Candidate, first: SpineSegment,
                doors: Sequence[_Door]) -> Optional[tuple[SpineSegment, list[_Door]]]:
    """The L's leg: perpendicular, leaving the far end of run 1, toward the roomier side.

    The leg's rectangle starts at run 1's perpendicular face so the two never overlap — the
    corner square belongs to run 1 and is counted once in the rule 4.2 area.
    """
    axis: Axis = "v" if cand.axis == "h" else "h"
    r = first.rect
    leg_dir = 1 if cand.space_high >= cand.space_low else -1
    avail = cand.space_high if leg_dir > 0 else cand.space_low
    if avail < cand.width:
        return None

    # The leg's band spans the far end of run 1, its length grows away perpendicular.
    if axis == "v":
        band_lo = r.y if first.anchor_end == "hi" else r.y2 - cand.width
        start = r.x2 if leg_dir > 0 else r.x
    else:
        band_lo = r.x if first.anchor_end == "hi" else r.x2 - cand.width
        start = r.y2 if leg_dir > 0 else r.y

    space_low = cand.space_low if axis == cand.axis else _leg_side_space(cand, first, "low")
    space_high = cand.space_high if axis == cand.axis else _leg_side_space(cand, first, "high")

    leg = _Candidate(axis=axis, run_dir=leg_dir, run_start=start, run_avail=avail,
                     off=band_lo, width=cand.width,
                     space_low=space_low, space_high=space_high,
                     kind=cand.kind, reach_needed=0)

    inner = _build(leg, doors, first.rect, notes_seed=())
    if not inner.segments:
        return None
    return inner.segments[0], [d for d in doors if d.room_id in set(inner.unserved)]


def _leg_side_space(cand: _Candidate, first: SpineSegment,
                    side: Literal["low", "high"]) -> int:
    """Depth available either side of the L's leg, along run 1's own axis.

    Run 1 occupies part of that depth, so the leg's near side is what is left between the
    leg and the envelope edge on run 1's anchored side, and its far side is unconstrained by
    run 1. Both are approximations of a plan that does not exist yet, which is why they are
    only ever used to decide whether a door MAY sit on a face, never to size a room.
    """
    if side == "low":
        return max(0, first.length_mm)
    return max(0, cand.run_avail - first.length_mm)


# =====================================================================================
# K. The aggregate check — every section 4 rule, re-measured on the emitted object
# =====================================================================================
@dataclass(frozen=True, slots=True)
class CirculationResult:
    """Section 4's verdict. ``hard`` and ``soft`` are separate tuples and stay that way."""

    ok: bool
    spine: Spine
    verdict: CirculationVerdict
    reach: Optional[Reachability]
    dead_ends_mm: Mapping[int, int]
    direction_changes: Mapping[str, int]
    hard: tuple[CirculationFinding, ...]
    soft: tuple[CirculationFinding, ...]
    soft_terms: Mapping[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "spine": self.spine.to_dict(),
            "circulation_pct": self.verdict.pct,
            "circulation_band": self.verdict.band,
            "circulation_sqm": mm2_to_sqm(self.verdict.circulation_mm2),
            "carpet_sqm": mm2_to_sqm(self.verdict.carpet_mm2),
            "dead_ends_mm": dict(self.dead_ends_mm),
            "direction_changes": dict(self.direction_changes),
            "reachable": None if self.reach is None else {
                "ok": self.reach.ok,
                "unreachable": list(self.reach.unreachable),
                "paths": {k: list(v) for k, v in self.reach.paths.items()},
                "through_space_crossings": [
                    f"{s.crossed}->{s.reached}" for s in self.reach.through_space_crossings],
                "exceptions_used": [
                    f"{s.crossed}->{s.reached}" for s in self.reach.exceptions_used],
            },
            "hard": [f.to_dict() for f in self.hard],
            "soft": [f.to_dict() for f in self.soft],
            "soft_terms": dict(self.soft_terms),
        }


def check_circulation(rooms: Sequence[Any], spine: Spine, *,
                      foyer_rect: Optional[Any] = None,
                      entry_point: Optional[tuple[int, int]] = None,
                      graph: Any = None,
                      room_types: Optional[Mapping[str, Any]] = None,
                      accessible: bool = False,
                      version: Optional[str] = None,
                      strict_reachability: bool = False) -> CirculationResult:
    """Re-measure every section 4 rule on geometry that already exists.

    This is the second half of the house rule that a hard constraint is enforced by
    construction AND re-checked geometrically. ``lay_spine`` cannot be the only enforcement
    because the GA moves dimensions afterwards (rule 11.3 permits it to move the corridor's
    position within its zone band), and a trim that was correct at step 6 is not necessarily
    correct after generation 200.
    """
    hard: list[CirculationFinding] = []
    soft: list[CirculationFinding] = []

    circ = circulation_area_mm2(rooms)
    carpet = carpet_area_mm2(rooms)
    pct = 100.0 * circ / carpet if carpet > 0 else 0.0
    verdict = classify_circulation(pct, circulation_mm2=circ, carpet_mm2=carpet)
    hard.extend(verdict.hard)
    soft.extend(verdict.soft)

    for fn in (check_width, check_shape, check_segments_serve, check_dead_ends):
        if fn is check_width:
            h, s = check_width(spine, accessible=accessible, version=version)
        else:
            h, s = fn(spine)  # type: ignore[assignment]
        hard.extend(h)
        soft.extend(s)

    foyer = _as_rect(foyer_rect) if foyer_rect is not None else spine.foyer_rect
    changes: dict[str, int] = {}
    if foyer is not None and spine.segments:
        ep = entry_point or (foyer.x + foyer.w // 2, foyer.y)
        h, s, changes = check_direction_changes(spine, foyer, ep)
        hard.extend(h)
        soft.extend(s)

    reach: Optional[Reachability] = None
    if graph is not None:
        reach = reachable(graph, room_types=room_types, strict=strict_reachability)
        hard.extend(reach.hard)
        soft.extend(reach.soft)

    return CirculationResult(
        ok=not hard, spine=spine, verdict=verdict, reach=reach,
        dead_ends_mm=dead_end_lengths(spine), direction_changes=changes,
        hard=tuple(hard), soft=tuple(soft),
        soft_terms={"circulation_efficiency": verdict.penalty})


# =====================================================================================
# L. Import-time self-checks
# =====================================================================================
# Real rejections, in the shape spec.py established: a bad edit fails collection rather than
# a request. Everything here is a relationship BETWEEN constants, because a relationship is
# what a later edit silently breaks.
def _self_check() -> None:
    if not (CORRIDOR_MIN_WIDTH_MM <= CORRIDOR_PREF_WIDTH_MM
            <= CORRIDOR_ACCESSIBLE_WIDTH_MM):
        raise AssertionError("rule 4.1's three widths are out of order; the preferred width "
                             "must sit between the minimum and the accessible figure.")
    if JAMB_MIN_MM > CORRIDOR_DEAD_END_MAX_MM:
        raise AssertionError(
            "the jamb pier a trimmed run keeps is larger than rule 4.4's dead-end cap, so "
            "_trim_dead_end would create the violation it exists to prevent.")
    if CIRCULATION_TYPES & {RoomType.LIVING, RoomType.DINING, RoomType.LIVING_DINING}:
        raise AssertionError("rule 4.2 excludes living and dining from the circulation "
                             "numerator by name.")
    if not CARPET_EXCLUDED.isdisjoint(CIRCULATION_TYPES):
        raise AssertionError("a room excluded from carpet area cannot also be counted as "
                             "circulation: the percentage would exceed 100 with no bug "
                             "visible anywhere.")
    missing = [str(rt) for rt in (RoomType.FOYER, RoomType.CORRIDOR)
               if rt not in THROUGH_SPACES]
    if missing:
        raise AssertionError(f"{missing} must be through-spaces or the rule 4.6 traversal "
                             "cannot leave the foyer.")
    # The four permitted exceptions must all name a crossing of a room that actually blocks,
    # or PASS_THROUGH_ALLOWED would be silently redundant with the non-habitable branch.
    if not any(a in HABITABLE for a, _ in PASS_THROUGH_ALLOWED):
        raise AssertionError("no PASS_THROUGH_ALLOWED pair crosses a habitable room, so "
                             "rule 4.6's exception list is doing nothing.")
    if PUBLIC_ZONE_DEPTH_FRACTION <= 0 or PUBLIC_ZONE_DEPTH_FRACTION >= 1:
        raise AssertionError("the public zone depth fraction must be a proper fraction.")
    if MAX_DIRECTION_CHANGES < 1:
        raise AssertionError("rule 4.5 must permit at least one turn; a plan with none is a "
                             "single straight corridor from the front door.")


_self_check()
