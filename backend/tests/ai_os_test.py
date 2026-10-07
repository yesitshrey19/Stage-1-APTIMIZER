"""Tests for Aptimizer X: AI Civil Engineering Operating System.

Every figure must come from the project: its engine results, its activity log or a re-run
of the engine. Nothing here may return the same invented history for every project.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_os as aioslib
import autonomous_planning as autoplanning


@pytest.fixture
def sample_project():
    return autoplanning.one_click_generate({"plot_area_sqm": 12000.0, "floors": 12})


def test_build_knowledge_graph_reads_the_project(sample_project):
    res = aioslib.build_knowledge_graph(sample_project)
    assert res["ok"] is True
    assert any(n["id"] == "CODE-IS456" for n in res["nodes"])
    zone = next(n for n in res["nodes"] if n["id"] == "PARAM-ZONE")["label"]
    assert "Zone II" in zone or "Zone III" in zone   # from the project city, not a fixed "Zone IV"


ACTIVITY = [
    {"at": "2026-10-01T10:00:00", "user_id": "u1", "user_name": "Asha", "action": "project.created", "detail": "Tower A"},
    {"at": "2026-10-02T10:00:00", "user_id": "u2", "user_name": "Ravi", "action": "approval.approve", "detail": "Site - approve"},
]


def test_engineering_memory_is_the_activity_log(sample_project):
    assert aioslib.get_engineering_memory(sample_project)["total_episodes"] == 0
    res = aioslib.get_engineering_memory(sample_project, ACTIVITY)
    assert res["total_episodes"] == 2
    assert [e["author"] for e in res["episodes"]] == ["Asha", "Ravi"]


def test_decision_log_is_a_hash_chain(sample_project):
    res = aioslib.audit_decision_log(sample_project, ACTIVITY)
    assert "APT-DEC-LOG-" in res["audit_hash"]
    assert res["total_decisions"] == 2
    tampered = [dict(ACTIVITY[0], detail="Tower B"), ACTIVITY[1]]
    other = aioslib.audit_decision_log(sample_project, tampered)
    # Editing the first entry changes every hash after it.
    assert other["decisions"][1]["entry_hash"] != res["decisions"][1]["entry_hash"]


def test_simulate_decision_sandbox_reruns_the_engine(sample_project):
    res = aioslib.simulate_decision_sandbox(sample_project, {
        "floors": 16, "concrete_grade": "M40", "slab_type": "PT Flat Slab", "footprint_scale": 1.05})
    assert res["ok"] is True
    assert res["simulation"]["floors"] == 16
    assert res["simulation"]["concrete_grade"] == "M40"
    assert res["simulation"]["quantities"]["concrete_m3"] > 0
    assert res["simulation"]["quantities"]["steel_reinforcement_mt"] > 0
    assert res["deltas"]["builtup_area_sqm"] > 0
    assert res["notes"]    # PT slabs are not modelled, and it says so


def test_compare_benchmarks_uses_the_take_off(sample_project):
    import engine
    res = aioslib.compare_benchmarks(sample_project)
    steel = next(b for b in res["benchmarks"] if b["metric"] == "Reinforcement Steel")
    vs = engine.analyse(sample_project)["quantities"]["takeoff"]["vs_thumb_rule"]
    assert steel["project_value"] == vs["steel_kg_per_sqm"]
    cost = next(b for b in res["benchmarks"] if b["metric"] == "Construction Cost")
    assert cost["status"] == "NO BENCHMARK"   # no unsourced cost benchmark
