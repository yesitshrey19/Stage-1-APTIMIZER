"""
Equipment Planning & Machinery Logistics Engine (Aptimizer V4)

Computes heavy construction equipment requirements, machinery fleet sizing,
mobilisation schedules, power/fuel demand, and crane coverage based on building
geometry, concrete/excavation quantities, and project duration.

Governing Standards & Best Practices:
- IS 4573: Code of practice for design and selection of tower cranes
- IS 4925: Concrete batching and mixing plants
- IS 5121: Safety code for construction, operation and maintenance of cranes and hoists
"""

import math
from typing import Any, Dict, List, Optional


def plan_equipment(project: Dict[str, Any], boq: Optional[Dict[str, Any]] = None,
                   programme: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Derives complete machinery fleet, deployment timeline, and energy requirements."""
    plot = project.get("plot") or {}
    towers = project.get("towers") or []
    num_towers = max(len(towers), 1)
    
    # Building height and floor metrics
    max_floors = max((int(t.get("floors") or 1) for t in towers), default=10)
    floor_height_m = max((float(t.get("floor_height") or t.get("floor_height_m") or 3.0) for t in towers), default=3.0)
    building_height_m = max_floors * floor_height_m
    # Built-up from the engine. Project towers carry no "builtup_sqm", so this used to fall
    # back to 5,000 m² a tower for every project.
    import engine
    try:
        total_builtup_sqm = float(engine.area_metrics(project)["builtup_area_sqm"]) or num_towers * 5000.0
    except Exception:
        total_builtup_sqm = num_towers * 5000.0
    
    # Excavation and concrete quantities
    # Quantities from the BOQ's take-off lines (the engine BOQ lists materials by key).
    mats = {m.get("key"): m for m in ((boq or {}).get("materials") or [])}
    concrete_cum = float((boq or {}).get("concrete_cum") or (mats.get("concrete") or {}).get("quantity")
                         or (total_builtup_sqm * 0.42))
    steel_mt = float((boq or {}).get("steel_mt") or ((mats.get("steel") or {}).get("quantity") or 0) / 1000.0
                     or (concrete_cum * 0.08))

    # Project duration from the programme (summary key duration_calendar_days).
    duration_days = float((programme or {}).get("duration_calendar_days") or (programme or {}).get("total_duration_days")
                          or (max_floors * 35 + 120))
    duration_months = max(round(duration_days / 30.0, 1), 6.0)
    superstructure_months = max(round((max_floors * 20) / 30.0, 1), 4.0)
    
    # 1. TOWER CRANES (IS 4573)
    # 1 crane per tower or every 45m radius footprint cluster
    tower_cranes_count = num_towers
    crane_hook_height_m = round(building_height_m + 12.0, 1)  # 12m clearance above parapet
    crane_jib_radius_m = min(max(round(math.sqrt(total_builtup_sqm / max_floors) * 0.8, 0), 40.0), 65.0)
    crane_tip_capacity_mt = 2.0 if crane_jib_radius_m > 50 else 2.5
    crane_max_capacity_mt = 6.0 if max_floors <= 20 else 10.0
    
    # 2. CONCRETE PLACEMENT (IS 4925)
    peak_monthly_pour_cum = round((concrete_cum / superstructure_months) * 1.35, 1)
    peak_daily_pour_cum = round(peak_monthly_pour_cum / 25.0, 1)
    
    # Batching Plant sizing: 30, 45, or 60 m3/h
    if peak_daily_pour_cum > 150:
        batching_capacity = 60
    elif peak_daily_pour_cum > 80:
        batching_capacity = 45
    else:
        batching_capacity = 30
    
    # Transit Mixers (6 m3 drum capacity, ~4 trips/day)
    transit_mixers_count = max(math.ceil(peak_daily_pour_cum / (6.0 * 4.0)), 2)
    
    # Concrete Boom / Stationary Pumps: 1 pump per ~40-60 m3/h pour
    concrete_pumps_count = max(math.ceil(peak_daily_pour_cum / 120.0), 1)
    
    # 3. VERTICAL LOGISTICS / HOISTS (IS 5121)
    # 1 twin-cage hoist per tower for buildings >= 6 floors
    passenger_hoists_count = num_towers if max_floors >= 6 else 0
    hoist_speed_mpm = 40.0 if max_floors >= 15 else 33.0
    hoist_capacity_kg = 2000
    
    # 4. REBAR PROCESSING
    bar_bending_count = max(math.ceil(steel_mt / 2000.0), 1)
    bar_cutting_count = bar_bending_count
    
    # 5. EARTHMOVING & SUBSTRUCTURE
    excavators_count = max(min(math.ceil(total_builtup_sqm / 10000.0) * 2, 4), 1)
    tippers_count = excavators_count * 2
    
    # 6. TOTAL CONNECTED POWER & DIESEL DEMAND
    power_kw = (
        (tower_cranes_count * 55.0) +
        (batching_capacity * 1.5) +
        (concrete_pumps_count * 45.0) +
        (passenger_hoists_count * 22.0) +
        (bar_bending_count * 7.5) +
        (bar_cutting_count * 7.5) +
        40.0  # Site illumination, dewatering, workshop
    )
    dg_backup_kva = round(power_kw * 1.25 / 0.8, 0)
    monthly_diesel_litres = round(power_kw * 4.5 * 25, 0)  # Average daily run hours
    
    # 7. FLEET ROSTER TABLE
    fleet = [
        {
            "category": "Lifting & Craneage",
            "name": f"Tower Crane (Topless / Luffing Jib)",
            "specification": f"Hook height {crane_hook_height_m}m, Jib {crane_jib_radius_m}m, Tip {crane_tip_capacity_mt}t / Max {crane_max_capacity_mt}t",
            "quantity": tower_cranes_count,
            "unit": "Units",
            "power_kw": tower_cranes_count * 55.0,
            "phase": "Superstructure & Facade",
            "mobilisation_month": 2,
            "demobilisation_month": int(superstructure_months + 2),
            "governing_code": "IS 4573 / IS 5121"
        },
        {
            "category": "Concreting Plant",
            "name": f"Automated Concrete Batching Plant",
            "specification": f"Capacity {batching_capacity} m³/hr, Twin-shaft mixer with aggregate bins",
            "quantity": 1,
            "unit": "Plant",
            "power_kw": batching_capacity * 1.5,
            "phase": "Substructure to Superstructure",
            "mobilisation_month": 1,
            "demobilisation_month": int(superstructure_months + 1),
            "governing_code": "IS 4925"
        },
        {
            "category": "Concreting Plant",
            "name": "Transit Mixers",
            "specification": "Drum capacity 6 m³, Hydraulic PTO drive, Chute extensions",
            "quantity": transit_mixers_count,
            "unit": "Trucks",
            "power_kw": 0.0,  # Diesel vehicular
            "phase": "Substructure & Superstructure",
            "mobilisation_month": 1,
            "demobilisation_month": int(superstructure_months + 1),
            "governing_code": "IS 4925"
        },
        {
            "category": "Concreting Plant",
            "name": "High-Pressure Concrete Static Pump",
            "specification": f"Delivery capacity 60-90 m³/hr, 125mm high-pressure pipeline to {building_height_m}m",
            "quantity": concrete_pumps_count,
            "unit": "Units",
            "power_kw": concrete_pumps_count * 45.0,
            "phase": "Superstructure",
            "mobilisation_month": 2,
            "demobilisation_month": int(superstructure_months + 1),
            "governing_code": "IS 4925 / ACI 304"
        },
        {
            "category": "Vertical Logistics",
            "name": "Passenger & Material Hoist (Twin Cage)",
            "specification": f"Rack & pinion drive, Speed {hoist_speed_mpm} m/min, Payload {hoist_capacity_kg} kg / 24 pax",
            "quantity": passenger_hoists_count,
            "unit": "Units",
            "power_kw": passenger_hoists_count * 22.0,
            "phase": "Superstructure to Finishing",
            "mobilisation_month": 3,
            "demobilisation_month": int(duration_months - 1),
            "governing_code": "IS 5121"
        },
        {
            "category": "Rebar Workshop",
            "name": "Heavy Duty Bar Bending Machine (BBM 40)",
            "specification": "Bending capacity up to 40mm Fe500D/Fe550D TMT rebar",
            "quantity": bar_bending_count,
            "unit": "Units",
            "power_kw": bar_bending_count * 7.5,
            "phase": "Substructure & Superstructure",
            "mobilisation_month": 1,
            "demobilisation_month": int(superstructure_months + 1),
            "governing_code": "IS 2502"
        },
        {
            "category": "Rebar Workshop",
            "name": "Heavy Duty Bar Cutting Machine (BCM 40)",
            "specification": "Cutting capacity up to 40mm high-yield deformed bars",
            "quantity": bar_cutting_count,
            "unit": "Units",
            "power_kw": bar_cutting_count * 7.5,
            "phase": "Substructure & Superstructure",
            "mobilisation_month": 1,
            "demobilisation_month": int(superstructure_months + 1),
            "governing_code": "IS 2502"
        },
        {
            "category": "Earthworks",
            "name": "Hydraulic Crawler Excavator",
            "specification": "Operating weight 20t, Bucket capacity 0.9-1.2 m³",
            "quantity": excavators_count,
            "unit": "Units",
            "power_kw": 0.0,
            "phase": "Excavation & Substructure",
            "mobilisation_month": 1,
            "demobilisation_month": 2,
            "governing_code": "IS 3764"
        }
    ]
    
    return {
        "summary": {
            "num_towers": num_towers,
            "building_height_m": building_height_m,
            "concrete_total_cum": concrete_cum,
            "steel_total_mt": steel_mt,
            "duration_months": duration_months,
            "peak_monthly_pour_cum": peak_monthly_pour_cum,
            "tower_cranes": tower_cranes_count,
            "batching_plant_capacity_cum_hr": batching_capacity,
            "transit_mixers": transit_mixers_count,
            "passenger_hoists": passenger_hoists_count,
            "total_power_demand_kw": round(power_kw, 1),
            "dg_backup_kva": dg_backup_kva,
            "est_monthly_diesel_litres": monthly_diesel_litres
        },
        "fleet": fleet
    }
