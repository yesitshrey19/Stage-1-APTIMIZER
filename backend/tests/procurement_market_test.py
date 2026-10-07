"""Tests for Aptimizer X: Smart Procurement & Ecosystem."""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import procurement_market as procurementlib
import autonomous_planning as autoplanning


@pytest.fixture
def sample_project():
    return autoplanning.one_click_generate({"plot_area_sqm": 12000.0, "floors": 12})


def test_live_material_prices():
    res = procurementlib.get_live_material_prices("Delhi-NCR")
    assert res["ok"] is True
    assert len(res["items"]) >= 8
    assert any("Fe 550D" in item["material"] for item in res["items"])

    res_mumbai = procurementlib.get_live_material_prices("Mumbai-MMR")
    assert res_mumbai["ok"] is True
    assert res_mumbai["selected_metro"] == "Mumbai-MMR"


def test_live_prices_are_labelled_as_reference_data():
    res = procurementlib.get_live_material_prices("Delhi-NCR")
    assert res["is_live"] is False and res["data_source"] == "reference_table"
    assert all(i["trend"] is None for i in res["items"])     # no invented price history


def test_forecast_material_prices():
    res = procurementlib.forecast_material_prices("steel", horizon_months=12, metro="Mumbai-MMR")
    assert res["ok"] is True and res["data_source"] == "model"
    assert len(res["forecast_series"]) == 12
    assert res["forecast_series"][0]["projected_price"] > 0
    assert res["forecast_series"][0]["confidence_pct"] is None


def test_supplier_register_names_no_real_company_without_records():
    res = procurementlib.get_supplier_intelligence()
    assert res["ok"] is True and res["data_source"] == "sample"
    assert all(s["rating"] is None and "VERIFIED" not in s["status"].replace("NOT VERIFIED", "") for s in res["suppliers"])
    assert not any(name in s["name"] for s in res["suppliers"] for name in ("Tata", "UltraTech", "ACC", "Jindal"))


def test_procurement_calendar_follows_the_programme(sample_project):
    res = procurementlib.get_procurement_calendar(sample_project)
    assert res["ok"] is True and res["data_source"] == "programme"
    assert res["total_procurement_milestones"] >= 3
    assert any("rebar" in m["material"].lower() for m in res["milestones"])
    assert all(m["order_date"] < m["delivery_date"] for m in res["milestones"])


def test_calculate_inventory_plan_from_take_off(sample_project):
    res = procurementlib.calculate_inventory_plan(sample_project)
    assert res["ok"] is True
    assert len(res["inventory_items"]) >= 3
    assert any("TMT Steel" in item["material"] for item in res["inventory_items"])


def test_marketplace_lists_only_real_or_planned_integrations():
    res = procurementlib.get_marketplace_catalog()
    assert res["ok"] is True
    assert res["developer_portal_url"] is None
    assert all(p["price"].startswith(("Available", "Planned")) for p in res["plugins"])
    assert all(a["endpoint"].split()[1].startswith("/api/") and "/v1/" not in a["endpoint"] for a in res["apis"])


def test_generate_tender_documents_priced_from_boq(sample_project):
    import engine
    res = procurementlib.generate_tender_documents(sample_project)
    assert res["ok"] is True
    assert "APT/NIT/" in res["nit"]["tender_ref_no"]
    assert res["nit"]["tender_ref_no"] == procurementlib.generate_tender_documents(sample_project)["nit"]["tender_ref_no"]
    assert res["nit"]["estimated_tender_value_inr"] == pytest.approx(engine.analyse(sample_project)["cost"]["total"], rel=1e-6)
    assert len(res["sections"]) == 6
    assert res["tender_package_status"].startswith("DRAFT")


def test_government_approval_dossier_checks_the_project(sample_project):
    res = procurementlib.generate_government_approval_dossier(sample_project)
    assert res["ok"] is True
    assert res["total_clearance_agencies"] >= 3
    # Owner documents (title deed, escrow) are not in the project, so nothing is 100% ready.
    assert res["overall_submission_readiness_pct"] < 100
    sample_project["documents"] = {k: True for k in ("title_deed", "sanctioned_plan", "escrow_account")}
    rera = next(c for c in procurementlib.generate_government_approval_dossier(sample_project)["clearances"]
                if "RERA" in c["agency"])
    assert rera["readiness_pct"] == 100


def test_educational_mode_guide():
    res = procurementlib.get_educational_mode_guide("setbacks")
    assert res["ok"] is True
    assert "NBC 2016" in res["guide"]["governing_code"]
    assert "fire" in res["guide"]["principle"].lower()
