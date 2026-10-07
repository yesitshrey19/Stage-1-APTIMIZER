"""Construction schedule engine: quantities -> activities -> CPM -> dates, float, cash flow.

The point of difference from a general scheduler (MS Project, Primavera) is that durations
are DERIVED, not typed. The app already knows the quantity of every work item and the daily
output of every trade, so

    work_days = quantity / (output_per_day x crew_size)

which means changing the tower from 12 to 18 floors re-plans the job automatically. A
general scheduler cannot do this because it does not know the bill of quantities.

SAFETY
------
A construction programme is not a neutral drawing. Formwork striking and back-prop removal
times are the single most common cause of slab collapse on Indian residential sites, and a
programme that asks a site to strike props early is an instruction to do something
dangerous. So the striking lags in FORMWORK_IS456 below are HARD minimums taken from
IS 456:2000 Cl. 11.3, applied as calendar-day lags that no crew size, no compression and
no user input can shorten. If a caller tries, the activity is clamped and a critical
warning is raised rather than the programme silently accepting it.

Curing and striking are measured in CALENDAR days, never working days -- concrete gains
strength on Sundays and holidays too. Conflating the two shortens a 14-day prop period to
roughly 11 real days, which is exactly the error this module exists to prevent.
"""
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- safety constants
# IS 456:2000 Table 11 / Cl. 11.3 -- minimum period before striking formwork, in CALENDAR
# days, for OPC without accelerators at an average ambient temperature >= 15 C.
FORMWORK_IS456 = {
    "vertical_sides": 1.0,          # columns, walls, beam sides (16-24 h)
    "slab_soffit_props_left": 3.0,  # soffit sheeting only, props remain
    "beam_soffit_props_left": 7.0,
    "props_slab_span_le_4_5m": 7.0,
    "props_slab_span_gt_4_5m": 14.0,
    "props_beam_span_le_6m": 14.0,
    "props_beam_span_gt_6m": 21.0,
}
# IS 456:2000 Cl. 13.5 -- minimum curing period.
CURING_MIN_DAYS = 7.0
CURING_MIN_DAYS_BLENDED = 10.0   # mineral-admixture (PPC/PSC) concrete


def prop_removal_days(slab_span_m: float, blended_cement: bool = False) -> Tuple[float, str]:
    """Minimum calendar days before slab props may be removed, with the governing reason.

    This is the constraint that decides how fast a floor cycle may legally run. It is
    returned with its justification so the UI can show WHY a cycle cannot be compressed.
    """
    if slab_span_m > 4.5:
        days, why = (FORMWORK_IS456["props_slab_span_gt_4_5m"],
                     f"IS 456 Cl. 11.3: props under a slab spanning {slab_span_m:.1f} m "
                     "(over 4.5 m) must stay for 14 days")
    else:
        days, why = (FORMWORK_IS456["props_slab_span_le_4_5m"],
                     f"IS 456 Cl. 11.3: props under a slab spanning {slab_span_m:.1f} m "
                     "(up to 4.5 m) must stay for 7 days")
    cure = CURING_MIN_DAYS_BLENDED if blended_cement else CURING_MIN_DAYS
    if cure > days:
        days = cure
        why = (f"IS 456 Cl. 13.5: {'blended-cement' if blended_cement else 'OPC'} concrete "
               f"requires {cure:.0f} days of curing")
    return days, why


# ---------------------------------------------------------------- calendar
class WorkCalendar:
    """Working-day calendar with an optional weather rule for exposed work.

    `monsoon_months` suppress only activities flagged weather-sensitive (external plaster,
    painting, roads, waterproofing). Internal work continues through the monsoon, which is
    how Indian sites actually operate.
    """

    def __init__(self, work_week: Sequence[int] = (0, 1, 2, 3, 4, 5),
                 holidays: Sequence[str] = (), monsoon_months: Sequence[int] = (6, 7, 8),
                 monsoon_blocks_exposed: bool = True):
        self.work_week = set(int(d) for d in work_week)       # 0 = Monday
        self.holidays = {self._as_date(h) for h in holidays if h}
        self.monsoon_months = set(int(m) for m in monsoon_months)
        self.monsoon_blocks_exposed = bool(monsoon_blocks_exposed)

    @staticmethod
    def _as_date(v) -> date:
        if isinstance(v, date):
            return v
        return date.fromisoformat(str(v)[:10])

    def is_working(self, d: date, exposed: bool = False) -> bool:
        if d.weekday() not in self.work_week or d in self.holidays:
            return False
        if exposed and self.monsoon_blocks_exposed and d.month in self.monsoon_months:
            return False
        return True

    def next_working(self, d: date, exposed: bool = False) -> date:
        guard = 0
        while not self.is_working(d, exposed):
            d += timedelta(days=1)
            guard += 1
            if guard > 400:   # a calendar with no working days at all would loop forever
                raise ScheduleError("no_working_days",
                                    "The calendar has no working days -- check the work "
                                    "week, holidays and monsoon settings.")
        return d

    def add_work_days(self, start: date, work_days: int, exposed: bool = False) -> date:
        """Return the day AFTER the last working day (half-open finish)."""
        d = self.next_working(start, exposed)
        remaining = max(int(work_days), 1)
        while remaining > 1:
            d += timedelta(days=1)
            d = self.next_working(d, exposed)
            remaining -= 1
        return d + timedelta(days=1)

    def sub_work_days(self, finish: date, work_days: int, exposed: bool = False) -> date:
        """Inverse of add_work_days: latest start that still finishes by `finish`."""
        d = finish - timedelta(days=1)
        guard = 0
        while not self.is_working(d, exposed):
            d -= timedelta(days=1)
            guard += 1
            if guard > 400:
                raise ScheduleError("no_working_days", "The calendar has no working days.")
        remaining = max(int(work_days), 1)
        while remaining > 1:
            d -= timedelta(days=1)
            while not self.is_working(d, exposed):
                d -= timedelta(days=1)
            remaining -= 1
        return d

    def work_days_between(self, a: date, b: date, exposed: bool = False) -> int:
        if b <= a:
            return 0
        n, d = 0, a
        while d < b:
            if self.is_working(d, exposed):
                n += 1
            d += timedelta(days=1)
        return n


class ScheduleError(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra

    def to_dict(self):
        return {"ok": False, "error": {"code": self.code, "message": self.message, **self.extra}}


# ---------------------------------------------------------------- model
@dataclass
class Dep:
    pred: str
    kind: str = "FS"          # FS | SS | FF
    lag_days: float = 0.0     # CALENDAR days (see module docstring)
    hard: bool = False        # a code-mandated minimum that may not be compressed
    reason: str = ""


@dataclass
class Activity:
    id: str
    name: str
    phase: str
    trade: str = ""
    quantity: float = 0.0
    unit: str = ""
    output_per_day: float = 0.0
    crew: int = 1
    work_days: int = 1
    exposed: bool = False           # weather-sensitive
    cost: float = 0.0
    tower: str = ""
    floor: Optional[int] = None     # drives the Line of Balance view
    deps: List[Dep] = field(default_factory=list)
    milestone: bool = False
    # What compression cost on this task, kept apart from the base labour so the user can
    # see the price of speed rather than finding it blended into a rate.
    acceleration_premium: float = 0.0
    crew_multiplier: float = 1.0
    crew_efficiency: float = 1.0
    custom: bool = False            # added by the user, not derived from the quantities
    removable: bool = True          # False when a code-mandated lag hangs off it
    # Which fields the user overrode, and the generated values they replaced. Both are
    # carried so the UI can mark edited cells and a reset can restore the original.
    edited: Dict[str, Any] = field(default_factory=dict)
    generated: Dict[str, Any] = field(default_factory=dict)
    pinned_start: Optional[date] = None
    pinned_finish: Optional[date] = None
    pin_conflict: str = ""          # why a pin could not be honoured, if it could not
    # Display order within a phase. Reordering is DISPLAY only -- dependencies decide when
    # work happens -- so this never reaches the CPM.
    order: Optional[int] = None
    # computed
    es: Optional[date] = None
    ef: Optional[date] = None
    ls: Optional[date] = None
    lf: Optional[date] = None
    total_float: int = 0
    free_float: int = 0
    critical: bool = False          # on the longest (driving) path
    driver: Optional[str] = None    # predecessor that actually set this early start

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "phase": self.phase, "trade": self.trade,
            "quantity": round(self.quantity, 2), "unit": self.unit,
            "output_per_day": self.output_per_day, "crew": self.crew,
            "work_days": self.work_days, "exposed": self.exposed,
            "cost": round(self.cost, 2), "tower": self.tower, "floor": self.floor,
            "milestone": self.milestone, "custom": self.custom,
            "acceleration_premium": round(self.acceleration_premium, 2),
            "crew_multiplier": self.crew_multiplier,
            "crew_efficiency": self.crew_efficiency,
            "removable": self.removable,
            "edited": self.edited, "generated": self.generated,
            "pinned_start": self.pinned_start.isoformat() if self.pinned_start else None,
            "pinned_finish": self.pinned_finish.isoformat() if self.pinned_finish else None,
            "pin_conflict": self.pin_conflict,
            "order": self.order,
            "start": self.es.isoformat() if self.es else None,
            "finish": (self.ef - timedelta(days=1)).isoformat() if self.ef else None,
            "late_start": self.ls.isoformat() if self.ls else None,
            "late_finish": (self.lf - timedelta(days=1)).isoformat() if self.lf else None,
            "total_float_days": self.total_float, "free_float_days": self.free_float,
            "critical": self.critical,
            # The predecessor that actually set this start -- the one link a planner needs
            # to see. The full list is in `predecessors` below.
            "driver": self.driver,
            "predecessors": [{"id": d.pred, "type": d.kind, "lag_days": d.lag_days,
                              "hard": d.hard, "reason": d.reason} for d in self.deps],
        }


# ---------------------------------------------------------------- time / cost curve
# Acceleration is not free, and the model used to say it was. Duration is
# quantity / (output x crew) and labour cost was work_days x crew x wage, so the crew term
# cancelled exactly: doubling the crew halved the days, doubled the headcount, and landed
# on the same rupee figure. Pulling a finish date in therefore cost nothing, which is wrong
# in the direction that matters commercially -- it is precisely the trade a developer is
# being asked to price.
#
# Three mechanisms put the cost back, and all three are tunable here rather than buried.

# (a) PRODUCTIVITY DERATING. Output per head falls as more crews share one front: the same
# hoist, the same pour front, the same access. These are the standard congestion figures
# used for delay-and-disruption analysis -- roughly 15% lost at double crew and nearly 30%
# at triple. Interpolated linearly between the points.
CREW_EFFICIENCY = [
    (1.0, 1.00),
    (1.5, 0.92),
    (2.0, 0.85),
    (2.5, 0.78),
    (3.0, 0.72),
]

# (b) ACCELERATION PREMIUM on wages. Compressing beyond the natural duration means
# overtime, second shifts and weekend working. Reported as its own cost line so the user
# sees what speed cost rather than finding it blended into the rate.
WAGE_PREMIUM = [
    (1.0, 0.00),
    (1.5, 0.12),
    (2.0, 0.20),
    (2.5, 0.27),
    (3.0, 0.32),
]

# (c) TIME-RELATED PRELIMINARIES. A longer programme costs more with no extra labour at
# all: site establishment, supervision, plant hire, temporary works and finance all run per
# month. The RATE is not invented here -- it is engine.DEFAULT_COST_ADDERS["preliminaries_pct"]
# converted to a monthly figure over the baseline duration, so the two modules cannot
# disagree about what preliminaries cost.


# The programme duration used as the reference for the monthly preliminaries rate. The
# engine expresses preliminaries as a percentage of works cost for a project of ordinary
# length; converting that to a per-month figure needs a length to divide by, and the
# baseline programme is the only non-arbitrary one available.
def preliminaries_per_month(project: dict, analysis: dict, baseline_months: float) -> float:
    """Monthly time-related cost, derived from the engine's own preliminaries percentage.

    A longer programme costs more even with no extra labour: site establishment,
    supervision, plant hire, temporary works and finance all run per month. Without this
    the model says a slower build is free, which is the mirror of the bug that said a
    faster one was.
    """
    import engine as _engine
    add = {**_engine.DEFAULT_COST_ADDERS, **(project.get("cost_adders") or {})}
    pct = float(add.get("preliminaries_pct") or 0)
    works = float((analysis.get("boq") or {}).get("works_total") or 0)
    if works <= 0:
        builtup = float((analysis.get("areas") or {}).get("builtup_area_sqm") or 0)
        if builtup <= 0:
            towers = project.get("towers") or []
            builtup = sum(float(t.get("builtup_area_sqm") or 0) for t in towers)
        rate = float((project.get("costs") or {}).get("rate_per_sqm") or 22000.0)
        works = builtup * rate

    if pct <= 0 or works <= 0 or baseline_months <= 0:
        return 0.0
    return works * pct / 100.0 / baseline_months


def _interp(table, x: float) -> float:
    """Linear interpolation on a (x, y) table, flat outside its ends."""
    if x <= table[0][0]:
        return table[0][1]
    if x >= table[-1][0]:
        return table[-1][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x0 <= x <= x1:
            span = (x1 - x0) or 1.0
            return y0 + (y1 - y0) * (x - x0) / span
    return table[-1][1]


def crew_efficiency(multiplier: float) -> float:
    """Output per head at this crew multiplier. 1.0 at the natural crew."""
    return _interp(CREW_EFFICIENCY, max(float(multiplier or 1.0), 1.0))


def wage_premium(multiplier: float) -> float:
    """Overtime and shift premium on the wage, as a fraction."""
    return _interp(WAGE_PREMIUM, max(float(multiplier or 1.0), 1.0))


def duration_days(quantity: float, output_per_day: float, crew: int) -> int:
    """work_days = quantity / (daily output x crew), never less than one day."""
    rate = float(output_per_day) * max(int(crew), 1)
    if rate <= 0 or quantity <= 0:
        return 1
    return max(1, int(math.ceil(quantity / rate)))


# ---------------------------------------------------------------- CPM
def _topo_order(acts: Dict[str, Activity]) -> List[str]:
    """Kahn's algorithm. A cycle is a hard error, not a warning: a circular dependency has
    no earliest start, and silently dropping an edge would produce a plausible-looking
    programme built on a relationship the planner did not intend."""
    indeg = {k: 0 for k in acts}
    succ: Dict[str, List[str]] = {k: [] for k in acts}
    for a in acts.values():
        for d in a.deps:
            if d.pred not in acts:
                raise ScheduleError("unknown_predecessor",
                                    f"Activity '{a.id}' depends on '{d.pred}', which does "
                                    "not exist in the programme.")
            indeg[a.id] += 1
            succ[d.pred].append(a.id)
    queue = sorted([k for k, v in indeg.items() if v == 0])
    order: List[str] = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for s in succ[n]:
            indeg[s] -= 1
            if indeg[s] == 0:
                queue.append(s)
        queue.sort()
    if len(order) != len(acts):
        stuck = sorted(set(acts) - set(order))
        raise ScheduleError("circular_dependency",
                            "The programme contains a circular dependency involving: "
                            + ", ".join(stuck[:6]) + ("..." if len(stuck) > 6 else ""),
                            activities=stuck[:20])
    return order


def _forward(acts, order, cal: WorkCalendar, start: date) -> None:
    for aid in order:
        a = acts[aid]
        es_cands = [start]
        ef_cands: List[date] = []
        for d in a.deps:
            p = acts[d.pred]
            lag = timedelta(days=float(d.lag_days))
            if d.kind == "SS":
                es_cands.append(p.es + lag)
            elif d.kind == "FF":
                ef_cands.append(p.ef + lag)
            else:  # FS -- successor may not start until the predecessor has finished
                es_cands.append(p.ef + lag)
        es = max(es_cands)
        # Remember WHICH relationship produced the earliest start. With calendars in play
        # float alone cannot identify the critical chain (see longest_path below), so the
        # driving link has to be captured while it is known.
        a.driver = None
        for d in a.deps:
            p_ = acts[d.pred]
            t = (p_.es if d.kind == "SS" else p_.ef) + timedelta(days=float(d.lag_days))
            if d.kind != "FF" and t == es:
                a.driver = d.pred
                break
        if a.milestone:
            a.es = cal.next_working(es)
            a.ef = a.es
            continue
        es = cal.next_working(es, a.exposed)
        ef = cal.add_work_days(es, a.work_days, a.exposed)
        for need in ef_cands:
            if need > ef:   # an FF link pushes the finish, so the start slides with it
                ef = need
                es = cal.sub_work_days(ef, a.work_days, a.exposed)
        a.es, a.ef = es, ef

        # A pinned date is a constraint, not a preference -- but it is checked against the
        # network before it is honoured. Three outcomes, in this order:
        #
        #   1. The pin is LATER than the earliest start. Honoured: this is how a user
        #      models a delay the generated plan cannot know about, and everything
        #      downstream moves with it, exactly as a real slip would.
        #   2. The pin is EARLIER than dependencies allow, and one of those dependencies
        #      is a code-mandated wait (curing, prop removal). REFUSED outright. IS 456
        #      lags outrank every user input; a pin that strikes props early is the one
        #      instruction this engine must never follow.
        #   3. The pin is EARLIER than dependencies allow for ordinary reasons. Refused
        #      too, but reported as a conflict naming the blocking predecessor and the
        #      shortfall, so the user can see what to change rather than wonder why their
        #      date vanished.
        if a.pinned_start and not a.milestone:
            want = a.pinned_start
            if want >= a.es:
                a.es = cal.next_working(want, a.exposed)
                a.ef = cal.add_work_days(a.es, a.work_days, a.exposed)
                a.pin_conflict = ""
            else:
                short = (a.es - want).days
                blocker = acts[a.driver].name if a.driver and a.driver in acts else "its predecessors"
                hard = next((d for d in a.deps if d.hard and d.pred == a.driver), None)
                if hard:
                    a.pin_conflict = (
                        f"{want.isoformat()} is {short} day{'s' if short != 1 else ''} too "
                        f"early and cannot be granted: {blocker} is followed by a "
                        f"code-mandated wait ({hard.reason or 'IS 456'}). That period is "
                        "curing or prop removal and no instruction shortens it.")
                else:
                    a.pin_conflict = (
                        f"{want.isoformat()} is {short} day{'s' if short != 1 else ''} "
                        f"earlier than {blocker} allows. Move that predecessor, or pin "
                        "this no earlier than " + a.es.isoformat() + ".")

        if a.pinned_finish and not a.milestone:
            want = a.pinned_finish
            if want >= a.ef:
                a.ef = cal.next_working(want, a.exposed)
                a.es = cal.sub_work_days(a.ef, a.work_days, a.exposed)
                if a.pinned_start and a.es < a.pinned_start:
                    a.es = a.pinned_start
            else:
                short = (a.ef - want).days
                a.pin_conflict = (
                    (a.pin_conflict + " ") if a.pin_conflict else "") + (
                    f"A finish of {want.isoformat()} is {short} day"
                    f"{'s' if short != 1 else ''} sooner than {a.work_days} working days "
                    "from the earliest possible start allows.")


def _latest_pred(cal: WorkCalendar, succ_time: date, lag: float, exposed: bool) -> date:
    """Invert the forward pass's working-day snapping.

    Forward computes ES = next_working(pred_time + lag). The backward pass has to invert
    exactly that, or the day a successor loses to a weekend is never given back and shows
    up as float that does not exist. Over a repeating floor cycle the error compounds --
    one phantom day per floor -- which would put float on genuinely critical work and send
    a planner to compress the wrong activity.

    The strict inverse is used deliberately. Because next_working() maps several days onto
    the same start, a "generous" inverse could hand a day back at EVERY link, and those
    days compound into float that does not survive contact with the forward pass -- an
    empirical delay test showed 8 days of reported float on activities where a single day
    of delay actually moved the completion date. Reporting float that is not there is the
    dangerous direction of error: it invites a planner to spend slack that does not exist,
    and on a construction programme spent slack becomes pressure on the site. The strict
    inverse can only ever understate float, never overstate it.
    """
    return succ_time - timedelta(days=float(lag))


def _backward(acts, order, cal: WorkCalendar, project_finish: date) -> None:
    succs: Dict[str, List[Tuple[Activity, Dep]]] = {k: [] for k in acts}
    for a in acts.values():
        for d in a.deps:
            succs[d.pred].append((a, d))
    for aid in reversed(order):
        a = acts[aid]
        lf_cands = [project_finish]
        ls_cands: List[date] = []
        for s, d in succs[aid]:
            if d.kind == "SS":
                ls_cands.append(_latest_pred(cal, s.ls, d.lag_days, s.exposed))
            elif d.kind == "FF":
                lf_cands.append(s.lf - timedelta(days=float(d.lag_days)))
            else:
                lf_cands.append(_latest_pred(cal, s.ls, d.lag_days, s.exposed))
        lf = min(lf_cands)
        if a.milestone:
            a.lf = lf
            a.ls = lf
            continue
        ls = cal.sub_work_days(lf, a.work_days, a.exposed)
        for need in ls_cands:
            if need < ls:
                ls = need
                lf = cal.add_work_days(ls, a.work_days, a.exposed)
        a.ls, a.lf = ls, lf


def _floats(acts, cal: WorkCalendar) -> None:
    succs: Dict[str, List[Tuple[Activity, Dep]]] = {k: [] for k in acts}
    for a in acts.values():
        for d in a.deps:
            succs[d.pred].append((a, d))
    for a in acts.values():
        # Measured on the FINISH side. The forward pass snaps a start forward to a working
        # day while the backward pass walks a finish back to one; comparing ES with LS
        # therefore counts that rounding twice and manufactures float. EF and LF are both
        # produced by the same working-day walk, so their difference is the honest slack.
        a.total_float = max(0, cal.work_days_between(a.ef, a.lf, a.exposed))
        # Free float: delay available before the EARLIEST successor is disturbed.
        gaps = []
        for s, d in succs[a.id]:
            lag = timedelta(days=float(d.lag_days))
            if d.kind == "SS":
                gaps.append(cal.work_days_between(a.es, s.es - lag, a.exposed))
            elif d.kind == "FF":
                gaps.append(cal.work_days_between(a.ef, s.ef - lag, a.exposed))
            else:
                gaps.append(cal.work_days_between(a.ef, s.es - lag, a.exposed))
        a.free_float = max(0, min(gaps)) if gaps else a.total_float
        a.critical = a.total_float <= 0


def longest_path(acts: Dict[str, Activity]) -> List[str]:
    """Criticality by LONGEST PATH rather than by zero total float.

    With a working calendar, float and criticality come apart. If a lag lands on a Sunday
    then finishing on Friday and finishing on Saturday both give the successor the same
    Monday start, so the predecessor picks up a day of real float while still driving the
    programme. Defining "critical" as total_float <= 0 therefore produces a chain with
    holes in it, and a planner compressing the wrong activity gains nothing.

    Primavera P6 exposes exactly this distinction as "Longest Path", and it is the correct
    definition here: walk back from the last-finishing activity through the relationship
    that actually drove each early start.
    """
    if not acts:
        return []
    last = max(acts.values(), key=lambda a: (a.ef, a.id))
    chain, seen, cur = [], set(), last.id
    while cur and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        cur = getattr(acts[cur], "driver", None)
    return list(reversed(chain))


# Ceiling on float verification, counted in activity visits (replays x activities). One
# visit costs roughly 20 us, so this holds verification near two and a half seconds on any
# size of job. Past it floats stay as the closed-form upper bound and the payload reports
# floats_verified: false rather than making the planner wait.
VERIFY_VISIT_BUDGET = 120_000


def _finish_with_delay(acts: Dict[str, Activity], order: List[str], cal: WorkCalendar,
                       start: date, aid: str, delay: int) -> date:
    """Project finish when ONE activity is pushed `delay` working days later.

    A cheap forward-only replay into local dicts, leaving the real activities untouched.
    """
    es: Dict[str, date] = {}
    ef: Dict[str, date] = {}
    for i in order:
        a = acts[i]
        cands = [start]
        ff: List[date] = []
        for d in a.deps:
            lag = timedelta(days=float(d.lag_days))
            if d.kind == "SS":
                cands.append(es[d.pred] + lag)
            elif d.kind == "FF":
                ff.append(ef[d.pred] + lag)
            else:
                cands.append(ef[d.pred] + lag)
        s = max(cands)
        if a.milestone:
            m = cal.next_working(s)
            if i == aid and delay:
                m = cal.next_working(cal.add_work_days(m, delay + 1) - timedelta(days=1))
            es[i] = ef[i] = m
            continue
        s = cal.next_working(s, a.exposed)
        if i == aid and delay:
            s = cal.add_work_days(s, delay + 1, a.exposed) - timedelta(days=1)
            s = cal.next_working(s, a.exposed)
        e = cal.add_work_days(s, a.work_days, a.exposed)
        for need in ff:
            if need > e:
                e = need
                s = cal.sub_work_days(e, a.work_days, a.exposed)
        es[i], ef[i] = s, e
    return max(ef.values())


def verify_floats(acts: Dict[str, Activity], order: List[str], cal: WorkCalendar,
                  start: date, baseline_finish: date, budget: int = VERIFY_VISIT_BUDGET) -> bool:
    """Replace each closed-form float with the delay the programme can actually absorb.

    A single backward pass over a working calendar computes float that does not survive
    contact with the forward pass: rounding a start up to a working day is not the inverse
    of walking a finish back to one, so the classical LS-ES value can claim slack that
    disappears the moment the delay is really taken. Measured against a live replay, an
    activity reporting eight days of float moved the completion date on the first day.

    Float is advisory, but a planner who believes in slack that is not there lets work
    drift and then compresses the floor cycle to recover -- which is precisely the pressure
    that gets props struck early. So every non-zero float is measured, by binary search on
    a real forward pass, and reported only if it holds. Returns False if the programme was
    too large to verify within `budget`, in which case floats stay as the closed-form
    upper bound and the payload says so.

    The budget counts ACTIVITY VISITS, not replays. Each replay is a forward pass over
    the whole programme, so its cost scales with the activity count -- a replay budget
    looks size-independent but is not, and on a four-tower job it quietly turned into
    well over a minute of work per re-plan.
    """
    candidates = [a for a in acts.values() if a.total_float > 0]
    replays = sum(max(1, math.ceil(math.log2(a.total_float + 1))) for a in candidates)
    if replays * max(len(acts), 1) > budget:
        return False
    for a in candidates:
        lo, hi = 0, a.total_float          # lo always achievable, hi the unverified claim
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _finish_with_delay(acts, order, cal, start, a.id, mid) <= baseline_finish:
                lo = mid
            else:
                hi = mid - 1
        a.total_float = lo
        a.free_float = min(a.free_float, lo)
    return True


def project_finish(activities: List[Activity], start: date, cal: WorkCalendar) -> date:
    """Completion date only: topological order and a forward pass, nothing else.

    The target solver calls this a dozen or more times per request. Float costs roughly
    four fifths of a full CPM run and the completion date does not depend on it, so the
    backward pass, the float calculation and the critical path are all skipped here.
    """
    acts = {a.id: a for a in activities}
    order = _topo_order(acts)
    _forward(acts, order, cal, start)
    return max(a.ef for a in acts.values())


def run_cpm(activities: List[Activity], start: date, cal: WorkCalendar,
            verify: bool = True) -> Dict[str, Any]:
    """Forward pass, backward pass, float and critical path."""
    if not activities:
        raise ScheduleError("no_activities", "There is nothing to schedule.")
    acts = {a.id: a for a in activities}
    if len(acts) != len(activities):
        raise ScheduleError("duplicate_activity", "Two activities share the same id.")
    order = _topo_order(acts)
    _forward(acts, order, cal, start)
    finish = max(a.ef for a in acts.values())
    _backward(acts, order, cal, finish)
    _floats(acts, cal)
    # Verification replays the forward pass per activity. The completion date does not
    # depend on it, so a headline-only caller skips it and gets the same duration far
    # faster; the floats it leaves behind are then unverified upper bounds.
    floats_verified = verify_floats(acts, order, cal, start, finish) if verify else False
    lp = set(longest_path(acts))
    for a in activities:
        a.critical = a.id in lp
    crit = [a.id for a in activities if a.critical]
    return {"order": order, "finish": finish, "critical_ids": crit,
            "longest_path": longest_path(acts), "floats_verified": floats_verified}


# ---------------------------------------------------------------- work breakdown
# Share of a floor's concrete that is vertical (columns/walls) rather than the slab. The
# split matters because the two are separate activities in the floor cycle and only the
# slab carries the prop-removal constraint.
COLUMN_CONCRETE_SHARE = 0.22
# Substructure concrete as a share of the whole job (excavation, PCC, footings, plinth).
SUBSTRUCTURE_CONCRETE_SHARE = 0.15
# Daily outputs not already covered by engine.LABOUR_TRADES, per crew, Indian norms.
OUTPUTS = {
    "excavation_m3": 120.0,      # hydraulic excavator
    "pcc_m3": 25.0,
    "formwork_m2": 14.0,         # shuttering carpenter
    "plaster_m2": 12.0,
    "backfill_m3": 60.0,
}
# Formwork area per m3 of slab concrete -- a 150 mm slab needs ~6.7 m2 of shuttering per m3.
FORMWORK_M2_PER_M3 = 6.7
# Ceiling on how much labour the target solver may add. Past roughly three times the
# planned gang the trades are queueing for the same slab and output per head falls, so a
# programme that only closes at 5x is not a plan, it is a wish. Overridable per project.
MAX_CREW_MULTIPLIER = 3.0
# Search resolution for the uniform multiplier. Crews are whole people and cfg.crew()
# rounds, so a finer step buys nothing but evaluations.
MULT_STEP = 0.05


@dataclass
class ScheduleConfig:
    start_date: str = ""
    target_finish: str = ""             # desired completion; solved for, never assumed
    work_week: Tuple[int, ...] = (0, 1, 2, 3, 4, 5)
    holidays: Tuple[str, ...] = ()
    monsoon_months: Tuple[int, ...] = (6, 7, 8)
    monsoon_blocks_exposed: bool = True
    crews: Dict[str, int] = field(default_factory=dict)
    crew_multipliers: Dict[str, float] = field(default_factory=dict)
    max_crew_multiplier: float = MAX_CREW_MULTIPLIER
    # Tasks the quantities cannot know about -- approvals, a compound wall, lift erection.
    # {name, phase, days, after, crew, cost}; `after` is an activity id, blank for
    # "from project start".
    extra_tasks: Tuple[Dict[str, Any], ...] = ()
    # Generated activity ids to drop. Successors are re-linked to the removed activity's
    # predecessors so the chain survives; anything a code-mandated lag hangs off is
    # refused (see apply_task_edits).
    excluded_tasks: Tuple[str, ...] = ()
    # Per-task user edits, keyed by activity id. Every field optional; absent means "use
    # the generated value", so the override layer starts empty and nothing renders blank.
    #   {name, work_days, crew, cost, start, finish, order}
    # The generator always runs first and these are applied on top of its output -- edit
    # only the crew and the days still recompute from the quantity, which is the arithmetic
    # that makes this a planning engine rather than a spreadsheet.
    task_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    blockwork_lag_floors: int = 2       # blockwork trails the structure by this many floors
    finishing_lag_floors: int = 2       # finishes trail blockwork likewise
    mobilisation_days: int = 10
    blended_cement: bool = False        # PPC/PSC -> longer minimum cure
    slab_span_m: float = 0.0            # 0 -> taken from the engineering grid
    allow_unsafe_striking: bool = False  # honoured, but always reported as critical
    # Populated by build_activities: for each crew key whose activities are actually
    # quantity-driven, the largest crew that key was given. Milestones and fixed-duration
    # work are left out because adding people to them changes nothing -- the solver would
    # only waste evaluations discovering that. The caller reads it to report headcount.
    seen_crews: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]):
        d = dict(d or {})
        cfg = cls()
        for k, v in d.items():
            if not hasattr(cfg, k) or v is None:
                continue
            cur = getattr(cfg, k)
            if isinstance(cur, tuple):
                v = tuple(v)
            elif isinstance(cur, bool):
                v = bool(v)
            elif isinstance(cur, int) and not isinstance(v, bool):
                v = int(v)
            elif isinstance(cur, float):
                v = float(v)
            setattr(cfg, k, v)
        return cfg

    def crew(self, trade: str, default: int = 1) -> int:
        base = max(1, int(self.crews.get(trade, default) or default))
        mult = float(self.crew_multipliers.get(trade, 1.0) or 1.0)
        return max(1, int(round(base * mult)))

    def to_dict(self):
        return {"start_date": self.start_date, "target_finish": self.target_finish,
                "work_week": list(self.work_week),
                "holidays": list(self.holidays), "monsoon_months": list(self.monsoon_months),
                "monsoon_blocks_exposed": self.monsoon_blocks_exposed, "crews": self.crews,
                "crew_multipliers": {k: round(v, 3) for k, v in self.crew_multipliers.items()},
                "max_crew_multiplier": self.max_crew_multiplier,
                "extra_tasks": [dict(t) for t in self.extra_tasks],
                "excluded_tasks": list(self.excluded_tasks),
                "task_overrides": {k: dict(v) for k, v in self.task_overrides.items()},
                "blockwork_lag_floors": self.blockwork_lag_floors,
                "finishing_lag_floors": self.finishing_lag_floors,
                "mobilisation_days": self.mobilisation_days,
                "blended_cement": self.blended_cement, "slab_span_m": self.slab_span_m,
                "allow_unsafe_striking": self.allow_unsafe_striking}


def _qty(analysis, key) -> float:
    for q in ((analysis.get("quantities") or {}).get("items") or []):
        if q.get("key") == key:
            return float(q.get("quantity") or 0)
    return 0.0


def _rate_of(analysis, key) -> Tuple[float, float]:
    """(unit rate, total amount) for a material from the BOQ, so activity cost and BOQ agree."""
    for m in ((analysis.get("boq") or {}).get("materials") or []):
        if m.get("key") == key:
            return float(m.get("rate") or 0), float(m.get("amount") or 0)
    return 0.0, 0.0


def build_activities(project: Dict[str, Any], analysis: Dict[str, Any],
                     cfg: ScheduleConfig) -> Tuple[List[Activity], List[Dict[str, str]]]:
    """Turn the computed quantities into a dependency-linked activity list."""
    warnings: List[Dict[str, str]] = []
    cfg.seen_crews = {}     # rebuilt every pass so the solver reads this run, not the last
    areas = analysis.get("areas") or {}
    towers = areas.get("towers") or []
    if not towers:
        raise ScheduleError("no_towers", "Add at least one tower before generating a programme.")
    total_builtup = float(areas.get("builtup_area_sqm") or 0)
    if total_builtup <= 0:
        raise ScheduleError("no_area", "The project has no built-up area to schedule.")

    eng = project.get("engineering") or {}
    span = cfg.slab_span_m or max(float(eng.get("grid_bay_x_m") or 0),
                                  float(eng.get("grid_bay_y_m") or 0)) or 4.0
    prop_days, prop_why = prop_removal_days(span, cfg.blended_cement)

    concrete_t, steel_t = _qty(analysis, "concrete"), _qty(analysis, "steel")
    bricks_t, tiles_t = _qty(analysis, "bricks"), _qty(analysis, "tiles")
    paint_t = _qty(analysis, "paint")
    fin_t = _qty(analysis, "finishing")
    plumb_t, elec_t = _qty(analysis, "plumbing_fixtures"), _qty(analysis, "electrical_points")
    r_conc = _rate_of(analysis, "concrete")[0]
    r_steel = _rate_of(analysis, "steel")[0]
    r_brick = _rate_of(analysis, "bricks")[0]
    r_tile = _rate_of(analysis, "tiles")[0]
    r_paint = _rate_of(analysis, "paint")[0]

    from calculation_basis import configured
    wage = {t[0]: float(configured(project.get("labour_rates") or {}, t[0], t[4]))
            for t in __import__("engine").LABOUR_TRADES}

    acts: List[Activity] = []

    def add(aid, name, phase, *, trade="", qty=0.0, unit="", out=0.0, crew_key=None,
            crew_default=1, exposed=False, mat_rate=0.0, deps=None, tower="", floor=None,
            milestone=False, fixed_days=None):
        key = crew_key or trade or aid
        crew = cfg.crew(key, crew_default)
        mult = float(cfg.crew_multipliers.get(key, 1.0) or 1.0)
        # Derated output, so a bigger crew no longer halves the duration cleanly. This is
        # what makes compression show up in the days, and therefore in the cost.
        eff = crew_efficiency(mult)
        wd = int(fixed_days) if fixed_days else duration_days(qty, out * eff, crew)
        if not milestone and not fixed_days and qty > 0 and out > 0:
            cfg.seen_crews[key] = max(cfg.seen_crews.get(key, 0), crew)
        base_wage = wage.get(crew_key or trade, 900.0)
        premium_rate = wage_premium(mult)
        labour_base = wd * crew * base_wage
        premium = labour_base * premium_rate
        labour_cost = labour_base + premium
        a = Activity(id=aid, name=name, phase=phase, trade=trade, quantity=qty, unit=unit,
                     output_per_day=out, crew=crew, work_days=wd, exposed=exposed,
                     cost=qty * mat_rate + (0 if milestone else labour_cost),
                     acceleration_premium=(0.0 if milestone else premium),
                     crew_multiplier=round(mult, 3), crew_efficiency=round(eff, 3),
                     tower=tower, floor=floor, deps=list(deps or []), milestone=milestone)
        acts.append(a)
        return a

    # ---- Phase 1: pre-construction -------------------------------------------------
    add("start", "Project start", "Pre-construction", milestone=True)
    add("mobilise", "Mobilisation, site office and hoarding", "Pre-construction",
        fixed_days=cfg.mobilisation_days, crew_key="mason", crew_default=4,
        deps=[Dep("start")])
    add("clearing", "Site clearing, levelling and setting out", "Pre-construction",
        qty=float(areas.get("ground_footprint_sqm") or 0) or 500.0, unit="m2", out=250.0,
        crew_key="mason", crew_default=3, exposed=True, deps=[Dep("mobilise")])

    sub_conc = concrete_t * SUBSTRUCTURE_CONCRETE_SHARE
    super_conc = concrete_t - sub_conc

    for ti, t in enumerate(towers):
        tid = f"t{ti}"
        tname = t.get("name") or f"Tower {ti + 1}"
        floors = max(int(t.get("floors") or 1), 1)
        plate = float(t.get("builtup_per_floor_sqm") or 0) or (
            float(t.get("builtup_sqm") or 0) / floors)
        tower_share = (float(t.get("builtup_sqm") or 0) / total_builtup) if total_builtup else 0
        foot = float(t.get("footprint_sqm") or plate)

        t_sub_conc = sub_conc * tower_share
        t_super_conc = super_conc * tower_share
        t_steel = steel_t * tower_share
        f_conc = t_super_conc / floors
        f_col_conc = f_conc * COLUMN_CONCRETE_SHARE
        f_slab_conc = f_conc - f_col_conc
        f_steel = t_steel / floors
        f_brick = (bricks_t * tower_share) / floors
        f_tile = (tiles_t * tower_share) / floors
        f_paint = (paint_t * tower_share) / floors
        f_plumb = (plumb_t * tower_share) / floors
        f_elec = (elec_t * tower_share) / floors

        # ---- Phase 2: substructure --------------------------------------------------
        add(f"{tid}_exc", f"{tname}: excavation", "Substructure", trade="excavation",
            qty=foot * 1.8, unit="m3", out=OUTPUTS["excavation_m3"], crew_key="concretor",
            crew_default=1, exposed=True, deps=[Dep("clearing")], tower=tname)
        add(f"{tid}_pcc", f"{tname}: PCC bed", "Substructure", trade="concretor",
            qty=t_sub_conc * 0.12, unit="m3", out=OUTPUTS["pcc_m3"], crew_key="concretor",
            crew_default=2, mat_rate=r_conc, deps=[Dep(f"{tid}_exc")], tower=tname)
        add(f"{tid}_fdn", f"{tname}: footings and pile caps", "Substructure",
            trade="concretor", qty=t_sub_conc * 0.58, unit="m3", out=3.0,
            crew_key="concretor", crew_default=4, mat_rate=r_conc,
            deps=[Dep(f"{tid}_pcc", lag_days=1.0, hard=True,
                      reason="IS 456 Cl. 11.3: PCC must set before footing steel is laid")],
            tower=tname)
        add(f"{tid}_plinth", f"{tname}: plinth beams and backfill", "Substructure",
            trade="concretor", qty=t_sub_conc * 0.30, unit="m3", out=3.0,
            crew_key="concretor", crew_default=3, mat_rate=r_conc,
            deps=[Dep(f"{tid}_fdn", lag_days=CURING_MIN_DAYS, hard=True,
                      reason=f"IS 456 Cl. 13.5: footings need {CURING_MIN_DAYS:.0f} days of curing")],
            tower=tname)

        # ---- Phase 3: superstructure floor cycle ------------------------------------
        # The cycle is Columns -> Slab formwork -> Slab steel -> Slab cast, and the next
        # floor's formwork may not begin until the props under the slab it will stand on
        # have served their full IS 456 period. That single link is what makes the
        # programme safe, so it is marked hard and is never compressed.
        for fl in range(1, floors + 1):
            prev = f"{tid}_slabcast_{fl - 1}"
            col_deps = ([Dep(f"{tid}_plinth", lag_days=CURING_MIN_DAYS, hard=True,
                             reason="IS 456 Cl. 13.5: plinth curing before columns rise")]
                        if fl == 1 else
                        [Dep(prev, lag_days=FORMWORK_IS456["vertical_sides"], hard=True,
                             reason="IS 456 Cl. 11.3: 24 h before striking vertical formwork")])
            add(f"{tid}_col_{fl}", f"{tname}: floor {fl} columns", "Superstructure",
                trade="concretor", qty=f_col_conc, unit="m3", out=2.5, crew_key="concretor",
                crew_default=4, mat_rate=r_conc, deps=col_deps, tower=tname, floor=fl)

            fw_deps = [Dep(f"{tid}_col_{fl}", lag_days=1.0, hard=True,
                           reason="IS 456 Cl. 11.3: column sides struck at 24 h")]
            if fl > 1:
                fw_deps.append(Dep(prev, lag_days=prop_days, hard=True, reason=prop_why))
            add(f"{tid}_fw_{fl}", f"{tname}: floor {fl} slab formwork", "Superstructure",
                trade="carpenter", qty=f_slab_conc * FORMWORK_M2_PER_M3, unit="m2",
                out=OUTPUTS["formwork_m2"], crew_key="carpenter", crew_default=6,
                deps=fw_deps, tower=tname, floor=fl)
            add(f"{tid}_rebar_{fl}", f"{tname}: floor {fl} slab reinforcement",
                "Superstructure", trade="bar_bender", qty=f_steel, unit="kg", out=350.0,
                crew_key="bar_bender", crew_default=6, mat_rate=r_steel,
                deps=[Dep(f"{tid}_fw_{fl}", "SS", lag_days=1.0)], tower=tname, floor=fl)
            add(f"{tid}_slabcast_{fl}", f"{tname}: floor {fl} slab concreting",
                "Superstructure", trade="concretor", qty=f_slab_conc, unit="m3", out=3.0,
                crew_key="concretor", crew_default=6, mat_rate=r_conc,
                deps=[Dep(f"{tid}_rebar_{fl}"), Dep(f"{tid}_fw_{fl}")],
                tower=tname, floor=fl)

        add(f"{tid}_topout", f"{tname}: structure topped out", "Superstructure",
            milestone=True, deps=[Dep(f"{tid}_slabcast_{floors}")], tower=tname)

        # ---- Phases 4-8: finishing, trailing the structure floor by floor ------------
        blag = max(1, int(cfg.blockwork_lag_floors))
        flag = max(1, int(cfg.finishing_lag_floors))
        for fl in range(1, floors + 1):
            gate = min(fl + blag, floors)   # structure must be this far ahead
            add(f"{tid}_block_{fl}", f"{tname}: floor {fl} blockwork", "Blockwork",
                trade="mason", qty=f_brick, unit="nos", out=500.0, crew_key="mason",
                crew_default=6, mat_rate=r_brick,
                deps=[Dep(f"{tid}_slabcast_{gate}", lag_days=prop_days, hard=True,
                          reason=prop_why + " -- blockwork must not load a freshly propped slab")],
                tower=tname, floor=fl)
            add(f"{tid}_mep_{fl}", f"{tname}: floor {fl} plumbing and electrical rough-in",
                "MEP", trade="plumber", qty=f_plumb + f_elec, unit="nos", out=6.0,
                crew_key="plumber", crew_default=3,
                deps=[Dep(f"{tid}_block_{fl}", "SS", lag_days=2.0)], tower=tname, floor=fl)
            add(f"{tid}_plaster_{fl}", f"{tname}: floor {fl} internal plaster", "Finishing",
                trade="mason", qty=plate * 2.6, unit="m2", out=OUTPUTS["plaster_m2"],
                crew_key="mason", crew_default=6,
                deps=[Dep(f"{tid}_block_{fl}", lag_days=CURING_MIN_DAYS, hard=True,
                          reason="IS 456 Cl. 13.5: blockwork must cure before plastering"),
                      Dep(f"{tid}_mep_{fl}")], tower=tname, floor=fl)
            add(f"{tid}_floor_{fl}", f"{tname}: floor {fl} flooring and tiling", "Finishing",
                trade="tiler", qty=f_tile, unit="m2", out=12.0, crew_key="tiler",
                crew_default=4, mat_rate=r_tile,
                deps=[Dep(f"{tid}_plaster_{fl}")], tower=tname, floor=fl)
            add(f"{tid}_paint_{fl}", f"{tname}: floor {fl} painting and finishes", "Finishing",
                trade="painter", qty=f_paint, unit="m2", out=35.0, crew_key="painter",
                crew_default=4, mat_rate=r_paint,
                deps=[Dep(f"{tid}_floor_{fl}")], tower=tname, floor=fl)

        add(f"{tid}_extplaster", f"{tname}: external plaster and painting", "Finishing",
            trade="painter", qty=foot * 0.9 * floors, unit="m2", out=OUTPUTS["plaster_m2"],
            crew_key="painter", crew_default=8, exposed=True,
            deps=[Dep(f"{tid}_topout", lag_days=CURING_MIN_DAYS, hard=True,
                      reason="IS 456 Cl. 13.5: curing before external finishing")],
            tower=tname)

    # ---- Phase 9: external development and handover ---------------------------------
    last_paints = [a.id for a in acts if a.id.endswith("_extplaster")]
    all_paint = [a.id for a in acts if "_paint_" in a.id]
    add("external", "External development, roads and landscaping", "External works",
        trade="mason", qty=float(areas.get("open_space_sqm") or 0) or 500.0, unit="m2",
        out=90.0, crew_key="mason", crew_default=8, exposed=True,
        deps=[Dep(i, "SS") for i in last_paints] or [Dep("clearing")])
    add("testing", "Testing, commissioning and snagging", "Handover", trade="plumber",
        qty=float(areas.get("total_units") or 1) * 2.0, unit="nos", out=6.0,
        crew_key="plumber", crew_default=4,
        deps=[Dep(i) for i in all_paint[-4:]] or [Dep("external")])
    add("handover", "Practical completion and handover", "Handover", milestone=True,
        deps=[Dep("testing"), Dep("external")])

    return acts, warnings


# ---------------------------------------------------------------- user task edits
def apply_task_edits(acts: List[Activity],
                     cfg: ScheduleConfig) -> Tuple[List[Activity], List[Dict[str, str]]]:
    """Apply the user's own additions and deletions to the generated activity list.

    The generated programme is complete for the work the quantities describe, but a real
    job has items no bill of quantities knows about -- a client approval, a compound wall,
    lift erection by a vendor. Those go in as `extra_tasks`. Anything genuinely not in
    scope comes out through `excluded_tasks`.

    Removal re-links: a successor of the removed activity inherits its predecessors, with
    the two lags added, so the chain keeps its shape and never loses a code-mandated wait.

    What removal will NOT do is take out an activity that a hard lag depends on. Deleting
    a slab pour would leave the strike-and-load lag hanging off whatever came before it,
    which is the same instruction as striking props early. Those requests are refused and
    reported instead of honoured.
    """
    warnings: List[Dict[str, str]] = []
    by_id = {a.id: a for a in acts}

    # An activity is fixed in place if any hard dependency points AT it.
    for a in acts:
        a.removable = not a.milestone
    for a in acts:
        for d in a.deps:
            if d.hard and d.pred in by_id:
                by_id[d.pred].removable = False

    drop: List[str] = []
    for aid in cfg.excluded_tasks:
        target = by_id.get(aid)
        if target is None:
            warnings.append({"severity": "info", "text":
                             f"'{aid}' is not in this programme, so there was nothing to remove."})
        elif not target.removable:
            warnings.append({"severity": "critical", "text": (
                f"'{target.name}' cannot be removed: a code-mandated wait is measured from "
                "it. Removing it would leave a curing or prop-removal period attached to "
                "the wrong activity, which is how props get struck early. It has been kept.")})
        else:
            drop.append(aid)

    if drop:
        dropped = set(drop)
        for a in acts:
            if a.id in dropped:
                continue
            merged: Dict[str, Dep] = {}
            queue = list(a.deps)
            while queue:
                d = queue.pop()
                if d.pred not in dropped:
                    keep = merged.get(d.pred)
                    # Two routes to the same predecessor: the later one governs.
                    if keep is None or d.lag_days > keep.lag_days:
                        merged[d.pred] = d
                    continue
                gone = by_id[d.pred]
                for up in gone.deps:
                    queue.append(Dep(up.pred, kind=d.kind, lag_days=d.lag_days + up.lag_days,
                                     hard=d.hard or up.hard,
                                     reason=up.reason or d.reason))
            a.deps = list(merged.values())
        acts = [a for a in acts if a.id not in dropped]
        by_id = {a.id: a for a in acts}
        warnings.append({"severity": "info", "text":
                         f"{len(dropped)} task(s) removed; their successors were re-linked."})

    for i, spec in enumerate(cfg.extra_tasks):
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        after = str(spec.get("after") or "").strip()
        if after and after not in by_id:
            warnings.append({"severity": "info", "text": (
                f"'{name}' was to follow a task that is not in the programme, so it "
                "starts with the project instead.")})
            after = ""
        days = max(1, int(spec.get("days") or 1))
        crew = max(1, int(spec.get("crew") or 1))
        order = spec.get("order")
        a = Activity(
            id=f"custom_{i}", name=name, phase=str(spec.get("phase") or "Pre-construction"),
            trade=str(spec.get("trade") or ""), work_days=days, crew=crew,
            cost=float(spec.get("cost") or 0), custom=True, removable=True,
            # A new task carries the position the user dropped it at. Without this it lands
            # wherever a date tie puts it -- which is why adding "Survey" to Pre-construction
            # appeared third, behind two other tasks that also start on day zero.
            order=int(order) if order is not None else None,
            deps=[Dep(after or "start")])
        acts.append(a)
        by_id[a.id] = a

    return acts, warnings


def apply_task_overrides(acts: List[Activity], cfg: ScheduleConfig) -> List[Dict[str, str]]:
    """Lay the user's per-task edits over the generated values.

    Precedence, applied here and reported to the user as a legend:

        a pinned date  >  an edited Days  >  an edited Crew  >  the generated figure

    Crew is the interesting one. Editing it alone recomputes Days from
    quantity / (output x crew) -- the arithmetic that makes this an engine rather than a
    spreadsheet. Editing Days as well means the user has asserted a duration, so Days
    wins and the crew change moves only labour cost.

    Dates are recorded as pins here, not applied: honouring one needs the whole network,
    so `_apply_pins` does that inside the CPM where the dependency times are known.
    """
    warnings: List[Dict[str, str]] = []
    overrides = cfg.task_overrides or {}
    if not overrides:
        return warnings

    by_id = {a.id: a for a in acts}
    for aid, ov in overrides.items():
        a = by_id.get(aid)
        if not a or not isinstance(ov, dict):
            continue

        if ov.get("name"):
            a.generated["name"] = a.name
            a.name = str(ov["name"])
            a.edited["name"] = True

        if ov.get("order") is not None:
            try:
                a.order = int(ov["order"])
            except (TypeError, ValueError):
                pass

        has_days = ov.get("work_days") not in (None, "")
        has_crew = ov.get("crew") not in (None, "")

        if has_crew:
            try:
                crew = max(1, int(ov["crew"]))
            except (TypeError, ValueError):
                crew = a.crew
            a.generated["crew"] = a.crew
            a.crew = crew
            a.edited["crew"] = True
            # Days recompute from the quantity unless the user also asserted a duration.
            if not has_days and a.quantity > 0 and a.output_per_day > 0 and not a.milestone:
                a.generated["work_days"] = a.work_days
                a.work_days = max(1, math.ceil(a.quantity / (a.output_per_day * crew)))
                a.edited["work_days_from_crew"] = True

        if has_days and not a.milestone:
            try:
                days = max(1, int(ov["work_days"]))
            except (TypeError, ValueError):
                days = a.work_days
            a.generated.setdefault("work_days", a.work_days)
            a.work_days = days
            a.edited["work_days"] = True
            a.edited.pop("work_days_from_crew", None)
            # The productivity the user just assumed, so they can see it.
            if a.quantity > 0 and a.crew > 0:
                a.edited["implied_output_per_day"] = round(
                    a.quantity / (days * a.crew), 2)

        if ov.get("cost") not in (None, ""):
            try:
                a.generated["cost"] = a.cost
                a.cost = float(ov["cost"])
                a.edited["cost"] = True
            except (TypeError, ValueError):
                pass

        for key, attr in (("start", "pinned_start"), ("finish", "pinned_finish")):
            raw = ov.get(key)
            if not raw:
                continue
            try:
                setattr(a, attr, date.fromisoformat(str(raw)))
                a.edited[key] = True
            except ValueError:
                warnings.append({"severity": "info", "text":
                                 f'"{raw}" is not a date the programme could read, so '
                                 f'{a.name} kept its calculated {key}.'})

    return warnings


# ---------------------------------------------------------------- safety audit
def audit_safety(acts: List[Activity], cfg: ScheduleConfig, span_m: float) -> List[Dict[str, Any]]:
    """Re-derive every code-mandated lag and clamp anything shorter.

    Runs even on lags this module generated itself. The generator and the checker are
    separate on purpose: a programme that is unsafe because of a later edit, an override or
    a future change to the builder must still be caught here rather than trusted upstream.
    """
    findings: List[Dict[str, Any]] = []
    required, why = prop_removal_days(span_m, cfg.blended_cement)
    by_id = {a.id: a for a in acts}
    for a in acts:
        for d in a.deps:
            if not d.hard:
                continue
            p = by_id.get(d.pred)
            minimum = 0.0
            if p is not None and "slabcast" in d.pred and ("_fw_" in a.id or "_block_" in a.id):
                minimum = required
            elif "curing" in (d.reason or "").lower() or "Cl. 13.5" in (d.reason or ""):
                minimum = CURING_MIN_DAYS_BLENDED if cfg.blended_cement else CURING_MIN_DAYS
            if minimum and d.lag_days < minimum - 1e-9:
                findings.append({
                    "severity": "critical", "activity": a.id, "predecessor": d.pred,
                    "given_days": d.lag_days, "required_days": minimum,
                    "text": (f"'{a.name}' was scheduled to follow '{p.name if p else d.pred}' "
                             f"after {d.lag_days:g} days, but {minimum:g} calendar days are "
                             f"the code minimum. {why}. Striking or loading early risks "
                             "collapse; the lag has been increased to the code minimum."),
                })
                if not cfg.allow_unsafe_striking:
                    d.lag_days = minimum
    return findings


def audit_scheduled_dates(acts: List[Activity], cfg: ScheduleConfig,
                          span_m: float) -> List[Dict[str, Any]]:
    """Second safety pass, on the DATES the CPM produced rather than the declared lags.

    `audit_safety` checks that every code-mandated lag is long enough. That is the right
    check for a generated programme, where dates follow from lags -- but a user-pinned date
    is applied to the dates directly, so a lag can be correct while the realised gap is
    not. `_forward` refuses such a pin, and this exists so that a future path which
    forgets to cannot ship an unsafe programme silently.

    Belt and braces on purpose: this is the one class of error in the module where being
    wrong means props come out early.
    """
    findings: List[Dict[str, Any]] = []
    required, why = prop_removal_days(span_m, cfg.blended_cement)
    by_id = {a.id: a for a in acts}
    for a in acts:
        if a.es is None or a.milestone:
            continue
        for d in a.deps:
            if not d.hard:
                continue
            p = by_id.get(d.pred)
            if p is None or p.ef is None:
                continue
            minimum = 0.0
            if "slabcast" in d.pred and ("_fw_" in a.id or "_block_" in a.id):
                minimum = required
            elif "curing" in (d.reason or "").lower() or "Cl. 13.5" in (d.reason or ""):
                minimum = CURING_MIN_DAYS_BLENDED if cfg.blended_cement else CURING_MIN_DAYS
            if not minimum:
                continue
            actual = (a.es - p.ef).days
            if actual < minimum - 1e-9:
                findings.append({
                    "severity": "critical", "activity": a.id, "predecessor": d.pred,
                    "given_days": actual, "required_days": minimum,
                    "text": (f"'{a.name}' is scheduled to start {actual:g} days after "
                             f"'{p.name}' finishes, but {minimum:g} calendar days are the "
                             f"code minimum. {why}. This is a date the programme was told "
                             "to use, not one it calculated -- clear the pinned date on "
                             "this task."),
                })
    return findings


# ---------------------------------------------------------------- target date solving
def solve_for_target(project: Dict[str, Any], analysis: Dict[str, Any], cfg: ScheduleConfig,
                     start: date, cal: WorkCalendar, target: date) -> Dict[str, Any]:
    """Find the smallest crew increase that finishes the job by `target`.

    Durations here are quantity / (output x crew), so the only honest lever on a
    completion date is labour. This walks that lever and reports what it costs.

    Two rules shape the answer:

    1. It only ever ADDS people. If the programme already beats the target the caller's
       crew sizes are left exactly as typed -- silently shrinking a crew someone entered
       is a surprising answer to the question "when must this finish".
    2. It cannot beat the code minimums. Prop removal and curing are calendar-day lags
       that no crew size touches (see FORMWORK_IS456), so past a point the date stops
       moving however many people are added. When the target sits below that floor this
       returns `unreachable` with the earliest date that IS safely achievable, rather
       than a plan that quietly asks a site to strike props early.

    Returns a report dict plus the multipliers to apply, under `_multipliers`.
    """
    evals = 0
    saved = cfg.crew_multipliers

    def finish_with(mults: Dict[str, float]) -> date:
        nonlocal evals
        evals += 1
        cfg.crew_multipliers = mults
        acts, _ = build_activities(project, analysis, cfg)
        acts, _ = apply_task_edits(acts, cfg)   # the user's own tasks count toward the date
        apply_task_overrides(acts, cfg)         # and so do their edits to the durations
        return project_finish(acts, start, cal)

    try:
        if target <= start:
            return {"status": "invalid", "evaluations": 0, "_multipliers": saved,
                    "note": "The target completion date is on or before the start date."}

        base_finish = finish_with({}) - timedelta(days=1)
        # Whatever crew keys this project's activities asked for are what may be tuned.
        trades = sorted(cfg.seen_crews)
        base_crews = dict(cfg.seen_crews)
        ceiling = max(1.0, float(cfg.max_crew_multiplier or MAX_CREW_MULTIPLIER))

        if base_finish <= target:
            return {
                "status": "already_met", "evaluations": evals, "_multipliers": saved,
                "days_early": (target - base_finish).days, "crews": base_crews,
                "note": ("The programme already finishes inside the target date with the "
                         "crews as entered, so nothing was changed."),
            }

        fastest = finish_with({t: ceiling for t in trades}) - timedelta(days=1)
        if fastest > target:
            return {
                "status": "unreachable", "evaluations": evals, "_multipliers": saved,
                "earliest_possible_finish": fastest.isoformat(),
                "days_short": (fastest - target).days, "crews": base_crews,
                "note": ("Even at the labour ceiling of "
                         f"{ceiling:g}x the target cannot be met. What is left is "
                         "calendar time, not work: prop removal and curing are fixed by "
                         "IS 456 and no crew size shortens them. Moving the date needs a "
                         "later completion, a second tower crane and pour front, or a "
                         "structural change that shortens the floor cycle."),
            }

        # A uniform staff-up is feasible but wasteful: it raises every crew on the site,
        # including trades whose work has float and never drives the completion date.
        # Instead, measure each trade on its own at the labour ceiling, then add the most
        # effective of them only until the target is met. A trade that cannot move the
        # date on its own is never staffed up.
        alone: List[Tuple[date, str]] = []
        if ceiling > 1.0:
            for t in trades:
                alone.append((finish_with({t: ceiling}) - timedelta(days=1), t))
            alone.sort()

        mults: Dict[str, float] = {}
        for _, t in alone:
            mults[t] = ceiling
            if finish_with(mults) - timedelta(days=1) <= target:
                break

        # Give back what the date can spare. With every other trade held where it is,
        # bisect each raised trade down to the smallest multiplier that still lands on
        # target -- a trade that can go all the way back to its entered crew is dropped.
        for t in sorted(mults):
            lo_t, hi_t = 1.0, mults[t]
            for _ in range(3):
                mid = (lo_t + hi_t) / 2
                if finish_with(dict(mults, **{t: mid})) - timedelta(days=1) <= target:
                    hi_t = mid
                else:
                    lo_t = mid
            mults[t] = hi_t
        mults = {t: m for t, m in mults.items() if m > 1.0 + 1e-6}

        achieved = finish_with(mults) - timedelta(days=1)
        added = {t: {"from": base_crews.get(t, 0), "to": cfg.seen_crews.get(t, 0),
                     "multiplier": round(mults[t], 2)}
                 for t in sorted(mults) if cfg.seen_crews.get(t, 0) > base_crews.get(t, 0)}
        return {
            "status": "met_with_more_labour", "evaluations": evals, "_multipliers": mults,
            "achieved_finish": achieved.isoformat(),
            "days_early": (target - achieved).days,
            "days_saved": (base_finish - achieved).days,
            "baseline_finish": base_finish.isoformat(),
            "crews": dict(cfg.seen_crews), "crew_changes": added,
            "note": ("Crews were raised only on the trades that actually drive the date; "
                     "the rest are left as entered."),
        }
    finally:
        cfg.crew_multipliers = saved


# ---------------------------------------------------------------- derived views
def _spread(a: Activity, cal: WorkCalendar) -> List[Tuple[date, float]]:
    """Split an activity's cost evenly over the working days it actually occupies."""
    if a.milestone or not a.es or a.ef <= a.es:
        return []
    days = [d for d in _daterange(a.es, a.ef) if cal.is_working(d, a.exposed)]
    if not days:
        return []
    per = a.cost / len(days)
    return [(d, per) for d in days]


def _daterange(a: date, b: date):
    d = a
    while d < b:
        yield d
        d += timedelta(days=1)


def _week(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def cash_flow(acts: List[Activity], cal: WorkCalendar) -> List[Dict[str, Any]]:
    """Weekly and cumulative spend -- the S-curve a lender asks for."""
    buckets: Dict[str, float] = {}
    for a in acts:
        for d, amt in _spread(a, cal):
            buckets[_week(d)] = buckets.get(_week(d), 0.0) + amt
    out, run = [], 0.0
    for wk in sorted(buckets):
        run += buckets[wk]
        out.append({"week": wk, "amount": round(buckets[wk], 2), "cumulative": round(run, 2)})
    return out


def resource_histogram(acts: List[Activity], cal: WorkCalendar) -> List[Dict[str, Any]]:
    """Peak head-count per trade per week -- what resource levelling would flatten."""
    weeks: Dict[str, Dict[str, int]] = {}
    for a in acts:
        if a.milestone or not a.trade or not a.es:
            continue
        for d in _daterange(a.es, a.ef):
            if not cal.is_working(d, a.exposed):
                continue
            w = weeks.setdefault(_week(d), {})
            w[a.trade] = max(w.get(a.trade, 0), 0) + a.crew
    out = []
    for wk in sorted(weeks):
        trades = weeks[wk]
        out.append({"week": wk, "total": sum(trades.values()),
                    "trades": {k: v for k, v in sorted(trades.items())}})
    return out


def line_of_balance(acts: List[Activity]) -> List[Dict[str, Any]]:
    """Floor-versus-time bands per trade.

    A Gantt with 12 near-identical floor cycles is unreadable and hides the thing that
    actually matters in repetitive high-rise work: whether two trades are converging on the
    same floor. Line of Balance shows exactly that, which is why it was invented for
    repetitive construction.
    """
    lines: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for a in acts:
        if a.floor is None or a.milestone or not a.es:
            continue
        lines.setdefault((a.tower, a.phase), []).append({
            "floor": a.floor, "activity": a.name, "trade": a.trade,
            "start": a.es.isoformat(), "finish": (a.ef - timedelta(days=1)).isoformat(),
            "critical": a.critical,
        })
    out = []
    for (tower, phase), pts in sorted(lines.items()):
        out.append({"tower": tower, "phase": phase,
                    "points": sorted(pts, key=lambda p: p["floor"])})
    return out


# ---------------------------------------------------------------- entry point
def plan_schedule(project: Dict[str, Any], analysis: Dict[str, Any],
                  overrides: Optional[Dict[str, Any]] = None,
                  summary: bool = False) -> Dict[str, Any]:
    """Project + computed quantities -> full programme. Never raises for user error.

    `summary` returns the headline figures only -- duration, dates, floor cycle, phase
    spans -- skipping float verification and the activity, cash-flow, resource and
    line-of-balance payloads. It exists so a metric strip on every screen can show the
    build duration without shipping a 120-activity programme each time.
    """
    try:
        cfg = ScheduleConfig.from_dict(overrides)
        start = (date.fromisoformat(cfg.start_date[:10]) if cfg.start_date else date.today())
        cal = WorkCalendar(cfg.work_week, cfg.holidays, cfg.monsoon_months,
                           cfg.monsoon_blocks_exposed)

        # A target completion date resources the job rather than describing it: solve for
        # the crews first, then plan once with the answer. Skipped for summary callers,
        # who want the headline figures cheaply and re-plan properly when asked.
        target_report = None
        if cfg.target_finish:
            target = date.fromisoformat(cfg.target_finish[:10])
            target_report = solve_for_target(project, analysis, cfg, start, cal, target)
            cfg.crew_multipliers = target_report.pop("_multipliers", {}) or {}
            target_report["requested_finish"] = target.isoformat()

        acts, warnings = build_activities(project, analysis, cfg)
        acts, edit_warnings = apply_task_edits(acts, cfg)
        warnings += edit_warnings
        # The generator has run; now the user's edits go on top of its output.
        warnings += apply_task_overrides(acts, cfg)

        eng = project.get("engineering") or {}
        span = cfg.slab_span_m or max(float(eng.get("grid_bay_x_m") or 0),
                                      float(eng.get("grid_bay_y_m") or 0)) or 4.0
        safety = audit_safety(acts, cfg, span)

        # The uncompressed duration, used as the reference the time/cost curve is measured
        # against. Computed with a forward pass only, so it costs about a millisecond.
        # Always computed, never only when multipliers exist. The monthly preliminaries
        # rate is derived by dividing the engine's percentage by THIS duration, so if it
        # were the actual duration instead the rate would self-cancel and preliminaries
        # would come out identical at every programme length -- which is exactly the bug
        # this whole fix exists to remove, reintroduced one level up.
        natural_months = 0.0
        try:
            plain = ScheduleConfig.from_dict({**cfg.to_dict(), "crew_multipliers": {},
                                              "target_finish": ""})
            plain_acts, _ = build_activities(project, analysis, plain)
            plain_acts, _ = apply_task_edits(plain_acts, plain)
            natural_finish = project_finish(plain_acts, start, cal)
            natural_months = ((natural_finish - start).days + 1) / 30.44
        except Exception:
            natural_months = 0.0

        cpm = run_cpm(acts, start, cal, verify=not summary)
        # Dates exist now, so the realised gaps can be checked as well as the lags.
        safety += audit_scheduled_dates(acts, cfg, span)
        finish = cpm["finish"] - timedelta(days=1)
        prop_days, prop_why = prop_removal_days(span, cfg.blended_cement)

        cycle = [a for a in acts if "_fw_" in a.id]
        floor_cycle = None
        if len(cycle) >= 2:
            c = sorted(cycle, key=lambda x: (x.tower, x.floor or 0))
            same = [x for x in c if x.tower == c[0].tower]
            if len(same) >= 2:
                floor_cycle = (same[1].es - same[0].es).days

        # Phase spans give a readable timeline without the full activity list.
        phases: Dict[str, Dict[str, Any]] = {}
        for a in acts:
            if a.milestone or not a.es:
                continue
            ph = phases.setdefault(a.phase, {"phase": a.phase, "start": a.es, "finish": a.ef,
                                             "activities": 0, "cost": 0.0})
            ph["start"] = min(ph["start"], a.es)
            ph["finish"] = max(ph["finish"], a.ef)
            ph["activities"] += 1
            ph["cost"] += a.cost
        phase_rows = sorted(phases.values(), key=lambda r: r["start"])
        for r in phase_rows:
            r["start"] = r["start"].isoformat()
            fin = r["finish"] - timedelta(days=1)
            r["calendar_days"] = (fin - date.fromisoformat(r["start"])).days + 1
            r["finish"] = fin.isoformat()
            r["cost"] = round(r["cost"], 2)

        scheduled_cost = sum(a.cost for a in acts)
        boq_total = float((analysis.get("boq") or {}).get("grand_total") or 0)

        # ---- time / cost curve --------------------------------------------------------
        # Three things move with programme length, and they move in opposite directions,
        # which is the point: compressing costs an overtime premium and loses output per
        # head to congestion; extending costs time-related preliminaries. Cost is therefore
        # lowest near the duration the quantities imply and rises either side of it.
        # A committed finish date later than the work needs does not make the work cheaper:
        # the site establishment, supervision and plant stay on hire until the project is
        # handed over. So preliminaries run to whichever is later, the date the work
        # finishes or the date the user has committed to. Without this the model says a
        # slower programme is free, which is the mirror of the bug that said a faster one
        # was.
        billed_finish = finish
        if cfg.target_finish:
            try:
                wanted = date.fromisoformat(cfg.target_finish)
                billed_finish = max(finish, wanted)
            except ValueError:
                pass
        months = max(((billed_finish - start).days + 1) / 30.44, 0.0)
        premium_total = sum(a.acceleration_premium for a in acts)
        # What congestion cost: the share of each derated task's labour that the lost
        # output per head accounts for.
        lost_productivity = sum(
            (a.cost - a.acceleration_premium) * (1.0 - a.crew_efficiency)
            for a in acts if 0 < a.crew_efficiency < 1.0)
        # Preliminaries are priced per month off the UNCOMPRESSED programme, so the monthly
        # rate does not itself move when the user changes the target date -- only the number
        # of months it is charged over does.
        prelim_month = preliminaries_per_month(project, analysis, natural_months or months)
        prelim_total = prelim_month * months
        time_cost = {
            "scheduled_cost": round(scheduled_cost, 2),
            "acceleration_premium": round(premium_total, 2),
            "lost_productivity": round(lost_productivity, 2),
            "preliminaries_per_month": round(prelim_month, 2),
            "preliminaries_total": round(prelim_total, 2),
            "duration_months": round(months, 2),
            "natural_months": round(natural_months or months, 2),
            "billed_to": billed_finish.isoformat(),
            "extended_days": max((billed_finish - finish).days, 0),
            "total_cost": round(scheduled_cost + prelim_total, 2),
            "note": ("Cost is lowest near the duration the quantities imply. Compressing "
                     "adds an overtime premium and loses output per head to congestion; "
                     "extending adds time-related preliminaries. Both directions cost."),
        }

        # ---- project budget ------------------------------------------------------------
        # The headline cost was the BOQ total and nothing else, so it never moved when the
        # programme changed: compressing the finish date, extending it, or adding a task
        # all left it at the same figure. The BOQ prices the MATERIALS AND WORK; these are
        # the costs that come from HOW and WHEN it is built, which the BOQ cannot know.
        added_tasks_cost = sum(a.cost for a in acts if a.custom)
        budget = {
            "boq_total": round(boq_total, 2),
            "acceleration_premium": round(premium_total, 2),
            "lost_productivity": round(lost_productivity, 2),
            "preliminaries": round(prelim_total, 2),
            "added_tasks": round(added_tasks_cost, 2),
            "programme_adjustment": round(
                premium_total + lost_productivity + prelim_total + added_tasks_cost, 2),
            "project_total": round(
                boq_total + premium_total + lost_productivity + prelim_total
                + added_tasks_cost, 2),
            "note": ("The BOQ prices the work. These add what the programme costs: overtime "
                     "and lost output when it is compressed, site running cost over its "
                     "length, and any task added by hand."),
        }

        return {
            "ok": True,
            "budget": budget,
            "config": cfg.to_dict(),
            "start": start.isoformat(),
            "finish": finish.isoformat(),
            "duration_calendar_days": (finish - start).days + 1,
            "duration_months": round(((finish - start).days + 1) / 30.44, 1),
            "activity_count": len(acts),
            "target": target_report,
            "crews": dict(cfg.seen_crews),
            "floats_verified": cpm["floats_verified"],
            "critical_count": len(cpm["critical_ids"]),
            "phases": phase_rows,
            "activities": [] if summary else [a.to_dict() for a in acts],
            "critical_path": [] if summary else cpm["critical_ids"],
            "cash_flow": [] if summary else cash_flow(acts, cal),
            "resources": [] if summary else resource_histogram(acts, cal),
            "line_of_balance": [] if summary else line_of_balance(acts),
            "summary_only": summary,
            "safety": {
                "slab_span_m": round(span, 2),
                "prop_removal_days": prop_days,
                "governing_rule": prop_why,
                "min_curing_days": CURING_MIN_DAYS_BLENDED if cfg.blended_cement else CURING_MIN_DAYS,
                "floor_cycle_days": floor_cycle,
                "findings": safety,
                "enforced": not cfg.allow_unsafe_striking,
            },
            "time_cost": time_cost,
            "cost_check": {
                "scheduled_cost": round(scheduled_cost, 2),
                "boq_grand_total": round(boq_total, 2),
                "variance_pct": round((scheduled_cost - boq_total) / boq_total * 100, 1) if boq_total else None,
                "note": ("The programme prices only the activities it models, so it will not "
                         "match the BOQ exactly. A large gap means work is missing from the "
                         "programme, not that the BOQ is wrong."),
            },
            "warnings": warnings,
        }
    except ScheduleError as exc:
        return exc.to_dict()
