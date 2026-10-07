"""
Integration tests for the new REST endpoints added in Aptimizer V4 and X+:
- /site-layout/refine
- /tasks
- /approvals
- /comments
- /automations
- /equipment-plan
- /utility-network-plan
- /generative-design/*
"""

import pytest


def test_site_layout_refine_stateless(api_project):
    client, pid = api_project
    body = {
        "project": {
            "plot": {
                "coordinates": [
                    {"lat": 12.9716, "lng": 77.5946},
                    {"lat": 12.9726, "lng": 77.5946},
                    {"lat": 12.9726, "lng": 77.5956},
                    {"lat": 12.9716, "lng": 77.5956}
                ]
            }
        },
        "generations": 10,
        "population": 20,
        "time_budget_s": 1.0
    }
    res = client.post("/api/site-layout/refine", json=body)
    assert res.status_code == 200
    data = res.json()
    assert "towers" in data or "reservation" in data
    assert "method" in data


def test_collaboration_tasks_endpoints(api_project):
    client, pid = api_project

    # 1. List tasks (initially empty)
    res = client.get(f"/api/projects/{pid}/tasks")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    # 2. Create task
    task_payload = {
        "title": "Verify Foundation Load Capacity",
        "description": "Cross-check against IS 6403",
        "assigned_to": "engineer@aptimizer.com",
        "role": "engineer",
        "stage": "Engineering",
        "priority": "high"
    }
    create_res = client.post(f"/api/projects/{pid}/tasks", json=task_payload)
    assert create_res.status_code == 200
    task = create_res.json()
    assert task["title"] == "Verify Foundation Load Capacity"
    tid = task["id"]

    # 3. Update task
    update_res = client.patch(f"/api/projects/{pid}/tasks/{tid}", json={"status": "completed"})
    assert update_res.status_code == 200
    assert update_res.json()["status"] == "completed"


def test_approvals_endpoints(api_project):
    client, pid = api_project

    # List approvals
    res = client.get(f"/api/projects/{pid}/approvals")
    assert res.status_code == 200
    approvals = res.json()
    assert len(approvals) == 5
    app_id = approvals[0]["id"]

    # Action: submit
    submit_res = client.post(f"/api/projects/{pid}/approvals/{app_id}/action", json={"action": "submit", "notes": "Ready for signoff"})
    assert submit_res.status_code == 200
    assert submit_res.json()["status"] == "submitted"


def test_comments_endpoints(api_project):
    client, pid = api_project

    # Create comment
    res = client.post(f"/api/projects/{pid}/comments", json={
        "content": "Check road setback radius",
        "module": "Site",
        "stage": "Site"
    })
    assert res.status_code == 200
    comment = res.json()
    assert comment["content"] == "Check road setback radius"
    cid = comment["id"]

    # Reply
    rep_res = client.post(f"/api/projects/{pid}/comments/{cid}/reply", json={"content": "Adjusted to 9m"})
    assert rep_res.status_code == 200
    assert len(rep_res.json()["replies"]) == 1


def test_equipment_and_utility_endpoints(api_project):
    client, pid = api_project

    eq_res = client.get(f"/api/projects/{pid}/equipment-plan")
    assert eq_res.status_code == 200
    assert "summary" in eq_res.json()
    assert "fleet" in eq_res.json()

    util_res = client.get(f"/api/projects/{pid}/utility-network-plan")
    assert util_res.status_code == 200
    assert "summary" in util_res.json()
    assert "networks" in util_res.json()


def test_generative_design_endpoints(api_project):
    client, pid = api_project

    fac_res = client.get(f"/api/projects/{pid}/generative-design/facades")
    assert fac_res.status_code == 200
    assert len(fac_res.json()) == 5

    # The UI alias serves the same concepts under the field names the studio cards render.
    ui_res = client.get(f"/api/projects/{pid}/generative-design/facade-options")
    assert ui_res.status_code == 200
    ui = ui_res.json()
    assert len(ui) == 5
    for f in ui:
        assert f["style_id"]
        assert f["description"]
        assert f["nano_banana_prompt"]
        assert 0 <= f["wwr_pct"] <= 60
        assert 0 < f["shgc"] < 1

    land_res = client.post(f"/api/projects/{pid}/generative-design/landscape", json={"open_space_sqm": 3500})
    assert land_res.status_code == 200
    zones = land_res.json()["zones"]
    assert len(zones) == 5
    for z in zones:
        assert "pct_of_open_space" in z
        assert "canopy_cover_pct" in z
        assert z["vegetation"]

    park_res = client.post(f"/api/projects/{pid}/generative-design/parking", json={"footprint_sqm": 2500, "layout_type": "orthogonal"})
    assert park_res.status_code == 200
    assert park_res.json()["total_capacity_bays"] > 0
    assert park_res.json()["driveway_width_m"] == 6.0


def test_automation_rules_execute(api_project):
    """The three default rules run their actions end to end: task creation, snapshot
    capture and approval invalidation all persist, not just come back in the response."""
    client, pid = api_project

    # Compliance violation creates a critical task.
    res = client.post(f"/api/projects/{pid}/automations/trigger", json={
        "trigger": "compliance_violation", "context": {"failed_rules": ["FAR cap"]}})
    assert res.status_code == 200
    executed = res.json()["actions_executed"]
    assert any("Created task" in a["result"] for a in executed)
    tasks = client.get(f"/api/projects/{pid}/tasks").json()
    assert any(t["created_by"] == "Workflow Engine" and t["priority"] == "critical" for t in tasks)

    # Stage approved captures a version snapshot. The engine labels it with the rule's
    # label_prefix and the trigger name, so it is distinguishable from the certified
    # snapshot the approval endpoint writes directly.
    res = client.post(f"/api/projects/{pid}/automations/trigger", json={
        "trigger": "stage_approved", "context": {"stage": "Design"}})
    assert any("Captured snapshot" in a["result"] for a in res.json()["actions_executed"])
    versions = client.get(f"/api/projects/{pid}/versions").json()
    assert any("stage_approved" in v["label"] for v in versions)

    # Layout change invalidates every approval gate signed downstream of the layout.
    for stage in ("Engineering", "Cost & BOQ"):
        gates = client.get(f"/api/projects/{pid}/approvals").json()
        gate = next(g for g in gates if g["stage"] == stage)
        client.post(f"/api/projects/{pid}/approvals/{gate['id']}/action",
                    json={"action": "submit", "notes": "t"})
        client.post(f"/api/projects/{pid}/approvals/{gate['id']}/action",
                    json={"action": "approve", "notes": "t"})
    res = client.post(f"/api/projects/{pid}/automations/trigger", json={
        "trigger": "layout_updated", "context": {"source": "test"}})
    assert any("reset:" in a["result"] for a in res.json()["actions_executed"])
    gates = client.get(f"/api/projects/{pid}/approvals").json()
    for stage in ("Engineering", "Cost & BOQ", "Deliver"):
        gate = next(g for g in gates if g["stage"] == stage)
        assert gate["status"] == "draft"
        assert gate["stamp"] is None
    # Only the gates that were actually signed carry an invalidation entry — a reset
    # must not fabricate audit history for a gate nobody ever touched.
    for stage in ("Engineering", "Cost & BOQ"):
        gate = next(g for g in gates if g["stage"] == stage)
        assert gate["history"][-1]["action"] == "invalidated"


def test_automation_compliance_check_endpoint(api_project):
    """The live check recomputes the real compliance verdict and only fires on failure."""
    client, pid = api_project
    res = client.post(f"/api/projects/{pid}/automation-events/compliance-check")
    assert res.status_code == 200
    data = res.json()
    assert data["triggered"] == "compliance_violation"
    assert "compliance" in data
    assert "failed" in data["compliance"]


def test_approval_approve_fires_automation_hook(api_project):
    """Signing a gate runs the stage_approved rule through the automation engine."""
    client, pid = api_project
    gates = client.get(f"/api/projects/{pid}/approvals").json()
    gate = next(g for g in gates if g["stage"] == "Design")
    client.post(f"/api/projects/{pid}/approvals/{gate['id']}/action", json={"action": "submit"})
    res = client.post(f"/api/projects/{pid}/approvals/{gate['id']}/action", json={"action": "approve"})
    assert res.status_code == 200
    assert res.json()["status"] == "approved"
    # The hook captured a certified snapshot through the engine as well as the direct insert.
    versions = client.get(f"/api/projects/{pid}/versions").json()
    assert any("Certified Approval - Design" in v["label"] for v in versions)
    assert any("stage_approved" in v["label"] for v in versions)


def test_refine_accepts_layout_config(api_project):
    """Refinement honours the caller's setbacks and tower band instead of defaults."""
    client, pid = api_project
    body = {
        "project": {
            "plot": {
                "coordinates": [
                    {"lat": 12.9716, "lng": 77.5946},
                    {"lat": 12.9726, "lng": 77.5946},
                    {"lat": 12.9726, "lng": 77.5956},
                    {"lat": 12.9716, "lng": 77.5956}
                ]
            }
        },
        "generations": 5,
        "population": 12,
        "time_budget_s": 1.0,
        "config": {
            "setbacks": {"default": 4.0, "front": 5.0, "rear": 3.0, "side": 3.0},
            "towers": {"floors_min": 2, "floors_max": 8},
        },
    }
    res = client.post("/api/site-layout/refine", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") is not False
    assert data["layout_metrics"]["tower_count"] >= 0
    for t in data["towers"]:
        assert 2 <= t["floors"] <= 8

