"""
Utility Network Planning & External Infrastructure Engine (Aptimizer V4)

Computes site-wide external utility engineering networks:
1. Gravity Sewer Trunk Network (CPHEEO peak factor, Manning-sized pipe, self-cleansing >= 0.6 m/s)
2. Stormwater Drainage Network (Rational method Q = C*I*A/360, Manning-sized box drains)
3. Water Supply & Fire Hydrant Ring Main (Hazen-Williams head loss, residual pressure >= 1.0 kg/cm2, IS 1172/NBC Part 4)
4. Electrical Power Distribution & Substation (kVA transformer sizing, DG backup, cable trenches, NBC Part 8)
"""

import math
from typing import Any, Dict, List, Optional
import engine
import iscodes

# Hydraulic constants. Manning n: uPVC / DWC sewer 0.010, smooth precast RCC drain 0.015.
SEWER_N, SEWER_SLOPE, SEWER_DESIGN_DEPTH = 0.010, 1.0 / 150.0, 0.8
SEWER_DIAMETERS_MM = [160, 200, 250, 315, 400, 500, 600]
SELF_CLEANSING_MPS = 0.6           # CPHEEO 2013, at present peak flow
DRAIN_N, DRAIN_SLOPE, DRAIN_FREEBOARD_MM = 0.015, 1.0 / 300.0, 150
DRAIN_SIZES_MM = [(300, 450), (450, 600), (600, 750), (750, 900), (900, 1050), (1200, 1350), (1500, 1650)]


def sewage_peak_factor(population: float) -> float:
    """CPHEEO Manual on Sewerage (2013) Table 3.5, by contributing population."""
    p = float(population or 0)
    return 3.0 if p <= 20_000 else 2.5 if p <= 50_000 else 2.25 if p <= 750_000 else 2.0


def _circle_section(d_m: float, depth_ratio: float):
    """(area m2, hydraulic radius m) of a circular pipe running at depth_ratio of D."""
    y = min(max(depth_ratio, 1e-6), 1.0)
    theta = 2.0 * math.acos(1.0 - 2.0 * y)
    area = d_m * d_m / 8.0 * (theta - math.sin(theta))
    wetted = d_m * theta / 2.0
    return area, (area / wetted if wetted else 0.0)


def pipe_flow_lps(d_m: float, depth_ratio: float, n: float, slope: float) -> float:
    area, r = _circle_section(d_m, depth_ratio)
    return area * (1.0 / n) * r ** (2.0 / 3.0) * slope ** 0.5 * 1000.0


def pipe_velocity_at_flow(d_m: float, q_lps: float, n: float, slope: float) -> float:
    """Velocity at the depth that carries q, by bisection on the depth ratio."""
    lo, hi = 1e-4, 0.94   # flow peaks near 0.94 D
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if pipe_flow_lps(d_m, mid, n, slope) < q_lps:
            lo = mid
        else:
            hi = mid
    _, r = _circle_section(d_m, hi)
    return (1.0 / n) * r ** (2.0 / 3.0) * slope ** 0.5


def box_flow_lps(w_m: float, flow_depth_m: float, n: float, slope: float) -> float:
    if w_m <= 0 or flow_depth_m <= 0:
        return 0.0
    area = w_m * flow_depth_m
    r = area / (w_m + 2.0 * flow_depth_m)
    return area * (1.0 / n) * r ** (2.0 / 3.0) * slope ** 0.5 * 1000.0


def plan_utility_network(project: Dict[str, Any], analysis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Derives comprehensive civil utility networks with hydraulic sizing and code compliance."""
    plot = project.get("plot") or {}
    towers = project.get("towers") or []
    num_towers = max(len(towers), 1)
    areas = (analysis or {}).get("areas") or engine.area_metrics(project)
    
    # Extract site and occupancy parameters
    plot_area_sqm = float(areas.get("plot_area_sqm") or plot.get("area_sqm") or 8000.0)
    plot_area_ha = plot_area_sqm / 10000.0
    
    # Persons / Occupancy
    utilities = (analysis or {}).get("utilities") or engine.utilities(project, areas)
    total_units = int(areas.get("total_units") or 0)
    if total_units <= 0:
        total_units = sum(int(t.get("units") or 40) if isinstance(t.get("units"), (int, float)) else 40 for t in towers) or 100
    persons = int(utilities.get("persons") or (total_units * 4.5))
    lpcd = float((project.get("utility_config") or {}).get("lpcd") or 135.0)
    
    # -------------------------------------------------------------------------
    # 1. GRAVITY SEWER TRUNK NETWORK (IS 1742 / CPHEEO)
    # -------------------------------------------------------------------------
    daily_sewage_kld = (persons * lpcd * 0.8) / 1000.0
    peak_factor = sewage_peak_factor(persons)
    peak_sewage_lps = (daily_sewage_kld * 1000.0 * peak_factor) / 86400.0

    # Smallest standard pipe whose capacity at 0.8 depth carries the peak, by Manning.
    n, slope = SEWER_N, SEWER_SLOPE
    sewer_pipe_dia_mm = next((d for d in SEWER_DIAMETERS_MM
                              if pipe_flow_lps(d / 1000.0, SEWER_DESIGN_DEPTH, n, slope) >= peak_sewage_lps),
                             SEWER_DIAMETERS_MM[-1])
    d_sewer_m = sewer_pipe_dia_mm / 1000.0
    sewer_capacity_lps = pipe_flow_lps(d_sewer_m, SEWER_DESIGN_DEPTH, n, slope)
    v_actual = round(pipe_velocity_at_flow(d_sewer_m, peak_sewage_lps, n, slope), 2)
    sewer_ok = peak_sewage_lps <= sewer_capacity_lps
    self_cleansing = v_actual >= SELF_CLEANSING_MPS

    # Manholes calculation: 1 manhole every 30m along perimeter (IS 1742 Cl 4.3)
    perimeter_m = float(plot.get("perimeter_m") or (math.sqrt(plot_area_sqm) * 4.0))
    manhole_spacing_m = 30.0
    manhole_count = max(math.ceil(perimeter_m * 0.65 / manhole_spacing_m) + num_towers, 4)

    # -------------------------------------------------------------------------
    # 2. STORMWATER DRAINAGE NETWORK (rational method, same basis as Engineering)
    # -------------------------------------------------------------------------
    # Q = C*I*A / 360 (m3/s, I in mm/h, A in ha). This used 10*C*I*A, which is m3/h,
    # labelled L/s -- 3.6 times too high. Intensity and runoff coefficients are now the
    # Storm Water module's, so the two pages report one number for one site.
    eng_cfg = project.get("engineering") or {}
    city = iscodes.city_reference(eng_cfg.get("city"), eng_cfg.get("state"))
    intensity_mm_hr = float(eng_cfg.get("rain_intensity_override") or 0) or float(city["rain_intensity_mm_hr"])
    roof_sqm = float(eng_cfg.get("roof_area_sqm") or 0) or float(areas.get("ground_footprint_sqm") or 0)
    c_site = iscodes.RUNOFF_C.get(eng_cfg.get("site_area_type") or "mixed_site", 0.6)
    c_roof = iscodes.RUNOFF_C["rcc_roof"]
    c_weighted = ((roof_sqm * c_roof + max(plot_area_sqm - roof_sqm, 0.0) * c_site) / plot_area_sqm
                  if plot_area_sqm else c_roof)
    peak_storm_runoff_lps = round(c_weighted * intensity_mm_hr * plot_area_ha / 360.0 * 1000.0, 1)

    # Smallest standard box drain that carries it below the freeboard, by Manning.
    drain_slope, drain_freeboard_mm = DRAIN_SLOPE, DRAIN_FREEBOARD_MM
    drain_width_mm, drain_depth_mm = next(
        ((w, d) for w, d in DRAIN_SIZES_MM
         if box_flow_lps(w / 1000.0, (d - drain_freeboard_mm) / 1000.0, DRAIN_N, drain_slope) >= peak_storm_runoff_lps),
        DRAIN_SIZES_MM[-1])
    flow_depth_m = (drain_depth_mm - drain_freeboard_mm) / 1000.0
    drain_capacity_lps = box_flow_lps(drain_width_mm / 1000.0, flow_depth_m, DRAIN_N, drain_slope)
    drain_ok = peak_storm_runoff_lps <= drain_capacity_lps
    drain_velocity = round(drain_capacity_lps / 1000.0 / (drain_width_mm / 1000.0 * flow_depth_m), 2)

    # -------------------------------------------------------------------------
    # 3. WATER SUPPLY & FIRE RING MAIN (IS 1172 / NBC Part 4)
    # -------------------------------------------------------------------------
    daily_water_kld = (persons * lpcd) / 1000.0
    domestic_peak_flow_lps = (daily_water_kld * 1000.0 * 3.0) / 86400.0  # Peak factor 3.0
    
    # Combined with Fire Hydrant demand: 2280 L/min = 38 L/s (NBC Part 4)
    fire_hydrant_flow_lps = 38.0
    combined_ring_flow_lps = domestic_peak_flow_lps + fire_hydrant_flow_lps
    
    # Ring Main pipe diameter (Ductile Iron DI Class K9)
    ring_main_dia_mm = 150 if combined_ring_flow_lps > 30.0 else 100
    
    # Head loss per 100m via Hazen-Williams (C = 130 for new DI pipe)
    # hf/100m = 10.67 * Q^1.852 / (C^1.852 * D^4.87) * 100
    q_cum_s = combined_ring_flow_lps / 1000.0
    d_m = ring_main_dia_mm / 1000.0
    ring_velocity = q_cum_s / (math.pi * d_m * d_m / 4.0)
    hf_per_100m = (10.67 * (q_cum_s ** 1.852)) / ((130.0 ** 1.852) * (d_m ** 4.87)) * 100.0
    
    # Booster Pump rating (bar / head)
    max_height_m = max((float(t.get("height") or (int(t.get("floors") or 1) * float(t.get("floor_height") or 3.0))) for t in areas.get("towers") or towers), default=30.0)
    residual_head_req_m = 10.0  # 1.0 kg/cm2
    total_dynamic_head_m = round(max_height_m + (hf_per_100m * (perimeter_m / 100.0)) + residual_head_req_m, 1)
    
    # -------------------------------------------------------------------------
    # 4. ELECTRICAL POWER DISTRIBUTION & SUBSTATION (NBC Part 8)
    # -------------------------------------------------------------------------
    connected_load_kw = round(float(utilities.get("connected_load_kw") or (total_units * 4.0)), 1)
    # Diversity factor 0.70
    max_demand_kw = round(connected_load_kw * 0.70, 1)
    # Transformer kVA at 0.85 power factor + 20% margin
    transformer_kva = round((max_demand_kw / 0.85) * 1.20, 0)
    
    # Standard Indian transformer steps (160, 250, 315, 500, 630, 750, 1000, 1250, 1600 kVA)
    steps = [160, 250, 315, 500, 630, 750, 1000, 1250, 1600, 2000]
    std_transformer_kva = next((s for s in steps if s >= transformer_kva), 1000)
    
    # DG Set rating (50% emergency + fire + lifts + water supply)
    dg_kva = next((s for s in steps if s >= (std_transformer_kva * 0.60)), 500)
    
    # -------------------------------------------------------------------------
    # NETWORK SUMMARY & SPECIFICATIONS TABLE
    # -------------------------------------------------------------------------
    networks = [
        {
            "system": "Gravity Sewerage",
            "element": "External Trunk Sewer",
            "specification": f"Ø{sewer_pipe_dia_mm}mm DWC Polyethylene / UPVC SN8 Pipe",
            "slope": "1 : 150 (0.67% grade)",
            "velocity": f"{v_actual:.2f} m/s at peak (" + ("self-cleansing" if self_cleansing else f"below {SELF_CLEANSING_MPS} m/s - flush or steepen") + ")",
            "capacity": (f"{peak_sewage_lps:.1f} L/s peak (x{peak_factor:g}) vs {sewer_capacity_lps:.1f} L/s at 0.8 depth"
                         + ("" if sewer_ok else " - EXCEEDS largest standard pipe; split the network")),
            "appurtenances": f"{manhole_count} Precast RCC Manholes at {manhole_spacing_m:.0f}m spacing",
            "governing_code": "IS 1742 / CPHEEO"
        },
        {
            "system": "Stormwater Drainage",
            "element": "Perimeter Box Culvert Drain",
            "specification": f"{drain_width_mm}mm W × {drain_depth_mm}mm D Precast RCC Box Drain",
            "slope": "1 : 300 (0.33% grade)",
            "velocity": f"{drain_velocity:.2f} m/s at design depth",
            "capacity": (f"{peak_storm_runoff_lps:.1f} L/s peak ({intensity_mm_hr:g} mm/hr, C={c_weighted:.2f}) vs {drain_capacity_lps:.1f} L/s"
                         + ("" if drain_ok else " - EXCEEDS largest standard drain; split the catchment")),
            "appurtenances": f"Cast Iron heavy-duty drop gratings & desilting catchpits",
            "governing_code": "NBC Part 9 / IS 1742"
        },
        {
            "system": "Water Distribution & Fire",
            "element": "External Dual Ring Main",
            "specification": f"Ø{ring_main_dia_mm}mm Ductile Iron (DI) Class K9 Pipe Loop",
            "slope": "Follows finished site grade",
            "velocity": f"{ring_velocity:.2f} m/s",
            "capacity": f"{combined_ring_flow_lps:.1f} L/s (Dual Domestic + Fire Booster Loop)",
            "appurtenances": f"Booster Pump Head {total_dynamic_head_m}m, Post Indicator Valves & 4-way Fire Inlets",
            "governing_code": "IS 1172 / NBC Part 4"
        },
        {
            "system": "Power & Electrical",
            "element": "Dedicated 11 kV / 415 V Substation",
            "specification": f"{std_transformer_kva} kVA Oil-Cooled Step-Down Transformer (11 kV / 415 V)",
            "slope": "1.0m underground RCC cable trench",
            "velocity": "N/A",
            "capacity": f"{connected_load_kw} kW Connected / {max_demand_kw} kW Peak Demand",
            "appurtenances": f"{dg_kva} kVA Soundproof Acoustic DG Backup Set, Vacuum Circuit Breaker (VCB)",
            "governing_code": "NBC Part 8 / CEA 2010"
        }
    ]
    
    return {
        "summary": {
            "plot_area_ha": round(plot_area_ha, 2),
            "persons_served": persons,
            "peak_sewage_lps": round(peak_sewage_lps, 2),
            "sewer_pipe_dia_mm": sewer_pipe_dia_mm,
            "manhole_count": manhole_count,
            "sewage_peak_factor": peak_factor,
            "sewer_capacity_lps": round(sewer_capacity_lps, 1),
            "sewer_velocity_mps": v_actual,
            "peak_storm_runoff_lps": peak_storm_runoff_lps,
            "storm_intensity_mm_hr": intensity_mm_hr,
            "storm_runoff_coefficient": round(c_weighted, 3),
            "storm_drain_capacity_lps": round(drain_capacity_lps, 1),
            "storm_drain_size_mm": f"{drain_width_mm}x{drain_depth_mm}",
            "water_ring_dia_mm": ring_main_dia_mm,
            "booster_pump_head_m": total_dynamic_head_m,
            "connected_load_kw": connected_load_kw,
            "transformer_kva": std_transformer_kva,
            "dg_backup_kva": dg_kva
        },
        "networks": networks
    }
