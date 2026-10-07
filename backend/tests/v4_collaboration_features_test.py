"""
Tests for Aptimizer V4 Collaboration & Workflow Features:
- Task Assignment
- Approval Workflow
- Comments & Reviews
- Workflow Automation
"""

import pytest
from collaboration import TaskManager, ApprovalWorkflow, CommentManager, AutomationEngine


def test_task_lifecycle():
    task = TaskManager.create_task(
        title="Check Fire Staircase Width",
        description="Verify against NBC 2016 Part 4 Table 7",
        assigned_to="engineer@aptimizer.com",
        role="engineer",
        stage="Engineering",
        priority="high",
        due_date="2026-10-15",
        created_by="Lead Architect"
    )
    assert task["id"].startswith("task_")
    assert task["status"] == "pending"
    assert task["priority"] == "high"
    assert task["stage"] == "Engineering"

    # Update task
    updated = TaskManager.update_task(task, {"status": "in_progress", "priority": "critical"})
    assert updated["status"] == "in_progress"
    assert updated["priority"] == "critical"

    # Complete task
    completed = TaskManager.update_task(updated, {"status": "completed"})
    assert completed["status"] == "completed"


def test_approval_workflow():
    gates = ApprovalWorkflow.initialize_stage_approvals()
    assert len(gates) == 5
    site_gate = next(g for g in gates if g["stage"] == "Site")
    assert site_gate["status"] == "draft"

    # Submit for review
    submitted = ApprovalWorkflow.submit_for_review(site_gate, "Civil Planner", "Setbacks verified with local authority")
    assert submitted["status"] == "submitted"
    assert submitted["submitted_by"] == "Civil Planner"
    assert len(submitted["history"]) == 1

    # Request changes
    rev_requested = ApprovalWorkflow.review_action(submitted, "Chief Engineer", "admin", "request_changes", "Provide rear road buffer")
    assert rev_requested["status"] == "revisions_requested"
    assert rev_requested["reviewed_by"] == "Chief Engineer"

    # Re-submit and approve
    re_submitted = ApprovalWorkflow.submit_for_review(rev_requested, "Civil Planner", "Buffer added")
    approved = ApprovalWorkflow.review_action(re_submitted, "Chief Engineer", "admin", "approve", "Approved for statutory submission")
    assert approved["status"] == "approved"
    assert approved["stamp"] is not None
    assert approved["stamp"]["certificate_id"].startswith("CERT-")
    assert approved["stamp"]["signer"] == "Chief Engineer"


def test_comments_and_reviews():
    comment = CommentManager.create_comment(
        author_name="Structural Lead",
        author_email="lead@aptimizer.com",
        content="Increase column C1-C4 size to 450x600 for seismic Zone IV",
        module="Engineering",
        stage="Engineering"
    )
    assert comment["id"].startswith("cmt_")
    assert not comment["resolved"]

    # Add reply
    replied = CommentManager.add_reply(comment, "Junior Engineer", "Updated column schedules accordingly")
    assert len(replied["replies"]) == 1
    assert replied["replies"][0]["author_name"] == "Junior Engineer"

    # Resolve comment
    resolved = CommentManager.set_resolved(replied, True, "Structural Lead")
    assert resolved["resolved"]
    assert resolved["resolved_by"] == "Structural Lead"


def test_workflow_automation_triggers():
    # Trigger compliance violation
    actions = AutomationEngine.evaluate_triggers("compliance_violation", {"margin": -0.15, "code": "FAR_EXCEEDED"})
    assert len(actions) >= 1
    rule_act = actions[0]
    assert rule_act["action"] == "create_task"
    assert rule_act["params"]["priority"] == "critical"

    # Trigger stage approved
    snapshot_actions = AutomationEngine.evaluate_triggers("stage_approved", {"stage": "Engineering"})
    assert any(a["action"] == "create_snapshot" for a in snapshot_actions)
