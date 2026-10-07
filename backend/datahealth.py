"""Whether the numbers on screen can be trusted, expressed as data rather than prose.

Every module in the app renders a figure whether or not the inputs behind it were ever
entered. `engine.analyse()` is total by construction -- a missing floor count is a zero,
not an error -- which is the right call for a live-recalculating UI and the wrong one for
a reader deciding whether to sign the drawing. The gap between "computed" and "computed
from something real" is invisible today.

This module makes it visible. Three separate questions, kept apart because a project can
fail one and pass the others:

  COMPLETENESS -- was the input supplied at all, or is a default standing in for it?
  FRESHNESS    -- was the derived artefact (site layout, GIS run, floor plates) produced
                  from the inputs as they are NOW, or from an earlier version of them?
  CONSISTENCY  -- do two stored things that describe the same fact still agree?

Nothing here recomputes the engine's answers or second-guesses them. It reports on the
inputs those answers rest on, so a low score is a statement about the project document,
never about the engineering.

Scores are 0-100 and weighted, because the checks are not equally load-bearing: a project
with no plot boundary has nothing downstream worth reading, whereas a missing finance
assumption costs one module. Weights live beside the checks that carry them.
"""
from typing import Any, Dict, List

import engine
import engineering as englib
import gis as gislib
import layout as layoutlib
from siteplan.version import polygon_signature

# Weight of each input group in the completeness score. The plot dominates because every
# other number in the app is derived from it.
INPUT_WEIGHTS = {
    "plot": 3.0,
    "dev_controls": 2.0,
    "towers": 3.0,
    "site_layout": 2.0,
    "gis": 1.5,
    "parking": 1.0,
    "engineering": 1.5,
    "finance": 1.0,
    "programme": 0.5,
}

SEVERITY_ORDER = ("critical", "warning", "info")


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _plot_check(project: Dict[str, Any]) -> Dict[str, Any]:
    plot = project.get("plot") or {}
    coords = plot.get("coordinates") or []
    checks = [
        ("Boundary drawn", len(coords) >= 3),
        # A placeholder box is a starting point, not a site. Counting it as a real
        # boundary is what lets a whole scheme be read off land the user never owned.
        ("Real boundary, not the placeholder", bool(coords) and not plot.get("is_placeholder")),
        ("Road edges classified", bool(plot.get("road_edges"))),
        ("Orientation set", plot.get("orientation_deg") is not None),
    ]
    return _group("plot", "Plot boundary", checks,
                  detail="Every area, envelope and cost figure is derived from this polygon.")


def _controls_check(project: Dict[str, Any]) -> Dict[str, Any]:
    dc = project.get("dev_controls") or {}
    sb = dc.get("setbacks") or {}
    checks = [
        ("Setbacks stored on the project", bool(sb)),
        ("Front setback set", bool(sb.get("front"))),
        ("Rear setback set", bool(sb.get("rear"))),
        ("Side setback set", bool(sb.get("side"))),
    ]
    return _group("dev_controls", "Setbacks & controls", checks,
                  detail="Unset setbacks fall back to seed defaults, not to your byelaw.")


def _towers_check(project: Dict[str, Any]) -> Dict[str, Any]:
    towers = project.get("towers") or []
    with_units = [t for t in towers if (t.get("units") or [])]
    with_floors = [t for t in towers if int(t.get("floors") or 0) > 0]
    checks = [
        ("At least one tower defined", bool(towers)),
        ("Every tower has a floor count", bool(towers) and len(with_floors) == len(towers)),
        ("Every tower has a unit mix", bool(towers) and len(with_units) == len(towers)),
        ("Floor-to-floor height set", all(float(t.get("floor_height") or 0) > 0 for t in towers) if towers else False),
    ]
    return _group("towers", "Towers & unit mix", checks,
                  detail="Floors and unit mix drive built-up area, loads, parking and cost.")


def _site_layout_check(project: Dict[str, Any]) -> Dict[str, Any]:
    layout = project.get("site_layout") or {}
    packed = layout.get("towers") or []
    checks = [
        ("Site layout generated", bool(layout)),
        ("Layout packed at least one block", bool(packed)),
        ("Roads and amenities reserved", bool(layout.get("roads"))),
        ("Layout reports no hard-constraint violation",
         bool(layout.get("layout_metrics", {}).get("feasible"))),
    ]
    return _group("site_layout", "Site layout engine", checks,
                  detail="Without it the 3D model falls back to an unpacked arrangement.")


def _gis_check(project: Dict[str, Any]) -> Dict[str, Any]:
    g = project.get("gis") or {}
    checks = [
        ("Site analysis run", bool(g)),
        ("Terrain sampled", bool((g.get("terrain") or {}).get("available"))),
        ("Sun path computed", bool(g.get("sun"))),
        ("Surroundings mapped", bool(g.get("features"))),
    ]
    return _group("gis", "GIS site analysis", checks,
                  detail="Seismic, solar and flood context come from here.")


def _parking_check(project: Dict[str, Any]) -> Dict[str, Any]:
    p = project.get("parking") or {}
    checks = [
        ("Parking configured", bool(p)),
        ("Ramp geometry set", bool((p.get("ramp") or {}).get("slope_pct"))),
        ("Basement levels declared", p.get("basement_levels") is not None),
    ]
    return _group("parking", "Parking", checks,
                  detail="Provision, ramp slope and accessible bays are compliance rules.")


def _engineering_check(project: Dict[str, Any], eng: Dict[str, Any]) -> Dict[str, Any]:
    cfg = project.get("engineering") or {}
    missing = eng.get("missing_inputs") or []
    checks = [
        ("Engineering config saved", bool(cfg)),
        ("City / state reference set", bool(cfg.get("city") or cfg.get("state"))),
        ("Soil type declared", bool(cfg.get("soil_type") or cfg.get("soil"))),
        ("No module reporting a missing input", not missing),
    ]
    return _group("engineering", "Engineering inputs", checks,
                  detail="Each unmet input makes one module fall back to an assumed value.")


def _finance_check(project: Dict[str, Any]) -> Dict[str, Any]:
    f = project.get("finance") or {}
    checks = [
        ("Finance assumptions saved", bool(f)),
        ("Sale rate set", bool(f.get("sale_rate") or f.get("sale_rate_per_sqm"))),
    ]
    return _group("finance", "Feasibility assumptions", checks,
                  detail="ROI and IRR are only as real as the rates behind them.")


def _programme_check(project: Dict[str, Any]) -> Dict[str, Any]:
    s = project.get("schedule") or {}
    checks = [
        ("Programme configured", bool(s)),
        ("Target date or start set", bool(s.get("start") or s.get("target_date"))),
    ]
    return _group("programme", "Programme", checks, detail="Drives the headline build duration.")


def _group(key: str, label: str, checks: List[Any], detail: str = "") -> Dict[str, Any]:
    items = [{"label": name, "ok": bool(ok)} for name, ok in checks]
    met = sum(1 for i in items if i["ok"])
    return {
        "key": key,
        "label": label,
        "detail": detail,
        "weight": INPUT_WEIGHTS.get(key, 1.0),
        "met": met,
        "total": len(items),
        "score": _pct(met, len(items)),
        "checks": items,
    }


def _freshness(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Derived artefacts checked against the inputs they were produced from.

    A stale artefact is worse than a missing one: the missing one shows an empty state,
    the stale one shows a confident answer to a question nobody asked any more.
    """
    plot = project.get("plot") or {}
    coords = plot.get("coordinates") or []
    out = []

    layout = project.get("site_layout") or {}
    if layout:
        sig = layout.get("polygon_signature")
        current = polygon_signature(coords) if len(coords) >= 3 else ""
        out.append({
            "key": "site_layout",
            "label": "Site layout vs current boundary",
            "state": "fresh" if (sig and sig == current) else "stale",
            "detail": "Regenerate the layout — it was packed into a different polygon."
                      if sig != current else "Packed from the boundary as it stands.",
        })

    gis = project.get("gis") or {}
    if gis:
        # One staleness rule for the whole app: the boundary moved, or the rules that
        # produced the numbers changed. Checking only the boundary (as this did) reported
        # a GIS artefact as fresh while the GIS module itself flagged it out of date.
        reason = gislib.staleness(gis, coords)
        out.append({
            "key": "gis",
            "label": "GIS analysis vs current boundary & rules",
            "state": "stale" if reason else "fresh",
            "detail": {
                "polygon": "Re-run the site analysis — it was sampled around a different boundary.",
                "rules": "Re-run the site analysis — the detection and scoring rules have "
                         "changed since it ran.",
            }.get(reason, "Sampled around the boundary as it stands, under the current rules."),
        })

    stale_plates = []
    for t in project.get("towers") or []:
        current_hash = layoutlib.unit_mix_hash(t)
        for floor, entry in (t.get("floor_layouts") or {}).items():
            if entry.get("unit_mix_hash") and entry["unit_mix_hash"] != current_hash:
                stale_plates.append(f"{t.get('name', 'Tower')} floor {floor}")
    if project.get("towers"):
        out.append({
            "key": "floor_plates",
            "label": "Floor plates vs unit mix",
            "state": "fresh" if not stale_plates else "stale",
            "count": len(stale_plates),
            "detail": (f"{len(stale_plates)} plate(s) were generated for a different unit mix: "
                       + ", ".join(stale_plates[:4]) + ("…" if len(stale_plates) > 4 else ""))
            if stale_plates else "Every generated plate matches its tower's current unit mix.",
        })

    return out


def _consistency(project: Dict[str, Any], base: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Two stored facts that describe the same thing, checked against each other."""
    out = []
    layout = project.get("site_layout") or {}
    engine_towers = layout.get("towers") or []
    towers = project.get("towers") or []

    if engine_towers:
        eng_floors = [int(t.get("floors") or 0) for t in engine_towers]
        plan_floors = [int(t.get("floors") or 0) for t in towers]
        agree = eng_floors == plan_floors
        out.append({
            "key": "towers_vs_layout",
            "label": "Planning towers vs packed blocks",
            "state": "ok" if agree else "mismatch",
            "detail": (f"Planning has {len(plan_floors)} tower(s) at {plan_floors} floors; the layout "
                       f"packed {len(eng_floors)} at {eng_floors}. Apply the site layout in "
                       f"Apartment Planning.") if not agree else
                      "Planning, the 3D model and the layout describe the same buildings.",
        })

    # The FAR cap is the compliance rule's threshold, not a second copy of it kept on the
    # areas block -- reading it from anywhere else would let the two drift.
    areas = base.get("areas") or {}
    far_rule = next((r for r in (base.get("compliance") or {}).get("results", [])
                     if r.get("param") == "far" and r.get("operator") == "max"), None)
    if far_rule:
        far = float(areas.get("far") or 0)
        cap = float(far_rule.get("threshold") or 0)
        out.append({
            "key": "far",
            "label": "Achieved FAR vs permitted",
            "state": "ok" if far <= cap + 1e-6 else "mismatch",
            "detail": f"{far:.3f} against a permitted {cap:.3f}.",
        })

    return out


def _compliance_margins(base: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Each rule as a signed distance from its threshold, in percent.

    A pass/fail count says three rules failed. This says which ones failed by a hair and
    which are nowhere near, which is the difference between an afternoon's rework and a
    redesign. Positive is slack, negative is the shortfall.
    """
    out = []
    for r in (base.get("compliance") or {}).get("results", []):
        threshold = float(r.get("threshold") or 0)
        actual = float(r.get("actual") or 0)
        if threshold:
            slack = (threshold - actual) / threshold * 100 if r.get("operator") == "max" \
                else (actual - threshold) / threshold * 100
        else:
            slack = 0.0
        out.append({
            "id": r.get("id"),
            "code": r.get("code"),
            "label": r.get("label"),
            "status": r.get("status"),
            "actual": actual,
            "threshold": threshold,
            "unit": r.get("unit"),
            "margin_pct": round(max(min(slack, 200.0), -200.0), 1),
        })
    out.sort(key=lambda x: x["margin_pct"])
    return out


def _module_warnings(eng: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Warning counts per engineering module, split by severity, for a stacked bar."""
    buckets: Dict[str, Dict[str, Any]] = {}
    for mod in (eng.get("modules") or {}).values():
        row = buckets.setdefault(mod["title"], {"module": mod["title"],
                                                **{s: 0 for s in SEVERITY_ORDER},
                                                "missing": len(mod.get("missing") or [])})
        for w in mod.get("warnings") or []:
            sev = w.get("severity") if w.get("severity") in SEVERITY_ORDER else "info"
            row[sev] += 1
    rows = [r for r in buckets.values() if any(r[s] for s in SEVERITY_ORDER) or r["missing"]]
    rows.sort(key=lambda r: (-r["critical"], -r["warning"], -r["missing"]))
    return rows


def report(project: Dict[str, Any]) -> Dict[str, Any]:
    """The whole picture, in one pass over the project document."""
    base = engine.analyse(project)
    eng = englib.analyse_engineering(project, base)

    groups = [
        _plot_check(project),
        _controls_check(project),
        _towers_check(project),
        _site_layout_check(project),
        _gis_check(project),
        _parking_check(project),
        _engineering_check(project, eng),
        _finance_check(project),
        _programme_check(project),
    ]

    weight_total = sum(g["weight"] for g in groups)
    completeness = round(sum(g["score"] * g["weight"] for g in groups) / weight_total, 1) \
        if weight_total else 0.0

    freshness = _freshness(project)
    consistency = _consistency(project, base)
    stale = [f for f in freshness if f["state"] == "stale"]
    mismatched = [c for c in consistency if c["state"] == "mismatch"]

    freshness_score = _pct(len(freshness) - len(stale), len(freshness)) if freshness else 100.0
    consistency_score = _pct(len(consistency) - len(mismatched), len(consistency)) if consistency else 100.0

    comp = base.get("compliance") or {}
    warnings = eng.get("warnings") or []
    critical = [w for w in warnings if w.get("severity") == "critical"]

    # Weighted the same way the three questions differ in cost: an input that was never
    # entered is a hole, a stale artefact is an actively wrong answer, so freshness and
    # consistency together carry as much as completeness.
    overall = round(completeness * 0.5 + freshness_score * 0.3 + consistency_score * 0.2, 1)

    return {
        "ok": True,
        "overall_score": overall,
        "scores": {
            "completeness": completeness,
            "freshness": freshness_score,
            "consistency": consistency_score,
            "compliance": float(comp.get("score") or 0),
        },
        "groups": groups,
        "freshness": freshness,
        "consistency": consistency,
        "compliance_margins": _compliance_margins(base),
        "module_warnings": _module_warnings(eng),
        "missing_inputs": eng.get("missing_inputs") or [],
        "counts": {
            "stale": len(stale),
            "mismatched": len(mismatched),
            "critical_warnings": len(critical),
            "total_warnings": len(warnings),
            "missing_inputs": len(eng.get("missing_inputs") or []),
            "rules_passed": comp.get("passed") or 0,
            "rules_failed": comp.get("failed") or 0,
        },
    }
