"""Urban Intelligence & Sustainability Engine.

Provides:
  - Land value outlook (from the project's own land cost and a stated growth assumption)
  - Climate & multi-hazard summary (city reference data and the GIS flood assessment)
  - Road noise estimate at the facade and air-quality guidance
  - Green building scorecard (the Engineering module's GRIHA / IGBC checklist)
  - ESG carbon summary (the Engineering module's embodied carbon)
  - 30-year lifecycle cost (capex from the BOQ, discounted opex and replacements)
  - Executive dashboard KPIs (finance, compliance, carbon and programme results)
  - Equipment plan and delay risks (BOQ equipment and the programme simulation)

Earlier versions returned the same figures for every project -- "IGBC PLATINUM", a POOR
(260) AQI, a metro station 650 m away, IRR 24.8 %. Everything here is now either computed
from the project or reported as not assessed, with the assumptions named.
"""

import math
from typing import Any, Dict

import engine
import iscodes


def _ctx(project: Dict[str, Any]):
    import engineering
    a = engine.analyse(project)
    e = engineering.analyse_engineering(project, a)
    return a, e


def _project_metrics(project: Dict[str, Any]):
    areas = engine.area_metrics(project)
    eng = project.get("engineering") or {}
    plot_area = float(areas.get("plot_area_sqm") or 0.0)
    builtup = float(areas.get("builtup_area_sqm") or 0.0)
    city = str(eng.get("city") or project.get("location") or "")
    return areas, plot_area, builtup, city


def _city(project: Dict[str, Any]) -> Dict[str, Any]:
    eng = project.get("engineering") or {}
    return iscodes.city_reference(eng.get("city") or project.get("location") or "", eng.get("state") or "")


# --------------------------------------------------------------------------- 1. Land Value Outlook

LAND_GROWTH_PCT = 8.5   # assumed annual land appreciation; replace with local market data


def predict_urban_growth_and_value(project: Dict[str, Any]) -> Dict[str, Any]:
    """Land value outlook from the land cost entered in Finance and a stated growth rate.

    No land price is invented: without a land cost the outlook is not computed. Growth
    catalysts (metro lines, ring roads) need an infrastructure-pipeline source, which the
    platform does not have, so none are claimed.
    """
    _, plot_area, _, city = _project_metrics(project)
    finance_cfg = project.get("finance") or {}
    land_cost = float(finance_cfg.get("land_cost") or 0.0)
    rate = (land_cost / plot_area) if land_cost > 0 and plot_area > 0 else None
    projections = []
    if rate:
        value = rate
        for yr in range(1, 6):
            value *= 1.0 + LAND_GROWTH_PCT / 100.0
            projections.append({
                "year": __import__("datetime").date.today().year + yr,
                "projected_land_rate_inr_sqm": round(value, 0),
                "estimated_plot_value_inr": round(value * plot_area, 0),
                "cumulative_gain_pct": round((value - rate) / rate * 100.0, 1),
            })
    return {
        "ok": True,
        "location": city,
        "base_land_rate_inr_sqm": round(rate, 0) if rate else None,
        "estimated_current_plot_value_inr": round(land_cost, 0) if land_cost > 0 else None,
        "five_year_cagr_pct": LAND_GROWTH_PCT if rate else None,
        "appreciation_projections": projections,
        "growth_catalysts": [],
        "infrastructure_capacity_index": None,
        "note": ("Outlook uses the land cost entered in Finance and an assumed "
                 f"{LAND_GROWTH_PCT}% annual growth." if rate else
                 "Enter the land cost in Finance to project land value. No infrastructure pipeline "
                 "data is connected, so no growth catalysts are claimed."),
    }


# --------------------------------------------------------------------------- 2. Climate & Multi-Hazard Summary

def analyze_climate_and_disasters(project: Dict[str, Any]) -> Dict[str, Any]:
    """Hazards from the city reference (IS 1893 zone, IS 875 wind, rainfall) and the GIS flood check."""
    _, _, _, city = _project_metrics(project)
    ref = _city(project)
    zone = str(ref.get("zone") or "II")
    z = iscodes.ZONE_FACTOR.get(zone, 0.10)
    vb = float(ref.get("wind_speed") or 0) or None
    rain = float(ref.get("rain_intensity_mm_hr") or 0) or None
    flood = (project.get("gis") or {}).get("flood") or {}
    zone_labels = {"II": "Low", "III": "Moderate", "IV": "Severe", "V": "Very Severe"}
    seismic_risk = {"II": "LOW", "III": "MODERATE", "IV": "HIGH", "V": "VERY HIGH"}.get(zone, "MODERATE")
    wind_risk = None if vb is None else "HIGH" if vb >= 50 else "MODERATE" if vb >= 44 else "LOW"
    flood_risk = str(flood.get("level") or "").upper() or None
    hazards = [
        {"hazard_type": "Seismic Hazard", "zone": f"Zone {zone} ({zone_labels.get(zone, '')} seismic intensity)",
         "peak_ground_acceleration_pga": f"Z = {z} (IS 1893 zone factor)", "risk_level": seismic_risk,
         "structural_mitigation": ("IS 13920 ductile detailing is mandatory in this zone." if zone in ("III", "IV", "V")
                                   else "Design to IS 1893; ductile detailing advisable for tall towers.")},
        {"hazard_type": "Urban Flooding & Inundation",
         "zone": flood.get("summary") or ("From GIS site analysis" if flood else "Not assessed - run GIS site analysis"),
         "peak_rainfall_intensity": f"{rain:g} mm/hr design intensity" if rain else None,
         "risk_level": flood_risk,
         "structural_mitigation": "; ".join(flood.get("reasons") or []) or "Set plinth above the highest recorded flood level."},
        {"hazard_type": "Wind & Cyclone Hazard", "zone": f"Basic wind speed Vb = {vb:g} m/s" if vb else None,
         "design_wind_pressure": f"{round(0.6 * vb ** 2 / 1000.0, 2)} kN/m² basic velocity pressure" if vb else None,
         "risk_level": wind_risk, "structural_mitigation": "Design cladding and frames to IS 875 (Part 3)."},
        {"hazard_type": "Extreme Heat", "zone": None, "peak_summer_temp": None, "risk_level": None,
         "structural_mitigation": "Not assessed - no climate dataset connected."},
    ]
    levels = {"LOW": 90, "MODERATE": 70, "HIGH": 45, "VERY HIGH": 25}
    known = [levels[h["risk_level"]] for h in hazards if h["risk_level"] in levels]
    score = round(sum(known) / len(known), 1) if known else None
    return {
        "ok": True,
        "city": ref.get("city") or city,
        "composite_resilience_score": score,
        "resilience_rating": (None if score is None else "RESILIENT" if score >= 75 else
                              "MODERATE" if score >= 55 else "VULNERABLE"),
        "score_basis": "Average of the assessed hazards (low 90, moderate 70, high 45, very high 25)",
        "hazards": hazards,
        "annual_solar_irradiation_kwh_m2": None,
        "prevailing_wind_direction": None,
    }


# --------------------------------------------------------------------------- 3. Noise & Air Quality

ROAD_NOISE_DBA = [(12.0, 68.0), (18.0, 72.0), (30.0, 75.0), (999.0, 78.0)]   # indicative kerb Leq by road width
CPCB_RESIDENTIAL_DAY_DBA = 55.0


def analyze_noise_and_pollution(project: Dict[str, Any]) -> Dict[str, Any]:
    """Facade noise from road width and front setback (point-source spreading); AQI is not invented."""
    plot = project.get("plot") or {}
    road_width = float(plot.get("road_width_m") or (project.get("engineering") or {}).get("road_width") or 18.0)
    sb = ((project.get("dev_controls") or {}).get("setbacks") or {}).get("front")
    if isinstance(sb, dict):
        sb = sb.get("minimum_m")
    distance = float(sb) if sb else 6.0
    kerb = next(db for w, db in ROAD_NOISE_DBA if road_width <= w)
    facade = round(kerb - 20 * math.log10(max(1.0, distance / 1.0)) / 2.0, 1)  # line source: -3 dB per doubling
    exceeds = facade > CPCB_RESIDENTIAL_DAY_DBA
    return {
        "ok": True,
        "noise_analysis": {
            "curb_noise_level_dba": kerb,
            "front_facade_noise_dba": facade,
            "cpcb_daytime_residential_norm_dba": CPCB_RESIDENTIAL_DAY_DBA,
            "acoustic_glazing_recommendation": ("Acoustic glazing for road-facing habitable rooms." if exceeds
                                                else "Standard glazing adequate on this estimate."),
            "compliance_status": (f"EXCEEDS the {CPCB_RESIDENTIAL_DAY_DBA:g} dB(A) daytime norm at the facade - mitigation needed"
                                  if exceeds else "Within the daytime residential norm"),
            "basis": f"Indicative kerb level for an {road_width:g} m road, line-source spreading over {distance:g} m.",
        },
        "air_quality_analysis": {
            "ambient_pm25_ug_m3": None,
            "ambient_pm10_ug_m3": None,
            "site_air_quality_index_aqi": None,
            "mitigation_strategy": [
                "Check the nearest CPCB / SPCB monitoring station for the site's PM2.5 and PM10.",
                "Evergreen tree belt along the road frontage.",
                "Dust suppression and screening during construction.",
            ],
        },
    }


# --------------------------------------------------------------------------- 4. Green Building Scorecard

def calculate_green_building_scorecard(project: Dict[str, Any], standard: str = "IGBC") -> Dict[str, Any]:
    """The Engineering module's green checklist: credits ticked or auto-credited for this project."""
    try:
        _, e = _ctx(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    g = e["modules"]["green"]
    cats = g.get("categories") or []
    items = g.get("items") or []
    categories = []
    for c in cats:
        done = [i["label"] for i in items if i.get("category") == c["category"] and i.get("checked")]
        categories.append({"category": c["category"], "max_points": c["total"], "awarded_points": c["earned"],
                           "highlights": ", ".join(done) if done else "No credits claimed yet"})
    earned = sum(c["awarded_points"] for c in categories)
    total = sum(c["max_points"] for c in categories)
    rating = next((o["value"] for o in g.get("outputs") or [] if "IGBC" in o["label"] or "GRIHA" in o["label"]
                   if (standard.upper() in o["label"].upper())), None)
    return {
        "ok": True,
        "standard": standard,
        "total_points_achieved": earned,
        "max_possible_points": total,
        "score_pct": round(earned / total * 100.0, 1) if total else 0.0,
        "certification_tier": rating or (g.get("recommendation") or {}).get("value"),
        "categories": categories,
        "estimated_energy_savings_pct": None,
        "estimated_potable_water_reduction_pct": None,
        "note": "Tick the credits the design will deliver in Engineering > Green Building to raise the score.",
    }


# --------------------------------------------------------------------------- 5. ESG Carbon Summary

# Construction-phase operational emissions, per m² built-up: site diesel (scope 1) and
# grid power during the build (scope 2). Stated assumptions, not measured values.
SCOPE1_T_PER_SQM = 0.015
SCOPE2_T_PER_SQM = 0.045


def generate_esg_report(project: Dict[str, Any]) -> Dict[str, Any]:
    """Carbon from the Engineering module's embodied carbon take-off; social and governance
    metrics are left for the project's own records rather than asserted."""
    try:
        a, e = _ctx(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    builtup = float(a["areas"]["builtup_area_sqm"] or 0)
    scope3 = float((e["modules"]["carbon"].get("derived") or {}).get("total_tco2e") or 0)
    s1, s2 = round(builtup * SCOPE1_T_PER_SQM, 1), round(builtup * SCOPE2_T_PER_SQM, 1)
    total = round(s1 + s2 + scope3, 1)
    not_set = "Not assessed - record from project documents"
    return {
        "ok": True,
        "reporting_framework": "GRI Standards & BRSR (Business Responsibility & Sustainability Reporting)",
        "carbon_accounting_tco2e": {
            "scope_1_direct_emissions": s1,
            "scope_2_electricity_emissions": s2,
            "scope_3_embodied_materials": round(scope3, 1),
            "total_footprint_tco2e": total,
            "carbon_intensity_tco2e_per_m2": round(total / builtup, 3) if builtup else None,
            "basis": (f"Scope 3 from the take-off (Engineering > Embodied Carbon); scopes 1 and 2 assumed at "
                      f"{SCOPE1_T_PER_SQM} and {SCOPE2_T_PER_SQM} tCO2e/m² for the build."),
        },
        "social_metrics": {"worker_welfare_compliance_pct": None, "onsite_creche_and_medical_aid": not_set,
                           "zero_accident_policy_enforcement": not_set},
        "governance_metrics": {"anti_bribery_vendor_clauses": not_set, "statutory_rera_escrow_segregation": not_set,
                               "whistleblower_and_audit_log": "Hash-chained project decision log available"},
        "esg_composite_rating": None,
    }


# --------------------------------------------------------------------------- 6. 30-Year Lifecycle Cost

OPEX_INR_PER_SQM_YR = 650.0       # energy, water, security and upkeep of common areas (assumption)
LCC_DISCOUNT_RATE = 0.08
REHAB_SCHEDULE = [(8, "Facade repainting & sealant replacement", 0.02), (12, "Pumps and MEP overhaul", 0.035),
                  (15, "Lift modernisation", 0.045), (20, "Solar inverter & battery refresh", 0.015),
                  (24, "Building services rehabilitation", 0.05)]
SALVAGE_SHARE = 0.22


def calculate_lifecycle_cost(project: Dict[str, Any], years: int = 30) -> Dict[str, Any]:
    """Capex from the BOQ; opex, replacements and salvage on stated assumptions; every cash
    flow discounted in the NPV (the old NPV left replacements and salvage out)."""
    try:
        a = engine.analyse(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    years = max(1, min(int(years or 30), 60))
    builtup = float(a["areas"]["builtup_area_sqm"] or 0)
    capex = float(a["cost"]["total"] or 0)
    opex = builtup * OPEX_INR_PER_SQM_YR
    rehab = [{"year": y, "item": item, "cost_inr": round(capex * share, 0)} for y, item, share in REHAB_SCHEDULE if y <= years]
    salvage = capex * SALVAGE_SHARE
    total_opex = opex * years
    total_rehab = sum(r["cost_inr"] for r in rehab)
    total = capex + total_opex + total_rehab - salvage
    d = LCC_DISCOUNT_RATE
    npv = (capex + sum(opex / (1 + d) ** y for y in range(1, years + 1))
           + sum(r["cost_inr"] / (1 + d) ** r["year"] for r in rehab) - salvage / (1 + d) ** years)
    return {
        "ok": True,
        "analysis_horizon_years": years,
        "initial_capital_expenditure_capex_inr": round(capex, 0),
        "cumulative_operational_expenditure_30yr_inr": round(total_opex, 0),
        "periodic_rehabilitation_cost_inr": round(total_rehab, 0),
        "estimated_salvage_value_inr": round(salvage, 0),
        "total_lifecycle_cost_inr": round(total, 0),
        "net_present_value_npv_inr": round(npv, 0),
        "rehabilitation_schedule": rehab,
        "cost_ratio_capex_vs_opex": (f"{round(capex / total * 100.0, 1)}% Capex / {round(total_opex / total * 100.0, 1)}% Opex"
                                     if total else None),
        "assumptions": f"Opex ₹{OPEX_INR_PER_SQM_YR:g}/m²/yr; discount {LCC_DISCOUNT_RATE:.0%}; salvage {SALVAGE_SHARE:.0%} of capex.",
    }


# --------------------------------------------------------------------------- 7. Executive Dashboard KPIs

def get_executive_dashboard_kpis(project: Dict[str, Any]) -> Dict[str, Any]:
    """Headline figures straight from the Finance, Compliance and Engineering results."""
    import finance
    try:
        a, e = _ctx(project)
        f = finance.analyse(project, a, project.get("finance") or {})
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    comp = a["compliance"]
    carbon = (e["modules"]["carbon"].get("derived") or {})
    green = calculate_green_building_scorecard(project)
    warn_fields = {w.get("field") for w in f.get("warnings") or []}
    fails = comp.get("failed", 0)
    health = ("COMPLIANCE ISSUES" if fails else "INPUTS INCOMPLETE" if "land_cost" in warn_fields else "ON TRACK")
    cfg = f["config"]
    equity = f["cost"]["total"] * (1 - float(cfg.get("debt_ratio") or 0) / 100.0)
    kpis = {
        "project_health_status": health,
        "project_internal_rate_of_return_irr_pct": f["profit"]["irr_pct"],
        "equity_multiple": round((equity + f["profit"]["net"]) / equity, 2) if equity > 0 else None,
        "gross_development_value_gdv_inr": f["revenue"]["gross"],
        "total_capex_budget_inr": f["cost"]["total"],
        "projected_gross_margin_inr": f["profit"]["net"],
        "projected_gross_margin_pct": f["profit"]["margin_pct"],
        "statutory_clearance_index_pct": round(comp["passed"] / comp["total"] * 100.0, 1) if comp.get("total") else None,
        "structural_safety_factor": None,
        "carbon_intensity_rating": (f"{carbon.get('per_sqm_kg')} kgCO2e/m² ({carbon.get('band')})"
                                    if carbon.get("per_sqm_kg") is not None else None),
        "green_certification_target": green.get("certification_tier"),
        "construction_schedule_status": "No progress recorded",
        "warnings": [w["text"] for w in f.get("warnings") or []],
    }
    return {"ok": True, "project_name": project.get("name", ""), "kpis": kpis}


# --------------------------------------------------------------------------- 8. Equipment Plan & Delay Risk

def schedule_equipment_and_risks(project: Dict[str, Any]) -> Dict[str, Any]:
    """Equipment from the BOQ; delay risks and buffer from the programme's Monte Carlo run."""
    import digital_twin
    try:
        a = engine.analyse(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    equipment = [{"equipment": q["label"], "allocated_qty": None, "status": "PLANNED (BOQ)",
                  "utilization_pct": None, "equipment_days": round(q["quantity"], 1)} for q in a["boq"]["equipment"]]
    sim = digital_twin.predict_delays(project)
    if not sim.get("ok"):
        return {"ok": True, "equipment_fleet": equipment, "identified_delay_risks": [],
                "recommended_float_buffer_days": None, "schedule_confidence_index_pct": None,
                "note": sim.get("error")}
    from datetime import date
    base = date.fromisoformat(sim["baseline_completion_date"])
    p80 = date.fromisoformat(sim["p80_completion_date"])
    risks = [{"factor": r["factor"], "probability": f"{r['probability_pct']}% of runs on the driving path",
              "impact_days": r["impact_days"], "mitigation": r["mitigation"]} for r in sim["risk_factors_analyzed"]]
    return {
        "ok": True,
        "equipment_fleet": equipment,
        "identified_delay_risks": risks,
        "recommended_float_buffer_days": max((p80 - base).days, 0),
        "schedule_confidence_index_pct": sim["on_time_probability_pct"],
        "basis": "Buffer = P80 minus planned completion from the programme Monte Carlo.",
    }
