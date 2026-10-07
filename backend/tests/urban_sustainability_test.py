"""Tests for Aptimizer X: Urban Intelligence & Sustainability Engine."""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import urban_sustainability as urbansustlib
import autonomous_planning as autoplanning


@pytest.fixture
def sample_project():
    return autoplanning.one_click_generate({"plot_area_sqm": 12000.0, "floors": 12})


def test_land_value_needs_a_land_cost(sample_project):
    res = urbansustlib.predict_urban_growth_and_value(sample_project)
    assert res["base_land_rate_inr_sqm"] is None and res["growth_catalysts"] == []
    sample_project["finance"] = {"land_cost": 120_000_000}
    res = urbansustlib.predict_urban_growth_and_value(sample_project)
    assert len(res["appreciation_projections"]) == 5
    assert res["appreciation_projections"][-1]["cumulative_gain_pct"] > 0


def test_climate_uses_city_reference(sample_project):
    res = urbansustlib.analyze_climate_and_disasters(sample_project)
    assert len(res["hazards"]) >= 3
    assert any(h["hazard_type"] == "Seismic Hazard" for h in res["hazards"])


def test_noise_estimate_and_no_invented_aqi(sample_project):
    res = urbansustlib.analyze_noise_and_pollution(sample_project)
    assert res["noise_analysis"]["front_facade_noise_dba"] < res["noise_analysis"]["curb_noise_level_dba"]
    assert res["air_quality_analysis"]["site_air_quality_index_aqi"] is None


def test_green_scorecard_is_the_engineering_checklist(sample_project):
    import engine, engineering
    res = urbansustlib.calculate_green_building_scorecard(sample_project)
    e = engineering.analyse_engineering(sample_project, engine.analyse(sample_project))
    earned = sum(c["earned"] for c in e["modules"]["green"]["categories"])
    assert res["total_points_achieved"] == earned
    assert len(res["categories"]) >= 5


def test_esg_uses_engineering_carbon_and_claims_no_rating(sample_project):
    res = urbansustlib.generate_esg_report(sample_project)
    assert res["carbon_accounting_tco2e"]["total_footprint_tco2e"] > 0
    assert res["esg_composite_rating"] is None
    assert res["social_metrics"]["worker_welfare_compliance_pct"] is None


def test_lifecycle_cost_npv_includes_every_cash_flow(sample_project):
    res = urbansustlib.calculate_lifecycle_cost(sample_project, years=30)
    assert res["analysis_horizon_years"] == 30
    assert res["total_lifecycle_cost_inr"] > res["initial_capital_expenditure_capex_inr"]
    assert len(res["rehabilitation_schedule"]) >= 4


def test_executive_kpis_come_from_finance(sample_project):
    import engine, finance
    res = urbansustlib.get_executive_dashboard_kpis(sample_project)
    f = finance.analyse(sample_project, engine.analyse(sample_project), {})
    assert res["kpis"]["project_internal_rate_of_return_irr_pct"] == f["profit"]["irr_pct"]
    assert res["kpis"]["structural_safety_factor"] is None


def test_equipment_and_risks_from_boq_and_programme(sample_project):
    res = urbansustlib.schedule_equipment_and_risks(sample_project)
    assert len(res["equipment_fleet"]) >= 3
    assert 1 <= len(res["identified_delay_risks"]) <= 3
    assert res["recommended_float_buffer_days"] >= 0
