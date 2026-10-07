"""AI Civil Engineering Operating System Engine.

Provides:
  - Engineering Knowledge Graph (semantic code & member linkages)
  - Engineering Memory (episodic design reasoning & iteration tracking)
  - Engineering Decision Log (immutable audit trail with confidence scoring)
  - Decision Sandbox & What-If Simulator (real-time structural/cost/FAR deltas)
  - Engineering Benchmarking (CPWD and national standards comparative analysis)
"""

from typing import Dict, Any, List, Optional
import hashlib
from datetime import datetime


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# --------------------------------------------------------------------------- 1. Engineering Knowledge Graph

def _analysis(project: Dict[str, Any]) -> Dict[str, Any]:
    import engine
    return engine.analyse(project)


def _eng_cfg(project: Dict[str, Any]) -> Dict[str, Any]:
    import engineering
    return {**engineering.DEFAULT_ENGINEERING, **(project.get("engineering") or {})}


def build_knowledge_graph(project: Dict[str, Any]) -> Dict[str, Any]:
    """Knowledge graph linking the governing standards to THIS project's parameters.

    Every parameter and component node is read from the project (city -> seismic zone,
    engineering settings -> concrete grade and slab, towers -> height), not a fixed template.
    """
    import iscodes
    towers = project.get("towers") or []
    e = _eng_cfg(project)
    city = iscodes.city_reference(e.get("city"), e.get("state"))
    zone = str(city.get("zone") or "II")
    height = max((int(t.get("floors") or 0) * float(t.get("floor_height") or t.get("floor_height_m") or 3.0)
                  for t in towers), default=0.0)
    try:
        a = _analysis(project)
        far_txt = f"FAR {a['areas']['far']:.2f}"
        perm = (a.get("far_derivation") or {}).get("permissible") or {}
        cap = perm.get("far_cap") if isinstance(perm, dict) else perm
        far_txt += f" of {cap:g} permitted" if isinstance(cap, (int, float)) else ""
    except Exception:
        far_txt = "FAR (incomplete project data)"
    grade = int(float(e.get("concrete_grade") or 25))
    slab = int(float(e.get("slab_thickness_mm") or 125))
    ductile = zone in ("III", "IV", "V")
    basement = int(((project.get("parking") or {}).get("basement_levels")) or 0)

    nodes = [
        {"id": "CODE-IS456", "label": "IS 456:2000", "type": "standard", "category": "Structural Concrete", "status": "Active"},
        {"id": "CODE-IS1893", "label": "IS 1893 (Part 1):2016", "type": "standard", "category": "Earthquake Resistant Design", "status": "Active"},
        {"id": "CODE-IS13920", "label": "IS 13920:2016", "type": "standard", "category": "Ductile Detailing",
         "status": "Active" if ductile else f"Not mandatory in Zone {zone}"},
        {"id": "CODE-NBC-P3", "label": "NBC 2016 Part 3", "type": "standard", "category": "Development Control", "status": "Active"},
        {"id": "CODE-NBC-P4", "label": "NBC 2016 Part 4", "type": "standard", "category": "Fire & Life Safety", "status": "Active"},
        {"id": "PARAM-FAR", "label": far_txt, "type": "parameter", "category": "Zoning"},
        {"id": "PARAM-HEIGHT", "label": f"Building Height ({height:.1f} m)", "type": "parameter", "category": "Geometry"},
        {"id": "PARAM-SETOFF", "label": "Front & Rear Setbacks", "type": "parameter", "category": "Statutory Envelope"},
        {"id": "PARAM-ZONE", "label": f"Seismic Zone {zone} ({city.get('city')})", "type": "parameter", "category": "Geotechnical & Hazard"},
        {"id": "COMP-COLUMNS", "label": f"RCC Columns (M{grade})", "type": "component", "category": "Substructure & Frame"},
        {"id": "COMP-SLAB", "label": f"RCC Slab ({slab} mm)", "type": "component", "category": "Floor System"},
        {"id": "COMP-FIRE-EGRESS", "label": "Protected Stairways", "type": "component", "category": "Life Safety"},
    ]
    if basement:
        nodes.append({"id": "COMP-BASEMENT", "label": f"Basement Parking ({basement} level{'s' if basement > 1 else ''})",
                      "type": "component", "category": "Substructure"})
    edges = [
        {"source": "CODE-NBC-P3", "target": "PARAM-FAR", "relation": "governs", "weight": 1.0},
        {"source": "CODE-NBC-P3", "target": "PARAM-SETOFF", "relation": "mandates", "weight": 0.9},
        {"source": "CODE-NBC-P4", "target": "COMP-FIRE-EGRESS", "relation": "enforces", "weight": 1.0},
        {"source": "CODE-IS456", "target": "COMP-COLUMNS", "relation": "specifies_mix", "weight": 0.95},
        {"source": "CODE-IS456", "target": "COMP-SLAB", "relation": "governs_deflection", "weight": 0.85},
        {"source": "CODE-IS1893", "target": "PARAM-ZONE", "relation": "classifies", "weight": 1.0},
        {"source": "PARAM-ZONE", "target": "COMP-COLUMNS", "relation": "sets_lateral_demand", "weight": 0.9},
        {"source": "PARAM-FAR", "target": "PARAM-HEIGHT", "relation": "constrains", "weight": 0.8},
    ]
    if ductile:
        edges.append({"source": "CODE-IS13920", "target": "COMP-COLUMNS", "relation": "mandates_confinement", "weight": 0.9})
    if basement:
        edges.append({"source": "PARAM-SETOFF", "target": "COMP-BASEMENT", "relation": "delimits_podium", "weight": 0.75})
    n = len(nodes)
    return {
        "ok": True,
        "nodes": nodes,
        "edges": edges,
        "metrics": {
            "total_nodes": n,
            "total_relationships": len(edges),
            "governing_standards_count": sum(1 for x in nodes if x["type"] == "standard"),
            "graph_density": round(len(edges) / (n * (n - 1)), 3) if n > 1 else 0.0,
        },
    }


# --------------------------------------------------------------------------- 2. Engineering Memory

DOMAINS = (("approval", "Approvals"), ("conversational", "Design Changes"), ("one_click", "Scheme Generation"),
           ("share", "Team & Access"), ("schedule", "Programme"), ("finance", "Finance"),
           ("site", "Site & Layout"), ("engineering", "Engineering"), ("created", "Project Setup"),
           ("updated", "Design Changes"), ("version", "Versions"))


def _domain(action: str) -> str:
    a = (action or "").lower()
    return next((d for key, d in DOMAINS if key in a), "General")


def _title(action: str) -> str:
    return (action or "activity").replace("project.", "").replace("_", " ").replace(".", " ").strip().capitalize()


def get_engineering_memory(project: Dict[str, Any], activity: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The project's design memory: what was changed, when and by whom, from its activity log.

    It used to return four fixed, back-dated "episodes" (M40 columns, low-E glazing...) that
    had never happened on the project. Now it only remembers what did.
    """
    project_id = str(project.get("_id", ""))
    rows = sorted(activity or [], key=lambda r: str(r.get("at") or ""))
    episodes = [{
        "id": f"MEM-{i:03d}",
        "timestamp": r.get("at"),
        "domain": _domain(r.get("action")),
        "title": _title(r.get("action")),
        "context": r.get("detail") or "",
        "rationale": r.get("detail") or "",
        "confidence_score": None,
        "author": r.get("user_name") or "unknown",
    } for i, r in enumerate(rows, 1)]
    counts: Dict[str, int] = {}
    for ep in episodes:
        counts[ep["domain"]] = counts.get(ep["domain"], 0) + 1
    return {
        "ok": True,
        "data_source": "activity_log",
        "project_id": project_id,
        "total_episodes": len(episodes),
        "episodes": episodes,
        "memory_summary": {
            "key_tradeoffs_resolved": len(episodes),
            "avg_confidence": None,
            "dominant_domain": max(counts, key=counts.get) if counts else None,
        },
    }


# --------------------------------------------------------------------------- 3. Engineering Decision Log

def audit_decision_log(project: Dict[str, Any], activity: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Tamper-evident log of the project's recorded actions.

    Each entry's hash covers the previous entry's hash, so altering or deleting any past
    entry changes every hash after it -- a hash chain, not a single checksum. The fixed,
    invented decisions the log used to show are gone.
    """
    project_id = str(project.get("_id", ""))
    rows = sorted(activity or [], key=lambda r: str(r.get("at") or ""))
    decisions, prev = [], hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    for i, r in enumerate(rows, 1):
        payload = "|".join([prev, str(r.get("at") or ""), str(r.get("user_id") or ""),
                            str(r.get("action") or ""), str(r.get("detail") or "")])
        prev = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        action = str(r.get("action") or "")
        decisions.append({
            "id": f"DEC-{i:04d}",
            "category": _domain(action),
            "decision": _title(action),
            "justification": r.get("detail") or "",
            "status": "APPROVED" if "approve" in action.lower() else "RECORDED",
            "confidence_pct": None,
            "agent_signoff": r.get("user_name") or "unknown",
            "timestamp": r.get("at"),
            "entry_hash": prev[:16].upper(),
        })
    return {
        "ok": True,
        "data_source": "activity_log",
        "project_id": project_id,
        "audit_hash": f"APT-DEC-LOG-{prev[:16].upper()}" if decisions else None,
        "total_decisions": len(decisions),
        "decisions": decisions,
        "all_approved": bool(decisions) and all(d["status"] == "APPROVED" for d in decisions),
    }


# --------------------------------------------------------------------------- 4. Decision Sandbox & What-If Simulator

def _carbon_t(analysis: Dict[str, Any]) -> float:
    import iscodes
    total = 0.0
    for it in analysis["quantities"]["items"]:
        c = iscodes.EMBODIED_CARBON.get(it["key"])
        if c:
            total += float(it["quantity"]) * float(c["factor"])
    return total / 1000.0


def simulate_decision_sandbox(project: Dict[str, Any], overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What-if: apply the overrides to a copy of the project and re-run the real engine.

    The baseline and the variant are priced, quantified and checked by the same engine as the
    rest of the platform, so the deltas are differences between two real estimates.
    """
    import copy
    overrides = overrides or {}
    try:
        base = _analysis(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}

    variant = copy.deepcopy(project)
    towers = variant.get("towers") or []
    base_floors = max((int(t.get("floors") or 1) for t in towers), default=1)
    sim_floors = int(overrides.get("floors") or base_floors)
    scale = max(float(overrides.get("footprint_scale") or 1.0), 0.1)
    grade_txt = str(overrides.get("concrete_grade") or f"M{int(float(_eng_cfg(project).get('concrete_grade') or 25))}")
    try:
        grade = int("".join(ch for ch in grade_txt if ch.isdigit()))
    except ValueError:
        grade = 25
    for t in towers:
        t["floors"] = sim_floors
        if scale != 1.0:
            t["footprint_area"] = float(t.get("footprint_area") or t.get("footprint_sqm") or 0) * scale
            # Built-up follows the homes, so a bigger plate carries proportionally more of them.
            for u in t.get("units") or []:
                u["count"] = max(int(round(int(u.get("count") or 0) * scale)), 0)
    variant["engineering"] = {**(variant.get("engineering") or {}), "concrete_grade": grade}
    try:
        sim = _analysis(variant)
    except Exception as exc:
        return {"ok": False, "error": f"Variant could not be analysed: {exc}"}

    def figures(a):
        tk = (a["quantities"].get("takeoff") or {}).get("totals") or {}
        return {"builtup": a["areas"]["builtup_area_sqm"], "far": a["areas"]["far"], "cost": a["cost"]["total"],
                "concrete": tk.get("concrete_m3"), "steel_mt": (tk.get("steel_kg") or 0) / 1000.0,
                "carbon": _carbon_t(a)}
    b, s = figures(base), figures(sim)
    far_rule = next((r for r in sim["compliance"]["results"] if r.get("param") == "far"), None)
    fails = [r["label"] for r in sim["compliance"]["results"] if r.get("status") == "fail"]
    slab = str(overrides.get("slab_type") or "")
    return {
        "ok": True,
        "baseline": {"floors": base_floors, "builtup_sqm": round(b["builtup"], 1), "far": round(b["far"], 2),
                     "estimated_cost_inr": round(b["cost"], 0)},
        "simulation": {
            "floors": sim_floors, "concrete_grade": f"M{grade}", "slab_type": slab or None,
            "builtup_sqm": round(s["builtup"], 1), "achieved_far": round(s["far"], 2),
            "estimated_cost_inr": round(s["cost"], 0),
            "cost_per_sqm_inr": round(s["cost"] / s["builtup"], 0) if s["builtup"] else None,
            "quantities": {"concrete_m3": s["concrete"], "steel_reinforcement_mt": round(s["steel_mt"], 1),
                           "embodied_carbon_tonnes": round(s["carbon"], 1)},
        },
        "deltas": {
            "builtup_area_sqm": round(s["builtup"] - b["builtup"], 1),
            "cost_inr": round(s["cost"] - b["cost"], 0),
            "cost_pct": round((s["cost"] - b["cost"]) / b["cost"] * 100.0, 2) if b["cost"] else 0.0,
            "verdict": ("FAR EXCEEDED" if far_rule and far_rule.get("status") == "fail"
                        else "COMPLIANCE ISSUES" if fails else "FEASIBLE"),
            "failed_checks": fails,
        },
        "notes": ([f"Slab type '{slab}' is not modelled by the take-off; the variant uses the project's RCC slab."]
                  if slab and "rcc" not in slab.lower() else []),
    }


# --------------------------------------------------------------------------- 5. Engineering Benchmarking

# Ranges an Indian QS sense-checks residential RCC against -- the same bands the take-off
# warns on. They are typical ranges, not a CPWD schedule; a cost benchmark is only shown when
# the user enters one, because no single built-in figure is defensible across cities and specs.
BENCHMARK_BANDS = {
    "steel": (28.0, 75.0),
    "concrete": (0.22, 0.48),
    "circulation": (10.0, 20.0),
}


def _band_row(metric, unit, value, lo, hi, basis):
    if value is None:
        status, variance = "NOT AVAILABLE", None
    elif value < lo:
        status, variance = "BELOW TYPICAL RANGE", round((value - lo) / lo * 100.0, 1)
    elif value > hi:
        status, variance = "ABOVE TYPICAL RANGE", round((value - hi) / hi * 100.0, 1)
    else:
        status, variance = "WITHIN RANGE", 0.0
    return {"metric": metric, "unit": unit, "project_value": value, "cpwd_benchmark": lo, "industry_p75": hi,
            "benchmark_basis": basis, "status": status, "variance_pct": variance}


def compare_benchmarks(project: Dict[str, Any]) -> Dict[str, Any]:
    """The project's own take-off, circulation and cost against typical ranges."""
    try:
        a = _analysis(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    ar = a["areas"]
    builtup = float(ar["builtup_area_sqm"] or 0)
    vs = (a["quantities"].get("takeoff") or {}).get("vs_thumb_rule") or {}
    core = sum(float(t.get("service_core_per_floor_sqm") or 0) * int(t.get("floors") or 0) for t in ar["towers"])
    circulation = round(core / builtup * 100.0, 1) if builtup else None
    cost_sqm = round(float(a["cost"]["total"]) / builtup, 0) if builtup else None
    lo_s, hi_s = BENCHMARK_BANDS["steel"]
    lo_c, hi_c = BENCHMARK_BANDS["concrete"]
    lo_k, hi_k = BENCHMARK_BANDS["circulation"]
    rows = [
        _band_row("Reinforcement Steel", "kg/m² built-up", vs.get("steel_kg_per_sqm"), lo_s, hi_s,
                  "Typical Indian residential RCC range"),
        _band_row("Concrete Volume", "m³/m² built-up", vs.get("concrete_m3_per_sqm"), lo_c, hi_c,
                  "Typical Indian residential RCC range"),
        _band_row("Common Circulation (cores and corridors)", "% of built-up", circulation, lo_k, hi_k,
                  "Typical apartment tower range"),
    ]
    user_bench = (project.get("config") or {}).get("cost_benchmark_per_sqm")
    if user_bench:
        ub = float(user_bench)
        rows.append({"metric": "Construction Cost", "unit": "₹/m² built-up", "project_value": cost_sqm,
                     "cpwd_benchmark": ub, "industry_p75": None, "benchmark_basis": "Benchmark entered for this project",
                     "status": "BELOW BENCHMARK" if cost_sqm is not None and cost_sqm <= ub else "ABOVE BENCHMARK",
                     "variance_pct": round((cost_sqm - ub) / ub * 100.0, 1) if cost_sqm is not None else None})
    else:
        rows.append({"metric": "Construction Cost", "unit": "₹/m² built-up", "project_value": cost_sqm,
                     "cpwd_benchmark": None, "industry_p75": None,
                     "benchmark_basis": "No cost benchmark set (config.cost_benchmark_per_sqm)",
                     "status": "NO BENCHMARK", "variance_pct": None})
    judged = [r for r in rows if r["status"] not in ("NOT AVAILABLE", "NO BENCHMARK")]
    good = sum(1 for r in judged if r["status"] in ("WITHIN RANGE", "BELOW BENCHMARK"))
    outside = [r["metric"] for r in judged if r["status"] not in ("WITHIN RANGE", "BELOW BENCHMARK")]
    return {
        "ok": True,
        "total_metrics_evaluated": len(judged),
        "overall_efficiency_score": round(good / len(judged) * 100.0, 1) if judged else None,
        "benchmarks": rows,
        "summary": ("All benchmarked metrics are within their typical ranges." if not outside else
                    "Outside typical range: " + ", ".join(outside) + ". Review the inputs behind them before issue."),
    }
