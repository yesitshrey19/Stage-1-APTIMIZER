"""IS-code and NBC driven engineering modules (Aptimizer V2).

Every module is self-contained: it reads the shared project data (plus the V1 analysis
results), returns its own outputs with inline IS/NBC clause references, and reports any
missing inputs instead of failing silently.
"""
import math

import engine as enginelib
import takeoff as takeofflib
import iscodes as C
import layout as layoutlib


def out(label, value, unit="", clause_key=None, note=""):
    return {"label": label, "value": value, "unit": unit,
            "clause": C.clause(clause_key) if clause_key else None, "note": note}


def check(label, ok, actual, required, clause_key, note=""):
    return {"label": label, "status": "pass" if ok else "fail", "actual": actual,
            "required": required, "clause": C.clause(clause_key), "note": note}


DEFAULT_ENGINEERING = {
    # Which building code a project is checked against. Defaults to NBC 2016 because that
    # is what state bye-laws still reference, which is what an approval is measured by --
    # SP 7:2026 withdrew it nationally but is voluntary until a state adopts it.
    "code_version": C.DEFAULT_CODE_VERSION,
    "city": "Bengaluru",
    "state": "Karnataka",
    "soil_type": "dense sand",
    "exposure_condition": "moderate",
    "structural_system": "SMRF",
    "importance": "residential",
    "occupancy_per_unit": 4,
    "roof_area_sqm": 0,
    "slab_thickness_mm": 125,
    "wall_thickness_mm": 230,
    "wall_material": "brick_masonry",
    "finishes_load_kn_sqm": 1.5,
    "grid_bay_x_m": 5.0,
    "grid_bay_y_m": 5.0,
    "beam_span_m": 5.0,
    "beam_support": "simply supported",
    "concrete_grade": 25,
    "steel_grade": 415,
    "column_steel_pct": 1.0,
    "unsupported_length_m": 0,      # 0 -> derived from floor height
    "frame_type": "brick_infill",   # governs the IS 1893 Cl. 7.6.2 period formula
    "wind_k1": 1.0,                 # IS 875-3 Table 1, risk / design life
    "wind_k3": 1.0,                 # IS 875-3 Cl. 6.3.3, topography
    "aggregate_size_mm": 20,
    "cement_type": "OPC 53",
    "concrete_volume_cum": 0,
    "sump_depth_m": 2.5,
    "oht_tanks": 2,
    "basement_headroom_m": 2.7,
    "aisle_width_m": 6.0,
    "two_wheeler_provided": 0,
    "refuge_floors_provided": 0,
    "extinguishers_per_floor": 0,
    "rwh_provided": True,
    "fire_lift_car_m": [1.2, 2.2],
    "stair_pressurisation": True,
    "pedestrian_ramp_slope": 12.0,
    "door_width_mm": 900,
    "lift_car_mm": [1100, 1400],
    "dual_handrails": True,
    "tactile_path": True,
    "site_area_type": "mixed_site",
    "green_checklist": {},
    "rain_intensity_override": 0,
}


def cfg(project):
    c = {**DEFAULT_ENGINEERING, **(project.get("engineering") or {})}
    soil = str(c.get("soil_type") or "").replace("_", " ").strip().lower()
    c["soil_type"] = soil if soil in C.SOILS else DEFAULT_ENGINEERING["soil_type"]
    # Normalised here rather than at each use, so a project carrying a typo or a version
    # this build does not know about is checked against the default and says so, instead
    # of raising deep inside a module or silently keying an empty table.
    c["code_version"] = C.code_version(c.get("code_version"))
    return c


def _building(project, base):
    towers = base["areas"]["towers"]
    tallest = max(towers, key=lambda t: t["height_m"]) if towers else None
    return {
        "height_m": tallest["height_m"] if tallest else 0,
        "floors": tallest["floors"] if tallest else 0,
        "floor_height": tallest["floor_height"] if tallest else 3.0,
        "footprint": base["areas"]["ground_footprint_sqm"],
        "builtup": base["areas"]["builtup_area_sqm"],
        "super_builtup": base["areas"]["super_builtup_area_sqm"],
        "units": base["areas"]["total_units"],
        "plot_area": base["areas"]["plot_area_sqm"],
        "towers": towers,
        "tallest": tallest,
    }


# ================================================================ 1. loads
def m1_structural_loads(project, base, e, city):
    b = _building(project, base)
    missing = []
    if not b["floors"]:
        missing.append("Tower floor count (Apartment Planning)")
    if not b["footprint"]:
        missing.append("Tower footprint area (Apartment Planning)")

    t = float(e["slab_thickness_mm"]) / 1000.0
    slab = C.UNIT_WEIGHTS["rcc"] * t
    finishes = float(e["finishes_load_kn_sqm"])
    wall_uw = C.UNIT_WEIGHTS.get(e["wall_material"], C.UNIT_WEIGHTS["brick_masonry"])
    wall_h = max(float(b["floor_height"]) - t, 2.4)
    wall_load_raw = C.wall_load_kn_sqm(e["wall_material"], e["wall_thickness_mm"], b["floor_height"], t)
    wall_load = round(wall_load_raw, 2)
    dead_raw = slab + finishes + wall_load_raw
    dead = round(dead_raw, 2)
    live = C.LIVE_LOADS["residential_room"]
    roof_live = C.LIVE_LOADS["roof_accessible"]
    service_raw = dead_raw + live
    service = round(service_raw, 2)
    factored_raw = 1.5 * service_raw
    factored = round(factored_raw, 2)

    trib = float(e["grid_bay_x_m"]) * float(e["grid_bay_y_m"])
    floors = max(int(b["floors"]), 1)
    column_load_raw = factored_raw * trib * floors
    column_load = round(column_load_raw, 1)

    # Grades come from the project, not a fixed M25/Fe415 — picking M40 in the mix design
    # module used to leave the column sized as if it were still M25.
    fck = float(e["concrete_grade"])
    fy = float(e["steel_grade"])
    p = max(float(e["column_steel_pct"]), 0.8) / 100.0     # IS 456 Cl. 26.5.3.1 minimum
    capacity_per_sqmm = 0.4 * fck + (0.67 * fy - 0.4 * fck) * p
    # Sizing lives in `takeoff` and is imported rather than repeated here: the quantity
    # take-off has to size the very same column, and two copies of a formula is how this
    # codebase previously ended up with two different answers for one project.
    req_area = takeofflib.column_required_area(column_load_raw, fck, fy, float(e["column_steel_pct"]))
    col_b, col_d = takeofflib.column_section(req_area)

    # IS 456 Cl. 25.1.2 — Cl. 39.3 (the axial capacity expression used above) is only
    # valid for a SHORT column carrying no more than the minimum eccentricity. Neither
    # condition was previously checked, so a slender column could be sized by a formula
    # that does not apply to it.
    floor_ht = float(b.get("floor_height") or 3.0)
    unsupported = float(e.get("unsupported_length_m") or 0.0) or max(floor_ht - 0.6, 2.0)
    least_dim = min(col_b, col_d)
    slenderness_raw = unsupported * 1000.0 / least_dim
    slenderness = round(slenderness_raw, 2)
    is_short = slenderness_raw < 12.0
    e_min = max(unsupported * 1000.0 / 500.0 + col_d / 30.0, 20.0)
    ecc_ok = e_min <= 0.05 * col_d

    span = float(e.get("beam_span_m") or 5.0)
    beam_w, beam_d = takeofflib.beam_section(span, e.get("beam_support") or "simply_supported")

    vb = city.get("wind_speed", 39.0)
    height_m = float(b.get("height_m") or (floors * floor_ht))
    k2 = next((v for h, v in C.WIND_K2 if height_m <= h), C.WIND_K2[-1][1])
    k1 = float(e.get("wind_k1") or 1.0)
    k3 = float(e.get("wind_k3") or 1.0)
    vz = vb * k1 * k2 * k3 * C.WIND_K4      # IS 875-3 Cl. 6.3
    pz_raw = 0.6 * vz ** 2 / 1000.0  # kN/m², Cl. 7.2
    # Cl. 7.2: pd = Kd Ka Kc pz, but never less than 0.70 pz
    pd_raw = max(pz_raw * C.wind_kd(city.get("city")) * C.WIND_KA * C.WIND_KC,
                 C.WIND_PD_MIN_FRACTION * pz_raw)
    pz = round(pz_raw, 3)
    pd = round(pd_raw, 3)

    # IS 875-3 Cl. 7.4: F = Cf x Ae x pd. Cf was previously omitted, which is the same as
    # taking it as 1.0 and under-states the lateral force by 20-40%.
    footprint_val = float(b.get("footprint") or 0.0)
    face_width = math.sqrt(max(footprint_val, 1)) if footprint_val > 0 else 0
    along_wind = face_width                       # square-plan assumption from footprint
    a_over_b = (along_wind / face_width) if face_width else 1.0
    h_over_b = (height_m / face_width) if face_width else 1.0
    cf = C.wind_force_coefficient(a_over_b, h_over_b)
    area_eff = face_width * height_m
    wind_force = round(cf * area_eff * pd_raw, 1)

    bay = max(float(e.get("grid_bay_x_m") or 6.0), float(e.get("grid_bay_y_m") or 6.0))
    if floors > 12 or height_m > 40:
        system = "RCC moment frame with shear walls (flat slab not advisable at this height)"
    elif bay <= 8.0 and floors <= 10:
        system = "Flat slab with perimeter beams — viable and reduces floor-to-floor height"
    else:
        system = "Conventional RCC beam–slab frame"

    warnings = []
    if not is_short:
        warnings.append({"severity": "critical",
                         "text": f"Slenderness ratio {slenderness} exceeds 12, so this is a SLENDER column. "
                                 "The IS 456 Cl. 39.3 short-column expression used to size it does not apply — "
                                 "additional moments per Cl. 39.7 are required.",
                         "clause": C.clause("column_slender")})
    if not ecc_ok:
        warnings.append({"severity": "critical",
                         "text": f"Minimum eccentricity {e_min:.1f} mm exceeds 0.05D ({0.05 * col_d:.1f} mm). "
                                 "Cl. 39.3 is not valid here; the column must be designed for combined "
                                 "axial load and moment.",
                         "clause": C.clause("column_ecc")})
    if not C.WIND_CF_VERIFIED:
        warnings.append({"severity": "warning",
                         "text": "The wind force coefficient table has not been verified against a "
                                 "controlled copy of IS 875 (Part 3) Table 26. Confirm Cf before using "
                                 "the lateral force for anything beyond feasibility.",
                         "clause": C.clause("wind_force")})

    norm_support = str(e.get("beam_support") or "simply supported").replace("_", " ").strip().lower()
    return {
        "id": "loads", "title": "Structural Load Estimator", "codes": ["IS 875 Parts 1–3", "IS 456:2000"],
        "missing": missing, "warnings": warnings,
        "outputs": [
            out("Slab self-weight", round(slab, 2), "kN/m²", "dead_load", f"25 kN/m³ × {e['slab_thickness_mm']} mm slab"),
            out("Wall load on slab", wall_load, "kN/m²", "dead_load", f"{wall_uw} kN/m³ × {e['wall_thickness_mm']} mm × {round(wall_h,2)} m"),
            out("Floor finishes", finishes, "kN/m²", "dead_load"),
            out("Total dead load", dead, "kN/m²", "dead_load"),
            out("Live load — residential floor", live, "kN/m²", "live_load"),
            out("Live load — accessible roof", roof_live, "kN/m²", "roof_live"),
            out("Service load (DL + LL)", service, "kN/m²", "live_load"),
            out("Factored load (1.5 DL + 1.5 LL)", factored, "kN/m²", "load_combo"),
            out("Tributary area per column", round(trib, 2), "m²", "column_design", f"{e['grid_bay_x_m']} × {e['grid_bay_y_m']} m bay"),
            out("Factored axial load per column", column_load, "kN", "column_design", f"over {floors} floors"),
            out("Recommended column size", f"{int(col_b)} × {int(col_d)}", "mm", "column_design",
                f"{p * 100:g}% steel, M{int(fck)}/Fe{int(fy)}"),
            out("Slenderness ratio (least dimension)", slenderness, "", "column_slender",
                f"unsupported length {unsupported:.2f} m ÷ {int(min(col_b, col_d))} mm"),
            out("Minimum eccentricity e_min", round(e_min, 1), "mm", "column_ecc",
                f"limit for Cl. 39.3 to apply is 0.05D = {round(0.05 * col_d, 1)} mm"),
            out("Preliminary beam size", f"{int(beam_w)} × {int(beam_d)}", "mm", "beam_depth", f"L/{12 if norm_support == 'simply supported' else 15} for {span} m span"),
            out("Basic wind speed Vb", vb, "m/s", "wind_speed", f"{city['city']} ({city['source']} data)"),
            out("Terrain / height factor k2", k2, "", "wind_k2", f"at {b['height_m']} m height"),
            out("Design wind pressure pz", pz, "kN/m²", "wind_pressure"),
            out("Design pressure pd (Kd·Ka·Kc)", pd, "kN/m²", "wind_pressure"),
            out("Force coefficient Cf", cf, "", "wind_force",
                f"a/b {a_over_b:.2f}, h/b {h_over_b:.2f}"
                + ("" if C.WIND_CF_VERIFIED else " — TABLE NOT YET VERIFIED against IS 875-3 Table 26")),
            out("Effective frontal area Ae", round(area_eff, 1), "m²", "wind_force",
                f"{round(face_width,1)} m wide × {b['height_m']} m tall"),
            out("Lateral wind force F = Cf·Ae·pd", wind_force, "kN", "wind_force",
                f"Cf {cf} applied — previously omitted, which under-stated this force"),
        ],
        "recommendation": {"label": "Suggested structural system", "value": system,
                           "clause": C.clause("flat_slab")},
        "per_tower": [_tower_loads(t, e, city, factored_raw, trib, dead_raw, live, pd_raw) for t in b["towers"]],
        "derived": {"dead": dead, "dead_raw": dead_raw, "live": live, "factored": factored, "factored_raw": factored_raw,
                    "column_load": column_load, "column_load_raw": column_load_raw,
                    "tributary": trib, "service": service, "service_raw": service_raw, "pd": pd, "pd_raw": pd_raw},
    }


def _column_size(load_kn, fck=25.0, fy=415.0, steel_pct=1.0):
    cb, cd = takeofflib.column_section(
        takeofflib.column_required_area(load_kn, fck, fy, steel_pct))
    return f"{int(cb)} × {int(cd)}"


def _tower_loads(t, e, city, factored_raw, trib, dead, live, pd):
    """Per-tower load / column / wind figures (module 1 outputs are the governing tower)."""
    floors = max(int(t["floors"]), 1)
    col_raw = factored_raw * trib * floors
    col = round(col_raw, 1)
    k1 = float(e.get("wind_k1") or 1.0)
    k2 = next((v for h, v in C.WIND_K2 if t["height_m"] <= h), C.WIND_K2[-1][1])
    k3 = float(e.get("wind_k3") or 1.0)
    pz_t_raw = 0.6 * (city["wind_speed"] * k1 * k2 * k3 * C.WIND_K4) ** 2 / 1000.0
    pd_t_raw = max(pz_t_raw * C.wind_kd(city.get("city")) * C.WIND_KA * C.WIND_KC,
                   C.WIND_PD_MIN_FRACTION * pz_t_raw)
    pd_t = round(pd_t_raw, 3)
    face = math.sqrt(max(t["footprint_sqm"], 1)) if t["footprint_sqm"] else 0
    cf_t = C.wind_force_coefficient(1.0, (t["height_m"] / face) if face else 1.0)
    return {
        "id": t["id"], "name": t["name"], "floors": floors, "height_m": t["height_m"],
        "footprint_sqm": t["footprint_sqm"], "builtup_sqm": t["builtup_sqm"],
        "column_load_kn": col,
        "column_size_mm": _column_size(col_raw, float(e["concrete_grade"]), float(e["steel_grade"]),
                                       float(e["column_steel_pct"])),
        "k2": k2, "design_pressure_kn_sqm": pd_t,
        "cf": cf_t,
        "wind_force_kn": round(cf_t * face * t["height_m"] * pd_t_raw, 1),
    }


# ================================================================ 2. seismic
def m2_seismic(project, base, e, city, loads):
    b = _building(project, base)
    zone = city["zone"]
    z = C.ZONE_FACTOR[zone]
    soil = e["soil_type"]
    soil_type = C.SOIL_SEISMIC_TYPE.get(soil, "II")
    h = max(b["height_m"], 3.0)
    frame_type = e["frame_type"] if e["frame_type"] in C.SEISMIC_FRAME_TYPES else C.DEFAULT_FRAME_TYPE
    base_dim = math.sqrt(max(b["footprint"], 1.0)) if b["footprint"] else 0.0
    ta_raw, ta_formula = C.seismic_period(h, base_dim, frame_type)
    ta = round(ta_raw, 3)
    ta_bare = round(0.075 * h ** 0.75, 3)

    def sa_g(T, st):
        if st == "I":
            return 2.5 if T < 0.40 else min(1.00 / T, 2.5) if T <= 4 else 0.25
        if st == "II":
            return 2.5 if T < 0.55 else min(1.36 / T, 2.5) if T <= 4 else 0.34
        return 2.5 if T < 0.67 else min(1.67 / T, 2.5) if T <= 4 else 0.42

    sa_raw = sa_g(ta_raw, soil_type)
    sa = round(sa_raw, 3)
    r = C.RESPONSE_R.get(e["structural_system"], 5.0)
    # Table 8 is decided per building: the most heavily occupied tower sets I for the
    # site-level figure, and each tower is re-checked on its own occupancy below.
    peak_occupants = max((float(t.get("occupants") or 0) for t in b["towers"]), default=0.0)
    imp, imp_basis = C.importance_factor(e["importance"], peak_occupants)
    ah_raw, ah_floored = C.ah_with_minimum(z * imp * sa_raw / (2 * r), zone)
    ah = round(ah_raw, 5)
    dead_for_seismic = loads["derived"].get("dead_raw", loads["derived"]["dead"])
    seismic_load = dead_for_seismic + 0.25 * loads["derived"]["live"]
    w_raw = seismic_load * b["builtup"]
    v_raw = ah_raw * w_raw
    w = round(w_raw, 1)
    v = round(v_raw, 1)

    warnings = []
    if zone in ("IV", "V") and h > 15:
        warnings.append({"severity": "critical", "text": f"Building height {h} m in Zone {zone} — ductile detailing "
                                                         "to IS 13920 is mandatory and a special seismic design review is required.",
                         "clause": C.clause("ductile")})
    elif zone == "III" and h > 24:
        warnings.append({"severity": "warning", "text": f"Height {h} m in Zone III — provide IS 13920 ductile detailing.",
                         "clause": C.clause("ductile")})
    if e["structural_system"] == "OMRF" and zone in ("III", "IV", "V"):
        warnings.append({"severity": "critical", "text": "Ordinary moment frames are not permitted in Zone III and above — "
                                                         "use SMRF, shear walls or a dual system.",
                         "clause": C.clause("seismic_R")})
    if soil == "soft clay" and zone in ("IV", "V"):
        warnings.append({"severity": "warning", "text": "Soft soil in a high seismic zone amplifies response — "
                                                        "liquefaction assessment recommended.", "clause": C.clause("seismic_sa")})

    if zone in ("IV", "V") or h > 40:
        system = "Dual system (RC moment frame + shear walls) with IS 13920 detailing"
    elif h > 24:
        system = "Shear wall / core wall assisted moment frame"
    else:
        system = "Special moment resisting frame (SMRF)"

    return {
        "id": "seismic", "title": "Seismic Zone & Base Shear", "codes": ["IS 1893 (Part 1):2016", "IS 13920:2016"],
        "missing": [] if b["floors"] else ["Tower floor count (Apartment Planning)"],
        "zone": zone, "zone_factor": z, "warnings": warnings,
        "outputs": [
            out("Seismic zone", zone, "", "seismic_zone", f"{city['city']}, {city['state']} ({city['source']} data)"),
            out("Zone factor Z", z, "", "seismic_zone"),
            out("Soil / site type", f"Type {soil_type} — {C.SOILS.get(soil, {}).get('label', soil)}", "", "seismic_sa"),
            out("Fundamental period Ta", ta, "s", "seismic_period", ta_formula),
            out("Frame type", C.SEISMIC_FRAME_TYPES[frame_type], "", "seismic_period",
                (f"bare-frame period would be {ta_bare} s — using it on an infilled frame "
                 "under-states base shear") if frame_type != "bare_frame" else ""),
            out("Spectral acceleration Sa/g", sa, "", "seismic_sa"),
            out("Response reduction R", r, "", "seismic_R", e["structural_system"]),
            out("Importance factor I", imp, "", "seismic_I", imp_basis),
            out("Design horizontal coefficient Ah", ah, "", "base_shear",
                f"Table 7 minimum for Zone {zone} governs" if ah_floored else "Z·I·(Sa/g) ÷ 2R"),
            out("Seismic weight W", w, "kN", "seismic_weight", "DL + 25% LL over built-up area"),
            out("Design base shear VB", v, "kN", "base_shear"),
            out("Base shear as % of W", round(ah_raw * 100, 2), "%", "base_shear"),
        ],
        "recommendation": {"label": "Recommended lateral system", "value": system, "clause": C.clause("seismic_R")},
        "per_tower": [_tower_seismic(t, z, soil_type, r,
                                     C.importance_factor(e["importance"], t.get("occupants") or 0)[0],
                                     sa_g, loads, frame_type, zone)
                      for t in b["towers"]],
        "derived": {"base_shear": v, "vb_kn": v, "ah": ah, "seismic_weight": w},
    }


def _tower_seismic(t, z, soil_type, r, imp, sa_g, loads, frame_type="brick_infill", zone=""):
    h = max(t["height_m"], 3.0)
    base_dim = math.sqrt(max(t["footprint_sqm"], 1.0)) if t["footprint_sqm"] else 0.0
    ta_raw = C.seismic_period(h, base_dim, frame_type)[0]
    ta = round(ta_raw, 3)
    sa_raw = sa_g(ta_raw, soil_type)
    sa = round(sa_raw, 3)
    ah_raw = C.ah_with_minimum(z * imp * sa_raw / (2 * r), zone)[0]
    ah = round(ah_raw, 5)
    dead_for_seismic = loads["derived"].get("dead_raw", loads["derived"]["dead"])
    w_raw = (dead_for_seismic + 0.25 * loads["derived"]["live"]) * t["builtup_sqm"]
    w = round(w_raw, 1)
    return {"id": t["id"], "name": t["name"], "floors": t["floors"], "height_m": t["height_m"],
            "period_s": ta, "sa_g": sa, "ah": ah, "seismic_weight_kn": w,
            "base_shear_kn": round(ah_raw * w_raw, 1), "base_shear_pct_w": round(ah_raw * 100, 2)}


# ================================================================ 3. foundation
def m3_foundation(project, base, e, loads):
    b = _building(project, base)
    soil = C.SOILS.get(e["soil_type"], C.SOILS["dense sand"])
    sbc = soil["sbc"]
    phi = math.radians(soil["phi"])
    gamma = soil["gamma"]
    col_load_raw = loads["derived"].get("column_load_raw", loads["derived"]["column_load"])
    q = min(sbc, col_load_raw / max(float(e["grid_bay_x_m"]) * float(e["grid_bay_y_m"]), 1) * 4)
    df = (q / gamma) * ((1 - math.sin(phi)) / (1 + math.sin(phi))) ** 2
    df = round(max(df, 0.5), 2)

    service_load_raw = col_load_raw / 1.5
    req_area_raw = service_load_raw / sbc if sbc else 0.0
    req_area = round(req_area_raw, 2)
    footing_side = round(math.sqrt(req_area_raw) + 0.05, 2) if req_area_raw > 0 else 0
    floors = int(b["floors"] or 0)
    poor = e["soil_type"] in ("soft clay",)

    if floors <= 3 and not poor:
        ftype = "Isolated / pad footings"
    elif floors <= 7 and not poor:
        ftype = "Combined footings or raft"
    elif floors <= 7 and poor:
        ftype = "Raft foundation (poor soil)"
    else:
        ftype = "Pile foundation with pile cap"

    # Bearing pressure must be measured against the footing actually provided, not the
    # exact required area — service_load / (service_load / sbc) is identically the SBC, so
    # the old output could never differ from it and the "vs SBC" comparison said nothing.
    provided_area_raw = footing_side ** 2 if footing_side else 0.0
    provided_area = round(provided_area_raw, 2)
    applied_raw = service_load_raw / provided_area_raw if provided_area_raw else 0.0
    applied = round(applied_raw, 1)
    utilisation = round(applied_raw / sbc * 100, 1) if sbc else 0
    trib = float(e["grid_bay_x_m"]) * float(e["grid_bay_y_m"])
    warnings = []
    if req_area_raw > 0.35 * trib:
        warnings.append({"severity": "critical",
                         "text": f"Required footing area {req_area} m² exceeds 35% of the {trib} m² column grid — "
                                 f"SBC of {sbc} kN/m² is insufficient for the {round(service_load_raw)} kN service load. "
                                 "Switch to a raft or piles, or improve the ground.",
                         "clause": C.clause("sbc")})
    elif req_area_raw > 0.2 * trib:
        warnings.append({"severity": "warning",
                         "text": f"Footings occupy {round(req_area_raw / trib * 100)}% of the grid area — combined footings "
                                 "or a raft may be more economical.", "clause": C.clause("found_type")})
    if poor and floors > 4:
        warnings.append({"severity": "critical", "text": "Soft clay with more than 4 floors — deep foundations required, "
                                                         "with settlement analysis.", "clause": C.clause("found_type")})

    return {
        "id": "foundation", "title": "Foundation Advisor", "codes": ["IS 6403:1981", "IS 1904:1986"],
        "missing": [] if floors else ["Tower floor count (Apartment Planning)"],
        "warnings": warnings,
        "sbc_table": [{"soil": v["label"], "sbc": v["sbc"], "phi": v["phi"], "gamma": v["gamma"],
                       "selected": k == e["soil_type"]} for k, v in C.SOILS.items()],
        "outputs": [
            out("Soil type", soil["label"], "", "sbc"),
            out("Safe bearing capacity (SBC)", sbc, "kN/m²", "sbc"),
            out("Angle of internal friction φ", soil["phi"], "°", "sbc"),
            out("Minimum foundation depth Df", df, "m", "rankine", "Rankine: (q/γ)·((1−sinφ)/(1+sinφ))²"),
            out("Column service load", round(service_load_raw, 1), "kN", "found_type"),
            out("Required footing area", req_area, "m²", "found_type"),
            out("Isolated footing size", f"{footing_side} × {footing_side}", "m", "found_type"),
            out("Provided footing area", provided_area, "m²", "found_type", "size rounded up from the required area"),
            out("Applied bearing pressure", applied, "kN/m²", "sbc",
                f"service load ÷ provided footing area, vs SBC {sbc} kN/m²"),
            out("Bearing capacity utilisation", utilisation, "%", "sbc",
                "applied pressure as a share of the safe bearing capacity"),
        ],
        "recommendation": {"label": "Recommended foundation type", "value": ftype, "clause": C.clause("found_type")},
    }


# ================================================================ 4. mix design
def m4_mix_design(project, base, e):
    grade = int(e["concrete_grade"])
    exposure = C.EXPOSURE.get(e["exposure_condition"], C.EXPOSURE["moderate"])
    agg = int(e["aggregate_size_mm"])
    s = C.MIX_STD_DEV.get(grade, 5.0)
    # Proportioning is shared with the quantity take-off, which needs cement, sand and
    # aggregate per m3 for the very same grade.
    mix = takeofflib.mix_proportions(grade, e["exposure_condition"], agg)
    target = mix["target_strength"]
    wc = mix["wc_ratio"]
    water = mix["water_l"]
    cement = mix["cement_kg"]
    coarse = mix["coarse_kg"]
    fine = mix["fine_kg"]
    ratio_fine = round(fine / cement, 2)
    ratio_coarse = round(coarse / cement, 2)
    concrete_item = next((it for it in base["quantities"]["items"] if it["key"] == "concrete"), None)
    default_vol = float(concrete_item["quantity"]) if concrete_item else 0.0
    volume = float(e["concrete_volume_cum"]) or default_vol

    boq = [
        {"material": "Cement", "per_cum": cement, "unit": "kg", "total": round(cement * volume, 1),
         "bags": round(cement * volume / 50.0, 1)},
        {"material": "Fine aggregate (sand)", "per_cum": fine, "unit": "kg", "total": round(fine * volume, 1),
         "tonnes": round(fine * volume / 1000.0, 2)},
        {"material": "Coarse aggregate", "per_cum": coarse, "unit": "kg", "total": round(coarse * volume, 1),
         "tonnes": round(coarse * volume / 1000.0, 2)},
        {"material": "Water", "per_cum": water, "unit": "litre", "total": round(water * volume, 1)},
    ]

    return {
        "id": "mix", "title": "Concrete Mix Design", "codes": ["IS 10262:2019", "IS 456:2000"], "missing": [],
        "outputs": [
            out("Grade of concrete", f"M{grade}", "", "mix_target"),
            out("Target mean strength f'ck", target, "N/mm²", "mix_target", f"fck + 1.65 × {s}"),
            out("Exposure condition", e["exposure_condition"], "", "mix_wc",
                f"max w/c {exposure['max_wc']}, min cement {exposure['min_cement']} kg/m³, cover {exposure['cover']} mm"),
            out("Free water-cement ratio", wc, "", "mix_wc"),
            out("Water content", water, "litre/m³", "mix_water", f"{agg} mm aggregate, 25–50 mm slump"),
            out("Cement content", cement, "kg/m³", "mix_cement", e["cement_type"]),
            out("Coarse aggregate volume fraction", mix["ca_volume_fraction"], "", "mix_ca"),
            out("Fine aggregate", fine, "kg/m³", "mix_ca"),
            out("Coarse aggregate", coarse, "kg/m³", "mix_ca"),
            out("Mix proportion (by weight)", f"1 : {ratio_fine} : {ratio_coarse}", "", "mix_target"),
        ],
        "recommendation": {"label": "Mix ratio", "value": f"1 : {ratio_fine} : {ratio_coarse} at w/c {wc}",
                           "clause": C.clause("mix_target")},
        "boq_link": {"volume_cum": round(volume, 2), "rows": boq,
                     "note": "Volume defaults to the BOQ concrete quantity; override it to price a specific pour."},
    }


# ================================================================ 5. water
def m5_water(project, base, e):
    b = _building(project, base)
    missing = [] if b["units"] else ["Unit count (Apartment Planning)"]

    # Shared with the Utilities module (engine.water_demand) so the two panels can no
    # longer disagree on daily demand, sump size or STP capacity for the same project.
    w = enginelib.water_demand(project, base["areas"])
    persons = w["persons"]
    dom, flush, ext = w["domestic_lpd"], w["flushing_lpd"], w["external_lpd"]
    total = w["total_lpd"]

    # The high-rise trigger was a bare 15 here, which no version switch could reach. It is
    # also the single value SP 7:2026 is most reported to move, so of all the numbers in
    # this module it is the one that must not be a literal.
    fw = C.fire_water(e["code_version"])
    fire_unread = C.unread_keys(fw)
    water_warnings = []
    if fire_unread:
        # Domestic demand is IS 1172 and is unaffected, so the module still answers that.
        # The fire reserve is not sized, and the sump it feeds is labelled as excluding it
        # rather than quietly reported as if the reserve were zero.
        tall = None
        fire_reserve = fire_static = 0
        water_warnings.append({"severity": "high", "message": (
            f"Fire storage is not included in the sump: {C.version_label(e['code_version'])} "
            f"has not been read, so neither the high-rise trigger nor the stored volume is "
            f"known. Domestic and flushing demand below are IS 1172 and are unaffected.")})
    else:
        tall = b["height_m"] > fw["high_rise_above_m"]
        fire_reserve = fw["reserve_in_sump_l"] if tall else 0
        fire_static = fw["static_storage_l"] if tall else 0
    sump_l = total + fire_reserve
    d = float(e["sump_depth_m"])
    area = sump_l / 1000.0 / d
    bw = math.sqrt(area / 2.0)
    sump_dim = f"{round(2 * bw, 2)} × {round(bw, 2)} × {d} m"

    oht_l = total * C.OHT_FRACTION
    tanks = max(int(e["oht_tanks"]), 2)
    per_tank = oht_l / tanks

    sewage = w["sewage_lpd"]
    stp_kld = round(sewage / 1000.0, 2)
    stp_type = next(label for cap, label in C.STP_TYPES if stp_kld <= cap)

    # The note beside the two fire figures names the trigger it was applied at, so a reader
    # never has to assume 15 m. When the version is unread there is no trigger to name.
    tall_note = ("threshold not read from " + C.version_label(e["code_version"])
                 if fire_unread else f"buildings above {fw['high_rise_above_m']:g} m")

    return {
        "id": "water", "title": "Water Infrastructure", "codes": ["IS 1172:1993", "NBC 2016 Part 9", "NBC 2016 Part 4"],
        "code_version": e["code_version"], "code_version_label": C.version_label(e["code_version"]),
        "missing": missing + ([f"{C.version_label(e['code_version'])} fire storage provisions "
                               f"— not read from the standard"] if fire_unread else []),
        "warnings": water_warnings,
        "outputs": [
            out("Population", persons, "persons", "water_demand", "per-unit-type occupancy from Apartment Planning"),
            # Labels carry the per-capita figure actually used: the 135 lpcd total is split
            # in the 135:45:15 proportions, so no line runs at its nominal table value.
            out(f"Domestic demand @{_lpcd(dom, persons)} lpcd", round(dom), "litre/day", "water_demand"),
            out(f"Flushing demand @{_lpcd(flush, persons)} lpcd", round(flush), "litre/day", "water_demand"),
            out(f"External / gardening @{_lpcd(ext, persons)} lpcd", round(ext), "litre/day", "water_demand"),
            out("Total daily demand", round(total), "litre/day", "water_demand"),
            out("Fire reserve in sump", fire_reserve, "litre", "fire_water", tall_note),
            out("Underground sump capacity", round(sump_l), "litre", "sump", "1 day demand + fire reserve"),
            out("Sump dimensions (L × B × D)", sump_dim, "", "sump", f"free board excluded, depth {d} m"),
            out("Overhead tank capacity", round(oht_l), "litre", "oht", "one-third of daily demand"),
            out("Overhead tanks", f"{tanks} × {round(per_tank)} L", "", "oht", "minimum 2 compartments (cross-connection rule)"),
            out("Sewage generation", round(sewage), "litre/day", "sewage", "80% of water supply"),
            out("STP capacity", stp_kld, "KLD", "stp"),
            out("Dedicated fire static storage", fire_static, "litre", "fire_water",
                "separate from domestic storage; " + tall_note),
        ],
        "recommendation": {"label": "Recommended STP technology", "value": stp_type, "clause": C.clause("stp")},
        "derived": {"total_lpd": total, "stp_kld": stp_kld, "persons": persons,
                    "reuse_potential_lpd": round(sewage * 0.8)},
    }


def _lpcd(litres_per_day, persons):
    return round(litres_per_day / persons, 1) if persons else 0


# ================================================================ 6. storm water & RWH
def m6_storm_rwh(project, base, e, city):
    b = _building(project, base)
    plot = b["plot_area"]
    roof = float(e["roof_area_sqm"]) or b["footprint"]
    missing = [] if plot else ["Plot polygon or dimensions (Plot & Site)"]
    intensity = float(e["rain_intensity_override"]) or city["rain_intensity_mm_hr"]
    c_roof = C.RUNOFF_C["rcc_roof"]
    c_site = C.RUNOFF_C.get(e["site_area_type"], 0.6)
    weighted_raw = (roof * c_roof + max(plot - roof, 0) * c_site) / plot if plot else c_roof
    weighted = round(weighted_raw, 3)
    area_ha = plot / 10000.0
    q_raw = weighted_raw * intensity * area_ha / 360.0  # m³/s
    q = round(q_raw, 4)

    n, s = C.MANNING_N, C.DRAIN_SLOPE
    d = ((q_raw * n * 4 ** (5 / 3)) / (math.pi * math.sqrt(s))) ** (3 / 8) if q_raw > 0 else 0
    dia_mm = max(math.ceil(d * 1000 / 50) * 50, 150)
    a_full = math.pi * (dia_mm / 1000.0) ** 2 / 4
    velocity_raw = q_raw / a_full if a_full else 0.0
    velocity = round(velocity_raw, 2)

    annual = round(roof * (city["annual_rainfall_mm"] / 1000.0) * c_roof * 1000, 0)  # litres/yr
    pit_vol_raw = roof * (intensity / 1000.0)  # 1 hour of peak rainfall, m³
    pit_vol = round(pit_vol_raw, 2)
    pit_side = round(math.sqrt(pit_vol_raw / 2.0), 2) if pit_vol_raw else 0
    mandatory = plot > C.RWH_MANDATORY_PLOT_SQM

    checks = [
        check("Self-cleansing velocity 0.6–3.0 m/s", 0.6 <= velocity_raw <= 3.0, f"{velocity} m/s", "0.6–3.0 m/s", "storm_pipe"),
        # The check is "is RWH provided", not "is it mandatory" — scoring a compliant
        # small plot as a red failure because the rule does not bite is backwards.
        check("Rainwater harvesting provided",
              bool(e["rwh_provided"]) if mandatory else True,
              "provided" if e["rwh_provided"] else "not provided",
              f"mandatory above {C.RWH_MANDATORY_PLOT_SQM} m²" if mandatory else "not applicable",
              "rwh",
              (f"Plot is {round(plot)} m² — RWH is mandatory; recharge pit sized below"
               if mandatory else
               f"Plot is {round(plot)} m², below the {C.RWH_MANDATORY_PLOT_SQM} m² threshold — "
               "not mandatory, still recommended")),
    ]

    return {
        "id": "storm", "title": "Storm Water & Rainwater Harvesting", "codes": ["IS 3764", "NBC 2016 Part 9"],
        "missing": missing, "checks": checks,
        "outputs": [
            out("Design rainfall intensity", intensity, "mm/hr", "storm_rational", f"{city['city']} ({city['source']} data)"),
            out("Annual rainfall", city["annual_rainfall_mm"], "mm", "storm_rational"),
            out("Weighted runoff coefficient C", weighted, "", "storm_rational", f"roof {c_roof} / site {c_site}"),
            out("Peak storm runoff Q", q, "m³/s", "storm_rational", "Q = C·I·A / 360"),
            out("Storm drain diameter", dia_mm, "mm", "storm_pipe", f"Manning n={n}, slope 1:{int(1/s)}"),
            out("Flow velocity", velocity, "m/s", "storm_pipe"),
            out("Roof catchment area", round(roof, 1), "m²", "rwh"),
            out("RWH annual yield", annual, "litre/year", "rwh", f"roof × rainfall × {c_roof}"),
            out("Recharge pit / storage volume", pit_vol, "m³", "rwh", "one hour of peak rainfall from the roof"),
            out("Recharge pit size (L × B × D)", f"{round(pit_side*2,2)} × {pit_side} × 1.0 m" if pit_side else "—", "", "rwh"),
        ],
        "recommendation": {"label": "RWH requirement",
                           "value": "Mandatory — provide recharge pits and a first-flush arrangement" if mandatory
                           else "Voluntary — recommended for water positive rating",
                           "clause": C.clause("rwh")},
        "derived": {"rwh_annual_l": annual, "mandatory": mandatory},
    }


# ================================================================ 7. parking (NBC)
def _ecs_required(b):
    """ECS demand — one definition, used by the Parking and Accessibility modules alike."""
    # The clause is per 100 m² of built-up (floor) area; super built-up adds a sales
    # loading that is not floor area and overstated the demand.
    return math.ceil(b["builtup"] / C.PARKING["ecs_per_sqm"]) if b["builtup"] else 0


def _accessible_bays_required(b):
    """Accessible bays (1 per 50 ECS).

    Previously the Parking module derived this from the NBC ECS demand while the
    Accessibility module derived it from engine's own `parking.required_slots`, so the
    same project could be told it needed two different numbers of accessible bays.
    """
    ecs = _ecs_required(b)
    return math.ceil(ecs / C.PARKING["accessible_per"]) if ecs else 0


def m7_parking_nbc(project, base, e):
    b = _building(project, base)
    p = project.get("parking") or {}
    ramp = p.get("ramp") or {}
    ecs_required = _ecs_required(b)
    provided = base["parking"]["provided_slots"]
    accessible_req = _accessible_bays_required(b)
    ev_req = math.ceil(ecs_required * C.PARKING["ev_pct"] / 100.0)
    tw_required = math.ceil(b["units"] * 0.5)
    tw_ecs_equivalent = round(tw_required / C.PARKING["two_wheeler_per_ecs"], 1)

    slope = float(ramp.get("slope_pct") or 0)
    width = float(ramp.get("width") or 0)
    checks = [
        check("ECS provided vs required (1 ECS / 100 m²)", provided >= ecs_required, provided, ecs_required, "parking_ecs"),
        check("Ramp gradient ≤ 1:8 (12.5%)", slope <= C.PARKING["max_ramp_pct"], f"{slope}%",
              f"≤ {C.PARKING['max_ramp_pct']}%", "parking_ramp"),
        check("Ramp width ≥ 3.6 m (one-way)", width >= C.PARKING["ramp_width_one_way"], f"{width} m",
              f"≥ {C.PARKING['ramp_width_one_way']} m", "parking_ramp",
              "6.0 m required if two-way traffic is used"),
        check("Basement clear headroom ≥ 2.4 m", float(e["basement_headroom_m"]) >= C.PARKING["basement_headroom"],
              f"{e['basement_headroom_m']} m", "≥ 2.4 m", "parking_headroom"),
        check("Aisle width ≥ 6.0 m for 90° bays", float(e["aisle_width_m"]) >= C.PARKING["aisle_90deg"],
              f"{e['aisle_width_m']} m", "≥ 6.0 m", "parking_aisle"),
        check("Accessible bays (1 per 50)", int(p.get("accessible_provided") or 0) >= accessible_req,
              p.get("accessible_provided") or 0, accessible_req, "parking_accessible"),
        check("EV-ready spaces (20%)", int(p.get("ev_provided") or 0) >= ev_req, p.get("ev_provided") or 0,
              ev_req, "parking_ev"),
        check("Two-wheeler spaces (0.5 per unit)", int(e["two_wheeler_provided"]) >= tw_required,
              e["two_wheeler_provided"], tw_required, "parking_2w",
              f"{tw_required} two-wheelers = {tw_ecs_equivalent} ECS (1 ECS = 3 two-wheelers)"),
    ]
    passed = sum(1 for c in checks if c["status"] == "pass")

    return {
        "id": "parking_nbc", "title": "Parking Compliance (NBC / SP:21)", "codes": ["NBC 2016 Part 4", "SP:21"],
        "missing": [] if b["builtup"] else ["Built-up area (Apartment Planning)"],
        "checks": checks, "passed": passed, "total": len(checks),
        "score": round(passed / len(checks) * 100, 1),
        "outputs": [
            out("Built-up area", round(b["builtup"], 1), "m²", "parking_ecs"),
            out("ECS required", ecs_required, "ECS", "parking_ecs", "1 ECS per 100 m² residential"),
            out("Car spaces provided", provided, "nos", "parking_ecs", "from the Parking module"),
            out("Accessible bays required", accessible_req, "nos", "parking_accessible"),
            out("EV-ready spaces required", ev_req, "nos", "parking_ev"),
            out("Two-wheeler spaces required", tw_required, "nos", "parking_2w"),
            out("Area per ECS (indicative)", C.PARKING["ecs_area"], "m²", "parking_ecs"),
        ],
    }


# ================================================================ 8. fire safety
def m8_fire(project, base, e):
    b = _building(project, base)
    tallest = b["tallest"]
    missing = [] if tallest else ["At least one tower (Apartment Planning)"]
    h = b["height_m"]
    floors = int(b["floors"] or 0)
    plate = (tallest["builtup_per_floor_sqm"] if tallest else 0)
    travel = tallest["max_travel_distance_m"] if tallest else 0
    stair_count = tallest["stair_count"] if tallest else 0
    stair_width = tallest["stair_min_width"] if tallest else 0
    lifts = tallest["lift_count"] if tallest else 0

    version = e["code_version"]
    F = C.fire_table(version)
    unread = C.unread_keys(F)
    if unread:
        # Refuse the module rather than check the part of it that happens to have numbers.
        # A fire score of "3 of 8 passed" computed from a table that is half empty reads as
        # a finding about the building; it is a finding about the table. Everything below
        # would also raise on the first comparison against UNREAD, which is the sentinel
        # doing its job -- this is the branch that turns that into an answer.
        return {
            "id": "fire", "title": f"Fire Safety Compliance ({C.version_label(version)})",
            "codes": [C.CODE_VERSIONS[version]["title"]],
            "code_version": version, "code_version_label": C.version_label(version),
            "missing": missing + [
                f"{C.version_label(version)} fire provisions — not read from the standard"],
            "checks": [], "passed": 0, "total": 0, "score": None,
            "unchecked": True, "unread_values": unread,
            "floor_rows": [], "refuge_floors": [],
            "warnings": [{"severity": "high", "message": (
                f"Fire safety cannot be checked against {C.version_label(version)}: "
                f"{len(unread)} threshold(s) have not been read from the standard "
                f"({', '.join(unread)}). Switch the project's code version to "
                f"{C.version_label(C.NBC_2016)} to check against the provisions state "
                f"bye-laws currently reference, or read SP 7:2026 and set the values.")}],
            "outputs": [
                out("Building height", h, "m", "fire_stairs"),
                out("Typical floor plate", round(plate, 1), "m²", "fire_ext"),
                out("Checked against", C.version_label(version), "", None,
                    "no threshold in this version has been read from the standard"),
            ],
        }

    need_two_stairs = h > F["two_stair_height_m"]
    need_refuge = h > F["refuge_above_m"]
    refuge_floors = list(range(F["refuge_every_floors"], floors + 1, F["refuge_every_floors"])) if need_refuge else []
    car = e["fire_lift_car_m"]
    need_fire_lift = h > F["fire_lift_above_m"]
    need_press = h > F["pressurisation_above_m"]
    ext_per_floor = math.ceil(plate / F["extinguisher_per_sqm"]) if plate else 0

    checks = [
        # Label and threshold both derive from the constant — a hardcoded "22.5 m" in the
        # label survived the constant changing to 30 m and told the user the wrong rule.
        check(f"Travel distance to nearest exit ≤ {F['max_travel_m']:g} m",
              travel <= F["max_travel_m"] and travel > 0,
              f"{travel} m", f"≤ {F['max_travel_m']:g} m", "fire_travel"),
        check("Minimum 2 staircases above 24 m", (stair_count >= 2) if need_two_stairs else stair_count >= 1,
              stair_count, "≥ 2" if need_two_stairs else "≥ 1", "fire_stairs",
              f"building height {h} m"),
        check("Staircase clear width ≥ 1.5 m", stair_width >= F["stair_min_width_m"], f"{stair_width} m",
              "≥ 1.5 m", "fire_stairs"),
        check("Refuge area every 7th floor above 24 m",
              (int(e["refuge_floors_provided"]) >= len(refuge_floors)) if need_refuge else True,
              e["refuge_floors_provided"], len(refuge_floors) if need_refuge else 0, "fire_refuge",
              f"required at floors {refuge_floors}" if refuge_floors else "not applicable below 24 m"),
        check("Fire lift provided above 30 m", (lifts >= 1) if need_fire_lift else True, lifts,
              "≥ 1 fire lift" if need_fire_lift else "not applicable", "fire_lift"),
        check("Fire lift car ≥ 1.1 × 2.1 m (stretcher)",
              (float(car[0]) >= F["fire_lift_car"][0] and float(car[1]) >= F["fire_lift_car"][1])
              if need_fire_lift else True, f"{car[0]} × {car[1]} m", "≥ 1.1 × 2.1 m", "fire_lift"),
        check(f"Stairwell pressurisation above {F['pressurisation_above_m']:g} m",
              bool(e["stair_pressurisation"]) if need_press else True,
              "provided" if e["stair_pressurisation"] else "not provided",
              "required" if need_press else "not applicable", "fire_press"),
        # ceil(plate/200) >= 1 is true for any positive plate, so the old form was a free
        # pass that inflated the fire score. Compare what is provided against what is
        # required instead.
        check("Fire extinguishers 1 per 200 m² per floor",
              int(e["extinguishers_per_floor"]) >= ext_per_floor if plate else False,
              f"{e['extinguishers_per_floor']} provided per floor",
              f"{ext_per_floor} required per floor", "fire_ext",
              f"floor plate {round(plate,1)} m² at 1 per {int(F['extinguisher_per_sqm'])} m²"),
    ]
    passed = sum(1 for c in checks if c["status"] == "pass")

    floor_rows = []
    for f in range(1, floors + 1):
        is_refuge = f in refuge_floors
        floor_rows.append({
            "floor": f,
            "level_m": round((f - 1) * (tallest["floor_height"] if tallest else 3), 2),
            "travel_ok": travel <= F["max_travel_m"] and travel > 0,
            "extinguishers": ext_per_floor,
            "refuge_required": is_refuge,
            "pressurisation": need_press,
            "status": "pass" if (travel <= F["max_travel_m"] and travel > 0
                                 and int(e["extinguishers_per_floor"]) >= ext_per_floor and ext_per_floor >= 1
                                 and (not is_refuge or int(e["refuge_floors_provided"]) >= len(refuge_floors))) else "fail",
        })

    return {
        "id": "fire", "title": "Fire Safety Compliance (NBC Part 4)", "codes": ["NBC 2016 Part 4", "IS 2190"],
        "code_version": version, "code_version_label": C.version_label(version),
        "unchecked": False,
        "missing": missing, "checks": checks, "passed": passed, "total": len(checks),
        "score": round(passed / len(checks) * 100, 1) if checks else 0,
        "floor_rows": floor_rows, "refuge_floors": refuge_floors,
        "outputs": [
            out("Building height", h, "m", "fire_stairs"),
            out("Typical floor plate", round(plate, 1), "m²", "fire_ext"),
            out("Extinguishers per floor", ext_per_floor, "nos", "fire_ext"),
            out("Refuge floors required", ", ".join(map(str, refuge_floors)) or "none", "", "fire_refuge"),
            out("Fire lift requirement", "required" if need_fire_lift else "not required", "", "fire_lift"),
            out("Stairwell pressurisation", "required" if need_press else "not required", "", "fire_press"),
        ],
    }


# ================================================================ 9. accessibility
def m9_accessibility(project, base, e):
    b = _building(project, base)
    tallest = b["tallest"]
    corridor_mm = (tallest["corridor_width"] * 1000) if tallest else 0
    car = e["lift_car_mm"]
    slope = float(e["pedestrian_ramp_slope"])
    max_slope_raw = C.ACCESS["ramp_slope"] * 100.0
    max_slope = round(max_slope_raw, 2)

    checks = [
        check("Ramp slope ≤ 1:12 (8.33%)", slope <= max_slope_raw + 1e-9, f"{slope}%", f"≤ {max_slope}%", "acc_ramp"),
        check("Clear door width ≥ 900 mm", float(e["door_width_mm"]) >= C.ACCESS["door_width_mm"],
              f"{e['door_width_mm']} mm", "≥ 900 mm", "acc_door"),
        check("Corridor width ≥ 1200 mm", corridor_mm >= C.ACCESS["corridor_min_mm"], f"{round(corridor_mm)} mm",
              "≥ 1200 mm", "acc_corridor", "1500 mm preferred for wheelchair passing"),
        check("Corridor width ≥ 1500 mm (preferred)", corridor_mm >= C.ACCESS["corridor_pref_mm"],
              f"{round(corridor_mm)} mm", "≥ 1500 mm preferred", "acc_corridor"),
        check("Lift car ≥ 1100 × 1400 mm", float(car[0]) >= C.ACCESS["lift_car_mm"][0]
              and float(car[1]) >= C.ACCESS["lift_car_mm"][1], f"{car[0]} × {car[1]} mm",
              "≥ 1100 × 1400 mm", "acc_lift"),
        check("Dual handrails at 760 & 900 mm", bool(e["dual_handrails"]),
              "provided" if e["dual_handrails"] else "not provided", "required", "acc_handrail"),
        check("Tactile guiding path entrance → lift", bool(e["tactile_path"]),
              "provided" if e["tactile_path"] else "not provided", "required", "acc_tactile"),
        check("Accessible parking bays",
              int((project.get("parking") or {}).get("accessible_provided") or 0)
              >= _accessible_bays_required(b),
              (project.get("parking") or {}).get("accessible_provided") or 0,
              _accessible_bays_required(b), "parking_accessible",
              "same 1-per-50-ECS basis as the Parking module"),
    ]
    passed = sum(1 for c in checks if c["status"] == "pass")
    return {
        "id": "accessibility", "title": "Accessibility Compliance (NBC Part 3 / UNCRPD)",
        "codes": ["NBC 2016 Part 3", "IS 3534", "RPwD Act 2016"],
        "missing": [] if tallest else ["Tower corridor width (Apartment Planning)"],
        "checks": checks, "passed": passed, "total": len(checks),
        "score": round(passed / len(checks) * 100, 1),
        "failed_clauses": [c["clause"] for c in checks if c["status"] == "fail"],
    }


# ================================================================ 11. green rating
def m11_green(project, base, e, water, storm):
    selected = dict(e.get("green_checklist") or {})
    auto = {}
    if bool(e.get("rwh_provided", True)) and storm["derived"]["rwh_annual_l"] > 0:
        auto["water_rwh"] = True
    if water["derived"]["stp_kld"] > 0:
        auto["water_stp"] = True
    items = []
    earned = 0
    total = 0
    for item in C.GREEN_CHECKLIST:
        is_auto = item.get("auto") and auto.get(item["id"])
        checked = bool(selected.get(item["id"])) or bool(is_auto)
        total += item["points"]
        if checked:
            earned += item["points"]
        items.append({**item, "checked": checked, "auto_credited": bool(is_auto)})
    pct = round(earned / total * 100, 1) if total else 0
    stars = 0
    for threshold, star in C.GRIHA_BANDS:
        if pct >= threshold:
            stars = star
    igbc = "Not certified"
    for threshold, level in C.IGBC_BANDS:
        if pct >= threshold:
            igbc = level
    categories = {}
    for i in items:
        c = categories.setdefault(i["category"], {"earned": 0, "total": 0})
        c["total"] += i["points"]
        if i["checked"]:
            c["earned"] += i["points"]

    return {
        "id": "green", "title": "Green Building Preliminary Rating", "codes": ["GRIHA v2019", "IGBC Green Homes v3.0"],
        "missing": [], "items": items,
        "categories": [{"category": k, **v, "pct": round(v["earned"] / v["total"] * 100, 1)} for k, v in categories.items()],
        "outputs": [
            out("Points earned", f"{earned} / {total}", "", "griha"),
            out("Score", pct, "%", "griha"),
            out("GRIHA rating", f"{stars} star" if stars else "below 1 star", "", "griha"),
            out("IGBC Green Homes level", igbc, "", "igbc"),
            out("Water efficiency credits", "auto-credited from Water & Storm modules", "", "rwh"),
        ],
        "recommendation": {"label": "Estimated rating",
                           "value": f"GRIHA {stars}★ / IGBC {igbc} at {pct}%", "clause": C.clause("griha")},
    }


# ================================================================ 12. column grid
def m12_grid(project, base, e):
    plot = project.get("plot") or {}
    b = _building(project, base)
    bx, by = float(e["grid_bay_x_m"]), float(e["grid_bay_y_m"])
    length = float(plot.get("length") or 0)
    width = float(plot.get("width") or 0)
    if not length or not width:
        side = math.sqrt(b["plot_area"]) if b["plot_area"] else 0
        length = width = round(side, 2)
    bays_x = int(length // bx) if bx else 0
    bays_y = int(width // by) if by else 0
    columns = (bays_x + 1) * (bays_y + 1) if bays_x and bays_y else 0

    span = max(bx, by)
    depth_mm = math.ceil(span * 1000 / 12 / 25) * 25
    ratio = round(span * 1000 / depth_mm, 1)

    tallest = b["tallest"]
    tallest_tower = tallest and next((t for t in (project.get("towers") or [])
                                      if t.get("id") == tallest["id"]), None)
    rooms = layoutlib.reference_rooms(tallest_tower) if tallest_tower else []
    corridor = tallest["corridor_width"] if tallest else 0
    corridor_aligned = abs((corridor % bx) if bx else 0) < 0.3 or abs(bx - (corridor % bx if bx else 0)) < 0.3

    clashes = []
    if rooms:
        max_x = max(float(r["x"]) + float(r["w"]) for r in rooms)
        max_y = max(float(r["y"]) + float(r["h"]) for r in rooms)
        gx = [i * bx for i in range(int(max_x // bx) + 1)]
        gy = [j * by for j in range(int(max_y // by) + 1)]
        for x in gx:
            for y in gy:
                for r in rooms:
                    rx, ry, rw, rh = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
                    if rx + 0.4 < x < rx + rw - 0.4 and ry + 0.4 < y < ry + rh - 0.4:
                        clashes.append({"x": round(x, 2), "y": round(y, 2), "room": r["name"], "type": r["type"]})
                        break

    options = []
    for ox, oy in [(5.0, 5.0), (6.0, 4.0), (6.0, 6.0), (7.5, 5.0), (4.5, 4.5)]:
        nx = int(length // ox) if ox else 0
        ny = int(width // oy) if oy else 0
        used = nx * ox * ny * oy
        options.append({"bay": f"{ox} × {oy} m", "bays": f"{nx} × {ny}",
                        "columns": (nx + 1) * (ny + 1) if nx and ny else 0,
                        "plot_utilisation_pct": round(used / (length * width) * 100, 1) if length and width else 0,
                        "beam_depth_mm": math.ceil(max(ox, oy) * 1000 / 12 / 25) * 25,
                        "selected": abs(ox - bx) < 0.01 and abs(oy - by) < 0.01})
    best = max(options, key=lambda o: (o["plot_utilisation_pct"], -o["columns"]))

    # Beam runs over this grid: drawable lines with spans, sections and continuity.
    # End bays are sized simply supported and interior bays continuous, which is how they
    # behave -- sizing a whole run as continuous under-sizes the ends.
    layout = takeofflib.beam_layout(bx, by, bays_x, bays_y,
                                    float(e.get("slab_thickness_mm") or 125.0))

    return {
        "id": "grid", "title": "Column Grid Optimizer", "codes": ["IS 456:2000", "IS 3861:2002"],
        "missing": [] if length else ["Plot length / width or polygon (Plot & Site)"],
        "options": options, "clashes": clashes, "beam_layout": layout,
        "outputs": [
            out("Plot envelope used", f"{length} × {width}", "m", "grid"),
            out("Selected grid", f"{bx} × {by}", "m", "grid"),
            out("Bays", f"{bays_x} × {bays_y}", "", "grid"),
            out("Column count", columns, "nos", "grid"),
            out("Beam depth for {} m span".format(span), depth_mm, "mm", "beam_depth", "L/12 preliminary"),
            out("Span / depth ratio", ratio, "", "beam_depth", "keep ≥ 12 for preliminary sizing"),
            out("Corridor alignment with grid", "aligned" if corridor_aligned else "not aligned", "", "grid",
                f"corridor {corridor} m vs {bx} m bay"),
            out("Columns inside usable room space", len(clashes), "nos", "grid",
                "shift the grid or absorb these columns into walls" if clashes else "no clashes detected"),
            out("Beams in the frame", layout["summary"].get("beam_count", 0), "nos", "beam_depth",
                "{} continuous over interior supports, {} simply supported at the ends".format(
                    layout["summary"].get("continuous", 0),
                    layout["summary"].get("simply_supported", 0))),
            out("Distinct beam sections", layout["summary"].get("distinct_sections", 0), "nos", "beam_depth",
                "fewer sections means fewer formwork sets and less rework on site"),
            out("Deepest beam", layout["summary"].get("deepest_mm", 0), "mm", "beam_depth",
                "governs the floor-to-floor height along with the slab and services"),
        ],
        "recommendation": {"label": "Most efficient grid", "value": f"{best['bay']} — {best['plot_utilisation_pct']}% "
                                                                   f"plot utilisation, {best['columns']} columns",
                           "clause": C.clause("grid")},
    }


# ================================================================ 13. embodied carbon
def m13_carbon(project, base, e, mix):
    """Cradle-to-gate embodied carbon, multiplied through the bill the estimator sees.

    The one thing to get right here is what NOT to count. The take-off derives cement,
    sand and aggregate from the concrete volume, so those lines are the concrete's own
    constituents. Concrete therefore carries batching and placing only -- see the note on
    C.EMBODIED_CARBON. Counting a ready-mix coefficient as well would double the clinker,
    which is most of the answer, and the total would be roughly twice the truth.
    """
    area = float(base["areas"]["builtup_area_sqm"] or 0)
    items = base["quantities"]["items"]

    rows = []
    total_kg = 0.0
    for it in items:
        coeff = C.EMBODIED_CARBON.get(it["key"])
        if not coeff:
            continue                       # doors, windows, fixtures: no defensible figure
        kg = float(it["quantity"] or 0) * coeff["factor"]
        total_kg += kg
        rows.append({
            "key": it["key"], "label": it["label"],
            "quantity": it["quantity"], "unit": it["unit"],
            "factor": coeff["factor"], "factor_unit": f'kgCO2e/{coeff["unit"]}',
            "basis": coeff["note"], "source": it.get("source", "ratio"),
            "kg_raw": kg,
            "tco2e": round(kg / 1000.0, 2),
        })
    rows.sort(key=lambda r: r["kg_raw"], reverse=True)
    for r in rows:
        r["share_pct"] = round(r["kg_raw"] / total_kg * 100, 1) if total_kg else 0.0

    per_sqm_raw = total_kg / area if area else 0.0
    per_sqm = round(per_sqm_raw, 1)
    band = "low"
    for threshold, name in C.CARBON_BENCHMARKS:
        if per_sqm_raw >= threshold:
            band = name
    priced = {r["key"] for r in rows}
    unpriced = [it["label"] for it in items if it["key"] not in priced]

    # Cement is the lever. Blended cement (PPC/PSC) cuts clinker roughly 30%, and the
    # mix design module already knows which cement this project specified.
    cement_row = next((r for r in rows if r["key"] == "cement"), None)
    cement_share = cement_row["share_pct"] if cement_row else 0.0
    blended_saving_t = round((cement_row["kg_raw"] / 1000.0 * 0.30), 1) if cement_row else 0.0
    trees_equiv = int(total_kg / C.TREE_SEQUESTRATION_KG_YR) if total_kg else 0

    warnings = []
    if any(r["source"] == "ratio" for r in rows if r["key"] in ("concrete", "cement")):
        warnings.append({"severity": "info", "text":
                         "Structural quantities came from per-m2 ratios rather than the "
                         "take-off, so the carbon figure is only as good as those ratios."})

    return {
        "id": "carbon", "title": "Embodied Carbon", "codes": ["IS 456:2000", "GRIHA v2019"],
        "missing": [] if area else ["Built-up area (Plot & Site, Apartment Planning)"],
        "warnings": warnings, "materials": rows, "unpriced": unpriced,
        "derived": {"total_tco2e": round(total_kg / 1000.0, 2), "per_sqm_kg": per_sqm,
                    "band": band, "cement_share_pct": cement_share,
                    "blended_cement_saving_tco2e": blended_saving_t},
        "outputs": [
            out("Embodied carbon", round(total_kg / 1000.0, 1), "tCO2e", "griha",
                "cradle to gate: making and delivering the materials, before any energy is used in the building"),
            out("Per square metre", per_sqm, "kgCO2e/m2", "griha",
                f"{band} for Indian residential RCC construction, where 300-450 is the usual range"),
            out("Largest contributor", rows[0]["label"] if rows else "-", "", "griha",
                f'{rows[0]["share_pct"]}% of the total' if rows else ""),
            out("Cement share", cement_share, "%", "griha",
                "clinker is the single biggest lever on this number"),
            out("Saving from blended cement", blended_saving_t, "tCO2e", "griha",
                "switching OPC to PPC or PSC cuts roughly 30% of cement carbon at equal strength"),
            out("Equivalent mature trees, one year", trees_equiv, "nos", "griha",
                "for scale only -- planting does not offset construction carbon in any real timeframe"),
        ],
        "recommendation": {
            "label": "Embodied carbon",
            "value": f'{round(total_kg / 1000.0, 1)} tCO2e, {per_sqm} kgCO2e/m2 ({band})',
            "clause": C.clause("griha")},
    }


# ================================================================ 14. plantation
def m14_trees(project, base, e, carbon):
    """How many trees the open space owes, what they should be, and where they go.

    Two numbers govern, and they disagree more often than not. The bye-law count (one
    tree per 80 m2 of open space) is what the sanction is checked against; the canopy
    target is what actually makes the site liveable. Planting to the count alone gives a
    site full of trees whose crowns never close, so both are reported and the binding
    one is named.
    """
    areas = base["areas"]
    open_sqm = float(areas.get("open_space_sqm") or 0)
    plot_sqm = float(areas.get("plot_area_sqm") or 0)
    norms = C.TREE_NORMS

    required = math.ceil(open_sqm / norms["sqm_open_space_per_tree"]) if open_sqm else 0
    canopy_target_sqm = open_sqm * norms["canopy_cover_target_pct"] / 100.0

    # Zones come from the generated site layout when there is one; otherwise the split is
    # proportional and the payload says so.
    layout = (project.get("site_layout") or {})
    green_sqm = float(((layout.get("green") or {}).get("area_sqm")) or 0)
    road_sqm = float(((layout.get("roads") or {}).get("total_area_sqm")) or 0)
    have_layout = green_sqm > 0 or road_sqm > 0
    if not have_layout:
        green_sqm = open_sqm * 0.55
        road_sqm = open_sqm * 0.25
    buffer_sqm = max(open_sqm - green_sqm - road_sqm, 0.0)

    zones = [
        {"zone": "Landscape and lawns", "area_sqm": round(green_sqm, 1), "area_sqm_raw": green_sqm, "kind": "open",
         "note": "large-canopy shade trees, spaced to close at maturity"},
        {"zone": "Road and driveway verges", "area_sqm": round(road_sqm, 1), "area_sqm_raw": road_sqm, "kind": "avenue",
         "note": "avenue planting at {} m centres, compact-rooted species only".format(
             norms["avenue_spacing_m"])},
        {"zone": "Boundary and setback strip",
         "area_sqm": round(buffer_sqm, 1), "area_sqm_raw": buffer_sqm, "kind": "buffer",
         "note": "narrow-crown screening against neighbours and noise"},
    ]

    allocatable = math.fsum(z["area_sqm_raw"] for z in zones) or 1.0
    plan = []
    canopy_sqm = 0.0
    for z in zones:
        count = int(round(required * (z["area_sqm_raw"] / allocatable)))
        picks = ([s for s in C.TREE_SPECIES if s["zone"] == z["kind"]]
                 or [s for s in C.TREE_SPECIES if s["zone"] == "open"])
        per = [count // len(picks)] * len(picks)
        for i in range(count - sum(per)):
            per[i] += 1
        rows = []
        for s, n in zip(picks, per):
            if n <= 0:
                continue
            crown = math.pi * (s["canopy_m"] / 2) ** 2 * n
            canopy_sqm += crown
            rows.append({"species": s["name"], "count": n, "native": s["native"],
                         "canopy_m": s["canopy_m"], "canopy_sqm": round(crown, 1),
                         "roots": s["roots"], "note": s["note"]})
        plan.append({**z, "trees": count, "species": rows})

    planted = sum(z["trees"] for z in plan)
    native = sum(r["count"] for z in plan for r in z["species"] if r["native"])
    native_pct = round(native / planted * 100, 1) if planted else 0.0
    canopy_pct = round(canopy_sqm / open_sqm * 100, 1) if open_sqm else 0.0
    avg_crown = (canopy_sqm / planted) if planted else 0.0
    for_canopy = math.ceil(canopy_target_sqm / avg_crown) if avg_crown else 0
    binding = "canopy cover" if for_canopy > required else "bye-law count"

    seq = planted * C.TREE_SEQUESTRATION_KG_YR / 1000.0
    embodied = float((carbon or {}).get("derived", {}).get("total_tco2e") or 0)
    offset_years = int(embodied / seq) if seq else 0

    warnings = []
    if not have_layout:
        warnings.append({"severity": "info", "text":
                         "No site layout has been generated, so the zone split is "
                         "proportional rather than measured. Generate the site layout to "
                         "get real planting areas."})
    if native_pct < norms["min_native_share_pct"]:
        warnings.append({"severity": "info", "text":
                         "Native stock is {}% of the planting, below the {}% that survives "
                         "here without permanent irrigation support.".format(
                             native_pct, norms["min_native_share_pct"])})

    offset_note = ""
    if offset_years:
        offset_note = ("roughly {} years of growth to absorb the {} tCO2e already embodied "
                       "in the materials -- worth doing, but not an offset").format(
                           offset_years, embodied)

    return {
        "id": "trees", "title": "Plantation Plan",
        "codes": ["Municipal building bye-laws", "National Forest Policy 1988"],
        "missing": [] if open_sqm else ["Plot polygon and tower footprints"],
        "warnings": warnings, "zones": plan, "species_library": C.TREE_SPECIES,
        "derived": {"required": required, "for_canopy": for_canopy, "planted": planted,
                    "canopy_pct": canopy_pct, "native_pct": native_pct,
                    "binding": binding, "sequestration_tco2e_yr": round(seq, 1)},
        "outputs": [
            out("Open space to plant", round(open_sqm, 1), "m2", None,
                "{}% of the plot".format(round(open_sqm / plot_sqm * 100, 1)) if plot_sqm else ""),
            out("Trees required by bye-law", required, "nos", None,
                "one per {} m2 of open space".format(norms["sqm_open_space_per_tree"])),
            out("Trees needed for canopy target", for_canopy, "nos", None,
                "to shade {}% of the open space once grown".format(norms["canopy_cover_target_pct"])),
            out("Binding requirement", binding, "", None,
                "plant to the larger of the two: the count satisfies the sanction, the "
                "canopy is what makes the site liveable"),
            out("Canopy once grown", canopy_pct, "% of open space", None,
                "crowns assumed to close without overlapping"),
            out("Native species share", native_pct, "%", None,
                "target {}%".format(norms["min_native_share_pct"])),
            out("Carbon absorbed", round(seq, 1), "tCO2e/yr", None, offset_note),
        ],
        "recommendation": {
            "label": "Plantation",
            "value": "{} trees, {}% native, {}% canopy once grown".format(
                max(required, for_canopy), native_pct, canopy_pct),
            "clause": None},
    }


# ================================================================ orchestrator
def analyse_engineering(project, base):
    e = cfg(project)
    city = C.city_reference(e.get("city"), e.get("state"))
    loads = m1_structural_loads(project, base, e, city)
    seismic = m2_seismic(project, base, e, city, loads)
    foundation = m3_foundation(project, base, e, loads)
    mix = m4_mix_design(project, base, e)
    water = m5_water(project, base, e)
    storm = m6_storm_rwh(project, base, e, city)
    parking = m7_parking_nbc(project, base, e)
    fire = m8_fire(project, base, e)
    access = m9_accessibility(project, base, e)
    green = m11_green(project, base, e, water, storm)
    grid = m12_grid(project, base, e)
    carbon = m13_carbon(project, base, e, mix)
    trees = m14_trees(project, base, e, carbon)

    modules = {m["id"]: m for m in [loads, seismic, foundation, mix, water, storm,
                                    parking, fire, access, green, grid, carbon, trees]}
    per_tower = {}
    for tl in loads.get("per_tower", []):
        per_tower[tl["id"]] = {"name": tl["name"], "loads": tl,
                              "seismic": next((s for s in seismic.get("per_tower", []) if s["id"] == tl["id"]), None)}
    missing = sorted({m for mod in modules.values() for m in mod.get("missing", [])})
    warnings = []
    for mod in modules.values():
        for w in mod.get("warnings", []):
            warnings.append({**w, "module": mod["title"]})

    return {
        "config": e,
        # Every result carries the document it was checked against. A compliance figure
        # without one is unreadable the moment more than one version exists, and from
        # 30 April 2026 more than one does.
        "code_version": C.CODE_VERSIONS[e["code_version"]],
        "city_reference": city,
        "modules": modules,
        "per_tower": per_tower,
        "missing_inputs": missing,
        "warnings": warnings,
        "summary": {
            "seismic_zone": seismic["zone"],
            "base_shear_kn": seismic["derived"]["base_shear"],
            "column_size": next(o["value"] for o in loads["outputs"] if o["label"] == "Recommended column size"),
            "foundation": foundation["recommendation"]["value"],
            "mix_ratio": mix["recommendation"]["value"],
            "water_demand_lpd": water["derived"]["total_lpd"],
            "stp_kld": water["derived"]["stp_kld"],
            "rwh_annual_l": storm["derived"]["rwh_annual_l"],
            "fire_score": fire["score"],
            "parking_score": parking["score"],
            "accessibility_score": access["score"],
            "green_rating": green["recommendation"]["value"],
            "embodied_carbon_tco2e": carbon["derived"]["total_tco2e"],
            "carbon_per_sqm_kg": carbon["derived"]["per_sqm_kg"],
            "trees_required": max(trees["derived"]["required"],
                                  trees["derived"]["for_canopy"]),
        },
    }
