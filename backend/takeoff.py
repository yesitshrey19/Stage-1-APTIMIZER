"""Quantity take-off from the structure the app has already designed.

The BOQ used to come from flat thumb rules -- 0.40 m3 of concrete and 45 kg of steel per
m2 of built-up area, applied whatever the building was. Those numbers do not know how many
floors the tower has, how far apart the columns are, how thick the slab is, or which
seismic zone the site sits in, even though the app computes all four. A 4-storey block on a
6 m grid and a 24-storey tower on a 4 m grid got the same rate per m2, which is the single
least defensible number in the estimate.

This module derives the quantities instead, the way a quantity surveyor would: count the
columns the grid implies, take their designed section, multiply by height; run the beams
along the grid lines; take the slab at its designed thickness; size the footings from the
service load and the soil. Steel follows each member rather than the floor area -- and for
columns it uses the percentage the engineer actually specified, so it is arithmetic, not a
rule of thumb. Cement, sand and aggregate come from the IS 10262 mix proportions for the
grade chosen, not from a per-m2 constant.

The old ratios remain available and the payload reports both, because a take-off that
silently disagrees with every estimator's intuition is worse than one you can check.

SINGLE SOURCE OF TRUTH
----------------------
`column_section`, `beam_section` and `mix_proportions` live here and are imported by
`engineering` as well. They were previously written out twice, which is how the app ended
up sizing a column for M25 after the user had picked M40 in the mix module.
"""
import math
from typing import Any, Dict, List, Optional

import iscodes as C

STEEL_DENSITY = 7850.0          # kg/m3

# Reinforcement by member, kg per m3 of concrete. Columns are NOT in this table: their
# steel comes from the percentage the engineer specified, which is an exact figure.
STEEL_KG_PER_M3 = {"slab": 80.0, "beam": 150.0, "footing": 70.0, "stair": 110.0}

# IS 13920 ductile detailing adds confinement steel in the plastic-hinge regions. It is a
# real quantity, not a contingency, and it lands on columns and beams only.
DUCTILE_STEEL_FACTOR = 1.15
DUCTILE_ZONES = ("III", "IV", "V")
LAP_ALLOWANCE = 1.05   # laps, chairs and spacers on measured bar weight

# Cores, lift walls, staircases and landings are not on the column grid and are not worth
# modelling individually at estimate stage; they are a well-known share of frame concrete.
CORE_CONCRETE_SHARE = 0.09
FOOTING_DEPTH_MIN_M = 0.45
PCC_THICKNESS_M = 0.10


# ---------------------------------------------------------------- member sizing
def column_section(req_area_mm2: float) -> tuple:
    """(breadth, depth) in mm for a required gross area, rounded to 25 mm sizes."""
    side = max(math.ceil(math.sqrt(max(req_area_mm2, 1.0)) / 25.0) * 25.0, 230.0)
    b = max(230.0 if side <= 300 else round(side * 0.65 / 25) * 25, 230.0)
    d = max(round(req_area_mm2 / b / 25) * 25, 300.0)
    return b, d


TIE_DIA_MM = 8.0
TIE_MAIN_BAR_MM = 16.0
TIE_COVER_M = 0.04


def column_ties_kg(b_m: float, d_m: float, clear_h_m: float) -> float:
    """Lateral ties in one column for one storey (IS 456 Cl. 26.5.3.2).

    8 mm ties at the least of the column's smaller side, 16 x main bar and 300 mm; each
    tie runs the perimeter inside the cover plus two 10-diameter hooks. Ties are required
    in every column whatever the zone, and leaving them out understated column steel.
    """
    spacing = min(min(b_m, d_m), 16 * TIE_MAIN_BAR_MM / 1000.0, 0.300)
    if spacing <= 0 or clear_h_m <= 0:
        return 0.0
    count = math.floor(clear_h_m / spacing) + 1
    length = 2 * ((b_m - 2 * TIE_COVER_M) + (d_m - 2 * TIE_COVER_M)) + 2 * 10 * TIE_DIA_MM / 1000.0
    return count * max(length, 0.0) * TIE_DIA_MM ** 2 / 162.0


def column_required_area(load_kn: float, fck: float, fy: float, steel_pct: float) -> float:
    """IS 456 Cl. 39.3 short-column axial capacity, inverted for area."""
    p = max(steel_pct, 0.8) / 100.0
    cap = 0.4 * fck + (0.67 * fy - 0.4 * fck) * p
    return load_kn * 1000.0 / cap if cap > 0 else 0.0


def beam_section(span_m: float, support: str = "simply supported") -> tuple:
    """(width, depth) in mm from the span/depth ratio for the support condition."""
    norm_support = str(support or "simply supported").replace("_", " ").strip().lower()
    divisor = 12.0 if norm_support == "simply supported" else 15.0
    d = max(math.ceil(span_m * 1000 / divisor / 25) * 25, 300)
    w = max(round(d / 2 / 25) * 25, 230)
    return float(w), float(d)


def beam_layout(bay_x: float, bay_y: float, bays_x: int, bays_y: int,
                slab_thickness_mm: float = 125.0) -> Dict[str, Any]:
    """Beam runs over the column grid: every line, its span, section and continuity.

    Returns drawable geometry -- each beam is a line in plot-local metres between two grid
    intersections -- rather than a table of sizes, because a size without a position is not
    a layout and cannot be checked against anything.

    Continuity is the part that matters for the sections. A beam continuous over interior
    supports carries the same load in a shallower section than a simply supported one
    (IS 456 Cl. 23.2.1 allows span/depth 26 against 20 for the basic ratio; the preliminary
    divisors here are 15 and 12). So the END bays of every run are sized as simply
    supported and the INTERIOR bays as continuous, which is how they actually behave --
    sizing the whole run off the longest span, or all of it as continuous, is the common
    error and it under-sizes the ends.
    """
    beams: List[Dict[str, Any]] = []
    if bays_x < 1 or bays_y < 1:
        return {"beams": [], "runs": [], "summary": {}}

    def add(run_id, idx, x1, y1, x2, y2, span, interior, direction):
        support = "continuous" if interior else "simply supported"
        w, d = beam_section(span, support)
        beams.append({
            "id": f"{run_id}-{idx}", "run": run_id, "direction": direction,
            "x1": round(x1, 2), "y1": round(y1, 2), "x2": round(x2, 2), "y2": round(y2, 2),
            "span_m": round(span, 2), "support": support,
            "width_mm": w, "depth_mm": d,
            "span_depth_ratio": round(span * 1000 / d, 1),
            # Effective flange width is where the slab acts with the beam (IS 456 Cl. 23.1.2).
            "flange_mm": round(min(w + 12 * slab_thickness_mm, span * 1000 / 6 + w), 0),
        })

    # Runs along X: one per grid line in Y.
    for j in range(bays_y + 1):
        y = j * bay_y
        run_id = f"BX{j + 1}"
        for i in range(bays_x):
            add(run_id, i + 1, i * bay_x, y, (i + 1) * bay_x, y, bay_x,
                interior=(bays_x > 1 and 0 < i < bays_x - 1), direction="x")

    # Runs along Y: one per grid line in X.
    for i in range(bays_x + 1):
        x = i * bay_x
        run_id = f"BY{i + 1}"
        for j in range(bays_y):
            add(run_id, j + 1, x, j * bay_y, x, (j + 1) * bay_y, bay_y,
                interior=(bays_y > 1 and 0 < j < bays_y - 1), direction="y")

    runs: Dict[str, Dict[str, Any]] = {}
    for b in beams:
        r = runs.setdefault(b["run"], {"run": b["run"], "direction": b["direction"],
                                       "spans": 0, "length_m": 0.0, "sections": set()})
        r["spans"] += 1
        r["length_m"] += b["span_m"]
        r["sections"].add(f'{int(b["width_mm"])}x{int(b["depth_mm"])}')
    run_list = [{**r, "length_m": round(r["length_m"], 2),
                 "sections": sorted(r["sections"])} for r in runs.values()]

    sections: Dict[str, int] = {}
    for b in beams:
        key = f'{int(b["width_mm"])} x {int(b["depth_mm"])}'
        sections[key] = sections.get(key, 0) + 1
    deepest = max(beams, key=lambda b: b["depth_mm"]) if beams else None

    return {
        "beams": beams,
        "runs": sorted(run_list, key=lambda r: r["run"]),
        "schedule": [{"section_mm": k, "count": v} for k, v in
                     sorted(sections.items(), key=lambda kv: -kv[1])],
        "summary": {
            "beam_count": len(beams),
            "total_length_m": round(sum(b["span_m"] for b in beams), 1),
            "distinct_sections": len(sections),
            "deepest_mm": deepest["depth_mm"] if deepest else 0,
            "governing_span_m": deepest["span_m"] if deepest else 0,
            "continuous": sum(1 for b in beams if b["support"] == "continuous"),
            "simply_supported": sum(1 for b in beams if b["support"] == "simply supported"),
        },
    }


def mix_proportions(grade: int, exposure_key: str, agg_mm: int) -> Dict[str, float]:
    """IS 10262:2019 proportioning -- kg of cement, sand and aggregate per m3."""
    exposure = C.EXPOSURE.get(exposure_key, C.EXPOSURE["moderate"])
    wc = float(exposure["max_wc"])
    water = float(C.MIX_WATER.get(agg_mm, 186))
    cement_raw = max(water / wc, float(exposure["min_cement"]))
    cement = max(round(water / wc, 1), exposure["min_cement"])
    if cement_raw > water / wc:
        wc_raw = water / cement_raw
        wc = round(wc_raw, 3)
    else:
        wc_raw = wc
    ca_vol_raw = C.MIX_CA_VOLUME.get(agg_mm, 0.62) + 0.01 * ((0.50 - wc_raw) / 0.05)
    fa_vol_raw = 1.0 - ca_vol_raw
    ca_vol = round(ca_vol_raw, 3)
    fa_vol = round(1 - ca_vol, 3)
    air = 0.02 if agg_mm == 10 else 0.01
    vol_agg_raw = 1.0 - air - (cement_raw / (C.SG["cement"] * 1000.0)) - (water / 1000.0)
    vol_agg = 1 - air - (cement / (C.SG["cement"] * 1000)) - (water / 1000.0)
    coarse_raw = vol_agg_raw * ca_vol_raw * C.SG["coarse"] * 1000.0
    fine_raw = vol_agg_raw * fa_vol_raw * C.SG["fine"] * 1000.0
    return {
        "ca_volume_fraction": ca_vol,
        "fa_volume_fraction": fa_vol,
        "cement_kg": cement,
        "coarse_kg": round(vol_agg * ca_vol * C.SG["coarse"] * 1000, 1),
        "fine_kg": round(vol_agg * fa_vol * C.SG["fine"] * 1000, 1),
        "cement_kg_raw": cement_raw,
        "coarse_kg_raw": coarse_raw,
        "fine_kg_raw": fine_raw,
        "water_l": float(water),
        "wc_ratio": wc,
        "target_strength": round(grade + 1.65 * C.MIX_STD_DEV.get(grade, 5.0), 2),
    }


# ---------------------------------------------------------------- grid geometry
def grid_counts(footprint_sqm: float, bay_x_m: float, bay_y_m: float) -> Dict[str, float]:
    """Columns and beam runs implied by a grid over a footprint.

    The plan outline is unknown at this stage, so it is taken as the square of equal area.
    That is the neutral assumption: any elongated plan of the same area needs slightly more
    beam length and slightly fewer columns, and the two errors work against each other.
    """
    a = max(float(footprint_sqm), 0.0)
    bx = max(float(bay_x_m), 1.0)
    by = max(float(bay_y_m), 1.0)
    if a <= 0:
        return {"lx": 0.0, "ly": 0.0, "nx": 0, "ny": 0, "columns": 0, "beam_length_m": 0.0}
    side = math.sqrt(a)
    nx = int(math.floor(side / bx)) + 1          # grid lines across
    ny = int(math.floor(side / by)) + 1
    return {
        "lx": side, "ly": side, "nx": nx, "ny": ny,
        "columns": nx * ny,
        # Beams run along every grid line in both directions.
        "beam_length_m": ny * side + nx * side,
    }


# ---------------------------------------------------------------- take-off
def structural_takeoff(project: Dict[str, Any], areas: Dict[str, Any]) -> Dict[str, Any]:
    """Concrete, steel, formwork and mix materials, derived per tower from the design.

    Accepts either the `areas` block or a whole analysis dict, because both call sites
    exist and passing the wrong one is otherwise a silent empty take-off.
    """
    if "towers" not in areas and isinstance(areas.get("areas"), dict):
        areas = areas["areas"]
    e = {**{"grid_bay_x_m": 5.0, "grid_bay_y_m": 5.0, "slab_thickness_mm": 125,
            "beam_span_m": 5.0, "beam_support": "simply supported", "concrete_grade": 25,
            "steel_grade": 415, "column_steel_pct": 1.0, "exposure_condition": "moderate",
            "aggregate_size_mm": 20, "finishes_load_kn_sqm": 1.5,
            "wall_thickness_mm": 230, "wall_material": "brick_masonry"},
          **(project.get("engineering") or {})}

    bx, by = float(e["grid_bay_x_m"]), float(e["grid_bay_y_m"])
    slab_t = float(e["slab_thickness_mm"]) / 1000.0
    fck, fy = float(e["concrete_grade"]), float(e["steel_grade"])
    col_pct = max(float(e["column_steel_pct"]), 0.8)
    beam_w_mm, beam_d_mm = beam_section(float(e["beam_span_m"]), e["beam_support"])
    beam_w, beam_d = beam_w_mm / 1000.0, beam_d_mm / 1000.0
    trib = bx * by

    city = C.city_reference(e.get("city"), e.get("state"))
    zone = str(city.get("zone") or "II")
    ductile = zone in DUCTILE_ZONES

    # Fall back to the engineering module's own default rather than the first entry in the
    # table -- that is "hard rock" at 3240 kN/m2, which would silently size footings for
    # the best ground in India on a project that never set a soil type.
    soil = C.SOILS.get(e.get("soil_type")) or C.SOILS["dense sand"]
    sbc = float(soil.get("sbc") or 150.0)

    # Service load per m2 of floor -- the same dead + live basis the loads module uses:
    # slab, finishes, masonry walls and live load. Walls are worked out per tower below
    # because their height follows the tower's floor height.
    slab_self = C.UNIT_WEIGHTS["rcc"] * slab_t
    finishes = float(e["finishes_load_kn_sqm"])
    live = C.LIVE_LOADS["residential_room"]

    rows: List[Dict[str, Any]] = []
    tot = {"concrete_m3": 0.0, "steel_kg": 0.0, "formwork_sqm": 0.0}

    for t in (areas.get("towers") or []):
        foot = float(t.get("footprint_sqm") or 0)
        floors = max(int(t.get("floors") or 0), 0)
        fh = float(t.get("floor_height") or 3.0)
        if foot <= 0 or floors <= 0:
            continue
        g = grid_counts(foot, bx, by)
        n_col = g["columns"]
        walls = C.wall_load_kn_sqm(e["wall_material"], e["wall_thickness_mm"], fh, slab_t)
        service_per_sqm = slab_self + finishes + walls + live
        factored_per_sqm = 1.5 * service_per_sqm

        # Column at the base carries every floor above it; sizing on that governs.
        col_load = factored_per_sqm * trib * floors
        cb_mm, cd_mm = column_section(column_required_area(col_load, fck, fy, col_pct))
        cb, cd = cb_mm / 1000.0, cd_mm / 1000.0
        clear_h = max(fh - beam_d, 0.5)

        col_c = cb * cd * clear_h * n_col * floors
        beam_c = g["beam_length_m"] * beam_w * max(beam_d - slab_t, 0.05) * floors
        slab_c = foot * slab_t * floors
        core_c = math.fsum((col_c, beam_c, slab_c)) * CORE_CONCRETE_SHARE

        # Footings: service load on one column, spread at the soil's safe bearing capacity.
        col_service = service_per_sqm * trib * floors
        f_area = col_service / sbc if sbc else 0.0
        f_side = math.sqrt(f_area) + 0.05 if f_area > 0 else 0.0
        f_depth = max(f_side / 4.0, FOOTING_DEPTH_MIN_M)
        found_c = f_side * f_side * f_depth * n_col
        pcc_c = (f_side + 0.2) ** 2 * PCC_THICKNESS_M * n_col

        col_long = col_c * (col_pct / 100.0) * STEEL_DENSITY
        col_ties = column_ties_kg(cb, cd, clear_h) * n_col * floors
        col_steel = col_long + col_ties
        # Beam bars run the full beam depth, through the slab zone the concrete line leaves
        # out (that concrete is counted in the slab), so the steel rate applies to the full
        # section, not just the web below the slab.
        beam_full_c = g["beam_length_m"] * beam_w * beam_d * floors
        beam_steel = beam_full_c * STEEL_KG_PER_M3["beam"]
        slab_steel = slab_c * STEEL_KG_PER_M3["slab"]
        core_steel = core_c * STEEL_KG_PER_M3["stair"]
        found_steel = found_c * STEEL_KG_PER_M3["footing"]
        if ductile:
            col_steel *= DUCTILE_STEEL_FACTOR
            beam_steel *= DUCTILE_STEEL_FACTOR
        # Bars come in 12 m lengths; a bar-bending schedule measures the laps (IS 456
        # Cl. 26.2.5), chairs and spacers as well as the bar the drawing shows.
        col_steel, beam_steel, slab_steel, core_steel, found_steel = (
            x * LAP_ALLOWANCE for x in (col_steel, beam_steel, slab_steel, core_steel, found_steel))

        fw_slab = foot * floors
        fw_beam = g["beam_length_m"] * (2 * max(beam_d - slab_t, 0.05) + beam_w) * floors
        fw_col = 2 * (cb + cd) * clear_h * n_col * floors

        concrete = math.fsum((col_c, beam_c, slab_c, core_c, found_c, pcc_c))
        steel = math.fsum((col_steel, beam_steel, slab_steel, core_steel, found_steel))
        formwork = math.fsum((fw_slab, fw_beam, fw_col))

        c_parts = {
            "columns": round(col_c, 2),
            "beams": round(beam_c, 2),
            "slabs": round(slab_c, 2),
            "cores_and_stairs": round(core_c, 2),
            "footings": round(found_c, 2),
            "pcc": round(pcc_c, 2),
        }
        concrete_sum = round(math.fsum(c_parts.values()), 2)
        c_parts["total_m3"] = concrete_sum

        rows.append({
            "id": t.get("id"), "name": t.get("name"), "floors": floors,
            "footprint_sqm": round(foot, 2),
            "grid": f"{bx:g} m x {by:g} m", "columns_per_floor": n_col,
            "column_section_mm": f"{int(cb_mm)} x {int(cd_mm)}",
            "beam_section_mm": f"{int(beam_w_mm)} x {int(beam_d_mm)}",
            "slab_thickness_mm": round(slab_t * 1000),
            "footing_size_m": round(f_side, 2), "sbc_kn_sqm": sbc,
            "service_load_kn_sqm": round(service_per_sqm, 2),
            # How the column steel figure is built, so it can be checked by hand.
            "column_steel_detail": {"longitudinal_kg": round(col_long, 1), "ties_kg": round(col_ties, 1),
                                    "ductile_factor": DUCTILE_STEEL_FACTOR if ductile else 1.0,
                                    "lap_allowance": LAP_ALLOWANCE},
            "concrete": c_parts,
            "steel": {"columns": round(col_steel), "beams": round(beam_steel),
                      "slabs": round(slab_steel), "cores_and_stairs": round(core_steel),
                      "footings": round(found_steel), "total_kg": round(steel)},
            "formwork_sqm": round(formwork, 1),
            "ductile_detailing": ductile,
        })
        tot["concrete_m3"] += concrete_sum
        tot["steel_kg"] += steel
        tot["formwork_sqm"] += formwork

    mix = mix_proportions(int(fck), e["exposure_condition"], int(e["aggregate_size_mm"]))
    vol = tot["concrete_m3"]
    builtup = float(areas.get("builtup_area_sqm") or 0)
    cement_kg_raw = mix.get("cement_kg_raw", mix["cement_kg"]) * vol
    fine_kg_raw = mix.get("fine_kg_raw", mix["fine_kg"]) * vol
    coarse_kg_raw = mix.get("coarse_kg_raw", mix["coarse_kg"]) * vol

    return {
        "ok": True,
        "basis": {
            "grid": f"{bx:g} m x {by:g} m", "slab_mm": round(slab_t * 1000),
            "concrete_grade": f"M{int(fck)}", "steel_grade": f"Fe{int(fy)}",
            "column_steel_pct": col_pct, "seismic_zone": zone,
            "ductile_detailing": ductile, "sbc_kn_sqm": sbc,
        },
        "towers": rows,
        "totals": {
            "concrete_m3": round(vol, 2),
            "steel_kg": round(tot["steel_kg"]),
            "formwork_sqm": round(tot["formwork_sqm"], 1),
            "cement_kg": round(cement_kg_raw),
            "cement_bags": round(cement_kg_raw / 50.0),
            "sand_kg": round(fine_kg_raw),
            "sand_m3": round(fine_kg_raw / 1600.0, 2),      # bulk density ~1600 kg/m3
            "aggregate_kg": round(coarse_kg_raw),
            "aggregate_m3": round(coarse_kg_raw / 1500.0, 2),
            "water_l": round(mix["water_l"] * vol),
        },
        "totals_raw": {
            "concrete_m3": vol,
            "steel_kg": tot["steel_kg"],
            "formwork_sqm": tot["formwork_sqm"],
            "cement_kg": cement_kg_raw,
            "cement_bags": cement_kg_raw / 50.0,
            "sand_kg": fine_kg_raw,
            "sand_m3": fine_kg_raw / 1600.0,
            "aggregate_kg": coarse_kg_raw,
            "aggregate_m3": coarse_kg_raw / 1500.0,
            "water_l": mix["water_l"] * vol,
        },
        "mix_per_cum": mix,
        "warnings": _sanity(vol, tot["steel_kg"], builtup),
        # What the discarded thumb rules would have said, so the two can be compared.
        "vs_thumb_rule": {
            "concrete_m3_per_sqm": round(vol / builtup, 3) if builtup else 0,
            "steel_kg_per_sqm": round(tot["steel_kg"] / builtup, 1) if builtup else 0,
            "thumb_concrete_m3_per_sqm": 0.40,
            "thumb_steel_kg_per_sqm": 45.0,
            "note": "Derived from the designed sections and the column grid. A wide grid or "
                    "a tall tower pushes these above the flat thumb rule; a low-rise block "
                    "on a tight grid falls below it.",
        },
    }


# Bands an Indian QS would sense-check a residential RCC take-off against. Falling outside
# them is not necessarily wrong -- a wide grid or a tall tower legitimately pushes past the
# top -- but it means an input is doing something unusual and is worth a look before the
# estimate is issued.
CONCRETE_BAND_M3_PER_SQM = (0.22, 0.48)
STEEL_BAND_KG_PER_M3 = (75.0, 135.0)
# The per-m3 band alone let a take-off at roughly half the usual steel per m2 through
# without a word, because thin members keep kg/m3 normal while the total is far too low.
# The band is for the FRAME this take-off measures (footings, columns, beams, slabs,
# cores). Whole-building rules of thumb (35-45 kg/m2) also carry lintels, chajjas, plinth
# beams, parapets and stair flights, which are not in it.
STEEL_BAND_KG_PER_SQM = (20.0, 75.0)


def _sanity(concrete_m3: float, steel_kg: float, builtup_sqm: float) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if builtup_sqm <= 0 or concrete_m3 <= 0:
        return out
    per_sqm = concrete_m3 / builtup_sqm
    lo, hi = CONCRETE_BAND_M3_PER_SQM
    if not (lo <= per_sqm <= hi):
        out.append({"severity": "warning", "metric": "concrete",
                    "text": f"Concrete works out at {per_sqm:.3f} m3 per m2 of built-up area, "
                            f"outside the {lo}-{hi} range typical of Indian residential RCC. "
                            "Check the slab thickness and the column grid."})
    ratio = steel_kg / concrete_m3
    slo, shi = STEEL_BAND_KG_PER_M3
    if not (slo <= ratio <= shi):
        out.append({"severity": "warning", "metric": "steel",
                    "text": f"Reinforcement works out at {ratio:.0f} kg per m3 of concrete, "
                            f"outside the usual {slo:.0f}-{shi:.0f} kg/m3. Check the column "
                            "steel percentage and the seismic zone."})
    kg_sqm = steel_kg / builtup_sqm
    klo, khi = STEEL_BAND_KG_PER_SQM
    if not (klo <= kg_sqm <= khi):
        out.append({"severity": "warning", "metric": "steel_per_sqm",
                    "text": f"Reinforcement works out at {kg_sqm:.1f} kg per m2 of built-up area, "
                            f"outside the usual {klo:.0f}-{khi:.0f} kg/m2 for an Indian residential RCC frame. "
                            "Check the column grid, the member sections and the steel percentages."})
    return out
