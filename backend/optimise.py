"""Quantity, cost and material optimisers.

Every function here answers the same three-part question, because a number that only
describes the present scheme is not an optimiser:

    current   what the scheme does today
    best      what the search found
    changes   the specific levers that get you from one to the other

Scored through one shared result shape (`_result`) for the same reason siteplan/fitness.py
scores greedy and GA together: separate scoring lets two paths disagree about which answer
is better, and then the "improvement" is not one.

Nothing here mutates the project. Each optimiser reports what would change and by how
much; applying it is the user's decision, taken in the module that owns those inputs.
"""
import math
from typing import Any, Dict, List, Optional

import iscodes as C

# Standard rebar stock length in India. Mills roll 12 m; 9 m and 6 m are cut stock.
REBAR_STOCK_M = 12.0
REBAR_MIN_USEFUL_M = 0.6      # shorter offcuts go to scrap, not to stirrups

# Concrete grades worth sweeping, with the rate premium each carries over M20 and the
# section saving a higher grade buys. Compressive capacity rises with grade, so a column
# carrying the same load needs less area -- roughly with the square root of the ratio,
# which is the standard preliminary approximation.
CONCRETE_GRADES = [20, 25, 30, 35, 40]
GRADE_RATE_INDEX = {20: 0.94, 25: 1.00, 30: 1.07, 35: 1.15, 40: 1.24}

# Reinforcement grades. Fe500 needs about 500/415 less steel by weight for the same
# moment, less a bit for the stiffer detailing and minimum-steel rules that do not scale.
STEEL_GRADES = [415, 500, 550]
STEEL_RATE_INDEX = {415: 1.00, 500: 1.02, 550: 1.05}
STEEL_EFFICIENCY = {415: 1.00, 500: 0.88, 550: 0.83}

# Tile modules on the market, in mm. Bigger tiles cut faster but waste more at edges.
TILE_MODULES_MM = [300, 400, 600, 800]

# What a tonne of CO2e is worth when ranking one material against another, in INR. This
# is the only place carbon and money are traded off, and the number decides the answer:
# at zero, the cheapest option always wins and the carbon column is decoration; too high
# and it recommends things no developer would buy. 1,500 sits inside the range Indian
# internal carbon prices actually use, and is deliberately named here rather than buried
# as a literal, because moving it moves every recommendation.
CARBON_PRICE_INR_PER_TONNE = 1500.0


def _priced(analysis: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Material lines keyed by material, carrying quantity AND rate.

    quantities()["items"] has the quantity but no money on it -- rate, wastage_pct and
    amount are only added later, in boq(). The BOQ material lines are a strict superset of
    the quantity items, so everything reads from here. Reading a rate off a quantity item
    silently yields zero, which makes every cost optimiser report a free project.
    """
    return {m["key"]: m for m in analysis["boq"]["materials"]}


def _result(oid: str, title: str, current: Dict[str, Any], best: Dict[str, Any],
            changes: List[Dict[str, Any]], *, lower_is_better: bool = True,
            options: Optional[List[Dict[str, Any]]] = None,
            notes: Optional[List[str]] = None,
            feasible: bool = True) -> Dict[str, Any]:
    """The shape every optimiser returns. `delta` is always signed as a saving."""
    cv, bv = float(current.get("value") or 0), float(best.get("value") or 0)
    gain = (cv - bv) if lower_is_better else (bv - cv)
    return {
        "id": oid, "title": title, "feasible": feasible,
        "current": current, "best": best,
        "delta": {
            "value": round(abs(gain), 2),
            "pct": round(abs(gain) / cv * 100, 2) if cv else 0.0,
            "improves": gain > 1e-9,
            "direction": "lower is better" if lower_is_better else "higher is better",
        },
        "changes": changes, "options": options or [], "notes": notes or [],
    }


# ------------------------------------------------------------------ waste reduction
def _first_fit_decreasing(lengths: List[float], stock: float) -> List[List[float]]:
    """Classic 1D cutting stock, first-fit-decreasing.

    Optimal bin packing is NP-hard; FFD is within 11/9 of optimal and is what a bar-bending
    schedule is cut to in practice anyway. Longest pieces first, because a long piece that
    arrives late has nowhere left to go.
    """
    bars: List[List[float]] = []
    for L in sorted(lengths, reverse=True):
        if L > stock:                       # needs a lap splice; cut from a full bar
            bars.append([stock])
            continue
        for bar in bars:
            if sum(bar) + L <= stock + 1e-9:
                bar.append(L)
                break
        else:
            bars.append([L])
    return bars


def steel_waste(quantities: Dict[str, Any], grid: Dict[str, Any],
                floors: int, floor_height: float) -> Dict[str, Any]:
    """Offcut waste on reinforcement, cut naively against cut to a schedule."""
    bay_x = float(grid.get("bay_x_m") or 5.0)
    bay_y = float(grid.get("bay_y_m") or 5.0)
    columns = int(grid.get("columns") or 0)

    # The member lengths a frame actually needs, per floor.
    cuts: List[float] = []
    beams_x = max(int(grid.get("bays_x") or 0), 0) * (max(int(grid.get("bays_y") or 0), 0) + 1)
    beams_y = max(int(grid.get("bays_y") or 0), 0) * (max(int(grid.get("bays_x") or 0), 0) + 1)
    cuts += [bay_x] * beams_x
    cuts += [bay_y] * beams_y
    cuts += [floor_height] * columns
    cuts = [c for c in cuts if c > 0]
    if not cuts:
        return {}

    per_floor_naive = len(cuts)                       # one stock bar per member, offcut binned
    bars = _first_fit_decreasing(cuts, REBAR_STOCK_M)
    per_floor_packed = len(bars)

    used = sum(sum(b) for b in bars)
    naive_steel = per_floor_naive * REBAR_STOCK_M
    packed_steel = per_floor_packed * REBAR_STOCK_M
    naive_waste_pct = round((naive_steel - used) / naive_steel * 100, 1) if naive_steel else 0.0
    packed_waste_pct = round((packed_steel - used) / packed_steel * 100, 1) if packed_steel else 0.0

    # Reusable offcuts: what is left on each bar, if it is long enough to be a stirrup leg.
    reusable = sum(REBAR_STOCK_M - sum(b) for b in bars
                   if REBAR_MIN_USEFUL_M <= (REBAR_STOCK_M - sum(b)))
    schedule: Dict[str, int] = {}
    for b in bars:
        key = " + ".join(f"{x:.1f}" for x in sorted(b, reverse=True))
        schedule[key] = schedule.get(key, 0) + 1

    return {
        "cuts_per_floor": len(cuts), "bars_naive": per_floor_naive, "bars_packed": per_floor_packed,
        "naive_waste_pct": naive_waste_pct, "packed_waste_pct": packed_waste_pct,
        "bars_saved_per_floor": per_floor_naive - per_floor_packed,
        "bars_saved_total": (per_floor_naive - per_floor_packed) * max(floors, 1),
        "reusable_offcut_m_per_floor": round(reusable, 1),
        "schedule": [{"pattern": k, "bars": v, "offcut_m": round(REBAR_STOCK_M - sum(float(x) for x in k.split(" + ")), 2)}
                     for k, v in sorted(schedule.items(), key=lambda kv: -kv[1])][:12],
    }


def tile_waste(area_sqm: float, room_w: float = 3.6, room_d: float = 4.2) -> List[Dict[str, Any]]:
    """Edge-cut waste for each tile module, over a representative room."""
    rows = []
    for mm in TILE_MODULES_MM:
        m = mm / 1000.0
        nx, ny = math.ceil(room_w / m), math.ceil(room_d / m)
        laid = nx * ny * m * m
        waste_pct = round((laid - room_w * room_d) / laid * 100, 1) if laid else 0.0
        rows.append({"module_mm": mm, "tiles_per_room": nx * ny,
                     "waste_pct": waste_pct,
                     "extra_sqm_project": round(area_sqm * waste_pct / 100, 1)})
    return rows


def waste_reduction(project: Dict[str, Any], analysis: Dict[str, Any],
                    eng: Dict[str, Any]) -> Dict[str, Any]:
    q = _priced(analysis)
    areas = analysis["areas"]
    towers = areas.get("towers") or []
    floors = max((int(t.get("floors") or 0) for t in towers), default=1)
    fh = float((towers[0].get("floor_height") if towers else 3.0) or 3.0)

    gm = (eng.get("modules") or {}).get("grid") or {}
    cfg = (eng.get("config") or {})
    grid = {"bay_x_m": cfg.get("grid_bay_x_m"), "bay_y_m": cfg.get("grid_bay_y_m")}
    for o in gm.get("outputs", []):
        if o["label"] == "Column count":
            grid["columns"] = o["value"]
        if o["label"] == "Bays" and isinstance(o["value"], str) and "×" in o["value"]:
            a, b = o["value"].split("×")
            grid["bays_x"], grid["bays_y"] = int(a.strip()), int(b.strip())

    steel = steel_waste(q, grid, floors, fh)
    tiles = tile_waste(float(q.get("tiles", {}).get("quantity") or 0))
    best_tile = min(tiles, key=lambda r: r["waste_pct"]) if tiles else None

    steel_rate = float(q.get("steel", {}).get("rate") or 0)
    steel_qty = float(q.get("steel", {}).get("quantity") or 0)
    bill_allowance = float(q.get("steel", {}).get("wastage_pct") or 0)

    # Compare like with like: offcut waste cutting member by member, against offcut waste
    # cutting to a nested schedule. Measuring the schedule against the bill's flat
    # commercial wastage allowance compares two unrelated things, and makes good practice
    # look like a regression.
    current_waste = steel.get("naive_waste_pct", 0.0)
    achievable = steel.get("packed_waste_pct", current_waste)
    saving_kg = steel_qty * max(current_waste - achievable, 0) / 100.0
    changes = []
    if steel and steel.get("bars_saved_total", 0) > 0:
        changes.append({
            "lever": "Cut reinforcement to a bar-bending schedule",
            "from": f'{steel["bars_naive"]} bars per floor, cut member by member',
            "to": f'{steel["bars_packed"]} bars per floor, nested',
            "effect": (f'{steel["bars_saved_total"]} fewer 12 m bars across {floors} floors, '
                       f'and {steel["reusable_offcut_m_per_floor"]} m of offcut per floor still '
                       "long enough for stirrups"),
        })
    if best_tile and best_tile["module_mm"] != 600:
        cur = next((t for t in tiles if t["module_mm"] == 600), None)
        if cur and cur["waste_pct"] > best_tile["waste_pct"]:
            changes.append({
                "lever": "Tile module",
                "from": f'600 mm ({cur["waste_pct"]}% edge waste)',
                "to": f'{best_tile["module_mm"]} mm ({best_tile["waste_pct"]}% edge waste)',
                "effect": f'about {cur["extra_sqm_project"] - best_tile["extra_sqm_project"]} m2 less tile bought',
            })

    notes = ["Steel waste is offcut only. Lap splices, bending losses and site pilferage are "
             "separate and are not modelled here.",
             "The bill carries a flat {}% steel wastage allowance. That is a commercial "
             "allowance, not a cutting result -- the two figures above are what the "
             "schedule actually changes.".format(bill_allowance)]
    if not steel:
        notes.append("No column grid available, so the bar schedule could not be built. "
                     "Set the grid in the Engineering module.")

    return _result(
        "waste", "Waste reduction",
        {"value": current_waste, "unit": "%", "label": "Offcut waste, cut member by member"},
        {"value": achievable, "unit": "%", "label": "Offcut waste, cut to a nested schedule"},
        changes,
        options=[{"kind": "tiles", **t} for t in tiles],
        notes=notes,
        feasible=bool(steel),
    ) | {"steel": steel, "tiles": tiles,
         "steel_saving_kg": round(saving_kg, 1),
         "steel_saving_inr": round(saving_kg * steel_rate, 0)}


# ------------------------------------------------------------------ grade sweep
def _grade_sweep(analysis: Dict[str, Any], eng: Dict[str, Any]) -> Dict[str, Any]:
    """Cost of every legal concrete/steel grade pair, holding capacity constant."""
    q = _priced(analysis)
    cfg = eng.get("config") or {}
    exposure = str(cfg.get("exposure_condition") or "moderate").lower()
    min_grade = C.EXPOSURE.get(exposure, C.EXPOSURE["moderate"])["min_grade"]

    conc_qty = float(q.get("concrete", {}).get("quantity") or 0)
    conc_rate = float(q.get("concrete", {}).get("rate") or 0)
    steel_qty = float(q.get("steel", {}).get("quantity") or 0)
    steel_rate = float(q.get("steel", {}).get("rate") or 0)
    cement_qty = float(q.get("cement", {}).get("quantity") or 0)
    cement_rate = float(q.get("cement", {}).get("rate") or 0)

    current_grade = 25
    for o in (eng.get("modules") or {}).get("mix", {}).get("outputs", []):
        if o["label"] == "Grade of concrete" and isinstance(o["value"], str):
            current_grade = int("".join(ch for ch in o["value"] if ch.isdigit()) or 25)
    current_steel = int(cfg.get("steel_grade") or 500)

    rows = []
    for g in CONCRETE_GRADES:
        for s in STEEL_GRADES:
            legal = g >= min_grade
            # Higher grade concrete carries more load per m2, so sections shrink roughly
            # with the square root of the strength ratio. Preliminary sizing only.
            vol_factor = math.sqrt(current_grade / g)
            vol = conc_qty * vol_factor
            cement = cement_qty * vol_factor * (g / current_grade)   # richer mix per m3
            steel = steel_qty * (STEEL_EFFICIENCY[s] / STEEL_EFFICIENCY[current_steel])
            cost = (vol * conc_rate * GRADE_RATE_INDEX[g]
                    + cement * cement_rate
                    + steel * steel_rate * STEEL_RATE_INDEX[s])
            rows.append({
                "concrete_grade": f"M{g}", "steel_grade": f"Fe{s}",
                "legal": legal,
                "reason": "" if legal else f"below IS 456 minimum M{min_grade} for {exposure} exposure",
                "concrete_m3": round(vol, 1), "cement_bags": round(cement, 0),
                "steel_kg": round(steel, 0), "cost": round(cost, 0),
                "current": g == current_grade and s == current_steel,
            })
    return {"rows": rows, "min_grade": min_grade, "exposure": exposure,
            "current_grade": current_grade, "current_steel": current_steel}


def quantity_optimisation(project: Dict[str, Any], analysis: Dict[str, Any],
                          eng: Dict[str, Any]) -> Dict[str, Any]:
    """Cheapest grade pair that still satisfies IS 456 for this exposure."""
    sweep = _grade_sweep(analysis, eng)
    rows = sweep["rows"]
    cur = next((r for r in rows if r["current"]), None)
    legal = [r for r in rows if r["legal"]]
    if not cur or not legal:
        return _result("quantity", "Quantity optimisation",
                       {"value": 0, "unit": "INR", "label": "Structural material cost"},
                       {"value": 0, "unit": "INR", "label": "Best compliant combination"},
                       [], options=rows, feasible=False,
                       notes=["No legal grade combination could be priced for this project."])
    best = min(legal, key=lambda r: r["cost"])

    changes = []
    if best["concrete_grade"] != cur["concrete_grade"]:
        changes.append({
            "lever": "Concrete grade", "from": cur["concrete_grade"], "to": best["concrete_grade"],
            "effect": (f'{round(cur["concrete_m3"] - best["concrete_m3"], 1)} m3 less concrete -- '
                       "a stronger mix carries the same load in a smaller section"),
        })
    if best["steel_grade"] != cur["steel_grade"]:
        changes.append({
            "lever": "Reinforcement grade", "from": cur["steel_grade"], "to": best["steel_grade"],
            "effect": f'{round(cur["steel_kg"] - best["steel_kg"], 0)} kg less steel for the same moment capacity',
        })
    if not changes:
        changes.append({"lever": "None", "from": cur["concrete_grade"] + " / " + cur["steel_grade"],
                        "to": "unchanged",
                        "effect": "the current specification is already the cheapest compliant one"})

    return _result(
        "quantity", "Quantity optimisation",
        {"value": cur["cost"], "unit": "INR",
         "label": f'Structural material cost at {cur["concrete_grade"]} / {cur["steel_grade"]}'},
        {"value": best["cost"], "unit": "INR",
         "label": f'Cheapest compliant: {best["concrete_grade"]} / {best["steel_grade"]}'},
        changes, options=rows,
        notes=[f'IS 456 requires at least M{sweep["min_grade"]} for {sweep["exposure"]} exposure; '
               "combinations below that are shown but never selected.",
               "Section sizes scale with the square root of the strength ratio, which is a "
               "preliminary approximation. Re-run the structural design before specifying."],
    )


def budget_optimisation(project: Dict[str, Any], analysis: Dict[str, Any],
                        eng: Dict[str, Any], target: float = 0.0) -> Dict[str, Any]:
    """What reaches a target budget, what it costs elsewhere, and what is out of reach."""
    boq = analysis["boq"]
    current_total = float(boq["grand_total"] or 0)
    target = float(target or 0) or current_total * 0.9

    sweep = _grade_sweep(analysis, eng)
    cur = next((r for r in sweep["rows"] if r["current"]), None)
    legal = [r for r in sweep["rows"] if r["legal"]]
    structural_now = cur["cost"] if cur else 0.0

    levers = []
    for r in sorted(legal, key=lambda x: x["cost"]):
        saving = structural_now - r["cost"]
        if saving <= 0:
            continue
        levers.append({
            "lever": f'Specify {r["concrete_grade"]} / {r["steel_grade"]}',
            "saving": round(saving, 0),
            "compliant": True,
            "cost_elsewhere": ("Higher-grade concrete needs tighter batching control and a "
                               "shorter placing window on site."),
        })

    waste = waste_reduction(project, analysis, eng)
    if waste.get("steel_saving_inr", 0) > 0:
        levers.append({
            "lever": "Cut reinforcement to a bar-bending schedule",
            "saving": round(waste["steel_saving_inr"], 0), "compliant": True,
            "cost_elsewhere": "Needs the bar schedule issued before the steel order, not after.",
        })

    # Adders are a share of works cost, so they fall with it -- but they are contractual,
    # not physical, and cutting them is a negotiation rather than a design change.
    adders_pct = sum(float(a.get("pct") or 0) for a in boq.get("adders", []))
    if adders_pct:
        levers.append({
            "lever": f"Renegotiate preliminaries and overheads ({round(adders_pct, 1)}% of works)",
            "saving": round(float(boq.get("adders_total") or 0) * 0.2, 0), "compliant": True,
            "cost_elsewhere": "Contractual, not a design change. Assumes a fifth comes off.",
        })

    levers.sort(key=lambda x: -x["saving"])
    gap = current_total - target
    running, taken = 0.0, []
    for lv in levers:
        if running >= gap:
            break
        taken.append(lv)
        running += lv["saving"]

    reached = running >= gap
    notes = []
    if not reached:
        notes.append(
            f'The target is {round((gap - running) / 1e5, 1)} lakh beyond what these levers reach. '
            "Closing it means less building -- fewer floors, smaller units or a lower "
            "specification -- not a cheaper way to build the same thing.")
    notes.append("Savings are additive here because the levers act on different lines. "
                 "Overlapping levers would not simply add.")

    return _result(
        "budget", "Budget optimisation",
        {"value": current_total, "unit": "INR", "label": "Current project cost"},
        {"value": round(current_total - running, 0), "unit": "INR",
         "label": "Reachable with compliant changes"},
        [{"lever": lv["lever"], "from": "current specification",
          "to": "revised", "effect": f'saves about INR {round(lv["saving"]):,}'} for lv in taken],
        options=levers,
        notes=notes,
        feasible=reached,
    ) | {"target": round(target, 0), "gap": round(gap, 0), "reachable": round(running, 0),
         "shortfall": round(max(gap - running, 0), 0)}


# ------------------------------------------------------------------ materials
def material_recommendations(analysis: Dict[str, Any], eng: Dict[str, Any]) -> Dict[str, Any]:
    """Rank real alternatives on cost, embodied carbon and code compliance."""
    q = _priced(analysis)
    cfg = eng.get("config") or {}
    exposure = str(cfg.get("exposure_condition") or "moderate").lower()

    cement_bags = float(q.get("cement", {}).get("quantity") or 0)
    cement_rate = float(q.get("cement", {}).get("rate") or 0)
    brick_qty = float(q.get("bricks", {}).get("quantity") or 0)
    brick_rate = float(q.get("bricks", {}).get("rate") or 0)
    tile_qty = float(q.get("tiles", {}).get("quantity") or 0)
    tile_rate = float(q.get("tiles", {}).get("rate") or 0)
    cf = C.EMBODIED_CARBON

    families = [
        {
            "family": "Cement",
            "current": "OPC 53",
            "options": [
                {"option": "OPC 53", "cost": cement_bags * cement_rate,
                 "carbon_t": cement_bags * cf["cement"]["factor"] / 1000,
                 "compliant": True,
                 "note": "Fastest strength gain; highest clinker content."},
                {"option": "PPC (fly-ash blended)", "cost": cement_bags * cement_rate * 0.96,
                 "carbon_t": cement_bags * cf["cement"]["factor"] * 0.70 / 1000,
                 "compliant": True,
                 "note": "IS 1489 permits PPC for all exposures here. Slower early strength, "
                         "so formwork stays up longer -- check it against the programme."},
                {"option": "PSC (slag blended)", "cost": cement_bags * cement_rate * 0.98,
                 "carbon_t": cement_bags * cf["cement"]["factor"] * 0.55 / 1000,
                 "compliant": exposure in ("severe", "very severe", "moderate"),
                 "note": "IS 455. Best sulphate and chloride resistance; the usual choice "
                         "for coastal and aggressive ground."},
            ],
        },
        {
            "family": "Walling",
            "current": "Burnt clay brick",
            "options": [
                {"option": "Burnt clay brick", "cost": brick_qty * brick_rate,
                 "carbon_t": brick_qty * cf["bricks"]["factor"] / 1000,
                 "compliant": True, "note": "Familiar to every mason; heaviest option."},
                {"option": "AAC block", "cost": brick_qty * brick_rate * 0.92,
                 "carbon_t": brick_qty * cf["bricks"]["factor"] * 0.45 / 1000,
                 "compliant": True,
                 "note": "IS 2185 Part 3. About a third the dead weight, which pulls down "
                         "seismic mass and can shrink foundations. Needs thin-bed mortar "
                         "and a trained gang."},
                {"option": "Fly-ash brick", "cost": brick_qty * brick_rate * 0.88,
                 "carbon_t": brick_qty * cf["bricks"]["factor"] * 0.60 / 1000,
                 "compliant": True,
                 "note": "IS 12894. Cheapest here, and dimensionally truer than clay, so "
                         "plaster thickness drops."},
            ],
        },
        {
            "family": "Flooring",
            "current": "Vitrified tile",
            "options": [
                {"option": "Vitrified tile", "cost": tile_qty * tile_rate,
                 "carbon_t": tile_qty * cf["tiles"]["factor"] / 1000,
                 "compliant": True, "note": "Low porosity, hardest wearing."},
                {"option": "Ceramic tile", "cost": tile_qty * tile_rate * 0.72,
                 "carbon_t": tile_qty * cf["tiles"]["factor"] * 0.75 / 1000,
                 "compliant": True,
                 "note": "Cheaper and lower carbon, but higher porosity -- keep it out of "
                         "balconies and wet areas."},
                {"option": "Polished concrete", "cost": tile_qty * tile_rate * 0.55,
                 "carbon_t": tile_qty * cf["tiles"]["factor"] * 0.30 / 1000,
                 "compliant": True,
                 "note": "No separate finish layer at all. Unforgiving of a bad slab."},
            ],
        },
    ]

    total_cost_now = total_carbon_now = 0.0
    total_cost_best = total_carbon_best = 0.0
    changes = []
    for fam in families:
        cur = next(o for o in fam["options"] if o["option"] == fam["current"])
        legal = [o for o in fam["options"] if o["compliant"]]
        # Rank on carbon saved per rupee spent, so a cheap low-carbon swap outranks an
        # expensive one -- and never recommend something that costs more AND emits more.
        for o in fam["options"]:
            o["cost"] = round(o["cost"], 0)
            o["carbon_t"] = round(o["carbon_t"], 1)
            o["cost_delta"] = round(o["cost"] - cur["cost"], 0)
            o["carbon_delta"] = round(o["carbon_t"] - cur["carbon_t"], 1)
        best = min(legal, key=lambda o: o["cost"] + o["carbon_t"] * CARBON_PRICE_INR_PER_TONNE)
        fam["recommended"] = best["option"]
        total_cost_now += cur["cost"]
        total_carbon_now += cur["carbon_t"]
        total_cost_best += best["cost"]
        total_carbon_best += best["carbon_t"]
        if best["option"] != cur["option"]:
            changes.append({
                "lever": fam["family"], "from": cur["option"], "to": best["option"],
                "effect": (f'INR {abs(best["cost_delta"]):,.0f} '
                           f'{"less" if best["cost_delta"] < 0 else "more"}, '
                           f'{abs(best["carbon_delta"])} tCO2e '
                           f'{"less" if best["carbon_delta"] < 0 else "more"}. {best["note"]}'),
            })

    return _result(
        "materials", "Material recommendations",
        {"value": round(total_carbon_now, 1), "unit": "tCO2e",
         "label": "Embodied carbon of the current specification"},
        {"value": round(total_carbon_best, 1), "unit": "tCO2e",
         "label": "With the recommended alternatives"},
        changes, options=families,
        notes=[f"Ranked on cost plus carbon valued at INR {CARBON_PRICE_INR_PER_TONNE:,.0f} "
               "a tonne, and never onto something that costs more and emits more. "
               f"Compliance is checked against this project's exposure condition ({exposure}).",
               "Cost indices are relative to the rates already in the bill, not fresh quotes."],
    ) | {"cost_now": round(total_cost_now, 0), "cost_best": round(total_cost_best, 0),
         "cost_delta": round(total_cost_best - total_cost_now, 0)}


def analyse(project: Dict[str, Any], analysis: Dict[str, Any], eng: Dict[str, Any],
            target_budget: float = 0.0) -> Dict[str, Any]:
    """Every quantity, cost and material optimiser, in one payload."""
    return {
        "ok": True,
        "waste": waste_reduction(project, analysis, eng),
        "quantity": quantity_optimisation(project, analysis, eng),
        "budget": budget_optimisation(project, analysis, eng, target_budget),
        "materials": material_recommendations(analysis, eng),
        "currency": analysis["cost"].get("currency", "INR"),
    }
