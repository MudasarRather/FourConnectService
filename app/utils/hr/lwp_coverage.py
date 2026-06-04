"""LWP (Leave-Without-Pay) auto-coverage for attendance no-shows.

Corporate rule (implemented here, called once from `daily_rollup`):

A working day the employee did not actually attend — no clock-in — is unpaid.
`daily_rollup` flags the unpaid portion as `lop_days`:
    0.5  -> approved half-day where the working half was never worked
    1.0  -> full no-show, incl. an approved WFH/Remote day with zero punches

We then try to cover that unpaid portion from the employee's LWP entitlement:
  * LWP balance can absorb the debit -> debit LWP; the day is *authorised*
    unpaid leave. A full day becomes status ``LWP``; an approved half-day keeps
    status ``HALF_DAY`` (its working half is the part debited to LWP).
  * LWP balance cannot cover it -> the day is an *unauthorised* ABSENCE
    (status ``ABSENT``, ``lop_days`` 1.0) and nothing is debited.

The LWP entitlement is the admin-configured ``LeavePolicy(LWP).annual_quota``.
It is lazily seeded into the employee's ``opening_balance`` the first time it is
needed in a fiscal year, so the dashboard's ``available = closing_balance``
formula reflects the quota. A quota of 0 means *no* coverage is possible and
every no-show falls through to ABSENT.

Idempotent + reversible: each call reconciles the *net* auto-LWP debit already
booked for ``on_date`` (marked ``is_auto_generated=True``, ``earned_on=on_date``)
against the freshly-computed target and writes only the delta. Re-running rollup
after the employee back-fills a punch releases the debit cleanly.

This module is import-light at module load and pulls leave models inside the
function to avoid any circular-import risk with the leaves router.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session


@dataclass
class LwpOutcome:
    status: object        # AttendanceStatus — may differ from the input status
    lop_days: float       # may be promoted to 1.0 when an uncovered no-show
    lwp_debited: float    # net LWP days now booked against this date
    covered: bool         # True when LWP absorbed the unpaid portion


def _fy_for(on_date: date, fy_start: str) -> str:
    """Resolve the "YYYY-YY" fiscal year for a date given an "MM-DD" FY start."""
    try:
        mm, dd = (int(x) for x in fy_start.split("-"))
    except Exception:
        mm, dd = 4, 1
    boundary = date(on_date.year, mm, dd)
    sy = on_date.year if on_date >= boundary else on_date.year - 1
    return f"{sy}-{str(sy + 1)[-2:]}"


def _closing(b) -> Decimal:
    return (
        Decimal(b.opening_balance or 0)
        + Decimal(b.accrued or 0)
        + Decimal(b.carry_forward_in or 0)
        + Decimal(b.adjustments or 0)
        - Decimal(b.used or 0)
        - Decimal(b.encashed or 0)
    )


def apply_lwp_coverage(
    db: Session,
    employee_id: UUID,
    on_date: date,
    *,
    status,
    lop_days: float,
    is_no_show: bool,
    actor_id: Optional[UUID],
) -> LwpOutcome:
    """Reconcile LWP coverage for a single (employee, date). See module docstring.

    ``is_no_show`` must be True only when the employee did NOT clock in
    (``check_in_time is None``). LWP is consumed **only** for genuine no-shows —
    a day the employee actually worked (even a late/short half-day) keeps its
    payroll ``lop_days`` classification but never debits LWP and is never
    downgraded to ABSENT.

    Callable for any elapsed working day: when there's nothing to debit AND no
    prior auto-debit to release, it returns immediately without creating a
    balance row.
    """
    from app.models.hr.attendance import AttendanceStatus
    from app.models.hr.leave_type import LeaveType, LedgerKind
    from app.models.hr.leave_policy import LeavePolicy
    from app.models.hr.leave_balance import LeaveBalance
    from app.models.hr.leave_balance_history import LeaveBalanceHistory
    from app.models.system_setting import SystemSetting
    from app.models.hr.attendance_log import AttendanceLogAction
    from app.utils.hr.attendance_logic import log  # module-level audit helper

    # LWP is consumed only by genuine no-shows. A worked day's lop is payroll-
    # only (kept on the row) and must NOT touch LWP.
    need = Decimal(str(lop_days)) if (is_no_show and lop_days > 0) else Decimal("0")

    # ── Cheap reconciliation probe: what has THIS date already auto-debited? ──
    prior = (
        db.query(LeaveBalanceHistory)
        .filter(
            LeaveBalanceHistory.employee_id == employee_id,
            LeaveBalanceHistory.leave_type == LeaveType.LWP,
            LeaveBalanceHistory.is_auto_generated == True,  # noqa: E712
            LeaveBalanceHistory.earned_on == on_date,
            LeaveBalanceHistory.kind == LedgerKind.REQUEST_APPROVED,
        )
        .all()
    )
    current = sum((Decimal(-(r.delta or 0)) for r in prior), Decimal("0"))

    # Nothing to book and nothing to release → no-op (don't create a balance row).
    if need == 0 and current == 0:
        return LwpOutcome(status=status, lop_days=float(lop_days), lwp_debited=0.0, covered=True)

    # ── Fiscal year + entitlement ────────────────────────────────────────
    fy_setting = (
        db.query(SystemSetting).filter(SystemSetting.key == "fiscal_year_start").first()
    )
    fy = _fy_for(on_date, fy_setting.value if fy_setting else "04-01")
    policy = (
        db.query(LeavePolicy)
        .filter(
            LeavePolicy.leave_type == LeaveType.LWP,
            LeavePolicy.is_deleted == False,  # noqa: E712
        )
        .first()
    )
    quota = Decimal(policy.annual_quota or 0) if policy else Decimal("0")

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
        db.add(bal)
        db.flush()

    # ── Lazy one-time seed of the entitlement (only when we're about to debit) ──
    if need > 0 and quota > 0:
        seed_marker = (
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
        if seed_marker is None:
            before = _closing(bal)
            bal.opening_balance = Decimal(bal.opening_balance or 0) + quota
            after = _closing(bal)
            bal.closing_balance = after
            db.add(LeaveBalanceHistory(
                employee_id=employee_id, leave_type=LeaveType.LWP, fiscal_year=fy,
                kind=LedgerKind.OPENING_SEED, delta=quota,
                balance_before=before, balance_after=after, actor_user_id=actor_id,
                note=f"LWP entitlement seeded ({quota} day(s)) for {fy}",
                is_auto_generated=True,
            ))
            db.flush()

    # Balance available once THIS date's prior booking is set aside.
    avail_excl = _closing(bal) + current
    covered = need > 0 and quota > 0 and avail_excl >= need
    target = need if covered else Decimal("0")

    # ── Write only the delta (idempotent + reversible) ───────────────────
    diff = target - current  # +ve ⇒ debit more; -ve ⇒ release a prior debit
    if diff != 0:
        before = _closing(bal)
        bal.used = Decimal(bal.used or 0) + diff  # a debit increases `used`
        after = _closing(bal)
        bal.closing_balance = after
        db.add(LeaveBalanceHistory(
            employee_id=employee_id, leave_type=LeaveType.LWP, fiscal_year=fy,
            kind=LedgerKind.REQUEST_APPROVED, delta=(-diff),
            balance_before=before, balance_after=after, actor_user_id=actor_id,
            note=(
                f"Auto LWP debit — no clock-in on {on_date.isoformat()}"
                if diff > 0
                else f"Auto LWP released — attendance recomputed for {on_date.isoformat()}"
            ),
            is_auto_generated=True, earned_on=on_date,
        ))
        try:
            log(
                db, actor_id=actor_id,
                action=AttendanceLogAction.LEAVE_BALANCE_ADJUSTED,
                target_table="hr_leave_balances", target_id=bal.id,
                employee_id=employee_id,
                payload={
                    "date": on_date.isoformat(),
                    "lwp_delta": float(-diff),
                    "lwp_total_for_day": float(target),
                    "reason": "auto_lwp_no_clockin",
                },
            )
        except Exception:
            pass
        db.flush()

    # ── Resolve the final attendance status (only no-shows change) ───────
    new_status = status
    new_lop = float(lop_days)
    if need > 0:  # genuine no-show
        if covered:
            if need >= Decimal("1.0"):
                new_status = AttendanceStatus.LWP   # full unpaid day, LWP-covered
            # half-day no-show stays HALF_DAY — its working half is the LWP debit
        else:
            new_status = AttendanceStatus.ABSENT    # LWP exhausted ⇒ unauthorised
            new_lop = 1.0

    return LwpOutcome(
        status=new_status, lop_days=new_lop,
        lwp_debited=float(target), covered=covered,
    )
