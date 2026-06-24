"""Travel offboarding — void an exiting employee's still-open travel requests.

Called from the EXIT lifecycle transition (employees.py) alongside the asset and
shift offboarding hooks. Only OPEN, pre-commitment requests are cancelled
(DRAFT / PENDING_APPROVAL / RETURNED) — these otherwise linger un-approvable in
the approval queues (the on-payroll guard in flow.apply_decision already blocks
their approval). APPROVED / IN_PROGRESS / COMPLETED trips are deliberately left
alone so booked or executing travel and its advances / DA aren't silently voided —
those belong to the travel settlement / manual-cancel flow. Caller wraps this in a
guarded try/except; it must never block the exit.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.hr.travel_request import TravelRequest
from app.models.hr.travel_type import TravelRequestStatus, TravelAuditAction
from app.utils.hr.travel.service import write_travel_audit

# Open, not-yet-committed states — safe to auto-void on exit.
_OPEN_STATES = (
    TravelRequestStatus.DRAFT,
    TravelRequestStatus.PENDING_APPROVAL,
    TravelRequestStatus.RETURNED,
)


def cancel_open_travel_on_separation(db: Session, employee, actor_id) -> int:
    """Cancel the employee's open (uncommitted) travel requests. Returns the count.

    Does NOT commit — the caller commits as part of the exit transaction.
    """
    reqs = (
        db.query(TravelRequest)
        .filter(
            TravelRequest.employee_id == employee.id,
            TravelRequest.is_deleted == False,  # noqa: E712
            TravelRequest.status.in_(_OPEN_STATES),
        )
        .all()
    )
    n = 0
    for req in reqs:
        from_status = req.status.value
        req.status = TravelRequestStatus.CANCELLED
        req.cancelled_at = datetime.now(timezone.utc)
        req.cancelled_by_id = actor_id
        req.cancelled_reason = "Auto-cancelled — employee exited"
        write_travel_audit(
            db, entity_type="REQUEST", entity_id=req.id, travel_request_id=req.id,
            action=TravelAuditAction.CANCEL, actor_id=actor_id,
            from_status=from_status, to_status=req.status.value,
            note="Auto-cancelled on exit (employee separated)",
        )
        n += 1
    return n
