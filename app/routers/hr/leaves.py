"""HR Leave & Absence — main router.

Two-tier approval flow: Manager (employee.reporting_manager_id) → HR (any
superadmin; falls back to that when employee.hr_manager_id is null).

Endpoint surface:
  Self-service:    /me, /me/balance, /me/balance/{fy}, POST /me, DELETE /me/{id}
  Manager:         /manager/queue, PATCH /manager/{id}/decide
  HR / admin:      /, /hr/queue, /stats, /balances, /policies, /calendar,
                   POST / (admin override), POST /balances/{eid}/adjust,
                   PATCH /hr/{id}/decide, PATCH /policies/{type},
                   POST /bulk-decide, GET /{id}, DELETE /{id},
                   GET /{id}/history
  Cron:            POST /cron/accrue-monthly, POST /cron/carry-forward

When a leave lands APPROVED, the router debits balance via _apply_ledger and
re-runs daily_rollup for each covered date so Attendance.status flips to
LEAVE synchronously. The same is reversed on admin-driven cancellation.
"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from math import ceil
from typing import Optional, Dict, List, Tuple
from uuid import UUID

import os
import uuid as _uuid_mod

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile, status as http_status
from sqlalchemy import or_, and_, func as sa_func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.database import get_db
from app.models.user import User
from app.models.system_setting import SystemSetting
from app.models.notification import Notification
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.designation import Designation
from app.models.hr.holiday import Holiday, HolidayType
from app.models.hr.attendance_log import AttendanceLogAction
from app.models.hr.leave_type import (
    LeaveType, LeaveStatus, LeaveSession, LeaveDecision, LedgerKind, EncashmentStatus,
)
from app.models.hr.leave_policy import LeavePolicy
from app.models.hr.leave_request import LeaveRequest
from app.models.hr.leave_proof_attachment import LeaveProofAttachment
from app.models.hr.leave_balance import LeaveBalance
from app.models.hr.leave_balance_history import LeaveBalanceHistory
from app.models.hr.leave_encashment import LeaveEncashment
from app.schemas.hr.leave import (
    LeavePolicyResponse, LeavePolicyListResponse, LeavePolicyUpdate,
    LeavePolicyCreate, LeavePolicyUsage, LeavePolicyDeleteBody,
    LeaveRequestCreate, LeaveRequestAdminCreate,
    LeaveDecisionBody, LeaveBulkDecideBody, LeaveWithdrawBody, LeaveDeleteBody,
    LeaveLapseBody, LeaveGrantPolicyBody,
    LeaveProofRequestBody, LeaveProofAttachmentResponse, ProofDeleteBody,
    LeaveDayBreakdown, LeaveRequestResponse, LeaveRequestListResponse,
    LeaveStats, LeaveTypeCount,
    LeaveBalanceResponse, LeaveBalanceListResponse, LeaveBalanceAdjustBody,
    LeaveHistoryResponse, LeaveHistoryListResponse,
    LeaveCalendarEntry, LeaveCalendarResponse,
    AccrueMonthlyBody, CarryForwardBody, CronRunResult,
    CompOffGrantBody, CompOffEntry, CompOffListResponse, CompOffStats,
    CompOffImpact, CompOffDeleteBody, EncashmentOption,
    EncashmentPreviewBody, EncashmentPreviewResponse, EncashmentCreateBody,
    EncashmentAdminCreateBody, EncashmentDecisionBody, EncashmentManagerDecideBody, EncashmentPayBody,
    EncashmentResponse, EncashmentListResponse, EncashmentStats,
    LeaveReportInfo, LeaveReportIndexResponse, LeaveReportPreview,
    LeaveAuditEntry, LeaveAuditListResponse,
)
from app.models.hr.attendance_log import AttendanceLog
from app.utils.hr import leave_reports
from fastapi.responses import Response
from app.utils.dependencies import get_current_user, get_current_superuser
from app.utils.hr.attendance_logic import daily_rollup, log, resolve_shift
from app.utils.hr.lifecycle_guard import (
    guard_within_tenure, guard_employable, guard_settleable, SEPARATED, is_employable,
)
from app.models.hr.employee import LifecycleState


router = APIRouter(prefix="/hr/leaves", tags=["HR — Leave & Absence"])


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _resolve_self_employee(db: Session, user: User) -> Employee:
    emp = db.query(Employee).filter(
        Employee.user_id == user.id,
        Employee.is_deleted == False,  # noqa: E712
    ).first()
    if not emp:
        raise HTTPException(404, "No employee profile linked to your account")
    return emp


def _try_self_employee(db: Session, user: User) -> Optional[Employee]:
    """Soft lookup — returns None for users not linked to an Employee row.

    Use this on read-only self-service endpoints (`GET /me`, `/me/balance`,
    `/me/comp-off`, etc.) so they can return an empty payload with
    ``unlinked=True`` instead of 404'ing every page load when a User account
    hasn't been paired with an Employee yet. Mutating endpoints still call
    ``_resolve_self_employee`` so they fail loudly.
    """
    return db.query(Employee).filter(
        Employee.user_id == user.id,
        Employee.is_deleted == False,  # noqa: E712
    ).first()


def _get_setting(db: Session, key: str, default: str) -> str:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return row.value if row else default


def _put_setting(db: Session, key: str, value: str, description: Optional[str] = None) -> None:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value, description=description))


def _fy_for(on_date: date, fy_start: str = "04-01") -> str:
    """Return fiscal year label for a date. fy_start = 'MM-DD' (default '04-01').

    Example: 2026-04-01 → "2026-27"; 2026-03-31 → "2025-26".
    """
    try:
        mm, dd = (int(x) for x in fy_start.split("-"))
    except Exception:
        mm, dd = 4, 1
    boundary = date(on_date.year, mm, dd)
    start_year = on_date.year if on_date >= boundary else on_date.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def _current_fy(db: Session) -> str:
    return _fy_for(date.today(), _get_setting(db, "fiscal_year_start", "04-01"))


def _fy_bounds(fy: str, fy_start: str = "04-01") -> Tuple[date, date]:
    """Return (start_date, end_date_inclusive) for a fiscal-year label."""
    try:
        mm, dd = (int(x) for x in fy_start.split("-"))
    except Exception:
        mm, dd = 4, 1
    start_year = int(fy.split("-")[0])
    start = date(start_year, mm, dd)
    end = date(start_year + 1, mm, dd) - timedelta(days=1)
    return start, end


def _generate_reference_no(db: Session, fy: str) -> str:
    """LR-{YY}-{6digit}. Counter lives in system_settings.leave_ref_counter.
    Retries on uniqueness violation up to 5 times.
    """
    yy = fy.split("-")[0][-2:]
    for _ in range(6):
        row = db.query(SystemSetting).filter(SystemSetting.key == "leave_ref_counter").first()
        if row:
            try:
                n = int(row.value) + 1
            except Exception:
                n = 1
            row.value = str(n)
        else:
            n = 1
            db.add(SystemSetting(key="leave_ref_counter", value="1",
                                 description="Monotonic counter for LeaveRequest.reference_no"))
        db.flush()
        candidate = f"LR-{yy}-{n:06d}"
        exists = db.query(LeaveRequest.id).filter(LeaveRequest.reference_no == candidate).first()
        if not exists:
            return candidate
    raise HTTPException(500, "Could not allocate leave reference number")


# Statuses that block an overlapping leave from being filed. Terminal/rejected
# states (REJECTED, MANAGER_REJECTED, CANCELLED, WITHDRAWN) are deliberately
# excluded — if a prior request was rejected or withdrawn, the employee should
# be free to re-file for the same dates.
_OVERLAP_BLOCKING_STATUSES = (
    LeaveStatus.DRAFT,
    LeaveStatus.PENDING_MANAGER,
    LeaveStatus.PENDING_HR,
    LeaveStatus.APPROVED,
)


def _assert_no_overlapping_leave(
    db: Session,
    employee_id: UUID,
    from_d: date,
    to_d: date,
    *,
    is_half_day: bool = False,
    which_session: Optional[LeaveSession] = None,
    exclude_id: Optional[UUID] = None,
) -> None:
    """Reject when the employee already has a non-terminal leave that overlaps
    the requested range.

    Two half-day leaves on the *same* single date are allowed only when they
    cover the opposite sessions (FIRST + SECOND); any other overlap raises a
    409. This is the single source of truth — both `/me` and the admin override
    endpoint route through here so the bug where two leaves can cover the same
    day is closed off in both code paths.
    """
    q = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.is_deleted == False,  # noqa: E712
        LeaveRequest.status.in_(_OVERLAP_BLOCKING_STATUSES),
        # Standard date-range overlap predicate: existing.from <= new.to AND existing.to >= new.from
        LeaveRequest.from_date <= to_d,
        LeaveRequest.to_date >= from_d,
    )
    if exclude_id is not None:
        q = q.filter(LeaveRequest.id != exclude_id)

    for existing in q.all():
        # Allow complementary half-days on the same single date.
        same_single_date = (
            from_d == to_d
            and existing.from_date == existing.to_date
            and existing.from_date == from_d
        )
        if (
            same_single_date
            and is_half_day
            and existing.is_half_day
            and which_session
            and existing.which_session
            and which_session != existing.which_session
        ):
            continue

        # Build a helpful, readable error.
        if existing.from_date == existing.to_date:
            range_str = existing.from_date.isoformat()
        else:
            range_str = f"{existing.from_date.isoformat()} → {existing.to_date.isoformat()}"
        status_lbl = existing.status.value.replace("_", " ").title()
        raise HTTPException(
            409,
            (
                f"You already have a {existing.leave_type.value} leave "
                f"({existing.reference_no}, {status_lbl}) covering {range_str}. "
                "Withdraw or cancel that request before applying again for the same dates."
            ),
        )


def _holidays_in_range(db: Session, from_d: date, to_d: date, work_location_id: Optional[UUID]) -> Dict[date, str]:
    """Map of date → holiday_name for active non-RESTRICTED holidays in range
    that apply to the employee's location.
    """
    rows = db.query(Holiday).filter(
        Holiday.date >= from_d,
        Holiday.date <= to_d,
        Holiday.is_active == True,        # noqa: E712
        Holiday.is_deleted == False,      # noqa: E712
        Holiday.holiday_type != HolidayType.RESTRICTED,
    ).all()
    out: Dict[date, str] = {}
    for h in rows:
        if h.location_id is None or h.location_id == work_location_id:
            out[h.date] = h.name
    return out


def _day_breakdown_for(
    db: Session, employee: Employee, from_d: date, to_d: date,
    is_half_day: bool, which_session: Optional[LeaveSession], policy: LeavePolicy,
) -> List[LeaveDayBreakdown]:
    """Per-date holiday/week-off/paid classification.

    Rule: a day costs balance (`is_paid=True`) only if it is a true working day
    for this employee — neither a public holiday nor their shift's weekly off.
    Off-days are ALWAYS excluded from balance debit regardless of the legacy
    `policy.count_holidays_weekoffs` flag.

    The shift is resolved PER-DAY so a mid-range EmployeeShiftAssignment change
    is reflected — e.g. moving from a Sun-only off shift to a Sat+Sun off shift
    on July 1 will pick up the new weekly_off_days for July 1+ days within the
    same leave request. A small in-call cache keys by shift.id so a stable
    shift assignment doesn't re-hit the DB on every iteration.
    """
    holidays = _holidays_in_range(db, from_d, to_d, employee.work_location_id)

    # Cache: shift.id -> weekly_off set. resolve_shift itself is cheap (one
    # indexed query) but a long range with a stable shift would still issue
    # N queries; this brings it down to 1.
    _weekly_off_cache: Dict[Optional[UUID], set] = {}

    def _weekly_off_on(d_iter: date) -> set:
        shift = resolve_shift(db, employee.id, d_iter)
        key = shift.id if shift else None
        cached = _weekly_off_cache.get(key)
        if cached is not None:
            return cached
        off = set(shift.weekly_off_days or []) if shift else {5, 6}
        _weekly_off_cache[key] = off
        return off

    out: List[LeaveDayBreakdown] = []
    d = from_d
    while d <= to_d:
        weekly_off = _weekly_off_on(d)
        is_holiday = d in holidays
        is_week_off = d.weekday() in weekly_off
        # Off-days never cost balance — the employee was already off that day.
        is_paid = not (is_holiday or is_week_off)
        out.append(LeaveDayBreakdown(
            on_date=d,
            is_holiday=is_holiday,
            is_week_off=is_week_off,
            is_paid=is_paid,
            is_half_day=(is_half_day and d == from_d and from_d == to_d),
            which_session=(which_session if is_half_day and d == from_d and from_d == to_d else None),
            holiday_name=holidays.get(d),
        ))
        d += timedelta(days=1)
    return out


def _count_working_days(breakdown: List[LeaveDayBreakdown]) -> int:
    """Count days that are neither a public holiday nor the employee's week-off."""
    return sum(1 for r in breakdown if not r.is_holiday and not r.is_week_off)


def _describe_off_day(row: LeaveDayBreakdown) -> str:
    """Human-readable label for why a day is an off-day."""
    if row.is_holiday:
        return f"a holiday ({row.holiday_name})" if row.holiday_name else "a public holiday"
    if row.is_week_off:
        return "your weekly off"
    return "an off-day"


def _assert_has_working_day(
    db: Session, employee: Employee, from_d: date, to_d: date,
    is_half_day: bool, which_session: Optional[LeaveSession], policy: LeavePolicy,
) -> List[LeaveDayBreakdown]:
    """Reject a leave request only when the range contains ZERO working days.

    Rule: off-days at the start, end, or middle of a range are LEGAL — they
    simply don't count toward the deduction (see `_day_breakdown_for` /
    `_compute_total_and_fy`). For example, a request 14-Aug (week-off) →
    17-Aug with 16-Aug as an admin holiday is accepted and debits 2 days
    (15-Aug + 17-Aug). The only invalid case is a range with no working day
    at all — there's nothing to apply leave for.

    Returns the computed breakdown so callers can reuse it without re-querying.
    """
    breakdown = _day_breakdown_for(db, employee, from_d, to_d, is_half_day, which_session, policy)
    if not breakdown:
        return breakdown

    if _count_working_days(breakdown) == 0:
        holiday_names = list(dict.fromkeys(
            r.holiday_name for r in breakdown if r.is_holiday and r.holiday_name
        ))
        suffix = ""
        if holiday_names:
            suffix = f" Holidays in range: {', '.join(holiday_names[:3])}{'…' if len(holiday_names) > 3 else ''}."
        raise HTTPException(
            422,
            "Selected dates fall entirely on weekends/holidays — pick at least one working day to apply leave." + suffix,
        )
    return breakdown


def _compute_total_and_fy(
    db: Session, employee: Employee, from_d: date, to_d: date,
    is_half_day: bool, which_session: Optional[LeaveSession], policy: LeavePolicy,
) -> Tuple[Decimal, Dict[str, float], List[LeaveDayBreakdown]]:
    """Return (total_days, fy_breakdown, day_breakdown)."""
    breakdown = _day_breakdown_for(db, employee, from_d, to_d, is_half_day, which_session, policy)
    fy_start = _get_setting(db, "fiscal_year_start", "04-01")

    total = Decimal("0")
    per_fy: Dict[str, Decimal] = {}
    for row in breakdown:
        if not row.is_paid:
            continue
        weight = Decimal("0.5") if row.is_half_day else Decimal("1.0")
        total += weight
        fy = _fy_for(row.on_date, fy_start)
        per_fy[fy] = per_fy.get(fy, Decimal("0")) + weight
    fy_breakdown = {k: float(v) for k, v in per_fy.items()}
    return total, fy_breakdown, breakdown


def _get_or_create_balance(
    db: Session, employee_id: UUID, leave_type: LeaveType, fy: str,
) -> LeaveBalance:
    row = db.query(LeaveBalance).filter(
        LeaveBalance.employee_id == employee_id,
        LeaveBalance.leave_type == leave_type,
        LeaveBalance.fiscal_year == fy,
        LeaveBalance.is_deleted == False,  # noqa: E712
    ).first()
    if row:
        return row
    row = LeaveBalance(
        employee_id=employee_id, leave_type=leave_type, fiscal_year=fy,
        opening_balance=Decimal("0"), accrued=Decimal("0"),
        carry_forward_in=Decimal("0"), used=Decimal("0"),
        encashed=Decimal("0"), adjustments=Decimal("0"),
        closing_balance=Decimal("0"),
    )
    db.add(row)
    db.flush()
    return row


def _recompute_closing(b: LeaveBalance) -> Decimal:
    return (
        Decimal(b.opening_balance or 0)
        + Decimal(b.accrued or 0)
        + Decimal(b.carry_forward_in or 0)
        + Decimal(b.adjustments or 0)
        - Decimal(b.used or 0)
        - Decimal(b.encashed or 0)
    )


def _apply_ledger(
    db: Session, balance: LeaveBalance, *,
    kind: LedgerKind, delta: Decimal, actor: Optional[User],
    note: Optional[str] = None, related_request_id: Optional[UUID] = None,
) -> LeaveBalanceHistory:
    """Mutate balance + write history row in one txn. Reject negative closing
    for non-override kinds (LWP is exempt and never lands here for ACCRUAL/USE).
    """
    before = _recompute_closing(balance)

    if kind == LedgerKind.ACCRUAL:
        balance.accrued = Decimal(balance.accrued or 0) + delta
    elif kind == LedgerKind.REQUEST_APPROVED:
        # delta is negative on debit
        balance.used = Decimal(balance.used or 0) + (-delta)
    elif kind == LedgerKind.REQUEST_CANCELLED:
        # reversal: delta is positive
        balance.used = max(Decimal("0"), Decimal(balance.used or 0) - delta)
    elif kind == LedgerKind.CARRY_FORWARD:
        balance.carry_forward_in = Decimal(balance.carry_forward_in or 0) + delta
    elif kind == LedgerKind.ENCASHMENT:
        balance.encashed = Decimal(balance.encashed or 0) + (-delta)
    elif kind == LedgerKind.ADMIN_ADJUST:
        balance.adjustments = Decimal(balance.adjustments or 0) + delta
    elif kind == LedgerKind.OPENING_SEED:
        balance.opening_balance = Decimal(balance.opening_balance or 0) + delta

    after = _recompute_closing(balance)
    if after < 0 and kind not in (LedgerKind.ADMIN_ADJUST, LedgerKind.OPENING_SEED):
        raise HTTPException(422, f"Insufficient balance: closing would go to {after}")
    balance.closing_balance = after

    hist = LeaveBalanceHistory(
        employee_id=balance.employee_id,
        leave_type=balance.leave_type,
        fiscal_year=balance.fiscal_year,
        kind=kind,
        delta=delta,
        balance_before=before,
        balance_after=after,
        actor_user_id=actor.id if actor else None,
        note=note,
        related_request_id=related_request_id,
    )
    db.add(hist)
    return hist


def _emit_notifications(
    db: Session, leave: LeaveRequest, *,
    employee_user_id: Optional[UUID], event: str,
    actor: Optional[User] = None,
) -> None:
    """Best-effort: write Notification rows. Failures swallowed in caller."""
    def _add(user_id: Optional[UUID], type_: str, message: str):
        if not user_id:
            return
        db.add(Notification(
            user_id=user_id, type=type_, title="Leave update",
            message=message, related_user_id=actor.id if actor else None,
            action_url=f"/admin/hr/leave/applications#{leave.reference_no}",
            is_read=False,
        ))

    ref = leave.reference_no
    type_label = leave.leave_type.value.replace("_", " ").title()
    range_label = leave.from_date.isoformat() if leave.from_date == leave.to_date else f"{leave.from_date} → {leave.to_date}"

    if event == "submitted":
        _add(employee_user_id, "leave_requested",
             f"{ref} · {type_label} {range_label} submitted")
        _add(leave.manager_id, "leave_manager_pending",
             f"{ref} awaiting your decision · {type_label}")
    elif event == "manager_approved":
        _add(employee_user_id, "leave_manager_pending",
             f"{ref} manager-approved; awaiting HR")
    elif event == "manager_rejected":
        _add(employee_user_id, "leave_rejected",
             f"{ref} declined by manager")
    elif event == "hr_approved":
        _add(employee_user_id, "leave_approved",
             f"{ref} approved · enjoy your time off")
    elif event == "hr_rejected":
        _add(employee_user_id, "leave_rejected",
             f"{ref} declined by HR")
    elif event == "withdrawn":
        _add(leave.manager_id, "leave_withdrawn",
             f"{ref} withdrawn by employee")
    elif event == "admin_override":
        _add(employee_user_id, "leave_approved",
             f"{ref} entered by HR · approved")
    elif event == "lapsed":
        _add(employee_user_id, "leave_rejected",
             f"{ref} · {type_label} {range_label} closed as lapsed (dates passed, not approved in time)")
    elif event == "proof_requested":
        # HR has asked the employee for supporting documents.
        if employee_user_id:
            db.add(Notification(
                user_id=employee_user_id,
                type="leave_proof_requested",
                title="Action needed — proof requested",
                message=f"HR needs supporting documents for your {type_label} leave `{ref}`",
                related_user_id=actor.id if actor else None,
                action_url="/user/self-service/leave",
                is_read=False,
            ))
    elif event == "proof_submitted":
        # Employee uploaded the first proof — ping the HR user who asked.
        target = leave.proof_requested_by_id
        if target:
            actor_name = _user_name(db, actor.id) if actor else None
            display_name = actor_name or "Employee"
            db.add(Notification(
                user_id=target,
                type="leave_proof_submitted",
                title="Proof submitted",
                message=f"{display_name} uploaded proof for `{ref}`",
                related_user_id=actor.id if actor else None,
                action_url="/admin/hr/leave/hr-queue",
                is_read=False,
            ))


_VALID_TRANSITIONS = {
    LeaveStatus.DRAFT: {LeaveStatus.PENDING_MANAGER, LeaveStatus.CANCELLED, LeaveStatus.LAPSED},
    LeaveStatus.PENDING_MANAGER: {LeaveStatus.PENDING_HR, LeaveStatus.MANAGER_REJECTED, LeaveStatus.WITHDRAWN, LeaveStatus.CANCELLED, LeaveStatus.LAPSED},
    LeaveStatus.PENDING_HR: {LeaveStatus.APPROVED, LeaveStatus.REJECTED, LeaveStatus.CANCELLED, LeaveStatus.LAPSED},
    LeaveStatus.APPROVED: {LeaveStatus.CANCELLED},
    LeaveStatus.REJECTED: set(),
    LeaveStatus.MANAGER_REJECTED: set(),
    LeaveStatus.CANCELLED: set(),
    LeaveStatus.WITHDRAWN: set(),
    LeaveStatus.LAPSED: set(),
}


def _assert_transition(current: LeaveStatus, next_: LeaveStatus) -> None:
    if next_ not in _VALID_TRANSITIONS.get(current, set()):
        raise HTTPException(409, f"Cannot transition leave from {current.value} to {next_.value}")


def _guard_not_past(leave: LeaveRequest, action: str = "approve this leave") -> None:
    """Block APPROVING a leave whose dates have already elapsed. A leave that was
    never actioned before its dates passed can't be approved (the days are gone);
    the manager / HR must close it as LAPSED with a remark instead. Rejections are
    never gated here — they must always be able to clear the queue."""
    if leave.to_date and leave.to_date < date.today():
        raise HTTPException(
            409,
            f"Cannot {action}: the leave ended on {leave.to_date.isoformat()} (already in the past). "
            f"It can no longer be approved — close it as lapsed with a remark instead.",
        )


# Pending stages a leave can be closed-out (lapsed) from.
_LAPSABLE_STATUSES = (LeaveStatus.DRAFT, LeaveStatus.PENDING_MANAGER, LeaveStatus.PENDING_HR)


# ─── Phase 4 — configurable approval chain ───
#
# A leave policy can store a custom chain of approval stages. When unset,
# the legacy two-tier `[MANAGER, HR]` flow applies. At submit time the chain
# is snapshotted onto the LeaveRequest (manager_id resolved, USER stages
# bound to their named user). The state machine then walks through the
# snapshot stage-by-stage. The router exposes a generic `/chain/{id}/decide`
# endpoint for USER stages while keeping the legacy `/manager/{id}/decide`
# and `/hr/{id}/decide` endpoints working for the default two-tier flow.

_DEFAULT_CHAIN: List[dict] = [
    {"approver_type": "MANAGER", "approver_user_id": None, "label": "Reporting Manager"},
    {"approver_type": "HR",      "approver_user_id": None, "label": "HR"},
]

_VALID_APPROVER_TYPES = {"MANAGER", "HR", "USER"}


def _normalize_chain_config(chain: Optional[List[dict]]) -> List[dict]:
    """Return a sanitized chain config (policy's chain or the default)."""
    if not chain:
        return [dict(s) for s in _DEFAULT_CHAIN]
    out: List[dict] = []
    for s in chain:
        t = (s.get("approver_type") or "MANAGER").upper()
        if t not in _VALID_APPROVER_TYPES:
            t = "MANAGER"
        out.append({
            "approver_type": t,
            "approver_user_id": s.get("approver_user_id"),
            "label": s.get("label") or {
                "MANAGER": "Reporting Manager", "HR": "HR", "USER": "Approver",
            }[t],
        })
    return out or [dict(s) for s in _DEFAULT_CHAIN]


def _build_request_steps(chain_cfg: List[dict], employee: Employee) -> List[dict]:
    """Snapshot chain config onto a new LeaveRequest. Resolves MANAGER and
    USER stages to concrete user ids; HR stays None (any superuser may act).
    """
    steps: List[dict] = []
    for i, stage in enumerate(chain_cfg):
        t = stage["approver_type"]
        resolved = None
        if t == "MANAGER":
            resolved = employee.reporting_manager_id
        elif t == "USER":
            resolved = stage.get("approver_user_id")
        steps.append({
            "step": i,
            "approver_type": t,
            "approver_user_id": str(resolved) if resolved else None,
            "label": stage["label"],
            "decision": None,
            "decided_by_id": None,
            "decided_at": None,
            "notes": None,
        })
    return steps


def _step_status(steps: List[dict], idx: int) -> LeaveStatus:
    """Map the current step into the externally-visible LeaveStatus enum.
    Two-tier-compatible: MANAGER stages report PENDING_MANAGER, anything else
    reports PENDING_HR. (Frontend reads approval_steps[current_step].label for
    the precise human-facing string.)
    """
    if idx >= len(steps):
        return LeaveStatus.APPROVED
    t = steps[idx]["approver_type"]
    if t == "MANAGER":
        return LeaveStatus.PENDING_MANAGER
    return LeaveStatus.PENDING_HR


def _auto_skip_unresolvable(steps: List[dict], start: int = 0) -> int:
    """Advance past stages that can't be resolved (e.g. MANAGER stage when
    the employee has no reporting_manager_id). Marks each as SKIPPED. Returns
    the new current_step index.
    """
    i = start
    now_iso = datetime.now(timezone.utc).isoformat()
    while i < len(steps):
        s = steps[i]
        if s["approver_type"] == "MANAGER" and not s["approver_user_id"]:
            s["decision"] = LeaveDecision.SKIPPED.value
            s["decided_at"] = now_iso
            s["notes"] = "No reporting manager configured — stage skipped"
            i += 1
            continue
        break
    return i


def _mirror_legacy_columns(leave: LeaveRequest) -> None:
    """Mirror approval_steps state into the legacy manager_*/hr_* columns so
    existing frontends and queue endpoints continue to work for the default
    two-tier chain.
      manager_* ← first MANAGER stage (if any)
      hr_*      ← last HR stage that has a decision (if any)
    """
    steps = list(leave.approval_steps or [])
    if not steps:
        return

    first_mgr = next((s for s in steps if s["approver_type"] == "MANAGER"), None)
    if first_mgr:
        leave.manager_id = UUID(first_mgr["approver_user_id"]) if first_mgr.get("approver_user_id") else leave.manager_id
        if first_mgr.get("decision"):
            leave.manager_decision = LeaveDecision(first_mgr["decision"])
            if first_mgr.get("decided_at"):
                leave.manager_decided_at = datetime.fromisoformat(first_mgr["decided_at"])
            leave.manager_notes = first_mgr.get("notes")

    last_hr = next((s for s in reversed(steps) if s["approver_type"] == "HR" and s.get("decision")), None)
    if last_hr:
        if last_hr.get("decided_by_id"):
            leave.hr_id = UUID(last_hr["decided_by_id"])
        leave.hr_decision = LeaveDecision(last_hr["decision"])
        if last_hr.get("decided_at"):
            leave.hr_decided_at = datetime.fromisoformat(last_hr["decided_at"])
        leave.hr_notes = last_hr.get("notes")


def _can_act_on_step(user: User, step: dict) -> bool:
    """Permission check for a single approval stage."""
    t = step["approver_type"]
    if t == "HR":
        return bool(user.is_superuser)
    if t == "MANAGER":
        return step.get("approver_user_id") == str(user.id)
    if t == "USER":
        # Named approver, or a superuser acting as fallback
        return step.get("approver_user_id") == str(user.id) or bool(user.is_superuser)
    return False


def _has_legacy_steps(leave: LeaveRequest) -> bool:
    """True when this request was created before Phase 4 — no snapshot exists."""
    return not (leave.approval_steps and len(leave.approval_steps) > 0)


def _get_my_team_employee_ids(db: Session, user_id: UUID) -> List[UUID]:
    """All employee_ids whose reporting_manager_id == this user."""
    rows = db.query(Employee.id).filter(
        Employee.reporting_manager_id == user_id,
        Employee.is_deleted == False,  # noqa: E712
    ).all()
    return [r[0] for r in rows]


def _policy_or_404(db: Session, leave_type: LeaveType) -> LeavePolicy:
    p = db.query(LeavePolicy).filter(
        LeavePolicy.leave_type == leave_type,
        LeavePolicy.is_deleted == False,  # noqa: E712
    ).first()
    if not p:
        raise HTTPException(404, f"No policy configured for {leave_type.value}")
    return p


def _employee_snapshot(db: Session, employee_id: UUID) -> dict:
    """Joined snapshot used by every response builder."""
    snap = (
        db.query(
            Employee.id, Employee.employee_id.label("code"),
            User.full_name.label("name"), User.email.label("email"),
            Department.name.label("dept"), Designation.name.label("desg"),
            Employee.work_location_id, Employee.reporting_manager_id, Employee.hr_manager_id,
            Employee.lifecycle_state.label("lifecycle"),
        )
        .join(User, User.id == Employee.user_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .outerjoin(Designation, Designation.id == Employee.designation_id)
        .filter(Employee.id == employee_id)
        .first()
    )
    if not snap:
        return {}
    return {
        "name": snap.name, "code": snap.code, "email": snap.email,
        "dept": snap.dept, "desg": snap.desg,
        "work_location_id": snap.work_location_id,
        "reporting_manager_id": snap.reporting_manager_id,
        "hr_manager_id": snap.hr_manager_id,
        "lifecycle_state": snap.lifecycle.value if snap.lifecycle else None,
    }


def _user_name(db: Session, user_id: Optional[UUID]) -> Optional[str]:
    if not user_id:
        return None
    row = db.query(User.full_name).filter(User.id == user_id).first()
    return row[0] if row else None


def _enrich_steps_with_names(db: Session, steps: List[dict]) -> List[dict]:
    """Resolve approver_user_id and decided_by_id to display names so the
    frontend doesn't render bare UUIDs in the chain pipeline."""
    if not steps:
        return steps
    uids = set()
    for s in steps:
        for k in ("approver_user_id", "decided_by_id"):
            v = s.get(k)
            if v:
                uids.add(v)
    if not uids:
        return [dict(s) for s in steps]
    # Single bulk lookup
    try:
        uuid_objs = [UUID(u) for u in uids]
    except Exception:
        return [dict(s) for s in steps]
    rows = db.query(User.id, User.full_name).filter(User.id.in_(uuid_objs)).all()
    name_by_id = {str(r[0]): r[1] for r in rows}
    out: List[dict] = []
    for s in steps:
        e = dict(s)
        if e.get("approver_user_id") and not e.get("approver_name"):
            e["approver_name"] = name_by_id.get(e["approver_user_id"])
        if e.get("decided_by_id") and not e.get("decided_by_name"):
            e["decided_by_name"] = name_by_id.get(e["decided_by_id"])
        out.append(e)
    return out


def _fetch_proof_attachments(db: Session, leave_id: UUID) -> List[LeaveProofAttachment]:
    """Fresh query for the active proof uploads on a leave, newest first.

    Uses a direct query rather than the `proof_attachments_rel` relationship so
    each call sees the current DB state (the relationship caches results inside
    the Session and would stale-read after deletes inside the same request)."""
    return (
        db.query(LeaveProofAttachment)
        .filter(
            LeaveProofAttachment.leave_request_id == leave_id,
            LeaveProofAttachment.is_deleted == False,  # noqa: E712
        )
        .order_by(LeaveProofAttachment.uploaded_at.desc())
        .all()
    )


def _to_response(
    db: Session, leave: LeaveRequest, *,
    include_breakdown: bool = False,
) -> LeaveRequestResponse:
    snap = _employee_snapshot(db, leave.employee_id)
    day_breakdown = None
    if include_breakdown:
        policy = db.query(LeavePolicy).filter(LeavePolicy.leave_type == leave.leave_type).first()
        if policy:
            emp = db.query(Employee).filter(Employee.id == leave.employee_id).first()
            if emp:
                day_breakdown = _day_breakdown_for(
                    db, emp, leave.from_date, leave.to_date,
                    leave.is_half_day, leave.which_session, policy,
                )
    proof_rows = _fetch_proof_attachments(db, leave.id)
    proof_attachments = [LeaveProofAttachmentResponse.model_validate(r) for r in proof_rows]
    return LeaveRequestResponse(
        id=leave.id, reference_no=leave.reference_no,
        employee_id=leave.employee_id,
        employee_name=snap.get("name"), employee_code=snap.get("code"),
        department_name=snap.get("dept"), designation_name=snap.get("desg"),
        leave_type=leave.leave_type,
        from_date=leave.from_date, to_date=leave.to_date,
        total_days=Decimal(leave.total_days or 0),
        is_half_day=bool(leave.is_half_day), which_session=leave.which_session,
        fy_breakdown=leave.fy_breakdown or {},
        reason=leave.reason, attachment_id=leave.attachment_id,
        proof_requested=bool(getattr(leave, "proof_requested", False)),
        proof_requested_at=getattr(leave, "proof_requested_at", None),
        proof_request_note=getattr(leave, "proof_request_note", None),
        proof_submitted_at=getattr(leave, "proof_submitted_at", None),
        proof_attachments=proof_attachments,
        proof_attachment_count=len(proof_attachments),
        contact_during_leave=leave.contact_during_leave,
        emergency_contact=leave.emergency_contact,
        status=leave.status,
        manager_id=leave.manager_id, manager_name=_user_name(db, leave.manager_id),
        manager_decision=leave.manager_decision,
        manager_decided_at=leave.manager_decided_at, manager_notes=leave.manager_notes,
        hr_id=leave.hr_id, hr_name=_user_name(db, leave.hr_id),
        hr_decision=leave.hr_decision,
        hr_decided_at=leave.hr_decided_at, hr_notes=leave.hr_notes,
        cancelled_at=leave.cancelled_at, cancelled_reason=leave.cancelled_reason,
        is_admin_override=bool(leave.is_admin_override),
        approval_steps=_enrich_steps_with_names(db, list(leave.approval_steps or [])),
        current_step=int(leave.current_step or 0),
        day_breakdown=day_breakdown,
        created_at=leave.created_at,
    )


def _balance_to_response(
    db: Session, b: LeaveBalance, policy: Optional[LeavePolicy] = None,
    snap: Optional[dict] = None,
) -> LeaveBalanceResponse:
    # `snap` lets callers pass a pre-fetched employee snapshot to avoid an
    # N+1 lookup when building many rows for the same employee.
    if snap is None:
        snap = _employee_snapshot(db, b.employee_id)
    quota = Decimal(policy.annual_quota) if policy else Decimal("0")
    available = Decimal(b.closing_balance or 0)
    used = Decimal(b.used or 0)
    util = float((used / quota) * 100) if quota and quota > 0 else 0.0
    return LeaveBalanceResponse(
        id=b.id, employee_id=b.employee_id,
        employee_name=snap.get("name"), employee_code=snap.get("code"),
        department_name=snap.get("dept"), lifecycle_state=snap.get("lifecycle_state"),
        leave_type=b.leave_type, fiscal_year=b.fiscal_year,
        opening_balance=Decimal(b.opening_balance or 0),
        accrued=Decimal(b.accrued or 0),
        carry_forward_in=Decimal(b.carry_forward_in or 0),
        used=used, encashed=Decimal(b.encashed or 0),
        adjustments=Decimal(b.adjustments or 0),
        closing_balance=available,
        available=available, quota=quota,
        monthly_accrual=(Decimal(policy.monthly_accrual or 0) if policy else Decimal("0")),
        utilisation_pct=round(util, 1),
    )


def _rollup_leave_dates(db: Session, leave: LeaveRequest, actor_id: UUID) -> None:
    """Re-run daily_rollup for every date covered by the leave so Attendance
    statuses reflect the new state. Best-effort; swallows individual failures."""
    d = leave.from_date
    while d <= leave.to_date:
        try:
            daily_rollup(db, leave.employee_id, d, actor_id=actor_id)
        except Exception:
            pass
        d += timedelta(days=1)


def _unwind_leave_effects(db: Session, leave: LeaveRequest, actor: User, *, was_approved: bool) -> None:
    """Undo the side-effects of a leave that is being rejected / withdrawn.

    If the leave had reached APPROVED, its balance debit is reversed; in EVERY
    case daily_rollup is re-run across the covered dates so any stale `LEAVE`
    attendance rows flip back to what the punches / holiday / week-off imply.
    This is the mirror of the delete handler and closes the gap where a
    previously-approved leave stayed marked LEAVE in attendance after rejection.
    Idempotent and best-effort — must never block the decision it follows.

    GATED on `was_approved`: a leave that never reached APPROVED has no debited
    balance and never stamped any LEAVE attendance row, so there is nothing to
    unwind. Re-running daily_rollup in that case would only fabricate ABSENT
    rows for the (possibly future) covered dates — the very recompute anti-pattern
    we avoid. So when the leave was never approved this is a deliberate no-op.
    """
    if not was_approved:
        return
    if leave.leave_type != LeaveType.LWP:
        for fy_label, days in (leave.fy_breakdown or {}).items():
            try:
                b = _get_or_create_balance(db, leave.employee_id, leave.leave_type, fy_label)
                _apply_ledger(
                    db, b, kind=LedgerKind.REQUEST_CANCELLED,
                    delta=Decimal(str(float(days))), actor=actor,
                    note=f"Reversed on rejection {leave.reference_no}",
                    related_request_id=leave.id,
                )
            except HTTPException:
                pass
        db.commit()
    _rollup_leave_dates(db, leave, actor_id=actor.id)
    db.commit()


# ═════════════════════════════════════════════════════════════════════════════
# Self-service endpoints
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/me", response_model=LeaveRequestListResponse)
def my_leaves(
    status_filter: Optional[LeaveStatus] = Query(None, alias="status"),
    leave_type: Optional[LeaveType] = None,
    fy: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _try_self_employee(db, user)
    if not emp:
        # User account isn't paired with an HR Employee row — return an
        # empty list with the unlinked flag so the page renders a calm
        # banner instead of toast-spamming a 404.
        return LeaveRequestListResponse(
            items=[], total=0, page=page, limit=limit, total_pages=1, unlinked=True,
        )
    q = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == emp.id,
        LeaveRequest.is_deleted == False,  # noqa: E712
    )
    if status_filter:
        q = q.filter(LeaveRequest.status == status_filter)
    if leave_type:
        q = q.filter(LeaveRequest.leave_type == leave_type)
    if fy:
        f_start, f_end = _fy_bounds(fy, _get_setting(db, "fiscal_year_start", "04-01"))
        q = q.filter(LeaveRequest.from_date >= f_start, LeaveRequest.from_date <= f_end)
    total = q.count()
    rows = (
        q.order_by(LeaveRequest.created_at.desc())
         .offset((page - 1) * limit).limit(limit).all()
    )
    return LeaveRequestListResponse(
        items=[_to_response(db, r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=max(1, ceil(total / limit) if limit else 1),
    )


@router.get("/me/balance", response_model=LeaveBalanceListResponse)
def my_balance(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _try_self_employee(db, user)
    fy = _current_fy(db)
    if not emp:
        return LeaveBalanceListResponse(items=[], total=0, fiscal_year=fy, unlinked=True)
    return _balance_list(db, employee_id=emp.id, fy=fy)


@router.get("/me/balance/{fy}", response_model=LeaveBalanceListResponse)
def my_balance_for_fy(
    fy: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _try_self_employee(db, user)
    if not emp:
        return LeaveBalanceListResponse(items=[], total=0, fiscal_year=fy, unlinked=True)
    return _balance_list(db, employee_id=emp.id, fy=fy)


def _balance_list(db: Session, *, employee_id: UUID, fy: str) -> LeaveBalanceListResponse:
    """Returns one row per leave type (creating missing rows on the fly)."""
    policies = db.query(LeavePolicy).filter(
        LeavePolicy.is_deleted == False,  # noqa: E712
        LeavePolicy.is_active == True,  # noqa: E712
    ).all()
    policy_by_type = {p.leave_type: p for p in policies}
    items: List[LeaveBalanceResponse] = []
    for lt in LeaveType:
        p = policy_by_type.get(lt)
        if not p:
            continue
        b = _get_or_create_balance(db, employee_id, lt, fy)
        items.append(_balance_to_response(db, b, p))
    db.commit()
    return LeaveBalanceListResponse(items=items, total=len(items), fiscal_year=fy)


@router.get("/me/preview")
def my_leave_preview(
    leave_type: LeaveType = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    is_half_day: bool = Query(False),
    which_session: Optional[LeaveSession] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Live preview for the apply-leave wizard.

    Returns the per-day classification (holiday / week-off / paid), the count
    of true working days in the range, and the total billable days under the
    selected policy. The wizard uses this to:
      * Show 'X working days · Y days will be debited'
      * Block the Continue/Submit button when working_days == 0
      * Surface the holiday names that caused a rejection

    No state is mutated. Returns 422 on impossible ranges (to_date < from_date,
    half-day spanning >1 date) so the wizard can echo the message.
    """
    emp = _resolve_self_employee(db, user)
    policy = _policy_or_404(db, leave_type)

    if to_date < from_date:
        raise HTTPException(422, "to_date must be on or after from_date")
    if is_half_day:
        if from_date != to_date:
            raise HTTPException(422, "Half-day leave must be a single date")
        if not which_session:
            raise HTTPException(422, "Half-day leave requires `which_session` (FIRST|SECOND)")

    breakdown = _day_breakdown_for(db, emp, from_date, to_date, is_half_day, which_session, policy)
    working_days = _count_working_days(breakdown)
    total_days, fy_breakdown, _ = _compute_total_and_fy(
        db, emp, from_date, to_date, is_half_day, which_session, policy,
    )
    holiday_names = list(dict.fromkeys(
        r.holiday_name for r in breakdown if r.is_holiday and r.holiday_name
    ))
    # Fetch the richer holiday metadata (type + source) for every date in
    # range so the wizard can show a clear tooltip on each off-day chip —
    # "Milad-un-Nabi · National holiday (imported)" rather than just
    # "Aug 25 — off". The classification (is_holiday) already came from
    # _day_breakdown_for; this is only the meta lookup.
    _holiday_meta: Dict[date, dict] = {}
    if any(r.is_holiday for r in breakdown):
        rows = db.query(Holiday).filter(
            Holiday.date >= from_date,
            Holiday.date <= to_date,
            Holiday.is_active == True,        # noqa: E712
            Holiday.is_deleted == False,      # noqa: E712
            Holiday.holiday_type != HolidayType.RESTRICTED,
        ).all()
        for h in rows:
            if h.location_id is None or h.location_id == emp.work_location_id:
                _holiday_meta[h.date] = {
                    "name": h.name,
                    "type": h.holiday_type.value if h.holiday_type else None,
                    "source": getattr(h, "source", "manual"),
                }
    all_off = working_days == 0

    block_reason = None
    if all_off:
        suffix = f" Holidays in range: {', '.join(holiday_names[:3])}{'…' if len(holiday_names) > 3 else ''}." if holiday_names else ""
        block_reason = "Selected dates fall entirely on weekends/holidays — pick at least one working day to apply leave." + suffix

    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "working_days": working_days,
        "off_days": len(breakdown) - working_days,
        # total_days now equals working_days (in calendar units) since off-days
        # are excluded unconditionally. Half-day weight applied inside.
        "total_days": float(total_days),
        "fy_breakdown": fy_breakdown,
        "all_off_days": all_off,
        # Compound flag the wizard uses to disable the Continue button — only
        # blocked when the range has zero working days. Off-days at the start,
        # end, or middle are LEGAL; they just don't count.
        "blocked": all_off,
        "block_reason": block_reason,
        "has_off_day_only": all_off,   # kept for back-compat with prior wizard build
        "holiday_names": holiday_names,
        "breakdown": [
            {
                "on_date": r.on_date.isoformat(),
                "is_holiday": r.is_holiday,
                "is_week_off": r.is_week_off,
                "is_paid": r.is_paid,
                "is_half_day": r.is_half_day,
                "which_session": r.which_session.value if r.which_session else None,
                "holiday_name": r.holiday_name,
                "holiday_type": _holiday_meta.get(r.on_date, {}).get("type") if r.is_holiday else None,
                "holiday_source": _holiday_meta.get(r.on_date, {}).get("source") if r.is_holiday else None,
            }
            for r in breakdown
        ],
    }


@router.post("/me", response_model=LeaveRequestResponse, status_code=http_status.HTTP_201_CREATED)
def create_my_leave(
    payload: LeaveRequestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    policy = _policy_or_404(db, payload.leave_type)

    # A leaving / departed employee may not take leave dated past their LWD.
    guard_within_tenure(emp, payload.to_date, "apply for leave")

    today = date.today()
    if payload.from_date < today:
        raise HTTPException(422, "Leave start date must be today or later")
    if policy.requires_notice_days and (payload.from_date - today).days < policy.requires_notice_days:
        raise HTTPException(422, f"{policy.leave_type.value} needs {policy.requires_notice_days} days notice")
    if policy.advance_book_days and (payload.from_date - today).days > policy.advance_book_days:
        raise HTTPException(422, f"{policy.leave_type.value} cannot be booked more than {policy.advance_book_days} days in advance")

    if payload.is_half_day:
        if payload.from_date != payload.to_date:
            raise HTTPException(422, "Half-day leave must be a single date")
        if not payload.which_session:
            raise HTTPException(422, "Half-day leave requires `which_session` (FIRST|SECOND)")

    if policy.requires_attachment and not payload.attachment_id:
        raise HTTPException(422, f"{policy.leave_type.value} requires an attachment")

    # Block overlapping requests up front (before reference allocation / balance
    # checks) so the caller gets a clean 409 instead of a partially-allocated
    # request that conflicts with an already-approved one.
    _assert_no_overlapping_leave(
        db, emp.id, payload.from_date, payload.to_date,
        is_half_day=payload.is_half_day,
        which_session=payload.which_session,
    )

    # Reject ranges that fall entirely on the employee's week-off days and/or
    # admin-approved public holidays. This runs ahead of _compute_total_and_fy
    # because for paid policies (count_holidays_weekoffs=True) the total_days <= 0
    # guard below never fires — the policy would still debit weekend/holiday days.
    _assert_has_working_day(
        db, emp, payload.from_date, payload.to_date,
        payload.is_half_day, payload.which_session, policy,
    )

    total_days, fy_breakdown, _ = _compute_total_and_fy(
        db, emp, payload.from_date, payload.to_date,
        payload.is_half_day, payload.which_session, policy,
    )
    if total_days <= 0:
        raise HTTPException(422, "Selected dates have no paid working days to debit")
    if policy.max_consecutive_days and total_days > Decimal(policy.max_consecutive_days):
        raise HTTPException(422, f"Exceeds max consecutive {policy.max_consecutive_days} days for this leave type")

    # Pre-check balance for non-LWP types
    if payload.leave_type != LeaveType.LWP:
        for fy_label, days in fy_breakdown.items():
            b = _get_or_create_balance(db, emp.id, payload.leave_type, fy_label)
            available = Decimal(b.closing_balance or 0)
            if Decimal(str(days)) > available:
                raise HTTPException(422, f"Insufficient {payload.leave_type.value} balance for {fy_label}: {available} available, {days} requested")

    fy_start = _get_setting(db, "fiscal_year_start", "04-01")
    ref = _generate_reference_no(db, _fy_for(payload.from_date, fy_start))

    # Phase 4 — snapshot the chain from policy (or the default two-tier one).
    # Self-loop guard: if the only resolvable manager is the employee themselves,
    # treat that MANAGER stage as unresolvable so it gets SKIPPED.
    chain_cfg = _normalize_chain_config(policy.approval_chain)
    snap_emp = emp
    if emp.reporting_manager_id == user.id:
        # Build a shallow stand-in so _build_request_steps resolves MANAGER to None.
        class _NoManagerEmployee:
            id = emp.id
            reporting_manager_id = None
        snap_emp = _NoManagerEmployee()
    steps = _build_request_steps(chain_cfg, snap_emp)
    cur_idx = _auto_skip_unresolvable(steps, 0)

    leave = LeaveRequest(
        reference_no=ref,
        employee_id=emp.id,
        leave_type=payload.leave_type,
        from_date=payload.from_date, to_date=payload.to_date,
        total_days=total_days,
        is_half_day=payload.is_half_day,
        which_session=payload.which_session,
        fy_breakdown=fy_breakdown,
        reason=payload.reason,
        attachment_id=payload.attachment_id,
        contact_during_leave=payload.contact_during_leave,
        emergency_contact=payload.emergency_contact,
        manager_id=emp.reporting_manager_id,
        status=_step_status(steps, cur_idx),
        approval_steps=steps,
        current_step=cur_idx,
    )
    # Mirror SKIPPED MANAGER stage into legacy columns so existing UIs show the skip.
    _mirror_legacy_columns(leave)
    db.add(leave)
    db.commit()
    db.refresh(leave)
    try:
        log(db, actor_id=user.id, action=AttendanceLogAction.LEAVE_REQUESTED,
            target_table="hr_leave_requests", target_id=leave.id, employee_id=emp.id,
            payload={"ref": ref, "type": payload.leave_type.value,
                     "from": str(payload.from_date), "to": str(payload.to_date),
                     "days": float(total_days)})
        _emit_notifications(db, leave, employee_user_id=user.id, event="submitted", actor=user)
        db.commit()
    except Exception:
        db.rollback()
    return _to_response(db, leave, include_breakdown=True)


@router.delete("/me/{leave_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def withdraw_my_leave(
    leave_id: UUID,
    body: Optional[LeaveWithdrawBody] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    leave = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not leave:
        raise HTTPException(404, "Leave request not found")
    if leave.employee_id != emp.id:
        raise HTTPException(403, "Cannot withdraw another employee's request")
    if leave.status != LeaveStatus.PENDING_MANAGER:
        raise HTTPException(409, "Only PENDING_MANAGER requests can be withdrawn — contact HR otherwise")

    leave.status = LeaveStatus.WITHDRAWN
    leave.cancelled_at = datetime.now(timezone.utc)
    leave.cancelled_by_id = user.id
    leave.cancelled_reason = (body.note if body and body.note else "Withdrawn by employee")
    db.commit()
    try:
        log(db, actor_id=user.id, action=AttendanceLogAction.LEAVE_WITHDRAWN,
            target_table="hr_leave_requests", target_id=leave.id, employee_id=emp.id,
            payload={"ref": leave.reference_no, "note": leave.cancelled_reason})
        _emit_notifications(db, leave, employee_user_id=user.id, event="withdrawn", actor=user)
        db.commit()
    except Exception:
        db.rollback()


# ═════════════════════════════════════════════════════════════════════════════
# Proof-request flow
#
# HR flags a request with proof_requested=True; the employee then uploads one
# or more files. Each upload becomes a LeaveProofAttachment row. The first
# successful upload stamps `proof_submitted_at` on the parent and pings the
# HR user who asked.
# ═════════════════════════════════════════════════════════════════════════════

_PROOF_ALLOWED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".doc", ".docx",
}
_PROOF_MAX_BYTES = 10 * 1024 * 1024  # 10 MB cap per file
_PROOF_MAX_PER_LEAVE = 10
_PROOF_STORAGE_SUBDIR = "leave-proofs"


def _proof_storage_root() -> str:
    """Absolute path to `storage/leave-proofs/` (created on demand).

    `storage/` is mounted at `/storage` by main.py StaticFiles. We mkdir-p the
    subdirectory lazily on the first upload so the path always exists by the
    time we open() into it.
    """
    backend_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    # __file__ → .../app/routers/hr/leaves.py → backend_root = .../FourConnectService
    backend_root = os.path.dirname(backend_root)
    storage_dir = os.path.join(backend_root, "storage", _PROOF_STORAGE_SUBDIR)
    os.makedirs(storage_dir, exist_ok=True)
    return storage_dir


@router.post("/{leave_id:uuid}/request-proof", response_model=LeaveRequestResponse)
def request_proof(
    leave_id: UUID,
    body: LeaveProofRequestBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """HR-only: ask the employee to upload supporting documents for this leave.

    Idempotent — repeated calls overwrite the note + bump
    `proof_requested_at`/`proof_requested_by_id` so HR can re-ping with an
    updated message. The notification fires every time.
    """
    leave = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not leave:
        raise HTTPException(404, "Leave request not found")

    leave.proof_requested = True
    leave.proof_requested_at = datetime.now(timezone.utc)
    leave.proof_requested_by_id = admin.id
    leave.proof_request_note = body.note
    db.commit()
    db.refresh(leave)

    # Notify the employee. Look up their user id via the linked Employee.
    employee_user_id: Optional[UUID] = None
    emp = db.query(Employee).filter(Employee.id == leave.employee_id).first()
    if emp:
        employee_user_id = emp.user_id
    try:
        _emit_notifications(
            db, leave,
            employee_user_id=employee_user_id,
            event="proof_requested",
            actor=admin,
        )
        db.commit()
    except Exception:
        db.rollback()

    return _to_response(db, leave)


@router.post(
    "/me/{leave_id}/proof",
    response_model=LeaveProofAttachmentResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def upload_my_proof(
    leave_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Employee uploads ONE supporting-document file for their own leave.

    Frontend calls this N times for N files. Validates extension + 10 MB cap,
    rejects if the leave already has 10 active proof rows, and on the FIRST
    successful upload also stamps `proof_submitted_at` + notifies the HR user
    who originally asked.
    """
    emp = _resolve_self_employee(db, user)

    leave = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not leave:
        raise HTTPException(404, "Leave request not found")
    if leave.employee_id != emp.id:
        raise HTTPException(403, "Cannot upload proof for another employee's leave")

    if not file.filename:
        raise HTTPException(400, "Missing filename")
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in _PROOF_ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            "Invalid file type. Allowed: PDF, images (jpg/jpeg/png/gif/webp), DOC, DOCX.",
        )

    file_content = await file.read()
    if len(file_content) > _PROOF_MAX_BYTES:
        raise HTTPException(400, "File too large. Max 10MB.")
    if not file_content:
        raise HTTPException(400, "Empty file")

    existing_count = (
        db.query(LeaveProofAttachment)
        .filter(
            LeaveProofAttachment.leave_request_id == leave.id,
            LeaveProofAttachment.is_deleted == False,  # noqa: E712
        )
        .count()
    )
    if existing_count >= _PROOF_MAX_PER_LEAVE:
        raise HTTPException(
            409,
            f"Maximum {_PROOF_MAX_PER_LEAVE} proof files allowed per leave request",
        )

    storage_dir = _proof_storage_root()
    file_uuid = _uuid_mod.uuid4()
    unique_filename = f"{file_uuid}{ext}"
    abs_path = os.path.join(storage_dir, unique_filename)
    file_url = f"/storage/{_PROOF_STORAGE_SUBDIR}/{unique_filename}"

    try:
        with open(abs_path, "wb") as f:
            f.write(file_content)
    except Exception as e:
        raise HTTPException(500, f"Save failed: {str(e)}")

    row = LeaveProofAttachment(
        leave_request_id=leave.id,
        file_url=file_url,
        file_path=abs_path,
        original_filename=file.filename[:255],
        file_size=len(file_content),
        mime_type=(file.content_type or None),
        uploaded_by_id=user.id,
    )
    db.add(row)

    # First-upload bookkeeping: stamp the parent + notify HR.
    first_upload = existing_count == 0
    if first_upload:
        leave.proof_submitted_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(row)

    if first_upload and leave.proof_requested_by_id:
        try:
            _emit_notifications(
                db, leave,
                employee_user_id=None,
                event="proof_submitted",
                actor=user,
            )
            db.commit()
        except Exception:
            db.rollback()

    return LeaveProofAttachmentResponse.model_validate(row)


@router.get("/{leave_id:uuid}/proofs")
def list_proofs(
    leave_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List active proof uploads for a leave. Superusers can read any leave;
    the owning employee can read their own. All other callers get a 403."""
    leave = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not leave:
        raise HTTPException(404, "Leave request not found")

    is_owner = False
    if not user.is_superuser:
        emp = db.query(Employee).filter(Employee.id == leave.employee_id).first()
        if emp and emp.user_id == user.id:
            is_owner = True
        if not is_owner:
            raise HTTPException(403, "Access denied")

    rows = _fetch_proof_attachments(db, leave.id)
    items = [LeaveProofAttachmentResponse.model_validate(r) for r in rows]
    return {
        "items": items,
        "total": len(items),
        "proof_requested": bool(leave.proof_requested),
        "proof_request_note": leave.proof_request_note,
        "proof_requested_at": leave.proof_requested_at.isoformat() if leave.proof_requested_at else None,
    }


@router.delete(
    "/me/{leave_id}/proof/{attachment_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
def delete_my_proof(
    leave_id: UUID,
    attachment_id: UUID,
    body: Optional[ProofDeleteBody] = Body(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Soft-delete a proof file the caller uploaded against their own leave.

    Optionally accepts a JSON body `{ reason, note }` whose contents are
    persisted to the audit log alongside the deletion event.
    """
    emp = _resolve_self_employee(db, user)

    leave = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not leave:
        raise HTTPException(404, "Leave request not found")
    if leave.employee_id != emp.id:
        raise HTTPException(403, "Cannot delete proof on another employee's leave")

    row = db.query(LeaveProofAttachment).filter(
        LeaveProofAttachment.id == attachment_id,
        LeaveProofAttachment.leave_request_id == leave.id,
        LeaveProofAttachment.is_deleted == False,  # noqa: E712
    ).first()
    if not row:
        raise HTTPException(404, "Proof attachment not found")
    if row.uploaded_by_id != user.id:
        raise HTTPException(403, "You can only delete proofs you uploaded")

    row.is_deleted = True
    db.commit()

    # Audit — best-effort. Never let a logging failure surface to the client.
    try:
        log(
            db,
            actor_id=user.id,
            action=AttendanceLogAction.LEAVE_PROOF_DELETED,
            target_table="hr_leave_proof_attachments",
            target_id=row.id,
            employee_id=emp.id,
            payload={
                "leave_ref": leave.reference_no,
                "filename": row.original_filename,
                "reason": body.reason if body else None,
                "note": body.note if body else None,
            },
        )
        db.commit()
    except Exception:
        db.rollback()


# ═════════════════════════════════════════════════════════════════════════════
# Manager queue
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/manager/queue", response_model=LeaveRequestListResponse)
def manager_queue(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Pending-manager requests where the current user is the reporting manager."""
    team_ids = _get_my_team_employee_ids(db, user.id)
    q = db.query(LeaveRequest).filter(
        LeaveRequest.is_deleted == False,  # noqa: E712
        LeaveRequest.status == LeaveStatus.PENDING_MANAGER,
        LeaveRequest.employee_id.in_(team_ids) if team_ids else False,
    )
    total = q.count()
    rows = (
        q.order_by(LeaveRequest.created_at.asc())
         .offset((page - 1) * limit).limit(limit).all()
    )
    return LeaveRequestListResponse(
        items=[_to_response(db, r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=max(1, ceil(total / limit) if limit else 1),
    )


@router.patch("/manager/{leave_id}/decide", response_model=LeaveRequestResponse)
def manager_decide(
    leave_id: UUID,
    body: LeaveDecisionBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    leave = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.is_deleted == False,  # noqa: E712
    ).with_for_update().first()
    if not leave:
        raise HTTPException(404, "Leave request not found")
    was_approved = leave.status == LeaveStatus.APPROVED
    # Verify the current user actually manages this employee
    emp = db.query(Employee).filter(Employee.id == leave.employee_id).first()
    if not emp or emp.reporting_manager_id != user.id:
        raise HTTPException(403, "You are not the reporting manager for this employee")
    # A leave whose dates have already passed can no longer be approved — close
    # it as lapsed with a remark instead (POST /{id}/lapse).
    if body.decision == LeaveDecision.APPROVED:
        _guard_not_past(leave, "approve this leave")

    # Phase 4 — chain-aware path. Legacy in-flight rows (empty approval_steps)
    # fall through to the original two-tier transitions.
    if not _has_legacy_steps(leave):
        steps = list(leave.approval_steps or [])
        idx = int(leave.current_step or 0)
        if idx >= len(steps):
            raise HTTPException(409, "Leave is fully resolved")
        cur = steps[idx]
        if cur["approver_type"] != "MANAGER":
            raise HTTPException(409, "Current approval stage is not a MANAGER stage")
        if not _can_act_on_step(user, cur):
            raise HTTPException(403, "You are not the configured approver for this stage")

        now_iso = datetime.now(timezone.utc).isoformat()
        if body.decision == LeaveDecision.APPROVED:
            cur["decision"] = LeaveDecision.APPROVED.value
            cur["decided_by_id"] = str(user.id)
            cur["decided_at"] = now_iso
            cur["notes"] = body.notes
            new_idx = _auto_skip_unresolvable(steps, idx + 1)
            leave.current_step = new_idx
            leave.status = _step_status(steps, new_idx)
        else:
            cur["decision"] = LeaveDecision.REJECTED.value
            cur["decided_by_id"] = str(user.id)
            cur["decided_at"] = now_iso
            cur["notes"] = body.notes
            leave.status = LeaveStatus.MANAGER_REJECTED
        leave.approval_steps = steps
        # SQLAlchemy doesn't auto-detect in-place dict mutation inside a JSONB
        # column. Without flag_modified the UPDATE skips approval_steps and the
        # step's decision/decided_at/notes never persist — making the row
        # reappear in /me/queue after reload even though manager_decision
        # (legacy column) was correctly set.
        flag_modified(leave, "approval_steps")
        _mirror_legacy_columns(leave)
    else:
        if body.decision == LeaveDecision.APPROVED:
            _assert_transition(leave.status, LeaveStatus.PENDING_HR)
            leave.status = LeaveStatus.PENDING_HR
            leave.manager_decision = LeaveDecision.APPROVED
        else:
            _assert_transition(leave.status, LeaveStatus.MANAGER_REJECTED)
            leave.status = LeaveStatus.MANAGER_REJECTED
            leave.manager_decision = LeaveDecision.REJECTED

        leave.manager_id = user.id
        leave.manager_decided_at = datetime.now(timezone.utc)
        leave.manager_notes = body.notes
    db.commit()
    db.refresh(leave)

    # Reject unwinds any approved side-effects (balance) + flips stale LEAVE
    # attendance rows back via daily_rollup.
    if leave.status in (LeaveStatus.MANAGER_REJECTED, LeaveStatus.REJECTED):
        _unwind_leave_effects(db, leave, user, was_approved=was_approved)

    try:
        action = AttendanceLogAction.LEAVE_MANAGER_APPROVED if body.decision == LeaveDecision.APPROVED else AttendanceLogAction.LEAVE_MANAGER_REJECTED
        log(db, actor_id=user.id, action=action,
            target_table="hr_leave_requests", target_id=leave.id, employee_id=leave.employee_id,
            payload={"ref": leave.reference_no, "notes": body.notes})
        ev = "manager_approved" if body.decision == LeaveDecision.APPROVED else "manager_rejected"
        emp_user_id = db.query(Employee.user_id).filter(Employee.id == leave.employee_id).scalar()
        _emit_notifications(db, leave, employee_user_id=emp_user_id, event=ev, actor=user)
        db.commit()
    except Exception:
        db.rollback()
    return _to_response(db, leave)


# ═════════════════════════════════════════════════════════════════════════════
# Phase 4 — Approver-candidate picker (drives the policy chain editor UI)
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/approver-candidates")
def list_approver_candidates(
    q: Optional[str] = Query(None, description="Free-text name or email substring"),
    limit: int = Query(40, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Lightweight user picker used by the leave-policy chain editor when
    configuring a USER-type approval stage. Returns active users matching
    the query, sorted by name. Superuser-only.
    """
    qry = db.query(User.id, User.full_name, User.email, User.is_superuser).filter(
        User.is_active == True,  # noqa: E712
    )
    if q:
        s = f"%{q.lower()}%"
        qry = qry.filter(or_(
            sa_func.lower(User.full_name).like(s),
            sa_func.lower(User.email).like(s),
        ))
    rows = qry.order_by(User.full_name).limit(limit).all()
    return {
        "items": [
            {"id": str(r.id), "full_name": r.full_name, "email": r.email,
             "is_superuser": bool(r.is_superuser)}
            for r in rows
        ],
        "total": len(rows),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Phase 4 — Generic per-stage approval (chain-aware)
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/me/queue", response_model=LeaveRequestListResponse)
def my_approval_queue(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every non-terminal leave whose current stage is waiting on this user.

    Covers all three approver types — MANAGER (employee.reporting_manager_id ==
    user.id), HR (caller is_superuser), and USER (named approver). This is the
    one queue endpoint that works for arbitrary chain shapes; the older
    /manager/queue and /hr/queue endpoints remain for backwards compatibility.
    """
    rows = (
        db.query(LeaveRequest)
          .filter(
              LeaveRequest.is_deleted == False,  # noqa: E712
              LeaveRequest.status.in_([LeaveStatus.PENDING_MANAGER, LeaveStatus.PENDING_HR]),
          )
          .order_by(LeaveRequest.created_at.asc())
          .all()
    )

    actionable: List[LeaveRequest] = []
    for r in rows:
        steps = list(r.approval_steps or [])
        idx = int(r.current_step or 0)
        if 0 <= idx < len(steps):
            if _can_act_on_step(user, steps[idx]):
                actionable.append(r)
            continue
        # Legacy in-flight rows (no snapshot) — fall back to the old
        # who-is-the-approver semantics so they still surface here.
        if r.status == LeaveStatus.PENDING_MANAGER:
            emp = db.query(Employee.reporting_manager_id).filter(Employee.id == r.employee_id).first()
            if emp and emp[0] == user.id:
                actionable.append(r)
        elif r.status == LeaveStatus.PENDING_HR and user.is_superuser:
            actionable.append(r)

    total = len(actionable)
    paged = actionable[(page - 1) * limit: (page - 1) * limit + limit]
    return LeaveRequestListResponse(
        items=[_to_response(db, r) for r in paged],
        total=total, page=page, limit=limit,
        total_pages=max(1, ceil(total / limit) if limit else 1),
    )


@router.patch("/chain/{leave_id}/decide", response_model=LeaveRequestResponse)
def chain_decide(
    leave_id: UUID,
    body: LeaveDecisionBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generic per-stage decision endpoint — works for any approver_type at
    the current stage. Routes to the same final-approval side-effects as
    /hr/{id}/decide when the chain terminates.

    For MANAGER-type stages, /manager/{id}/decide remains the canonical
    endpoint and applies the same chain-aware mutation. For HR-type stages,
    /hr/{id}/decide is the canonical endpoint and requires superuser. This
    endpoint exists primarily to support USER-type stages, but accepts any
    stage type the caller is authorised for.
    """
    leave = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.is_deleted == False,  # noqa: E712
    ).with_for_update().first()
    if not leave:
        raise HTTPException(404, "Leave request not found")
    was_approved = leave.status == LeaveStatus.APPROVED
    if _has_legacy_steps(leave):
        raise HTTPException(409, "This request predates Phase 4 — use /manager or /hr decide")

    steps = list(leave.approval_steps or [])
    idx = int(leave.current_step or 0)
    if idx >= len(steps):
        raise HTTPException(409, "Leave is fully resolved")
    cur = steps[idx]
    if not _can_act_on_step(user, cur):
        raise HTTPException(403, "You are not the configured approver for the current stage")
    if body.decision == LeaveDecision.APPROVED:
        _guard_not_past(leave, "approve this leave")

    now_iso = datetime.now(timezone.utc).isoformat()
    if body.decision == LeaveDecision.APPROVED:
        cur["decision"] = LeaveDecision.APPROVED.value
        cur["decided_by_id"] = str(user.id)
        cur["decided_at"] = now_iso
        cur["notes"] = body.notes
        new_idx = _auto_skip_unresolvable(steps, idx + 1)
        leave.approval_steps = steps
        flag_modified(leave, "approval_steps")
        leave.current_step = new_idx
        if new_idx >= len(steps):
            # Final approval — debit balance + rollup attendance
            if leave.leave_type != LeaveType.LWP:
                for fy_label, days in (leave.fy_breakdown or {}).items():
                    b = _get_or_create_balance(db, leave.employee_id, leave.leave_type, fy_label)
                    _apply_ledger(
                        db, b, kind=LedgerKind.REQUEST_APPROVED,
                        delta=Decimal(str(-float(days))), actor=user,
                        note=f"Approved {leave.reference_no}",
                        related_request_id=leave.id,
                    )
            leave.status = LeaveStatus.APPROVED
        else:
            leave.status = _step_status(steps, new_idx)
    else:
        cur["decision"] = LeaveDecision.REJECTED.value
        cur["decided_by_id"] = str(user.id)
        cur["decided_at"] = now_iso
        cur["notes"] = body.notes
        leave.approval_steps = steps
        flag_modified(leave, "approval_steps")
        # Mid-chain rejection: terminal MANAGER_REJECTED if a MANAGER stage,
        # otherwise REJECTED. Mirrors the existing two-tier semantics.
        leave.status = LeaveStatus.MANAGER_REJECTED if cur["approver_type"] == "MANAGER" else LeaveStatus.REJECTED
    _mirror_legacy_columns(leave)
    db.commit()
    db.refresh(leave)

    if leave.status == LeaveStatus.APPROVED:
        _rollup_leave_dates(db, leave, actor_id=user.id)
        db.commit()
    elif leave.status in (LeaveStatus.REJECTED, LeaveStatus.MANAGER_REJECTED):
        _unwind_leave_effects(db, leave, user, was_approved=was_approved)

    try:
        if body.decision == LeaveDecision.APPROVED:
            action = AttendanceLogAction.LEAVE_HR_APPROVED if leave.status == LeaveStatus.APPROVED else AttendanceLogAction.LEAVE_MANAGER_APPROVED
            ev = "hr_approved" if leave.status == LeaveStatus.APPROVED else "manager_approved"
        else:
            action = AttendanceLogAction.LEAVE_MANAGER_REJECTED if cur["approver_type"] == "MANAGER" else AttendanceLogAction.LEAVE_HR_REJECTED
            ev = "manager_rejected" if cur["approver_type"] == "MANAGER" else "hr_rejected"
        log(db, actor_id=user.id, action=action,
            target_table="hr_leave_requests", target_id=leave.id, employee_id=leave.employee_id,
            payload={"ref": leave.reference_no, "stage": cur.get("label"),
                     "approver_type": cur.get("approver_type"), "notes": body.notes})
        emp_user_id = db.query(Employee.user_id).filter(Employee.id == leave.employee_id).scalar()
        _emit_notifications(db, leave, employee_user_id=emp_user_id, event=ev, actor=user)
        db.commit()
    except Exception:
        db.rollback()
    return _to_response(db, leave)


# ═════════════════════════════════════════════════════════════════════════════
# Admin / HR — list, stats, queue, decide
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/", response_model=LeaveRequestListResponse)
def admin_list_leaves(
    status_filter: Optional[LeaveStatus] = Query(None, alias="status"),
    leave_type: Optional[LeaveType] = None,
    employee_id: Optional[UUID] = None,
    department_id: Optional[UUID] = None,
    manager_id: Optional[UUID] = None,
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    qry = db.query(LeaveRequest).filter(LeaveRequest.is_deleted == False)  # noqa: E712
    if status_filter:
        qry = qry.filter(LeaveRequest.status == status_filter)
    if leave_type:
        qry = qry.filter(LeaveRequest.leave_type == leave_type)
    if employee_id:
        qry = qry.filter(LeaveRequest.employee_id == employee_id)
    if manager_id:
        qry = qry.filter(LeaveRequest.manager_id == manager_id)
    if department_id:
        qry = qry.join(Employee, Employee.id == LeaveRequest.employee_id).filter(
            Employee.department_id == department_id,
        )
    if from_:
        qry = qry.filter(LeaveRequest.to_date >= from_)
    if to:
        qry = qry.filter(LeaveRequest.from_date <= to)
    if q:
        s = f"%{q.lower()}%"
        qry = qry.filter(or_(
            sa_func.lower(LeaveRequest.reference_no).like(s),
            sa_func.lower(LeaveRequest.reason).like(s),
        ))

    total = qry.count()
    rows = (
        qry.order_by(LeaveRequest.created_at.desc())
           .offset((page - 1) * limit).limit(limit).all()
    )
    return LeaveRequestListResponse(
        items=[_to_response(db, r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=max(1, ceil(total / limit) if limit else 1),
    )


@router.get("/stats", response_model=LeaveStats)
def admin_stats(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    today = date.today()
    base = db.query(LeaveRequest).filter(LeaveRequest.is_deleted == False)  # noqa: E712
    pending_manager = base.filter(LeaveRequest.status == LeaveStatus.PENDING_MANAGER).count()
    pending_hr = base.filter(LeaveRequest.status == LeaveStatus.PENDING_HR).count()
    approved_today = base.filter(
        LeaveRequest.status == LeaveStatus.APPROVED,
        LeaveRequest.hr_decided_at >= datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
    ).count()
    month_start = today.replace(day=1)
    approved_this_month = base.filter(
        LeaveRequest.status == LeaveStatus.APPROVED,
        LeaveRequest.hr_decided_at >= datetime.combine(month_start, datetime.min.time(), tzinfo=timezone.utc),
    ).count()
    rejected_this_month = base.filter(
        LeaveRequest.status.in_([LeaveStatus.REJECTED, LeaveStatus.MANAGER_REJECTED]),
        LeaveRequest.updated_at >= datetime.combine(month_start, datetime.min.time(), tzinfo=timezone.utc),
    ).count()
    on_leave_today = base.filter(
        LeaveRequest.status == LeaveStatus.APPROVED,
        LeaveRequest.from_date <= today, LeaveRequest.to_date >= today,
    ).count()
    upcoming_30d = base.filter(
        LeaveRequest.status == LeaveStatus.APPROVED,
        LeaveRequest.from_date > today, LeaveRequest.from_date <= today + timedelta(days=30),
    ).count()

    # By-type YTD for the dashboard donut
    fy = _current_fy(db)
    fy_start_str = _get_setting(db, "fiscal_year_start", "04-01")
    fy_from, fy_to = _fy_bounds(fy, fy_start_str)
    by_type_rows = (
        db.query(LeaveRequest.leave_type, sa_func.count(LeaveRequest.id), sa_func.coalesce(sa_func.sum(LeaveRequest.total_days), 0))
        .filter(
            LeaveRequest.is_deleted == False,  # noqa: E712
            LeaveRequest.status == LeaveStatus.APPROVED,
            LeaveRequest.from_date >= fy_from, LeaveRequest.from_date <= fy_to,
        )
        .group_by(LeaveRequest.leave_type)
        .all()
    )
    by_type = [
        LeaveTypeCount(leave_type=lt, count=int(c), days=Decimal(d))
        for (lt, c, d) in by_type_rows
    ]
    return LeaveStats(
        pending_manager=pending_manager, pending_hr=pending_hr,
        approved_today=approved_today, approved_this_month=approved_this_month,
        rejected_this_month=rejected_this_month, on_leave_today=on_leave_today,
        upcoming_30d=upcoming_30d, by_type_ytd=by_type,
    )


@router.get("/hr/queue", response_model=LeaveRequestListResponse)
def hr_queue(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    qry = db.query(LeaveRequest).filter(
        LeaveRequest.is_deleted == False,  # noqa: E712
        LeaveRequest.status == LeaveStatus.PENDING_HR,
    )
    total = qry.count()
    rows = (
        qry.order_by(LeaveRequest.created_at.asc())
           .offset((page - 1) * limit).limit(limit).all()
    )
    return LeaveRequestListResponse(
        items=[_to_response(db, r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=max(1, ceil(total / limit) if limit else 1),
    )


@router.patch("/hr/{leave_id}/decide", response_model=LeaveRequestResponse)
def hr_decide(
    leave_id: UUID,
    body: LeaveDecisionBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    leave = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.is_deleted == False,  # noqa: E712
    ).with_for_update().first()
    if not leave:
        raise HTTPException(404, "Leave request not found")
    was_approved = leave.status == LeaveStatus.APPROVED

    # An already-submitted leave that extends past the employee's LWD must not
    # be approved (it may have been filed before notice was given). Only guard
    # on approval — rejections must always be allowed to clear the queue.
    if body.decision == LeaveDecision.APPROVED:
        _emp = db.query(Employee).filter(Employee.id == leave.employee_id).first()
        guard_within_tenure(_emp, leave.to_date, "approve leave")
        # Past-dated leaves can't be approved retroactively — lapse them instead.
        _guard_not_past(leave, "approve this leave")

    # Phase 4 — chain-aware path: the HR decide endpoint advances whatever the
    # current stage is, provided it is an HR-type stage. (USER stages flow
    # through the generic /chain/{id}/decide endpoint.) Legacy in-flight rows
    # without a snapshot stay on the old two-tier transition logic below.
    if not _has_legacy_steps(leave):
        steps = list(leave.approval_steps or [])
        idx = int(leave.current_step or 0)
        if idx >= len(steps):
            raise HTTPException(409, "Leave is fully resolved")
        cur = steps[idx]
        if cur["approver_type"] != "HR":
            raise HTTPException(409, "Current approval stage is not an HR stage — use /chain/{id}/decide")
        # Superuser dep already guarantees HR authority

        now_iso = datetime.now(timezone.utc).isoformat()
        if body.decision == LeaveDecision.APPROVED:
            cur["decision"] = LeaveDecision.APPROVED.value
            cur["decided_by_id"] = str(admin.id)
            cur["decided_at"] = now_iso
            cur["notes"] = body.notes
            new_idx = _auto_skip_unresolvable(steps, idx + 1)
            leave.approval_steps = steps
            flag_modified(leave, "approval_steps")
            leave.current_step = new_idx
            if new_idx >= len(steps):
                # Final-approve: debit balance + flip status
                if leave.leave_type != LeaveType.LWP:
                    for fy_label, days in (leave.fy_breakdown or {}).items():
                        b = _get_or_create_balance(db, leave.employee_id, leave.leave_type, fy_label)
                        _apply_ledger(
                            db, b, kind=LedgerKind.REQUEST_APPROVED,
                            delta=Decimal(str(-float(days))), actor=admin,
                            note=f"Approved {leave.reference_no}",
                            related_request_id=leave.id,
                        )
                leave.status = LeaveStatus.APPROVED
            else:
                leave.status = _step_status(steps, new_idx)
        else:
            cur["decision"] = LeaveDecision.REJECTED.value
            cur["decided_by_id"] = str(admin.id)
            cur["decided_at"] = now_iso
            cur["notes"] = body.notes
            leave.approval_steps = steps
            flag_modified(leave, "approval_steps")
            leave.status = LeaveStatus.REJECTED
        _mirror_legacy_columns(leave)
    else:
        if body.decision == LeaveDecision.APPROVED:
            _assert_transition(leave.status, LeaveStatus.APPROVED)
            if leave.leave_type != LeaveType.LWP:
                for fy_label, days in (leave.fy_breakdown or {}).items():
                    b = _get_or_create_balance(db, leave.employee_id, leave.leave_type, fy_label)
                    _apply_ledger(
                        db, b, kind=LedgerKind.REQUEST_APPROVED,
                        delta=Decimal(str(-float(days))), actor=admin,
                        note=f"Approved {leave.reference_no}",
                        related_request_id=leave.id,
                    )
            leave.status = LeaveStatus.APPROVED
            leave.hr_decision = LeaveDecision.APPROVED
        else:
            _assert_transition(leave.status, LeaveStatus.REJECTED)
            leave.status = LeaveStatus.REJECTED
            leave.hr_decision = LeaveDecision.REJECTED

        leave.hr_id = admin.id
        leave.hr_decided_at = datetime.now(timezone.utc)
        leave.hr_notes = body.notes
    db.commit()
    db.refresh(leave)

    # Gate rollup on the resulting status — in N-stage chains an HR approval
    # may be mid-chain and the leave isn't actually APPROVED yet.
    if leave.status == LeaveStatus.APPROVED:
        _rollup_leave_dates(db, leave, actor_id=admin.id)
        db.commit()
    elif leave.status in (LeaveStatus.REJECTED, LeaveStatus.MANAGER_REJECTED):
        _unwind_leave_effects(db, leave, admin, was_approved=was_approved)

    try:
        action = AttendanceLogAction.LEAVE_HR_APPROVED if body.decision == LeaveDecision.APPROVED else AttendanceLogAction.LEAVE_HR_REJECTED
        log(db, actor_id=admin.id, action=action,
            target_table="hr_leave_requests", target_id=leave.id, employee_id=leave.employee_id,
            payload={"ref": leave.reference_no, "notes": body.notes,
                     "days": float(leave.total_days or 0)})
        ev = "hr_approved" if body.decision == LeaveDecision.APPROVED else "hr_rejected"
        emp_user_id = db.query(Employee.user_id).filter(Employee.id == leave.employee_id).scalar()
        _emit_notifications(db, leave, employee_user_id=emp_user_id, event=ev, actor=admin)
        db.commit()
    except Exception:
        db.rollback()
    return _to_response(db, leave)


@router.post("/{leave_id}/lapse", response_model=LeaveRequestResponse)
def lapse_leave(
    leave_id: UUID,
    body: LeaveLapseBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Close an un-actioned, past-dated leave as LAPSED with a mandatory remark.

    Authorised for the employee's reporting MANAGER or any HR superuser — the two
    parties who could have approved it. Only valid while the leave is still
    pending (DRAFT / PENDING_MANAGER / PENDING_HR) AND its dates have already
    passed; a future/ongoing leave must be approved or rejected normally.
    """
    leave = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.is_deleted == False,  # noqa: E712
    ).with_for_update().first()
    if not leave:
        raise HTTPException(404, "Leave request not found")
    if leave.status not in _LAPSABLE_STATUSES:
        raise HTTPException(409, f"Only a pending leave can be closed as lapsed (currently {leave.status.value}).")
    if not (leave.to_date and leave.to_date < date.today()):
        raise HTTPException(409, "This leave hasn't lapsed — its dates are today or in the future. Approve or reject it instead.")

    emp = db.query(Employee).filter(Employee.id == leave.employee_id).first()
    is_mgr = bool(emp and emp.reporting_manager_id == user.id)
    if not (user.is_superuser or is_mgr):
        raise HTTPException(403, "Only the reporting manager or HR can close this leave.")

    role = "HR" if user.is_superuser else "Manager"
    note = f"[Lapsed by {role}] {body.reason.strip()}"
    frm = leave.status.value
    _assert_transition(leave.status, LeaveStatus.LAPSED)
    leave.status = LeaveStatus.LAPSED
    # Record the remark on the actor's column so the detail drawer shows who/why.
    if user.is_superuser:
        leave.hr_id = user.id
        leave.hr_decided_at = datetime.now(timezone.utc)
        leave.hr_notes = note
    else:
        leave.manager_id = user.id
        leave.manager_decided_at = datetime.now(timezone.utc)
        leave.manager_notes = note
    # Mark the current chain stage closed so the queue stops surfacing it.
    if not _has_legacy_steps(leave):
        steps = list(leave.approval_steps or [])
        idx = int(leave.current_step or 0)
        if 0 <= idx < len(steps):
            steps[idx]["decision"] = LeaveDecision.SKIPPED.value
            steps[idx]["decided_by_id"] = str(user.id)
            steps[idx]["decided_at"] = datetime.now(timezone.utc).isoformat()
            steps[idx]["notes"] = note
            leave.approval_steps = steps
            flag_modified(leave, "approval_steps")
    db.commit()
    db.refresh(leave)

    try:
        log(db, actor_id=user.id, action=AttendanceLogAction.LEAVE_HR_REJECTED,
            target_table="hr_leave_requests", target_id=leave.id, employee_id=leave.employee_id,
            payload={"ref": leave.reference_no, "lapsed": True, "by": role, "from": frm, "reason": body.reason})
        emp_user_id = db.query(Employee.user_id).filter(Employee.id == leave.employee_id).scalar()
        _emit_notifications(db, leave, employee_user_id=emp_user_id, event="lapsed", actor=user)
        db.commit()
    except Exception:
        db.rollback()
    return _to_response(db, leave)


@router.post("/bulk-decide", response_model=LeaveRequestListResponse)
def hr_bulk_decide(
    body: LeaveBulkDecideBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Bulk approve/reject from the HR queue. Each row processed independently
    so a single failure doesn't block the rest."""
    out: List[LeaveRequest] = []
    for lid in body.ids:
        try:
            l = hr_decide(lid, LeaveDecisionBody(decision=body.decision, notes=body.notes), db=db, admin=admin)
            out.append(l)
        except HTTPException:
            continue
    return LeaveRequestListResponse(items=out, total=len(out), page=1, limit=len(out) or 1, total_pages=1)


@router.post("/", response_model=LeaveRequestResponse, status_code=http_status.HTTP_201_CREATED)
def admin_create_leave(
    payload: LeaveRequestAdminCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Admin manual entry — skips both pending stages, lands APPROVED."""
    emp = db.query(Employee).filter(
        Employee.id == payload.employee_id,
        Employee.is_deleted == False,  # noqa: E712
    ).first()
    if not emp:
        raise HTTPException(404, "Employee not found")
    policy = _policy_or_404(db, payload.leave_type)

    # A leaving / departed employee may not be given leave dated past their LWD.
    guard_within_tenure(emp, payload.to_date, "apply for leave")

    if payload.is_half_day:
        if payload.from_date != payload.to_date:
            raise HTTPException(422, "Half-day leave must be a single date")
        if not payload.which_session:
            raise HTTPException(422, "Half-day leave requires which_session")

    # Admin overrides must respect the no-overlap rule too — a second approved
    # leave on the same calendar day would double-debit balance and produce
    # contradictory attendance rollups.
    _assert_no_overlapping_leave(
        db, emp.id, payload.from_date, payload.to_date,
        is_half_day=payload.is_half_day,
        which_session=payload.which_session,
    )

    total_days, fy_breakdown, _ = _compute_total_and_fy(
        db, emp, payload.from_date, payload.to_date,
        payload.is_half_day, payload.which_session, policy,
    )
    if total_days <= 0:
        raise HTTPException(422, "Selected dates have no paid working days to debit")

    if payload.leave_type != LeaveType.LWP:
        for fy_label, days in fy_breakdown.items():
            b = _get_or_create_balance(db, emp.id, payload.leave_type, fy_label)
            available = Decimal(b.closing_balance or 0)
            if Decimal(str(days)) > available:
                # admin override — allow negative but record audit
                pass

    fy_start = _get_setting(db, "fiscal_year_start", "04-01")
    ref = _generate_reference_no(db, _fy_for(payload.from_date, fy_start))

    # Phase 4 — synthesize an all-approved chain so the new viewer renders
    # consistently. Every stage is marked SKIPPED except the last, which the
    # admin signs as APPROVED.
    chain_cfg = _normalize_chain_config(policy.approval_chain)
    steps = _build_request_steps(chain_cfg, emp)
    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()
    for i, s in enumerate(steps[:-1]):
        s["decision"] = LeaveDecision.SKIPPED.value
        s["decided_by_id"] = str(admin.id)
        s["decided_at"] = iso_now
        s["notes"] = "Admin override — stage skipped"
    if steps:
        last = steps[-1]
        last["decision"] = LeaveDecision.APPROVED.value
        last["decided_by_id"] = str(admin.id)
        last["decided_at"] = iso_now
        last["notes"] = payload.admin_note or "Admin manual entry"

    leave = LeaveRequest(
        reference_no=ref,
        employee_id=emp.id,
        leave_type=payload.leave_type,
        from_date=payload.from_date, to_date=payload.to_date,
        total_days=total_days,
        is_half_day=payload.is_half_day,
        which_session=payload.which_session,
        fy_breakdown=fy_breakdown,
        reason=payload.reason,
        attachment_id=payload.attachment_id,
        contact_during_leave=payload.contact_during_leave,
        emergency_contact=payload.emergency_contact,
        status=LeaveStatus.APPROVED,
        manager_id=emp.reporting_manager_id,
        manager_decision=LeaveDecision.SKIPPED,
        manager_decided_at=now,
        manager_notes="Admin override — manager stage skipped",
        hr_id=admin.id,
        hr_decision=LeaveDecision.APPROVED,
        hr_decided_at=now,
        hr_notes=payload.admin_note or "Admin manual entry",
        is_admin_override=True,
        approval_steps=steps,
        current_step=len(steps),
    )
    db.add(leave)
    db.commit()
    db.refresh(leave)

    if leave.leave_type != LeaveType.LWP:
        for fy_label, days in fy_breakdown.items():
            b = _get_or_create_balance(db, emp.id, leave.leave_type, fy_label)
            try:
                _apply_ledger(
                    db, b, kind=LedgerKind.REQUEST_APPROVED,
                    delta=Decimal(str(-float(days))), actor=admin,
                    note=f"Admin override {ref}", related_request_id=leave.id,
                )
            except HTTPException:
                # admin override is allowed to push negative — use ADMIN_ADJUST instead
                _apply_ledger(
                    db, b, kind=LedgerKind.ADMIN_ADJUST,
                    delta=Decimal(str(-float(days))), actor=admin,
                    note=f"Admin override (override-debit) {ref}", related_request_id=leave.id,
                )
        db.commit()

    _rollup_leave_dates(db, leave, actor_id=admin.id)
    db.commit()

    try:
        log(db, actor_id=admin.id, action=AttendanceLogAction.LEAVE_ADMIN_OVERRIDE,
            target_table="hr_leave_requests", target_id=leave.id, employee_id=emp.id,
            payload={"ref": ref, "type": leave.leave_type.value, "days": float(total_days)})
        _emit_notifications(db, leave, employee_user_id=emp.user_id, event="admin_override", actor=admin)
        db.commit()
    except Exception:
        db.rollback()
    return _to_response(db, leave, include_breakdown=True)


# ═════════════════════════════════════════════════════════════════════════════
# Policies
# ═════════════════════════════════════════════════════════════════════════════

_ACTIVE_REQUEST_STATUSES = (
    LeaveStatus.PENDING_MANAGER, LeaveStatus.PENDING_HR, LeaveStatus.APPROVED,
)


def _compute_policy_usage(db: Session, leave_type: LeaveType,
                          policy: Optional[LeavePolicy]) -> LeavePolicyUsage:
    """Aggregate how deeply a leave type is embedded in employee data.

    Drives the delete modal's impact banner — counts are computed in SQL
    (no row pull) per the performance bar.
    """
    balance_count = db.query(sa_func.count(LeaveBalance.id)).filter(
        LeaveBalance.leave_type == leave_type
    ).scalar() or 0
    employee_count = db.query(sa_func.count(sa_func.distinct(LeaveBalance.employee_id))).filter(
        LeaveBalance.leave_type == leave_type
    ).scalar() or 0
    nonzero_balance_count = db.query(sa_func.count(sa_func.distinct(LeaveBalance.employee_id))).filter(
        LeaveBalance.leave_type == leave_type,
        LeaveBalance.closing_balance > 0,
    ).scalar() or 0
    total_requests = db.query(sa_func.count(LeaveRequest.id)).filter(
        LeaveRequest.leave_type == leave_type
    ).scalar() or 0
    active_requests = db.query(sa_func.count(LeaveRequest.id)).filter(
        LeaveRequest.leave_type == leave_type,
        LeaveRequest.status.in_(_ACTIVE_REQUEST_STATUSES),
    ).scalar() or 0
    upcoming_approved = db.query(sa_func.count(LeaveRequest.id)).filter(
        LeaveRequest.leave_type == leave_type,
        LeaveRequest.status == LeaveStatus.APPROVED,
        LeaveRequest.to_date >= date.today(),
    ).scalar() or 0
    return LeavePolicyUsage(
        leave_type=leave_type,
        exists=policy is not None,
        is_active=bool(policy.is_active) if policy else False,
        balance_count=balance_count,
        employee_count=employee_count,
        nonzero_balance_count=nonzero_balance_count,
        total_requests=total_requests,
        active_requests=active_requests,
        upcoming_approved=upcoming_approved,
        in_use=bool(balance_count or total_requests),
    )


@router.get("/policies", response_model=LeavePolicyListResponse)
def list_policies(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = db.query(LeavePolicy).filter(LeavePolicy.is_deleted == False).all()  # noqa: E712
    # Types with no LIVE policy → available to the create wizard. Preserve the
    # canonical taxonomy order so the picker reads naturally.
    live_types = {r.leave_type for r in rows}
    creatable = [t for t in LeaveType if t not in live_types]
    return LeavePolicyListResponse(
        items=[LeavePolicyResponse.model_validate(r) for r in rows],
        total=len(rows),
        creatable_types=creatable,
    )


@router.post("/policies", response_model=LeavePolicyResponse, status_code=201)
def create_policy(
    body: LeavePolicyCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Configure a policy for a leave type that has no active one.

    Because ``leave_type`` is uniquely constrained, a soft-deleted row for the
    same type is *revived* (un-deleted + overwritten) rather than inserting a
    duplicate. An already-live policy yields 409.
    """
    existing = db.query(LeavePolicy).filter(
        LeavePolicy.leave_type == body.leave_type
    ).first()
    if existing and not existing.is_deleted:
        raise HTTPException(
            409,
            f"A policy for {body.leave_type.value} already exists — edit it instead",
        )

    data = body.model_dump()
    # JSONB approval chain must be plain dicts (UUIDs → str) for psycopg2.
    chain = data.pop("approval_chain", None)
    if chain is not None:
        chain = [
            {
                "approver_type": s["approver_type"],
                "approver_user_id": str(s["approver_user_id"]) if s.get("approver_user_id") else None,
                "label": s["label"],
            }
            for s in chain
        ]

    if existing:                       # revive the tombstoned row
        p = existing
        for k, v in data.items():
            setattr(p, k, v)
        p.approval_chain = chain
        p.is_deleted = False
        revived = True
    else:                              # fresh insert
        p = LeavePolicy(**data, approval_chain=chain)
        db.add(p)
        revived = False
    p.updated_by_id = admin.id

    db.commit()
    db.refresh(p)
    log(db, actor_id=admin.id, action=AttendanceLogAction.LEAVE_POLICY_CREATED,
        target_table="hr_leave_policies", target_id=p.id,
        payload={
            "leave_type": body.leave_type.value,
            "revived": revived,
            "label": p.label,
            "annual_quota": float(p.annual_quota),
        })
    db.commit()
    return LeavePolicyResponse.model_validate(p)


@router.patch("/policies/{leave_type}", response_model=LeavePolicyResponse)
def update_policy(
    leave_type: LeaveType,
    body: LeavePolicyUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    p = _policy_or_404(db, leave_type)
    changed = body.model_dump(exclude_unset=True)
    for k, v in changed.items():
        setattr(p, k, v)
    p.updated_by_id = admin.id
    db.commit()
    db.refresh(p)
    log(db, actor_id=admin.id, action=AttendanceLogAction.LEAVE_POLICY_UPDATED,
        target_table="hr_leave_policies", target_id=p.id,
        payload={"leave_type": leave_type.value, "fields": sorted(changed.keys())})
    db.commit()
    return LeavePolicyResponse.model_validate(p)


@router.get("/policies/{leave_type}/usage", response_model=LeavePolicyUsage)
def policy_usage(
    leave_type: LeaveType,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Impact preflight — how many employees/requests reference this type."""
    policy = db.query(LeavePolicy).filter(
        LeavePolicy.leave_type == leave_type,
        LeavePolicy.is_deleted == False,  # noqa: E712
    ).first()
    return _compute_policy_usage(db, leave_type, policy)


@router.delete("/policies/{leave_type}", status_code=200)
def delete_policy(
    leave_type: LeaveType,
    body: LeavePolicyDeleteBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Soft-delete a leave-type policy.

    The row is tombstoned (``is_deleted=True``, ``is_active=False``) so existing
    employee balances and the leave ledger are preserved — this only stops the
    type from appearing in the booking wizard, the quota spectrum, and future
    balance materialisation. A reason is mandatory; when the type is still in
    use the caller must also set ``acknowledge_impact``.
    """
    p = _policy_or_404(db, leave_type)
    usage = _compute_policy_usage(db, leave_type, p)
    if usage.in_use and not body.acknowledge_impact:
        raise HTTPException(
            409,
            f"{leave_type.value} is implemented for {usage.employee_count} employee(s) "
            f"and has {usage.active_requests} active request(s). Re-confirm to proceed.",
        )

    p.is_deleted = True
    p.is_active = False
    p.updated_by_id = admin.id
    db.commit()
    log(db, actor_id=admin.id, action=AttendanceLogAction.LEAVE_POLICY_DELETED,
        target_table="hr_leave_policies", target_id=p.id,
        payload={
            "leave_type": leave_type.value,
            "reason": body.reason,
            "reason_category": body.reason_category,
            "impact": {
                "employee_count": usage.employee_count,
                "balance_count": usage.balance_count,
                "total_requests": usage.total_requests,
                "active_requests": usage.active_requests,
                "upcoming_approved": usage.upcoming_approved,
            },
        })
    db.commit()
    return {
        "ok": True,
        "leave_type": leave_type.value,
        "soft_deleted": True,
        "impact": usage.model_dump(mode="json"),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Balances
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/balances", response_model=LeaveBalanceListResponse)
def admin_balances(
    employee_id: Optional[UUID] = None,
    department_id: Optional[UUID] = None,
    leave_type: Optional[LeaveType] = None,
    fy: Optional[str] = None,
    include_separated: bool = Query(
        False, description="Include EXITED / ARCHIVED / INACTIVE employees (audit only). "
                           "Default hides them — you can't manage reserves for people who have left."),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=300),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    fy = fy or _current_fy(db)

    # Active policies define which leave types appear (and their annual quota).
    # We show a row for EVERY active employee × EVERY active policy type — even
    # when no LeaveBalance row has been materialised yet — so HR sees the full
    # roster's reserves, not only employees who happen to have submitted leave.
    policies = {p.leave_type: p for p in db.query(LeavePolicy).filter(
        LeavePolicy.is_active == True  # noqa: E712
    ).all()}
    if leave_type and leave_type not in policies:
        # Caller asked for a type that has no active policy — fall back to it
        # anyway so the column isn't silently dropped.
        policies[leave_type] = db.query(LeavePolicy).filter(
            LeavePolicy.leave_type == leave_type
        ).first()
    type_keys = [leave_type] if leave_type else list(policies.keys())

    # Paginate by EMPLOYEE (not by balance row) so each page carries a complete
    # set of type rows per employee. Ordered by display name for a stable list.
    emp_q = (
        db.query(Employee.id)
        .join(User, User.id == Employee.user_id)
        .filter(Employee.is_deleted == False)  # noqa: E712
    )
    # Separated employees (EXITED / ARCHIVED / INACTIVE) drop out of the active
    # reserve roster by default — their leave ledger is frozen for F&F, not for
    # day-to-day management. ON_NOTICE stays (still on payroll). A null state is
    # treated as active. `include_separated=true` surfaces them for audit.
    if not include_separated:
        emp_q = emp_q.filter(or_(
            Employee.lifecycle_state.is_(None),
            Employee.lifecycle_state.notin_(SEPARATED),
        ))
    if employee_id:
        emp_q = emp_q.filter(Employee.id == employee_id)
    if department_id:
        emp_q = emp_q.filter(Employee.department_id == department_id)
    total = emp_q.count()
    emp_ids = [
        r[0] for r in emp_q.order_by(User.full_name.asc())
        .offset((page - 1) * limit).limit(limit).all()
    ]
    if not emp_ids:
        return LeaveBalanceListResponse(items=[], total=total, fiscal_year=fy)

    # One bulk query for any balances that DO exist, keyed (employee, type).
    existing = {}
    bq = db.query(LeaveBalance).filter(
        LeaveBalance.fiscal_year == fy,
        LeaveBalance.is_deleted == False,  # noqa: E712
        LeaveBalance.employee_id.in_(emp_ids),
    )
    if leave_type:
        bq = bq.filter(LeaveBalance.leave_type == leave_type)
    for b in bq.all():
        existing[(b.employee_id, b.leave_type)] = b

    # Cache employee snapshots once per employee (each appears N-types times).
    snap_cache: dict = {}

    def _snap(eid):
        if eid not in snap_cache:
            snap_cache[eid] = _employee_snapshot(db, eid)
        return snap_cache[eid]

    items: List[LeaveBalanceResponse] = []
    for eid in emp_ids:
        snap = _snap(eid)
        for lt in type_keys:
            policy = policies.get(lt)
            b = existing.get((eid, lt))
            if b is not None:
                items.append(_balance_to_response(db, b, policy, snap=snap))
            else:
                quota = Decimal(policy.annual_quota) if policy else Decimal("0")
                items.append(LeaveBalanceResponse(
                    id=_uuid_mod.uuid4(),  # synthetic — no persisted row yet
                    employee_id=eid,
                    employee_name=snap.get("name"), employee_code=snap.get("code"),
                    department_name=snap.get("dept"), lifecycle_state=snap.get("lifecycle_state"),
                    leave_type=lt, fiscal_year=fy,
                    opening_balance=Decimal("0"), accrued=Decimal("0"),
                    carry_forward_in=Decimal("0"), used=Decimal("0"),
                    encashed=Decimal("0"), adjustments=Decimal("0"),
                    closing_balance=Decimal("0"),
                    available=Decimal("0"), quota=quota,
                    monthly_accrual=(Decimal(policy.monthly_accrual or 0) if policy else Decimal("0")),
                    utilisation_pct=0.0,
                ))
    return LeaveBalanceListResponse(items=items, total=total, fiscal_year=fy)


@router.post("/balances/{employee_id}/adjust", response_model=LeaveBalanceResponse)
def admin_adjust_balance(
    employee_id: UUID,
    body: LeaveBalanceAdjustBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    fy = body.fiscal_year or _current_fy(db)

    # Lifecycle gate: you cannot CREDIT new leave to someone who is leaving or has
    # left (only ACTIVE / ON_PROBATION may receive new entitlement); DEBIT
    # corrections are allowed while ON_NOTICE but blocked once fully separated.
    emp = db.query(Employee).filter(
        Employee.id == employee_id, Employee.is_deleted == False,  # noqa: E712
    ).first()
    _delta = Decimal(body.delta)
    if _delta > 0:
        guard_employable(emp, "credit leave balance to")
    elif _delta < 0:
        guard_settleable(emp, "adjust leave balance for")
    elif emp is None:
        raise HTTPException(404, "Employee not found")

    b = _get_or_create_balance(db, employee_id, body.leave_type, fy)
    policy = db.query(LeavePolicy).filter(LeavePolicy.leave_type == body.leave_type).first()

    delta = Decimal(body.delta)
    # Enforce the policy cap on CREDIT adjustments. A capped leave type
    # (annual_quota > 0) can never hold more than its quota plus whatever was
    # legitimately carried forward — without this, an admin could credit a
    # 5-day Bereavement balance up to 6+ (the reported bug). Uncapped types
    # (annual_quota == 0, e.g. Comp-Off / LWP / Study / Special) are exempt.
    if delta > 0 and policy is not None:
        quota = Decimal(policy.annual_quota or 0)
        if quota > 0:
            current = _recompute_closing(b)
            cap = quota + Decimal(b.carry_forward_in or 0)
            projected = current + delta
            if projected > cap:
                room = cap - current
                carried = Decimal(b.carry_forward_in or 0)
                cap_desc = (f"policy quota {quota}"
                            + (f" + {carried} carried-forward" if carried > 0 else ""))
                raise HTTPException(
                    422,
                    f"{body.leave_type.value} balance is capped at {cap} day(s) ({cap_desc}). "
                    f"Current balance is {current}; a credit of {delta} would exceed the cap by "
                    f"{projected - cap}. You can credit at most {room if room > 0 else 0} more day(s).",
                )

    _apply_ledger(
        db, b, kind=LedgerKind.ADMIN_ADJUST,
        delta=delta, actor=admin, note=body.reason,
    )
    db.commit()
    db.refresh(b)
    try:
        log(db, actor_id=admin.id, action=AttendanceLogAction.LEAVE_BALANCE_ADJUSTED,
            target_table="hr_leave_balances", target_id=b.id, employee_id=employee_id,
            payload={"type": body.leave_type.value, "fy": fy, "delta": float(body.delta),
                     "reason": body.reason})
        db.commit()
    except Exception:
        db.rollback()
    return _balance_to_response(db, b, policy)


@router.post("/balances/grant-policy")
def admin_grant_policy(
    body: LeaveGrantPolicyBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Bulk top-up: lift every eligible employee's available balance UP TO the
    policy annual quota for the chosen (capped) leave types. Idempotent — anyone
    already at/above quota is skipped and nobody is ever reduced. Only ACTIVE /
    ON_PROBATION employees are credited (leaving / separated employees are
    skipped, mirroring the single-adjust lifecycle guard)."""
    fy = body.fiscal_year or _current_fy(db)

    # Grantable = annually-credited policies only: annual_quota > 0 AND
    # monthly_accrual == 0. Accrual-based types (e.g. Earned) are credited
    # automatically by cron/accrue-monthly — bulk-granting them here would
    # double-credit on top of accrual, so they're excluded.
    policies = [
        p for p in db.query(LeavePolicy).filter(
            LeavePolicy.is_active == True,   # noqa: E712
            LeavePolicy.is_deleted == False,  # noqa: E712
        ).all()
        if Decimal(p.annual_quota or 0) > 0 and Decimal(p.monthly_accrual or 0) == 0
        and p.leave_type != LeaveType.LWP   # LWP is unpaid — never a granted entitlement
    ]
    if body.leave_types:
        wanted = set(body.leave_types)
        policies = [p for p in policies if p.leave_type in wanted]
    if not policies:
        raise HTTPException(422, "No annually-credited leave policy matches the selection "
                                 "(accrual-based types like Earned are credited monthly, not granted here).")

    eq = db.query(Employee).filter(Employee.is_deleted == False)  # noqa: E712
    if body.employee_ids:
        eq = eq.filter(Employee.id.in_(body.employee_ids))
    if body.department_id:
        eq = eq.filter(Employee.department_id == body.department_id)
    employees = eq.all()

    note = body.reason or f"Bulk policy grant · FY {fy}"
    granted_rows = 0
    credited_days = Decimal("0")
    touched: set = set()
    skipped_ineligible = 0
    skipped_satisfied = 0

    for emp in employees:
        if not is_employable(emp):
            skipped_ineligible += 1
            continue
        for p in policies:
            b = _get_or_create_balance(db, emp.id, p.leave_type, fy)
            # Top up the ENTITLEMENT already credited this FY — NOT the available
            # balance. `credited` excludes used/encashed, so an employee who has
            # already received their annual quota gets nothing even after using
            # some of it (this closes the "re-credit the used days" loophole).
            credited = (Decimal(b.opening_balance or 0) + Decimal(b.accrued or 0)
                        + Decimal(b.carry_forward_in or 0) + Decimal(b.adjustments or 0))
            target = Decimal(p.annual_quota or 0) + Decimal(b.carry_forward_in or 0)
            delta = target - credited
            if delta <= 0:
                skipped_satisfied += 1
                continue
            _apply_ledger(db, b, kind=LedgerKind.ADMIN_ADJUST, delta=delta, actor=admin, note=note)
            granted_rows += 1
            credited_days += delta
            touched.add(emp.id)
    db.commit()

    try:
        log(db, actor_id=admin.id, action=AttendanceLogAction.LEAVE_BALANCE_ADJUSTED,
            target_table="hr_leave_balances", target_id=None,
            payload={"bulk_policy_grant": True, "fy": fy,
                     "types": [p.leave_type.value for p in policies],
                     "employees": len(touched), "rows": granted_rows,
                     "days": float(credited_days), "reason": body.reason})
        db.commit()
    except Exception:
        db.rollback()

    return {
        "fiscal_year": fy,
        "granted_rows": granted_rows,
        "credited_days": float(credited_days),
        "employees_credited": len(touched),
        "employees_in_scope": len(employees),
        "skipped_ineligible": skipped_ineligible,
        "skipped_already_satisfied": skipped_satisfied,
        "leave_types": [p.leave_type.value for p in policies],
    }


# ═════════════════════════════════════════════════════════════════════════════
# Calendar
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/calendar", response_model=LeaveCalendarResponse)
def calendar_view(
    from_: date = Query(..., alias="from"),
    to: date = None,
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    to = to or (from_ + timedelta(days=30))
    q = db.query(LeaveRequest).filter(
        LeaveRequest.is_deleted == False,  # noqa: E712
        LeaveRequest.status.in_([LeaveStatus.APPROVED, LeaveStatus.PENDING_HR]),
        LeaveRequest.to_date >= from_, LeaveRequest.from_date <= to,
    )
    if department_id:
        q = q.join(Employee, Employee.id == LeaveRequest.employee_id).filter(
            Employee.department_id == department_id,
        )
    rows = q.order_by(LeaveRequest.from_date).all()
    policy_color = {p.leave_type: p.color_hex for p in db.query(LeavePolicy).all()}
    items: List[LeaveCalendarEntry] = []
    for r in rows:
        snap = _employee_snapshot(db, r.employee_id)
        items.append(LeaveCalendarEntry(
            id=r.id, reference_no=r.reference_no,
            employee_id=r.employee_id, employee_name=snap.get("name"),
            employee_code=snap.get("code"), department_name=snap.get("dept"),
            leave_type=r.leave_type, from_date=r.from_date, to_date=r.to_date,
            is_half_day=bool(r.is_half_day), which_session=r.which_session,
            status=r.status, color_hex=policy_color.get(r.leave_type),
        ))
    return LeaveCalendarResponse(items=items, from_date=from_, to_date=to, total=len(items))


# ═════════════════════════════════════════════════════════════════════════════
# Single record + history + delete
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/{leave_id:uuid}", response_model=LeaveRequestResponse)
def get_leave(
    leave_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    leave = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not leave:
        raise HTTPException(404, "Leave request not found")
    return _to_response(db, leave, include_breakdown=True)


@router.get("/{leave_id:uuid}/history", response_model=LeaveHistoryListResponse)
def leave_history(
    leave_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = db.query(LeaveBalanceHistory).filter(
        LeaveBalanceHistory.related_request_id == leave_id,
    ).order_by(LeaveBalanceHistory.created_at.asc()).all()
    items: List[LeaveHistoryResponse] = []
    for h in rows:
        items.append(LeaveHistoryResponse(
            id=h.id, employee_id=h.employee_id, leave_type=h.leave_type,
            fiscal_year=h.fiscal_year, kind=h.kind, delta=Decimal(h.delta),
            balance_before=Decimal(h.balance_before), balance_after=Decimal(h.balance_after),
            note=h.note, actor_user_id=h.actor_user_id,
            actor_name=_user_name(db, h.actor_user_id),
            related_request_id=h.related_request_id, created_at=h.created_at,
        ))
    return LeaveHistoryListResponse(items=items, total=len(items))


@router.delete("/{leave_id:uuid}", status_code=http_status.HTTP_204_NO_CONTENT)
def admin_delete_leave(
    leave_id: UUID,
    payload: Optional[LeaveDeleteBody] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Soft-delete. If the row was APPROVED, reverse the balance debit and
    re-run daily_rollup so attendance unwinds back to whatever the punches imply."""
    leave = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).first()
    if not leave:
        raise HTTPException(404, "Leave request not found")
    was_approved = leave.status == LeaveStatus.APPROVED
    leave.is_deleted = True
    leave.status = LeaveStatus.CANCELLED
    leave.cancelled_at = datetime.now(timezone.utc)
    leave.cancelled_by_id = admin.id
    cleaned = (payload.reason.strip() if payload and payload.reason else None)
    leave.cancelled_reason = cleaned or "Admin delete"
    db.commit()

    if was_approved and leave.leave_type != LeaveType.LWP:
        # reverse debit
        for fy_label, days in (leave.fy_breakdown or {}).items():
            b = _get_or_create_balance(db, leave.employee_id, leave.leave_type, fy_label)
            try:
                _apply_ledger(
                    db, b, kind=LedgerKind.REQUEST_CANCELLED,
                    delta=Decimal(str(float(days))), actor=admin,
                    note=f"Reversed on delete {leave.reference_no}",
                    related_request_id=leave.id,
                )
            except HTTPException:
                pass
        db.commit()
        _rollup_leave_dates(db, leave, actor_id=admin.id)
        db.commit()

    try:
        log(db, actor_id=admin.id, action=AttendanceLogAction.LEAVE_CANCELLED,
            target_table="hr_leave_requests", target_id=leave.id, employee_id=leave.employee_id,
            payload={"ref": leave.reference_no, "was_approved": was_approved,
                     "reason": cleaned, "reason_category": payload.reason_category if payload else None})
        db.commit()
    except Exception:
        db.rollback()


# ═════════════════════════════════════════════════════════════════════════════
# Cron — accrual + carry-forward
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/cron/accrue-monthly", response_model=CronRunResult)
def cron_accrue_monthly(
    body: AccrueMonthlyBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Run monthly accrual for the supplied (or current) month. Idempotent:
    skips (employee, leave_type, month) combinations that already have an
    ACCRUAL ledger entry for the same month.
    """
    month_str = body.month or date.today().strftime("%Y-%m")
    y, m = (int(x) for x in month_str.split("-"))
    month_anchor = date(y, m, 1)
    fy = _fy_for(month_anchor, _get_setting(db, "fiscal_year_start", "04-01"))

    policies = db.query(LeavePolicy).filter(
        LeavePolicy.is_deleted == False, LeavePolicy.is_active == True,  # noqa: E712
        LeavePolicy.monthly_accrual > 0,
    ).all()
    if not policies:
        return CronRunResult(processed=0, skipped_existing=0, fiscal_year=fy, month=month_str,
                             notes="No policies have monthly_accrual > 0")

    active_emp_ids = [
        r[0] for r in db.query(Employee.id).filter(Employee.is_deleted == False).all()  # noqa: E712
    ]

    processed = 0
    skipped = 0
    for emp_id in active_emp_ids:
        for p in policies:
            # Idempotency: check existing ACCRUAL row for this month
            month_start_dt = datetime(y, m, 1, tzinfo=timezone.utc)
            next_month = datetime(y + (1 if m == 12 else 0), (m % 12) + 1, 1, tzinfo=timezone.utc)
            existing = db.query(LeaveBalanceHistory.id).filter(
                LeaveBalanceHistory.employee_id == emp_id,
                LeaveBalanceHistory.leave_type == p.leave_type,
                LeaveBalanceHistory.fiscal_year == fy,
                LeaveBalanceHistory.kind == LedgerKind.ACCRUAL,
                LeaveBalanceHistory.created_at >= month_start_dt,
                LeaveBalanceHistory.created_at < next_month,
            ).first()
            if existing:
                skipped += 1
                continue
            b = _get_or_create_balance(db, emp_id, p.leave_type, fy)
            # Cap accrual at the annual quota (fresh side = opening + accrued +
            # adjustments, carry-forward is separate). This makes accrual safe to
            # coexist with manual grants/back-credits and guarantees the credited
            # entitlement never exceeds the annual quota.
            fresh = (Decimal(b.opening_balance or 0) + Decimal(b.accrued or 0)
                     + Decimal(b.adjustments or 0))
            room = Decimal(p.annual_quota or 0) - fresh
            if room <= 0:
                skipped += 1
                continue
            delta = min(Decimal(p.monthly_accrual), room)
            try:
                _apply_ledger(db, b, kind=LedgerKind.ACCRUAL,
                              delta=delta, actor=admin,
                              note=f"Monthly accrual {month_str}")
                processed += 1
            except HTTPException:
                pass
    db.commit()
    try:
        log(db, actor_id=admin.id, action=AttendanceLogAction.LEAVE_BALANCE_ACCRUED,
            target_table="hr_leave_balances", target_id=None, employee_id=None,
            payload={"month": month_str, "processed": processed, "skipped": skipped})
        db.commit()
    except Exception:
        db.rollback()
    return CronRunResult(processed=processed, skipped_existing=skipped,
                         fiscal_year=fy, month=month_str)


@router.post("/cron/accrue-catchup", response_model=CronRunResult)
def cron_accrue_catchup(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Catch-up accrual for the CURRENT fiscal year: runs monthly accrual for
    every month from the FY start through the current month. Idempotent — each
    month skips if already accrued, and the per-month cap keeps everyone at or
    below their annual quota. This is the correct way to credit accrual-based
    leaves (Casual, Earned) that were never accrued (e.g. before the scheduler
    was wired) — NOT a lump-sum grant."""
    fy_start = _get_setting(db, "fiscal_year_start", "04-01")
    try:
        sm, sd = (int(x) for x in fy_start.split("-"))
    except Exception:
        sm, sd = 4, 1
    today = date.today()
    start_year = today.year if (today.month, today.day) >= (sm, sd) else today.year - 1
    cursor = date(start_year, sm, 1)
    processed = total_skipped = 0
    months = 0
    while (cursor.year, cursor.month) <= (today.year, today.month):
        res = cron_accrue_monthly(AccrueMonthlyBody(month=cursor.strftime("%Y-%m")), db=db, admin=admin)
        processed += res.processed
        total_skipped += res.skipped_existing
        months += 1
        cursor = date(cursor.year + (1 if cursor.month == 12 else 0), (cursor.month % 12) + 1, 1)
    return CronRunResult(processed=processed, skipped_existing=total_skipped,
                         fiscal_year=_current_fy(db),
                         notes=f"Caught up {months} month(s) of accrual for the current FY")


@router.post("/cron/carry-forward", response_model=CronRunResult)
def cron_carry_forward(
    body: CarryForwardBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Close `from_fy` and seed `to_fy` with carry-forward credits."""
    policies = {p.leave_type: p for p in db.query(LeavePolicy).all()}
    processed = 0
    rows = db.query(LeaveBalance).filter(
        LeaveBalance.fiscal_year == body.from_fy,
        LeaveBalance.is_deleted == False,  # noqa: E712
    ).all()
    for old in rows:
        policy = policies.get(old.leave_type)
        cf_cap = Decimal(policy.max_carry_forward) if policy else Decimal("0")
        if cf_cap <= 0:
            continue
        closing = Decimal(old.closing_balance or 0)
        carry = min(closing, cf_cap)
        if carry <= 0:
            continue
        new_b = _get_or_create_balance(db, old.employee_id, old.leave_type, body.to_fy)
        try:
            _apply_ledger(db, new_b, kind=LedgerKind.CARRY_FORWARD,
                          delta=carry, actor=admin,
                          note=f"Carry-forward {body.from_fy} → {body.to_fy}")
            processed += 1
        except HTTPException:
            pass
    db.commit()
    try:
        log(db, actor_id=admin.id, action=AttendanceLogAction.LEAVE_BALANCE_CARRY_FORWARD,
            target_table="hr_leave_balances", target_id=None, employee_id=None,
            payload={"from_fy": body.from_fy, "to_fy": body.to_fy, "processed": processed})
        db.commit()
    except Exception:
        db.rollback()
    return CronRunResult(processed=processed, skipped_existing=0,
                         fiscal_year=body.to_fy, notes=f"Carried forward from {body.from_fy}")


# ═════════════════════════════════════════════════════════════════════════════
# Phase 2 — Comp-Off
# ═════════════════════════════════════════════════════════════════════════════

def _compoff_expiry_days(db: Session) -> int:
    val = _get_setting(db, "comp_off_expiry_days", "90")
    try:
        return int(val)
    except Exception:
        return 90


def _compoff_entry(db: Session, h: LeaveBalanceHistory) -> CompOffEntry:
    snap = _employee_snapshot(db, h.employee_id)
    today = date.today()
    days_until = None
    is_expired = False
    if h.expires_on:
        delta = (h.expires_on - today).days
        days_until = delta
        is_expired = delta < 0
    return CompOffEntry(
        id=h.id, employee_id=h.employee_id,
        employee_name=snap.get("name"), employee_code=snap.get("code"),
        department_name=snap.get("dept"),
        days=Decimal(h.delta or 0),
        earned_on=h.earned_on, expires_on=h.expires_on,
        is_auto_generated=bool(h.is_auto_generated),
        note=h.note, actor_name=_user_name(db, h.actor_user_id),
        created_at=h.created_at,
        is_expired=is_expired, days_until_expiry=days_until,
    )


@router.get("/comp-off", response_model=CompOffListResponse)
def admin_list_compoff(
    employee_id: Optional[UUID] = None,
    department_id: Optional[UUID] = None,
    only_active: bool = Query(True, description="Hide expired & fully-used grants"),
    fy: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    fy = fy or _current_fy(db)
    q = db.query(LeaveBalanceHistory).filter(
        LeaveBalanceHistory.leave_type == LeaveType.COMP_OFF,
        LeaveBalanceHistory.kind == LedgerKind.COMP_OFF_EARNED,
        LeaveBalanceHistory.fiscal_year == fy,
    )
    if employee_id:
        q = q.filter(LeaveBalanceHistory.employee_id == employee_id)
    if department_id:
        q = q.join(Employee, Employee.id == LeaveBalanceHistory.employee_id).filter(
            Employee.department_id == department_id
        )
    if only_active:
        today = date.today()
        q = q.filter(or_(
            LeaveBalanceHistory.expires_on.is_(None),
            LeaveBalanceHistory.expires_on >= today,
        ))
    total = q.count()
    rows = (
        q.order_by(LeaveBalanceHistory.earned_on.desc().nullslast(),
                   LeaveBalanceHistory.created_at.desc())
         .offset((page - 1) * limit).limit(limit).all()
    )
    return CompOffListResponse(
        items=[_compoff_entry(db, r) for r in rows],
        total=total, fiscal_year=fy,
    )


@router.get("/comp-off/stats", response_model=CompOffStats)
def admin_compoff_stats(
    fy: Optional[str] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    fy = fy or _current_fy(db)
    base = db.query(LeaveBalanceHistory).filter(
        LeaveBalanceHistory.leave_type == LeaveType.COMP_OFF,
        LeaveBalanceHistory.fiscal_year == fy,
    )
    today = date.today()
    earned_q = base.filter(LeaveBalanceHistory.kind == LedgerKind.COMP_OFF_EARNED)
    total_earned = Decimal(earned_q.with_entities(sa_func.coalesce(sa_func.sum(LeaveBalanceHistory.delta), 0)).scalar() or 0)
    total_used = Decimal(base.filter(LeaveBalanceHistory.kind == LedgerKind.COMP_OFF_USED)
                         .with_entities(sa_func.coalesce(sa_func.sum(LeaveBalanceHistory.delta), 0))
                         .scalar() or 0)
    # used is stored as positive in history.delta? In _apply_ledger we write -delta into used.
    # For display we want the absolute value of used.
    total_used = abs(total_used)
    total_expired = Decimal(base.filter(LeaveBalanceHistory.kind == LedgerKind.COMP_OFF_EXPIRED)
                            .with_entities(sa_func.coalesce(sa_func.sum(LeaveBalanceHistory.delta), 0))
                            .scalar() or 0)
    total_expired = abs(total_expired)
    # Active = earned - used - expired (subset of earned still usable)
    active = total_earned - total_used - total_expired
    expiring_30 = earned_q.filter(
        LeaveBalanceHistory.expires_on.isnot(None),
        LeaveBalanceHistory.expires_on >= today,
        LeaveBalanceHistory.expires_on <= today + timedelta(days=30),
    ).count()
    auto_n = earned_q.filter(LeaveBalanceHistory.is_auto_generated == True).count()  # noqa: E712
    manual_n = earned_q.filter(LeaveBalanceHistory.is_auto_generated == False).count()  # noqa: E712
    return CompOffStats(
        total_earned=total_earned, total_used=total_used, total_expired=total_expired,
        balance_active=max(Decimal("0"), active),
        expiring_in_30d=int(expiring_30),
        auto_generated_count=int(auto_n), manual_count=int(manual_n),
    )


@router.post("/comp-off/grant", response_model=CompOffEntry, status_code=http_status.HTTP_201_CREATED)
def admin_grant_compoff(
    body: CompOffGrantBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    emp = db.query(Employee).filter(
        Employee.id == body.employee_id, Employee.is_deleted == False,  # noqa: E712
    ).first()
    if not emp:
        raise HTTPException(404, "Employee not found")
    # Comp-off compensates for a holiday/week-off the employee has ALREADY
    # worked — the worked date cannot be in the future (you can't pre-credit
    # leave for work that hasn't happened). Only the expiry date looks ahead.
    if body.earned_on > date.today():
        raise HTTPException(
            422,
            "Comp-off is earned for a day already worked — the worked date can't be in the future.",
        )
    fy = _fy_for(body.earned_on, _get_setting(db, "fiscal_year_start", "04-01"))
    # Block duplicate manual grants for the same date
    dup = db.query(LeaveBalanceHistory.id).filter(
        LeaveBalanceHistory.employee_id == emp.id,
        LeaveBalanceHistory.leave_type == LeaveType.COMP_OFF,
        LeaveBalanceHistory.kind == LedgerKind.COMP_OFF_EARNED,
        LeaveBalanceHistory.earned_on == body.earned_on,
    ).first()
    if dup:
        raise HTTPException(409, f"Comp-off already credited for {body.earned_on}")

    bal = _get_or_create_balance(db, emp.id, LeaveType.COMP_OFF, fy)
    before = _recompute_closing(bal)
    bal.adjustments = Decimal(bal.adjustments or 0) + body.days
    after = before + body.days
    bal.closing_balance = after
    expires = body.expires_on or (body.earned_on + timedelta(days=_compoff_expiry_days(db)))
    h = LeaveBalanceHistory(
        employee_id=emp.id, leave_type=LeaveType.COMP_OFF, fiscal_year=fy,
        kind=LedgerKind.COMP_OFF_EARNED,
        delta=body.days, balance_before=before, balance_after=after,
        actor_user_id=admin.id, note=body.reason,
        is_auto_generated=False, earned_on=body.earned_on, expires_on=expires,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    try:
        log(db, actor_id=admin.id, action=AttendanceLogAction.COMP_OFF_GRANTED,
            target_table="hr_leave_balance_history", target_id=h.id, employee_id=emp.id,
            payload={"earned_on": body.earned_on.isoformat(), "days": float(body.days),
                     "reason": body.reason, "expires_on": expires.isoformat()})
        db.add(Notification(
            user_id=emp.user_id, type="leave_approved", title="Comp-off credited",
            message=f"{body.days} comp-off day(s) for working on {body.earned_on}",
            related_user_id=admin.id, is_read=False,
        ))
        db.commit()
    except Exception:
        db.rollback()
    return _compoff_entry(db, h)


def _compoff_or_404(db: Session, compoff_id: UUID) -> LeaveBalanceHistory:
    h = db.query(LeaveBalanceHistory).filter(
        LeaveBalanceHistory.id == compoff_id,
        LeaveBalanceHistory.leave_type == LeaveType.COMP_OFF,
        LeaveBalanceHistory.kind == LedgerKind.COMP_OFF_EARNED,
    ).first()
    if not h:
        raise HTTPException(404, "Comp-off credit not found")
    return h


def _compoff_impact(db: Session, h: LeaveBalanceHistory) -> CompOffImpact:
    snap = _employee_snapshot(db, h.employee_id)
    days = Decimal(h.delta or 0)
    bal = db.query(LeaveBalance).filter(
        LeaveBalance.employee_id == h.employee_id,
        LeaveBalance.leave_type == LeaveType.COMP_OFF,
        LeaveBalance.fiscal_year == h.fiscal_year,
        LeaveBalance.is_deleted == False,  # noqa: E712
    ).first()
    active = Decimal(bal.closing_balance or 0) if bal else Decimal("0")
    after = active - days
    today = date.today()
    is_expired = bool(h.expires_on and (h.expires_on - today).days < 0)
    return CompOffImpact(
        id=h.id, employee_id=h.employee_id,
        employee_name=snap.get("name"), employee_code=snap.get("code"),
        days=days, earned_on=h.earned_on, expires_on=h.expires_on,
        is_auto_generated=bool(h.is_auto_generated), is_expired=is_expired,
        fiscal_year=h.fiscal_year,
        balance_active=active, balance_after=after,
        # An already-expired credit no longer counts toward the live reserve, so
        # removing it can't overdraw; otherwise a negative result means it was spent.
        would_go_negative=(not is_expired) and after < 0,
        note=h.note,
    )


@router.get("/comp-off/{compoff_id:uuid}/impact", response_model=CompOffImpact)
def compoff_impact(
    compoff_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """How removing a comp-off credit affects the employee's reserve."""
    return _compoff_impact(db, _compoff_or_404(db, compoff_id))


@router.delete("/comp-off/{compoff_id:uuid}", status_code=200)
def delete_compoff(
    compoff_id: UUID,
    body: CompOffDeleteBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Revoke a comp-off credit — reverses the balance and removes the grant.

    The ledger is append-only by convention, so we don't keep a tombstoned
    EARNED row (it would keep showing as an active credit). Instead we reverse
    the balance contribution, hard-delete the earned row, and write a permanent
    COMP_OFF_REVOKED audit entry capturing the full snapshot + reason. A reason
    is mandatory; risky reversals (would overdraw the reserve) require the
    caller to also set ``acknowledge_impact``.
    """
    h = _compoff_or_404(db, compoff_id)
    impact = _compoff_impact(db, h)
    if impact.would_go_negative and not body.acknowledge_impact:
        raise HTTPException(
            409,
            f"Reversing {impact.days} day(s) would overdraw {impact.employee_name or 'the employee'}'s "
            f"reserve (active {impact.balance_active} → {impact.balance_after}). "
            "The credit was likely already spent — re-confirm to proceed.",
        )

    days = Decimal(h.delta or 0)
    snapshot = {
        "compoff_id": str(h.id),
        "earned_on": h.earned_on.isoformat() if h.earned_on else None,
        "expires_on": h.expires_on.isoformat() if h.expires_on else None,
        "days": float(days),
        "is_auto_generated": bool(h.is_auto_generated),
        "original_note": h.note,
        "reason": body.reason,
        "reason_category": body.reason_category,
        "balance_active_before": float(impact.balance_active),
        "balance_after": float(impact.balance_after),
    }
    employee_id = h.employee_id

    # Reverse the balance contribution the grant added via `adjustments`.
    bal = db.query(LeaveBalance).filter(
        LeaveBalance.employee_id == h.employee_id,
        LeaveBalance.leave_type == LeaveType.COMP_OFF,
        LeaveBalance.fiscal_year == h.fiscal_year,
        LeaveBalance.is_deleted == False,  # noqa: E712
    ).first()
    if bal:
        bal.adjustments = Decimal(bal.adjustments or 0) - days
        bal.closing_balance = _recompute_closing(bal)

    db.delete(h)
    db.commit()

    try:
        log(db, actor_id=admin.id, action=AttendanceLogAction.COMP_OFF_REVOKED,
            target_table="hr_leave_balance_history", target_id=compoff_id,
            employee_id=employee_id, payload=snapshot)
        db.commit()
    except Exception:
        db.rollback()

    return {"ok": True, "revoked": True, "impact": impact.model_dump(mode="json")}


@router.get("/me/comp-off", response_model=CompOffListResponse)
def my_compoff(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    fy = _current_fy(db)
    rows = (
        db.query(LeaveBalanceHistory)
        .filter(
            LeaveBalanceHistory.employee_id == emp.id,
            LeaveBalanceHistory.leave_type == LeaveType.COMP_OFF,
            LeaveBalanceHistory.kind == LedgerKind.COMP_OFF_EARNED,
            LeaveBalanceHistory.fiscal_year == fy,
        )
        .order_by(LeaveBalanceHistory.earned_on.desc().nullslast(),
                  LeaveBalanceHistory.created_at.desc())
        .all()
    )
    return CompOffListResponse(
        items=[_compoff_entry(db, r) for r in rows],
        total=len(rows), fiscal_year=fy,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Phase 2 — Leave Encashment
# ═════════════════════════════════════════════════════════════════════════════

def _encashment_base(db: Session, emp: Optional[Employee]) -> Decimal:
    """Per-month pool leave encashment is computed against, from the single source
    of truth — HR Settings → Payroll Rules ``ENCASHMENT_BASIS`` (BASIC|GROSS|CTC).

    Mirrors the exit settlement engine's ``_settlement_base`` exactly so in-service
    encashment and final-settlement encashment use the *same* basis and produce the
    same numbers. Default BASIC. Falls back through comp → employee heuristics.
    """
    if emp is None:
        return Decimal("0")
    from app.utils.hr.payroll.rule_config import get_rule
    from app.models.hr.employee_compensation import EmployeeCompensation
    basis = str(get_rule(db, "ENCASHMENT_BASIS") or "BASIC").upper()
    comp = (db.query(EmployeeCompensation)
            .filter(EmployeeCompensation.employee_id == emp.id,
                    EmployeeCompensation.is_deleted == False,   # noqa: E712
                    EmployeeCompensation.is_active == True)      # noqa: E712
            .order_by(EmployeeCompensation.effective_from.desc())
            .first())
    if basis == "GROSS":
        if comp and comp.monthly_gross:
            return Decimal(str(comp.monthly_gross))
        if comp and comp.monthly_ctc:
            return Decimal(str(comp.monthly_ctc))
        return Decimal(str(emp.monthly_ctc or 0))
    if basis == "CTC":
        if comp and comp.monthly_ctc:
            return Decimal(str(comp.monthly_ctc))
        return Decimal(str(emp.monthly_ctc or 0))
    # BASIC (default) — explicit basic, else heuristic 50% of gross/CTC.
    if comp and comp.basic_amount:
        return Decimal(str(comp.basic_amount))
    if comp and comp.monthly_gross:
        return (Decimal(str(comp.monthly_gross)) * Decimal("0.5"))
    if emp.monthly_ctc:
        return (Decimal(str(emp.monthly_ctc)) * Decimal("0.5"))
    return Decimal("0")


def _compute_encashment(
    db: Session, employee_id: UUID, leave_type: LeaveType,
    days_requested: Decimal, basic_override: Optional[Decimal] = None,
) -> dict:
    """Apply system_settings.leave_encashment_formula. The default formula is
    `basic_salary * days_encashed / 30`. Admin can edit it via the settings.

    The ``basic_salary`` fed to the formula is resolved from the single source of
    truth — Payroll Rules ``ENCASHMENT_BASIS`` (shared with exit settlement) — so
    changing the basis applies consistently to in-service AND final-settlement
    encashment. An explicit ``basic_override`` (admin) still wins.
    Returns dict with amount, formula_used, basic_salary, available_balance.
    """
    formula = _get_setting(db, "leave_encashment_formula", "basic_salary * days_encashed / 30")
    fy = _current_fy(db)
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    basic = Decimal(basic_override) if basic_override else _encashment_base(db, emp)
    bal = _get_or_create_balance(db, employee_id, leave_type, fy)
    available = Decimal(bal.closing_balance or 0)
    # Safe formula eval — only allows the two named variables and basic arithmetic.
    safe_globals = {"__builtins__": {}}
    safe_locals = {
        "basic_salary": float(basic),
        "days_encashed": float(days_requested),
    }
    try:
        amount = Decimal(str(round(eval(formula, safe_globals, safe_locals), 2)))  # noqa: S307
    except Exception:
        amount = (basic * Decimal(days_requested) / Decimal("30")).quantize(Decimal("0.01"))
    return {
        "amount": amount, "formula_used": formula, "basic_salary": basic,
        "available_balance": available, "fiscal_year": fy,
    }


def _encash_to_response(db: Session, e: LeaveEncashment) -> EncashmentResponse:
    snap = _employee_snapshot(db, e.employee_id)
    return EncashmentResponse(
        id=e.id, reference_no=e.reference_no,
        employee_id=e.employee_id,
        employee_name=snap.get("name"), employee_code=snap.get("code"),
        department_name=snap.get("dept"),
        leave_type=e.leave_type, fiscal_year=e.fiscal_year,
        days_requested=Decimal(e.days_requested or 0),
        basic_salary_snapshot=Decimal(e.basic_salary_snapshot or 0),
        formula_used=e.formula_used,
        amount=Decimal(e.amount or 0),
        status=e.status,
        request_notes=e.request_notes,
        manager_id=e.manager_id, manager_name=_user_name(db, e.manager_id),
        manager_decision=e.manager_decision, manager_decided_at=e.manager_decided_at,
        manager_notes=e.manager_notes,
        decided_by_id=e.decided_by_id, decided_by_name=_user_name(db, e.decided_by_id),
        decided_at=e.decided_at, decision_notes=e.decision_notes,
        paid_at=e.paid_at, paid_by_id=e.paid_by_id, payroll_ref=e.payroll_ref,
        created_at=e.created_at,
    )


def _generate_encash_ref(db: Session, fy: str) -> str:
    yy = fy.split("-")[0][-2:]
    for _ in range(6):
        row = db.query(SystemSetting).filter(SystemSetting.key == "leave_encash_counter").first()
        if row:
            try:
                n = int(row.value) + 1
            except Exception:
                n = 1
            row.value = str(n)
        else:
            n = 1
            db.add(SystemSetting(key="leave_encash_counter", value="1",
                                 description="Counter for LeaveEncashment.reference_no"))
        db.flush()
        candidate = f"EN-{yy}-{n:06d}"
        if not db.query(LeaveEncashment.id).filter(LeaveEncashment.reference_no == candidate).first():
            return candidate
    raise HTTPException(500, "Could not allocate encashment reference")


@router.get("/me/encashment/options", response_model=List[EncashmentOption])
def my_encashment_options(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Encashable leave types for the calling employee + their available balance.

    Employee-safe alternative to the superuser-only `GET /policies` — the
    self-service modal uses this to populate the leave-type picker.
    """
    emp = _try_self_employee(db, user)
    if not emp:
        return []
    fy = _current_fy(db)
    pols = db.query(LeavePolicy).filter(
        LeavePolicy.is_deleted == False,        # noqa: E712
        LeavePolicy.is_active == True,          # noqa: E712
        LeavePolicy.encashment_allowed == True,  # noqa: E712
    ).order_by(LeavePolicy.leave_type).all()
    out = []
    for p in pols:
        bal = db.query(LeaveBalance).filter(
            LeaveBalance.employee_id == emp.id,
            LeaveBalance.leave_type == p.leave_type,
            LeaveBalance.fiscal_year == fy,
            LeaveBalance.is_deleted == False,    # noqa: E712
        ).first()
        out.append(EncashmentOption(
            leave_type=p.leave_type,
            label=p.label or p.leave_type.value,
            available_balance=Decimal(bal.closing_balance or 0) if bal else Decimal("0"),
        ))
    return out


@router.post("/me/encashment/preview", response_model=EncashmentPreviewResponse)
def my_encashment_preview(
    body: EncashmentPreviewBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    calc = _compute_encashment(db, emp.id, body.leave_type, body.days_requested, body.basic_salary)
    pol = db.query(LeavePolicy).filter(LeavePolicy.leave_type == body.leave_type).first()
    return EncashmentPreviewResponse(
        formula_used=calc["formula_used"],
        basic_salary=calc["basic_salary"],
        days_requested=body.days_requested,
        amount=calc["amount"],
        available_balance=calc["available_balance"],
        encashment_allowed=bool(pol.encashment_allowed) if pol else False,
        fiscal_year=calc["fiscal_year"],
    )


@router.post("/me/encashment", response_model=EncashmentResponse, status_code=http_status.HTTP_201_CREATED)
def my_encashment_create(
    body: EncashmentCreateBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    pol = _policy_or_404(db, body.leave_type)
    if not pol.encashment_allowed:
        raise HTTPException(422, f"{body.leave_type.value} is not encashable")
    calc = _compute_encashment(db, emp.id, body.leave_type, body.days_requested)
    if calc["available_balance"] < body.days_requested:
        raise HTTPException(422, f"Insufficient {body.leave_type.value} balance: {calc['available_balance']} available")
    fy = calc["fiscal_year"]
    ref = _generate_encash_ref(db, fy)
    # Stage 1 routing — endorse via the reporting manager first, unless the
    # employee has no manager or is their own manager (then skip straight to HR).
    has_manager = bool(emp.reporting_manager_id) and emp.reporting_manager_id != user.id
    e = LeaveEncashment(
        reference_no=ref, employee_id=emp.id, leave_type=body.leave_type, fiscal_year=fy,
        days_requested=body.days_requested,
        basic_salary_snapshot=calc["basic_salary"], formula_used=calc["formula_used"],
        amount=calc["amount"],
        status=EncashmentStatus.PENDING_MANAGER if has_manager else EncashmentStatus.PENDING,
        request_notes=body.request_notes,
        manager_id=emp.reporting_manager_id if has_manager else None,
        manager_decision=None if has_manager else "SKIPPED",
        manager_decided_at=None if has_manager else datetime.now(timezone.utc),
        manager_notes=None if has_manager else "No reporting manager — routed to HR",
    )
    db.add(e)
    db.commit(); db.refresh(e)
    try:
        log(db, actor_id=user.id, action=AttendanceLogAction.ENCASHMENT_REQUESTED,
            target_table="hr_leave_encashments", target_id=e.id, employee_id=emp.id,
            payload={"ref": ref, "days": float(body.days_requested), "amount": float(calc["amount"]),
                     "routed_to": "manager" if has_manager else "hr"})
        db.commit()
    except Exception:
        db.rollback()
    return _encash_to_response(db, e)


@router.get("/encashment/manager/queue", response_model=EncashmentListResponse)
def manager_encashment_queue(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Encashment requests awaiting the calling user's manager endorsement."""
    team_ids = _get_my_team_employee_ids(db, user.id)
    q = db.query(LeaveEncashment).filter(
        LeaveEncashment.is_deleted == False,  # noqa: E712
        LeaveEncashment.status == EncashmentStatus.PENDING_MANAGER,
        LeaveEncashment.employee_id.in_(team_ids) if team_ids else False,
    )
    total = q.count()
    rows = (
        q.order_by(LeaveEncashment.created_at.asc())
         .offset((page - 1) * limit).limit(limit).all()
    )
    return EncashmentListResponse(
        items=[_encash_to_response(db, r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=max(1, ceil(total / limit) if limit else 1),
    )


@router.patch("/encashment/{enc_id:uuid}/manager-decide", response_model=EncashmentResponse)
def manager_decide_encashment(
    enc_id: UUID,
    body: EncashmentManagerDecideBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reporting-manager endorsement (stage 1). APPROVED → forwards to HR
    (status PENDING); REJECTED → terminal. No balance is touched here — the
    leave balance is only locked at HR sanction."""
    e = db.query(LeaveEncashment).filter(
        LeaveEncashment.id == enc_id, LeaveEncashment.is_deleted == False,  # noqa: E712
    ).with_for_update().first()
    if not e:
        raise HTTPException(404, "Not found")
    if e.status != EncashmentStatus.PENDING_MANAGER:
        raise HTTPException(409, f"Cannot endorse a {e.status.value} request")
    # Only the assigned reporting manager (or a superuser) may endorse.
    emp = db.query(Employee).filter(Employee.id == e.employee_id).first()
    is_manager = emp and emp.reporting_manager_id == user.id
    if not is_manager and not user.is_superuser:
        raise HTTPException(403, "You are not the reporting manager for this employee")

    now = datetime.now(timezone.utc)
    e.manager_id = e.manager_id or (emp.reporting_manager_id if emp else None)
    e.manager_decided_at = now
    e.manager_notes = body.notes
    if body.decision == "APPROVED":
        e.manager_decision = "APPROVED"
        e.status = EncashmentStatus.PENDING          # → HR sanction
        action = AttendanceLogAction.ENCASHMENT_REQUESTED  # endorsement keeps it in-flight
        notif_title, notif_msg = "Encashment endorsed", f"{e.reference_no} · forwarded to HR"
    else:
        e.manager_decision = "REJECTED"
        e.status = EncashmentStatus.REJECTED
        action = AttendanceLogAction.ENCASHMENT_REJECTED
        notif_title, notif_msg = "Encashment update", f"{e.reference_no} · REJECTED by manager"
    db.commit(); db.refresh(e)
    try:
        log(db, actor_id=user.id, action=action,
            target_table="hr_leave_encashments", target_id=e.id, employee_id=e.employee_id,
            payload={"ref": e.reference_no, "stage": "manager", "decision": e.manager_decision,
                     "notes": body.notes})
        db.add(Notification(
            user_id=emp.user_id if emp else None,
            type="leave_approved" if body.decision == "APPROVED" else "leave_rejected",
            title=notif_title, message=notif_msg, related_user_id=user.id, is_read=False,
        ))
        db.commit()
    except Exception:
        db.rollback()
    return _encash_to_response(db, e)


@router.delete("/me/encashment/{enc_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def my_encashment_cancel(
    enc_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    e = db.query(LeaveEncashment).filter(
        LeaveEncashment.id == enc_id,
        LeaveEncashment.is_deleted == False,  # noqa: E712
    ).first()
    if not e:
        raise HTTPException(404, "Not found")
    if e.employee_id != emp.id:
        raise HTTPException(403, "Cannot cancel another employee's request")
    if e.status != EncashmentStatus.PENDING:
        raise HTTPException(409, "Only PENDING requests can be cancelled")
    e.status = EncashmentStatus.CANCELLED
    db.commit()
    try:
        log(db, actor_id=user.id, action=AttendanceLogAction.ENCASHMENT_CANCELLED,
            target_table="hr_leave_encashments", target_id=e.id, employee_id=emp.id,
            payload={"ref": e.reference_no}); db.commit()
    except Exception:
        db.rollback()


@router.get("/me/encashment", response_model=EncashmentListResponse)
def my_encashment_list(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    rows = (
        db.query(LeaveEncashment)
        .filter(
            LeaveEncashment.employee_id == emp.id,
            LeaveEncashment.is_deleted == False,  # noqa: E712
        )
        .order_by(LeaveEncashment.created_at.desc())
        .all()
    )
    return EncashmentListResponse(
        items=[_encash_to_response(db, r) for r in rows],
        total=len(rows), page=1, limit=len(rows) or 1, total_pages=1,
    )


@router.get("/encashment", response_model=EncashmentListResponse)
def admin_encashment_list(
    status_filter: Optional[EncashmentStatus] = Query(None, alias="status"),
    employee_id: Optional[UUID] = None,
    department_id: Optional[UUID] = None,
    fy: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(LeaveEncashment).filter(LeaveEncashment.is_deleted == False)  # noqa: E712
    if status_filter:
        q = q.filter(LeaveEncashment.status == status_filter)
    if employee_id:
        q = q.filter(LeaveEncashment.employee_id == employee_id)
    if fy:
        q = q.filter(LeaveEncashment.fiscal_year == fy)
    if department_id:
        q = q.join(Employee, Employee.id == LeaveEncashment.employee_id).filter(
            Employee.department_id == department_id
        )
    total = q.count()
    rows = (
        q.order_by(LeaveEncashment.created_at.desc())
         .offset((page - 1) * limit).limit(limit).all()
    )
    return EncashmentListResponse(
        items=[_encash_to_response(db, r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=max(1, ceil(total / limit) if limit else 1),
    )


@router.get("/encashment/stats", response_model=EncashmentStats)
def admin_encashment_stats(
    fy: Optional[str] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    fy = fy or _current_fy(db)
    base = db.query(LeaveEncashment).filter(LeaveEncashment.is_deleted == False)  # noqa: E712
    pending_q = base.filter(LeaveEncashment.status == EncashmentStatus.PENDING)
    return EncashmentStats(
        pending=pending_q.count(),
        approved=base.filter(LeaveEncashment.status == EncashmentStatus.APPROVED).count(),
        paid=base.filter(LeaveEncashment.status == EncashmentStatus.PAID).count(),
        rejected=base.filter(LeaveEncashment.status == EncashmentStatus.REJECTED).count(),
        pending_amount=Decimal(pending_q.with_entities(sa_func.coalesce(sa_func.sum(LeaveEncashment.amount), 0)).scalar() or 0),
        paid_this_fy=Decimal(base.filter(
            LeaveEncashment.status == EncashmentStatus.PAID,
            LeaveEncashment.fiscal_year == fy,
        ).with_entities(sa_func.coalesce(sa_func.sum(LeaveEncashment.amount), 0)).scalar() or 0),
    )


@router.post("/encashment", response_model=EncashmentResponse, status_code=http_status.HTTP_201_CREATED)
def admin_create_encashment(
    body: EncashmentAdminCreateBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Admin manual entry — lands APPROVED."""
    emp = db.query(Employee).filter(Employee.id == body.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    pol = _policy_or_404(db, body.leave_type)
    if not pol.encashment_allowed:
        raise HTTPException(422, f"{body.leave_type.value} is not encashable")
    calc = _compute_encashment(db, emp.id, body.leave_type, body.days_requested, body.basic_salary_override)
    fy = calc["fiscal_year"]
    ref = _generate_encash_ref(db, fy)
    e = LeaveEncashment(
        reference_no=ref, employee_id=emp.id, leave_type=body.leave_type, fiscal_year=fy,
        days_requested=body.days_requested,
        basic_salary_snapshot=calc["basic_salary"], formula_used=calc["formula_used"],
        amount=calc["amount"],
        status=EncashmentStatus.APPROVED,
        request_notes=body.request_notes,
        decided_by_id=admin.id, decided_at=datetime.now(timezone.utc),
        decision_notes="Admin manual entry",
    )
    db.add(e); db.commit(); db.refresh(e)
    try:
        log(db, actor_id=admin.id, action=AttendanceLogAction.ENCASHMENT_APPROVED,
            target_table="hr_leave_encashments", target_id=e.id, employee_id=emp.id,
            payload={"ref": ref, "amount": float(calc["amount"]), "manual": True})
        db.commit()
    except Exception:
        db.rollback()
    return _encash_to_response(db, e)


@router.patch("/encashment/{enc_id}/decide", response_model=EncashmentResponse)
def admin_decide_encashment(
    enc_id: UUID,
    body: EncashmentDecisionBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    e = db.query(LeaveEncashment).filter(
        LeaveEncashment.id == enc_id, LeaveEncashment.is_deleted == False,  # noqa: E712
    ).with_for_update().first()
    if not e:
        raise HTTPException(404, "Not found")
    if e.status != EncashmentStatus.PENDING:
        raise HTTPException(409, f"Cannot decide {e.status.value} request")
    if body.decision == EncashmentStatus.APPROVED:
        # Optional re-compute with the latest basic salary
        if body.basic_salary_override is not None:
            calc = _compute_encashment(db, e.employee_id, e.leave_type, Decimal(e.days_requested), body.basic_salary_override)
            e.basic_salary_snapshot = calc["basic_salary"]
            e.amount = calc["amount"]
            e.formula_used = calc["formula_used"]
        e.status = EncashmentStatus.APPROVED
        # Debit the leave balance now (encashment locks the days)
        bal = _get_or_create_balance(db, e.employee_id, e.leave_type, e.fiscal_year)
        _apply_ledger(db, bal, kind=LedgerKind.ENCASHMENT,
                      delta=Decimal(str(-float(e.days_requested))), actor=admin,
                      note=f"Encashment approved {e.reference_no}",
                      related_request_id=None)
        action = AttendanceLogAction.ENCASHMENT_APPROVED
    else:
        e.status = EncashmentStatus.REJECTED
        action = AttendanceLogAction.ENCASHMENT_REJECTED
    e.decided_by_id = admin.id
    e.decided_at = datetime.now(timezone.utc)
    e.decision_notes = body.notes
    db.commit(); db.refresh(e)
    try:
        log(db, actor_id=admin.id, action=action,
            target_table="hr_leave_encashments", target_id=e.id, employee_id=e.employee_id,
            payload={"ref": e.reference_no, "amount": float(e.amount), "notes": body.notes})
        db.add(Notification(
            user_id=db.query(Employee.user_id).filter(Employee.id == e.employee_id).scalar(),
            type="leave_approved" if body.decision == EncashmentStatus.APPROVED else "leave_rejected",
            title="Encashment update", message=f"{e.reference_no} · {e.status.value}",
            related_user_id=admin.id, is_read=False,
        ))
        db.commit()
    except Exception:
        db.rollback()
    return _encash_to_response(db, e)


@router.post("/encashment/{enc_id}/pay", response_model=EncashmentResponse)
def admin_pay_encashment(
    enc_id: UUID,
    body: EncashmentPayBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Mark an APPROVED encashment as PAID. Phase 3 will replace this manual
    step with payroll-batch integration."""
    e = db.query(LeaveEncashment).filter(LeaveEncashment.id == enc_id).first()
    if not e:
        raise HTTPException(404, "Not found")
    if e.status != EncashmentStatus.APPROVED:
        raise HTTPException(409, f"Cannot pay {e.status.value} request — must be APPROVED")
    e.status = EncashmentStatus.PAID
    e.paid_at = datetime.now(timezone.utc)
    e.paid_by_id = admin.id
    e.payroll_ref = body.payroll_ref
    db.commit(); db.refresh(e)
    try:
        log(db, actor_id=admin.id, action=AttendanceLogAction.ENCASHMENT_PAID,
            target_table="hr_leave_encashments", target_id=e.id, employee_id=e.employee_id,
            payload={"ref": e.reference_no, "payroll_ref": body.payroll_ref}); db.commit()
    except Exception:
        db.rollback()
    return _encash_to_response(db, e)


# ═════════════════════════════════════════════════════════════════════════════
# Phase 2 — Calendar ICS export
# ═════════════════════════════════════════════════════════════════════════════

def _ics_dt(d: date) -> str:
    return d.strftime("%Y%m%d")


@router.get("/calendar/export.ics")
def calendar_ics_export(
    from_: date = Query(..., alias="from"),
    to: Optional[date] = None,
    department_id: Optional[UUID] = None,
    leave_type: Optional[LeaveType] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """RFC 5545 iCalendar feed of every approved (and pending-HR) leave in the
    range. Imports cleanly into Outlook / Google / Apple Calendar."""
    to = to or (from_ + timedelta(days=60))
    q = db.query(LeaveRequest).filter(
        LeaveRequest.is_deleted == False,  # noqa: E712
        LeaveRequest.status.in_([LeaveStatus.APPROVED, LeaveStatus.PENDING_HR]),
        LeaveRequest.to_date >= from_, LeaveRequest.from_date <= to,
    )
    if department_id:
        q = q.join(Employee, Employee.id == LeaveRequest.employee_id).filter(
            Employee.department_id == department_id,
        )
    if leave_type:
        q = q.filter(LeaveRequest.leave_type == leave_type)
    rows = q.order_by(LeaveRequest.from_date).all()

    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//FourConnect HR//Leave Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:FourConnect Leave",
        "X-WR-TIMEZONE:Asia/Kolkata",
    ]
    for r in rows:
        snap = _employee_snapshot(db, r.employee_id)
        # DTEND is exclusive in iCal — add 1 day
        dtend = r.to_date + timedelta(days=1)
        summary = f"{snap.get('name','?')} · {r.leave_type.value.replace('_',' ').title()}{' (PENDING)' if r.status == LeaveStatus.PENDING_HR else ''}"
        desc = (r.reason or "").replace("\n", "\\n").replace(",", "\\,")
        lines += [
            "BEGIN:VEVENT",
            f"UID:leave-{r.id}@fourconnect",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{_ics_dt(r.from_date)}",
            f"DTEND;VALUE=DATE:{_ics_dt(dtend)}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{r.reference_no} · {desc[:200]}",
            "STATUS:" + ("CONFIRMED" if r.status == LeaveStatus.APPROVED else "TENTATIVE"),
            "TRANSP:OPAQUE",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    body = "\r\n".join(lines)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="fourconnect-leave-{from_.isoformat()}-{to.isoformat()}.ics"',
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# Phase 3 — Reports
# ═════════════════════════════════════════════════════════════════════════════

# Set of audit-log actions surfaced in the leave-module audit-logs viewer.
# Mirrors what the leave router writes, plus the older balance/comp-off/
# encashment actions.
_LEAVE_LOG_ACTIONS = (
    "LEAVE_REQUESTED", "LEAVE_MANAGER_APPROVED", "LEAVE_MANAGER_REJECTED",
    "LEAVE_HR_APPROVED", "LEAVE_HR_REJECTED",
    "LEAVE_CANCELLED", "LEAVE_WITHDRAWN", "LEAVE_ADMIN_OVERRIDE",
    "LEAVE_POLICY_CREATED", "LEAVE_POLICY_UPDATED", "LEAVE_POLICY_DELETED",
    "LEAVE_BALANCE_ACCRUED", "LEAVE_BALANCE_CARRY_FORWARD", "LEAVE_BALANCE_ADJUSTED",
    "COMP_OFF_EARNED", "COMP_OFF_GRANTED", "COMP_OFF_REVOKED", "COMP_OFF_USED", "COMP_OFF_EXPIRED",
    "ENCASHMENT_REQUESTED", "ENCASHMENT_APPROVED", "ENCASHMENT_REJECTED",
    "ENCASHMENT_PAID", "ENCASHMENT_CANCELLED",
)


def _report_or_404(report_key: str) -> dict:
    if report_key not in leave_reports.REPORT_KEYS:
        raise HTTPException(404, f"Unknown report key: {report_key}")
    return leave_reports.report_meta(report_key)


@router.get("/reports/index", response_model=LeaveReportIndexResponse)
def reports_index(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Card metadata for every available report — drives the admin Reports grid."""
    items = []
    for key in leave_reports.REPORT_KEYS:
        meta = leave_reports.report_meta(key)
        items.append(LeaveReportInfo(
            key=key,
            name=meta["name"], tagline=meta["tagline"], subtitle=meta.get("subtitle"),
            accent=meta["accent"], accent_soft=meta["accent_soft"],
            accent_deep=meta["accent_deep"],
            icon=meta["icon"], motif=meta["motif"],
        ))
    return LeaveReportIndexResponse(items=items, total=len(items))


@router.get("/reports/{report_key}/preview", response_model=LeaveReportPreview)
def report_preview(
    report_key: str,
    from_: date = Query(..., alias="from"),
    to: date = Query(...),
    department_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """JSON preview — the drawer renders this before exporting to PDF/Excel/CSV."""
    meta = _report_or_404(report_key)
    rows = leave_reports.fetch_rows(
        db, report_key, from_, to,
        department_id=department_id, employee_id=employee_id,
    )
    shaped = leave_reports.shape(report_key, rows)
    summary = leave_reports.shape_summary(report_key, shaped)
    # Normalise dates to strings for JSON
    def _norm(v):
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, date):
            return v.isoformat()
        return v
    preview_rows = [{k: _norm(v) for k, v in r.items()} for r in shaped[:limit]]
    return LeaveReportPreview(
        key=report_key, name=meta["name"],
        period={"from": from_.isoformat(), "to": to.isoformat()},
        summary=summary, rows=preview_rows, total_rows=len(shaped),
    )


@router.get("/reports/{report_key}/export")
def report_export(
    report_key: str,
    format: str = Query("csv", pattern="^(csv|excel|pdf)$"),
    from_: date = Query(..., alias="from"),
    to: date = Query(...),
    department_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """File download in the requested format."""
    meta = _report_or_404(report_key)
    rows = leave_reports.fetch_rows(
        db, report_key, from_, to,
        department_id=department_id, employee_id=employee_id,
    )
    shaped = leave_reports.shape(report_key, rows)
    summary = leave_reports.shape_summary(report_key, shaped)
    period_meta = {"period": {"from": from_, "to": to}}

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    filename = f"fourconnect-leave-{report_key}-{from_.isoformat()}-{to.isoformat()}-{stamp}"

    if format == "csv":
        content = leave_reports.render_csv(report_key, shaped, summary, period_meta)
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
        )
    if format == "excel":
        content = leave_reports.render_excel(report_key, shaped, summary, period_meta)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'},
        )
    # PDF
    try:
        content = leave_reports.render_pdf(report_key, shaped, summary, period_meta)
    except OSError as e:
        if "libgobject" in str(e) or "Cannot find" in str(e):
            raise HTTPException(503, "WeasyPrint can't find GTK DLLs — run vendor/setup_gtk.py once")
        raise
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )


# ═════════════════════════════════════════════════════════════════════════════
# Phase 3 — Audit Logs viewer
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/audit-logs", response_model=LeaveAuditListResponse)
def audit_logs(
    action: Optional[str] = None,
    employee_id: Optional[UUID] = None,
    actor_user_id: Optional[UUID] = None,
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Append-only audit trail filtered to leave-module actions."""
    q = db.query(AttendanceLog).filter(AttendanceLog.action.in_(_LEAVE_LOG_ACTIONS))
    if action:
        # Allow filtering to a single action (no validation: any leave-action value)
        q = q.filter(AttendanceLog.action == action)
    if employee_id:
        q = q.filter(AttendanceLog.employee_id == employee_id)
    if actor_user_id:
        q = q.filter(AttendanceLog.actor_user_id == actor_user_id)
    if from_:
        q = q.filter(AttendanceLog.created_at >= datetime.combine(from_, datetime.min.time(), tzinfo=timezone.utc))
    if to:
        q = q.filter(AttendanceLog.created_at <= datetime.combine(to, datetime.max.time(), tzinfo=timezone.utc))
    total = q.count()
    rows = (
        q.order_by(AttendanceLog.created_at.desc())
         .offset((page - 1) * limit).limit(limit).all()
    )
    # Bulk-resolve actor + employee names
    actor_ids = list({r.actor_user_id for r in rows if r.actor_user_id})
    emp_ids = list({r.employee_id for r in rows if r.employee_id})
    actor_names = {u.id: u.full_name for u in db.query(User.id, User.full_name).filter(User.id.in_(actor_ids)).all()} if actor_ids else {}
    emp_snaps = {}
    if emp_ids:
        for row in (
            db.query(Employee.id, Employee.employee_id, User.full_name)
              .join(User, User.id == Employee.user_id)
              .filter(Employee.id.in_(emp_ids)).all()
        ):
            emp_snaps[row.id] = (row.employee_id, row.full_name)
    out = []
    for r in rows:
        emp_code, emp_name = emp_snaps.get(r.employee_id, (None, None))
        out.append(LeaveAuditEntry(
            id=r.id,
            action=r.action.value if hasattr(r.action, "value") else str(r.action),
            actor_user_id=r.actor_user_id,
            actor_name=actor_names.get(r.actor_user_id),
            employee_id=r.employee_id,
            employee_name=emp_name, employee_code=emp_code,
            target_table=r.target_table, target_id=r.target_id,
            payload=r.payload or {},
            created_at=r.created_at,
        ))
    return LeaveAuditListResponse(
        items=out,
        total=total, page=page, limit=limit,
        total_pages=max(1, ceil(total / limit) if limit else 1),
        actions_available=list(_LEAVE_LOG_ACTIONS),
    )
