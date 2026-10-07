"""Smart Procurement & Ecosystem Platform Engine.

Provides:
  - Reference material prices across Indian metro markets (a maintained table, not a feed)
  - Material price trend projection (seasonal + inflation model)
  - Supplier register (sample until the project's own vendors are entered)
  - Procurement calendar driven by the project's CPM programme
  - Inventory planning (EOQ, reorder level, safety stock) from the take-off and programme
  - Integration catalogue: what the platform actually exports, and what is planned
  - Tender document generator (NIT, GCC, item-rate BOQ) priced from the project's BOQ
  - Government approval dossier with readiness checked against the project's data
  - Educational mode knowledge base for junior engineers

Every figure that can come from the project does; anything that cannot is labelled with
`data_source` so a static table is never presented as live market data.
"""

import hashlib
import math
from datetime import date, timedelta
from typing import Any, Dict, Optional

PRICES_AS_OF = "2026-03-20"
PRICES_NOTE = ("Reference prices from Aptimizer's maintained table (as of " + PRICES_AS_OF + "), "
               "not a live market feed. Confirm current rates with suppliers before ordering.")
SAMPLE_NOTE = "Sample data - replace with the project's own records."


# --------------------------------------------------------------------------- 1. Reference Material Prices

METRO_PRICES = {
    "Delhi-NCR": {
        "steel_fe500d_inr_mt": 56500.0,
        "steel_fe550d_inr_mt": 58200.0,
        "cement_opc53_inr_bag": 385.0,
        "cement_ppc_inr_bag": 355.0,
        "rmc_m25_inr_m3": 4400.0,
        "rmc_m35_inr_m3": 4950.0,
        "river_sand_inr_cft": 65.0,
        "aggregate_20mm_inr_cft": 42.0,
        "aac_blocks_inr_m3": 3400.0,
    },
    "Mumbai-MMR": {
        "steel_fe500d_inr_mt": 58200.0,
        "steel_fe550d_inr_mt": 60100.0,
        "cement_opc53_inr_bag": 410.0,
        "cement_ppc_inr_bag": 375.0,
        "rmc_m25_inr_m3": 4650.0,
        "rmc_m35_inr_m3": 5200.0,
        "river_sand_inr_cft": 78.0,
        "aggregate_20mm_inr_cft": 48.0,
        "aac_blocks_inr_m3": 3650.0,
    },
    "Bengaluru": {
        "steel_fe500d_inr_mt": 57400.0,
        "steel_fe550d_inr_mt": 59300.0,
        "cement_opc53_inr_bag": 395.0,
        "cement_ppc_inr_bag": 365.0,
        "rmc_m25_inr_m3": 4500.0,
        "rmc_m35_inr_m3": 5050.0,
        "river_sand_inr_cft": 72.0,
        "aggregate_20mm_inr_cft": 45.0,
        "aac_blocks_inr_m3": 3500.0,
    },
    "Hyderabad": {
        "steel_fe500d_inr_mt": 55800.0,
        "steel_fe550d_inr_mt": 57600.0,
        "cement_opc53_inr_bag": 370.0,
        "cement_ppc_inr_bag": 340.0,
        "rmc_m25_inr_m3": 4350.0,
        "rmc_m35_inr_m3": 4850.0,
        "river_sand_inr_cft": 62.0,
        "aggregate_20mm_inr_cft": 39.0,
        "aac_blocks_inr_m3": 3350.0,
    },
    "Chennai": {
        "steel_fe500d_inr_mt": 57100.0,
        "steel_fe550d_inr_mt": 58900.0,
        "cement_opc53_inr_bag": 405.0,
        "cement_ppc_inr_bag": 370.0,
        "rmc_m25_inr_m3": 4550.0,
        "rmc_m35_inr_m3": 5100.0,
        "river_sand_inr_cft": 74.0,
        "aggregate_20mm_inr_cft": 44.0,
        "aac_blocks_inr_m3": 3550.0,
    },
    "Pune": {
        "steel_fe500d_inr_mt": 57800.0,
        "steel_fe550d_inr_mt": 59600.0,
        "cement_opc53_inr_bag": 400.0,
        "cement_ppc_inr_bag": 368.0,
        "rmc_m25_inr_m3": 4580.0,
        "rmc_m35_inr_m3": 5150.0,
        "river_sand_inr_cft": 70.0,
        "aggregate_20mm_inr_cft": 46.0,
        "aac_blocks_inr_m3": 3600.0,
    },
}


def get_live_material_prices(metro: str = "Delhi-NCR") -> Dict[str, Any]:
    """Reference construction material prices for an Indian metro (static table)."""
    selected = metro if metro in METRO_PRICES else "Delhi-NCR"
    prices = METRO_PRICES[selected]
    rows = [
        ("TMT Rebar Fe 500D", "Metric Tonne", "steel_fe500d_inr_mt", "Moderate"),
        ("TMT Rebar Fe 550D", "Metric Tonne", "steel_fe550d_inr_mt", "Moderate"),
        ("OPC 53 Grade Cement", "50 kg Bag", "cement_opc53_inr_bag", "Low"),
        ("PPC Cement (Flyash)", "50 kg Bag", "cement_ppc_inr_bag", "Low"),
        ("Ready-Mix Concrete M25", "Cubic Metre", "rmc_m25_inr_m3", "Low"),
        ("Ready-Mix Concrete M35", "Cubic Metre", "rmc_m35_inr_m3", "Low"),
        ("River Sand / M-Sand", "Cubic Feet", "river_sand_inr_cft", "High"),
        ("Crushed Stone Aggregate (20mm)", "Cubic Feet", "aggregate_20mm_inr_cft", "Low"),
        ("AAC Blocks (Lightweight)", "Cubic Metre", "aac_blocks_inr_m3", "Low"),
    ]
    items = [{"material": m, "unit": u, "price_inr": prices[k],
              # No price history is held, so no trend is claimed; volatility is the
              # commodity's usual behaviour, stated as indicative.
              "trend": None, "volatility": f"{v} (indicative)"} for m, u, k, v in rows]
    return {
        "ok": True,
        "is_live": False,
        "data_source": "reference_table",
        "selected_metro": selected,
        "available_metros": list(METRO_PRICES.keys()),
        "last_updated": PRICES_AS_OF,
        "index_source": PRICES_NOTE,
        "items": items,
    }


# --------------------------------------------------------------------------- 2. Material Price Trend Projection

FORECAST_KEYS = {"steel": ("steel_fe550d_inr_mt", "₹/MT"), "cement": ("cement_opc53_inr_bag", "₹/bag"),
                 "concrete": ("rmc_m35_inr_m3", "₹/m³")}
ANNUAL_INFLATION_PCT = 4.2


def forecast_material_prices(material: str = "steel", horizon_months: int = 12,
                             metro: str = "Delhi-NCR") -> Dict[str, Any]:
    """Seasonal + inflation projection from the reference price, starting this month.

    A model, not market intelligence: monsoon softening in Jul-Aug, post-monsoon firming in
    Oct-Jan, and a flat annual inflation rate. The band widens with the horizon.
    """
    kind = "steel" if "steel" in material.lower() else "cement" if "cement" in material.lower() else "concrete"
    key, unit = FORECAST_KEYS[kind]
    base_price = float((METRO_PRICES.get(metro) or METRO_PRICES["Delhi-NCR"])[key])
    horizon = max(1, min(int(horizon_months or 12), 36))
    today = date.today()
    monthly = (1 + ANNUAL_INFLATION_PCT / 100.0) ** (1 / 12.0) - 1
    series = []
    for m in range(horizon):
        y, mo = divmod(today.month - 1 + m, 12)
        month = date(today.year + y, mo + 1, 1)
        seasonal = 0.96 if month.month in (7, 8) else 1.04 if month.month in (10, 11, 12, 1) else 1.0
        predicted = round(base_price * seasonal * (1 + monthly) ** m, 1)
        band = 0.04 + 0.005 * m
        series.append({
            "month": month.strftime("%b %Y"),
            "projected_price": predicted,
            "lower_bound": round(predicted * (1 - band), 1),
            "upper_bound": round(predicted * (1 + band), 1),
            "confidence_pct": None,   # no statistical model behind the band
        })
    return {
        "ok": True,
        "data_source": "model",
        "method": (f"Reference price for {metro} x seasonal factor x {ANNUAL_INFLATION_PCT}% annual "
                   "inflation; band +/-4% widening 0.5%/month. Not a market forecast."),
        "material": material,
        "unit": unit,
        "horizon_months": horizon,
        "annualized_inflation_rate_pct": ANNUAL_INFLATION_PCT,
        "forecast_series": series,
        "procurement_advisory": "On this seasonal model, July-August is the cheaper window for bulk steel and cement.",
    }


# --------------------------------------------------------------------------- 3. Supplier Register

def get_supplier_intelligence(project: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The project's vendor register, or anonymous sample rows describing what to capture.

    Ratings and delivery performance must come from the project's own purchase records.
    The sample rows name no real company and claim no verification.
    """
    vendors = ((project or {}).get("site_data") or {}).get("suppliers") or []
    if vendors:
        return {"ok": True, "data_source": "project", "note": "", "total_suppliers": len(vendors),
                "suppliers": vendors, "market_availability": None}
    categories = [
        ("SUP-A", "TMT rebar mill / stockist", "TMT Rebar Fe 500D / 550D", 7, ["IS 1786:2008", "BIS licence"]),
        ("SUP-B", "Cement manufacturer / dealer", "OPC 53 / PPC Cement", 3, ["IS 269:2015", "IS 1489"]),
        ("SUP-C", "Ready-mix concrete plant", "RMC M25-M50", 1, ["IS 4926:2003"]),
        ("SUP-D", "Structural steel supplier", "Structural Steel & Plates", 10, ["IS 2062:2011"]),
        ("SUP-E", "AAC block manufacturer", "AAC Blocks", 5, ["IS 2185 (Part 3)"]),
    ]
    suppliers = [{
        "id": i, "name": f"{n} (sample)", "category": c, "rating": None, "delivery_on_time_pct": None,
        "avg_lead_time_days": lead, "credit_terms_days": None, "status": "SAMPLE - NOT VERIFIED",
        "certified_standards": std,
    } for i, n, c, lead, std in categories]
    return {"ok": True, "data_source": "sample", "note": SAMPLE_NOTE + " Ratings come from your own purchase records.",
            "total_suppliers": 0, "suppliers": suppliers, "market_availability": None}


# --------------------------------------------------------------------------- shared: programme

def _programme(project: Dict[str, Any]):
    import engine
    import schedule
    analysis = engine.analyse(project)
    plan = schedule.plan_schedule(project, analysis, project.get("schedule") or {})
    return analysis, plan


def _d(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


# Programme trade -> (material, unit conversion to order unit, order unit, supplier lead days)
PROCURED = {
    "bar_bender": ("TMT rebar", 0.001, "MT", 7),
    "concretor": ("Concrete (RMC or cement + aggregates)", 1.0, "m³", 2),
    "mason": ("Blocks / bricks and mortar materials", 1.0, None, 5),
    "tiler": ("Floor and wall tiles", 1.0, "m²", 14),
    "painter": ("Paint and primers", 1.0, "m²", 7),
}


# --------------------------------------------------------------------------- 4. Procurement Calendar

def get_procurement_calendar(project: Dict[str, Any]) -> Dict[str, Any]:
    """Order dates set back from the programme's activity starts by each supplier lead time.

    Grouped by material and calendar month, so each row is one call-off order.
    """
    try:
        _, plan = _programme(project)
    except Exception as exc:
        return {"ok": False, "error": f"Programme unavailable: {exc}"}
    today = date.today()
    groups: Dict[tuple, Dict[str, Any]] = {}
    for a in plan.get("activities") or []:
        spec = PROCURED.get(a.get("trade"))
        start = _d(a.get("start"))
        if not spec or not start or not a.get("quantity"):
            continue
        material, factor, unit, lead = spec
        unit = unit or a.get("unit") or ""
        key = (material, unit, start.year, start.month)
        g = groups.setdefault(key, {"first": start, "qty": 0.0, "act": a.get("id"), "lead": lead,
                                    "phase": a.get("name")})
        g["qty"] += float(a["quantity"]) * factor
        if start < g["first"]:
            g["first"], g["act"], g["phase"] = start, a.get("id"), a.get("name")
    milestones = []
    for (material, unit, _, _), g in sorted(groups.items(), key=lambda kv: kv[1]["first"]):
        delivery = g["first"] - timedelta(days=1)
        order = delivery - timedelta(days=g["lead"])
        status = "OVERDUE" if order < today and delivery >= today else "ORDER NOW" if order == today else \
                 "PAST" if delivery < today else "PLANNED"
        milestones.append({
            "phase": g["phase"], "order_date": order.isoformat(), "delivery_date": delivery.isoformat(),
            "material": material, "quantity": f"{g['qty']:,.1f} {unit}".strip(),
            "cpm_activity_id": g["act"], "status": status,
        })
    return {
        "ok": True,
        "data_source": "programme",
        "project_id": str(project.get("_id", "")),
        "total_procurement_milestones": len(milestones),
        "milestones": milestones,
        "jit_efficiency_score": None,
        "method": "Delivery one day before the first activity of the month that uses it; order placed one supplier lead time earlier.",
    }


# --------------------------------------------------------------------------- 5. Inventory Planning

# Ordering and holding assumptions for the EOQ (Wilson) formula.
ORDER_COST_INR = 2500.0          # per purchase order: processing, transport mobilisation, receipt
CARRYING_COST_PCT = 12.5         # of unit price per year: capital, storage, losses
SAFETY_DAYS = 5                  # cover for supplier and site variability
STORAGE = {"steel": ("MT", 1.0), "cement": ("bags", 20.0), "sand": ("m³", 1.2), "aggregate": ("m³", 1.2)}


def calculate_inventory_plan(project: Dict[str, Any]) -> Dict[str, Any]:
    """EOQ, safety stock and reorder point from the take-off and the programme."""
    try:
        analysis, plan = _programme(project)
    except Exception as exc:
        return {"ok": False, "error": f"Programme unavailable: {exc}"}
    import engine
    qty = {i["key"]: float(i["quantity"]) for i in analysis["quantities"]["items"]}
    rates = {**engine.DEFAULT_RATES, **(project.get("rates") or {})}
    acts = plan.get("activities") or []

    def window(trades) -> float:
        starts = [_d(a.get("start")) for a in acts if a.get("trade") in trades and _d(a.get("start"))]
        ends = [_d(a.get("finish")) for a in acts if a.get("trade") in trades and _d(a.get("finish"))]
        if not starts or not ends:
            return 0.0
        return max(((max(ends) - min(starts)).days + 1) * 6.0 / 7.0, 1.0)   # six-day site week

    specs = [
        ("TMT Steel Rebar", "steel", qty.get("steel", 0) / 1000.0, rates["steel"] * 1000.0, ("bar_bender",), 7),
        ("Cement (OPC 53)", "cement", qty.get("cement", 0), rates["cement"], ("concretor", "mason"), 3),
        ("Sand", "sand", qty.get("sand", 0), rates["sand"], ("concretor", "mason"), 3),
        ("Coarse Aggregate", "aggregate", qty.get("aggregate", 0), rates["aggregate"], ("concretor",), 3),
    ]
    items = []
    for name, key, total, unit_price, trades, lead in specs:
        days = window(trades)
        if total <= 0 or days <= 0:
            continue
        unit, per_sqm = STORAGE[key]
        daily = total / days
        annual = daily * 312.0                              # 52 six-day weeks
        holding = unit_price * CARRYING_COST_PCT / 100.0
        eoq = min(math.sqrt(2.0 * annual * ORDER_COST_INR / holding), total) if holding > 0 else total
        safety = daily * SAFETY_DAYS
        reorder = daily * (lead + SAFETY_DAYS)
        items.append({
            "material": name,
            "total_project_demand": f"{total:,.1f} {unit}",
            "daily_consumption_rate": f"{daily:,.2f} {unit}/day over {days:.0f} working days",
            "lead_time_days": lead,
            "safety_stock": f"{safety:,.1f} {unit}",
            "reorder_point": f"{reorder:,.1f} {unit}",
            "economic_order_qty_eoq": f"{eoq:,.1f} {unit}",
            "storage_yard_allocation": f"{(eoq + safety) / per_sqm:,.0f} m² at {per_sqm:g} {unit}/m²",
        })
    return {
        "ok": True,
        "data_source": "take-off + programme",
        "inventory_items": items,
        "carrying_cost_pct": CARRYING_COST_PCT,
        "order_cost_inr": ORDER_COST_INR,
        "stockout_risk_score": f"Reorder points cover lead time plus {SAFETY_DAYS} days of use",
    }


# --------------------------------------------------------------------------- 6. Integration Catalogue

def get_marketplace_catalog() -> Dict[str, Any]:
    """What the platform actually integrates with today, and what is only planned.

    The previous catalogue listed plugins, install counts and /api/v1 endpoints that do not
    exist. Every "available" row below is a real export the API serves.
    """
    plugins = [
        {"id": "INT-01", "name": "IFC4 export (Revit, ArchiCAD, Navisworks)", "category": "BIM", "rating": None, "installs": None, "price": "Available"},
        {"id": "INT-02", "name": "DXF R2018 export (AutoCAD, BricsCAD)", "category": "CAD", "rating": None, "installs": None, "price": "Available"},
        {"id": "INT-03", "name": "DWG export via ODA File Converter", "category": "CAD", "rating": None, "installs": None, "price": "Available when the converter is installed on the server"},
        {"id": "INT-04", "name": "BOQ Excel workbook", "category": "Estimating", "rating": None, "installs": None, "price": "Available"},
        {"id": "INT-05", "name": "STAAD.Pro / ETABS model exchange", "category": "Structural", "rating": None, "installs": None, "price": "Planned - not available"},
        {"id": "INT-06", "name": "Primavera P6 / MS Project XML", "category": "Scheduling", "rating": None, "installs": None, "price": "Planned - not available"},
    ]
    apis = [
        {"endpoint": "GET /api/projects/{id}/bim/export/ifc", "desc": "IFC4 georeferenced model", "auth": "Bearer token"},
        {"endpoint": "GET /api/projects/{id}/bim/export/dxf", "desc": "Layered DXF site plan", "auth": "Bearer token"},
        {"endpoint": "GET /api/projects/{id}/boq.xlsx", "desc": "Bill of quantities workbook", "auth": "Bearer token"},
        {"endpoint": "GET /api/projects/{id}/reports/{type}", "desc": "PDF reports", "auth": "Bearer token"},
        {"endpoint": "GET /api/procurement/live-prices", "desc": "Reference material price table (not a live feed)", "auth": "Bearer token"},
    ]
    return {
        "ok": True,
        "data_source": "platform",
        "total_plugins": sum(1 for p in plugins if p["price"].startswith("Available")),
        "plugins": plugins,
        "total_apis": len(apis),
        "apis": apis,
        "developer_portal_url": None,
    }


# --------------------------------------------------------------------------- 7. Tender Document Generator

def generate_tender_documents(project: Dict[str, Any]) -> Dict[str, Any]:
    """Tender package priced from the project's own BOQ and programme."""
    try:
        analysis, plan = _programme(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    p_name = project.get("name") or "Residential Project"
    est_cost = float(analysis["cost"]["total"] or 0)
    months = math.ceil(float(plan.get("duration_months") or 0)) or None
    # A stable reference: hash() is salted per process and changed on every restart.
    ref = hashlib.sha1(str(project.get("_id") or p_name).encode("utf-8")).hexdigest()[:6].upper()
    today = date.today()
    nit = {
        "tender_ref_no": f"APT/NIT/{ref}/{today.year}",
        "title": f"Construction of Civil, Structural & Finishing Works for {p_name}",
        "estimated_tender_value_inr": round(est_cost, 0),
        "earnest_money_deposit_emd_inr": round(est_cost * 0.01, 0),  # 1% indicative; set per authority
        "completion_period_months": months,
        "bid_submission_deadline": (today + timedelta(days=21)).isoformat() + " (proposed)",
        "bid_opening_date": (today + timedelta(days=22)).isoformat() + " (proposed)",
    }
    sections = [
        "Section 1: Notice Inviting Tender (NIT)",
        "Section 2: Instructions to Bidders (ITB) & Pre-Qualification Criteria",
        "Section 3: General Conditions of Contract (GCC) based on FIDIC / CPWD",
        "Section 4: Special Conditions of Contract (SCC) & Milestone Penalties",
        "Section 5: Technical Specifications (Civil, Structural, Waterproofing, Finishes)",
        "Section 6: Bill of Quantities (BOQ) with Item-Rate Pricing Schedule",
    ]
    return {
        "ok": True,
        "data_source": "BOQ + programme",
        "nit": nit,
        "sections": sections,
        "contract_type": "Item Rate / Measurement Contract with Price Variation Clause",
        "tender_package_status": "DRAFT - review by the tendering authority before issue",
    }


# --------------------------------------------------------------------------- 8. Government Approval Assistant

def generate_government_approval_dossier(project: Dict[str, Any]) -> Dict[str, Any]:
    """Approval dossiers with each attachment checked against what the project actually holds.

    "Ready" means the platform can produce that sheet from the project's data and its check
    passes; documents only the owner holds (title deed, escrow) stay "to provide" until
    recorded in project["documents"].
    """
    import engine
    try:
        a = engine.analyse(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    docs = project.get("documents") or {}
    results = a["compliance"].get("results") or []

    def passed(*words) -> bool:
        hits = [r for r in results if any(w in str(r.get("name") or r.get("rule") or r.get("label") or "").lower() for w in words)]
        return bool(hits) and all(r.get("pass", r.get("passed", r.get("status") == "pass")) for r in hits)

    ar, util, park = a["areas"], a["utilities"], a["parking"]
    has_plot = ar["plot_area_sqm"] > 0
    checks = {
        "RERA (Real Estate Regulatory Authority)": ("Form A/B Project Registration Dossier", [
            ("Title deed", bool(docs.get("title_deed"))),
            ("Sanctioned site plan", bool(docs.get("sanctioned_plan"))),
            ("Quarterly cash flow (Finance module)", has_plot and ar["total_units"] > 0),
            ("Escrow account details", bool(docs.get("escrow_account"))),
        ]),
        "State Fire & Emergency Services": ("Fire Safety NOC Application", [
            ("Staircase widths to NBC Part 4", passed("stair")),
            ("Exits and travel distance to NBC Part 4", passed("exit", "travel")),
            ("Fire hydrant and ring main schematic (Utilities)", True),
            ("Fire tender access drawing", bool(docs.get("fire_access_drawing"))),
        ]),
        "Municipal Town Planning Authority": ("Building Sanction & Commencement Certificate", [
            ("Setback drawings to NBC Part 3", has_plot and passed("setback")),
            ("FAR computation sheet", has_plot and passed("far")),
            ("Parking layout and ECS statement", park.get("deficit", 0) <= 0),
            ("Structural stability certificate", bool(docs.get("structural_certificate"))),
        ]),
        "State Pollution Control Board (SPCB)": ("Consent to Establish (CTE)", [
            ("STP capacity design", float(util.get("stp_capacity_kld") or 0) > 0),
            ("Rainwater harvesting scheme", float(util.get("rwh_storage_cum") or 0) > 0),
            ("DG set acoustic enclosure specification", bool(docs.get("dg_specification"))),
            ("Solid waste management plan", bool(docs.get("solid_waste_plan"))),
        ]),
    }
    clearances = []
    OWNER_DOCS = {"Title deed", "Sanctioned site plan", "Escrow account details", "Fire tender access drawing",
                  "Structural stability certificate", "DG set acoustic enclosure specification",
                  "Solid waste management plan"}
    for agency, (document, items) in checks.items():
        ready = sum(1 for _, ok in items if ok)
        pct = round(ready / len(items) * 100)
        clearances.append({
            "agency": agency,
            "document": document,
            "required_attachments": [f"{name} - " + ("ready" if ok else "to provide" if name in OWNER_DOCS
                                                       else "fails the project's compliance check")
                                     for name, ok in items],
            "status": "READY TO COMPILE" if pct == 100 else "INCOMPLETE",
            "readiness_pct": pct,
        })
    overall = round(sum(c["readiness_pct"] for c in clearances) / len(clearances), 1)
    return {
        "ok": True,
        "data_source": "project",
        "total_clearance_agencies": len(clearances),
        "overall_submission_readiness_pct": overall,
        "clearances": clearances,
    }


# --------------------------------------------------------------------------- 9. Educational Mode Insights

def get_educational_mode_guide(topic: str = "setbacks") -> Dict[str, Any]:
    """Provides interactive engineering explanations for junior engineers and students."""
    guides = {
        "setbacks": {
            "topic": "Statutory Building Setbacks",
            "governing_code": "NBC 2016 Part 3, Clause 8 & Table 2",
            "principle": "Setbacks ensure fire engine maneuverability, natural light, cross-ventilation, and privacy between adjacent plots.",
            "rule_of_thumb": "Front setback is governed by abutting road width and plot area; Side and rear open spaces increase with building height above 10m at the rate of 1m per 3m of height.",
            "common_mistakes": "Neglecting podium projections into the clear fire driveway.",
        },
        "ductility": {
            "topic": "Ductile Detailing of Reinforced Concrete",
            "governing_code": "IS 13920:2016",
            "principle": "Ensures that high-rise frames in Seismic Zones III, IV, and V dissipate earthquake energy through plastic hinge formation without brittle collapse.",
            "rule_of_thumb": "Column confinement ties must have 135° hooks with 10d extension; beam-column joints require special confining reinforcement.",
            "common_mistakes": "Using 90° stirrup bends which open up under cyclic lateral sway.",
        },
        "pt_slabs": {
            "topic": "Post-Tensioned Flat Slabs",
            "governing_code": "IS 1343:2012 / ACI 318",
            "principle": "High-strength steel strands are stressed after concrete attains 70% compressive strength, imparting pre-compression to neutralize tensile stresses.",
            "rule_of_thumb": "Span-to-depth ratios of 30 to 45 can be achieved, reducing floor slab thickness by 25% and lowering foundation loads.",
            "common_mistakes": "Stripping shores prematurely before tendon stressing is fully certified.",
        }
    }

    selected = guides.get(topic.lower()) or guides["setbacks"]
    return {
        "ok": True,
        "selected_topic": topic,
        "available_topics": list(guides.keys()),
        "guide": selected,
    }

