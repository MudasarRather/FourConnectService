"""Monthly late-mark accumulation penalty.

Corporate punctuality rule (configurable via SystemSetting):
    Every Nth non-condoned LATE mark in a calendar month (default N=3) costs the
    employee `late_penalty_days` days of pay (default 0.5), debited from their
    LWP balance. A late mark is a day with status LATE that the admin has NOT
    condoned (`Attendance.late_condoned = False`). Condoned lates are waived and
    don't count.

The penalty is a *pay deduction tracked against LWP* — it never flips a worked
LATE day to ABSENT. If LWP is exhausted, only the covered portion is booked and
the uncovered remainder is logged for payroll (`uncovered` in the audit payload).

Reconciliation (idempotent + reversible): each call recomputes the whole month's
`penalty_due`, compares it to the LATE_PENALTY LWP debit already booked for that
month, and writes only the delta. Condoning a late, deleting a day, or a punch
correction that clears LATE all reduce the count on the next rollup and release
the over-booked penalty automatically.

Anchored at the month's last day (`earned_on = month_end`, kind=LATE_PENALTY) so
it never collides with per-day no-show LWP debits (kind=REQUEST_APPROVED).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.utils.hr.lwp_coverage import _fy_for, _closing


@dataclass
class LatePenaltyOutcome:
    late_count: int
    penalty_due: float
    penalty_booked: float
    uncovered: float


def _month_bounds(d: date) -> tuple[date, date]:
    start = date(d.year, d.month, 1)
    if d.month == 12:
        end = date(d.year, 12, 31)
    else:
        end = date(d.year, d.month + 1, 1) - timedelta(days=1)
    return start, end


def _setting(db: Session, key: str, default: str) -> str:
    from app.models.system_setting import SystemSetting
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return row.value if row and row.value not in (None, "") else default


def reconcile_late_penalty(
    db: Session, employee_id: UUID, on_date: date, actor_id: Optional[UUID],
) -> LatePenaltyOutcome:
    """Recompute & reconcile the LWP late-mark penalty for on_date's month."""
    from app.models.hr.attendance import Attendance, AttendanceStatus
    from app.models.hr.leave_type import LeaveType, LedgerKind
    from app.models.hr.leave_policy import LeavePolicy
    from app.models.hr.leave_balance import LeaveBalance
    from app.models.hr.leave_balance_history import LeaveBalanceHistory
    from app.models.hr.attendance_log import AttendanceLogAction
    from app.utils.hr.attendance_logic import log

    today = date.today()
    m_start, m_end = _month_bounds(on_date)
    elapsed_end = min(m_end, today - timedelta(days=1))  # count only fully-elapsed days

    threshold = max(1, int(float(_setting(db, "late_marks_per_penalty", "3"))))
    per_block = Decimal(_setting(db, "late_penalty_days", "0.5"))

    # ── Count non-condoned LATE marks this month (elapsed only) ──────────
    late_count = (
        db.query(Attendance)
        .filter(
            Attendance.employee_id == employee_id,
            Attendance.date >= m_start,
            Attendance.date <= elapsed_end,
            Attendance.status == AttendanceStatus.LATE,
            Attendance.late_condoned == False,   # noqa: E712
            Attendance.is_deleted == False,       # noqa: E712
        )
        .count()
    )
    penalty_due = (Decimal(late_count) // threshold) * per_block  # floor blocks × per

    # ── What's already booked for this month? ────────────────────────────
    fy = _fy_for(on_date, _setting(db, "fiscal_year_start", "04-01"))
    prior = (
        db.query(LeaveBalanceHistory)
        .filter(
            LeaveBalanceHistory.employee_id == employee_id,
            LeaveBalanceHistory.leave_type == LeaveType.LWP,
            LeaveBalanceHistory.kind == LedgerKind.LATE_PENALTY,
            LeaveBalanceHistory.earned_on >= m_start,
            LeaveBalanceHistory.earned_on <= m_end,
        )
        .all()
    )
    current = sum((Decimal(-(r.delta or 0)) for r in prior), Decimal("0"))

    if penalty_due == 0 and current == 0:
        return LatePenaltyOutcome(int(late_count), 0.0, 0.0, 0.0)

    # ── LWP balance (+ lazy entitlement seed, shared marker with no-show) ─
    bal = (
        db.query(LeaveBalance)
        .filter(
            LeaveBalance.employee_id == employee_id,
            LeaveBalance.leave_type == LeaveType.LWP,
            LeaveBalance.fiscal_year == fy,
            LeaveBalance.is_deleted == False,  # noqa: E712
        )
        .first()
    )
    if bal is None:
        bal = LeaveBalance(
            employee_id=employee_id, leave_type=LeaveType.LWP, fiscal_year=fy,
            opening_balance=0, accrued=0, carry_forward_in=0,
            used=0, encashed=0, adjustments=0, closing_balance=0,
        )
        db.add(bal); db.flush()

    if penalty_due > 0:
        policy = (
            db.query(LeavePolicy)
            .filter(LeavePolicy.leave_type == LeaveType.LWP, LeavePolicy.is_deleted == False)  # noqa: E712
            .first()
        )
        quota = Decimal(policy.annual_quota or 0) if policy else Decimal("0")
        if quota > 0:
            seeded = (
                db.query(LeaveBalanceHistory.id)
                .filter(
                    LeaveBalanceHistory.employee_id == employee_id,
                    LeaveBalanceHistory.leave_type == LeaveType.LWP,
                    LeaveBalanceHistory.fiscal_year == fy,
                    LeaveBalanceHistory.kind == LedgerKind.OPENING_SEED,
                    LeaveBalanceHistory.is_auto_generated == True,  # noqa: E712
                )
                .first()
            )
            if seeded is None:
                before = _closing(bal)
                bal.opening_balance = Decimal(bal.opening_balance or 0) + quota
                bal.closing_balance = _closing(bal)
                db.add(LeaveBalanceHistory(
                    employee_id=employee_id, leave_type=LeaveType.LWP, fiscal_year=fy,
                    kind=LedgerKind.OPENING_SEED, delta=quota,
                    balance_before=before, balance_after=bal.closing_balance, actor_user_id=actor_id,
                    note=f"LWP entitlement seeded ({quota} day(s)) for {fy}",
                    is_auto_generated=True,
                ))
                db.flush()

    # Available once this month's prior penalty is set aside; never overdraw LWP.
    avail_excl = _closing(bal) + current
    target = min(penalty_due, avail_excl) if avail_excl > 0 else Decimal("0")
    if target < 0:
        target = Decimal("0")
    uncovered = penalty_due - target

    diff = target - current
    if diff != 0:
        before = _closing(bal)
        bal.used = Decimal(bal.used or 0) + diff
        bal.closing_balance = _closing(bal)
        db.add(LeaveBalanceHistory(
            employee_id=employee_id, leave_type=LeaveType.LWP, fiscal_year=fy,
            kind=LedgerKind.LATE_PENALTY, delta=(-diff),
            balance_before=before, balance_after=bal.closing_balance, actor_user_id=actor_id,
            note=(
                f"Late-mark penalty — {late_count} late(s) in {on_date.strftime('%b %Y')} "
                f"(every {threshold} = {per_block}d)"
                if diff > 0
                else f"Late-mark penalty released — recomputed {on_date.strftime('%b %Y')}"
            ),
            is_auto_generated=True, earned_on=m_end,
        ))
        try:
            log(
                db, actor_id=actor_id,
                action=AttendanceLogAction.LEAVE_BALANCE_ADJUSTED,
                target_table="hr_leave_balances", target_id=bal.id, employee_id=employee_id,
                payload={
                    "month": on_date.strftime("%Y-%m"), "late_count": int(late_count),
                    "penalty_due": float(penalty_due), "penalty_delta": float(-diff),
                    "uncovered": float(uncovered), "reason": "late_mark_accumulation",
                },
            )
        except Exception:
            pass
        db.flush()

    return LatePenaltyOutcome(int(late_count), float(penalty_due), float(target), float(uncovered))
