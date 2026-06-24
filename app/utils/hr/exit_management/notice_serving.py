"""HR Exit Management — Notice-period serving telemetry.

The notice page must be anchored on **when the notice actually started**
(``notice_period_start_date``), not just a naive ``last_working_date - today``
countdown. This module turns the raw case dates into a corporate-correct notice
picture and, on demand, reconciles it against the employee's real attendance,
leave balance and the projected Full-&-Final impact — so HR can see, in one
place, whether the leaver is actually serving their notice, what paid-leave
cover they have, and how it all lands on the settlement.

Two layers:
  * ``notice_metrics(case)`` — cheap, date-only. Safe to call per row on the board.
  * ``notice_serving_snapshot(db, case)`` — the full read (attendance + leave +
    F&F projection). One DB-heavy call, used by the per-case drawer/panel.

Read-only: never mutates. Reuses the settlement engine so the projection matches
the real F&F to the rupee.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.hr.employee import Employee
from app.models.hr.exit_case import ExitCase
from app.models.hr.exit_policy import ExitPolicy
from app.utils.hr.exit_management.service import resolved_notice_days

# India HRMS — business clock is IST. The standard working window below is the
# fallback when a precise shift can't be resolved.
_IST = timezone(timedelta(hours=5, minutes=30))
_WORK_START = time(9, 0)
_WORK_END = time(18, 0)


def _today_shift_ended(db: Session, emp: Optional[Employee], today: date) -> bool:
    """True once `emp`'s working day for `today` is over — so today counts as a
    COMPLETED notice day even with no clock-in. Uses the resolved shift's end time,
    falling back to the standard 18:00 IST close when no shift resolves. Conservative
    on any error → False (treat today as still in progress) so the served / not-
    recorded counts never inflate by a day the employee may yet serve. Mirrors the
    attendance finalizer's shift-end gate."""
    if not emp:
        return False
    try:
        from app.utils.hr.attendance_logic import resolve_shift
        shift = resolve_shift(db, emp.id, today)
        end_t = shift.end_time if (shift and shift.end_time) else _WORK_END
        start_t = shift.start_time if (shift and shift.start_time) else _WORK_START
        end_dt = datetime.combine(today, end_t, tzinfo=_IST)
        if end_t <= start_t:                     # overnight shift ends next day
            end_dt = end_dt + timedelta(days=1)
        return datetime.now(_IST) >= end_dt
    except Exception:
        return False


def notice_serving_window_end(db: Session, emp: Optional[Employee],
                              lwd: Optional[date], today: date) -> date:
    """Last notice day to COUNT in the serving readout: yesterday while today is
    still in progress, extended to include today once its shift has ended — capped
    at the last working day. Shared by the admin snapshot and the self-service view
    so both always agree on the served / not-recorded counts."""
    base_end = today if _today_shift_ended(db, emp, today) else (today - timedelta(days=1))
    return min(lwd, base_end) if lwd else base_end


def notice_start_moment(db: Session, case: ExitCase) -> Dict[str, Any]:
    """The actual MOMENT notice began (date + time), not just the date.

    Prefers the ``NOTICE_STARTED`` audit event (the click that put the employee
    ON_NOTICE), falling back to acceptance / case creation. Reports whether that
    moment fell inside standard working hours (09:00–18:00 IST, Mon–Sat) — so HR
    can see if a separation was filed/started after hours or on a day off.
    """
    out: Dict[str, Any] = {
        "started_at": None, "time_label": None, "weekday": None,
        "in_working_hours": None, "window": "09:00–18:00 IST", "source": None,
    }
    dt = None
    source = None
    try:
        from app.models.hr.exit_audit_log import ExitAuditLog
        from app.models.hr.exit_type import ExitAuditAction
        row = (
            db.query(ExitAuditLog)
            .filter(ExitAuditLog.exit_case_id == case.id,
                    ExitAuditLog.action == ExitAuditAction.NOTICE_STARTED)
            .order_by(ExitAuditLog.created_at.desc())
            .first()
        )
        if row and row.created_at:
            dt, source = row.created_at, "notice_started_event"
    except Exception:
        pass
    if dt is None and getattr(case, "accepted_at", None):
        dt, source = case.accepted_at, "accepted_at"
    if dt is None and getattr(case, "created_at", None):
        dt, source = case.created_at, "case_created"
    if dt is None:
        return out

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(_IST)
    in_hours = local.weekday() < 6 and (_WORK_START <= local.time() <= _WORK_END)
    out.update({
        "started_at": local.isoformat(),
        "time_label": local.strftime("%H:%M"),
        "weekday": local.strftime("%A"),
        "in_working_hours": bool(in_hours),
        "source": source,
    })
    return out


def _i(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def is_notice_served(case: ExitCase) -> Dict[str, Any]:
    """Has the leaver actually served their notice — the gate for disbursing F&F?

    Non-raising. Returns ``{served, reason, remaining_days, last_working_date}``.
    The Full & Final must NOT be paid out while the employee is still working
    their notice; it settles only once the commitment window is fulfilled:

    * already SEPARATED (EXITED / ARCHIVED / INACTIVE) → relieved, notice done;
    * notice formally **waived / bought out** → no serving required (deliberate
      early relieve — the buyout / short-notice recovery is in the F&F instead);
    * otherwise the **last working day must have arrived** (today ≥ LWD). While
      days remain, the notice is still being served → block.

    A case with no last working day set is blocked (you can't settle an open-ended
    notice). Mirrored client-side in ``useExit.noticeServed`` for the UI gate; this
    is the authoritative server-side check used by the pay handler.
    """
    from app.models.hr.employee import LifecycleState
    SEPARATED = (LifecycleState.EXITED, LifecycleState.ARCHIVED, LifecycleState.INACTIVE)

    emp = getattr(case, "employee", None)
    lwd = case.last_working_date
    out = {
        "served": False, "reason": None,
        "remaining_days": ((lwd - date.today()).days if lwd else None),
        "last_working_date": lwd.isoformat() if lwd else None,
    }

    if emp is not None and emp.lifecycle_state in SEPARATED:
        out["served"] = True
        return out
    if getattr(case, "notice_waived", False):
        out["served"] = True
        return out
    if not lwd:
        out["reason"] = "Set the last working day before the Full & Final can be disbursed."
        return out
    today = date.today()
    if today >= lwd:
        out["served"] = True
        return out
    remaining = (lwd - today).days
    out["reason"] = (
        f"Notice is still being served — {remaining} day(s) remain to the last working day "
        f"({lwd.strftime('%d %b %Y')}). The F&F can be disbursed once notice is served."
    )
    return out


def notice_metrics(case: ExitCase, policy: Optional[ExitPolicy] = None) -> Dict[str, Any]:
    """Date-anchored notice progress. Everything is measured from the REAL
    ``notice_period_start_date`` → ``last_working_date`` window, with the
    policy-required notice as the benchmark the served window is judged against."""
    today = date.today()
    start = case.notice_period_start_date
    lwd = case.last_working_date
    required = resolved_notice_days(case, policy if policy is not None else case.policy)

    # The window the employee is actually committed to (start → LWD).
    window = None
    if start and lwd:
        window = max((lwd - start).days, 0)

    served = None
    if start:
        # Days elapsed since notice began, capped at the window and never negative.
        elapsed = (today - start).days
        served = min(max(elapsed, 0), window) if window is not None else max(elapsed, 0)

    remaining = (lwd - today).days if lwd else None
    not_started = bool(start and today < start)
    overdue = bool(remaining is not None and remaining < 0)

    progress_pct = 0
    if window and window > 0 and served is not None:
        progress_pct = int(round(min(served / window, 1) * 100))
    elif overdue:
        progress_pct = 100

    # Short notice = the committed window is less than the policy requires → the
    # shortfall is recoverable in the F&F (this is the notice-pay recovery basis).
    shortfall_days = 0
    if window is not None and required and not case.notice_waived:
        shortfall_days = max(required - window, 0)
    if case.notice_waived:
        shortfall_days = _i(case.notice_buyout_days)

    return {
        "notice_period_start_date": start.isoformat() if start else None,
        "last_working_date": lwd.isoformat() if lwd else None,
        "today": today.isoformat(),
        "required_days": required,
        "notice_total_days": window,          # the served window (start → LWD)
        "served_days": served,
        "remaining_days": remaining,
        "progress_pct": progress_pct,
        "not_started": not_started,
        "overdue": overdue,
        "notice_waived": bool(case.notice_waived),
        "short_notice": shortfall_days > 0 and not case.notice_waived,
        "shortfall_days": shortfall_days,
    }


# Attendance buckets for the serving readout. Mirrors the settlement engine's
# unpaid set so the "lost pay" story is consistent.
_PRESENT_LIKE = ("PRESENT", "LATE", "WFH", "REMOTE", "ON_DUTY", "HALF_DAY")
_LEAVE_LIKE = ("LEAVE",)
_REST_LIKE = ("HOLIDAY", "WEEK_OFF")
_UNPAID = ("ABSENT", "LWP")


def _attendance_during_notice(db: Session, emp: Employee, start: date, end: date) -> Dict[str, Any]:
    """Bucket the leaver's attendance across the served window [start..end]."""
    out = {
        "present_days": 0.0, "leave_days": 0.0, "absent_days": 0, "lwp_days": 0,
        "rest_days": 0, "lop_days": 0.0, "rows": 0, "no_record_days": 0,
        "window_from": start.isoformat(), "window_to": end.isoformat(),
    }
    try:
        from app.models.hr.attendance import Attendance, AttendanceStatus
        rows = (
            db.query(Attendance)
            .filter(
                Attendance.employee_id == emp.id,
                Attendance.is_deleted == False,  # noqa: E712
                Attendance.date >= start,
                Attendance.date <= end,
            )
            .all()
        )
        present_set = {AttendanceStatus[s] for s in _PRESENT_LIKE if s in AttendanceStatus.__members__}
        leave_set = {AttendanceStatus[s] for s in _LEAVE_LIKE if s in AttendanceStatus.__members__}
        rest_set = {AttendanceStatus[s] for s in _REST_LIKE if s in AttendanceStatus.__members__}
        present = Decimal("0"); leave = Decimal("0"); lop = Decimal("0")
        absent = 0; lwp = 0; rest = 0
        for r in rows:
            lop += Decimal(str(r.lop_days or 0))
            st = r.status
            if st in present_set:
                present += (Decimal("1") - Decimal(str(r.lop_days or 0)))
            elif st in leave_set:
                leave += Decimal("1")
            elif st in rest_set:
                rest += 1
            elif st.value == "ABSENT":
                absent += 1
            elif st.value == "LWP":
                lwp += 1
        out.update({
            "present_days": float(present), "leave_days": float(leave),
            "absent_days": absent, "lwp_days": lwp, "rest_days": rest,
            "lop_days": float(lop), "rows": len(rows),
        })
        # Elapsed calendar days with no attendance row at all (potential unrecorded gaps).
        elapsed_cal = (end - start).days + 1
        out["no_record_days"] = max(elapsed_cal - len(rows), 0)
        return out
    except Exception:
        return out


def _leave_available(db: Session, emp: Employee) -> Dict[str, Any]:
    """Current closing balance of paid leave the employee could still draw on
    (latest fiscal year per type). Answers 'do they have leave to cover absence?'"""
    out = {"by_type": {}, "total_paid": 0.0, "encashable_earned": 0.0}
    try:
        from app.models.hr.leave_balance import LeaveBalance
        from app.models.hr.leave_type import LeaveType
        rows = (
            db.query(LeaveBalance)
            .filter(LeaveBalance.employee_id == emp.id, LeaveBalance.is_deleted == False)  # noqa: E712
            .all()
        )
        paid_types = {t for t in ("EARNED", "CASUAL", "SICK") if t in LeaveType.__members__}
        latest: Dict[Any, Any] = {}
        for r in rows:
            key = r.leave_type
            cur = latest.get(key)
            if cur is None or (r.fiscal_year or "") > (cur.fiscal_year or ""):
                latest[key] = r
        total = Decimal("0")
        for lt, r in latest.items():
            bal = max(Decimal(str(r.closing_balance or 0)), Decimal("0"))
            name = lt.value if hasattr(lt, "value") else str(lt)
            if name in paid_types:
                out["by_type"][name] = float(bal)
                total += bal
            if name == "EARNED":
                out["encashable_earned"] = float(bal)
        out["total_paid"] = float(total)
        return out
    except Exception:
        return out


def notice_serving_snapshot(db: Session, case: ExitCase) -> Dict[str, Any]:
    """Full serving picture: notice progress + attendance during notice + leave
    cover + the projected F&F impact, reconciled against the real settlement engine."""
    emp = case.employee or db.query(Employee).filter(Employee.id == case.employee_id).first()
    policy = case.policy
    metrics = notice_metrics(case, policy)

    today = date.today()
    start = case.notice_period_start_date
    lwd = case.last_working_date
    attendance = None
    if emp and start:
        # Today only counts once its shift has ended (else it's still in progress).
        end = notice_serving_window_end(db, emp, lwd, today)
        if end >= start:
            attendance = _attendance_during_notice(db, emp, start, end)

    leave = _leave_available(db, emp) if emp else {"by_type": {}, "total_paid": 0.0, "encashable_earned": 0.0}

    # ─── F&F projection (reuse the settlement engine so figures match exactly) ───
    projection: Dict[str, Any] = {}
    try:
        from app.utils.hr.exit_management import settlement_engine as se
        comp = se._active_comp(db, case.employee_id) if emp else None
        basic = se._monthly_basic(comp, emp) if emp else Decimal("0")
        notice_rec = se._notice_recovery(case, policy, comp, emp) if emp else {"amount": Decimal("0")}
        encash = se._leave_encashment(db, emp, basic) if emp else {"amount": Decimal("0"), "days": Decimal("0")}
        # Lost pay from unpaid days served so far (already-elapsed LOP weight × per-day gross).
        per_day_gross = Decimal("0")
        if emp:
            import calendar as _cal
            gross = se._monthly_gross(comp, emp)
            ref = lwd or today
            mdays = _cal.monthrange(ref.year, ref.month)[1]
            per_day_gross = (gross / Decimal(mdays)) if mdays else Decimal("0")
        lop_days = Decimal(str(attendance["lop_days"])) if attendance else Decimal("0")
        projection = {
            "notice_recovery": float(notice_rec.get("amount") or 0),
            "notice_recovery_basis": notice_rec.get("basis"),
            "leave_encashment": float(encash.get("amount") or 0),
            "leave_encashment_days": float(encash.get("days") or 0),
            "lop_days_so_far": float(lop_days),
            "lop_amount_so_far": float((per_day_gross * lop_days).quantize(Decimal("0.01"))) if per_day_gross else 0.0,
            "per_day_gross": float(per_day_gross.quantize(Decimal("0.01"))) if per_day_gross else 0.0,
        }
    except Exception:
        projection = {}

    return {
        "case_id": str(case.id),
        "employee_name": (emp.user.full_name if emp and emp.user and getattr(emp.user, "full_name", None) else
                          (emp.employee_code if emp else None)),
        "metrics": metrics,
        "start": notice_start_moment(db, case),
        "attendance": attendance,
        "leave": leave,
        "projection": projection,
    }
