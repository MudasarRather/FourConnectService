"""Central employee-lifecycle write guards — one source of truth.

Every HR module that commits something *to* an employee should funnel the
eligibility decision through here so the rule is identical everywhere and there
is no loophole. The backend is the authoritative boundary; the frontend also
filters pickers, but server-side enforcement is what actually protects the data.

Policy (``LifecycleState``):

* ``EMPLOYABLE`` = ACTIVE, ON_PROBATION
    May receive brand-new, forward-looking commitments — a new asset allocation,
    training, induction, a welcome kit, a promotion / transfer, a NEW ERP login,
    a future leave / shift / travel.

* ON_NOTICE — still employed and on payroll, but leaving.
    NO new forward-looking commitments, and nothing dated *after* the last
    working day. BUT dues and settlement stay open: reimbursement of past
    expenses, final compensation, payroll adjustments, F&F, asset RETURN,
    clearance, exit interview.

* SUSPENDED — employed but access frozen. Treated like "not employable" for new
    commitments (no new assets / training / access); stays on payroll.

* INACTIVE / EXITED / ARCHIVED — no longer an active employee. Block everything
    new; only settlement closure / reversals / record corrections remain.

Usage::

    from app.utils.hr.lifecycle_guard import guard_employable, guard_within_tenure
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    guard_employable(emp, "allocate an asset to this employee")
"""
from datetime import date as _date
from typing import Optional

from fastapi import HTTPException

from app.models.hr.employee import Employee, LifecycleState

# ── state sets ───────────────────────────────────────────────────────────────
# may receive brand-new forward-looking commitments
EMPLOYABLE = (LifecycleState.ACTIVE, LifecycleState.ON_PROBATION)
# still drawing salary / eligible for dues + settlement (ON_NOTICE included)
ON_PAYROLL = (LifecycleState.ACTIVE, LifecycleState.ON_PROBATION, LifecycleState.ON_NOTICE)
# no longer an active employee at all
SEPARATED = (LifecycleState.EXITED, LifecycleState.ARCHIVED, LifecycleState.INACTIVE)
# leaving or left — anything dated past the last working day is invalid
LEAVING_OR_GONE = (LifecycleState.ON_NOTICE,) + SEPARATED


def _label(emp: Employee) -> str:
    return getattr(emp, "employee_id", None) or getattr(emp, "employee_code", None) or "this employee"


def _state_value(emp: Employee) -> str:
    s = emp.lifecycle_state
    return s.value if hasattr(s, "value") else str(s)


def _reason(emp: Employee) -> str:
    s = emp.lifecycle_state
    return {
        LifecycleState.ON_NOTICE: "is serving notice (leaving)",
        LifecycleState.SUSPENDED: "is suspended",
        LifecycleState.EXITED: "has exited",
        LifecycleState.ARCHIVED: "is archived",
        LifecycleState.INACTIVE: "is inactive",
    }.get(s, f"is {_state_value(emp)}")


def guard_employable(emp: Optional[Employee], action: str = "perform this action") -> Employee:
    """Block a NEW forward-looking commitment unless the employee is ACTIVE/ON_PROBATION.

    Raises 404 if ``emp`` is falsy, 409 otherwise. Returns ``emp`` for chaining.
    """
    if emp is None:
        raise HTTPException(404, "Employee not found")
    if emp.lifecycle_state not in EMPLOYABLE:
        raise HTTPException(
            409,
            f"Cannot {action}: {_label(emp)} {_reason(emp)}. "
            f"Only active employees can receive new assignments.",
        )
    return emp


def guard_on_payroll(emp: Optional[Employee], action: str = "perform this action") -> Employee:
    """Block unless the employee is currently ON PAYROLL (ACTIVE / ON_PROBATION /
    ON_NOTICE). Allows notice-period staff — they may still legitimately incur an
    in-tenure commitment — but blocks SUSPENDED and the fully separated. Use for a
    forward commitment a notice-period employee can still validly hold, e.g.
    approving or raising a travel request.
    """
    if emp is None:
        raise HTTPException(404, "Employee not found")
    if emp.lifecycle_state not in ON_PAYROLL:
        raise HTTPException(
            409,
            f"Cannot {action}: {_label(emp)} {_reason(emp)}; "
            f"only employees currently on payroll qualify.",
        )
    return emp


def guard_settleable(emp: Optional[Employee], action: str = "settle this") -> Employee:
    """Allow dues / settlement actions for ON_NOTICE too; block only the fully
    separated (EXITED / ARCHIVED / INACTIVE). Use for reimbursement, payroll,
    final compensation, F&F-style closures.
    """
    if emp is None:
        raise HTTPException(404, "Employee not found")
    if emp.lifecycle_state in SEPARATED:
        raise HTTPException(
            409,
            f"Cannot {action}: {_label(emp)} {_reason(emp)}; the record is closed for new entries.",
        )
    return emp


def guard_within_tenure(emp: Optional[Employee], the_date, action: str = "schedule this") -> Employee:
    """For a leaving / departed employee, block anything dated *after* the last
    working day. No-op for active employees (who have no last_working_date).

    ``the_date`` may be a ``date`` or ISO string; falsy values are ignored.
    """
    if emp is None:
        raise HTTPException(404, "Employee not found")
    lwd = getattr(emp, "last_working_date", None)
    if not lwd or not the_date or emp.lifecycle_state not in LEAVING_OR_GONE:
        return emp
    d = the_date
    if isinstance(d, str):
        try:
            d = _date.fromisoformat(d[:10])
        except ValueError:
            return emp
    if d > lwd:
        raise HTTPException(
            409,
            f"Cannot {action} on {d.isoformat()}: it is after {_label(emp)}'s "
            f"last working day ({lwd.isoformat()}).",
        )
    return emp


def guard_schedulable(emp: Optional[Employee], the_date, action: str = "schedule this") -> Employee:
    """Combined guard for placing an employee on a future-dated roster / shift.

    The fully-separated (EXITED / ARCHIVED / INACTIVE) are blocked outright — they
    are no longer an active employee, regardless of any stale (even future-dated)
    last_working_date. A leaving employee (ON_NOTICE) may still be scheduled, but
    not past their last working day. ACTIVE / ON_PROBATION pass freely.
    """
    if emp is None:
        raise HTTPException(404, "Employee not found")
    if emp.lifecycle_state in SEPARATED:
        raise HTTPException(
            409,
            f"Cannot {action}: {_label(emp)} {_reason(emp)}; they are no longer an active employee.",
        )
    return guard_within_tenure(emp, the_date, action)


def is_employable(emp: Optional[Employee]) -> bool:
    """Boolean form for non-raising checks (e.g. filtering)."""
    return bool(emp) and emp.lifecycle_state in EMPLOYABLE


def travel_approval_block_reason(emp: Optional[Employee], the_date=None) -> Optional[str]:
    """Non-raising mirror of ``guard_on_payroll`` + ``guard_within_tenure`` for the
    travel-approval path. Returns a human-readable reason when an APPROVE should be
    refused, else ``None``.

    The frontend reads this off the request response to disable the Approve action
    (and explain why) *before* the approver clicks — the raising guards in
    ``flow.apply_decision`` remain the authoritative enforcement, so the two can
    never drift. ``the_date`` is the trip date to test (typically return_date or
    departure_date); may be a ``date`` or ISO string.
    """
    if emp is None:
        return None
    state = emp.lifecycle_state
    # Not on payroll → no new commitment can be approved (SUSPENDED + all SEPARATED).
    if state not in ON_PAYROLL:
        return {
            LifecycleState.SUSPENDED: "This traveller is suspended — approval is frozen until they are reinstated.",
            LifecycleState.EXITED: "This traveller has exited the organisation, so the request can no longer be approved.",
            LifecycleState.ARCHIVED: "This traveller's record is archived, so the request can no longer be approved.",
            LifecycleState.INACTIVE: "This traveller is inactive, so the request can no longer be approved.",
        }.get(state, f"This traveller is {_state_value(emp)}, so the request can no longer be approved.")
    # On payroll but leaving (ON_NOTICE) — block a trip dated past the last working day.
    lwd = getattr(emp, "last_working_date", None)
    if lwd and the_date and state in LEAVING_OR_GONE:
        d = the_date
        if isinstance(d, str):
            try:
                d = _date.fromisoformat(d[:10])
            except ValueError:
                d = None
        if d and d > lwd:
            return (f"The trip ends {d.strftime('%d %b %Y')}, after this traveller's last "
                    f"working day ({lwd.strftime('%d %b %Y')}), so it can no longer be approved.")
    return None
