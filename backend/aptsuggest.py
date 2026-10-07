"""Opening questions for APT, built from the module the user is looking at.

One generator per workspace module, each drawing on that module's own computed values.
A question that names the number on screen -- "Bricks are the largest line at INR 5.5 Cr,
what is driving it?" -- gets asked; a generic "tell me about the BOQ" does not, because it
reads as filler and the user closes the panel.

Every generator degrades rather than fails: a project without a GIS run, without a
programme or without towers still gets questions, falling back to the general set. A blank
empty state is the one outcome that is never acceptable.
"""
from typing import Any, Callable, Dict, List, Optional

# Module ids as they appear in frontend/src/pages/Workspace.jsx GROUPS.
MODULES = ("plot", "controls", "gis", "planning", "parking", "3d", "calculations",
           "engineering", "boq", "cost", "programme", "finance", "compliance",
           "reports", "collaboration")


def _money(v: float) -> str:
    v = float(v or 0)
    if v >= 1e7:
        return f"INR {v / 1e7:.2f} Cr"
    if v >= 1e5:
        return f"INR {v / 1e5:.2f} L"
    return f"INR {v:,.0f}"


def _rule(an, rid: str):
    return next((r for r in an["compliance"]["results"] if r["id"] == rid), None)


def _failing(an):
    return [r for r in an["compliance"]["results"] if r["status"] == "fail"]


def _tightest(an):
    """The rule passing by the thinnest margin -- the one most worth asking about."""
    passing = [r for r in an["compliance"]["results"] if r["status"] == "pass"]
    if not passing:
        return None
    def margin(r):
        try:
            return abs(float(r["actual"] or 0) - float(r["threshold"] or 0))
        except (TypeError, ValueError):
            return float("inf")
    return min(passing, key=margin)


# ---------------------------------------------------------------- per module
def _plot(proj, an, ctx):
    ar = an["areas"]
    out = [f'The plot is {ar["plot_area_sqm"]:,.0f} m² and {ar["ground_coverage_pct"]}% is '
           "covered. How much of it is actually buildable after setbacks?"]
    far = _rule(an, "far_max")
    if far:
        unused = round(float(far["threshold"]) - float(far["actual"]), 2)
        if unused > 0.01:
            out.append(f'FAR is {far["actual"]} against a cap of {far["threshold"]}. '
                       f'What would it take to use the remaining {unused}?')
    edges = (proj.get("plot") or {}).get("road_edges") or []
    if edges:
        out.append(f'There are {len(edges)} road-facing edges. Which setback governs on each?')
    else:
        out.append("No road edges are marked. How does that affect the setbacks applied?")
    return out


def _controls(proj, an, ctx):
    out = ["Which development control is limiting the height of this scheme?"]
    tight = _tightest(an)
    if tight:
        out.append(f'"{tight["label"]}" passes at {tight["actual"]} against {tight["threshold"]}. '
                   "How much headroom is that really?")
    out.append("If the access road were wider, which limits would move and by how much?")
    return out


def _gis(proj, an, ctx):
    g = proj.get("gis")
    if not g:
        return ["No site analysis has been run yet. What would GIS intelligence tell me "
                "about this plot?"]
    out = []
    flood = (g.get("flood") or {}).get("level")
    if flood:
        out.append(f'Flood risk is rated {flood}. What does that mean for the foundation design?')
    facades = (g.get("sun") or {}).get("facades") or []
    if facades:
        best = max(facades, key=lambda f: f.get("sun_hours_equinox") or 0)
        out.append(f'{best["facade"]} gets the most sun at {best["sun_hours_equinox"]} hours. '
                   "How should that drive the unit layout?")
    slope = (g.get("terrain") or {}).get("avg_slope_pct")
    if slope is not None:
        out.append(f'Average slope is {slope}%. What does that add to excavation and retaining?')
    return out


def _planning(proj, an, ctx):
    ar = an["areas"]
    ratio = (ar["carpet_area_sqm"] / ar["builtup_area_sqm"] * 100) if ar["builtup_area_sqm"] else 0
    out = [f'Carpet is {ratio:.0f}% of built-up area. Why that ratio, and is it typical?']
    towers = proj.get("towers") or []
    units = (towers[0].get("units") if towers else []) or []
    if units:
        out.append("Which unit type earns the most per m² of built-up area?")
    out.append(f'There are {ar["total_units"]} flats today. How many would a 5% bigger '
               "floor plate give?")
    return out


def _parking(proj, an, ctx):
    pk = an["parking"]
    out = [f'{pk["required_slots"]} slots are required against {pk["provided_slots"]} provided. '
           "Which NBC clause sets that rate?"]
    if pk["deficit"]:
        out.append(f'There is a shortfall of {pk["deficit"]} slots. Where is the easiest place '
                   "to find them?")
    else:
        out.append(f'Parking has {pk["surplus"]} spare slots. Is that space better used '
                   "another way?")
    out.append(f'Parking takes {pk["total_parking_area_sqm"]:,.0f} m². How is that split '
               "between basement and podium, and what does each cost?")
    return out


def _calculations(proj, an, ctx):
    ar = an["areas"]
    return [f'FAR comes out at {ar["far"]}. Show me step by step how that is calculated.',
            "Which single input moves the built-up area most?",
            "What tolerance applies to these area figures for a sanction submission?"]


def _engineering(proj, an, ctx):
    eng = ctx.get("engineering")
    out = ["Show me step by step how the seismic base shear was calculated."]
    if eng:
        s = eng.get("summary") or {}
        if s.get("foundation"):
            out.append(f'The recommended foundation is {s["foundation"]}. Why that type here?')
        if s.get("carbon_per_sqm_kg"):
            out.append(f'Embodied carbon is {s["carbon_per_sqm_kg"]} kgCO₂e/m². '
                       "What is driving most of it?")
    return out


def _boq(proj, an, ctx):
    mats = an["boq"]["materials"]
    out = []
    biggest = max(mats, key=lambda m: m["amount"], default=None)
    if biggest:
        out.append(f'{biggest["label"]} is the largest line at {_money(biggest["amount"])}. '
                   "What is driving it?")
    steel = next((m for m in mats if m["key"] == "steel"), None)
    if steel:
        out.append(f'Steel comes to {steel["quantity"]:,.0f} {steel["unit"]} '
                   f'({steel.get("source", "ratio")}). How was that derived?')
    out.append("Where does material waste concentrate, and what would reduce it?")
    return out


def _cost(proj, an, ctx):
    c = an["cost"]
    return [f'Cost per flat is {_money(c["per_unit"])}. What is that made up of?',
            f'Material is {_money(c["material"])} of {_money(c["total"])}. '
            "Which head has the most room to move?",
            "What is the single biggest saving available without breaking the code?"]


def _programme(proj, an, ctx):
    plan = ctx.get("programme")
    if not plan:
        return ["No programme has been generated yet. What would it tell me?"]
    out = [f'The programme finishes {plan.get("finish")}. What is on the critical path?']
    cycle = (plan.get("safety") or {}).get("floor_cycle_days")
    if cycle:
        out.append(f'The floor cycle is {cycle} days. Why can it not be compressed further?')
    out.append("Which phase carries the most spare time?")
    return out


def _finance(proj, an, ctx):
    fin = ctx.get("finance")
    if not fin:
        return ["No feasibility figures yet. What would I need to enter to get an ROI?"]
    out = []
    be = fin.get("break_even") or {}
    if be.get("sale_rate_per_sqft"):
        out.append(f'Break-even is {_money(be["sale_rate_per_sqft"])}/sqft. '
                   "How much headroom is there above that?")
    if (fin.get("profit") or {}).get("irr_pct") is not None:
        out.append(f'IRR is {fin["profit"]["irr_pct"]}%. What is driving it most?')
    out.append("What would a six-month sales delay do to the payback?")
    return out


def _compliance(proj, an, ctx):
    out = []
    failing = _failing(an)
    if failing:
        out.append(f'Why does "{failing[0]["label"]}" fail, and what is the smallest change '
                   "that fixes it?")
        out.append(f'Which clause governs "{failing[0]["label"]}"?')
    else:
        out.append(f'All {an["compliance"]["total"]} rules pass. Which one is closest to failing?')
        tight = _tightest(an)
        if tight:
            out.append(f'Which clause sets the {tight["label"]} limit of {tight["threshold"]}?')
    out.append("What would a sanction reviewer question first in this scheme?")
    return out


def _reports(proj, an, ctx):
    return ["Which report should I send the client for a summary?",
            "What goes into the IS/NBC engineering summary?",
            "How current are the numbers in these reports?"]


def _collaboration(proj, an, ctx):
    return ["What changed between the last two revisions?",
            "Which change moved the cost most?",
            "Did compliance improve or get worse in the last revision?"]


GENERATORS: Dict[str, Callable] = {
    "plot": _plot, "controls": _controls, "gis": _gis, "planning": _planning,
    "parking": _parking, "3d": _planning, "calculations": _calculations,
    "engineering": _engineering, "boq": _boq, "cost": _cost,
    "programme": _programme, "finance": _finance, "compliance": _compliance,
    "reports": _reports, "collaboration": _collaboration,
}


def general(proj, an, ctx) -> List[str]:
    """The set used when no module is named, or when a module has nothing notable."""
    out = []
    failing = _failing(an)
    if failing:
        out.append(f'Why does "{failing[0]["label"]}" fail, and what is the smallest change '
                   "that fixes it?")
    else:
        tight = _tightest(an)
        if tight:
            out.append(f'How much headroom is there on "{tight["label"]}"?')
    biggest = max(an["boq"]["materials"], key=lambda m: m["amount"], default=None)
    if biggest:
        out.append(f'{biggest["label"]} is the largest line in the bill at '
                   f'{_money(biggest["amount"])}. What is driving it?')
    plan = ctx.get("programme")
    if plan and plan.get("finish"):
        out.append(f'The programme finishes {plan["finish"]}. What is on the critical path?')
    out.append("Show me step by step how the seismic base shear was calculated.")
    return out


def build(proj: Dict[str, Any], an: Dict[str, Any], module: str = "",
          ctx: Optional[Dict[str, Any]] = None) -> List[str]:
    """Up to four questions for this module, topped up from the general set.

    A generator that produced fewer than three is not a failure -- some modules genuinely
    have little to say about a thin project -- so the general set fills the gap rather
    than leaving a short or empty list.
    """
    ctx = ctx or {}
    out: List[str] = []
    gen = GENERATORS.get(module)
    if gen:
        try:
            out = [q for q in gen(proj, an, ctx) if q]
        except Exception:        # a thin project must never blank the panel
            out = []
    if len(out) < 3:
        for q in general(proj, an, ctx):
            if q not in out:
                out.append(q)
    return out[:4]
