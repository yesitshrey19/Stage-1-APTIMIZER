"""Smart Building Recommendations: one prioritised feed from every module's output.

Each module already makes a recommendation in its own screen — the foundation advisor
names a type, the parking module counts a shortfall, the GIS module flags a flood level.
Scattered across fifteen modules, nothing sums them up, so a user working in Programme
never sees what GIS flagged. This engine walks every computed state, collects the
actionable items, scores each by impact and effort, and returns one ranked list.

Every recommendation carries: where it came from (module + testid anchor so the UI can
deep-link), what to do, why (the numbers behind it), impact, effort and the code or
clause that makes it more than an opinion. Deterministic by design — the same project
always produces the same list — because a recommendation engine that reshuffles on
refresh reads as random, not smart.
"""
from typing import Any, Dict, List
from datetime import datetime, timezone

# Impact 1-5, effort 1-5. Rank = impact / effort: high value for little work first.
_IMPACT = {"critical": 5, "high": 4, "medium": 3, "low": 2}


def _rec(module: str, title: str, action: str, why: str, *, impact: str = "medium",
         effort: int = 2, anchor: str = "", source: str = "", tone: str = "") -> Dict[str, Any]:
    return {
        "module": module,
        "anchor": anchor,
        "source": source or module,
        "title": title,
        "action": action,
        "why": why,
        "impact": impact,
        "effort": effort,
        "rank": round(_IMPACT.get(impact, 3) / max(effort, 1), 2),
        "tone": tone,
    }


def build(project: Dict[str, Any], an: Dict[str, Any], eng: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    recs: List[Dict[str, Any]] = []
    ar = an.get("areas") or {}
    pk = an.get("parking") or {}
    comp = an.get("compliance") or {}
    gis = project.get("gis") or {}
    fin = an.get("finance")
    cf = an.get("capacity_forecast") or {}

    # ---- compliance: failing rules are the hard floor ----
    for r in comp.get("results", []):
        if r.get("status") != "fail":
            continue
        if r.get("param") == "parking_deficit":
            recs.append(_rec(
                "parking", f"Close the parking shortfall of {pk.get('deficit', 0)} slots",
                "Add a basement level, convert surplus open space to surface bays, or trim the unit mix. "
                "Mechanical stackers buy density where land is short.",
                f"{pk.get('required_slots', 0)} ECS required against {pk.get('provided_slots', 0)} provided "
                f"({r.get('message', '')}).",
                impact="critical", effort=3, anchor="parking-supply-section",
                source=r.get("code", "")))
        elif r.get("param") in ("far", "fsi"):
            recs.append(_rec(
                "calculations", f"Built-up exceeds the permissible {'FAR' if r.get('param') == 'far' else 'FSI'}",
                f"Trim floors on the tallest towers or reduce the footprint until {r.get('param')} "
                f"falls from {r.get('actual')} to at most {r.get('threshold')}.",
                "The FAR cap is a statutory maximum — the scheme is not sanctionable as it stands.",
                impact="critical", effort=4, anchor="calc-far-panel", source=r.get("code", "")))
        elif r.get("param") == "ground_coverage_pct":
            recs.append(_rec(
                "planning", "Ground coverage exceeds the bye-law cap",
                f"Reduce tower footprints or podium area from {r.get('actual')}% to at most "
                f"{r.get('threshold')}%, or move mass into additional floors.",
                "Ground coverage caps how much of the plot the building plan occupies.",
                impact="critical", effort=4, anchor="calc-coverage", source=r.get("code", "")))
        else:
            recs.append(_rec(
                "compliance", f"Fix failing rule: {r.get('label')}",
                r.get("message", ""),
                f"{r.get('code')} — the compliance module flags this on every recompute until it passes.",
                impact="critical", effort=3, anchor="compliance-results-section", source=r.get("code", "")))

    # ---- parking surplus: space worth reclaiming ----
    if pk.get("deficit", 1) == 0 and pk.get("surplus", 0) and pk["surplus"] > 12:
        recs.append(_rec(
            "parking", f"Reclaim {pk['surplus']} surplus parking bays",
            "Convert surplus ECS to landscaped court, amenity floor or sellable basement storage — "
            "each bay costs roughly 25-30 m² including aisles.",
            f"{pk['surplus']} bays over the requirement at ~28 m² each is "
            f"{pk['surplus'] * 28:,.0f} m² of floor area doing nothing.",
            impact="medium", effort=2, anchor="parking-supply-section", tone="opportunity"))

    # ---- site intelligence ----
    if gis:
        flood = gis.get("flood") or {}
        if flood.get("level") == "high":
            recs.append(_rec(
                "gis", "Site is flood-exposed — design the plinth for it",
                f"Raise the ground floor to at least {flood.get('plinth_height_m', 0.6)} m and size "
                "storm-water retention for the design cloudburst.",
                "; ".join((flood.get("reasons") or [])[:2]) + ".",
                impact="critical", effort=2, anchor="gis-flood-section"))
        elif flood.get("level") == "moderate":
            recs.append(_rec(
                "gis", "Moderate flood risk — verify drainage before sanction",
                f"Set plinth at {flood.get('plinth_height_m', 0.6)} m and keep electrical/STP above it.",
                "; ".join((flood.get("reasons") or [])[:2]) + ".",
                impact="medium", effort=1, anchor="gis-flood-section"))
        seis = gis.get("seismic") or {}
        for flag in seis.get("risk_flags", []):
            if flag.get("severity") in ("critical", "warning"):
                recs.append(_rec(
                    "gis", flag["title"],
                    flag["detail"],
                    f"IS 1893 zone {seis.get('zone')} — site class {seis.get('site_class')}, "
                    f"screening surface shaking {seis.get('pga_surface_g')} g (PGA Z {seis.get('zone_factor_z')} g).",
                    impact="critical" if flag["severity"] == "critical" else "medium",
                    effort=3, anchor="gis-seismic-section"))
        suit = gis.get("suitability") or {}
        weak = [b for b in suit.get("breakdown", []) if b.get("score", 100) < 50]
        for b in weak[:2]:
            recs.append(_rec(
                "gis", f"Site weakness: {b['factor'].lower()}",
                "Weigh this factor in the site layout — the suitability breakdown scores it "
                f"{b.get('score')}/100 ({b.get('note', '')}).",
                "A low scoring factor in a weighted suitability model is where value hides "
                "when comparing this plot with alternatives.",
                impact="medium", effort=2, anchor="gis-suitability-section"))
        solar = gis.get("solar") or {}
        if solar.get("payback_years") is not None and solar["payback_years"] <= 7:
            recs.append(_rec(
                "gis", "Rooftop solar pays back inside 7 years",
                f"A {solar.get('installable_kwp', 0):.0f} kWp array on the tower terraces saves "
                f"₹{(solar.get('annual_saving_inr') or 0):,.0f} a year. Reserve conduit and "
                "structural capacity at design time.",
                f"Payback {solar.get('payback_years')} years at {solar.get('specific_yield_kwh_per_kwp')} "
                "kWh/kWp — cheapest at construction, expensive to retrofit.",
                impact="medium", effort=2, anchor="gis-solar-section", tone="opportunity"))

    # ---- capacity headroom: the developer's own upside ----
    if cf.get("available") and cf.get("headroom_units", 0) > 0:
        recs.append(_rec(
            "calculations", f"Capacity headroom: up to {cf.get('headroom_pct', 0):.0f}% more units",
            f"The FAR and coverage caps allow about {cf.get('max_units')} units at "
            f"{cf.get('max_units_floors')} floors — {cf.get('headroom_units')} more than the "
            f"{cf.get('current_units')} designed. {cf.get('binding_at_max', 'FAR')} binds first.",
            "Apartment capacity prediction walks the same compliance rules the scheme is "
            "checked against, so the headroom is real, not aspirational.",
            impact="high", effort=3, anchor="calc-capacity-section", tone="opportunity"))

    # ---- engineering: foundation and structural advisories ----
    if eng:
        summary = eng.get("summary") or {}
        if summary.get("foundation"):
            recs.append(_rec(
                "engineering", f"Foundation advisory: {summary['foundation']}",
                "Carry this into the structural detailing stage; the advisor derived it from "
                "soil type, tower loads and seismic zone.",
                "Foundation choice set at concept stage avoids the most expensive redesign there is.",
                impact="medium", effort=1, anchor="eng-foundation-section"))
        for w in (eng.get("warnings") or [])[:3]:
            recs.append(_rec(
                "engineering", "Engineering warning",
                w.get("text", "") if isinstance(w, dict) else str(w),
                "Raised by the IS/NBC engineering modules during analysis.",
                impact="medium", effort=2, anchor="eng-warnings-section"))
        missing = eng.get("missing_inputs") or []
        if missing:
            recs.append(_rec(
                "engineering", f"{len(missing)} engineering input(s) missing",
                f"Fill in: {', '.join(m[:40] for m in missing[:4])}. Missing inputs make the "
                "affected modules fall back to conservative defaults.",
                "Precision guide: defaults widen margins, which silently inflates quantities and cost.",
                impact="low", effort=1, anchor="eng-missing-section"))

    # ---- finance: profitability levers ----
    if fin:
        profit = fin.get("profit") or {}
        irr = profit.get("irr_pct")
        if irr is not None and irr < 15:
            recs.append(_rec(
                "finance", f"IRR of {irr}% is below the 15% developer hurdle",
                "Lift saleable efficiency (carpet/built-up), rephase sales against construction "
                "drawings, or renegotiate the land payment schedule.",
                "Feasibility module: the return does not clear a typical hurdle rate.",
                impact="high", effort=3, anchor="finance-profit-section"))

    recs.sort(key=lambda r: (-r["rank"], r["module"]))
    return recs


_TONE_BY_IMPACT = {"critical": "risk", "high": "watch", "medium": "info", "low": "info"}


def recommendations(project: Dict[str, Any], an: Dict[str, Any],
                   eng: Dict[str, Any] = None) -> Dict[str, Any]:
    """UI-facing feed: build() ranked internally, this shapes it for the screen --
    stable ids for test anchoring, a tone per card, and summary counts."""
    ranked = build(project, an, eng)
    out: List[Dict[str, Any]] = []
    counts = {"opportunities": 0, "risks": 0, "watch": 0, "info": 0}
    for i, r in enumerate(ranked):
        tone = r.get("tone") or _TONE_BY_IMPACT.get(r.get("impact"), "info")
        counts["opportunities" if tone == "opportunity"
               else "risks" if tone == "risk"
               else "watch" if tone == "watch" else "info"] += 1
        out.append({
            "id": f"{r['module']}-{i}",
            "title": r["title"],
            "message": r["action"],
            "evidence": r["why"],
            "tone": tone,
            "category": r["module"],
            "anchor": r["anchor"],
            "impact": r["impact"],
            "effort": r["effort"],
            "rank": r["rank"],
            "source": r["source"],
        })
    return {"summary": counts, "recommendations": out,
            "generated_at": datetime.now(timezone.utc).isoformat()}
