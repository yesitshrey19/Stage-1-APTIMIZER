"""STAGE 3b — genetic refinement of tower placement.

Greedy packing (stage 3a) lays towers on a regular grid at one rotation per region. That is
a good seed and a poor optimum: the grid cannot slide one tower two metres north to clear a
spacing shortfall, cannot rotate a single block against the others, and cannot trade a
storey for a footprint. This stage does exactly those things, and nothing else.

Three properties this module guarantees, in order of importance.

  1. It never returns a layout worse than its seed. The seed is carried as an elite from
     the first generation to the last and the result is compared against it before being
     returned, so refinement is a one-way ratchet. `fitness.py`'s own docstring names the
     failure this prevents -- a GA scoring differently from greedy can "improve" a layout
     into something objectively worse -- and the defence is not merely that both call
     `evaluate`, it is that the seed's score is a floor on the answer.

  2. It is deterministic. The RNG is seeded from the layout itself, so the same project
     refines to the same towers on every run and on every machine. A site plan that moved
     when nothing changed would be impossible to review, and impossible to sign.

  3. It stops on a clock, not only on a generation count. `time_budget_s` bounds the work
     regardless of population or plot size, because this runs inside a request.

Every candidate is scored through `fitness.evaluate`, so refinement and greedy rank layouts
by the same objective. Containment, overlap and reserved circulation are hard there and
score -inf, so an infeasible mutation cannot survive selection.

One constraint is tightened rather than inherited: inter-tower spacing. `evaluate` prices
it as a soft penalty, which is correct for ranking two lawful layouts and wrong as the only
gate on a search, because a finite penalty means a large enough floor-area gain simply buys
a shortfall. Greedy never makes that trade -- `pack._enforce_spacing` drops towers outright
-- so leaving it soft here would let refinement hand back a layout greedy had already
rejected as unlawful. See `_spacing_ok`.
"""
import math
import random
import time
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shapely.affinity import rotate, translate
from shapely.geometry import box

from .config import SiteLayoutConfig
from .fitness import (FitnessResult, PackContext, TowerPlacement, evaluate,
                      required_spacing)

__all__ = ["refine", "GAReport"]


def _spacing_ok(towers: Sequence[TowerPlacement], cfg: SiteLayoutConfig) -> bool:
    """Every pair clears its light-and-ventilation gap.

    `fitness.evaluate` prices spacing as a soft penalty, which is right for ranking two
    lawful layouts. It is wrong as the only gate on a search: the penalty is finite, so a
    big enough floor-area gain buys a shortfall, and the GA will take that trade every
    time. Greedy never does -- `pack._enforce_spacing` drops towers outright -- so scoring
    alone would let refinement return something greedy had already rejected as unlawful.

    Spacing is a requirement, not a preference. Refinement searches only the space greedy
    was willing to occupy, and the seed is already inside it, so the ratchet still holds.
    """
    for i in range(len(towers)):
        for j in range(i + 1, len(towers)):
            need = required_spacing(towers[i], towers[j], cfg)
            if towers[i].polygon.distance(towers[j].polygon) < need - 1e-6:
                return False
    return True


def _score(towers: Sequence[TowerPlacement], ctx: PackContext) -> FitnessResult:
    """`evaluate`, with the spacing requirement applied as a hard constraint."""
    fit = evaluate(towers, ctx)
    if fit.feasible and not _spacing_ok(towers, ctx.cfg):
        return FitnessResult(False, float("-inf"),
                             ["inter-tower spacing below the light-and-ventilation minimum"],
                             {}, fit.metrics)
    return fit


class GAReport(dict):
    """Plain dict so it round-trips through the API; a class for the docstring only."""


# ---------------------------------------------------------------- genome helpers
def _rebuild(t: TowerPlacement, *, cx=None, cy=None, width=None, depth=None,
             rotation_deg=None, floors=None) -> TowerPlacement:
    """A tower with some genes changed and its polygon rebuilt to match.

    The polygon is derived, never edited: every mutation goes through here so a placement
    can never carry geometry that disagrees with its own centre, size or rotation.
    """
    cx = t.cx if cx is None else cx
    cy = t.cy if cy is None else cy
    width = t.width if width is None else width
    depth = t.depth if depth is None else depth
    rot = t.rotation_deg if rotation_deg is None else rotation_deg
    floors = t.floors if floors is None else floors

    poly = box(-width / 2.0, -depth / 2.0, width / 2.0, depth / 2.0)
    poly = rotate(poly, rot, origin=(0, 0))
    poly = translate(poly, cx, cy)
    return replace(t, polygon=poly, cx=cx, cy=cy, width=width, depth=depth,
                   rotation_deg=rot, floors=int(floors))


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _aspect_ok(width: float, depth: float, cfg: SiteLayoutConfig) -> bool:
    lo, hi = min(width, depth), max(width, depth)
    if lo <= 0:
        return False
    ratio = hi / lo
    return cfg.towers.min_aspect <= ratio <= cfg.towers.max_aspect


def _footprint_ok(width: float, depth: float, cfg: SiteLayoutConfig) -> bool:
    area = width * depth
    return (cfg.towers.min_footprint <= area <= cfg.towers.max_footprint
            and _aspect_ok(width, depth, cfg))


# ---------------------------------------------------------------- mutation operators
def _mutate_nudge(t: TowerPlacement, rng: random.Random, step: float) -> TowerPlacement:
    """Slide a tower. The operator the grid cannot express, and the one that pays."""
    return _rebuild(t, cx=t.cx + rng.gauss(0.0, step), cy=t.cy + rng.gauss(0.0, step))


def _mutate_rotate(t: TowerPlacement, rng: random.Random, cfg: SiteLayoutConfig) -> TowerPlacement:
    """Turn one block independently of its neighbours."""
    return _rebuild(t, rotation_deg=(t.rotation_deg + rng.gauss(0.0, 12.0)) % 180.0)


def _mutate_resize(t: TowerPlacement, rng: random.Random, cfg: SiteLayoutConfig) -> TowerPlacement:
    """Scale the plate, keeping it inside the configured footprint and aspect window."""
    fw = 1.0 + rng.gauss(0.0, 0.06)
    fd = 1.0 + rng.gauss(0.0, 0.06)
    w = _clamp(t.width * fw, 1.0, 1e4)
    d = _clamp(t.depth * fd, 1.0, 1e4)
    if not _footprint_ok(w, d, cfg):
        return t
    return _rebuild(t, width=w, depth=d)


def _mutate_storeys(t: TowerPlacement, rng: random.Random, cfg: SiteLayoutConfig) -> TowerPlacement:
    """Trade height against the spacing and FAR penalties, within the configured band."""
    floors = int(_clamp(t.floors + rng.choice((-2, -1, 1, 2)),
                        cfg.towers.floors_min, cfg.towers.floors_max))
    return _rebuild(t, floors=floors)


def _mutate_drop(towers: List[TowerPlacement], rng: random.Random) -> List[TowerPlacement]:
    """Remove a tower. Sometimes the best layout is the one with fewer, taller blocks --
    a crowded plate loses more to the spacing penalty than the extra floor area wins."""
    if len(towers) <= 1:
        return towers
    out = list(towers)
    out.pop(rng.randrange(len(out)))
    return out


_OPERATORS = (
    (0.40, "nudge"),
    (0.20, "rotate"),
    (0.15, "resize"),
    (0.15, "storeys"),
    (0.10, "drop"),
)


def _mutate(towers: Sequence[TowerPlacement], rng: random.Random,
            cfg: SiteLayoutConfig, step: float) -> List[TowerPlacement]:
    """One mutation per call, on one tower, chosen by the weights above."""
    out = list(towers)
    if not out:
        return out

    roll = rng.random()
    cum = 0.0
    op = "nudge"
    for weight, name in _OPERATORS:
        cum += weight
        if roll <= cum:
            op = name
            break

    if op == "drop":
        return _mutate_drop(out, rng)

    i = rng.randrange(len(out))
    t = out[i]
    if op == "nudge":
        out[i] = _mutate_nudge(t, rng, step)
    elif op == "rotate":
        out[i] = _mutate_rotate(t, rng, cfg)
    elif op == "resize":
        out[i] = _mutate_resize(t, rng, cfg)
    else:
        out[i] = _mutate_storeys(t, rng, cfg)
    return out


def _crossover(a: Sequence[TowerPlacement], b: Sequence[TowerPlacement],
               rng: random.Random) -> List[TowerPlacement]:
    """Spatial split: towers west of a cut from one parent, east of it from the other.

    Splitting on a coordinate rather than on list index keeps each parent's local
    arrangement intact. Index crossover would interleave two grids into a set of towers
    that overlap everywhere, and every child would score -inf.
    """
    if not a or not b:
        return list(a or b)
    xs = [t.cx for t in a] + [t.cx for t in b]
    cut = rng.uniform(min(xs), max(xs)) if len(set(xs)) > 1 else xs[0]
    child = [t for t in a if t.cx <= cut] + [t for t in b if t.cx > cut]
    return child or list(a)


# ---------------------------------------------------------------- the loop
def refine(seed: Sequence[TowerPlacement], ctx: PackContext, *,
           population: Optional[int] = None, generations: Optional[int] = None,
           time_budget_s: Optional[float] = None,
           rng_seed: Optional[int] = None) -> Tuple[List[TowerPlacement], GAReport]:
    """Refine a greedy layout. Returns (towers, report); never worse than `seed`.

    Sizing comes from `cfg.ga` unless overridden here, so the search is tuned in the same
    place as the rest of the layout engine rather than at each call site.

    The report carries both scores and why the run stopped, so the caller can say what
    refinement bought rather than presenting a different layout with no explanation.

        towers, report = refine(greedy_towers, ctx)
        report["improvement_pct"]   # 0.0 when the seed was already the best found
    """
    cfg = ctx.cfg
    ga = cfg.ga
    population = max(4, int(population if population is not None else ga.population))
    generations = max(1, int(generations if generations is not None else ga.generations))
    time_budget_s = float(time_budget_s if time_budget_s is not None else ga.time_budget_s)
    elite = max(1, min(int(ga.elite), population - 1))
    if rng_seed is None and ga.seed is not None:
        rng_seed = int(ga.seed)

    base = list(seed)
    base_fit = _score(base, ctx)

    report: GAReport = GAReport({
        "ran": False, "generations": 0, "evaluations": 1,
        "seed_score": base_fit.score if base_fit.feasible else None,
        "final_score": base_fit.score if base_fit.feasible else None,
        "improvement_pct": 0.0, "stopped": "not run", "improved": False,
    })

    # An infeasible or empty seed is not something to breed from: every child inherits the
    # violation and scores -inf, so the run would burn its budget to return the seed.
    if not base or not base_fit.feasible:
        report["stopped"] = "seed is empty or infeasible; nothing to refine"
        return base, report

    # Deterministic by construction. Derived from the seed geometry so the same layout
    # always refines the same way, without the caller having to thread a seed through.
    if rng_seed is None:
        key = round(sum((t.cx * 31.0 + t.cy * 17.0 + t.width * 7.0 + t.floors) for t in base), 3)
        rng_seed = abs(hash((len(base), key))) % (2 ** 32)
    rng = random.Random(rng_seed)

    # Nudge distance scales with the site, so a 60 m plot and a 600 m one both move by an
    # amount that means something relative to their own geometry.
    step = max(1.0, math.sqrt(max(ctx.plot_area, 1.0)) * 0.02)

    best, best_fit = base, base_fit
    pool: List[Tuple[List[TowerPlacement], FitnessResult]] = [(base, base_fit)]
    for _ in range(population - 1):
        cand = _mutate(base, rng, cfg, step)
        pool.append((cand, _score(cand, ctx)))
    evaluations = population

    started = time.perf_counter()
    stopped = "generation limit"
    gens_run = 0
    stale = 0

    for gen in range(generations):
        if time.perf_counter() - started > time_budget_s:
            stopped = "time budget"
            break
        gens_run = gen + 1

        # Feasible first, then by score. The seed is always in the pool, so `survivors`
        # can never be empty and the elite can never be worse than where we started.
        pool.sort(key=lambda p: (p[1].feasible, p[1].score), reverse=True)
        if pool[0][1].score > best_fit.score:
            best, best_fit = pool[0]
            stale = 0
        else:
            stale += 1

        # Converged: nothing has beaten the incumbent for a while, so keep the budget.
        if stale >= 8:
            stopped = "converged"
            break

        survivors = pool[:max(2, population // 3)]
        # Elites carry forward unchanged. The incumbent is first, so even if every child
        # this generation is infeasible the pool still holds the best layout found.
        nxt: List[Tuple[List[TowerPlacement], FitnessResult]] = [(best, best_fit)]
        nxt += [p for p in pool[:elite] if p[0] is not best][:max(0, elite - 1)]
        while len(nxt) < population:
            pa = rng.choice(survivors)[0]
            pb = rng.choice(survivors)[0]
            if pa is not pb and rng.random() < ga.crossover_rate:
                child = _crossover(pa, pb, rng)
            else:
                child = list(pa)
            if rng.random() < ga.mutation_rate or child is pa:
                child = _mutate(child, rng, cfg, step)
            nxt.append((child, _score(child, ctx)))
            evaluations += 1
        pool = nxt

    pool.sort(key=lambda p: (p[1].feasible, p[1].score), reverse=True)
    if pool and pool[0][1].score > best_fit.score:
        best, best_fit = pool[0]

    # The ratchet. Even with the elite carried through every generation, the result is
    # compared against the seed one final time before it leaves this function.
    if not best_fit.feasible or best_fit.score <= base_fit.score:
        report.update({"ran": True, "generations": gens_run, "evaluations": evaluations,
                       "final_score": base_fit.score, "stopped": stopped,
                       "improved": False, "improvement_pct": 0.0})
        return base, report

    gain = (best_fit.score - base_fit.score) / abs(base_fit.score) * 100 if base_fit.score else 0.0
    report.update({
        "ran": True, "generations": gens_run, "evaluations": evaluations,
        "final_score": best_fit.score, "stopped": stopped, "improved": True,
        "improvement_pct": round(gain, 2),
        "towers_before": len(base), "towers_after": len(best),
        "floor_area_before": round(base_fit.metrics.get("floor_area", 0.0), 1),
        "floor_area_after": round(best_fit.metrics.get("floor_area", 0.0), 1),
        "rng_seed": rng_seed,
    })
    return list(best), report
