"""Project state assembled for APT, the in-app assistant.

Two rules govern everything here.

COMPUTED OUTPUTS ONLY. No vertex arrays, no per-floor room layouts, no full activity
lists, no week-by-week cash flow. Those are the biggest things in a project document and
the least useful in a conversation: the assistant is asked "why is the base shear this
number", never "list every slab pour". Sending them would cost thousands of tokens a
message and bury the figures that answer the question.

ROUND ON THE WAY IN. A base shear of 1234.56789012 kN spends tokens on digits that are
noise, and invites the model to quote a precision the engine never claimed.

Retrieved clause text is the one exception to the first rule, and it earns it. The
registry hands the assistant clause NUMBERS; the alternative to also handing it the
passage is an assistant that reconstructs what the clause says from memory, fluently and
wrongly. Verbatim code text is the only raw text here worth its tokens, and it is capped
so that it stays a citation rather than becoming the context.

The builders here are also the ones the single-shot AI reports use, so the assistant and
the reports can never describe the same project differently.
"""
from typing import Any, Dict, List, Optional

import citations as citelib
import engine
import iscodes as C

# Enough clause entries for the model to cite from a list rather than from memory. The
# whole registry is 62 entries and about 6 kB serialised -- cheap next to the cost of one
# confabulated clause number.
CLAUSE_BUDGET = 0            # 0 = send them all


def _r(v: Any, places: int = 2) -> Any:
    """Round anything numeric, leave everything else alone."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return round(float(v), places)
    if isinstance(v, dict):
        return {k: _r(x, places) for k, x in v.items()}
    if isinstance(v, list):
        return [_r(x, places) for x in v]
    return v


# ------------------------------------------------------------------ shared builders
def project_summary(proj: Dict[str, Any], an: Dict[str, Any]) -> Dict[str, Any]:
    ar = an["areas"]
    return _r({
        "name": proj.get("name"), "client": proj.get("client"),
        "location": proj.get("location"), "status": proj.get("status"),
        "plot_area_sqm": ar["plot_area_sqm"], "plot_area_acres": ar["plot_area_acres"],
        "builtup_area_sqm": ar["builtup_area_sqm"], "carpet_area_sqm": ar["carpet_area_sqm"],
        "super_builtup_area_sqm": ar["super_builtup_area_sqm"],
        "far": ar["far"], "fsi": ar["fsi"],
        "ground_coverage_pct": ar["ground_coverage_pct"],
        "open_space_pct": ar["open_space_pct"], "open_space_sqm": ar["open_space_sqm"],
        "total_units": ar["total_units"], "occupants": ar["occupants"],
        "max_height_m": ar["max_height_m"], "total_floors": ar["total_floors"],
        "density_units_per_acre": ar["density_units_per_acre"],
    })


def tower_summary(proj: Dict[str, Any], an: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per tower. Unit MIX, not the per-floor room layout."""
    src = {t.get("id"): t for t in (proj.get("towers") or [])}
    rows = []
    for tm in an["areas"]["towers"]:
        t = src.get(tm.get("id")) or {}
        rows.append(_r({
            "name": tm.get("name"), "floors": tm.get("floors"),
            "floor_height_m": tm.get("floor_height"), "height_m": tm.get("height_m"),
            "footprint_sqm": tm.get("footprint_sqm"),
            "units_per_floor": tm.get("units_per_floor"), "total_units": tm.get("total_units"),
            "carpet_sqm": tm.get("carpet_sqm"),
            "mix": [{"type": u.get("type"), "per_floor": u.get("count"),
                     "carpet_area_sqm": u.get("carpet_area"),
                     "balcony_area_sqm": u.get("balcony_area")}
                    for u in (t.get("units") or [])],
            "lifts": tm.get("lift_count"), "stairs": tm.get("stair_count"),
            "corridor_width_m": tm.get("corridor_width"),
        }))
    return rows


def compliance_summary(an: Dict[str, Any]) -> Dict[str, Any]:
    """Every rule with its threshold, actual and verdict -- failures first.

    All of them, not just the failures: "why does this pass" is as common a question as
    "why does this fail", and a rule that is absent reads as a rule that was not checked.
    """
    co = an["compliance"]
    rules = [_r({"id": r["id"], "rule": r["label"], "operator": r["operator"],
                 "threshold": r["threshold"], "actual": r["actual"],
                 "unit": r.get("unit"), "status": r["status"],
                 "message": r.get("message")}) for r in co["results"]]
    rules.sort(key=lambda r: 0 if r["status"] == "fail" else 1)
    return {"score_pct": co["score"], "passed": co["passed"], "failed": co["failed"],
            "total": co["total"], "overall": co["overall"], "rules": rules}


NOTE_CHARS = 90          # an output note is a hint, not a paragraph


def _compact(d: Dict[str, Any]) -> Dict[str, Any]:
    """Drop keys with nothing in them.

    Across ~112 engineering outputs, the repeated key NAMES cost more than the values do:
    an empty "unit": "" or "note": null is pure overhead in every one of them. Dropping
    them cut the assembled context by roughly a quarter with nothing lost.
    """
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def engineering_summary(eng: Dict[str, Any]) -> Dict[str, Any]:
    """Each module's outputs and derived values, with the clause each output cites."""
    mods = {}
    for mid, m in (eng.get("modules") or {}).items():
        outputs = []
        for o in (m.get("outputs") or []):
            cl = o.get("clause") or {}
            # One string rather than two keys: "IS 875 (Part 1):1987 Table 1" is what a
            # citation looks like anyway, and it is what the guard parses on the way back.
            ref = " ".join(x for x in (cl.get("code"), cl.get("clause")) if x)
            note = (o.get("note") or "")[:NOTE_CHARS]
            outputs.append(_compact(_r({"label": o["label"], "value": o["value"],
                                        "unit": o.get("unit"), "clause": ref,
                                        "note": note})))
        # `codes` is dropped: every clause reference above already names its standard, so
        # the module-level list repeats them once per module for nothing.
        mods[mid] = _compact({
            "title": m.get("title"), "outputs": outputs,
            "derived": _r(m.get("derived") or {}),
            "recommendation": _compact(_r(m.get("recommendation") or {})),
            "missing_inputs": m.get("missing"),
        })
    return _compact({"config": _r(eng.get("config") or {}),
                     "city_reference": _r(eng.get("city_reference") or {}),
                     "summary": _r(eng.get("summary") or {}),
                     "modules": mods,
                     "warnings": [w.get("text") for w in (eng.get("warnings") or [])][:20]})


def cost_summary(an: Dict[str, Any]) -> Dict[str, Any]:
    """Bill totals and the priced lines -- amounts, not the ratio arithmetic behind them."""
    boq = an["boq"]
    return _r({
        "total": an["cost"]["total"], "per_unit": an["cost"]["per_unit"],
        "per_sqm": an["cost"]["per_sqm"], "currency": an["cost"]["currency"],
        "material_total": boq["material_total"], "labour_total": boq["labour_total"],
        "equipment_total": boq["equipment_total"],
        "works_total": boq["works_total"], "adders_total": boq["adders_total"],
        "materials": [{"item": m["label"], "quantity": m["quantity"], "unit": m["unit"],
                       "rate": m["rate"], "amount": m["amount"], "source": m.get("source")}
                      for m in boq["materials"]],
        "adders": [{"item": a["label"], "pct": a.get("pct"), "amount": a["amount"]}
                   for a in boq.get("adders", [])],
    }, 0)


def utilities_summary(an: Dict[str, Any]) -> Dict[str, Any]:
    return _r(an["utilities"], 1)


def parking_summary(an: Dict[str, Any]) -> Dict[str, Any]:
    pk = an["parking"]
    return _r({k: pk.get(k) for k in
               ("required_slots", "provided_slots", "deficit", "surplus",
                "basement_slots", "ground_slots", "visitor_required", "visitor_provided",
                "ev_required", "ev_provided", "accessible_required", "accessible_provided",
                "total_parking_area_sqm", "efficiency_pct", "all_checks_pass", "norm")}, 1)


def gis_summary(proj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Site indices only. The feature lists and elevation samples stay out."""
    g = proj.get("gis")
    if not g:
        return None
    out = {"terrain": _r({k: (g.get("terrain") or {}).get(k) for k in
                          ("avg_slope_pct", "slope_class", "relief_m", "mean_m")}, 1),
           "flood": _r({k: (g.get("flood") or {}).get(k) for k in ("level", "score")}, 1),
           "suitability": _r(g.get("suitability") or {}, 1),
           "buildability": _r(g.get("buildability") or {}, 1),
           "feature_counts": g.get("feature_counts")}
    sun = g.get("sun") or {}
    if sun.get("facades"):
        out["facades"] = _r([{k: f.get(k) for k in ("facade", "bearing_deg", "sun_hours_equinox")}
                             for f in sun["facades"]], 1)
    solar = g.get("solar") or {}
    if solar:
        out["solar"] = _r({k: solar.get(k) for k in
                           ("installable_kwp", "annual_yield_kwh", "payback_years",
                            "co2_avoided_tonnes_per_yr")}, 1)
    return out


def finance_summary(proj: Dict[str, Any], an: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        import finance as financelib
        f = financelib.analyse(proj, an, proj.get("finance"))
    except Exception:
        return None
    # Headline figures only. The month-by-month cash flow is 30-plus rows and answers
    # nothing a conversation asks.
    return _r({"assumptions": f["config"], "revenue": f["revenue"]["gross"],
               "cost": f["cost"], "profit": f["profit"], "break_even": f["break_even"],
               "timing": f["timing"], "saleable_sqft": f["saleable"]["total_sqft"]}, 0)


def programme_summary(proj: Dict[str, Any], an: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        import schedule as schedlib
        p = schedlib.plan_schedule(proj, an, (proj.get("schedule") or {}), summary=True)
    except Exception:
        return None
    if not p.get("ok"):
        return None
    return _r({k: p.get(k) for k in
               ("start", "finish", "duration_months", "duration_calendar_days",
                "activity_count", "crews", "floats_verified")}, 1)


# The optimiser panels are a UI feature with their own AI prompt. APT gets each
# optimiser's headline and its single biggest lever, which is enough to answer "what
# should I change" and to point at the panel for the rest. Sending all eleven optimisers
# in full was a sixth of the whole context for something a conversation rarely asks about.
EFFECT_CHARS = 60
MAX_LEVERS = 1


def _levers(changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lever, from, to, and a clipped effect.

    The full effect strings are written for a person reading a panel and run to two or
    three sentences each. Across a dozen optimisers that is most of a thousand tokens of
    prose restating numbers the model already has."""
    out = []
    for c in changes[:MAX_LEVERS]:
        eff = (c.get("effect") or "")
        out.append({"lever": c.get("lever"), "from": c.get("from"), "to": c.get("to"),
                    "effect": eff[:EFFECT_CHARS] + ("..." if len(eff) > EFFECT_CHARS else "")})
    return out


def optimiser_summary(proj: Dict[str, Any], an: Dict[str, Any],
                      eng: Dict[str, Any]) -> Dict[str, Any]:
    """Each optimiser's current, best and levers -- never the option grids."""
    out: Dict[str, Any] = {}
    try:
        import optimise as optlib
        for k, v in optlib.analyse(proj, an, eng).items():
            if isinstance(v, dict) and "current" in v:
                out[k] = _r({"current": v["current"], "best": v["best"],
                             "delta": v["delta"], "changes": _levers(v["changes"])}, 1)
    except Exception:
        pass
    try:
        import planopt as planoptlib
        for k, v in planoptlib.analyse(proj, an).items():
            if isinstance(v, dict) and "current" in v:
                out[k] = _r({"current": v["current"], "best": v["best"],
                             "delta": v["delta"], "changes": _levers(v["changes"])}, 1)
    except Exception:
        pass
    return out


def _clauses_in_play(eng: Dict[str, Any]) -> List[Dict[str, str]]:
    """Registry entries this project's modules actually reference, plus every code they
    name. Anything else is a clause about a system this building does not have."""
    cited = set()
    for m in (eng.get("modules") or {}).values():
        for o in (m.get("outputs") or []):
            cl = o.get("clause") or {}
            if cl.get("code"):
                cited.add((cl["code"], cl.get("clause", "")))
        rec = (m.get("recommendation") or {}).get("clause") or {}
        if rec.get("code"):
            cited.add((rec["code"], rec.get("clause", "")))
    rows = [r for r in citelib.registry_context()
            if (r["code"], r["clause"]) in cited]
    return rows or citelib.registry_context()


# A retrieved passage has no natural length -- one clause is a sentence, the next is a
# page of a load table -- and the rest of this context already runs to several kB. Left
# unbounded, one quoted extract would dwarf the project state the assistant is actually
# being asked about, so all of them share this budget and the ones that do not fit are
# dropped.
EXTRACT_CHARS = 4000


def _cut(text: str, limit: int) -> str:
    """Trim to `limit`, backing up to the last sentence, line or word break.

    Cutting mid-word reads as corruption rather than as an ellipsis; the caller marks the
    result truncated so the assistant knows the clause does not end there."""
    head = text[:limit]
    for sep, keep in ((". ", 1), ("\n", 0), (" ", 0)):
        i = head.rfind(sep)
        if i > limit // 2:                       # a boundary near the start is not one
            return head[:i + keep].rstrip()
    return head.rstrip()


def _extracts(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """codesearch hits as quotable passages: the code, the clause, the text, the score.

    The component scores, the chunk ids and the merge bookkeeping stay behind -- they are
    how retrieval decided, and a conversation can do nothing with them. Best-first until
    the budget is spent, and the first one that overruns ends the list: half a clause
    with more clauses after it invites the model to read across the seam."""
    out: List[Dict[str, Any]] = []
    spent = 0
    for h in sorted(hits, key=lambda x: x.get("score") or 0, reverse=True):
        text = (h.get("text") or "").strip()
        room = EXTRACT_CHARS - spent
        if not text or room <= 0:
            break
        row = {"code": h.get("code"), "clause": h.get("clause"),
               "heading": h.get("heading"), "score": h.get("score")}
        if len(text) <= room:
            spent += len(text)
            out.append(_compact(_r({**row, "text": text})))
            continue
        text = _cut(text, room)
        if text:
            out.append(_compact(_r({**row, "text": text, "truncated": True})))
        break
    return out


def build(proj: Dict[str, Any], an: Dict[str, Any], eng: Dict[str, Any],
          revision_diff: Optional[Dict[str, Any]] = None,
          code_extracts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The whole project state APT is given before each message."""
    ctx: Dict[str, Any] = {
        "project": project_summary(proj, an),
        "towers": tower_summary(proj, an),
        "compliance": compliance_summary(an),
        "engineering": engineering_summary(eng),
        "cost": cost_summary(an),
        "parking": parking_summary(an),
        "utilities": utilities_summary(an),
        # The clause registry the assistant must cite FROM. Handing it the list is half of
        # the citation guard; the other half checks the reply against it afterwards.
        # Scoped to the clauses this project's own modules cite -- the full 62-entry
        # registry is mostly about systems this project does not have, and the prompt tells
        # the assistant to say so rather than reach for a clause it was not given.
        "clause_registry": _clauses_in_play(eng),
    }
    # Complementary to clause_registry above, not a replacement for it: the registry is
    # the curated list to cite FROM, these are the passages the corpus actually returned
    # for this question, and the prompt tells them apart.
    for key, value in (("code_extracts", _extracts(code_extracts or [])),
                       ("gis", gis_summary(proj)),
                       ("finance", finance_summary(proj, an)),
                       ("programme", programme_summary(proj, an)),
                       ("revision_diff", revision_diff)):
        if value:
            ctx[key] = value
    opt = optimiser_summary(proj, an, eng)
    if opt:
        ctx["optimisers"] = opt
    return ctx
