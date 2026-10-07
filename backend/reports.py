"""PDF and Excel report generation."""
import io
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage

BRAND = colors.HexColor("#2563EB")
DARK = colors.HexColor("#0F172A")
LIGHT = colors.HexColor("#F1F5F9")

# One report per workspace module, in the order the menu lists them, so a reader looking
# for "the parking numbers" opens the document named after the page they navigate by.
#
# Grouped as the menu groups them:
#   Site             plot, site
#   Design           planning, parking, layout
#   Engineering      calculations, engineering, structural, water, fire, sustainability
#   Cost & Programme boq, cost, programme
#   Deliver          compliance, datahealth, executive
#
# Four ids were merged rather than kept as documents of their own: they repeated figures
# the survivor already carried, and a reader choosing between two reports with the same
# numbers picks wrong half the time.
#
#   utilities      -> water        (identical STP, tank and RWH sizing)
#   quantity       -> boq          (quantities are the unpriced half of the bill)
#   accessibility  -> compliance   (it is a compliance chapter, not a document)
#   controls       -> compliance   (setbacks are checked, not designed, at this stage)
#
# Parking was previously merged into the Executive Summary. That was wrong once the
# parking engine grew a per-building demand model, an authority norm and its own check
# list: a client reads slots, but a sanction reviewer reads the norm the slots came from,
# and none of that fitted in a nine-line block inside somebody else's document.
REPORT_TITLES = {
    "executive": "Executive Summary",
    # ---- Site
    "plot": "Plot & Setbacks Report",
    "site": "Site Analysis Report",
    "township": "Township & Master Plan Report",
    # ---- Design
    "planning": "Apartment Planning & Vastu Report",
    "floorplans": "Tower Floor Plans & Layout Drawings",
    "parking": "Parking Report",
    "layout": "Site Layout & Massing Report",
    # ---- Engineering
    "calculations": "Area & FAR Calculation Report",
    "engineering": "IS / NBC Engineering Summary",
    "structural": "Structural Design Basis Report",
    "water": "Water & Sanitation Infrastructure Report",
    "fire": "Fire & Life Safety Compliance Report",
    "sustainability": "Sustainability & Carbon Report",
    # ---- Cost & Programme
    "boq": "BOQ & Quantities Report",
    "cost": "Cost & Feasibility Report",
    "programme": "Construction Programme Report",
    # ---- Deliver
    "compliance": "Compliance Validation Report",
    "datahealth": "Data Reliability Report",
}

# Merged ids and module aliases still resolve, so an old link, a saved bookmark or a
# module name typed straight into the URL lands on the report that carries those numbers
# instead of a 400.
MERGED_INTO = {
    "utilities": "water",
    "quantity": "boq",
    "quantities": "boq",
    "accessibility": "compliance",
    "controls": "compliance",
    "dev-controls": "compliance",
    "finance": "cost",
    "feasibility": "cost",
    "gis": "site",
    "masterplan": "township",
    "vastu": "planning",
    "autonomous-studio": "planning",
    "studio": "layout",
    "generative-studio": "layout",
    "floorplan": "floorplans",
    "floor-plans": "floorplans",
    "floor_plans": "floorplans",
    "3d": "layout",
    "site-layout": "layout",
    "site_layout": "layout",
    "data-health": "datahealth",
    "data_health": "datahealth",
    "reliability": "datahealth",
    "bim": "layout",
    "procurement": "boq",
    "market": "boq",
    "smart-procurement": "boq",
    "digital-twin": "site",
    "smart-site": "site",
    "urban-sustainability": "sustainability",
    "esg": "sustainability",
    "green-building": "sustainability",
}

# The order a merged PDF reads in. The Executive Summary leads because it is the roll-up
# a reader opens the set for; everything after it follows the workspace menu exactly, so
# the contents page and the sidebar agree on where a subject lives.
# Stage 1 (Site): the reports the site modules produce, in workflow order.
SITE_REPORTS = ("plot", "layout", "site", "township")

ALL_ORDER = ["executive",
             "plot", "site", "township",
             "planning", "floorplans", "parking", "layout",
             "calculations", "engineering", "structural", "water", "fire", "sustainability",
             "boq", "cost", "programme",
             "compliance", "datahealth"]


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("H", parent=ss["Heading1"], fontSize=18, textColor=DARK, spaceAfter=4))
    ss.add(ParagraphStyle("Sub", parent=ss["Normal"], fontSize=9, textColor=colors.HexColor("#475569")))
    ss.add(ParagraphStyle("Sec", parent=ss["Heading2"], fontSize=12, textColor=BRAND, spaceBefore=12, spaceAfter=6))
    return ss


def _table(data, col_widths=None, align_right_from=1):
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (align_right_from, 1), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    t.setStyle(TableStyle(style))
    return t


def _kv(pairs):
    return _table([["Parameter", "Value"]] + [[k, str(v)] for k, v in pairs], col_widths=[100 * mm, 65 * mm])


def _n(v):
    if isinstance(v, (int, float)):
        return f"{v:,.2f}"
    return str(v)


def _mod_table(module, col_widths=None):
    rows = [["Parameter", "Value", "Unit", "IS / NBC clause"]]
    for o in module.get("outputs", []):
        cl = o.get("clause") or {}
        rows.append([o["label"], str(o["value"]), o.get("unit", ""),
                     f"{cl.get('code', '')} {cl.get('clause', '')}".strip()])
    return _table(rows, col_widths or [58 * mm, 30 * mm, 18 * mm, 60 * mm], align_right_from=1)


def _checks_table(module):
    rows = [["Check", "Actual", "Required", "Status", "Clause"]]
    for c in module.get("checks", []):
        cl = c.get("clause") or {}
        rows.append([c["label"], str(c["actual"]), str(c["required"]), c["status"].upper(),
                     f"{cl.get('code', '')} {cl.get('clause', '')}".strip()])
    return _table(rows, [54 * mm, 22 * mm, 26 * mm, 16 * mm, 48 * mm], align_right_from=1)


# Several sections of one report name the same derived artefact -- the summary states the
# programme's duration and the body tabulates its phases -- and each of those artefacts is
# a full re-analysis. `build_pdf` opens one cache per document and hands it to every
# helper, so the CPM, the cash flow and the reliability pass run once per report rather
# than once per section that mentions them. The cache never outlives the document, so it
# can never serve a figure from a project that has since been edited.
def _memo(d, key, fn):
    if d is None:
        return fn()
    if key not in d:
        d[key] = fn()
    return d[key]


def _programme(project: dict, a: dict, d: dict = None):
    """The programme, or None. Imported here so reports never hard-depend on it."""
    def build():
        try:
            import schedule as schedlib
            plan = schedlib.plan_schedule(project, a, (project.get("schedule") or {}))
            return plan if plan.get("ok") else None
        except Exception:
            return None
    return _memo(d, "programme", build)


def _finance(project: dict, a: dict, d: dict = None):
    def build():
        try:
            import finance as financelib
            return financelib.analyse(project, a, project.get("finance"))
        except Exception:
            return None
    return _memo(d, "finance", build)


def _datahealth(project: dict, d: dict = None):
    """The reliability report, or None. Lazy so reports never hard-depend on it."""
    def build():
        try:
            import datahealth as dhlib
            return dhlib.report(project)
        except Exception:
            return None
    return _memo(d, "datahealth", build)


def _vastu_audits(project: dict, d: dict = None):
    return _memo(d, "vastu", lambda: _vastu_audits_uncached(project))


def _vastu_audits_uncached(project: dict):
    """Per tower: (tower, {floor -> audit}) for every floor plate that has been generated.

    Audited here rather than read off the stored entry, because only floors regenerated
    since the Vastu audit shipped carry a stored `validation.vastu`. Re-running the audit
    on the stored rooms gives the same answer for those and an answer at all for the rest.
    """
    out = []
    try:
        import aifloorplan
    except Exception:
        return out
    for t in (project.get("towers") or []):
        plates = t.get("floor_layouts") or {}
        if not plates:
            continue
        floors = max(int(t.get("floors") or 1), 1)
        per_floor = {}
        for key, entry in plates.items():
            rooms = (entry or {}).get("rooms") or []
            if not rooms:
                continue
            stored = ((entry.get("validation") or {}).get("vastu")) or None
            try:
                per_floor[int(key)] = stored or aifloorplan.audit_vastu_and_mep(
                    rooms, int(key), floors)
            except Exception:
                continue
        if per_floor:
            out.append((t, per_floor))
    return out


def _optimisers(project: dict, a: dict, eng: dict):
    out = {}
    try:
        import optimise as optlib
        out.update({k: v for k, v in optlib.analyse(project, a, eng).items()
                    if isinstance(v, dict) and "current" in v})
    except Exception:
        pass
    try:
        import planopt as planoptlib
        out.update({k: v for k, v in planoptlib.analyse(project, a).items()
                    if isinstance(v, dict) and "current" in v})
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- per-tower breakdowns
# Some figures are computed per tower by the engine (loads, base shear, column size). Most
# are not: the BOQ, the cost and the utilities are project-wide, and a per-tower row for
# them can only be an APPORTIONMENT. Those tables say so in a footnote and name the basis,
# because a reader who assumes an apportioned figure was independently derived will use it
# to compare towers that were never separately costed.
APPORTIONED = ("Apportioned by each tower's share of built-up area — these are not "
               "separately computed per tower, so they show where a project total lands, "
               "not an independent estimate.")


def _shares(a: dict):
    """(tower, share-of-built-up) for each tower, summing to 1."""
    towers = a["areas"]["towers"]
    total = sum(float(t["builtup_sqm"] or 0) for t in towers) or 1.0
    return [(t, float(t["builtup_sqm"] or 0) / total) for t in towers]


def _tower_structural(el, ss, a: dict, eng: dict):
    per = (eng or {}).get("per_tower") or {}
    if not per:
        return
    rows = []
    for t in a["areas"]["towers"]:
        e = per.get(t.get("id")) or {}
        loads = e.get("loads") or {}
        seis = e.get("seismic") or {}
        rows.append([
            t["name"], t["floors"], _n(t["height_m"]), _n(t["footprint_sqm"]),
            _n(t["builtup_per_floor_sqm"]), loads.get("column_size_mm", "-"),
            _n(loads.get("column_load_kn")), _n(seis.get("base_shear_kn")),
        ])
    el += [Paragraph("Per Tower — Structural", ss["Sec"]),
           _table([["Tower", "Floors", "Height (m)", "Footprint (m²)", "Plate (m²)",
                    "Column (mm)", "Column load (kN)", "Base shear (kN)"]] + rows,
                  col_widths=[26 * mm, 14 * mm, 18 * mm, 22 * mm, 20 * mm, 24 * mm,
                              24 * mm, 22 * mm]),
           Paragraph("Column load is the worst-case axial load on an interior column "
                     "(IS 875 Parts 1–2 loads, tributary area method). Base shear is the "
                     "design seismic force at the base, IS 1893 (Part 1):2016 Cl. 7.6. "
                     "Both are computed per tower, not apportioned.", ss["Sub"])]
    found = next((o for o in (eng["modules"]["foundation"]["outputs"]) if "ype" in o["label"]), None)
    rec = eng["modules"]["foundation"].get("recommendation") or {}
    if rec:
        el += [Paragraph(f"Foundation, all towers: {rec.get('value')} — the foundation "
                         "module sizes one system for the site, not one per tower.", ss["Sub"])]


def _tower_boq(el, ss, a: dict, cur: str):
    shares = _shares(a)
    if len(shares) < 1:
        return
    mats = {m["key"]: m for m in a["boq"]["materials"]}
    keys = [k for k in ("concrete", "steel", "bricks", "tiles", "paint") if k in mats]
    rows = []
    for t, share in shares:
        row = [t["name"], f"{share * 100:.1f}%"]
        for k in keys:
            row.append(_n(float(mats[k]["quantity"]) * share))
        row.append(_n(sum(float(mats[k]["amount"]) * share for k in keys)))
        rows.append(row)
    total = ["Project total", "100.0%"] + [_n(float(mats[k]["quantity"])) for k in keys] \
            + [_n(sum(float(mats[k]["amount"]) for k in keys))]
    header = ["Tower", "Share"] + [f'{mats[k]["label"].split(" (")[0]} ({mats[k]["unit"]})'
                                   for k in keys] + [f"Cost ({cur})"]
    el += [Paragraph("Per Tower — Quantities and Cost", ss["Sec"]),
           _table([header] + rows + [total],
                  col_widths=[24 * mm, 16 * mm] + [22 * mm] * len(keys) + [30 * mm]),
           Paragraph(APPORTIONED + " The total row is the project figure and equals the sum "
                     "of the rows above it.", ss["Sub"])]


def _tower_cost(el, ss, project: dict, a: dict, cur: str, d: dict = None):
    shares = _shares(a)
    fin = _finance(project, a, d)
    total_cost = float(a["cost"]["total"] or 0)
    sale_by_sqft = None
    if fin:
        sale_by_sqft = float(fin["config"].get("sale_rate_per_sqft") or 0)
    rows = []
    for t, share in shares:
        cost = total_cost * share
        bu = float(t["builtup_sqm"] or 0)
        units = int(t["total_units"] or 0)
        saleable = float(t["super_builtup_sqm"] or 0) * 10.7639
        rows.append([
            t["name"], _n(cost), _n(cost / bu) if bu else "-",
            _n(cost / units) if units else "-", _n(saleable),
            _n(saleable * sale_by_sqft) if sale_by_sqft else "-",
        ])
    rows.append(["Project total", _n(total_cost), _n(a["cost"]["per_sqm"]),
                 _n(a["cost"]["per_unit"]),
                 _n(sum(float(t["super_builtup_sqm"] or 0) * 10.7639 for t, _ in shares)),
                 _n(fin["revenue"]["from_sales"]) if fin else "-"])
    el += [Paragraph("Per Tower — Cost and Revenue", ss["Sec"]),
           _table([["Tower", f"Cost ({cur})", f"Per m² ({cur})", f"Per flat ({cur})",
                    "Saleable (sqft)", f"Revenue ({cur})"]] + rows,
                  col_widths=[26 * mm, 30 * mm, 24 * mm, 26 * mm, 26 * mm, 32 * mm]),
           Paragraph("Cost is " + APPORTIONED[0].lower() + APPORTIONED[1:] +
                     " Saleable area and revenue ARE per tower: saleable is that tower's "
                     "own super built-up area, priced at the rate in Feasibility & ROI. "
                     "The revenue total therefore equals the sum of its rows; the cost "
                     "total does too, by construction of the apportionment.", ss["Sub"])]


def _tower_programme(el, ss, project: dict, a: dict, plan: dict):
    acts = plan.get("activities") or []
    if not acts:
        return
    by_tower = {}
    for x in acts:
        key = x.get("tower")
        if not key:
            continue
        b = by_tower.setdefault(key, {"start": x["start"], "finish": x["finish"],
                                      "critical": False, "n": 0})
        b["start"] = min(b["start"], x["start"])
        b["finish"] = max(b["finish"], x["finish"])
        b["critical"] = b["critical"] or bool(x.get("critical"))
        b["n"] += 1
    if not by_tower:
        return
    from datetime import date as _date
    names = {t.get("id"): t.get("name") for t in a["areas"]["towers"]}
    rows = []
    for key, b in sorted(by_tower.items(), key=lambda kv: kv[1]["start"]):
        days = (_date.fromisoformat(b["finish"]) - _date.fromisoformat(b["start"])).days + 1
        rows.append([names.get(key, key), b["start"], b["finish"], _n(days), _n(b["n"]),
                     "yes" if b["critical"] else "no"])
    cycle = (plan.get("safety") or {}).get("floor_cycle_days")
    el += [Paragraph("Per Tower — Programme", ss["Sec"]),
           _table([["Tower", "Start", "Finish", "Calendar days", "Tasks", "On critical path"]]
                  + rows,
                  col_widths=[28 * mm, 26 * mm, 26 * mm, 26 * mm, 20 * mm, 28 * mm],
                  align_right_from=6),
           Paragraph(f"Dates are the earliest start and latest finish of that tower's own "
                     f"tasks, from the CPM forward pass. Floor cycle is {cycle} days for "
                     "every tower — it is set by the slab cycle and the IS 456 curing and "
                     "prop-removal minimums, which do not vary by tower. A tower is on the "
                     "critical path if any of its tasks is.", ss["Sub"])]


def _tower_water(el, ss, a: dict):
    shares = _shares(a)
    u = a["utilities"]
    total_occ = sum(int(t["occupants"] or 0) for t, _ in shares) or 1
    demand = float(u.get("water_demand_lpd") or 0)
    ug = float(u.get("ug_tank_cum") or 0)
    oh = float(u.get("oh_tank_cum") or 0)
    rows = []
    for t, _share in shares:
        occ = int(t["occupants"] or 0)
        f = occ / total_occ
        rows.append([t["name"], _n(occ), _n(demand * f), _n(ug * f), _n(oh * f)])
    rows.append(["Project total", _n(total_occ), _n(demand), _n(ug), _n(oh)])
    el += [Paragraph("Per Tower — Water Demand", ss["Sec"]),
           _table([["Tower", "Occupants", "Demand (litre/day)", "Sump (m³)", "Overhead (m³)"]]
                  + rows,
                  col_widths=[30 * mm, 24 * mm, 34 * mm, 26 * mm, 30 * mm]),
           Paragraph("Demand per occupant follows IS 1172:1993. Tank volumes are apportioned "
                     "by occupancy — in practice one sump serves the site, so these rows show "
                     "each tower's share of a shared tank, not a tank per tower. The total is "
                     "the sized volume and equals the sum of the shares.", ss["Sub"])]


def _tower_compliance(el, ss, a: dict):
    towers = a["areas"]["towers"]
    plot = float(a["areas"]["plot_area_sqm"] or 0)
    rows = []
    for t in towers:
        fp = float(t["footprint_sqm"] or 0)
        rows.append([t["name"], _n(t["height_m"]), _n(t["floors"]), _n(fp),
                     _n(fp / plot * 100) if plot else "-",
                     _n(t["stair_min_width"]), _n(t["lift_count"]),
                     _n(t["exits_per_floor"])])
    rows.append(["Project", _n(a["areas"]["max_height_m"]), _n(a["areas"]["total_floors"]),
                 _n(a["areas"]["ground_footprint_sqm"]),
                 _n(a["areas"]["ground_coverage_pct"]), "-", "-", "-"])
    el += [Paragraph("Per Tower — Rules Evaluated Per Building", ss["Sec"]),
           _table([["Tower", "Height (m)", "Floors", "Footprint (m²)", "Coverage (%)",
                    "Stair (m)", "Lifts", "Exits/floor"]] + rows,
                  col_widths=[24 * mm, 20 * mm, 16 * mm, 24 * mm, 22 * mm, 18 * mm,
                              14 * mm, 20 * mm]),
           Paragraph("Coverage is that tower's footprint over the whole plot, so the rows "
                     "sum to the project coverage. Stair width, lift count and exits are "
                     "checked per building: NBC 2016 Part 4 Cl. 4.3 (exits) and Part 3 "
                     "(stairs and lifts). Height governs the setback minimum, so a taller "
                     "tower raises the requirement for the whole site.", ss["Sub"])]


def _tower_carbon(el, ss, a: dict, eng: dict):
    carbon = ((eng or {}).get("modules") or {}).get("carbon")
    if not carbon:
        return
    total = float(carbon["derived"]["total_tco2e"] or 0)
    rows = []
    for t, share in _shares(a):
        bu = float(t["builtup_sqm"] or 0)
        tco2 = total * share
        rows.append([t["name"], _n(bu), _n(tco2), f"{share * 100:.1f}%",
                     _n(tco2 * 1000 / bu) if bu else "-"])
    rows.append(["Project total", _n(a["areas"]["builtup_area_sqm"]), _n(total), "100.0%",
                 _n(carbon["derived"]["per_sqm_kg"])])
    el += [Paragraph("Per Tower — Embodied Carbon", ss["Sec"]),
           _table([["Tower", "Built-up (m²)", "Carbon (tCO₂e)", "Share", "Per m² (kgCO₂e)"]]
                  + rows,
                  col_widths=[30 * mm, 28 * mm, 28 * mm, 20 * mm, 32 * mm]),
           Paragraph(APPORTIONED + " Carbon per m² is therefore the same for every tower; "
                     "it varies only if the towers differ in specification, which this "
                     "model does not currently track per tower.", ss["Sub"])]


# ---------------------------------------------------------------- report summary
# Every report opens with its own answer before its workings. A reader who only wants the
# outcome -- "does it pass, what does it cost, is the data trustworthy" -- should not have
# to reconstruct it from six tables, and a reader who does want the tables loses nothing
# by having read the conclusion first.
#
# The summary is DERIVED, never separately computed: every figure here is read back out of
# the same analysis the body tabulates, so the headline and the workings cannot disagree.


def _fmt_pass(ok: bool, yes="PASS", no="FAIL"):
    return yes if ok else no


def _summary_executive(project, a, eng, d):
    ar, c, p = a["areas"], a["compliance"], a["parking"]
    suit = ((project.get("gis") or {}).get("suitability") or {}).get("score")
    rows = [
        ("Scheme", f'{len(ar["towers"])} tower(s), {ar["total_units"]} flats, '
                   f'up to {_n(ar["max_height_m"])} m'),
        ("Built-up area (m2)", _n(ar["builtup_area_sqm"])),
        ("FAR / FSI achieved", f'{ar["far"]} / {ar["fsi"]}'),
        ("Ground coverage (%)", _n(ar["ground_coverage_pct"])),
        (f'Construction cost ({a["cost"]["currency"]})', _n(a["cost"]["total"])),
        (f'Cost per flat ({a["cost"]["currency"]})', _n(a["cost"]["per_unit"])),
        ("Compliance", f'{c["passed"]} of {c["total"]} rules pass ({c["score"]}%)'),
        ("Parking", f'{p["provided_slots"]} provided against {p["required_slots"]} required'),
        ("Site suitability", f"{suit}%" if suit is not None else "GIS analysis not run"),
    ]
    verdict = (f'{ar["total_units"]} flats on {_n(ar["plot_area_acres"])} acres at FAR '
               f'{ar["far"]}, costing {_n(a["cost"]["total"])} {a["cost"]["currency"]}. '
               + ("Every compliance rule passes as configured."
                  if c["failed"] == 0 else
                  f'{c["failed"]} compliance rule(s) fail and are listed below.'))
    return verdict, rows


def _summary_plot(project, a, eng, d):
    ar = a["areas"]
    plot = project.get("plot") or {}
    edges = plot.get("road_edges") or []
    rows = [
        ("Plot area", f'{_n(ar["plot_area_sqm"])} m2 ({_n(ar["plot_area_acres"])} acres)'),
        ("Boundary vertices", len(plot.get("coordinates") or [])),
        ("Road-facing edges", f'{len(edges)} marked'
                              + (f', widest {_n(max(float(e.get("width") or 0) for e in edges))} m'
                                 if edges else "")),
        ("Ground footprint (m2)", _n(ar["ground_footprint_sqm"])),
        ("Ground coverage (%)", _n(ar["ground_coverage_pct"])),
        ("Open space", f'{_n(ar["open_space_sqm"])} m2 ({_n(ar["open_space_pct"])}%)'),
        ("Building height governing setbacks (m)", _n(ar["max_height_m"])),
    ]
    verdict = ("The plot geometry and the setbacks checked against it. Setbacks are "
               "compared with the NBC 2016 Part 3 minimum for this plot size, road width "
               "and building height; any shortfall is named below."
               if edges else
               "No road-facing edge is marked, so the front setback cannot be assigned to "
               "a specific boundary and the check below falls back to the plot-size rule.")
    return verdict, rows


def _summary_site(project, a, eng, d):
    g = project.get("gis") or {}
    if not g:
        return ("No site analysis has been run for this project, so nothing in this "
                "report is populated. Open GIS Intelligence and run it."), []
    t, fl = g.get("terrain") or {}, g.get("flood") or {}
    suit, build = g.get("suitability") or {}, g.get("buildability") or {}
    acc, wind, solar = g.get("accessibility") or {}, g.get("wind") or {}, g.get("solar") or {}
    rows = [
        ("Suitability", f'{suit.get("score")}% ({suit.get("grade")})' if suit else "-"),
        ("Buildability", (f'{"buildable" if build.get("buildable") else "constrained"} - '
                          f'{build.get("critical_count", 0)} critical, '
                          f'{build.get("warning_count", 0)} warning')
         if build else "-"),
        ("Terrain", f'{t.get("slope_class", "-")}, {_n(t.get("avg_slope_pct"))}% average slope, '
                    f'{_n(t.get("relief_m"))} m relief'),
        ("Flood risk", f'{fl.get("level", "-")} (score {_n(fl.get("score"))})'),
        ("Access score", f'{acc.get("score")}/100' if acc else "-"),
        ("Prevailing wind", f'{wind.get("prevailing")}, {_n(wind.get("mean_speed_ms"))} m/s '
                            f'({wind.get("summer")} in summer)' if wind else "-"),
        ("Rooftop solar", f'{_n(solar.get("installable_kwp"))} kWp, '
                          f'{_n(solar.get("annual_yield_kwh"))} kWh a year' if solar else "-"),
        ("Search radius / context", f'{g.get("radius_m")} m, '
                                    f'{sum((g.get("feature_counts") or {}).values())} mapped features'),
    ]
    verdict = (f'Site scores {suit.get("score")}% ({suit.get("grade")}) and is '
               f'{"buildable" if build.get("buildable") else "constrained"} on the flags '
               f'checked. Flood risk is {fl.get("level", "unknown")}.')
    return verdict, rows


def _summary_planning(project, a, eng, d):
    ar = a["areas"]
    towers = project.get("towers") or []
    mix = {}
    for t in towers:
        for u in (t.get("units") or []):
            mix[u.get("type", "?")] = mix.get(u.get("type", "?"), 0) \
                + int(u.get("count") or 0) * int(t.get("floors") or 0)
    plates = sum(len(t.get("floor_layouts") or {}) for t in towers)
    designed = sum(max(int(t.get("floors") or 0), 0) for t in towers)
    audits = _vastu_audits(project, d)
    scored = [au["score"] for _t, per in audits for au in per.values()]
    breaches = sum(len(au.get("violations") or []) for _t, per in audits for au in per.values())
    rows = [
        ("Towers", len(towers)),
        ("Flats", f'{ar["total_units"]} across {ar["total_floors"]} floor(s) of building'),
        ("Unit mix", ", ".join(f"{k.upper()} x{v}" for k, v in sorted(mix.items())) or "-"),
        ("Carpet area (m2)", _n(ar["carpet_area_sqm"])),
        ("Super built-up / saleable (m2)", _n(ar["super_builtup_area_sqm"])),
        ("Floor plates laid out", f"{plates} of {designed}"),
        ("Vastu score (mean of laid-out floors)",
         f"{round(sum(scored) / len(scored), 1)}%" if scored else "no floor plate generated"),
        ("Hard Vastu rule breaches", breaches if scored else "-"),
    ]
    verdict = (f'{ar["total_units"]} flats laid out over {plates} generated floor plate(s). '
               + ("Every laid-out floor meets the manual's hard rules."
                  if scored and not breaches else
                  f"{breaches} hard rule breach(es) are listed per unit below."
                  if scored else
                  "No floor plate has been generated yet, so the Vastu audit has nothing "
                  "to read."))
    return verdict, rows


def _summary_parking(project, a, eng, d):
    p = a["parking"]
    norm = p.get("norm") or {}
    checks = p.get("checks") or []
    failed = [c for c in checks if not c.get("pass")]
    rows = [
        ("Norm applied", f'{norm.get("authority", "-")} ({norm.get("state", "-")})'),
        ("Norm status", "verified against the published rule" if norm.get("verified")
                        else "unverified default - confirm with the authority"),
        ("Car slots", f'{p["provided_slots"]} provided against {p["required_slots"]} required'),
        ("Balance", f'{p["deficit"]} short' if p["deficit"] else f'{p["surplus"]} surplus'),
        ("Where they sit", f'{p["basement_slots"]} basement, {p["ground_slots"]} ground'),
        ("Two-wheelers", f'{p.get("scooters_required", 0)} required '
                         f'({p.get("scooter_ecs_equivalent", 0)} ECS equivalent)'),
        ("Reserved (visitor / EV / accessible)",
         f'{p["visitor_provided"]}/{p["visitor_required"]}, '
         f'{p["ev_provided"]}/{p["ev_required"]}, '
         f'{p["accessible_provided"]}/{p["accessible_required"]}'),
        ("Checks", f'{len(checks) - len(failed)} of {len(checks)} pass'),
        ("Ramp geometry", _fmt_pass(p["ramp_pass"])),
    ]
    verdict = (f'{p["provided_slots"]} car slots against a requirement of '
               f'{p["required_slots"]} under {norm.get("authority", "the configured norm")}. '
               + ("All parking checks pass." if not failed else
                  f'{len(failed)} check(s) fail: '
                  + "; ".join(c["label"] for c in failed[:3]) + "."))
    return verdict, rows


def _summary_layout(project, a, eng, d):
    sl = project.get("site_layout") or {}
    if not sl:
        return ("No site layout has been generated for this project. Open Plot & Setbacks "
                "and run the three layout stages (envelope, reserve, generate) to populate "
                "this report."), []
    m = sl.get("layout_metrics") or {}
    roads = sl.get("roads") or {}
    green = sl.get("green") or {}
    sp = sl.get("surface_parking") or {}
    amenities = sl.get("amenities") or []
    rows = [
        ("Towers placed", m.get("tower_count", len(sl.get("towers") or []))),
        ("Buildable floor area (m2)", _n(m.get("total_buildable_area_sqm"))),
        ("Achieved FAR", f'{m.get("achieved_far")} against a cap of {m.get("far_cap")}'),
        ("Ground coverage (%)", _n(m.get("ground_coverage_pct"))),
        ("Open space (%)", _n(m.get("open_space_pct"))),
        ("Internal roads (m2)", f'{_n(roads.get("total_area_sqm"))} '
                                f'(ring {_n(roads.get("ring_width_m"))} m wide)'),
        ("Amenity blocks", f'{len(amenities)} reserved, '
                           f'{_n(sum(float(x.get("area_sqm") or 0) for x in amenities))} m2'),
        ("Landscaped green", f'{_n(green.get("area_sqm"))} m2 '
                             f'({_n(green.get("pct_of_plot"))}% of plot)'),
        ("Surface parking bays", sp.get("bay_count", 0)),
        ("Layout feasible", _fmt_pass(bool(m.get("feasible")), "yes", "no - see warnings")),
    ]
    verdict = (f'{m.get("tower_count", 0)} tower(s) packed into the buildable envelope at '
               f'FAR {m.get("achieved_far")} and {m.get("ground_coverage_pct")}% coverage. '
               + ("The layout satisfies every hard constraint."
                  if m.get("feasible") else
                  "The layout breaks a hard constraint - see the warnings below."))
    return verdict, rows


def _summary_calculations(project, a, eng, d):
    ar, far = a["areas"], a["far_derivation"]
    s = (a.get("area_derivation") or {}).get("summary") or {}
    perm = far.get("permissible") or {}
    rows = [
        ("Carpet area (m2)", _n(ar["carpet_area_sqm"])),
        ("Built-up area (m2)", _n(ar["builtup_area_sqm"])),
        ("Super built-up / saleable (m2)", _n(ar["super_builtup_area_sqm"])),
        ("Society amenities counted in saleable (m2)", _n(ar["society_amenities_sqm"])),
        ("Wall allowance / common loading",
         f'{s.get("wall_allowance_pct", "-")}% / {s.get("common_area_loading_pct", "-")}%'),
        ("Implied saleable multiplier", f'{s.get("implied_multiplier", "-")}x '
                                        f'({s.get("implied_loading_pct", "-")}% total loading)'),
        ("FAR", far["substitution"]),
        ("FSI", f'{far["fsi"]} (FAR x {far["fsi_factor"]:g})'),
        ("Permissible FAR", f'{perm.get("far_cap") or "not set"}'
                            + (f', {perm.get("used_pct")}% used, '
                               f'{_n(perm.get("headroom_sqm"))} m2 of headroom'
                               if perm.get("far_cap") else "")),
    ]
    verdict = (f'FAR {far["far"]} and FSI {far["fsi"]} from {_n(ar["builtup_area_sqm"])} m2 '
               f'of built-up over {_n(ar["plot_area_sqm"])} m2 of plot. '
               + (f'That is {perm.get("used_pct")}% of the {perm.get("far_cap")} cap.'
                  if perm.get("far_cap") else "No FAR cap is configured to check it against."))
    return verdict, rows


def _summary_engineering(project, a, eng, d):
    if not eng:
        return "The engineering modules produced no output for this project.", []
    s = eng["summary"]
    warns = eng.get("warnings") or []
    critical = [w for w in warns if w.get("severity") == "critical"]
    rows = [
        ("Design city", f'{eng["city_reference"]["city"]}, {eng["city_reference"]["state"]}'),
        ("Seismic zone / base shear", f'{s["seismic_zone"]}, {_n(s["base_shear_kn"])} kN'),
        ("Structure", f'{s["column_size"]} columns on a {s["foundation"]}, {s["mix_ratio"]} mix'),
        ("Water demand / STP", f'{_n(s["water_demand_lpd"])} litre/day, {_n(s["stp_kld"])} KLD'),
        ("Fire safety score (%)", _n(s["fire_score"])),
        ("Accessibility score (%)", _n(s["accessibility_score"])),
        ("NBC parking score (%)", _n(s["parking_score"])),
        ("Embodied carbon", f'{_n(s.get("embodied_carbon_tco2e"))} tCO2e '
                            f'({_n(s.get("carbon_per_sqm_kg"))} kgCO2e/m2)'),
        ("Green rating", s["green_rating"]),
        ("Code warnings", f'{len(warns)} ({len(critical)} critical)'),
        ("Missing inputs", len(eng.get("missing_inputs") or [])),
    ]
    verdict = (f'Designed for {eng["city_reference"]["city"]} in seismic zone '
               f'{s["seismic_zone"]}: {s["column_size"]} columns on a {s["foundation"]}. '
               + ("No critical code warning is outstanding." if not critical else
                  f"{len(critical)} critical code warning(s) are outstanding."))
    return verdict, rows


def _summary_structural(project, a, eng, d):
    if not eng:
        return "The engineering modules produced no output for this project.", []
    m, s = eng["modules"], eng["summary"]
    rows = [
        ("Governing load code", "IS 875 Parts 1-3 (dead, imposed and wind)"),
        ("Recommended column size (mm)", s["column_size"]),
        ("Column load basis", m["loads"]["recommendation"]["value"]),
        ("Seismic zone (IS 1893 Table 3)", s["seismic_zone"]),
        ("Design base shear (kN)", _n(s["base_shear_kn"])),
        ("Foundation (IS 1904 / IS 6403)", s["foundation"]),
        ("Concrete mix (IS 10262)", s["mix_ratio"]),
        ("Column grid", m["grid"]["recommendation"]["value"]
         if m["grid"].get("recommendation") else "-"),
    ]
    verdict = (f'{s["column_size"]} columns carrying a base shear of '
               f'{_n(s["base_shear_kn"])} kN in zone {s["seismic_zone"]}, founded on a '
               f'{s["foundation"]} in {s["mix_ratio"]} concrete.')
    return verdict, rows


def _summary_water(project, a, eng, d):
    u = a["utilities"]
    rows = [
        ("Population served", u["persons"]),
        ("Demand basis (IS 1172)", f'{_n(u["lpcd"])} litre per person per day'),
        ("Total demand (litre/day)", _n(u["water_demand_lpd"])),
        ("Sump / underground (m3)", _n(u["ug_tank_cum"])),
        ("Overhead (m3)", _n(u["oh_tank_cum"])),
        ("STP capacity (KLD)", _n(u["stp_capacity_kld"])),
        ("WTP capacity (KLD)", _n(u["wtp_capacity_kld"])),
        ("Rainwater harvested (litre/year)", _n(u["rwh_annual_litres"])),
        ("Plant rooms", f'{_n(u["pump_room_sqm"])} m2 pump, '
                        f'{_n(u["electrical_room_sqm"])} m2 electrical'),
    ]
    verdict = (f'{_n(u["water_demand_lpd"])} litre/day for {u["persons"]} residents, held in '
               f'{_n(u["ug_tank_cum"])} m3 of sump and {_n(u["oh_tank_cum"])} m3 overhead, '
               f'with a {_n(u["stp_capacity_kld"])} KLD STP.')
    return verdict, rows


def _summary_fire(project, a, eng, d):
    if not eng:
        return "The engineering modules produced no output for this project.", []
    m = eng["modules"]["fire"]
    ar = a["areas"]
    # Egress geometry lives on the compliance params, not on `areas` -- they are the
    # values the rules are evaluated against, and `areas` never carried them.
    pr = a["compliance"]["params"]
    failed = [c for c in m.get("checks", []) if c.get("status") != "pass"]
    refuge = [r for r in m.get("floor_rows", []) if r.get("refuge_required")]
    rows = [
        ("Clause checks", f'{m["passed"]} of {m["total"]} pass ({m["score"]}%)'),
        ("Building height (m)", _n(ar["max_height_m"])),
        ("Floors checked", len(m.get("floor_rows", []))),
        ("Refuge floors required", len(refuge)),
        ("Minimum exits per floor", pr["min_exits_per_floor"]),
        ("Maximum travel distance (m)", _n(pr["max_travel_distance_m"])),
        ("Minimum staircase width (m)", _n(pr["min_stair_width"])),
        ("Minimum corridor width (m)", _n(pr["min_corridor_width"])),
        ("Failing clauses", ", ".join(c["label"] for c in failed[:3]) or "none"),
    ]
    verdict = (f'{m["passed"]} of {m["total"]} NBC Part 4 clauses pass ({m["score"]}%). '
               + ("Every clause checked is met." if not failed else
                  f"{len(failed)} clause(s) fail and are listed below."))
    return verdict, rows


def _summary_sustainability(project, a, eng, d):
    mods = (eng or {}).get("modules") or {}
    carbon, trees, green = mods.get("carbon"), mods.get("trees"), mods.get("green")
    solar = (project.get("gis") or {}).get("solar") or {}
    rows = [
        ("Green rating", green["recommendation"]["value"] if green else "-"),
        ("Embodied carbon (tCO2e)", _n(carbon["derived"]["total_tco2e"]) if carbon else "-"),
        ("Carbon intensity (kgCO2e/m2)", _n(carbon["derived"]["per_sqm_kg"]) if carbon else "-"),
        ("Largest carbon source",
         (max(carbon["materials"], key=lambda m: m["tco2e"])["label"]
          + f' ({max(carbon["materials"], key=lambda m: m["tco2e"])["share_pct"]}%)')
         if carbon and carbon.get("materials") else "-"),
        ("Trees required", _n(max(trees["derived"]["required"], trees["derived"]["for_canopy"]))
         if trees else "-"),
        ("Rooftop solar (kWp)", _n(solar.get("installable_kwp")) if solar else "GIS not run"),
        ("Solar generation (kWh/year)", _n(solar.get("annual_yield_kwh")) if solar else "-"),
        ("Solar CO2 avoided (t/year)",
         _n(solar.get("co2_avoided_tonnes_per_yr")) if solar else "-"),
    ]
    net = ""
    if carbon and solar.get("co2_avoided_tonnes_per_yr"):
        yrs = float(carbon["derived"]["total_tco2e"] or 0) / \
            float(solar["co2_avoided_tonnes_per_yr"] or 1)
        net = (f' Rooftop solar offsets the embodied carbon in about {yrs:,.0f} years of '
               'operation.')
    verdict = (f'{_n(carbon["derived"]["total_tco2e"]) if carbon else "-"} tCO2e embodied at '
               f'{_n(carbon["derived"]["per_sqm_kg"]) if carbon else "-"} kgCO2e/m2, rated '
               f'{green["recommendation"]["value"] if green else "-"}.' + net)
    return verdict, rows


def _summary_boq(project, a, eng, d):
    boq, cur = a["boq"], a["cost"]["currency"]
    top = sorted(boq["materials"], key=lambda m: float(m["amount"] or 0), reverse=True)[:3]
    rows = [
        (f"Material total ({cur})", _n(boq["material_total"])),
        (f"Labour total ({cur})", _n(boq["labour_total"])),
        (f"Equipment total ({cur})", _n(boq["equipment_total"])),
        (f"Grand total ({cur})", _n(boq["grand_total"])),
        (f"Cost per m2 ({cur})", _n(boq["cost_per_sqm"])),
        (f"Cost per flat ({cur})", _n(boq["cost_per_unit"])),
        ("Line items", f'{len(boq["materials"])} material, {len(boq["labour"])} labour, '
                       f'{len(boq["equipment"])} equipment'),
        ("Largest material heads", ", ".join(m["label"].split(" (")[0] for m in top)),
    ]
    verdict = (f'{_n(boq["grand_total"])} {cur} of measured work at '
               f'{_n(boq["cost_per_sqm"])} {cur} per m2 built-up. Quantities come from '
               'configurable thumb-rule ratios, not from a measured take-off.')
    return verdict, rows


def _summary_cost(project, a, eng, d):
    cost, cur = a["cost"], a["cost"]["currency"]
    fin = _finance(project, a, d)
    rows = [
        (f"Total construction cost ({cur})", _n(cost["total"])),
        ("Split (material / labour / equipment)",
         f'{_n(cost["material"])} / {_n(cost["labour"])} / {_n(cost["equipment"])}'),
        (f"Cost per flat ({cur})", _n(cost["per_unit"])),
        (f"Cost per m2 ({cur})", _n(cost["per_sqm"])),
    ]
    if fin:
        rows += [
            (f"Gross revenue ({cur})", _n(fin["revenue"]["gross"])),
            (f"Net profit ({cur})", _n(fin["profit"]["net"])),
            ("Margin / return on cost (%)",
             f'{_n(fin["profit"]["margin_pct"])} / {_n(fin["profit"]["roi_pct"])}'),
            ("Annual IRR (%)", _n(fin["profit"]["irr_pct"])
             if fin["profit"]["irr_pct"] is not None else "not reachable"),
            ("Cash positive from month", fin["timing"]["payback_month"]
             if fin["timing"]["payback_month"] is not None else "never"),
            (f"Peak funding needed ({cur})", _n(fin["timing"]["peak_funding_need"])),
        ]
        verdict = (f'{_n(fin["revenue"]["gross"])} {cur} of revenue against '
                   f'{_n(fin["cost"]["total"])} {cur} of cost - a '
                   f'{_n(fin["profit"]["margin_pct"])}% margin and '
                   f'{_n(fin["profit"]["roi_pct"])}% return on cost.')
    else:
        verdict = (f'{_n(cost["total"])} {cur} of construction cost. No feasibility '
                   'assumptions are set, so revenue and return are not modelled.')
    return verdict, rows


def _summary_programme(project, a, eng, d):
    plan = _programme(project, a, d)
    if not plan:
        return ("The programme could not be generated. Check that towers, floors and "
                "quantities are set."), []
    saf = plan.get("safety") or {}
    crit = [x for x in plan.get("activities", []) if x.get("critical")]
    rows = [
        ("Start", plan["start"]),
        ("Completion", plan["finish"]),
        ("Duration", f'{_n(plan["duration_months"])} months '
                     f'({_n(plan["duration_calendar_days"])} calendar days)'),
        ("Phases / tasks", f'{len(plan.get("phases") or [])} / {_n(plan.get("activity_count"))}'),
        ("Tasks on the critical path", len(crit)),
        ("Floor cycle (days)", _n(saf.get("floor_cycle_days"))),
        ("Irreducible cure/prop time (days)", _n(saf.get("prop_removal_days"))),
        ("Safety findings", len(saf.get("findings") or [])),
    ]
    verdict = (f'{_n(plan["duration_months"])} months from {plan["start"]} to '
               f'{plan["finish"]}, on a {_n(saf.get("floor_cycle_days"))}-day floor cycle '
               'that IS 456 curing and prop-removal minimums will not let you compress.')
    return verdict, rows


def _summary_compliance(project, a, eng, d):
    c = a["compliance"]
    failed = [r for r in c["results"] if r["status"] != "pass"]
    rows = [
        ("Rules evaluated", c["total"]),
        ("Passed / failed", f'{c["passed"]} / {c["failed"]}'),
        ("Score (%)", _n(c["score"])),
        ("Overall", c["overall"].upper()),
        ("Failing rules", ", ".join(r["label"] for r in failed[:4]) or "none"),
    ]
    if eng:
        rows += [
            ("Accessibility (NBC Part 3)",
             f'{eng["modules"]["accessibility"]["score"]}%'),
            ("NBC parking checks", f'{eng["modules"]["parking_nbc"]["score"]}%'),
        ]
    verdict = (f'{c["passed"]} of {c["total"]} rules pass ({c["score"]}%). '
               + ("The scheme is compliant against every rule configured for it."
                  if not failed else
                  f'{len(failed)} rule(s) fail: ' + "; ".join(r["label"] for r in failed[:3])
                  + ". A failing rule is a sanction risk, not a modelling error."))
    return verdict, rows


def _summary_datahealth(project, a, eng, d):
    dh = _datahealth(project, d)
    if not dh:
        return "The data reliability report could not be generated for this project.", []
    s, n = dh["scores"], dh["counts"]
    rows = [
        ("Overall reliability", f'{dh["overall_score"]}/100'),
        ("Completeness (were the inputs supplied?)", f'{s["completeness"]}%'),
        ("Freshness (were the artefacts rebuilt?)", f'{s["freshness"]}%'),
        ("Consistency (do stored facts agree?)", f'{s["consistency"]}%'),
        ("Stale artefacts", n["stale"]),
        ("Inconsistencies", n["mismatched"]),
        ("Critical code warnings", n["critical_warnings"]),
        ("Missing engineering inputs", n["missing_inputs"]),
        ("Compliance rules failed", n["rules_failed"]),
    ]
    faults = []
    if n["stale"]:
        faults.append(f'{n["stale"]} derived artefact(s) are stale')
    if n["mismatched"]:
        faults.append(f'{n["mismatched"]} stored fact(s) disagree')
    if n["missing_inputs"]:
        faults.append(f'{n["missing_inputs"]} engineering input(s) are missing')
    if s["completeness"] < 100:
        faults.append(f'input completeness is {s["completeness"]}%, so some figures rest on '
                      'defaults rather than on entered values')
    verdict = (f'This project document scores {dh["overall_score"]}/100 for reliability. '
               + ("Every input is entered, every derived artefact is current and every "
                  "stored fact agrees with its counterpart."
                  if not faults else
                  "; ".join(faults).capitalize()
                  + ". This grades the project document, never the engineering — the "
                    "groups and artefacts below name each one."))
    return verdict, rows


def _summary_floorplans(project, a, eng, d):
    towers = project.get("towers") or []
    count = len(towers)
    total_units = a["areas"]["total_units"]
    verdict = f"{count} tower{'s' if count != 1 else ''} with generated architectural floor plate layouts across {total_units} total units."
    rows = [
        ("Towers with layouts", str(count)),
        ("Total units", str(total_units)),
        ("Total built-up area", f"{_n(a['areas']['builtup_area_sqm'])} m²"),
        ("Drawing standard", "2D Architectural Presentation Plan"),
    ]
    return verdict, rows


SUMMARIES = {
    "executive": _summary_executive,
    "plot": _summary_plot,
    "site": _summary_site,
    "planning": _summary_planning,
    "floorplans": _summary_floorplans,
    "parking": _summary_parking,
    "layout": _summary_layout,
    "calculations": _summary_calculations,
    "engineering": _summary_engineering,
    "structural": _summary_structural,
    "water": _summary_water,
    "fire": _summary_fire,
    "sustainability": _summary_sustainability,
    "boq": _summary_boq,
    "cost": _summary_cost,
    "programme": _summary_programme,
    "compliance": _summary_compliance,
    "datahealth": _summary_datahealth,
}


def _summary_block(el, ss, report_type: str, project: dict, a: dict, eng: dict,
                   d: dict):
    """"Report Summary" -- the report's own answer, before its workings.

    Failure here must never cost the reader the report: a summary that cannot be built is
    dropped with a line saying so, and the body follows as normal.
    """
    fn = SUMMARIES.get(report_type)
    if not fn:
        return
    el += [Paragraph("Report Summary", ss["Sec"])]
    try:
        verdict, rows = fn(project, a, eng, d)
    except Exception as exc:
        el += [Paragraph(f"The summary could not be assembled ({exc}). The detailed "
                         "sections below are unaffected.", ss["Sub"])]
        return
    el += [Paragraph(f"<b>{verdict}</b>", ss["Sub"]), Spacer(1, 4)]
    if rows:
        el += [_table([["Finding", "Value"]] + [[k, str(v)] for k, v in rows],
                      col_widths=[85 * mm, 80 * mm], align_right_from=2)]


def build_pdf(report_type: str, project: dict, a: dict, eng: dict = None) -> bytes:
    report_type = MERGED_INTO.get(report_type, report_type)
    if report_type == "township":
        # The township report is built from a master plan, not from the project analysis;
        # without this the generic path produced a header and a metrics table and nothing else.
        from autonomous_planning import township_mixed_use_plan
        plan = township_mixed_use_plan(project, {})
        plan.setdefault("is_mixed_use", True)
        return build_township_pdf(project, plan)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    ss = _styles()
    el = [
        Paragraph("APTIMIZER", ParagraphStyle("brand", fontSize=10, textColor=BRAND, spaceAfter=2)),
        Paragraph(REPORT_TITLES.get(report_type, "Project Report"), ss["H"]),
        Paragraph(f"Project: {project.get('name', '')} &nbsp;|&nbsp; Client: {project.get('client', '-')} "
                  f"&nbsp;|&nbsp; Location: {project.get('location', '-')} &nbsp;|&nbsp; "
                  f"Generated: {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}", ss["Sub"]),
        Spacer(1, 8),
    ]
    areas, boq, cost = a["areas"], a["boq"], a["cost"]
    cur = cost["currency"]

    derived = {}
    _summary_block(el, ss, report_type, project, a, eng, derived)

    if report_type in SITE_REPORTS:
        # Site-stage reports quote the site figures only; unit counts and built-up area
        # belong to the design stage and would disagree with the layout engine's yield.
        el += [Paragraph("Key Site Metrics", ss["Sec"]), _kv([
            ("Plot Area (m²)", _n(areas["plot_area_sqm"])),
            ("Plot Area (acres)", _n(areas["plot_area_acres"])),
            ("FAR / FSI", f"{areas['far']:.2f} / {areas['fsi']:.2f}"),
            ("Ground Coverage (%)", _n(areas["ground_coverage_pct"])),
            ("Open Space (%)", _n(areas["open_space_pct"])),
        ])]
    elif report_type in REPORT_TITLES:
        el += [Paragraph("Key Project Metrics", ss["Sec"]), _kv([
            ("Plot Area (m²)", _n(areas["plot_area_sqm"])),
            ("Plot Area (acres)", _n(areas["plot_area_acres"])),
            ("Total Units", areas["total_units"]),
            ("Built-up Area (m²)", _n(areas["builtup_area_sqm"])),
            ("FAR / FSI", f"{areas['far']:.2f} / {areas['fsi']:.2f}"),
            ("Ground Coverage (%)", _n(areas["ground_coverage_pct"])),
        ])]

    if report_type in ("boq", "executive"):
        el += [Paragraph("Material Summary", ss["Sec"]),
               _table([["Item", "Unit", "Quantity", f"Rate ({cur})", f"Amount ({cur})"]] +
                      [[m["label"], m["unit"], _n(m["quantity"]), _n(m["rate"]), _n(m["amount"])]
                       for m in boq["materials"]] +
                      [["Material Total", "", "", "", _n(boq["material_total"])]],
                      col_widths=[60 * mm, 18 * mm, 28 * mm, 28 * mm, 33 * mm])]
    if report_type == "boq":
        el += [Paragraph("Labour Summary", ss["Sec"]),
               _table([["Trade", "Unit", "Man-days", f"Wage ({cur})", f"Amount ({cur})"]] +
                      [[l["label"], l["unit"], _n(l["quantity"]), _n(l["rate"]), _n(l["amount"])]
                       for l in boq["labour"]] +
                      [["Labour Total", "", "", "", _n(boq["labour_total"])]],
                      col_widths=[60 * mm, 20 * mm, 26 * mm, 28 * mm, 33 * mm]),
               Paragraph("Equipment Summary", ss["Sec"]),
               _table([["Equipment", "Unit", "Days", f"Rate ({cur})", f"Amount ({cur})"]] +
                      [[e["label"], e["unit"], _n(e["quantity"]), _n(e["rate"]), _n(e["amount"])]
                       for e in boq["equipment"]] +
                      [["Equipment Total", "", "", "", _n(boq["equipment_total"])]],
                      col_widths=[60 * mm, 20 * mm, 26 * mm, 28 * mm, 33 * mm])]

    if report_type == "plot":
        plot = project.get("plot") or {}
        edges = plot.get("road_edges") or []
        el += [Paragraph("Plot Geometry", ss["Sec"]), _kv([
            ("Plot area (m²)", _n(areas["plot_area_sqm"])),
            ("Plot area (acres)", _n(areas["plot_area_acres"])),
            ("Boundary vertices", len(plot.get("coordinates") or [])),
            ("Recorded length x width (m)", f'{_n(plot.get("length"))} x {_n(plot.get("width"))}'),
            ("Orientation (deg)", _n(plot.get("orientation_deg") or 0)),
            ("Ground footprint (m²)", _n(areas["ground_footprint_sqm"])),
            ("Ground coverage (%)", _n(areas["ground_coverage_pct"])),
            ("Open space (m²)", _n(areas["open_space_sqm"])),
            ("Open space (%)", _n(areas["open_space_pct"])),
        ])]
        if edges:
            el += [Paragraph("Road-Facing Edges", ss["Sec"]),
                   _table([["Edge", "Width (m)"]]
                          + [[f'Edge {e.get("edge_index")}', _n(e.get("width"))] for e in edges],
                          col_widths=[60 * mm, 40 * mm])]
        else:
            el += [Paragraph("No road-facing edges are marked, so the front setback cannot "
                             "be assigned to a specific boundary.", ss["Sub"])]

    # ---------------------------------------------------------------- setbacks
    if report_type in ("compliance", "plot"):
        try:
            import siteplan as _sp
            applied = ((project.get("dev_controls") or {}).get("setbacks")
                       or {"default": 6.0, "front": 9.0, "rear": 4.5, "side": 4.5})
            plot = project.get("plot") or {}
            road = max([float(e.get("width") or 0)
                        for e in (plot.get("road_edges") or [])] or [0.0])
            chk = _sp.validate_setbacks(
                applied, plot_area=float(areas["plot_area_sqm"] or 0),
                road_width=road, height_m=float(areas["max_height_m"] or 0))
            rows = [[r["edge"].title(), _n(r["applied_m"]), _n(r["minimum_m"]),
                     "PASS" if r["ok"] else "FAIL",
                     _n(r["shortfall_m"]) if r["shortfall_m"] else "-"]
                    for r in chk["edges"]]
            el += [Paragraph("Setbacks & Development Controls", ss["Sec"]),
                   _table([["Edge", "Applied (m)", "Minimum (m)", "Status", "Short by (m)"]]
                          + rows,
                          col_widths=[32 * mm, 28 * mm, 28 * mm, 24 * mm, 30 * mm]),
                   Paragraph(f"Minimums are set by {chk['edges'][0]['clause']}. Side and rear "
                             f"open space scales with building height, which is "
                             f"{_n(areas['max_height_m'])} m here; the front setback is the "
                             "larger of the plot-size and height requirements.", ss["Sub"])]
            if not chk["ok"]:
                short = ", ".join(f'{r["edge"]} by {r["shortfall_m"]} m'
                                  for r in chk["edges"] if not r["ok"])
                el += [Paragraph(f"<b>Not sanctionable as drawn:</b> {short}.", ss["Sub"])]
            if chk.get("note"):
                el += [Paragraph(chk["note"], ss["Sub"])]
        except Exception as exc:
            el += [Paragraph("Setbacks & Development Controls", ss["Sec"]),
                   Paragraph(f"Could not be evaluated: {exc}", ss["Sub"])]

    if report_type == "boq":
        el += [Paragraph("Estimated Quantities", ss["Sec"]),
               _table([["Material", "Unit", "Ratio", "Basis", "Quantity"]] +
                      [[q["label"], q["unit"], _n(q["ratio"]), q["basis"], _n(q["quantity"])]
                       for q in a["quantities"]["items"]],
                      col_widths=[60 * mm, 20 * mm, 26 * mm, 25 * mm, 34 * mm])]

    if report_type in ("cost", "executive"):
        el += [Paragraph("Cost Summary", ss["Sec"]), _kv([
            (f"Material Cost ({cur})", _n(cost["material"])),
            (f"Labour Cost ({cur})", _n(cost["labour"])),
            (f"Equipment Cost ({cur})", _n(cost["equipment"])),
            (f"Total Construction Cost ({cur})", _n(cost["total"])),
            (f"Cost per Flat ({cur})", _n(cost["per_unit"])),
            (f"Cost per m² ({cur})", _n(cost["per_sqm"])),
        ])]

    if report_type in ("parking", "executive"):
        p = a["parking"]
        el += [Paragraph("Parking Summary", ss["Sec"]), _kv([
            ("Required Slots", p["required_slots"]), ("Provided Slots", p["provided_slots"]),
            ("Basement Slots", p["basement_slots"]), ("Ground Slots", p["ground_slots"]),
            ("Visitor (req/prov)", f"{p['visitor_required']} / {p['visitor_provided']}"),
            ("EV (req/prov)", f"{p['ev_required']} / {p['ev_provided']}"),
            ("Accessible (req/prov)", f"{p['accessible_required']} / {p['accessible_provided']}"),
            ("Deficit", p["deficit"]), ("Area per slot (m²)", _n(p["area_per_slot_actual"])),
            ("Ramp validation", "PASS" if p["ramp_pass"] else "FAIL"),
        ])]

    # The parking module's own workings: the norm the demand came from, the per-building
    # demand it was assembled out of, and the checks it is graded on. A site-wide total
    # cannot show any of that, which is why parking is a report again rather than a block
    # inside the Executive Summary.
    if report_type == "parking":
        p = a["parking"]
        norm = p.get("norm") or {}
        el += [Paragraph("Governing Norm", ss["Sec"]), _kv([
            ("Authority", norm.get("authority", "-")),
            ("State", norm.get("state", "-")),
            ("Basis", norm.get("basis") or "-"),
            ("Verified against the published rule", "yes" if norm.get("verified") else "no"),
            ("Visitor share of car spaces (%)", _n(norm.get("visitor_pct"))),
            ("Ratio applied (slots per flat)", _n(p.get("ratio_per_unit"))),
        ])]
        if norm.get("note"):
            el += [Paragraph(norm["note"], ss["Sub"])]
        if not norm.get("verified"):
            el += [Paragraph("<b>The rule in use is an unverified default.</b> Parking is "
                             "set by the local development control regulation, which varies "
                             "by city and is revised periodically. Confirm the ratio with "
                             "the sanctioning authority before it is designed to.", ss["Sub"])]

        by_tower = p.get("towers") or []
        if by_tower:
            el += [Paragraph("Per Tower — Demand and Supply", ss["Sec"]),
                   _table([["Tower", "Floors", "Flats", "Cars required", "Two-wheelers",
                            "Own slots", "Stilt", "Podium"]] +
                          [[t.get("name"), t.get("floors"), t.get("units"),
                            _n(t.get("cars_required")), _n(t.get("scooters_required")),
                            _n(t.get("own_slots")), _n(t.get("stilt_slots")),
                            _n(t.get("podium_slots"))] for t in by_tower],
                          col_widths=[28 * mm, 16 * mm, 16 * mm, 26 * mm, 26 * mm,
                                      22 * mm, 18 * mm, 20 * mm]),
                   Paragraph("Demand is computed per building from its own unit mix, not "
                             "apportioned from a site total. Sanction is granted per "
                             "building, so a site-wide surplus does not excuse a building "
                             "that is short of its own requirement.", ss["Sub"])]
            if p.get("buildings_short"):
                el += [Paragraph("<b>Short of their own demand:</b> "
                                 + ", ".join(p["buildings_short"]) + ".", ss["Sub"])]

        el += [Paragraph("Supply Efficiency", ss["Sec"]), _kv([
            ("Total parking area (m²)", _n(p.get("total_parking_area_sqm"))),
            ("Area per slot achieved (m²)", _n(p.get("area_per_slot_actual"))),
            ("Benchmark area per ECS (m²)", _n(p.get("benchmark_area_per_ecs_sqm"))),
            ("Layout efficiency (%)", _n(p.get("efficiency_pct"))),
            ("Slots held in the shared pool", _n(p.get("shared_pool"))),
            ("Shared slots allocated", _n(p.get("shared_allocated"))),
            ("Two-wheelers required", _n(p.get("scooters_required"))),
            ("Two-wheelers as ECS", _n(p.get("scooter_ecs_equivalent"))),
        ])]

        checks = p.get("checks") or []
        if checks:
            el += [Paragraph("Parking Checks", ss["Sec"]),
                   _table([["Check", "Result", "Status"]] +
                          [[c["label"], str(c.get("value", "")),
                            "PASS" if c.get("pass") else "FAIL"] for c in checks],
                          col_widths=[96 * mm, 42 * mm, 22 * mm], align_right_from=2)]
        ramp = p.get("ramp_checks") or []
        if ramp:
            el += [Paragraph("Ramp Geometry", ss["Sec"]),
                   _table([["Check", "Actual", "Status"]] +
                          [[c["label"], str(c.get("value", "")),
                            "PASS" if c.get("pass") else "FAIL"] for c in ramp],
                          col_widths=[96 * mm, 42 * mm, 22 * mm], align_right_from=2)]
        for w in (p.get("warnings") or []):
            el += [Paragraph(f'<b>[{str(w.get("severity", "note")).upper()}]</b> '
                             f'{w.get("text", "")}', ss["Sub"])]

    # "utilities" resolves to "water" before this point, so the merged id can never be the
    # one tested here -- naming it was why the sizing table went missing from the report
    # that absorbed it.
    if report_type in ("water", "executive"):
        u = a["utilities"]
        el += [Paragraph("Utility Planning", ss["Sec"]), _kv([
            ("Population", u["persons"]), ("Water Demand (litres/day)", _n(u["water_demand_lpd"])),
            ("UG Tank (m³)", _n(u["ug_tank_cum"])), ("OH Tank (m³)", _n(u["oh_tank_cum"])),
            ("STP Capacity (KLD)", _n(u["stp_capacity_kld"])), ("WTP Capacity (KLD)", _n(u["wtp_capacity_kld"])),
            ("Rainwater Harvest (litres/yr)", _n(u["rwh_annual_litres"])),
            ("Electrical Room (m²)", _n(u["electrical_room_sqm"])), ("Pump Room (m²)", _n(u["pump_room_sqm"])),
        ])]
    if report_type == "water":
        u = a["utilities"]
        el += [Paragraph("Demand Build-up", ss["Sec"]), _kv([
            ("Consumption basis (IS 1172:1993)", f'{_n(u["lpcd"])} litre per person per day'),
            ("Domestic (litre/day)", _n(u["domestic_lpd"])),
            ("Flushing (litre/day)", _n(u["flushing_lpd"])),
            ("External / landscape (litre/day)", _n(u["external_lpd"])),
            ("Sump volume (litre)", _n(u["ug_tank_litres"])),
            ("Overhead volume (litre)", _n(u["oh_tank_litres"])),
            ("RWH storage (m³)", _n(u["rwh_storage_cum"])),
            ("Annual rainfall assumed (mm)", _n(u["annual_rainfall_mm"])),
            ("Connected electrical load (kW)", _n(u["connected_load_kw"])),
        ]), Paragraph("Domestic and flushing are split because only the flushing share can "
                      "be met from treated STP output; the external allowance is landscape "
                      "and washdown, which is the first demand to drop in a shortage.",
                      ss["Sub"])]

    if report_type in ("compliance", "executive"):
        c = a["compliance"]
        el += [Paragraph(f"Compliance — {c['passed']}/{c['total']} rules passed "
                         f"(score {c['score']}%)", ss["Sec"]),
               _table([["Code", "Rule", "Limit", "Actual", "Status"]] +
                      [[r["code"], r["label"],
                        f"{'max' if r['operator'] == 'max' else 'min'} {r['threshold']}{r['unit']}",
                        _n(r["actual"]), r["status"].upper()] for r in c["results"]],
                      col_widths=[22 * mm, 72 * mm, 32 * mm, 25 * mm, 20 * mm], align_right_from=2)]

    if eng and report_type == "structural":
        m = eng["modules"]
        el += [Paragraph("1. Structural Load Estimator — IS 875 Parts 1–3", ss["Sec"]), _mod_table(m["loads"]),
               Paragraph(f"<b>{m['loads']['recommendation']['label']}:</b> {m['loads']['recommendation']['value']}", ss["Sub"]),
               Paragraph("2. Seismic Design — IS 1893 (Part 1):2016", ss["Sec"]), _mod_table(m["seismic"])]
        for w in m["seismic"]["warnings"]:
            el += [Paragraph(f"<b>{w['severity'].upper()}:</b> {w['text']}", ss["Sub"])]
        el += [Paragraph("3. Foundation Advisor — IS 6403 / IS 1904", ss["Sec"]), _mod_table(m["foundation"]),
               Paragraph(f"<b>Recommended:</b> {m['foundation']['recommendation']['value']}", ss["Sub"]),
               Paragraph("4. Concrete Mix Design — IS 10262:2019", ss["Sec"]), _mod_table(m["mix"]),
               Paragraph("12. Column Grid", ss["Sec"]), _mod_table(m["grid"])]
        if m["loads"].get("per_tower"):
            el += [Paragraph("Per-tower load & wind summary", ss["Sec"]),
                   _table([["Tower", "Floors", "Height (m)", "Footprint (m²)", "Column load (kN)",
                            "Column size (mm)", "Wind force (kN)"]] +
                          [[t["name"], t["floors"], _n(t["height_m"]), _n(t["footprint_sqm"]),
                            _n(t["column_load_kn"]), t["column_size_mm"], _n(t["wind_force_kn"])]
                           for t in m["loads"]["per_tower"]],
                          col_widths=[28 * mm, 16 * mm, 22 * mm, 26 * mm, 28 * mm, 26 * mm, 26 * mm])]
        if m["seismic"].get("per_tower"):
            el += [Paragraph("Per-tower seismic summary — IS 1893 Cl. 7.6", ss["Sec"]),
                   _table([["Tower", "Height (m)", "Ta (s)", "Sa/g", "Ah", "W (kN)", "VB (kN)", "VB/W (%)"]] +
                          [[t["name"], _n(t["height_m"]), _n(t["period_s"]), _n(t["sa_g"]), t["ah"],
                            _n(t["seismic_weight_kn"]), _n(t["base_shear_kn"]), _n(t["base_shear_pct_w"])]
                           for t in m["seismic"]["per_tower"]],
                          col_widths=[26 * mm, 22 * mm, 18 * mm, 18 * mm, 20 * mm, 26 * mm, 26 * mm, 22 * mm])]

    if eng and report_type == "water":
        m = eng["modules"]
        el += [Paragraph("Water Infrastructure — IS 1172:1993 / NBC Part 9", ss["Sec"]), _mod_table(m["water"]),
               Paragraph(f"<b>{m['water']['recommendation']['label']}:</b> {m['water']['recommendation']['value']}", ss["Sub"]),
               Paragraph("Storm Water & Rainwater Harvesting — IS 3764 / NBC Part 9", ss["Sec"]),
               _mod_table(m["storm"]), _checks_table(m["storm"])]

    if eng and report_type == "fire":
        m = eng["modules"]["fire"]
        el += [Paragraph(f"Fire & Life Safety — {m['passed']}/{m['total']} checks passed ({m['score']}%)", ss["Sec"]),
               _checks_table(m), Paragraph("Per-floor checklist", ss["Sec"]),
               _table([["Floor", "Level (m)", "Travel OK", "Extinguishers", "Refuge required", "Status"]] +
                      [[r["floor"], _n(r["level_m"]), "yes" if r["travel_ok"] else "no", r["extinguishers"],
                        "yes" if r["refuge_required"] else "—", r["status"].upper()] for r in m["floor_rows"]],
                      col_widths=[18 * mm, 24 * mm, 24 * mm, 30 * mm, 34 * mm, 26 * mm])]

    if eng and report_type == "compliance":
        m = eng["modules"]["accessibility"]
        el += [Paragraph(f"Accessibility — score {m['score']}% ({m['passed']}/{m['total']} clauses met)", ss["Sec"]),
               _checks_table(m),
               Paragraph("Parking accessibility and NBC parking checks", ss["Sec"]),
               _checks_table(eng["modules"]["parking_nbc"])]

    if eng and report_type == "engineering":
        s = eng["summary"]
        el += [Paragraph("IS / NBC Engineering Summary", ss["Sec"]), _kv([
            ("Design city", f"{eng['city_reference']['city']}, {eng['city_reference']['state']}"),
            ("Seismic zone (IS 1893 Table 3)", s["seismic_zone"]),
            ("Design base shear (kN)", _n(s["base_shear_kn"])),
            ("Recommended column size (mm)", s["column_size"]),
            ("Foundation type (IS 1904)", s["foundation"]),
            ("Concrete mix (IS 10262)", s["mix_ratio"]),
            ("Water demand (litre/day)", _n(s["water_demand_lpd"])),
            ("STP capacity (KLD)", _n(s["stp_kld"])),
            ("RWH annual yield (litre)", _n(s["rwh_annual_l"])),
            ("Fire safety score (%)", _n(s["fire_score"])),
            ("NBC parking score (%)", _n(s["parking_score"])),
            ("Accessibility score (%)", _n(s["accessibility_score"])),
            # m13 and m14. The summary claims to cover the engineering modules, so leaving
            # carbon and plantation out of it would make it quietly wrong.
            ("Embodied carbon (tCO2e)", _n(s.get("embodied_carbon_tco2e"))),
            ("Embodied carbon (kgCO2e/m2)", _n(s.get("carbon_per_sqm_kg"))),
            ("Trees required", _n(s.get("trees_required"))),
            ("Green rating", s["green_rating"]),
        ])]
        if eng["warnings"]:
            el += [Paragraph("Code warnings", ss["Sec"])]
            el += [Paragraph(f"<b>[{w['severity'].upper()}] {w['module']}:</b> {w['text']}", ss["Sub"])
                   for w in eng["warnings"]]
        if eng["missing_inputs"]:
            el += [Paragraph("Missing inputs", ss["Sec"]),
                   Paragraph(", ".join(eng["missing_inputs"]), ss["Sub"])]

    if eng and report_type == "executive":
        s = eng["summary"]
        el += [Paragraph("IS / NBC Engineering Highlights", ss["Sec"]), _kv([
            ("Seismic zone", s["seismic_zone"]), ("Base shear (kN)", _n(s["base_shear_kn"])),
            ("Column size (mm)", s["column_size"]), ("Foundation", s["foundation"]),
            ("Water demand (litre/day)", _n(s["water_demand_lpd"])), ("STP (KLD)", _n(s["stp_kld"])),
            ("Fire safety score (%)", _n(s["fire_score"])),
            ("Accessibility score (%)", _n(s["accessibility_score"])),
            ("Green rating", s["green_rating"]),
        ])]

    if report_type == "executive":
        g = project.get("gis") or {}
        suit, build = g.get("suitability"), g.get("buildability")
        if suit:
            el += [Paragraph(f"Site Intelligence Scorecard — suitability {suit['score']}% ({suit['grade']})", ss["Sec"]),
                   _table([["Factor", "Score", "Weight", "Contribution"]] +
                          [[b["factor"], _n(b["score"]), f"{b['weight_pct']}%", _n(b["contribution"])]
                           for b in suit.get("breakdown", [])],
                          col_widths=[75 * mm, 26 * mm, 26 * mm, 33 * mm])]
        if build:
            el += [Paragraph(f"Buildability — {'buildable' if build['buildable'] else 'constrained'} "
                             f"({build['critical_count']} critical, {build['warning_count']} warning)", ss["Sec"]),
                   _table([["Severity", "Finding", "Detail"]] +
                          [[f["severity"].upper(), f["title"], f["detail"]] for f in build.get("flags", [])],
                          col_widths=[20 * mm, 45 * mm, 95 * mm], align_right_from=3)]
        if not suit and not build:
            el += [Paragraph("Site Intelligence", ss["Sec"]),
                   Paragraph("No GIS site analysis has been run for this project yet — run GIS Intelligence "
                             "to include the site suitability scorecard.", ss["Sub"])]

        # The modules that grew reports of their own still owe the summary one line each:
        # a client reading only this document should learn that the scheme was laid out,
        # audited against the Vastu manual and checked for data reliability, and where to
        # look for the workings.
        sl = project.get("site_layout") or {}
        m = sl.get("layout_metrics") or {}
        if m:
            el += [Paragraph("Site Layout", ss["Sec"]), _kv([
                ("Blocks placed", m.get("tower_count")),
                ("Achieved FAR / cap", f'{m.get("achieved_far")} / {m.get("far_cap")}'),
                ("Ground coverage (%)", _n(m.get("ground_coverage_pct"))),
                ("Open space (%)", _n(m.get("open_space_pct"))),
                ("Amenity blocks reserved", len(sl.get("amenities") or [])),
                ("Surface parking bays", m.get("surface_bays")),
                ("Feasible against hard constraints", "yes" if m.get("feasible") else "no"),
            ]), Paragraph("Full land budget, circulation and placed-block geometry are in "
                          "the Site Layout & Massing report.", ss["Sub"])]

        audits = _vastu_audits(project, derived)
        if audits:
            scored = [au["score"] for _t, per in audits for au in per.values()]
            breaches = sum(len(au.get("violations") or [])
                           for _t, per in audits for au in per.values())
            el += [Paragraph("Apartment Planning & Vastu", ss["Sec"]), _kv([
                ("Floor plates laid out",
                 sum(len(per) for _t, per in audits)),
                ("Mean Vastu score (%)", round(sum(scored) / len(scored), 1) if scored else "-"),
                ("Hard rule breaches", breaches),
            ]), Paragraph("Sector anchors, per-flat scores and the violation list are in "
                          "the Apartment Planning & Vastu report.", ss["Sub"])]

        dh = _datahealth(project, derived)
        if dh:
            s, n = dh["scores"], dh["counts"]
            el += [Paragraph(f'Data Reliability — {dh["overall_score"]}/100', ss["Sec"]), _kv([
                ("Completeness (%)", s["completeness"]),
                ("Freshness (%)", s["freshness"]),
                ("Consistency (%)", s["consistency"]),
                ("Stale artefacts", n["stale"]),
                ("Inconsistencies", n["mismatched"]),
                ("Missing engineering inputs", n["missing_inputs"]),
            ]), Paragraph("This grades the project document, not the engineering: it says "
                          "whether the figures above rest on inputs that were entered or on "
                          "defaults standing in for them. The Data Reliability report names "
                          "each one.", ss["Sub"])]


    # ---------------------------------------------------------------- per tower
    # A blended total hides which tower drives which number, which is the whole reason a
    # reader opens a per-tower report.
    if eng and report_type == "structural":
        _tower_structural(el, ss, a, eng)
    if report_type == "boq":
        _tower_boq(el, ss, a, cur)
    if report_type == "cost":
        _tower_cost(el, ss, project, a, cur, derived)
    if report_type == "water":
        _tower_water(el, ss, a)
    if report_type == "compliance":
        _tower_compliance(el, ss, a)
    if eng and report_type == "sustainability":
        _tower_carbon(el, ss, a, eng)

    # ---------------------------------------------------------------- programme
    if report_type == "programme":
        plan = _programme(project, a, derived)
        if not plan:
            el += [Paragraph("Construction Programme", ss["Sec"]),
                   Paragraph("The programme could not be generated for this project. Check "
                             "that towers, floors and quantities are set.", ss["Sub"])]
        else:
            saf = plan.get("safety") or {}
            el += [Paragraph("Programme Summary", ss["Sec"]), _kv([
                ("Start", plan["start"]), ("Completion", plan["finish"]),
                ("Duration (months)", _n(plan["duration_months"])),
                ("Calendar days", _n(plan["duration_calendar_days"])),
                ("Tasks", _n(plan.get("activity_count"))),
                ("Floor cycle (days)", _n(saf.get("floor_cycle_days"))),
            ])]
            el += [Paragraph("Phase Breakdown", ss["Sec"]),
                   _table([["Phase", "Start", "Finish", "Days", "Tasks", f"Cost ({cur})"]] +
                          [[ph["phase"], ph["start"], ph["finish"], _n(ph["calendar_days"]),
                            _n(ph["activities"]), _n(ph["cost"])] for ph in plan["phases"]],
                          col_widths=[42 * mm, 26 * mm, 26 * mm, 18 * mm, 18 * mm, 34 * mm])]
            crit = [x for x in plan.get("activities", []) if x.get("critical")][:20]
            if crit:
                el += [Paragraph("Critical Path (first 20 tasks)", ss["Sec"]),
                       _table([["Task", "Start", "Finish", "Days"]] +
                              [[x["name"], x["start"], x["finish"], _n(x["work_days"])]
                               for x in crit],
                              col_widths=[86 * mm, 26 * mm, 26 * mm, 26 * mm])]
            if saf:
                el += [Paragraph("Safety Basis (IS 456)", ss["Sec"]),
                       Paragraph(f"The floor cycle cannot be compressed below "
                                 f"{saf.get('prop_removal_days')} calendar days — "
                                 f"{saf.get('governing_rule')}. Adding labour shortens every "
                                 "other activity, never this one.", ss["Sub"])]
            _tower_programme(el, ss, project, a, plan)
            findings = (saf.get("findings") or [])
            if findings:
                el += [Paragraph("Safety Findings", ss["Sec"])] + \
                      [Paragraph(f"<b>[{f['severity'].upper()}]</b> {f['text']}", ss["Sub"])
                       for f in findings]

    # ---------------------------------------------------------------- feasibility
    if report_type == "cost":
        fin = _finance(project, a, derived)
        if fin:
            el += [Paragraph("Feasibility & Return", ss["Sec"]), _kv([
                ("Gross revenue", _n(fin["revenue"]["gross"])),
                ("Total project cost", _n(fin["cost"]["total"])),
                ("Net profit", _n(fin["profit"]["net"])),
                ("Margin (%)", _n(fin["profit"]["margin_pct"])),
                ("Return on cost (%)", _n(fin["profit"]["roi_pct"])),
                ("Annual IRR (%)", _n(fin["profit"]["irr_pct"])
                 if fin["profit"]["irr_pct"] is not None else "not reachable"),
                ("Cash positive (month)", fin["timing"]["payback_month"]
                 if fin["timing"]["payback_month"] is not None else "never"),
                ("Peak funding needed", _n(fin["timing"]["peak_funding_need"])),
                ("Break-even sale rate (per sqft)", _n(fin["break_even"]["sale_rate_per_sqft"])),
                ("Break-even flats", _n(fin["break_even"]["units"])),
            ])]
            el += [Paragraph("Revenue by Flat Type", ss["Sec"]),
                   _table([["Type", "Flats", "Saleable (sqft)", f"Rate ({cur})", f"Revenue ({cur})"]] +
                          [[r["type"].upper(), _n(r["units"]), _n(r["saleable_sqft"]),
                            _n(r["rate_per_sqft"]), _n(r["revenue"])]
                           for r in fin["revenue"]["by_type"]],
                          col_widths=[36 * mm, 22 * mm, 38 * mm, 30 * mm, 41 * mm])]
            flow = fin.get("cash_flow") or []
            if flow:
                every3 = [f for i, f in enumerate(flow) if i % 3 == 0]
                el += [Paragraph("Cash Flow (every third month)", ss["Sec"]),
                       _table([["Month", f"Out ({cur})", f"In ({cur})", f"Cumulative ({cur})"]] +
                              [[f["month"], _n(f["outflow"]), _n(f["inflow"]), _n(f["cumulative"])]
                               for f in every3],
                              col_widths=[24 * mm, 46 * mm, 46 * mm, 51 * mm])]

    if report_type == "cost":
        opt = _optimisers(project, a, eng)
        if opt:
            rows = []
            for key, o in opt.items():
                # Not `cur`: that name holds the currency for the rest of this function,
                # and shadowing it here left later sections printing an optimiser dict.
                now, best = o["current"], o["best"]
                unit = now.get("unit") or ""
                change = "; ".join(
                    f'{c["lever"]}: {c["from"]} -> {c["to"]}'
                    for c in (o.get("changes") or [])[:2]) or "no change available"
                rows.append([o["title"],
                             f'{_n(now["value"])} {unit}'.strip(),
                             f'{_n(best["value"])} {unit}'.strip(),
                             change])
            el += [Paragraph("Optimisation Findings", ss["Sec"]),
                   Paragraph("Each row is what the scheme does today, the best the search "
                             "found, and the change that gets there. Nothing here has been "
                             "applied.", ss["Sub"]),
                   _table([["Optimiser", "Now", "Best found", "Change required"]] + rows,
                          col_widths=[34 * mm, 28 * mm, 28 * mm, 77 * mm],
                          align_right_from=4)]

    # ---------------------------------------------------------------- site
    if report_type == "site":
        g = project.get("gis") or {}
        if not g:
            el += [Paragraph("Site Analysis", ss["Sec"]),
                   Paragraph("No site analysis has been run. Open GIS Intelligence and run it "
                             "to populate this report.", ss["Sub"])]
        else:
            t, fl = g.get("terrain") or {}, g.get("flood") or {}
            el += [Paragraph("Terrain & Flood", ss["Sec"]), _kv([
                ("Average slope (%)", _n(t.get("avg_slope_pct"))),
                ("Slope class", t.get("slope_class", "-")),
                ("Relief (m)", _n(t.get("relief_m"))),
                ("Flood risk", fl.get("level", "-")),
                ("Flood score", _n(fl.get("score"))),
            ])]
            fac = (g.get("sun") or {}).get("facades") or []
            if fac:
                el += [Paragraph("Sun & Orientation", ss["Sec"]),
                       _table([["Facade", "Bearing", "Sun hours", "Guidance"]] +
                              [[f["facade"], f"{f['bearing_deg']}°", _n(f["sun_hours_equinox"]),
                                f["recommendation"]] for f in fac],
                              col_widths=[32 * mm, 20 * mm, 22 * mm, 90 * mm], align_right_from=4)]
            wind = g.get("wind") or {}
            if wind:
                el += [Paragraph(f"Wind — {wind.get('region', '-')}", ss["Sec"]), _kv([
                    ("Prevailing direction", wind.get("prevailing", "-")),
                    ("Summer / monsoon direction", wind.get("summer", "-")),
                    ("Winter direction", wind.get("winter", "-")),
                    ("Mean speed (m/s)", _n(wind.get("mean_speed_ms"))),
                ])]
                rose = wind.get("rose") or []
                if rose:
                    el += [_table([["Direction"] + [r["direction"] for r in rose],
                                   ["Frequency (%)"] + [str(r["frequency_pct"]) for r in rose]],
                                  col_widths=[30 * mm] + [16 * mm] * len(rose))]
                el += [Paragraph(str(wind.get("guidance") or ""), ss["Sub"])]

            # The accessibility block used to print keys the GIS module does not return,
            # so it rendered a dash on every project. It returns `notes` and a score.
            acc = g.get("accessibility") or {}
            if acc:
                el += [Paragraph(f"Access — {acc.get('score', '-')}/100", ss["Sec"]), _kv([
                    ("Nearest road", f'{_n(acc.get("nearest_road_m"))} m '
                                     f'({acc.get("nearest_road_kind") or "unclassified"})'
                     if acc.get("nearest_road_m") is not None else "none mapped in radius"),
                    ("Widest road nearby (m)", _n(acc.get("widest_road_m"))
                     if acc.get("widest_road_m") is not None else "-"),
                    ("Roads within 100 m", acc.get("roads_within_100m", 0)),
                    ("Nearest transit stop (m)", _n(acc.get("nearest_transit_m"))
                     if acc.get("nearest_transit_m") is not None else "none mapped in radius"),
                ])]
                el += [Paragraph(f"· {n}", ss["Sub"]) for n in (acc.get("notes") or [])]

            counts = g.get("feature_counts") or {}
            if counts:
                el += [Paragraph("Surrounding Context", ss["Sec"]),
                       _table([["Feature", "Count within the search radius"]] +
                              [[k.replace("_", " ").title(), str(v)]
                               for k, v in sorted(counts.items())],
                              col_widths=[100 * mm, 65 * mm]),
                       Paragraph(f'Mapped from OpenStreetMap within {g.get("radius_m")} m of '
                                 f'the plot centroid. A count of zero means nothing of that '
                                 'kind is mapped there, which is not the same as nothing '
                                 'being there.', ss["Sub"])]

            suit = g.get("suitability")
            if suit:
                el += [Paragraph(f"Suitability — {suit['score']}% ({suit['grade']})", ss["Sec"]),
                       _table([["Factor", "Score", "Weight", "Contribution"]] +
                              [[b["factor"], _n(b["score"]), f"{b['weight_pct']}%", _n(b["contribution"])]
                               for b in suit.get("breakdown", [])],
                              col_widths=[75 * mm, 26 * mm, 26 * mm, 33 * mm])]

            build = g.get("buildability")
            if build:
                el += [Paragraph(f"Buildability — "
                                 f"{'buildable' if build['buildable'] else 'constrained'} "
                                 f"({build['critical_count']} critical, "
                                 f"{build['warning_count']} warning)", ss["Sec"]),
                       _table([["Severity", "Finding", "Detail"]] +
                              [[f["severity"].upper(), f["title"], f["detail"]]
                               for f in build.get("flags", [])]
                              or [["-", "No flag raised", "Nothing on this site trips a "
                                   "buildability check."]],
                              col_widths=[20 * mm, 45 * mm, 100 * mm], align_right_from=3)]

            solar = g.get("solar")
            if solar:
                cfg = solar.get("config") or {}
                ins = solar.get("insolation") or {}
                el += [Paragraph("Rooftop Solar Potential", ss["Sec"]), _kv([
                    ("Terrace area (m²)", _n(solar["roof_area_sqm"])),
                    ("Usable after lifts, tanks and access (m²)", _n(solar["usable_area_sqm"])),
                    ("Installable (kWp)", _n(solar["installable_kwp"])),
                    ("Annual generation (kWh)", _n(solar["annual_yield_kwh"])),
                    ("Specific yield (kWh per kWp)", _n(solar["specific_yield_kwh_per_kwp"])),
                    ("Annual insolation (kWh/m²)", _n(ins.get("annual_kwh_per_sqm"))),
                    ("Capital cost", _n(solar["capex_inr"])),
                    ("Annual saving", _n(solar["annual_saving_inr"])),
                    ("Payback (years)", _n(solar["payback_years"])
                     if solar.get("payback_years") else "not reached in the system life"),
                    ("Lifetime generation (kWh)", _n(solar["lifetime_kwh"])),
                    ("CO₂ avoided (tonnes/year)", _n(solar["co2_avoided_tonnes_per_yr"])),
                ])]
                monthly = ins.get("monthly") or []
                if monthly:
                    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                    el += [_table([["Month"] + months[:len(monthly)],
                                   ["kWh/m²"] + [str(m.get("kwh_per_sqm_month"))
                                                 for m in monthly]],
                                  col_widths=[22 * mm] + [12 * mm] * len(monthly))]
                el += [Paragraph(f'Priced against a ₹{cfg.get("tariff_per_kwh")}/kWh tariff '
                                 f'over a {cfg.get("life_years")}-year life at '
                                 f'{cfg.get("degradation_pct_yr")}% annual degradation. No '
                                 'subsidy or export tariff is assumed; both vary by state '
                                 'and would shorten the payback.', ss["Sub"])]

    # ---------------------------------------------------------------- apartment planning
    # Everything the Apartment Planning page decides: the mix that produces the unit count
    # every other report starts from, the floor plates generated from it, and what the
    # Vastu manual says about them. None of it had a document before.
    if report_type == "planning":
        towers = project.get("towers") or []
        if not towers:
            el += [Paragraph("Apartment Planning", ss["Sec"]),
                   Paragraph("No tower is defined, so there is no unit mix to report.",
                             ss["Sub"])]
        else:
            rows, mix_rows = [], {}
            for t, der in ((t, next((d for d in areas["towers"]
                                     if d.get("id") == t.get("id")), {})) for t in towers):
                floors = int(t.get("floors") or 0)
                for u in (t.get("units") or []):
                    per_floor = int(u.get("count") or 0)
                    carpet = float(u.get("carpet_area") or 0)
                    balcony = float(u.get("balcony_area") or 0)
                    rows.append([t.get("name"), str(u.get("type", "")).upper(), per_floor,
                                 per_floor * floors, _n(carpet), _n(balcony),
                                 _n(carpet * per_floor * floors)])
                    key = str(u.get("type", "?")).upper()
                    m = mix_rows.setdefault(key, {"units": 0, "carpet": 0.0})
                    m["units"] += per_floor * floors
                    m["carpet"] += carpet * per_floor * floors
            el += [Paragraph("Unit Mix", ss["Sec"]),
                   _table([["Tower", "Type", "Per floor", "Total flats", "Carpet (m²)",
                            "Balcony (m²)", "Carpet total (m²)"]] + rows,
                          col_widths=[26 * mm, 18 * mm, 20 * mm, 22 * mm, 24 * mm,
                                      24 * mm, 30 * mm])]
            total_units = sum(m["units"] for m in mix_rows.values()) or 1
            el += [Paragraph("Mix Across the Scheme", ss["Sec"]),
                   _table([["Type", "Flats", "Share of flats", "Carpet (m²)",
                            "Average carpet (m²)"]] +
                          [[k, v["units"], f'{v["units"] / total_units * 100:.1f}%',
                            _n(v["carpet"]), _n(v["carpet"] / v["units"]) if v["units"] else "-"]
                           for k, v in sorted(mix_rows.items())],
                          col_widths=[28 * mm, 24 * mm, 30 * mm, 34 * mm, 40 * mm])]

            el += [Paragraph("Per Tower — Building Programme", ss["Sec"]),
                   _table([["Tower", "Floors", "Height (m)", "Flats/floor", "Flats",
                            "Plate (m²)", "Service core (m²)", "Lifts", "Stairs"]] +
                          [[t["name"], t["floors"], _n(t["height_m"]), t["units_per_floor"],
                            t["total_units"], _n(t["builtup_per_floor_sqm"]),
                            _n(t["service_core_per_floor_sqm"]), t["lift_count"],
                            t["stair_count"]] for t in areas["towers"]],
                          col_widths=[24 * mm, 16 * mm, 20 * mm, 20 * mm, 16 * mm,
                                      20 * mm, 26 * mm, 14 * mm, 14 * mm]),
                   Paragraph("Flats per floor and the service core per floor are the two "
                             "figures the built-up area is assembled from; every area, "
                             "quantity and cost downstream is a multiple of them.", ss["Sub"])]

            amen = project.get("society_amenities") or []
            if amen:
                el += [Paragraph("Society Amenities", ss["Sec"]),
                       _table([["Amenity", "Type", "Area (m²)"]] +
                              [[x.get("name"), x.get("type", "-"), _n(x.get("area"))]
                               for x in amen] +
                              [["Total", "", _n(areas["society_amenities_sqm"])]],
                              col_widths=[80 * mm, 45 * mm, 40 * mm]),
                       Paragraph("These are entered once for the society, not per tower. "
                                 "They are added to super built-up (saleable) area and are "
                                 "never counted in built-up, so they do not enter FAR.",
                                 ss["Sub"])]

            audits = _vastu_audits(project, derived)
            if not audits:
                el += [Paragraph("Vastu Audit", ss["Sec"]),
                       Paragraph("No floor plate has been generated, so there is no layout "
                                 "to audit. Open Apartment Planning and generate a floor "
                                 "layout for at least one tower.", ss["Sub"])]
            else:
                el += [Paragraph("Vastu Audit — Generative Architecture & Vastu Logic Manual",
                                 ss["Sec"]),
                       Paragraph("Hard rules are geometry that is wrong if broken and are "
                                 "reported as violations. The fixed kitchen is checked in SE; "
                                 "sector targets include pooja NE and master SW. A flat with one "
                                 "facade cannot always give every anchor its sector, so an "
                                 "unmet anchor is stated rather than relabelled compliant.",
                                 ss["Sub"])]
                frows = []
                for t, per in audits:
                    for fl in sorted(per):
                        au = per[fl]
                        frows.append([t.get("name"), str(fl), f'{au.get("score")}%',
                                      str(len(au.get("violations") or [])),
                                      str(len(au.get("unit_audits") or {})),
                                      au.get("status", "-")])
                el += [_table([["Tower", "Floor", "Score", "Hard breaches", "Flats",
                                "Status"]] + frows,
                              col_widths=[26 * mm, 16 * mm, 20 * mm, 26 * mm, 16 * mm,
                                          62 * mm], align_right_from=2)]

                # The anchors, unit scores and violations of one representative floor. The
                # whole set would run to hundreds of rows on a tall tower and say the same
                # thing on every typical floor.
                t0, per0 = audits[0]
                fl0 = sorted(per0)[0]
                au0 = per0[fl0]
                el += [Paragraph(f'Sector Anchors — {t0.get("name")}, floor {fl0}', ss["Sec"]),
                       _kv([(k.replace("_", " ").title(), v)
                            for k, v in (au0.get("anchors") or {}).items()])]
                ua = au0.get("unit_audits") or {}
                if ua:
                    el += [Paragraph(f'Per Flat — {t0.get("name")}, floor {fl0}', ss["Sec"]),
                           _table([["Flat", "Type", "Entry", "Facing", "Score",
                                    "Plan covered", "Breaches"]] +
                                  [[uid, str(v.get("unit_type") or "-").upper(),
                                    v.get("entry_edge", "-"), v.get("facing", "-"),
                                    f'{v.get("score")}%', f'{v.get("coverage_pct")}%',
                                    str(len(v.get("violations") or []))]
                                   for uid, v in ua.items()],
                                  col_widths=[30 * mm, 18 * mm, 16 * mm, 30 * mm, 18 * mm,
                                              26 * mm, 22 * mm]),
                           Paragraph("Plan covered is the share of the flat's box that "
                                     "belongs to a named room. Anything short of 100% is "
                                     "floor area no room claims, which the manual treats as "
                                     "unbuilt plan rather than spare space.", ss["Sub"])]
                breaches = [v for _t, per in audits for au in per.values()
                            for v in (au.get("violations") or [])]
                if breaches:
                    el += [Paragraph(f"Hard Rule Violations ({len(breaches)} across every "
                                     "generated floor)", ss["Sec"])]
                    el += [Paragraph(f"· {v}", ss["Sub"]) for v in breaches[:40]]
                    if len(breaches) > 40:
                        el += [Paragraph(f"… and {len(breaches) - 40} more of the same kinds.",
                                         ss["Sub"])]

            # Proposed Floor Plan Drawings for each tower
            el += [Paragraph("Proposed Floor Plate Drawings", ss["Sec"]),
                   Paragraph("Architectural presentation layout generated for each tower's typical floor plate.", ss["Sub"]),
                   Spacer(1, 4)]
            for t in towers:
                tname = t.get("name") or "Tower"
                try:
                    import floorplan_render
                    img_bytes = floorplan_render.render_floorplan_image(
                        t, project_name=project.get("name", ""), dpi=150, show_title_block=True
                    )
                    if img_bytes:
                        el += [
                            Paragraph(f"Floor Plate Layout — {tname}", ss["Sub"]),
                            RLImage(io.BytesIO(img_bytes), width=170 * mm, height=115 * mm),
                            Spacer(1, 8),
                        ]
                except Exception as exc:
                    el += [Paragraph(f"Floor plan layout image for {tname} could not be rendered: {exc}", ss["Sub"])]

    # ---------------------------------------------------------------- tower floor plans
    if report_type == "floorplans":
        towers = project.get("towers") or []
        if not towers:
            el += [Paragraph("Proposed Tower Floor Plans", ss["Sec"]),
                   Paragraph("No towers defined in this project.", ss["Sub"])]
        else:
            el += [Paragraph("Proposed Tower Floor Plans & Layout Sheets", ss["Sec"]),
                   Paragraph("Architectural 2D drawings generated from the proposed room and unit configurations for each building.", ss["Sub"]),
                   Spacer(1, 4)]
            for idx, t in enumerate(towers):
                tname = t.get("name") or f"Tower {idx + 1}"
                floors = int(t.get("floors") or 1)
                units = t.get("units") or []
                footprint = float(t.get("footprint_area") or 0)
                fl_keys = list((t.get("floor_layouts") or {}).keys())
                primary_fl = fl_keys[0] if fl_keys else "1"

                el += [
                    Paragraph(f"Building Sheet: {tname}", ss["Sec"]),
                    _kv([
                        ("Tower Name", tname),
                        ("Storeys (Floors)", str(floors)),
                        ("Plate Footprint", f"{_n(footprint)} m²"),
                        ("Units per Floor", str(sum(int(u.get('count', 0)) for u in units))),
                        ("Corridor Width", f"{_n(t.get('corridor_width', 1.8))} m"),
                        ("Typical Level Drawn", f"Floor {primary_fl}"),
                    ]),
                    Spacer(1, 6),
                ]
                try:
                    import floorplan_render
                    img_bytes = floorplan_render.render_floorplan_image(
                        t, project_name=project.get("name", ""), dpi=180, show_title_block=True
                    )
                    if img_bytes:
                        el += [
                            RLImage(io.BytesIO(img_bytes), width=172 * mm, height=118 * mm),
                            Spacer(1, 10),
                        ]
                except Exception as exc:
                    el += [Paragraph(f"Drawing for {tname} could not be rendered: {exc}", ss["Sub"])]

    # ---------------------------------------------------------------- site layout
    # What the Plot Management layout engine and the 3D view are both drawing: the
    # buildable envelope, what was reserved out of it, and what was packed into the rest.
    if report_type == "layout":
        sl = project.get("site_layout") or {}
        if not sl:
            el += [Paragraph("Site Layout", ss["Sec"]),
                   Paragraph("No site layout has been generated for this project. Open Plot "
                             "Management and run the three stages — envelope, reserve roads "
                             "and amenities, generate layout — to populate this report.",
                             ss["Sub"])]
        else:
            m = sl.get("layout_metrics") or {}
            roads = sl.get("roads") or {}
            blocks = sl.get("blocks") or {}
            green = sl.get("green") or {}
            residual = sl.get("residual") or {}
            sp = sl.get("surface_parking") or {}
            el += [Paragraph("Land Budget", ss["Sec"]),
                   _table([["Land use", "Area (m²)", "Share of plot"]] +
                          [[k, _n(v),
                            f'{float(v or 0) / float(areas["plot_area_sqm"] or 1) * 100:.1f}%']
                           for k, v in [
                               ("Tower footprint", m.get("total_footprint_sqm")),
                               ("Internal roads (ring + driveways)", roads.get("total_area_sqm")),
                               ("Amenity blocks", sum(float(x.get("area_sqm") or 0)
                                                      for x in (sl.get("amenities") or []))),
                               ("Landscaped green", green.get("area_sqm")),
                               ("Surface parking", sp.get("area_sqm")),
                               ("Unallocated residual", residual.get("area_sqm")),
                           ]] +
                          [["Plot", _n(areas["plot_area_sqm"]), "100.0%"]],
                          col_widths=[85 * mm, 40 * mm, 40 * mm]),
                   Paragraph("Shares are of the whole plot and will not sum to 100%: the "
                             "setback strip outside the buildable envelope is in none of "
                             "these categories, and the residual is what remains inside it "
                             "after everything above was placed.", ss["Sub"])]

            el += [Paragraph("Layout Performance", ss["Sec"]), _kv([
                ("Packing method", sl.get("method", "-")),
                ("Towers placed", m.get("tower_count")),
                ("Buildable floor area (m²)", _n(m.get("total_buildable_area_sqm"))),
                ("Achieved FAR", m.get("achieved_far")),
                ("FAR cap targeted", m.get("far_cap")),
                ("Ground coverage (%)", _n(m.get("ground_coverage_pct"))),
                ("Open space (%)", _n(m.get("open_space_pct"))),
                ("Units generated", m.get("unit_count")),
                ("Surface parking bays", m.get("surface_bays")),
                ("Feasible against hard constraints",
                 "yes" if m.get("feasible") else "no"),
                ("Fitness score", m.get("score") if m.get("score") is not None
                 else "not scored — the layout is infeasible"),
            ])]
            pen = m.get("penalties") or {}
            if pen:
                el += [Paragraph("Fitness Penalties", ss["Sec"]),
                       _table([["Penalty", "Value"]] +
                              [[k.replace("_", " ").title(), _n(v)] for k, v in pen.items()],
                              col_widths=[100 * mm, 65 * mm]),
                       Paragraph("Penalties are what the packing search traded off to reach "
                                 "this arrangement. A large one names the constraint that "
                                 "shaped the layout most.", ss["Sub"])]

            twrs = sl.get("towers") or []
            if twrs:
                el += [Paragraph("Placed Blocks", ss["Sec"]),
                       _table([["Block", "Width (m)", "Depth (m)", "Rotation (°)", "Floors",
                                "Height (m)", "Footprint (m²)", "Floor area (m²)", "Units"]] +
                              [[t.get("name"), _n(t.get("width_m")), _n(t.get("depth_m")),
                                _n(t.get("rotation_deg")), t.get("floors"),
                                _n(t.get("height_m")), _n(t.get("footprint_sqm")),
                                _n(t.get("floor_area_sqm")), t.get("units")]
                               for t in twrs],
                              col_widths=[22 * mm, 18 * mm, 18 * mm, 20 * mm, 14 * mm,
                                          18 * mm, 24 * mm, 24 * mm, 14 * mm])]

            amenities = sl.get("amenities") or []
            if amenities:
                el += [Paragraph("Reserved Amenities", ss["Sec"]),
                       _table([["Amenity", "Requested (m²)", "Reserved (m²)", "W x D (m)",
                                "Floors", "Gross floor area (m²)"]] +
                              [[x.get("name"), _n(x.get("requested_area_sqm")),
                                _n(x.get("area_sqm")),
                                f'{_n(x.get("width_m"))} x {_n(x.get("depth_m"))}',
                                x.get("floors"), _n(x.get("gross_floor_area_sqm"))]
                               for x in amenities],
                              col_widths=[42 * mm, 30 * mm, 28 * mm, 30 * mm, 16 * mm,
                                          34 * mm]),
                       Paragraph("Reserved area can fall short of the requested area where "
                                 "the land left after setbacks and roads cannot hold the "
                                 "block at its configured proportions.", ss["Sub"])]

            el += [Paragraph("Circulation", ss["Sec"]), _kv([
                ("Ring road width (m)", _n(roads.get("ring_width_m"))),
                ("Ring road area (m²)", _n(roads.get("ring_area_sqm"))),
                ("Driveway width (m)", _n(roads.get("driveway_width_m"))),
                ("Driveway area (m²)", _n(roads.get("driveway_area_sqm"))),
                ("Corridors set out", len(roads.get("corridors") or [])),
                ("Road grid angle (°)", _n(roads.get("grid_angle_deg"))),
                ("Developable blocks between roads", blocks.get("count")),
                ("Block area (m²)", _n(blocks.get("area_sqm"))),
            ])]
            for w in (sl.get("warnings") or [])[:12]:
                el += [Paragraph(f"<b>Layout warning:</b> {w}", ss["Sub"])]
            if sl.get("polygon_signature") and (project.get("plot") or {}).get("coordinates"):
                el += [Paragraph(f'Generated by layout engine {sl.get("engine_version", "-")} '
                                 'against the plot boundary as it stood at the time. The '
                                 'Data Reliability report says whether that boundary has '
                                 'changed since.', ss["Sub"])]

    # ---------------------------------------------------------------- calculations
    # The Calculations page, as a document: how carpet becomes built-up becomes saleable,
    # and how FAR and FSI fall out of it. Reproducible by hand from the rows shown.
    if report_type == "calculations":
        far = a["far_derivation"]
        der = a.get("area_derivation") or {}
        el += [Paragraph("Area Derivation — Step by Step", ss["Sec"]),
               _table([["#", "Step", "Formula", "Result"]] +
                      [[str(s["step"]), s["title"], s["formula"], s["result"]]
                       for s in (der.get("step_by_step_formulas") or [])],
                      col_widths=[8 * mm, 40 * mm, 72 * mm, 45 * mm], align_right_from=4)]
        for s in (der.get("step_by_step_formulas") or []):
            el += [Paragraph(f'<b>{s["step"]}. {s["title"]}:</b> {s["explanation"]}', ss["Sub"])]

        el += [Paragraph("FAR Derivation", ss["Sec"]),
               _kv([(i["label"], f'{_n(i["value"])} {i["unit"]}')
                    for i in far["inputs"]]
                   + [("Formula", far["formula"]),
                      ("Substitution", far["substitution"]),
                      ("FAR", far["far"]),
                      ("FSI factor (config)", f'{far["fsi_factor"]:g}'),
                      ("FSI", far["fsi"])]),
               Paragraph(far["builtup_rule"], ss["Sub"]),
               Paragraph(far["far_vs_fsi"], ss["Sub"])]

        if far["towers"]:
            el += [Paragraph("Per Tower — Contribution to Built-up", ss["Sec"]),
                   _table([["Tower", "Floors", "Carpet (m²)", "Balcony (m²)",
                            "Service core/floor (m²)", "Built-up/floor (m²)",
                            "Built-up (m²)", "Share"]] +
                          [[t["name"], t["floors"], _n(t["carpet_sqm"]), _n(t["balcony_sqm"]),
                            _n(t["service_core_per_floor_sqm"]),
                            _n(t["builtup_per_floor_sqm"]), _n(t["builtup_sqm"]),
                            f'{t["share_pct"]}%'] for t in far["towers"]] +
                          [["Project total", "", _n(areas["carpet_area_sqm"]), "", "",
                            "", _n(areas["builtup_area_sqm"]), "100.0%"]],
                          col_widths=[22 * mm, 14 * mm, 22 * mm, 22 * mm, 28 * mm,
                                      26 * mm, 24 * mm, 16 * mm])]

        el += [Paragraph("Not Counted in FAR", ss["Sec"]),
               _table([["Item", "Area (m²)", "Why it never enters the numerator"]] +
                      [[x["item"], _n(x["area_sqm"]), x["reason"]] for x in far["excluded"]],
                      col_widths=[36 * mm, 24 * mm, 105 * mm], align_right_from=3),
               Paragraph(far["no_deductions_note"], ss["Sub"])]

        perm = far.get("permissible") or {}
        el += [Paragraph("Against the Permissible Limit", ss["Sec"]), _kv([
            ("Governing control", perm.get("governing_control", "not set")),
            ("Permissible FAR", perm.get("far_cap") or "not set"),
            ("FAR used (%)", _n(perm.get("used_pct")) if perm.get("used_pct") is not None else "-"),
            ("Headroom (ratio)", perm.get("headroom_ratio")
             if perm.get("headroom_ratio") is not None else "-"),
            ("Headroom (m² of further built-up)", _n(perm.get("headroom_sqm"))
             if perm.get("headroom_sqm") is not None else "-"),
        ]), Paragraph("The permissible FAR is the rule configured in this project, not a "
                      "statutory lookup. Development control regulations vary by zone and "
                      "are revised periodically — confirm the cap with the sanctioning "
                      "authority before designing to the headroom shown.", ss["Sub"])]

        towers_der = der.get("towers") or []
        if towers_der:
            el += [Paragraph("Per Tower — Service Core Build-up", ss["Sec"]),
                   _table([["Tower", "Corridor (m²)", "Stairs (m²)", "Lifts (m²)",
                            "Core/floor (m²)", "Core total (m²)"]] +
                          [[t.get("name"),
                            _n((t.get("service_core") or {}).get("corridor_sqm")),
                            _n((t.get("service_core") or {}).get("stairs_sqm")),
                            _n((t.get("service_core") or {}).get("lifts_sqm")),
                            _n((t.get("service_core") or {}).get("per_floor_sqm")),
                            _n((t.get("service_core") or {}).get("total_tower_sqm"))]
                           for t in towers_der],
                          col_widths=[30 * mm, 26 * mm, 24 * mm, 24 * mm, 30 * mm, 30 * mm])]

    # ---------------------------------------------------------------- data reliability
    # Whether the numbers in every other report rest on inputs that were actually entered.
    # A low score here is a statement about the project document, never about the
    # engineering, and the report says so in as many words.
    if report_type == "datahealth":
        dh = _datahealth(project, derived)
        if not dh:
            el += [Paragraph("Data Reliability", ss["Sec"]),
                   Paragraph("The reliability report could not be generated for this "
                             "project.", ss["Sub"])]
        else:
            s = dh["scores"]
            el += [Paragraph("Scores", ss["Sec"]), _kv([
                ("Overall reliability (0-100)", dh["overall_score"]),
                ("Completeness — was the input supplied at all?", f'{s["completeness"]}%'),
                ("Freshness — was the artefact rebuilt from the inputs as they are now?",
                 f'{s["freshness"]}%'),
                ("Consistency — do two stored facts about the same thing agree?",
                 f'{s["consistency"]}%'),
                ("Compliance score carried through", f'{s["compliance"]}%'),
            ]), Paragraph("Overall is completeness x 0.5 + freshness x 0.3 + consistency x "
                          "0.2. An input never entered is a hole; a stale artefact is an "
                          "actively wrong answer, which is why the two together carry as "
                          "much weight as completeness.", ss["Sub"])]

            el += [Paragraph("Input Completeness by Group", ss["Sec"]),
                   _table([["Input group", "Score", "Weight", "Detail"]] +
                          [[g.get("label"), f'{_n(g.get("score"))}%', _n(g.get("weight")),
                            g.get("detail", "")] for g in dh.get("groups", [])],
                          col_widths=[34 * mm, 20 * mm, 18 * mm, 93 * mm], align_right_from=4)]

            fresh = dh.get("freshness") or []
            if fresh:
                el += [Paragraph("Freshness of Derived Artefacts", ss["Sec"]),
                       _table([["Artefact", "State", "Detail"]] +
                              [[f.get("label"), str(f.get("state", "")).upper(),
                                f.get("detail", "")] for f in fresh],
                              col_widths=[38 * mm, 22 * mm, 105 * mm], align_right_from=3),
                       Paragraph("A stale artefact was generated from an earlier version of "
                                 "the inputs. It is still drawn and still costed, so it is "
                                 "worse than a missing one: it looks current.", ss["Sub"])]

            cons = dh.get("consistency") or []
            if cons:
                el += [Paragraph("Consistency Between Stored Facts", ss["Sec"]),
                       _table([["Check", "State", "Detail"]] +
                              [[c.get("label"), str(c.get("state", "")).upper(),
                                c.get("detail", "")] for c in cons],
                              col_widths=[38 * mm, 22 * mm, 105 * mm], align_right_from=3)]

            margins = dh.get("compliance_margins") or []
            if margins:
                el += [Paragraph("Compliance Margins", ss["Sec"]),
                       _table([["Rule", "Actual", "Limit", "Margin", "State"]] +
                              [[m.get("label"), _n(m.get("actual")), _n(m.get("threshold")),
                                _n(m.get("margin")), str(m.get("state", "")).upper()]
                               for m in margins],
                              col_widths=[62 * mm, 24 * mm, 24 * mm, 26 * mm, 29 * mm],
                              align_right_from=1),
                       Paragraph("A rule that passes by a hair is not the same as one that "
                                 "passes comfortably: a small margin will be lost by any "
                                 "change in the direction it is tight.", ss["Sub"])]

            mw = dh.get("module_warnings") or []
            if mw:
                el += [Paragraph("Outstanding Module Warnings", ss["Sec"])]
                el += [Paragraph(f'<b>[{str(w.get("severity", "note")).upper()}] '
                                 f'{w.get("module", "")}:</b> {w.get("text", "")}', ss["Sub"])
                       for w in mw[:25]]
            if dh.get("missing_inputs"):
                el += [Paragraph("Missing Engineering Inputs", ss["Sec"]),
                       Paragraph(", ".join(dh["missing_inputs"]), ss["Sub"])]

    # ---------------------------------------------------------------- sustainability
    if eng and report_type == "sustainability":
        mods = eng.get("modules") or {}
        green, carbon, trees = mods.get("green"), mods.get("carbon"), mods.get("trees")
        if green:
            el += [Paragraph("Green Rating", ss["Sec"]),
                   _kv([(o["label"], o["value"]) for o in green["outputs"]])]
        if carbon:
            el += [Paragraph("Embodied Carbon", ss["Sec"]),
                   _kv([(o["label"], f"{o['value']} {o['unit']}".strip())
                        for o in carbon["outputs"]]),
                   _table([["Material", "Quantity", "Unit", "Factor", "tCO2e", "Share"]] +
                          [[m["label"], _n(m["quantity"]), m["unit"], _n(m["factor"]),
                            _n(m["tco2e"]), f"{m['share_pct']}%"]
                           for m in carbon["materials"]],
                          col_widths=[48 * mm, 26 * mm, 16 * mm, 22 * mm, 24 * mm, 22 * mm])]
            if carbon.get("unpriced"):
                el += [Paragraph("Not carried in the carbon figure: "
                                 + ", ".join(carbon["unpriced"])
                                 + " — no defensible coefficient.", ss["Sub"])]
        if trees:
            el += [Paragraph("Plantation Plan", ss["Sec"]),
                   _kv([(o["label"], f"{o['value']} {o['unit']}".strip())
                        for o in trees["outputs"]]),
                   _table([["Zone", "Area (m²)", "Trees", "Species"]] +
                          [[z["zone"], _n(z["area_sqm"]), _n(z["trees"]),
                            ", ".join(sp["species"].split(" (")[0] for sp in z["species"])]
                           for z in trees["zones"]],
                          col_widths=[42 * mm, 24 * mm, 18 * mm, 74 * mm], align_right_from=4)]
        solar = (project.get("gis") or {}).get("solar")
        if solar:
            cfg = solar.get("config") or {}
            el += [Paragraph("Rooftop Solar", ss["Sec"]), _kv([
                ("Terrace area (m²)", _n(solar["roof_area_sqm"])),
                ("Usable after plant and access (m²)", _n(solar["usable_area_sqm"])),
                ("Installable (kWp)", _n(solar["installable_kwp"])),
                ("Annual generation (kWh)", _n(solar["annual_yield_kwh"])),
                ("Lifetime generation (kWh)", _n(solar["lifetime_kwh"])),
                ("CO₂ avoided (t/yr)", _n(solar["co2_avoided_tonnes_per_yr"])),
                ("CO₂ avoided over life (t)",
                 _n(float(solar["co2_avoided_tonnes_per_yr"] or 0) * float(cfg.get("life_years") or 0))),
                ("Annual saving", _n(solar["annual_saving_inr"])),
                ("Payback (years)", _n(solar["payback_years"])
                 if solar.get("payback_years") else "not reached in the system life"),
            ])]
            if carbon:
                total = float(carbon["derived"]["total_tco2e"] or 0)
                per_yr = float(solar["co2_avoided_tonnes_per_yr"] or 0)
                el += [Paragraph(
                    f"The rooftop array avoids {_n(per_yr)} tCO₂e a year against "
                    f"{_n(total)} tCO₂e already embodied in the structure — an operational "
                    f"offset of the build's carbon in roughly "
                    f"{total / per_yr:,.0f} years." if per_yr else
                    "The array generates nothing at the configured roof area, so it offsets "
                    "none of the embodied carbon.", ss["Sub"])]
        else:
            el += [Paragraph("Rooftop Solar", ss["Sec"]),
                   Paragraph("No GIS site analysis has been run, so the rooftop solar "
                             "potential is unknown. Run GIS Intelligence to size it from "
                             "this location's insolation and the towers' terrace area.",
                             ss["Sub"])]

    el += [Spacer(1, 10), Paragraph("Generated by Aptimizer — figures are estimates based on configurable "
                                    "thumb-rule ratios and project rule sets.", ss["Sub"])]
    doc.build(el)
    return buf.getvalue()


def build_all_pdf(project: dict, a: dict, eng: dict = None) -> bytes:
    """Every report in one document, behind a contents page.

    Built by concatenating the individual PDFs with pypdf rather than by assembling one
    giant story: each report already knows how to lay itself out, and rebuilding that
    inline would leave two definitions of every section to keep in step.
    """
    from pypdf import PdfWriter, PdfReader

    writer = PdfWriter()

    # Contents page, generated the same way the reports are so it matches them.
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    ss = _styles()
    el = [
        Paragraph("APTIMIZER", ParagraphStyle("brand", fontSize=10, textColor=BRAND, spaceAfter=2)),
        Paragraph("Complete Project Report Set", ss["H"]),
        Paragraph(f"Project: {project.get('name', '')} &nbsp;|&nbsp; Client: {project.get('client', '-')} "
                  f"&nbsp;|&nbsp; Location: {project.get('location', '-')} &nbsp;|&nbsp; "
                  f"Generated: {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}", ss["Sub"]),
        Spacer(1, 10),
        Paragraph("Contents", ss["Sec"]),
    ]

    built = []
    for key in ALL_ORDER:
        try:
            built.append((key, build_pdf(key, project, a, eng)))
        except Exception:
            # One report failing must not cost the reader the other ten.
            continue

    page = 2                       # the contents page itself is page 1
    rows = [["#", "Report", "Page"]]
    for i, (key, pdf) in enumerate(built, 1):
        rows.append([str(i), REPORT_TITLES[key], str(page)])
        page += len(PdfReader(io.BytesIO(pdf)).pages)
    el += [_table(rows, col_widths=[14 * mm, 130 * mm, 20 * mm], align_right_from=3)]
    el += [Spacer(1, 10),
           Paragraph("Generated by Aptimizer — figures are estimates based on configurable "
                     "thumb-rule ratios and project rule sets.", ss["Sub"])]
    doc.build(el)

    writer.append(PdfReader(io.BytesIO(buf.getvalue())))
    for _key, pdf in built:
        writer.append(PdfReader(io.BytesIO(pdf)))

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def build_boq_excel(project: dict, a: dict) -> bytes:
    wb = Workbook()
    boq, cur = a["boq"], a["cost"]["currency"]
    head = Font(bold=True, color="FFFFFF")

    def sheet(ws, title, headers, rows, total_label, total):
        ws.title = title
        ws.append(headers)
        for c in ws[1]:
            c.font = head
            c.alignment = Alignment(horizontal="center")
        for r in rows:
            ws.append(r)
        ws.append([total_label, "", "", "", total])
        ws[f"A{ws.max_row}"].font = Font(bold=True)
        ws[f"E{ws.max_row}"].font = Font(bold=True)
        for col, w in zip("ABCDE", [38, 12, 16, 16, 18]):
            ws.column_dimensions[col].width = w

    ws1 = wb.active
    sheet(ws1, "Materials", ["Item", "Unit", "Quantity", f"Rate ({cur})", f"Amount ({cur})"],
          [[m["label"], m["unit"], m["quantity"], m["rate"], m["amount"]] for m in boq["materials"]],
          "Material Total", boq["material_total"])
    sheet(wb.create_sheet(), "Labour", ["Trade", "Unit", "Man-days", f"Wage ({cur})", f"Amount ({cur})"],
          [[l["label"], l["unit"], l["quantity"], l["rate"], l["amount"]] for l in boq["labour"]],
          "Labour Total", boq["labour_total"])
    sheet(wb.create_sheet(), "Equipment", ["Equipment", "Unit", "Days", f"Rate ({cur})", f"Amount ({cur})"],
          [[e["label"], e["unit"], e["quantity"], e["rate"], e["amount"]] for e in boq["equipment"]],
          "Equipment Total", boq["equipment_total"])

    ws4 = wb.create_sheet("Summary")
    ws4.append(["Project", project.get("name", "")])
    for k, v in [("Plot Area (m2)", a["areas"]["plot_area_sqm"]),
                 ("Built-up Area (m2)", a["areas"]["builtup_area_sqm"]),
                 ("Total Units", a["areas"]["total_units"]),
                 ("FAR", a["areas"]["far"]), ("FSI", a["areas"]["fsi"]),
                 (f"Material ({cur})", boq["material_total"]), (f"Labour ({cur})", boq["labour_total"]),
                 (f"Equipment ({cur})", boq["equipment_total"]),
                 (f"Grand Total ({cur})", boq["grand_total"]),
                 (f"Cost per Flat ({cur})", boq["cost_per_unit"]),
                 (f"Cost per m2 ({cur})", boq["cost_per_sqm"])]:
        ws4.append([k, v])
    ws4.column_dimensions["A"].width = 30
    ws4.column_dimensions["B"].width = 22

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()



# ---------------------------------------------------------------- Stage 1 (Site) reports
def _report_doc():
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    return buf, doc


def _report_head(title: str, project: dict, ss) -> list:
    return [
        Paragraph("APTIMIZER", ParagraphStyle("brand", fontSize=10, textColor=BRAND, spaceAfter=2)),
        Paragraph(title, ss["H"]),
        Paragraph(f"Project: {project.get('name', '')} &nbsp;|&nbsp; Location: {project.get('location') or '-'} "
                  f"&nbsp;|&nbsp; Generated: {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}", ss["Sub"]),
        Spacer(1, 8),
    ]


def build_township_pdf(project: dict, plan: dict) -> bytes:
    """Township & Master Plan report, from the same plan the Township module draws."""
    buf, doc = _report_doc()
    ss = _styles()
    el = _report_head(REPORT_TITLES["township"], project, ss)
    el[-1] = Spacer(1, 4)
    cell = ParagraphStyle("cell", parent=ss["Normal"], fontSize=8, leading=10)
    m = plan.get("master_plan_metrics") or {}
    area = float(plan.get("township_area_sqm") or 0)
    mixed = plan.get("is_mixed_use", True)
    el += [Paragraph("Report Summary", ss["Sec"]),
           Paragraph(f"A {'mixed-use' if mixed else 'residential'} master plan over {_n(area)} m2 "
                     f"({area / 4046.86:,.2f} acres): {m.get('estimated_dwelling_units', '-')} dwellings at a blended "
                     f"FAR of {m.get('blended_far', '-')}, with {m.get('open_space_ratio_pct', '-')}% of the land kept "
                     f"as open space. Land-use shares follow URDPFI-style planning norms and are indicative; "
                     f"align the plan with the statutory development plan before detailed design.", ss["Normal"]),
           Paragraph("Master Plan Metrics", ss["Sec"]),
           _kv([
               ("Township area (m²)", _n(area)),
               ("Township area (acres)", f"{area / 4046.86:,.2f}"),
               ("Development model", "Mixed-use (retail podium + commercial galleria)" if mixed else "Residential"),
               ("Potential built-up area (m²)", _n(m.get("total_potential_builtup_sqm"))),
               ("Blended FAR", m.get("blended_far", "-")),
               ("Estimated dwelling units", m.get("estimated_dwelling_units", "-")),
               ("Estimated population", f"{m['estimated_population']:,}" if m.get("estimated_population") else "-"),
               ("Commercial leasable area (m²)", _n(m.get("commercial_leasable_sqm"))),
               ("Open space ratio (%)", m.get("open_space_ratio_pct", "-")),
           ])]
    zones = plan.get("zoning_distribution") or []
    if zones:
        el += [Paragraph("Land Use & Zoning Distribution", ss["Sec"]),
               _table([["Zone", "Share (%)", "Area (m²)", "Permissible FSI"]] +
                      [[z.get("zone", ""), z.get("share_pct", ""), _n(z.get("area_sqm")), z.get("permissible_fsi", "")]
                       for z in zones],
                      col_widths=[80 * mm, 25 * mm, 32 * mm, 30 * mm])]
    sectors = plan.get("sectors") or []
    if sectors:
        el += [Paragraph("Sector Breakdown", ss["Sec"]),
               _table([["Sector", "Area (m²)", "Dwellings", "Towers", "GLA (m²)", "Amenities"]] +
                      [[Paragraph(sec.get("name", ""), cell), _n(sec.get("area_sqm")), sec.get("units") or "-",
                        sec.get("towers") or "-", _n(sec["gla_sqm"]) if sec.get("gla_sqm") else "-",
                        Paragraph(", ".join(sec.get("amenities") or []) or "-", cell)]
                       for sec in sectors],
                      col_widths=[44 * mm, 22 * mm, 18 * mm, 14 * mm, 24 * mm, 45 * mm])]
    circ = plan.get("circulation_strategy") or {}
    if circ:
        el += [Paragraph("Circulation & Mobility Strategy", ss["Sec"]),
               _table([["Element", "Strategy"]] + [
                   ["Pedestrian segregation", Paragraph(str(circ.get("segregation", "-")), cell)],
                   ["Gate hierarchy", Paragraph(str(circ.get("access_points", "-")), cell)],
                   ["Internal right of way", Paragraph(f"{circ.get('internal_road_width_m', '-')} m clear roadway "
                                                       "with dedicated cycle track and tree verge", cell)],
               ], col_widths=[50 * mm, 117 * mm], align_right_from=2)]
    doc.build(el)
    return buf.getvalue()


def build_site_stage_pdf(project: dict, a: dict, eng: dict, township_plan: dict) -> bytes:
    """Every Stage 1 (Site) report in one document, behind a contents page."""
    from pypdf import PdfWriter, PdfReader

    built = []
    for key in ("plot", "layout", "site"):
        try:
            built.append((key, build_pdf(key, project, a, eng)))
        except Exception:
            continue               # one report failing must not cost the reader the others
    if township_plan:
        try:
            built.append(("township", build_township_pdf(project, township_plan)))
        except Exception:
            pass

    buf, doc = _report_doc()
    ss = _styles()
    el = _report_head("Site Stage Report Set", project, ss)
    el += [Paragraph("Contents", ss["Sec"])]
    page, rows = 2, [["#", "Report", "Page"]]
    for i, (key, pdf) in enumerate(built, 1):
        rows.append([str(i), REPORT_TITLES[key], str(page)])
        page += len(PdfReader(io.BytesIO(pdf)).pages)
    el += [_table(rows, col_widths=[14 * mm, 130 * mm, 20 * mm], align_right_from=2),
           Spacer(1, 10),
           Paragraph("Stage 1 (Site) — preliminary site planning outputs. Not a cadastral survey, geotechnical "
                     "report or statutory approval; qualified review is required before design proceeds.", ss["Sub"])]
    doc.build(el)

    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(buf.getvalue())))
    for _key, pdf in built:
        writer.append(PdfReader(io.BytesIO(pdf)))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
