"""HR Training & Development — training-request approval chain.

A direct adaptation of the Reimbursements chain. The default chain is
MANAGER -> HR. At submit the chain is snapshotted onto ``TrainingRequest.approval_steps``
(+ ``current_step``), resolving the MANAGER stage to the employee's reporting
manager and dropping it (with a SKIPPED marker) when there is none — falling back
to an HR gate so a request can never auto-approve silently. The state machine then
walks the snapshot stage-by-stage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import HTTPException

from app.models.hr.training_request import TrainingRequestStatus, TrainingRequestDecision


_DEFAULT_CHAIN = [
    {"approver_type": "MANAGER", "label": "Reporting Manager"},
    {"approver_type": "HR", "label": "HR"},
]

_S = TrainingRequestStatus
_VALID_TRANSITIONS = {
    _S.DRAFT: {_S.PENDING_APPROVAL, _S.CANCELLED},
    _S.PENDING_APPROVAL: {
        _S.PENDING_APPROVAL, _S.APPROVED, _S.REJECTED, _S.RETURNED, _S.CANCELLED,
    },
    _S.RETURNED: {_S.PENDING_APPROVAL, _S.CANCELLED},
    _S.APPROVED: {_S.FULFILLED, _S.CANCELLED},
    _S.FULFILLED: set(),
    _S.REJECTED: set(),
    _S.CANCELLED: set(),
}


def assert_request_transition(current: TrainingRequestStatus, next_: TrainingRequestStatus) -> None:
    if next_ not in _VALID_TRANSITIONS.get(current, set()):
        raise HTTPException(409, f"Cannot transition request from {current.value} to {next_.value}")


def build_request_steps(employee) -> List[dict]:
    """Snapshot the default chain onto a new request, resolving MANAGER -> the
    employee's reporting manager."""
    steps: List[dict] = []
    for idx, stage in enumerate(_DEFAULT_CHAIN):
        t = stage["approver_type"]
        resolved = getattr(employee, "reporting_manager_id", None) if t == "MANAGER" else None
        steps.append({
            "step": idx,
            "approver_type": t,
            "approver_user_id": str(resolved) if resolved else None,
            "label": stage["label"],
            "decision": None,
            "decided_by_id": None,
            "decided_at": None,
            "notes": None,
        })
    return steps


def auto_skip_unresolvable(steps: List[dict], start: int = 0) -> int:
    """Advance past MANAGER stages with no resolvable approver. Marks each SKIPPED.
    Returns the new current_step index."""
    i = start
    now_iso = datetime.now(timezone.utc).isoformat()
    while i < len(steps):
        s = steps[i]
        if s["approver_type"] == "MANAGER" and not s.get("approver_user_id"):
            s["decision"] = TrainingRequestDecision.SKIPPED.value
            s["decided_at"] = now_iso
            s["notes"] = "No reporting manager configured — stage skipped"
            i += 1
            continue
        break
    return i


def can_act_on_step(user, step: dict) -> bool:
    t = step["approver_type"]
    if t == "HR":
        return bool(user.is_superuser)
    if t == "MANAGER":
        return step.get("approver_user_id") == str(user.id) or bool(user.is_superuser)
    return bool(user.is_superuser)


def mirror_request_final_columns(req) -> None:
    """Mirror the last APPROVED step into the denormalised approved_* columns."""
    steps = list(req.approval_steps or [])
    last_approved = next(
        (s for s in reversed(steps) if s.get("decision") == TrainingRequestDecision.APPROVED.value),
        None,
    )
    if last_approved:
        if last_approved.get("decided_by_id"):
            try:
                req.approved_by_id = UUID(last_approved["decided_by_id"])
            except Exception:
                pass
        if last_approved.get("decided_at"):
            try:
                req.approved_at = datetime.fromisoformat(last_approved["decided_at"])
            except Exception:
                pass
        req.approver_notes = last_approved.get("notes")
