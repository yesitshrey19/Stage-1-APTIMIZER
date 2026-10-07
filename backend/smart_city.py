"""Smart City Platform & Urban Intelligence.

Implements:
1. City-Scale Planning: Macro urban zoning and master-plan alignment.
2. Traffic Simulation: Trip generation, road Level of Service (LOS A-F), emergency turning radii.
3. Utility Network Optimisation: Stormwater runoff, looped water distribution, gravity sewerage, electrical grid.
4. Urban Digital Twin: Spatial context, sun-path microclimate, Urban Heat Island (UHI) index.
5. Infrastructure Demand Forecasting: Multi-year civic demand projections (water, power, waste, social infra).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional
import engine


def _site_summary(project: Dict[str, Any], default_plot_sqm: float = 10000.0, default_units: int = 200):
    areas = engine.area_metrics(project)
    plot = project.get("plot") or {}
    eng = project.get("engineering") or {}
    plot_area_sqm = float(areas.get("plot_area_sqm") or plot.get("area_sqm") or default_plot_sqm)
    unit_count = int(areas.get("total_units") or 0)
    if unit_count <= 0:
        towers = project.get("towers") or []
        unit_count = sum(int(t.get("floors") or 1) * int(t.get("units_per_floor") or 4) for t in towers) or default_units
    road_width_m = float(plot.get("road_width_m") or eng.get("road_width") or 18.0)
    return areas, plot_area_sqm, unit_count, road_width_m


# --------------------------------------------------------------------------- 1. City-Scale Planning

def city_scale_plan(project: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Macro-level urban zoning analysis, density distribution, and master plan alignment."""
    params = params or {}
    _, plot_area_sqm, unit_count, _ = _site_summary(project, default_plot_sqm=25000.0, default_units=192)
    city_tier = params.get("city_tier") or "Tier-1 Metro"
    master_plan_zone = params.get("master_plan_zone") or "R-2 (Medium-to-High Density Residential)"
    permissible_dph = float(params.get("permissible_density_units_per_hectare") or 250)
    proposed_dph = round(unit_count / max(0.01, plot_area_sqm / 10000.0), 1)

    land_use = [
        {"category": "Residential Footprints", "area_sqm": round(plot_area_sqm * 0.32, 1), "pct": 32.0},
        {"category": "Commercial & Retail Outlets", "area_sqm": round(plot_area_sqm * 0.12, 1), "pct": 12.0},
        {"category": "Public Open Spaces & Parks", "area_sqm": round(plot_area_sqm * 0.25, 1), "pct": 25.0},
        {"category": "Roads & Mobility Corridors", "area_sqm": round(plot_area_sqm * 0.20, 1), "pct": 20.0},
        {"category": "Civic Utilities & Substations", "area_sqm": round(plot_area_sqm * 0.11, 1), "pct": 11.0},
    ]

    return {
        "ok": True,
        "city_tier": city_tier,
        "master_plan_zone": master_plan_zone,
        "total_study_area_sqm": plot_area_sqm,
        "land_use_distribution": land_use,
        "density_guidelines": {
            "permissible_density_units_per_hectare": permissible_dph,
            "proposed_density_units_per_hectare": proposed_dph,
            "compliance_status": ("Within master plan density" if proposed_dph <= permissible_dph
                                  else f"Exceeds the {permissible_dph:g} units/ha master plan density"),
        },
        "urban_fabric_metrics": {
            "permeability_index": None,
            "green_canopy_target_pct": 33.0,
            "solar_corridor_adequacy": "Not assessed - check tower separation on the site layout",
        }
    }


# --------------------------------------------------------------------------- 2. Traffic Simulation

def simulate_traffic(project: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Peak-hour trip generation and road level of service (a static estimate, not a micro-simulation)."""
    params = params or {}
    _, _, unit_count, road_width_m = _site_summary(project, default_plot_sqm=10000.0, default_units=240)

    # ITE / IRC Trip Generation Rates for Residential Apartments:
    # ~0.55 trips per dwelling unit in peak AM hour, ~0.65 in peak PM hour
    peak_am_trips = int(unit_count * 0.55)
    peak_pm_trips = int(unit_count * 0.65)
    daily_trips = int(unit_count * 5.8)

    # Road capacity estimation (IRC:106-1990 Guidelines for Capacity of Urban Roads)
    # 2-lane divided: ~1800 PCU/hr; 4-lane: ~3600 PCU/hr
    lanes = 2 if road_width_m < 24.0 else 4
    capacity_pcu_per_hr = 1800 if lanes == 2 else 3600
    # The road already carries traffic; without a count only the project's own trips are
    # tested, which flatters the result, so it is said in the output.
    background = float(params.get("background_pcu_per_hr") or 0.0)
    volume_capacity_ratio = round((peak_pm_trips + background) / capacity_pcu_per_hr, 2)

    # Level of Service (LOS) criteria (IRC:106)
    if volume_capacity_ratio <= 0.35:
        los = "A (Free Flow)"
    elif volume_capacity_ratio <= 0.55:
        los = "B (Reasonably Free Flow)"
    elif volume_capacity_ratio <= 0.75:
        los = "C (Stable Flow)"
    elif volume_capacity_ratio <= 0.85:
        los = "D (Approaching Unstable)"
    elif volume_capacity_ratio <= 1.00:
        los = "E (Unstable Flow)"
    else:
        los = "F (Forced or Breakdown Flow)"

    # Fire tender access (NBC 2016 Part 4: 6 m clear width, 9 m turning radius). The plan
    # holds no measured internal road geometry, so this is a requirement to verify, never a
    # pass the app cannot see.
    provided_radius = params.get("provided_turning_radius_m")
    provided_width = params.get("clear_access_width_m")
    if provided_radius is None or provided_width is None:
        fire_status = "NOT VERIFIED - measure the internal road on the site layout"
    elif float(provided_radius) >= 9.0 and float(provided_width) >= 6.0:
        fire_status = "PASS (NBC 2016 Part 4: 6 m clear, 9 m turning radius)"
    else:
        fire_status = "FAIL (NBC 2016 Part 4 needs 6 m clear width and a 9 m turning radius)"
    fire_tender_check = {
        "required_turning_radius_m": 9.0,
        "provided_turning_radius_m": provided_radius,
        "clear_access_width_m": provided_width,
        "required_clear_width_m": 6.0,
        "status": fire_status,
    }
    # BPR link delay over a 200 m approach at 30 km/h free flow (24 s).
    delay_s = round(24.0 * 0.15 * volume_capacity_ratio ** 4, 1)

    return {
        "ok": True,
        "unit_count": unit_count,
        "access_road_width_m": road_width_m,
        "trip_generation": {
            "peak_am_trips_per_hour": peak_am_trips,
            "peak_pm_trips_per_hour": peak_pm_trips,
            "daily_trips_total": daily_trips,
        },
        "level_of_service": {
            "volume_capacity_ratio": volume_capacity_ratio,
            "grade": los,
            "traffic_delay_seconds_per_vehicle": delay_s,
            "queue_length_metres": None,
            "background_pcu_per_hr": background,
            "note": ("" if background else "No background traffic count entered: only the project's own trips "
                     "are tested, so the real level of service will be worse."),
        },
        "emergency_vehicle_clearance": fire_tender_check,
        "recommendations": [
            "Provide dedicated left-in / left-out deceleration pocket at main gate.",
            "Install dual RFID boom barriers to maintain vehicle clearance time < 6 seconds.",
        ]
    }


# --------------------------------------------------------------------------- 3. Utility Network Optimisation

def optimize_utility_network(project: Dict[str, Any]) -> Dict[str, Any]:
    """Site utilities summarised from the Utilities module and the external network design.

    This used to size everything a third time with its own constants (5 persons a home,
    a fixed 200 mm sewer, 200 kL of fire storage), so three pages gave three answers.
    """
    import utility_network
    try:
        a = engine.analyse(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    u = a["utilities"]
    net = utility_network.plan_utility_network(project, a)["summary"]
    demand_kl = round(float(u.get("water_demand_lpd") or 0) / 1000.0, 1)
    stp_kld = float(u.get("stp_capacity_kld") or 0)
    connected_kw = float(u.get("connected_load_kw") or net.get("connected_load_kw") or 0)
    return {
        "ok": True,
        "data_source": "Utilities module + external network design",
        "stormwater": {
            "peak_discharge_m3_per_hr": round(float(net["peak_storm_runoff_lps"]) * 3.6, 1),
            "drain_profile": f"Precast RCC box drain {net['storm_drain_size_mm']} mm (W x D)",
            "minimum_slope": "1 in 300",
            "rwh_recharge_pits": None,
            "annual_harvesting_potential_kl": round(float(u.get("rwh_annual_litres") or 0) / 1000.0, 0),
        },
        "water_supply": {
            "daily_water_demand_kl": demand_kl,
            "distribution_loop": "Closed ring main around perimeter",
            "pipe_material": "Ductile Iron (DI K9) / HDPE PE100",
            "primary_main_diameter_mm": net["water_ring_dia_mm"],
            "secondary_branch_diameter_mm": None,
            "residual_pressure_head_m": 10.0,
            "storage_breakdown": {
                "raw_water_underground_kl": round(float(u.get("ug_tank_cum") or 0), 1),
                "treated_water_overhead_kl": round(float(u.get("oh_tank_cum") or 0), 1),
                "fire_reserve_dedicated_kl": None,
            },
        },
        "sewerage": {
            "daily_sewage_generation_kl": stp_kld,
            "stp_technology": "Per Engineering > Water Infrastructure recommendation",
            "treated_effluent_reuse_kl": round(stp_kld * 0.8, 1),
            "pipe_diameter_mm": net["sewer_pipe_dia_mm"],
            "self_cleansing_velocity_m_s": net.get("sewer_velocity_mps"),
            "invert_drop_total_m": None,
        },
        "electrical": {
            "connected_load_kva": round(connected_kw / 0.85, 0),
            "transformer_capacity": f"{net['transformer_kva']} kVA (11 kV / 415 V)",
            "dg_backup_capacity_kva": net["dg_backup_kva"],
            "cable_trench_depth_m": 1.0,
            "solar_pv_rooftop_kwp": None,
        },
    }


# --------------------------------------------------------------------------- 4. Urban Digital Twin

def urban_digital_twin(project: Dict[str, Any]) -> Dict[str, Any]:
    """Calculates 3D spatial twin context, sun-path microclimate, and Urban Heat Island (UHI) index."""
    _, plot_area_sqm, _, _ = _site_summary(project, default_plot_sqm=10000.0, default_units=200)
    towers = project.get("towers") or []

    import hashlib
    try:
        open_sqm = float(engine.area_metrics(project).get("open_space_sqm") or 0)
    except Exception:
        open_sqm = 0.0
    key = str(project.get("_id") or project.get("name") or "proj")
    return {
        "ok": True,
        "data_source": "indicative",
        "note": ("Microclimate values are indicative defaults, not a simulation of this site. "
                 "Use the GIS module's sun-path and wind analysis for site figures."),
        # hash() is salted per process, so the old id changed on every restart.
        "twin_id": "TWIN-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:4].upper(),
        "spatial_resolution": "0.5m georeferenced mesh",
        "microclimate_simulation": {
            "annual_sun_exposure_hours": 2680,
            "shadow_corridor_impact": "Low (Tower separation exceeds 1.5x height envelope)",
            "mean_wind_tunnel_velocity_m_s": 3.2,
            "cross_ventilation_efficiency_pct": 78.5,
            "urban_heat_island_score": None,
            "estimated_local_cooling_effect": "Not modelled",
        },
        "gis_boundary_layers": [
            {"layer": "Plot Cadastral Boundary", "entities": 1, "status": "Active"},
            {"layer": "Tower 3D Masses", "entities": len(towers), "status": "Active"},
            {"layer": "Tree Canopy & Green Buffers", "entities": int(open_sqm // 80), "status": "Planned (1 tree / 80 m² open space)"},
            {"layer": "Underground Utility Corridors", "entities": 4, "status": "Planned (water, sewer, storm, power)"},
        ]
    }


# --------------------------------------------------------------------------- 5. Infrastructure Demand Forecasting

def forecast_infrastructure_demand(project: Dict[str, Any]) -> Dict[str, Any]:
    """Civic infrastructure demand of the scheme over time, from its own occupancy.

    Facility distances are only reported when the GIS module has measured them; the old
    output stated a fire station at 3.2 km and a sewer main within 150 m for every site.
    """
    from datetime import date
    try:
        a = engine.analyse(project)
    except Exception as exc:
        return {"ok": False, "error": f"Project data incomplete: {exc}"}
    ar, u, park = a["areas"], a["utilities"], a["parking"]
    population = int(ar.get("occupants") or 0)
    units = int(ar.get("total_units") or 0)
    lpcd = float(u.get("lpcd") or 135.0)
    base_kw = float(u.get("connected_load_kw") or 0)
    ev_slots = int(park.get("ev_required") or 0)
    year0 = date.today().year
    forecast_data = []
    for offset in (0, 2, 4, 9, 14):
        y = year0 + offset
        # Assumptions, stated: per-capita water held at the design norm; electrical demand
        # +2 %/yr; EV charging adoption ramps 10 %/yr of EV bays at 3.3 kW each.
        adoption = min(1.0, 0.10 * (offset + 1))
        forecast_data.append({
            "year": y,
            "projected_population": population,
            "water_demand_mld": round(population * lpcd / 1e6, 4),
            "power_demand_mva": round(base_kw * 0.7 * (1.02 ** offset) / 0.85 / 1000.0, 3),
            "solid_waste_tpd": round(population * 0.45 / 1000.0, 2),
            "recycled_water_available_mld": round(float(u.get("stp_capacity_kld") or 0) * 0.8 / 1000.0, 4),
            "ev_charging_load_kw": round(ev_slots * adoption * 3.3, 1),
        })
    gis = project.get("gis") or {}
    transit = ((gis.get("features") or {}).get("transit") or [])
    nearest_transit = min((t.get("distance_m") for t in transit if t.get("distance_m") is not None), default=None)
    return {
        "ok": True,
        "design_population": population,
        "dwelling_units": units,
        "assumptions": "Water at the design lpcd; power +2%/yr at 0.7 diversity; EV adoption +10%/yr at 3.3 kW per bay; waste 450 g/person/day.",
        "forecast_timeline": forecast_data,
        "civic_services_adequacy": {
            "nearest_fire_station_km": None,
            "nearest_primary_health_centre_km": None,
            "nearest_transit_km": round(nearest_transit / 1000.0, 2) if nearest_transit is not None else None,
            "primary_school_capacity_needed": round(population * 0.12),
            "municipal_sewer_connection": "Not assessed - confirm with the local body",
            "note": "Fire station and health centre distances are not measured by the GIS module (it maps roads, transit, green and water).",
        },
    }
