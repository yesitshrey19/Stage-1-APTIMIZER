"""Tests for Autonomous AI Engineering & Intelligent Planning (Aptimizer X / X+)."""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import autonomous_planning as autoplanning


def test_one_click_generate_affordable():
    res = autoplanning.one_click_generate({
        "name": "Affordable Test",
        "plot_area_sqm": 8000.0,
        "target_tier": "affordable",
        "city": "Bengaluru",
        "floors": 10,
    })
    assert res["name"] == "Affordable Test"
    assert res["tier"] == "affordable"
    assert len(res["plot"]["coordinates"]) == 5
    assert len(res["towers"]) >= 1
    assert res["achieved_metrics"]["achieved_far"] > 0
    assert res["parking"]["slots_provided"] >= res["parking"]["slots_required"]


def test_one_click_generate_luxury():
    res = autoplanning.one_click_generate({
        "name": "Luxury Heights",
        "plot_area_sqm": 15000.0,
        "target_tier": "luxury",
        "floors": 18,
    })
    assert res["tier"] == "luxury"
    assert res["parking"]["ev_charging_slots"] > 0
    assert any(u["type"] == "Penthouse" for u in res["unit_mix"])


def test_conversational_design_mutations():
    base = autoplanning.one_click_generate({"plot_area_sqm": 10000.0, "floors": 12})
    # Mutation 1: Floors
    m1 = autoplanning.conversational_design(base, "Make Tower A 16 floors")
    assert m1["ok"] is True
    assert any("16 floors" in r for r in m1["mutations_applied"])
    assert m1["updated_project"]["towers"][0]["floors"] == 16

    # Mutation 2: EV slots
    m2 = autoplanning.conversational_design(base, "Add 25 EV charging slots")
    assert m2["ok"] is True
    assert any("25 EV" in r for r in m2["mutations_applied"])


def test_multi_agent_review():
    project = autoplanning.one_click_generate({"plot_area_sqm": 12000.0, "floors": 14})
    review = autoplanning.multi_agent_review(project)
    assert review["ok"] is True
    assert len(review["agents"]) == 5
    roles = {a["role"] for a in review["agents"]}
    assert "Chief Architect Agent" in roles
    assert "Lead Structural Engineer Agent" in roles
    assert "MEP & Sustainability Director Agent" in roles
    assert "Chief Quantity Surveyor & Cost Agent" in roles
    assert "Statutory Compliance & Safety Officer Agent" in roles
    assert review["overall_engineering_score"] > 80
    assert "certificate_hash" in review["sign_off_certificate"]


def test_autonomous_compliance_audit():
    project = autoplanning.one_click_generate({"plot_area_sqm": 10000.0, "floors": 12})
    audit = autoplanning.autonomous_compliance_audit(project)
    assert audit["ok"] is True
    assert audit["total_checks"] >= 5
    assert audit["passed"] > 0
    assert any(c["id"] == "far_check" for c in audit["checks"])


def test_autonomous_boq_engine():
    project = autoplanning.one_click_generate({"plot_area_sqm": 10000.0, "floors": 12})
    boq = autoplanning.autonomous_boq_engine(project)
    assert boq["ok"] is True
    assert len(boq["items"]) >= 5
    assert boq["summary"]["total_project_cost_inr"] > 0
    assert boq["summary"]["cost_per_sqm_inr"] > 0


def test_township_mixed_use_plan():
    project = autoplanning.one_click_generate({"plot_area_sqm": 50000.0})
    township = autoplanning.township_mixed_use_plan(project, {"township_area_sqm": 60000.0, "is_mixed_use": True})
    assert township["ok"] is True
    assert township["township_area_sqm"] == 60000.0
    assert len(township["zoning_distribution"]) >= 4
    assert len(township["sectors"]) >= 3
    assert township["master_plan_metrics"]["blended_far"] > 0
