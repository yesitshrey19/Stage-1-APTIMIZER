"""
Tests for Aptimizer V4 Engineering Decision Support:
- Equipment Planning
- Utility Network Planning
"""

import pytest
from equipment import plan_equipment
from utility_network import plan_utility_network


def test_equipment_planning_sizing():
    project = {
        "floor_height": 3.0,
        "towers": [
            {"floors": 15, "builtup_sqm": 8000, "units": 60},
            {"floors": 18, "builtup_sqm": 9500, "units": 72}
        ]
    }
    boq = {"concrete_cum": 7500, "steel_mt": 600}
    plan = plan_equipment(project, boq)
    
    summary = plan["summary"]
    assert summary["num_towers"] == 2
    assert summary["building_height_m"] == 18 * 3.0  # 54m
    assert summary["tower_cranes"] == 2
    assert summary["passenger_hoists"] == 2
    assert summary["batching_plant_capacity_cum_hr"] in [30, 45, 60]
    assert summary["total_power_demand_kw"] > 100.0
    assert summary["dg_backup_kva"] > 150.0

    fleet = plan["fleet"]
    categories = {item["category"] for item in fleet}
    assert "Lifting & Craneage" in categories
    assert "Concreting Plant" in categories
    assert "Vertical Logistics" in categories
    assert "Rebar Workshop" in categories


def test_utility_network_planning():
    project = {
        "plot": {"area_sqm": 12000, "perimeter_m": 440},
        "floor_height": 3.0,
        "towers": [
            {"floors": 14, "units": 56},
            {"floors": 14, "units": 56}
        ]
    }
    analysis = {
        "utilities": {"persons": 500},
        "areas": {"ground_coverage_pct": 32.0}
    }
    net = plan_utility_network(project, analysis)
    
    summary = net["summary"]
    assert summary["persons_served"] == 500
    assert summary["peak_sewage_lps"] > 0
    assert summary["sewer_pipe_dia_mm"] in [160, 200, 250]
    assert summary["manhole_count"] >= 4
    assert summary["peak_storm_runoff_lps"] > 0
    assert summary["water_ring_dia_mm"] in [100, 150]
    assert summary["connected_load_kw"] > 0
    assert summary["transformer_kva"] >= 500

    networks = net["networks"]
    systems = {item["system"] for item in networks}
    assert "Gravity Sewerage" in systems
    assert "Stormwater Drainage" in systems
    assert "Water Distribution & Fire" in systems
    assert "Power & Electrical" in systems
