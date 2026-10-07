"""Precision and reliability regressions for core civil-engineering arithmetic.

# Module focus: engine.area_metrics / boq / compliance precision and rounding behavior
"""

import math

import pytest

import engine
from defaults import default_project


def _base_project():
    return default_project("PREC", "QA", "Hyderabad", "P-1", "owner")


def test_zero_config_factors_and_zero_rates_are_respected_without_fallbacks():
    p = _base_project()
    p["config"].update({"wall_thickness_factor": 0.0, "common_area_loading": 0.0, "fsi_factor": 0.0})
    p["labour_rates"] = {"mason": 0.0, "plasterer": 0.0, "helper": 0.0, "carpenter": 0.0, "bar_bender": 0.0, "concretor": 0.0,
                         "tiler": 0.0, "painter": 0.0, "plumber": 0.0, "electrician": 0.0}
    p["equipment_rates"] = {"mixer": 0.0, "vibrator": 0.0, "hoist": 0.0, "crane": 0.0, "scaffold": 0.0}

    out = engine.analyse(p)
    assert out["areas"]["fsi"] == 0.0
    assert out["boq"]["labour_total"] == 0.0
    assert out["boq"]["equipment_total"] == 0.0


def test_small_fractional_geometry_keeps_precision_in_aggregations():
    p = _base_project()
    t = p["towers"][0]
    t["floors"] = 3
    t["units"] = [{"id": "u1", "type": "2bhk", "count": 1, "carpet_area": 63.3333, "balcony_area": 7.7777}]
    t["corridor_width"] = 1.111
    t["corridor_length"] = 8.888

    out = engine.analyse(p)
    builtup = out["areas"]["builtup_area_sqm"]
    tower_sum = math.fsum(x["builtup_sqm"] for x in out["areas"]["towers"])
    assert builtup == pytest.approx(tower_sum, rel=1e-12)
    assert builtup != round(builtup, 2)


def test_far_just_above_cap_fails_without_rounded_pass():
    p = _base_project()
    plot = p["plot"]
    plot["coordinates"] = []
    plot["length"] = 10
    plot["width"] = 10

    t = p["towers"][0]
    t["floors"] = 1
    t["units"] = [{"id": "u1", "type": "custom", "count": 1, "carpet_area": 90.95, "balcony_area": 0.0}]
    t["corridor_width"] = 0
    t["corridor_length"] = 0
    t["staircases"] = []
    t["lifts"] = []

    p["config"].update({"wall_thickness_factor": 0.10, "common_area_loading": 0.0, "fsi_factor": 1.0})
    p["society_amenities"] = []

    out = engine.analyse(p)
    far = out["areas"]["far"]
    rule = next(r for r in out["compliance"]["results"] if r["id"] == "far_max")
    assert far > 1.0
    assert far < 1.001
    assert rule["status"] == "pass"

    p["compliance_rules"] = [
        {**r, "id": "far_max", "threshold": far - 0.0003} if r["id"] == "far_max" else r
        for r in p["compliance_rules"]
    ]
    out2 = engine.analyse(p)
    rule2 = next(r for r in out2["compliance"]["results"] if r["id"] == "far_max")
    assert out2["areas"]["far"] > (far - 0.0003)
    assert rule2["status"] == "fail"


def test_money_product_uses_half_up_at_cent_boundary():
    assert engine.money_product(1.005, 1) == 1.01
    assert engine.money_product(2.675, 1) == 2.68


def test_mandays_and_equipment_days_remain_unrounded_before_costing():
    p = _base_project()
    out = engine.analyse(p)
    mason = next(x for x in out["boq"]["labour"] if x["key"] == "mason")
    crane = next(x for x in out["boq"]["equipment"] if x["key"] == "crane")
    assert mason["quantity"] != round(mason["quantity"], 0)
    assert crane["quantity"] != round(crane["quantity"], 0)


def test_wall_factor_change_is_reflected_in_displayed_formula_text():
    p = _base_project()
    p["config"]["wall_thickness_factor"] = 0.18
    out = engine.analyse(p)
    assert "18% wall thickness" in out["far_derivation"]["builtup_rule"]


def test_small_local_polygon_does_not_collapse_to_zero_area():
    p = _base_project()
    p["plot"]["coordinates"] = [
        [12.9710000, 77.5940000],
        [12.9710008, 77.5940012],
        [12.9710015, 77.5940001],
    ]
    out = engine.analyse(p)
    assert out["areas"]["plot_area_sqm"] > 0
