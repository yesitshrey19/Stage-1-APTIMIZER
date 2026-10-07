"""Tests for Digital Twin & Smart Construction Platform (Aptimizer V5).

The contract: anything derivable from the project (planned value, the delay forecast) is
computed from its programme; anything that only site can supply comes from `site_data` and
is otherwise returned as clearly-labelled sample data. Above all, no formwork stripping
clearance is ever produced from sample data.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import digital_twin as digitaltwinlib
import bim as bimlib
import autonomous_planning as autoplanning


@pytest.fixture
def sample_project():
    return autoplanning.one_click_generate({"plot_area_sqm": 12000.0, "floors": 12})


def test_iot_registry_is_labelled_sample_without_site_devices(sample_project):
    res = digitaltwinlib.iot_registry(sample_project)
    assert res["ok"] is True
    assert res["data_source"] == "sample"
    assert res["devices_online"] == 0
    assert all(d["status"] == "sample" for d in res["devices"])


def test_iot_registry_reads_site_devices(sample_project):
    sample_project["site_data"] = {"devices": [{"id": "D1", "status": "online"}, {"id": "D2", "status": "offline"}]}
    res = digitaltwinlib.iot_registry(sample_project)
    assert res["data_source"] == "site"
    assert res["total_sensors_deployed"] == 2 and res["devices_online"] == 1


def test_no_stripping_clearance_from_sample_data(sample_project):
    conc = digitaltwinlib.sensor_telemetry(sample_project)["concrete_curing_maturity"]
    assert conc["data_source"] == "sample"
    assert conc["strip_ok"] is False
    assert "SAFE TO STRIP" not in conc["formwork_stripping_advisory"]
    assert "IS 456" in conc["formwork_stripping_advisory"]


def test_maturity_needs_calibration_before_any_strength(sample_project):
    sample_project["site_data"] = {"maturity": {"grade_mpa": 25, "span_m": 4.0,
                                                "readings": [{"hours": 0, "temp_c": 30}, {"hours": 240, "temp_c": 30}]}}
    conc = digitaltwinlib.sensor_telemetry(sample_project)["concrete_curing_maturity"]
    assert conc["estimated_compressive_strength_mpa"] is None
    assert conc["strip_ok"] is False
    assert conc["equivalent_age_maturity_index"] == 7200       # 240 h x 30 °C above a 0 °C datum


def test_maturity_clearance_needs_strength_and_time(sample_project):
    calib = [{"maturity_c_h": 500, "strength_mpa": 8}, {"maturity_c_h": 3000, "strength_mpa": 18},
             {"maturity_c_h": 8000, "strength_mpa": 26}]
    sample_project["site_data"] = {"maturity": {"grade_mpa": 25, "span_m": 4.0, "calibration": calib,
                                                "readings": [{"hours": 0, "temp_c": 30}, {"hours": 120, "temp_c": 30}]}}
    early = digitaltwinlib.sensor_telemetry(sample_project)["concrete_curing_maturity"]
    assert early["strip_ok"] is False                       # 5 days < the 7-day IS 456 minimum
    sample_project["site_data"]["maturity"]["readings"][-1]["hours"] = 200
    later = digitaltwinlib.sensor_telemetry(sample_project)["concrete_curing_maturity"]
    assert later["estimated_compressive_strength_mpa"] >= 0.7 * 25
    assert later["strip_ok"] is True


def test_progress_4d_follows_the_programme(sample_project):
    res = digitaltwinlib.track_progress_4d(sample_project)
    assert res["ok"] is True
    ev = res["earned_value_metrics"]
    assert ev["budget_at_completion_inr_cr"] > 0
    # Without recorded progress there is no EV, so no SPI/CPI is invented.
    assert ev["schedule_performance_index_spi"] is None and ev["cost_performance_index_cpi"] is None
    assert len(res["floor_4d_breakdown"]) == 12


def test_progress_4d_uses_recorded_progress(sample_project):
    plan = digitaltwinlib._programme(sample_project)[1]
    first = plan["activities"][0]["id"]
    sample_project["site_data"] = {"status_date": plan["finish"],
                                   "progress": {"activity_pct": {a["id"]: 100 for a in plan["activities"]},
                                                "actual_cost_inr": 1.0}}
    res = digitaltwinlib.track_progress_4d(sample_project)
    assert res["earned_value_metrics"]["schedule_performance_index_spi"] == pytest.approx(1.0, abs=0.01)
    assert res["project_completion_pct"] == pytest.approx(100.0, abs=0.1)
    assert first


def test_quality_safety_audit_labels_sample(sample_project):
    res = digitaltwinlib.quality_safety_audit(sample_project)
    assert res["ok"] is True and res["data_source"] == "sample"
    assert res["safety"]["lost_time_injuries_lti"] is None     # never claims a safety record


def test_predict_delays_simulates_the_programme(sample_project):
    res = digitaltwinlib.predict_delays(sample_project, iterations=200)
    assert res["ok"] is True
    assert res["monte_carlo_iterations"] == 200
    assert res["predicted_completion_date"] >= res["baseline_completion_date"]
    assert res["p80_completion_date"] >= res["predicted_completion_date"]
    assert 0.0 <= res["on_time_probability_pct"] <= 100.0
    assert 1 <= len(res["risk_factors_analyzed"]) <= 3
    # Seeded: the same project gives the same forecast every time.
    again = digitaltwinlib.predict_delays(sample_project, iterations=200)
    assert again["predicted_completion_date"] == res["predicted_completion_date"]


def test_facility_management_labels_sample_and_has_stable_id(sample_project):
    res = digitaltwinlib.facility_management(sample_project)
    assert res["ok"] is True and res["data_source"] == "sample"
    assert res["facility_id"] == digitaltwinlib.facility_management(sample_project)["facility_id"]


def test_dwg_export_is_real_dwg_or_honest_dxf(sample_project):
    data, fmt = bimlib.export_siteplan_cad(sample_project)
    assert fmt in ("dwg", "dxf")
    if fmt == "dxf":
        assert b"SECTION" in data and b"HEADER" in data
    else:
        assert data[:4] == b"AC10"
