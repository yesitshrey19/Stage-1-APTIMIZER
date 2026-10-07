"""
Collaboration & Workflow Module (Aptimizer V4)

Implements enterprise-grade collaboration capabilities:
1. Task Assignment: Project work items, assignees, priorities, stages, and status tracking.
2. Approval Workflow: Stage sign-offs (Site, Design, Engineering, Cost, Deliver) with audit trail,
   role-based gates, digital approval stamps, and rejection/revision handling.
3. Comments & Reviews: Contextual threaded reviews tagged to project stages and modules.
4. Workflow Automation: Rule-based automated triggers responding to project state changes
   (e.g., auto-flagging compliance failures, auto-recalculating on layout updates,
   and auto-generating certified snapshots upon stage approval).
"""

from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List, Optional
import hashlib
import hmac
import json

STAGES = ["Site", "Design", "Engineering", "Cost & BOQ", "Deliver"]

APPROVAL_STATUSES = ["draft", "submitted", "approved", "revisions_requested"]

TASK_PRIORITIES = ["low", "medium", "high", "critical"]
TASK_STATUSES = ["pending", "in_progress", "review", "completed"]

# Approval gates a geometry change invalidates, in workflow order. The layout feeds
# engineering, engineering feeds cost, cost feeds the deliverable — so when the site
# layout moves, everything downstream of it signed against stale numbers.
DOWNSTREAM_OF_LAYOUT = ["Engineering", "Cost & BOQ", "Deliver"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

STAMP_FIELDS = ("signoff_stage", "signer", "role", "verified_at", "approval_id", "notes")


def _stamp_key() -> bytes:
    import auth
    return auth._secret().encode("utf-8")


def sign_stamp(stamp: Dict[str, Any]) -> str:
    payload = json.dumps({k: stamp.get(k) for k in STAMP_FIELDS}, sort_keys=True, separators=(",", ":"))
    return hmac.new(_stamp_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_stamp(stamp: Dict[str, Any]) -> bool:
    """True when the stamp is unaltered since it was signed by this server."""
    sig = (stamp or {}).get("signature")
    return bool(sig) and hmac.compare_digest(sig, sign_stamp(stamp))



def make_id() -> str:
    return uuid.uuid4().hex[:12]


# -----------------------------------------------------------------------------
# 1. TASK ASSIGNMENT
# -----------------------------------------------------------------------------
class TaskManager:
    @staticmethod
    def create_task(
        title: str,
        description: str = "",
        assigned_to: str = "",
        role: str = "engineer",
        stage: str = "Design",
        priority: str = "medium",
        due_date: Optional[str] = None,
        created_by: str = ""
    ) -> Dict[str, Any]:
        return {
            "id": f"task_{make_id()}",
            "title": title.strip(),
            "description": description.strip(),
            "assigned_to": assigned_to.strip().lower(),
            "role": role,
            "stage": stage if stage in STAGES else "Design",
            "priority": priority if priority in TASK_PRIORITIES else "medium",
            "status": "pending",
            "due_date": due_date or "",
            "created_by": created_by,
            "created_by_name": created_by,
            "user_name": assigned_to.strip().lower() or "Unassigned",
            "created_at": now_iso(),
            "updated_at": now_iso()
        }

    @staticmethod
    def update_task(task: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"title", "description", "assigned_to", "role", "stage", "priority", "status", "due_date"}
        for k, v in updates.items():
            if k in allowed and v is not None:
                if k == "status" and v not in TASK_STATUSES:
                    continue
                if k == "priority" and v not in TASK_PRIORITIES:
                    continue
                task[k] = v
        task["updated_at"] = now_iso()
        return task


# -----------------------------------------------------------------------------
# 2. APPROVAL WORKFLOW
# -----------------------------------------------------------------------------
class ApprovalWorkflow:
    @staticmethod
    def initialize_stage_approvals() -> List[Dict[str, Any]]:
        """Default approval gates for each project stage."""
        return [
            {
                "id": f"app_{stage.lower().replace(' ', '_').replace('&', 'and')}",
                "stage": stage,
                "status": "draft",  # draft -> submitted -> approved / revisions_requested
                "submitted_by": None,
                "submitted_at": None,
                "reviewed_by": None,
                "reviewed_at": None,
                "reviewer_role": None,
                "stamp": None,
                "notes": "",
                "history": []
            }
            for stage in STAGES
        ]

    @staticmethod
    def submit_for_review(approval: Dict[str, Any], user_name: str, notes: str = "") -> Dict[str, Any]:
        approval["status"] = "submitted"
        approval["submitted_by"] = user_name
        approval["submitted_at"] = now_iso()
        approval["notes"] = notes
        approval["history"].append({
            "action": "submitted",
            "by": user_name,
            "at": now_iso(),
            "notes": notes
        })
        return approval

    @staticmethod
    def review_action(
        approval: Dict[str, Any],
        user_name: str,
        role: str,
        action: str,  # 'approve' or 'request_changes'
        notes: str = ""
    ) -> Dict[str, Any]:
        timestamp = now_iso()
        if action == "approve":
            approval["status"] = "approved"
            approval["reviewed_by"] = user_name
            approval["reviewed_at"] = timestamp
            approval["reviewer_role"] = role
            approval["notes"] = notes
            stamp = {
                "signoff_stage": approval["stage"],
                "signer": user_name,
                "role": role,
                "verified_at": timestamp,
                "approval_id": approval.get("id"),
                "notes": notes,
            }
            # A real signature: HMAC-SHA256 over the stamp with the server's signing key, so a
            # certificate can be checked with verify_stamp() and any edit to it is detectable.
            # (It used to be a random id with nothing behind it.)
            stamp["signature"] = sign_stamp(stamp)
            stamp["certificate_id"] = f"CERT-{stamp['signature'][:8].upper()}"
            approval["stamp"] = stamp
            approval["history"].append({
                "action": "approved",
                "by": user_name,
                "role": role,
                "at": timestamp,
                "notes": notes,
                "certificate_id": approval["stamp"]["certificate_id"]
            })
        else:
            approval["status"] = "revisions_requested"
            approval["reviewed_by"] = user_name
            approval["reviewed_at"] = timestamp
            approval["reviewer_role"] = role
            approval["notes"] = notes
            approval["history"].append({
                "action": "revisions_requested",
                "by": user_name,
                "role": role,
                "at": timestamp,
                "notes": notes
            })
        return approval


# -----------------------------------------------------------------------------
# 3. COMMENTS & REVIEWS
# -----------------------------------------------------------------------------
class CommentManager:
    @staticmethod
    def create_comment(
        author_name: str,
        author_email: str,
        content: str,
        module: str = "General",
        stage: str = "Design",
        target_ref: str = ""
    ) -> Dict[str, Any]:
        return {
            "id": f"cmt_{make_id()}",
            "author_name": author_name,
            # `user_name` is what the thread UI renders; kept alongside the canonical
            # author_name so either field name finds the author.
            "user_name": author_name,
            "author_email": author_email.lower(),
            "content": content.strip(),
            "module": module,
            "stage": stage,
            "target_ref": target_ref,
            "resolved": False,
            "resolved_by": None,
            "resolved_at": None,
            "created_at": now_iso(),
            "replies": []
        }

    @staticmethod
    def add_reply(comment: Dict[str, Any], author_name: str, content: str) -> Dict[str, Any]:
        reply = {
            "id": f"rep_{make_id()}",
            "author_name": author_name,
            "user_name": author_name,
            "content": content.strip(),
            "created_at": now_iso()
        }
        comment["replies"].append(reply)
        return comment

    @staticmethod
    def set_resolved(comment: Dict[str, Any], resolved: bool, user_name: str) -> Dict[str, Any]:
        comment["resolved"] = resolved
        if resolved:
            comment["resolved_by"] = user_name
            comment["resolved_at"] = now_iso()
        else:
            comment["resolved_by"] = None
            comment["resolved_at"] = None
        return comment


# -----------------------------------------------------------------------------
# 4. WORKFLOW AUTOMATION
# -----------------------------------------------------------------------------
class AutomationEngine:
    DEFAULT_RULES = [
        {
            "id": "rule_compliance_guard",
            "name": "Compliance Failure Alert",
            "description": "Auto-create a high-priority engineering review task when statutory FAR or setback limits are violated.",
            "trigger": "compliance_violation",
            "enabled": True,
            "action": "create_task",
            "action_params": {
                "title": "Resolve Statutory Compliance Margin Warning",
                "priority": "critical",
                "role": "engineer"
            }
        },
        {
            "id": "rule_auto_snapshot",
            "name": "Approved Stage Snapshot",
            "description": "Automatically capture an immutable project version snapshot whenever a stage approval gate is signed off.",
            "trigger": "stage_approved",
            "enabled": True,
            "action": "create_snapshot",
            "action_params": {
                "label_prefix": "Certified Approval"
            }
        },
        {
            "id": "rule_layout_invalidate",
            "name": "Layout Propagation Guard",
            "description": "Flag engineering and BOQ calculations for re-verification when tower footprints change, and reset every approval gate signed against the old geometry.",
            "trigger": "layout_updated",
            "enabled": True,
            "action": "invalidate_approvals",
            "action_params": {
                "message": "Site layout geometry changed; quantities and programme schedules queued for re-verification.",
                "stages": DOWNSTREAM_OF_LAYOUT
            }
        }
    ]

    @staticmethod
    def evaluate_triggers(
        trigger_name: str,
        context: Dict[str, Any],
        active_rules: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        rules = active_rules if active_rules is not None else AutomationEngine.DEFAULT_RULES
        actions_to_execute = []
        for rule in rules:
            if rule.get("enabled", True) and rule.get("trigger") == trigger_name:
                actions_to_execute.append({
                    "rule_id": rule["id"],
                    "rule_name": rule["name"],
                    "action": rule["action"],
                    "params": rule.get("action_params", {}),
                    "context": context,
                    "triggered_at": now_iso()
                })
        return actions_to_execute

    @staticmethod
    def invalidate_approvals(approvals: List[Dict[str, Any]], stages: List[str]) -> List[Dict[str, Any]]:
        """Reset the named gates to draft and record why, so no approval survives a
        geometry change it was never computed against.

        Only gates not already in draft are touched — a reset that rewrites an untouched
        gate would fabricate history entries for decisions nobody made.
        """
        for gate in approvals:
            if gate.get("stage") not in stages:
                continue
            if gate.get("status") == "draft" and not gate.get("history"):
                continue
            previous = gate.get("status")
            gate["status"] = "draft"
            gate["submitted_by"] = None
            gate["submitted_at"] = None
            gate["reviewed_by"] = None
            gate["reviewed_at"] = None
            gate["reviewer_role"] = None
            gate["stamp"] = None
            gate.setdefault("history", []).append({
                "action": "invalidated",
                "by": "Workflow Engine",
                "role": "automation",
                "at": now_iso(),
                "notes": "Auto-invalidated: site layout geometry changed after this gate was signed.",
                "previous_status": previous,
            })
        return approvals
