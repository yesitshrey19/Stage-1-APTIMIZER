"""Planning and civil optimisers.

Same contract as optimise.py, and the same reason for it: current, best, and the change
between them. These differ in one important way -- every candidate is scored by actually
re-running `engine.analyse` on a mutated copy of the project, not by a parallel model of
what the engine would say.

That matters. A shortcut model that estimates FAR or parking demand itself will eventually
disagree with the engine, and then the optimiser recommends something the compliance tab
immediately rejects. Running the real thing costs about a millisecond per candidate, which
buys exhaustive sweeps and answers that cannot contradict the rest of the app.

Nothing here mutates the stored project. Each optimiser reports what would change.
"""
import copy
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

import engine
import finance as financelib
from optimise import _result

# Sweep bounds. Wide enough to find the boundary, narrow enough that the search stays
# inside what a planning application could actually argue for.
FLOOR_SWEEP = 24              # floors either side of the current count
FOOTPRINT_STEPS = 16
MIX_STEPS = 12

# Mechanical stack parking roughly halves the area per car but costs more per bay and
# needs an attendant, so it is offered as an option and never chosen silently.
STACK_AREA_FACTOR = 0.55
STACK_COST_PER_BAY = 250_000.0
# Built area is roughly this much cheaper above ground than below it: excavation, shoring,
# dewatering, tanking and ramp length all fall away.
BASEMENT_COST_INDEX = 1.55
PODIUM_COST_INDEX = 1.0


def _failed(an: Dict[str, Any]) -> List[str]:
    return [r["label"] for r in an["compliance"]["results"] if r["status"] != "pass"]


def _limits(an: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {r["id"]: r for r in an["compliance"]["results"]}


def _variant(project: Dict[str, Any], mutate: Callable[[Dict[str, Any]], None]
             ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """A mutated copy of the project and its real engine analysis."""
    p = copy.deepcopy(project)
    mutate(p)
    return p, engine.analyse(p)


def _revenue(project: Dict[str, Any], an: Dict[str, Any]) -> float:
    try:
        return float(financelib.analyse(project, an, project.get("finance"))["revenue"]["gross"])
    except Exception:
        return 0.0


# ------------------------------------------------------------------ floors
def floor_optimisation(project: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Sweep floor counts against the height limit, FAR, parking and profit.

    Cost per flat is reported but is NOT the objective, because it is degenerate: it rises
    monotonically with height (taller towers need bigger columns, more lifts, more pumping),
    so minimising it always answers "build one storey" and throws away the site. Profit is
    what floor count actually trades -- each extra floor adds flats until a code rule stops
    it, and the optimiser's job is to find that stopping point.
    """
    towers = project.get("towers") or []
    if not towers:
        return _result("floors", "Floor optimisation",
                       {"value": 0, "unit": "INR", "label": "Cost per flat"},
                       {"value": 0, "unit": "INR", "label": "Cost per flat"},
                       [], feasible=False, notes=["No towers to sweep."])

    base_floors = int(towers[0].get("floors") or 0)
    rows = []
    for f in range(max(base_floors - FLOOR_SWEEP, 1), base_floors + FLOOR_SWEEP + 1):
        def mut(p, f=f):
            for t in p["towers"]:
                t["floors"] = f
        p2, an2 = _variant(project, mut)
        fails = _failed(an2)
        units = an2["areas"]["total_units"]
        revenue = _revenue(p2, an2)
        rows.append({
            "floors": f, "units": units,
            "far": an2["areas"]["far"],
            "height_m": an2["areas"]["max_height_m"],
            "cost_per_unit": an2["cost"]["per_unit"],
            "total_cost": an2["cost"]["total"],
            "revenue": round(revenue, 0),
            "profit": round(revenue - an2["cost"]["total"], 0),
            "compliant": not fails, "fails": fails,
            "current": f == base_floors,
        })

    cur = next(r for r in rows if r["current"])
    legal = [r for r in rows if r["compliant"] and r["units"] > 0]
    if not legal:
        return _result("floors", "Floor optimisation",
                       {"value": cur["profit"], "unit": "INR", "label": "Profit today"},
                       {"value": cur["profit"], "unit": "INR", "label": "No compliant alternative"},
                       [], options=rows, feasible=False, lower_is_better=False,
                       notes=["No floor count in the sweep passes every check."])
    best = max(legal, key=lambda r: r["profit"])

    changes = []
    if best["floors"] != cur["floors"]:
        changes.append({
            "lever": "Floors per tower", "from": f'{cur["floors"]}', "to": f'{best["floors"]}',
            "effect": (f'{best["units"] - cur["units"]:+d} flats, FAR {cur["far"]} to {best["far"]}, '
                       f'height {cur["height_m"]} to {best["height_m"]} m. Cost per flat rises from '
                       f'INR {cur["cost_per_unit"]:,.0f} to {best["cost_per_unit"]:,.0f} -- a taller '
                       "tower is dearer per flat -- but the extra flats more than cover it."),
        })
    else:
        changes.append({"lever": "Floors per tower", "from": f'{cur["floors"]}', "to": "unchanged",
                        "effect": "no compliant floor count earns more than this one"})

    ceiling = max((r["floors"] for r in rows if r["compliant"]), default=cur["floors"])
    notes = [f'The sweep runs {rows[0]["floors"]} to {rows[-1]["floors"]} floors. '
             f'{ceiling} is the tallest that still passes every check.']
    blocked = next((r for r in rows if r["floors"] == ceiling + 1), None)
    if blocked:
        notes.append(f'At {blocked["floors"]} floors it fails: {", ".join(blocked["fails"])}. '
                     "Fix that and the ceiling moves.")
    notes.append("Cost per flat is in the table but is not what is being maximised. It rises "
                 "with every floor, so minimising it would answer \"build one storey\" and "
                 "throw the site away.")
    notes.append("Profit uses the sale rates saved in Feasibility & ROI.")

    return _result(
        "floors", "Floor optimisation",
        {"value": cur["profit"], "unit": "INR",
         "label": f'Profit at {cur["floors"]} floors ({cur["units"]} flats)'},
        {"value": best["profit"], "unit": "INR",
         "label": f'Profit at {best["floors"]} floors ({best["units"]} flats)'},
        changes, options=rows, notes=notes, lower_is_better=False)


# ------------------------------------------------------------------ FAR / FSI
def far_optimisation(project: Dict[str, Any], analysis: Dict[str, Any],
                     which: str = "far") -> Dict[str, Any]:
    """Headroom against the cap, and the legal change that would consume it."""
    lim = _limits(analysis)
    rule = lim.get(f"{which}_max")
    areas = analysis["areas"]
    current = float(areas[which])
    cap = float(rule["threshold"]) if rule else 0.0
    plot = float(areas["plot_area_sqm"] or 0)
    towers = project.get("towers") or []
    base_floors = int(towers[0].get("floors") or 0) if towers else 0

    if not cap or not plot:
        return _result(which, f"{which.upper()} optimisation",
                       {"value": current, "unit": "", "label": f"{which.upper()} today"},
                       {"value": current, "unit": "", "label": "No cap recorded"},
                       [], feasible=False,
                       notes=[f"No {which.upper()} cap is recorded for this project, so there is "
                              "nothing to optimise against. Set it in Compliance."])

    # How many more floors the headroom buys, checked against every other rule rather
    # than assumed -- height, coverage and parking usually bind before FAR does.
    reachable, reachable_far, blocker = base_floors, current, []
    for f in range(base_floors, base_floors + FLOOR_SWEEP * 2):
        def mut(p, f=f):
            for t in p["towers"]:
                t["floors"] = f
        _, an2 = _variant(project, mut)
        fails = _failed(an2)
        if fails:
            blocker = fails
            break
        reachable, reachable_far = f, an2["areas"][which]

    headroom = round(cap - current, 3)
    unused_sqm = round(headroom * plot, 1)
    changes = []
    if reachable > base_floors:
        changes.append({
            "lever": "Floors per tower", "from": str(base_floors), "to": str(reachable),
            "effect": (f'{which.upper()} {current} to {round(reachable_far, 3)} of the {cap} allowed. '
                       f'That is the most the site takes before {", ".join(blocker) or "the cap"} binds.'),
        })
    else:
        changes.append({
            "lever": "Floors per tower", "from": str(base_floors), "to": "unchanged",
            "effect": (f'Adding a floor already fails: {", ".join(blocker) or "no headroom"}. '
                       f'The unused {which.upper()} cannot be built without changing the footprint '
                       "or the plot."),
        })

    return _result(
        which, f"{which.upper()} optimisation",
        {"value": current, "unit": "", "label": f"{which.upper()} used today"},
        {"value": round(reachable_far, 3), "unit": "",
         "label": f"Reachable while still compliant (cap {cap})"},
        changes,
        lower_is_better=False,
        options=[{"cap": cap, "current": current, "reachable": round(reachable_far, 3),
                  "headroom": headroom, "unused_buildable_sqm": unused_sqm}],
        notes=[f'{unused_sqm} m2 of buildable area sits unused under the {cap} cap. '
               "Headroom is only worth something if another rule does not stop you using it.",
               "FSI here is FAR times the project's FSI factor, so the two move together."],
    )


# ------------------------------------------------------------------ open space
def open_space_optimisation(project: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Treat open space as the objective: how much is reachable at constant floor area.

    Open space is normally a floor to clear. Turned into an objective, the lever is
    footprint: a slimmer, taller tower houses the same flats on less ground.
    """
    towers = project.get("towers") or []
    areas = analysis["areas"]
    if not towers:
        return _result("open_space", "Open space optimisation",
                       {"value": areas["open_space_pct"], "unit": "%", "label": "Open space"},
                       {"value": areas["open_space_pct"], "unit": "%", "label": "Open space"},
                       [], feasible=False, notes=["No towers to reshape."])

    base_fp = float(towers[0].get("footprint_area") or 0)
    base_floors = int(towers[0].get("floors") or 0)
    base_units = areas["total_units"]

    rows = []
    for i in range(FOOTPRINT_STEPS):
        shrink = 1.0 - i * 0.04                      # down to 40% of today's footprint
        fp = base_fp * shrink
        # Hold total floor area: a smaller plate needs more floors for the same flats.
        floors = max(int(round(base_floors / shrink)), 1)

        def mut(p, fp=fp, floors=floors):
            for t in p["towers"]:
                t["footprint_area"] = round(fp, 2)
                t["floors"] = floors
        _, an2 = _variant(project, mut)
        fails = _failed(an2)
        rows.append({
            "footprint_sqm": round(fp, 1), "floors": floors,
            "open_space_pct": an2["areas"]["open_space_pct"],
            "ground_coverage_pct": an2["areas"]["ground_coverage_pct"],
            "units": an2["areas"]["total_units"],
            "height_m": an2["areas"]["max_height_m"],
            "cost_per_unit": an2["cost"]["per_unit"],
            "compliant": not fails, "fails": fails,
            "current": i == 0,
        })

    cur = rows[0]
    # Only count an option that does not quietly cost flats to buy the greenery.
    legal = [r for r in rows if r["compliant"] and r["units"] >= base_units]
    best = max(legal, key=lambda r: r["open_space_pct"]) if legal else cur

    changes = []
    if best is not cur and best["open_space_pct"] > cur["open_space_pct"]:
        changes.append({
            "lever": "Tower footprint and height",
            "from": f'{cur["footprint_sqm"]} m2 over {cur["floors"]} floors',
            "to": f'{best["footprint_sqm"]} m2 over {best["floors"]} floors',
            "effect": (f'Open space {cur["open_space_pct"]}% to {best["open_space_pct"]}% for the '
                       f'same {best["units"]} flats. A slimmer plate at {best["height_m"]} m '
                       "gives the ground back."),
        })
    else:
        changes.append({"lever": "Tower footprint", "from": f'{cur["footprint_sqm"]} m2',
                        "to": "unchanged",
                        "effect": "no slimmer tower holds the same flats and still complies"})

    return _result(
        "open_space", "Open space optimisation",
        {"value": cur["open_space_pct"], "unit": "%", "label": "Open space today"},
        {"value": best["open_space_pct"], "unit": "%",
         "label": "Reachable at the same number of flats"},
        changes, lower_is_better=False, options=rows,
        notes=["Total floor area is held constant, so this trades ground for height rather "
               "than trading away flats.",
               "A taller, slimmer tower costs more per m2 to build -- lifts, pumping and "
               "wind loads all rise. Check the cost per flat column before committing."],
    )


# ------------------------------------------------------------------ apartment mix
def mix_optimisation(project: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Shift the unit mix to maximise revenue, subject to FAR and parking."""
    towers = project.get("towers") or []
    units = (towers[0].get("units") if towers else []) or []
    if len(units) < 2:
        return _result("mix", "Apartment mix optimisation",
                       {"value": 0, "unit": "INR", "label": "Gross revenue"},
                       {"value": 0, "unit": "INR", "label": "Gross revenue"},
                       [], feasible=False,
                       notes=["At least two unit types are needed before a mix can be shifted."])

    base_counts = [int(u.get("count") or 0) for u in units]
    total = sum(base_counts)
    base_rev = _revenue(project, analysis)

    rows = []
    # Sweep the share of the FIRST type from 0 to all, moving the balance to the last type.
    for i in range(MIX_STEPS + 1):
        first = round(total * i / MIX_STEPS)
        counts = list(base_counts)
        counts[0] = first
        rest = total - first
        for j in range(1, len(counts)):
            counts[j] = rest // (len(counts) - 1)
        counts[-1] += rest - sum(counts[1:])

        def mut(p, counts=counts):
            for t in p["towers"]:
                for u, c in zip(t["units"], counts):
                    u["count"] = c
        p2, an2 = _variant(project, mut)
        fails = _failed(an2)
        rev = _revenue(p2, an2)
        rows.append({
            "mix": {u.get("type"): c for u, c in zip(units, counts)},
            "units": an2["areas"]["total_units"],
            "far": an2["areas"]["far"],
            "parking_required": an2["parking"]["required_slots"],
            "parking_deficit": an2["parking"]["deficit"],
            "revenue": round(rev, 0), "cost": an2["cost"]["total"],
            "margin": round(rev - an2["cost"]["total"], 0),
            "compliant": not fails, "fails": fails,
            "current": counts == base_counts,
        })

    cur = next((r for r in rows if r["current"]), None) or {
        "mix": {u.get("type"): c for u, c in zip(units, base_counts)},
        "revenue": round(base_rev, 0), "far": analysis["areas"]["far"],
        "parking_deficit": analysis["parking"]["deficit"], "compliant": True}
    legal = [r for r in rows if r["compliant"]]
    best = max(legal, key=lambda r: r["revenue"]) if legal else cur

    def show(m):
        return ", ".join(f"{k.upper()} x{v}" for k, v in m.items())

    changes = []
    if best["mix"] != cur["mix"]:
        changes.append({
            "lever": "Flats per floor by type", "from": show(cur["mix"]), "to": show(best["mix"]),
            "effect": (f'Revenue {round((best["revenue"] - cur["revenue"]) / 1e5):+,} lakh. '
                       f'FAR {best["far"]}, parking shortfall {best["parking_deficit"]}.'),
        })
    else:
        changes.append({"lever": "Flats per floor by type", "from": show(cur["mix"]),
                        "to": "unchanged",
                        "effect": "the current mix already earns the most of any compliant mix"})

    return _result(
        "mix", "Apartment mix optimisation",
        {"value": cur["revenue"], "unit": "INR", "label": "Gross revenue on the current mix"},
        {"value": best["revenue"], "unit": "INR", "label": "Best compliant mix"},
        changes, lower_is_better=False, options=rows,
        notes=["Revenue uses the sale rates saved in Feasibility & ROI. Change those and the "
               "best mix changes with them.",
               "The total flats per floor is held constant, so this is a mix shift, not a "
               "density increase.",
               "Only saleability inside the building is modelled. Whether the market absorbs "
               "that many of one type is not something the project data can answer."],
    )


# ------------------------------------------------------------------ parking
def parking_optimisation(project: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Least built area that still meets the NBC slot requirement."""
    pk = analysis["parking"]
    cfg = project.get("parking") or {}
    required = int(pk["required_slots"] or 0)
    area_per_slot = float(cfg.get("area_per_slot") or 30.0)
    b_levels = int(cfg.get("basement_levels") or 0)
    b_area = float(cfg.get("basement_area_per_level") or 0)
    g_area = float(cfg.get("ground_area") or 0)
    current_area = b_levels * b_area + g_area

    # Cost weight, not rupees: basement area is dearer than podium, so the same slot count
    # is cheaper pushed upward. Reported as an index so it never reads as a real quote.
    def weighted(basement, ground):
        return basement * BASEMENT_COST_INDEX + ground * PODIUM_COST_INDEX

    current_weighted = weighted(b_levels * b_area, g_area)
    need_area = required * area_per_slot

    options = []
    for stack in (False, True):
        per_slot = area_per_slot * (STACK_AREA_FACTOR if stack else 1.0)
        need = required * per_slot
        for levels in range(0, b_levels + 3):
            basement = min(need, levels * b_area) if b_area else 0.0
            ground = max(need - basement, 0.0)
            slots = int((basement + ground) / per_slot) if per_slot else 0
            options.append({
                "stack": stack, "basement_levels": levels,
                "basement_sqm": round(basement, 1), "ground_sqm": round(ground, 1),
                "total_sqm": round(basement + ground, 1),
                "slots": slots, "meets_requirement": slots >= required,
                "cost_index": round(weighted(basement, ground)
                                    + (required * STACK_COST_PER_BAY / 1e5 if stack else 0), 1),
                "note": ("mechanical stackers, roughly half the area per car but a capital "
                         "cost per bay and an attendant" if stack else "conventional bays"),
            })

    legal = [o for o in options if o["meets_requirement"]]
    best = min(legal, key=lambda o: o["cost_index"]) if legal else None

    changes = []
    if best:
        if best["basement_levels"] != b_levels:
            changes.append({
                "lever": "Basement levels", "from": str(b_levels), "to": str(best["basement_levels"]),
                "effect": (f'{round(current_area - best["total_sqm"], 1):+} m2 of parking structure. '
                           "Basement area costs about 55% more than podium once excavation, "
                           "shoring and tanking are counted."),
            })
        if best["stack"]:
            changes.append({
                "lever": "Mechanical stackers", "from": "conventional bays", "to": "stacked bays",
                "effect": (f'{required} bays in {best["total_sqm"]} m2 instead of {round(need_area, 1)} m2. '
                           "Adds capital cost per bay and needs an attendant."),
            })
    if not changes:
        changes.append({"lever": "Parking layout", "from": f'{round(current_area, 1)} m2',
                        "to": "unchanged",
                        "effect": "already the least structure that meets the requirement"})

    return _result(
        "parking", "Parking optimisation",
        {"value": round(current_area, 1), "unit": "m2", "label": "Parking structure today"},
        {"value": best["total_sqm"] if best else round(current_area, 1), "unit": "m2",
         "label": f"Least area that still provides {required} slots"},
        changes, options=options,
        feasible=bool(best),
        notes=[f'The NBC requirement of {required} slots is fixed by the unit count and is '
               "never traded away here -- only the structure that houses it changes.",
               "Cost index is relative, not a quote: basement 1.55, podium 1.00."],
    )


# ------------------------------------------------------------------ utilities
def utility_optimisation(project: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Size the tanks, STP and rainwater store for least cost at compliance."""
    u = analysis["utilities"]
    demand_lpd = float(u.get("water_demand_lpd") or 0)
    ug = float(u.get("ug_tank_cum") or 0)
    oh = float(u.get("oh_tank_cum") or 0)
    stp = float(u.get("stp_capacity_kld") or 0)
    rwh_store = float(u.get("rwh_storage_cum") or 0)
    rwh_yr = float(u.get("rwh_annual_litres") or 0)

    # Indicative construction cost per cubic metre of stored capacity, and per KLD of
    # treatment. Relative sizing is what matters here, not the absolute figure.
    TANK_INR_PER_CUM = 12_000.0
    STP_INR_PER_KLD = 45_000.0

    current_cost = (ug + oh + rwh_store) * TANK_INR_PER_CUM + stp * STP_INR_PER_KLD

    rows = []
    # Recycled water from the STP displaces fresh demand for flushing and landscape, which
    # is the one lever that shrinks storage without touching compliance.
    for reuse_pct in (0, 20, 40, 60):
        flush = float(u.get("flushing_lpd") or 0)
        ext = float(u.get("external_lpd") or 0)
        displaced = (flush + ext) * reuse_pct / 100.0
        net_demand = max(demand_lpd - displaced, 0.0)
        # Tank sizing scales with demand; the code minimum for fire storage does not, so
        # storage never falls below the fire reserve.
        scale = net_demand / demand_lpd if demand_lpd else 1.0
        ug2 = max(ug * scale, ug * 0.5)
        oh2 = max(oh * scale, oh * 0.5)
        cost = (ug2 + oh2 + rwh_store) * TANK_INR_PER_CUM + stp * STP_INR_PER_KLD
        rows.append({
            "reuse_pct": reuse_pct, "net_demand_lpd": round(net_demand, 0),
            "ug_tank_cum": round(ug2, 1), "oh_tank_cum": round(oh2, 1),
            "stp_kld": stp, "rwh_storage_cum": rwh_store,
            "cost": round(cost, 0), "current": reuse_pct == 0,
            "note": ("no treated water reused" if reuse_pct == 0
                     else f"{reuse_pct}% of flushing and landscape demand met from the STP"),
        })

    cur = rows[0]
    best = min(rows, key=lambda r: r["cost"])
    changes = []
    if best["reuse_pct"] > 0:
        changes.append({
            "lever": "Treated water reuse",
            "from": "none — all demand from fresh supply",
            "to": f'{best["reuse_pct"]}% of flushing and landscape from the STP',
            "effect": (f'Fresh demand {round(cur["net_demand_lpd"])} to {round(best["net_demand_lpd"])} '
                       f'litres a day, so storage drops from {cur["ug_tank_cum"] + cur["oh_tank_cum"]} '
                       f'to {round(best["ug_tank_cum"] + best["oh_tank_cum"], 1)} m3. '
                       "Needs dual plumbing, which is cheap to build in and expensive to retrofit."),
        })
    if rwh_yr > 0:
        changes.append({
            "lever": "Rainwater harvesting",
            "from": f'{round(rwh_store, 1)} m3 of storage',
            "to": "keep",
            "effect": (f'Captures {round(rwh_yr / 1000):,} m3 a year, about '
                       f'{round(rwh_yr / (demand_lpd * 365) * 100, 1) if demand_lpd else 0}% of '
                       "annual demand. Already mandatory here and already sized."),
        })
    if not changes:
        changes.append({"lever": "Utility sizing", "from": "current", "to": "unchanged",
                        "effect": "already at the compliant minimum"})

    return _result(
        "utilities", "Utility optimisation",
        {"value": round(current_cost, 0), "unit": "INR",
         "label": "Tanks, STP and rainwater storage as sized"},
        {"value": best["cost"], "unit": "INR", "label": "With treated water reused"},
        changes, options=rows,
        notes=["Storage never falls below half the sized volume, because the fire reserve "
               "inside the sump is fixed by code and does not shrink with demand.",
               f'Costed at INR {TANK_INR_PER_CUM:,.0f} per m3 stored and INR {STP_INR_PER_KLD:,.0f} '
               "per KLD treated. Indicative, for comparing options against each other."],
    )


def analyse(project: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Every planning and civil optimiser, in one payload."""
    return {
        "ok": True,
        "floors": floor_optimisation(project, analysis),
        "far": far_optimisation(project, analysis, "far"),
        "fsi": far_optimisation(project, analysis, "fsi"),
        "open_space": open_space_optimisation(project, analysis),
        "mix": mix_optimisation(project, analysis),
        "parking": parking_optimisation(project, analysis),
        "utilities": utility_optimisation(project, analysis),
        "currency": analysis["cost"].get("currency", "INR"),
    }
