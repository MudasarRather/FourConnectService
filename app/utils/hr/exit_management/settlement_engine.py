"""HR Exit Management — Full & Final settlement compute engine.

Pure compute: reads compensation / leave balance / clearance recoveries / policy
and writes the earning + recovery lines onto the ExitSettlement row plus a fully
reproducible ``computation_snapshot``. Does NOT post to payroll (that happens in
the ``pay`` endpoint via ``payroll_post.py``).

Every external read is guarded so missing/partial data yields 0 for that line
rather than crashing the close-out. ``overrides`` lets HR pin any line.
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.hr.employee import Employee
from app.models.hr.employee_compensation import EmployeeCompensation
from app.models.hr.exit_case import ExitCase
from app.models.hr.exit_settlement import ExitSettlement
from app.models.hr.exit_clearance import ExitClearanceItem
from app.models.hr.exit_policy import ExitPolicy
from app.models.hr.exit_type import ResignationType
from app.utils.hr.exit_management.service import resolved_notice_days

Q2 = Decimal("0.01")
# Leave types encashed at exit (privilege/earned leave is standard practice).
ENCASHABLE_LEAVE_TYPES = ("EARNED",)


def _d(v: Any) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception:
        return Decimal("0")


def _round(v: Decimal) -> Decimal:
    return _d(v).quantize(Q2, rounding=ROUND_HALF_UP)


def _active_comp(db: Session, employee_id) -> Optional[EmployeeCompensation]:
    return (
        db.query(EmployeeCompensation)
        .filter(
            EmployeeCompensation.employee_id == employee_id,
            EmployeeCompensation.is_deleted == False,  # noqa: E712
            EmployeeCompensation.is_active == True,  # noqa: E712
        )
        .order_by(EmployeeCompensation.effective_from.desc())
        .first()
    )


def _monthly_basic(comp: Optional[EmployeeCompensation], emp: Employee) -> Decimal:
    if comp and comp.basic_amount:
        return _d(comp.basic_amount)
    if comp and comp.monthly_gross:
        return _d(comp.monthly_gross) * Decimal("0.5")   # fallback heuristic
    if emp.monthly_ctc:
        return _d(emp.monthly_ctc) * Decimal("0.5")
    return Decimal("0")


def _monthly_gross(comp: Optional[EmployeeCompensation], emp: Employee) -> Decimal:
    if comp and comp.monthly_gross:
        return _d(comp.monthly_gross)
    if comp and comp.monthly_ctc:
        return _d(comp.monthly_ctc)
    if emp.monthly_ctc:
        return _d(emp.monthly_ctc)
    return Decimal("0")


def _monthly_ctc(comp: Optional[EmployeeCompensation], emp: Employee) -> Decimal:
    if comp and comp.monthly_ctc:
        return _d(comp.monthly_ctc)
    if emp.monthly_ctc:
        return _d(emp.monthly_ctc)
    return _monthly_gross(comp, emp)


def _settlement_base(comp, emp, basis: str, basic: Decimal) -> Decimal:
    """Per-month pool a BASIC|GROSS|CTC basis resolves to (default BASIC)."""
    b = str(basis or "BASIC").upper()
    if b == "GROSS":
        return _monthly_gross(comp, emp)
    if b == "CTC":
        return _monthly_ctc(comp, emp)
    return basic


# Attendance statuses that never carry paid weight in a final settlement — an
# unauthorised absence or a leave-without-pay day earns nothing.
_UNPAID_ATT_STATUSES = ("ABSENT", "LWP")


def _exit_month_released(db: Session, employee_id, year: int, month: int) -> bool:
    """True if the exit month's salary was already disbursed via a RELEASED payslip
    (so the F&F must not pay it a second time)."""
    try:
        from app.models.hr.payslip import Payslip, PayslipStatus
        return (
            db.query(Payslip)
            .filter(
                Payslip.employee_id == employee_id,
                Payslip.period_month == month,
                Payslip.period_year == year,
                Payslip.status == PayslipStatus.RELEASED,
            )
            .first()
            is not None
        )
    except Exception:
        return False


def _attendance_paid_days(db: Session, emp: Employee, lwd: date) -> Dict[str, Any]:
    """Paid-day weight from ACTUAL attendance in the exit month.

    Sums ``1 - lop_days`` over attendance rows in the window
    ``[max(month-start, joining) .. min(LWD, today)]``. ABSENT / LWP days and
    days with no attendance row contribute 0, half/short days contribute their
    paid fraction, and un-elapsed future days are never counted. This is the
    attendance-gated basis: no proof of a worked/paid day ⇒ no pending salary
    for it (the F&F is the reconciliation point, stricter than monthly payroll's
    lenient "no row = paid" default).
    """
    out = {"paid_days": Decimal("0"), "rows": 0, "window_start": None, "window_end": None}
    try:
        from app.models.hr.attendance import Attendance, AttendanceStatus
        month_start = date(lwd.year, lwd.month, 1)
        win_start = month_start
        if emp.joining_date and emp.joining_date > win_start:
            win_start = emp.joining_date
        win_end = min(lwd, date.today())
        out["window_start"] = win_start.isoformat()
        out["window_end"] = win_end.isoformat()
        if win_end < win_start:
            return out
        rows = (
            db.query(Attendance)
            .filter(
                Attendance.employee_id == emp.id,
                Attendance.is_deleted == False,  # noqa: E712
                Attendance.date >= win_start,
                Attendance.date <= win_end,
            )
            .all()
        )
        unpaid = {AttendanceStatus[s] for s in _UNPAID_ATT_STATUSES if s in AttendanceStatus.__members__}
        total = Decimal("0")
        for r in rows:
            if r.status in unpaid:
                continue
            w = Decimal("1") - _d(r.lop_days)
            if w > 0:
                total += w
        out["paid_days"] = total
        out["rows"] = len(rows)
        return out
    except Exception:
        return out


def _pending_salary(db: Session, emp: Employee, comp: Optional[EmployeeCompensation], lwd: Optional[date]) -> Dict[str, Any]:
    """Pro-rata gross for the exit month, GATED ON ACTUAL ATTENDANCE up to the LWD.

    Per-day rate mirrors payroll (``monthly_gross / days_in_month``); the payable
    day count comes from attendance — present / approved-leave / holiday / week-off
    / WFH-with-punch, net of LOP — that has already elapsed and is on/before the
    LWD, **not** the raw calendar day-of-month (the previous behaviour, which paid
    a full pro-rata regardless of whether the employee ever showed up). Days with
    no attendance, ABSENT/LWP days and future days earn nothing. If the exit month
    was already disbursed via a RELEASED payslip, nothing is pending.
    """
    if not lwd:
        return {"amount": Decimal("0"), "paid_days": 0, "days": 0, "month_days": 0, "basis": "none"}
    gross = _monthly_gross(comp, emp)
    # Settle the month CURRENTLY being earned, clamped to the LWD — NOT the LWD
    # month. An employee mid-notice whose last day is in a future month was
    # showing ₹0 against that future month ("0/31 paid days") even though they're
    # actively earning this month's salary that regular payroll hasn't released
    # yet. Anchoring on min(LWD, today) surfaces the real earned-but-unpaid salary
    # now; at/after the LWD this resolves to exactly the LWD month (unchanged).
    settle_ref = min(lwd, date.today())
    month_days = calendar.monthrange(settle_ref.year, settle_ref.month)[1]
    per_day = (gross / Decimal(month_days)) if month_days else Decimal("0")
    settle_month = settle_ref.strftime("%Y-%m")

    # Don't pay a month a second time once regular payroll has RELEASED its payslip.
    if _exit_month_released(db, emp.id, settle_ref.year, settle_ref.month):
        return {
            "amount": Decimal("0"), "paid_days": 0, "days": 0, "month_days": month_days,
            "monthly_gross": _round(gross), "per_day": _round(per_day),
            "settle_month": settle_month, "basis": "already_paid_via_payslip",
        }

    att = _attendance_paid_days(db, emp, settle_ref)
    paid = att["paid_days"]
    amount = per_day * paid
    return {
        "amount": _round(amount), "paid_days": _round(paid), "days": _round(paid),
        "month_days": month_days, "monthly_gross": _round(gross), "per_day": _round(per_day),
        "attendance_rows": att["rows"], "window_start": att["window_start"],
        "window_end": att["window_end"], "settle_month": settle_month,
        "basis": "attendance_paid_days",
    }


def _leave_encashment(db: Session, emp: Employee, base: Decimal, basis_label: str = "BASIC") -> Dict[str, Any]:
    """Sum encashable closing balances, valued at base/30 per day.

    ``base`` is the per-month pool the configured ENCASHMENT_BASIS resolves to
    (BASIC by default, optionally GROSS/CTC); ``basis_label`` is recorded in the
    snapshot for provenance.
    """
    basic = base
    try:
        from app.models.hr.leave_balance import LeaveBalance
        from app.models.hr.leave_type import LeaveType
        rows = (
            db.query(LeaveBalance)
            .filter(
                LeaveBalance.employee_id == emp.id,
                LeaveBalance.is_deleted == False,  # noqa: E712
            )
            .all()
        )
        wanted = {LeaveType[t] for t in ENCASHABLE_LEAVE_TYPES if t in LeaveType.__members__}
        # Latest fiscal year per type.
        best: Dict[Any, Any] = {}
        for r in rows:
            if r.leave_type not in wanted:
                continue
            cur = best.get(r.leave_type)
            if cur is None or (r.fiscal_year or "") > (cur.fiscal_year or ""):
                best[r.leave_type] = r
        days = sum((_d(r.closing_balance) for r in best.values()), Decimal("0"))
        days = max(days, Decimal("0"))
        per_day = (basic / Decimal("30")) if basic else Decimal("0")
        amount = per_day * days
        return {"amount": _round(amount), "days": _round(days), "per_day": _round(per_day), "basis": basis_label}
    except Exception:
        return {"amount": Decimal("0"), "days": Decimal("0"), "per_day": Decimal("0"), "basis": basis_label}


def _gratuity(emp: Employee, policy: Optional[ExitPolicy], basic: Decimal) -> Dict[str, Any]:
    if not policy or not policy.gratuity_enabled:
        return {"amount": Decimal("0"), "years": 0, "eligible": False}
    if not emp.joining_date:
        return {"amount": Decimal("0"), "years": 0, "eligible": False}
    end = emp.last_working_date or emp.exit_date or date.today()
    years = Decimal((end - emp.joining_date).days) / Decimal("365.25")
    min_years = _d(policy.gratuity_min_years)
    if years < min_years:
        return {"amount": Decimal("0"), "years": float(round(years, 2)), "eligible": False}
    # Standard formula: 15/26 × last basic × completed years.
    completed = int(years)
    amount = (Decimal("15") / Decimal("26")) * basic * Decimal(completed)
    return {"amount": _round(amount), "years": completed, "eligible": True}


def _approved_unpaid_reimbursements(db: Session, emp: Employee) -> Decimal:
    try:
        from app.models.hr.claim import Claim
        from app.models.hr.reimbursement_type import ClaimStatus
        rows = db.query(Claim).filter(
            Claim.employee_id == emp.id,
            Claim.is_deleted == False,  # noqa: E712
            Claim.status == ClaimStatus.APPROVED,
        ).all()
        return _round(sum((_d(getattr(c, "approved_amount", None) or getattr(c, "amount", 0)) for c in rows), Decimal("0")))
    except Exception:
        return Decimal("0")


def _notice_recovery(case: ExitCase, policy: Optional[ExitPolicy], comp, emp, default_basis: str = "BASIC") -> Dict[str, Any]:
    """Shortfall (or buyout) days × per-day basis. 0 if waived w/o buyout or full notice served.

    The per-policy ``buyout_basis`` wins when a policy applies; otherwise the org
    default (Payroll Rules → NOTICE_RECOVERY_BASIS, "BASIC" by default) is used.
    """
    required = resolved_notice_days(case, policy)
    basis = (policy.buyout_basis if policy else default_basis)
    per_day_pool = _monthly_basic(comp, emp) if basis == "BASIC" else _monthly_gross(comp, emp)
    per_day = (per_day_pool / Decimal("30")) if per_day_pool else Decimal("0")

    if case.notice_waived:
        bdays = case.notice_buyout_days or 0
        return {"amount": _round(per_day * Decimal(bdays)), "shortfall_days": bdays, "per_day": _round(per_day), "basis": basis, "mode": "buyout"}

    # Served days from notice start to LWD.
    if case.notice_period_start_date and case.last_working_date:
        served = max((case.last_working_date - case.notice_period_start_date).days, 0)
    else:
        served = required
    shortfall = max(required - served, 0)
    return {"amount": _round(per_day * Decimal(shortfall)), "shortfall_days": shortfall,
            "required": required, "served": served, "per_day": _round(per_day), "basis": basis, "mode": "shortfall"}


def _clearance_recovery(db: Session, case: ExitCase) -> Dict[str, Any]:
    """Sum of recovery_amount across clearance items (unreturned asset / dues)."""
    rows = db.query(ExitClearanceItem).filter(ExitClearanceItem.exit_case_id == case.id).all()
    total = sum((_d(r.recovery_amount) for r in rows if r.recovery_amount), Decimal("0"))
    return {"amount": _round(total)}


def _travel_advance_recovery(db: Session, emp: Employee) -> Dict[str, Any]:
    """Outstanding travel advances — cash already disbursed but not yet recovered
    (status RELEASED/SETTLED) — owed back by the employee in the F&F."""
    try:
        from app.models.hr.travel_advance import TravelAdvance
        from app.models.hr.travel_type import AdvanceStatus
        owed = {AdvanceStatus[s] for s in ("RELEASED", "SETTLED") if s in AdvanceStatus.__members__}
        rows = db.query(TravelAdvance).filter(
            TravelAdvance.employee_id == emp.id,
            TravelAdvance.is_deleted == False,  # noqa: E712
        ).all()
        total = Decimal("0")
        n = 0
        for r in rows:
            if r.status not in owed:
                continue
            base = _d(r.approved_amount if r.approved_amount is not None else r.advance_amount)
            outstanding = base - _d(r.recovered_amount)
            if outstanding > 0:
                total += outstanding
                n += 1
        return {"amount": _round(total), "count": n}
    except Exception:
        return {"amount": Decimal("0"), "count": 0}


def compute_settlement(
    db: Session,
    case: ExitCase,
    settlement: ExitSettlement,
    *,
    overrides: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
) -> ExitSettlement:
    """Compute all F&F lines onto ``settlement`` + a reproducible snapshot."""
    overrides = overrides or {}
    emp = case.employee or db.query(Employee).filter(Employee.id == case.employee_id).first()
    policy = case.policy
    comp = _active_comp(db, case.employee_id)
    basic = _monthly_basic(comp, emp)
    lwd = case.last_working_date or case.exit_date

    # Settlement bases from HR Settings → Payroll Rules. Both default to BASIC,
    # which matches the historical computation → no change until configured. The
    # per-policy buyout_basis still wins for notice recovery when a policy applies.
    from app.utils.hr.payroll.rule_config import get_rule
    enc_basis = str(get_rule(db, "ENCASHMENT_BASIS") or "BASIC").upper()
    notice_basis = str(get_rule(db, "NOTICE_RECOVERY_BASIS") or "BASIC").upper()

    pending = _pending_salary(db, emp, comp, lwd)
    leave = _leave_encashment(db, emp, _settlement_base(comp, emp, enc_basis, basic), basis_label=enc_basis)
    grat = _gratuity(emp, policy, basic)
    reimb = _approved_unpaid_reimbursements(db, emp)
    notice = _notice_recovery(case, policy, comp, emp, default_basis=notice_basis)
    asset_rec = _clearance_recovery(db, case)
    travel_adv = _travel_advance_recovery(db, emp)

    def ov(key: str, fallback: Decimal) -> Decimal:
        return _round(_d(overrides[key])) if key in overrides else _round(fallback)

    # ─── Earnings ───
    settlement.pending_salary = ov("pending_salary", pending["amount"])
    settlement.leave_encashment_amount = ov("leave_encashment_amount", leave["amount"])
    settlement.leave_encashment_days = _round(_d(overrides.get("leave_encashment_days", leave["days"])))
    settlement.incentives_amount = ov("incentives_amount", _d(settlement.incentives_amount))
    settlement.bonus_amount = ov("bonus_amount", _d(settlement.bonus_amount))
    settlement.reimbursements_amount = ov("reimbursements_amount", reimb)
    settlement.gratuity_amount = ov("gratuity_amount", grat["amount"])
    settlement.other_earnings = ov("other_earnings", _d(settlement.other_earnings))
    settlement.total_earnings = _round(
        settlement.pending_salary + settlement.leave_encashment_amount
        + settlement.incentives_amount + settlement.bonus_amount
        + settlement.reimbursements_amount + settlement.gratuity_amount
        + settlement.other_earnings
    )

    # ─── Recoveries ───
    settlement.notice_recovery = ov("notice_recovery", notice["amount"])
    settlement.loan_recovery = ov("loan_recovery", _d(settlement.loan_recovery))
    settlement.advance_recovery = ov("advance_recovery", travel_adv["amount"])
    settlement.asset_recovery = ov("asset_recovery", asset_rec["amount"])
    settlement.other_deductions = ov("other_deductions", _d(settlement.other_deductions))
    settlement.total_recoveries = _round(
        settlement.notice_recovery + settlement.loan_recovery
        + settlement.advance_recovery + settlement.asset_recovery
        + settlement.other_deductions
    )

    settlement.net_amount = _round(settlement.total_earnings - settlement.total_recoveries)

    settlement.computation_snapshot = {
        "basic_monthly": float(basic),
        "last_working_date": lwd.isoformat() if lwd else None,
        "pending_salary": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in pending.items()},
        "leave_encashment": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in leave.items()},
        "gratuity": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in grat.items()},
        "reimbursements": float(reimb),
        "notice_recovery": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in notice.items()},
        "asset_recovery": float(asset_rec["amount"]),
        "advance_recovery": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in travel_adv.items()},
        "overrides_applied": list(overrides.keys()),
        "override_note": (note or None),
        "totals": {
            "earnings": float(settlement.total_earnings),
            "recoveries": float(settlement.total_recoveries),
            "net": float(settlement.net_amount),
        },
    }

    # Mirror on the case for list/dashboard.
    case.settlement_net_amount = settlement.net_amount
    return settlement
