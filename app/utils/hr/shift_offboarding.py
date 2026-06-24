"""Offboarding hook — when an employee gives notice or EXITs, no shift
assignment may extend past their last working day.

* An assignment that begins **entirely after** the last working day is a future
  deployment the employee will never work → it is removed.
* An assignment that spans (or open-ends) past the last working day is **capped**
  so it ends on the last working day.
* Assignments that already ended on/before the last working day are historical
  and left untouched.

Once windows are capped to the last working day, the employee naturally drops
off the "active today / upcoming" board and the dashboard counts (which filter
by date window) — no separate lifecycle filter is needed there.

Fully guarded: any failure is swallowed so it can never break the lifecycle
transition. Adds to the session but does NOT commit — the caller's transaction
commits.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.hr.shift import EmployeeShiftAssignment


def close_shift_assignments_on_separation(
    db: Session,
    employee,
    actor_user_id: Optional[UUID] = None,  # noqa: ARG001 — kept for signature parity
) -> dict:
    """Cap / remove an employee's shift assignments at their last working day.

    Returns ``{"capped": n, "removed": n}``. Never raises.
    """
    result = {"capped": 0, "removed": 0}
    try:
        lwd = getattr(employee, "last_working_date", None)
        if not lwd:
            return result
        rows = (
            db.query(EmployeeShiftAssignment)
            .filter(EmployeeShiftAssignment.employee_id == employee.id)
            .all()
        )
        for a in rows:
            if a.effective_from > lwd:
                # Starts entirely after the last working day → never worked.
                db.delete(a)
                result["removed"] += 1
            elif a.effective_until is None or a.effective_until > lwd:
                # Open-ended or runs past the last working day → cap it.
                a.effective_until = lwd
                result["capped"] += 1
        db.flush()
    except Exception:  # noqa: BLE001 — never break the lifecycle transition
        import traceback
        traceback.print_exc()
    return result
