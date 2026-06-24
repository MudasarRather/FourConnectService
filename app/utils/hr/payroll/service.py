"""Payroll service layer — batch generation, transitions, audit, numbering.

Keeps the routers thin (CLAUDE.md ~50-line rule). All money math in Decimal.
Batch generation is idempotent: re-running deletes only non-released payslips
and re-inserts, guarded by the batch status state machine.
"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, List, Dict

from sqlalchemy import func as sa_func, or_, and_, exists
from sqlalchemy.orm import Session, joinedload

from app.models.system_setting import SystemSetting
from app.models.user import User
from app.models.hr.employee import Employee, LifecycleState
from app.models.hr.attendance import Attendance, AttendanceStatus
from app.models.hr.shift import Shift, ShiftType
from app.models.hr.night_policy import NightShiftPolicy
from app.models.hr.holiday import Holiday
from app.models.hr.holiday_shift import HolidayShiftAssignment, HolidayCompType
from app.models.hr.overtime import OvertimeRequest, OtStatus, OtPayrollStatus
from app.models.hr.overtime_rule import OvertimeRule
from app.models.hr.salary_component import ComponentType
from app.models.hr.employee_compensation import EmployeeCompensation, CompensationStatus
from app.models.hr.leave_encashment import LeaveEncashment
from app.models.hr.leave_type import EncashmentStatus
from app.models.hr.payroll_adjustment import PayrollAdjustment, AdjustmentStatus
from app.models.hr.payroll_batch import PayrollBatch, PayrollBatchStatus
from app.models.hr.payslip import Payslip, PayslipLine, PayslipStatus
from app.models.hr.payroll_config import PayrollAuditLog, PayrollAuditAction
from app.models.hr.salary_structure import SalaryStructure
from app.utils.hr.payroll import (
    load_config, resolve_structure, compute_payslip, days_in_month, fy_for,
)

Q2 = Decimal("0.01")
# Standard paid hours/day used as the OT hourly-rate divisor:
#   hourly = full monthly Basic(+DA) / (days_in_month × OT_HOURS_PER_DAY)
# One-line tunable if a different OT convention (e.g. fixed 26-day month) is needed.
OT_HOURS_PER_DAY = Decimal("8")


# ─────────────────────────────── numbering ───────────────────────────────

def _next_counter(db: Session, key: str, desc: str) -> int:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row:
        try:
            n = int(row.value) + 1
        except Exception:
            n = 1
        row.value = str(n)
    else:
        n = 1
        db.add(SystemSetting(key=key, value="1", description=desc))
    db.flush()
    return n


def next_batch_no(db: Session, year: int, month: int) -> str:
    yy = str(year)[-2:]
    for _ in range(6):
        n = _next_counter(db, "payroll_batch_counter", "Monotonic counter for PayrollBatch.batch_no")
        candidate = f"PR-{yy}-{month:02d}-{n:03d}"
        if not db.query(PayrollBatch.id).filter(PayrollBatch.batch_no == candidate).first():
            return candidate
    raise RuntimeError("Could not allocate batch number")


def next_payslip_no(db: Session, year: int, month: int, emp_code: str) -> str:
    base = f"PS-{str(year)[-2:]}{month:02d}-{(emp_code or 'EMP')}"
    cand = base
    i = 1
    while db.query(Payslip.id).filter(Payslip.payslip_no == cand).first():
        i += 1
        cand = f"{base}-{i}"
    return cand


# ─────────────────────────────── audit ───────────────────────────────

def write_audit(db: Session, *, entity_type: str, entity_id, action: PayrollAuditAction,
                batch_id=None, actor_id=None, from_status: Optional[str] = None,
                to_status: Optional[str] = None, note: Optional[str] = None,
                payload: Optional[Dict] = None) -> None:
    db.add(PayrollAuditLog(
        entity_type=entity_type, entity_id=entity_id, action=action,
        batch_id=batch_id, actor_id=actor_id, from_status=from_status,
        to_status=to_status, note=note, payload=payload,
    ))


# ─────────────────────────────── helpers ───────────────────────────────

def fy_for_period(year: int, month: int) -> str:
    return fy_for(date(year, month, 1))


def remaining_months_in_fy(month: int) -> int:
    return ((3 - month) % 12) + 1


def month_bounds(year: int, month: int):
    return date(year, month, 1), date(year, month, days_in_month(year, month))


def month_lop_days(db: Session, employee_id, year: int, month: int) -> Decimal:
    start, end = month_bounds(year, month)
    total = (
        db.query(sa_func.coalesce(sa_func.sum(Attendance.lop_days), 0))
        .filter(Attendance.employee_id == employee_id, Attendance.is_deleted == False,  # noqa: E712
                Attendance.date >= start, Attendance.date <= end)
        .scalar()
    )
    return Decimal(str(total or 0))


def tds_paid_ytd(db: Session, employee_id, year: int, month: int) -> Decimal:
    """TDS already deducted for this employee in the CURRENT fiscal year (Apr–Mar),
    on payslips for months BEFORE `month`. Feeds cumulative TDS averaging in the
    engine so the monthly deduction levels out and auto-catches-up after a capped
    (net-floored) month. Excludes cancelled payslips."""
    sy = year if month >= 4 else year - 1     # fiscal year starts in April
    fy_start_idx = sy * 12 + 4                 # April of the FY-start year
    cur_idx = year * 12 + month
    idx = Payslip.period_year * 12 + Payslip.period_month
    total = (
        db.query(sa_func.coalesce(sa_func.sum(PayslipLine.amount), 0))
        .join(Payslip, Payslip.id == PayslipLine.payslip_id)
        .filter(
            Payslip.employee_id == employee_id,
            Payslip.is_deleted == False,  # noqa: E712
            Payslip.status != PayslipStatus.CANCELLED,
            PayslipLine.component_code == "TDS",
            idx >= fy_start_idx, idx < cur_idx,
        )
        .scalar()
    )
    return Decimal(str(total or 0))


def taxable_oneoff_paid_ytd(db: Session, employee_id, year: int, month: int) -> Decimal:
    """One-time TAXABLE earnings already paid this FY on prior months' payslips —
    bonus / incentive / commission / arrear / night / holiday / overtime. These are
    NOT structure salary components (``component_id`` is null) and must be in the
    annual taxable base so their TDS is withheld. Leave encashment is excluded (its
    exemption u/s 10(10AA) is settled in the F&F, not auto-taxed here)."""
    sy = year if month >= 4 else year - 1
    fy_start_idx = sy * 12 + 4
    cur_idx = year * 12 + month
    idx = Payslip.period_year * 12 + Payslip.period_month
    total = (
        db.query(sa_func.coalesce(sa_func.sum(PayslipLine.amount), 0))
        .join(Payslip, Payslip.id == PayslipLine.payslip_id)
        .filter(
            Payslip.employee_id == employee_id,
            Payslip.is_deleted == False,  # noqa: E712
            Payslip.status != PayslipStatus.CANCELLED,
            PayslipLine.component_id.is_(None),          # one-time, not a salary component
            PayslipLine.component_code != "LEAVE_ENCASH",
            PayslipLine.is_taxable == True,              # noqa: E712
            PayslipLine.component_type == ComponentType.EARNING,
            idx >= fy_start_idx, idx < cur_idx,
        )
        .scalar()
    )
    return Decimal(str(total or 0))


def stale_payslips(db: Session, batch, *, tolerance: Decimal = Decimal("0.5")) -> List[Dict[str, Any]]:
    """Non-released payslips whose frozen Loss-of-Pay no longer matches attendance.

    A run generated BEFORE its period's attendance was finalized (or before later
    corrections / exit-related absences were recorded) carries stale LOP — releasing
    it would pay outdated figures (e.g. paying a full month to someone who never
    clocked in). We first complete the period's attendance (the same idempotent
    ``finalize_period_for_payroll`` step generation runs), then compare each payslip's
    stored ``lop_days`` against the live attendance total. Read-only on the payslips —
    callers gate APPROVE / RELEASE on an empty result and tell the admin to
    re-generate. Returns one dict per stale payslip."""
    finalize_period_for_payroll(db, batch.period_year, batch.period_month, batch.department_id)
    out: List[Dict[str, Any]] = []
    slips = (
        db.query(Payslip)
        .filter(Payslip.batch_id == batch.id, Payslip.is_deleted == False,  # noqa: E712
                Payslip.status.notin_([PayslipStatus.RELEASED, PayslipStatus.CANCELLED]))
        .all()
    )
    for s in slips:
        current = month_lop_days(db, s.employee_id, batch.period_year, batch.period_month)
        stored = Decimal(str(s.lop_days or 0))
        if abs(current - stored) > tolerance:
            out.append({
                "payslip_id": str(s.id),
                "employee_id": str(s.employee_id),
                "employee_code": getattr(s.employee, "employee_id", None),
                "stored_lop": float(stored),
                "current_lop": float(current),
            })
    return out


def finalize_period_for_payroll(db: Session, year: int, month: int, department_id=None) -> int:
    """Make attendance complete for a payroll period BEFORE pay is computed.

    Loss-of-Pay is summed from attendance rows (``month_lop_days``). A day with no
    row contributes 0 LOP — i.e. "no clock-in, no record" was silently being PAID.
    This gap-fills a definitive status (via the canonical ``daily_rollup``: WEEK_OFF /
    HOLIDAY / approved-LEAVE = paid, everything else unworked = ABSENT → LOP 1.0) for
    every fully-elapsed day of the period, for the *same candidate set payroll pays*
    (ACTIVE / ON_PROBATION / ON_NOTICE, plus EXITED owed a final settlement — not just
    ACTIVE like the daily finalizer). After this, an employee who never clocked in and
    has no APPROVED leave is correctly unpaid for those days.

    Safety: only CREATES missing rows (never recomputes an existing one — respects the
    recompute caution), only for days that have fully elapsed (skips today + future),
    and only within each employee's tenure window [joining_date, last_working_date].
    Idempotent. Returns the number of rows materialised.
    """
    from app.models.hr.attendance import AttendanceSource
    from app.utils.hr.attendance_logic import daily_rollup

    start, end = month_bounds(year, month)
    cutoff = min(end, date.today() - timedelta(days=1))   # only fully-elapsed days
    if cutoff < start:
        return 0
    emps = candidate_employees(db, year, month, department_id)
    created = 0
    for emp in emps:
        d_start = start
        if emp.joining_date and emp.joining_date > d_start:
            d_start = emp.joining_date
        d_end = cutoff
        lwd = emp.last_working_date or emp.exit_date
        if lwd and lwd < d_end:        # don't fabricate absence after they left
            d_end = lwd
        d = d_start
        touched = False
        while d <= d_end:
            has_row = (
                db.query(Attendance.id)
                .filter(Attendance.employee_id == emp.id, Attendance.date == d,
                        Attendance.is_deleted == False)  # noqa: E712
                .first()
            )
            if not has_row:
                daily_rollup(db, emp.id, d, source=AttendanceSource.SYSTEM)
                created += 1
                touched = True
            d += timedelta(days=1)
        if touched:
            db.commit()
    return created


def resolve_compensation(db: Session, employee: Employee, year: int, month: int) -> Optional[EmployeeCompensation]:
    """The compensation row whose [effective_from, effective_to] window COVERS the
    period — not merely the latest ACTIVE row.

    Compensation is effective-dated: a future-dated revision (still ACTIVE, e.g. a
    raise effective next quarter) must NOT bleed into the current/past periods,
    and a SUPERSEDED row remains the correct one for any period its window still
    covers. We therefore match on the date window across ACTIVE + SUPERSEDED rows
    (excluding only DRAFT / CANCELLED) and take the latest one that started on or
    before the period end. This is the contract documented on the model.
    """
    start, end = month_bounds(year, month)
    return (
        db.query(EmployeeCompensation)
        .filter(
            EmployeeCompensation.employee_id == employee.id,
            EmployeeCompensation.is_deleted == False,  # noqa: E712
            EmployeeCompensation.status.in_([CompensationStatus.ACTIVE, CompensationStatus.SUPERSEDED]),
            EmployeeCompensation.effective_from <= end,
            or_(EmployeeCompensation.effective_to.is_(None),
                EmployeeCompensation.effective_to >= start),
        )
        .order_by(EmployeeCompensation.effective_from.desc())
        .first()
    )


def create_compensation_revision(
    db: Session, employee: Employee, *, annual_ctc, effective_from,
    structure_id=None, monthly_ctc=None, tax_regime=None,
    revision_reason: Optional[str] = None, revision_ref: Optional[str] = None,
    tds_declarations: Optional[Dict] = None, activate: bool = True, actor_id=None,
) -> EmployeeCompensation:
    """THE single code path to add an EmployeeCompensation revision.

    Used by the Payroll → Compensation drawer AND by lifecycle promotions, so the
    two can never diverge. Computes the breakdown snapshot, and when ``activate``:
    supersedes prior active rows and mirrors the active CTC / regime / structure
    back onto the Employee record (the HR profile reads those fields). Does NOT
    commit — the caller owns the transaction boundary.
    """
    annual_ctc = Decimal(str(annual_ctc))
    monthly_ctc = Decimal(str(monthly_ctc)) if monthly_ctc is not None else (annual_ctc / 12)

    sid = structure_id or _structure_id_for(db, employee, None)
    regime_enum = tax_regime if tax_regime is not None else employee.tax_regime
    regime_str = regime_enum.value if (regime_enum is not None and hasattr(regime_enum, "value")) else (regime_enum or "NEW")

    gross = basic = breakdown = None
    if sid:
        components = resolve_structure(db, sid)
        if components:
            cfg = load_config(db, fy_for(date.today()), None)
            cfg["PF_RESTRICT_TO_CEILING"] = pf_restrict_for(db, sid)
            result = compute_payslip(
                components=components, monthly_ctc=monthly_ctc, annual_ctc=annual_ctc,
                monthly_gross_hint=None, regime=regime_str, declarations=tds_declarations,
                working_days=Decimal("30"), lop_days=Decimal("0"), cfg=cfg,
            )
            gross = result["gross_earnings"]
            basic = next((l["amount"] for l in result["lines"] if l["component_code"] == "BASIC"), None)
            breakdown = {l["component_code"]: str(l["amount"]) for l in result["lines"]}

    comp = EmployeeCompensation(
        employee_id=employee.id, structure_id=sid,
        effective_from=effective_from, annual_ctc=annual_ctc, monthly_ctc=monthly_ctc,
        monthly_gross=gross, basic_amount=basic, breakdown=breakdown,
        tax_regime=(regime_enum if (regime_enum is not None and hasattr(regime_enum, "value")) else employee.tax_regime),
        revision_reason=revision_reason, revision_ref=revision_ref, tds_declarations=tds_declarations,
        status=CompensationStatus.ACTIVE if activate else CompensationStatus.DRAFT,
        created_by_id=actor_id,
    )
    db.add(comp)
    db.flush()

    if activate:
        prior = db.query(EmployeeCompensation).filter(
            EmployeeCompensation.employee_id == employee.id,
            EmployeeCompensation.id != comp.id,
            EmployeeCompensation.is_deleted == False,  # noqa: E712
            EmployeeCompensation.status == CompensationStatus.ACTIVE,
        ).all()
        for p in prior:
            p.status = CompensationStatus.SUPERSEDED
            if not p.effective_to or p.effective_to >= effective_from:
                p.effective_to = effective_from - timedelta(days=1)
        if sid:
            employee.salary_structure_id = sid
        employee.annual_ctc = annual_ctc
        employee.monthly_ctc = monthly_ctc
        if comp.tax_regime:
            employee.tax_regime = comp.tax_regime

    write_audit(db, entity_type="COMPENSATION", entity_id=comp.id, action=PayrollAuditAction.CREATE,
                actor_id=actor_id, note=revision_reason or "New compensation")
    return comp


def _structure_id_for(db: Session, employee: Employee, comp: Optional[EmployeeCompensation]):
    if comp and comp.structure_id:
        return comp.structure_id
    if employee.salary_structure_id:
        return employee.salary_structure_id
    default = db.query(SalaryStructure).filter(
        SalaryStructure.is_default == True, SalaryStructure.is_deleted == False  # noqa: E712
    ).first()
    return default.id if default else None


def pf_restrict_for(db: Session, structure_id) -> bool:
    """The structure's PF policy: True = cap PF at the ₹15,000 ceiling (₹1,800),
    False = 12% of full Basic. Defaults to True when unknown."""
    if not structure_id:
        return True
    row = db.query(SalaryStructure.pf_restrict_to_ceiling).filter(
        SalaryStructure.id == structure_id).first()
    return True if row is None or row[0] is None else bool(row[0])


# ─────────────────────────────── eligibility ───────────────────────────────
# A single source of truth shared by the run wizard's eligibility preview, the
# Processing snapshot, and batch generation — so "who gets paid" is computed the
# same way everywhere and the run can never silently produce 0 with no reason.

PAYABLE_STATES = (LifecycleState.ACTIVE, LifecycleState.ON_PROBATION, LifecycleState.ON_NOTICE)

# Machine reason → human label surfaced in the eligibility roster / exceptions.
ELIGIBILITY_REASONS = {
    "no_compensation": "No active compensation or CTC assigned",
    "no_structure": "No salary structure resolves for this employee",
    "no_components": "Assigned salary structure has no components",
}


def candidate_employees(db: Session, year: int, month: int,
                        department_id=None) -> List[Employee]:
    """Employees in the payroll candidate set for a period.

    Includes (a) currently payable staff (ACTIVE / ON_PROBATION / ON_NOTICE) and
    (b) EXITED staff eligible for a final settlement — those who still hold an
    active compensation covering the period AND who actually worked into the
    month (attendance present, or their last-working/exit date lands on/after the
    period start). The exited path is what lets a mid-month leaver get a prorated
    final payslip instead of being silently dropped.
    """
    start, end = month_bounds(year, month)
    q = db.query(Employee).options(joinedload(Employee.user),
                                   joinedload(Employee.department)).filter(
        Employee.is_deleted == False)  # noqa: E712

    payable = Employee.lifecycle_state.in_(PAYABLE_STATES)
    has_active_comp = exists().where(and_(
        EmployeeCompensation.employee_id == Employee.id,
        EmployeeCompensation.is_deleted == False,  # noqa: E712
        EmployeeCompensation.status == CompensationStatus.ACTIVE,
        EmployeeCompensation.effective_from <= end,
    ))
    worked_into_period = exists().where(and_(
        Attendance.employee_id == Employee.id,
        Attendance.is_deleted == False,  # noqa: E712
        Attendance.date >= start, Attendance.date <= end,
    ))
    # An exited employee is payable ONLY through their last working day. If that
    # day is known, the period must start on or before it — a month entirely
    # AFTER the exit is never paid (closes the "stray post-exit attendance ⇒ full
    # pay" loophole). Only when no exit/last-working date is recorded do we fall
    # back to attendance as proof of work for the period.
    last_day = sa_func.coalesce(Employee.last_working_date, Employee.exit_date)
    exited_final_settlement = and_(
        Employee.lifecycle_state == LifecycleState.EXITED,
        has_active_comp,
        or_(
            last_day >= start,
            and_(Employee.last_working_date.is_(None), Employee.exit_date.is_(None), worked_into_period),
        ),
    )
    q = q.filter(or_(payable, exited_final_settlement))
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    return q.order_by(Employee.employee_id).all()


def payslip_blocker(db: Session, employee: Employee, year: int, month: int) -> Optional[str]:
    """Why (if at all) this employee cannot be paid for the period.

    Returns a machine reason key from ELIGIBILITY_REASONS, or None when payable.
    Mirrors exactly the None-paths of build_payslip_for_employee so the preview
    and the generated set never disagree.
    """
    comp = resolve_compensation(db, employee, year, month)
    monthly_ctc = comp.monthly_ctc if comp else employee.monthly_ctc
    if not monthly_ctc or Decimal(str(monthly_ctc)) <= 0:
        return "no_compensation"
    structure_id = _structure_id_for(db, employee, comp)
    if not structure_id:
        return "no_structure"
    if not resolve_structure(db, structure_id):
        return "no_components"
    return None


def resolve_eligibility(db: Session, year: int, month: int, department_id=None) -> Dict:
    """Build the eligibility roster + summary for a period/scope.

    For every eligible employee we run the REAL compute engine for the period, so
    the preview reflects exactly what generation will mint — including attendance
    + leave Loss-of-Pay proration (``lop_days`` already encodes paid leave = 0,
    unpaid leave / absence > 0) and statutory deductions. The roster therefore
    shows estimated NET pay, paid vs working days, and LOP — not just gross CTC.
    Pure read: safe before a batch exists (wizard) or after generate (exceptions).
    """
    # Finalize attendance first so "no clock-in / no approved leave" days count as
    # LOP instead of silently reading as paid (otherwise the preview overstates pay).
    finalize_period_for_payroll(db, year, month, department_id)
    emps = candidate_employees(db, year, month, department_id)
    cfg = load_config(db, fy_for_period(year, month), None)
    rows: List[Dict] = []
    eligible = 0
    final_settlement = 0
    est_monthly = Decimal("0")
    est_net = Decimal("0")
    est_gross = Decimal("0")
    est_employer = Decimal("0")
    for emp in emps:
        blocker = payslip_blocker(db, emp, year, month)
        comp = resolve_compensation(db, emp, year, month)
        monthly_ctc = Decimal(str((comp.monthly_ctc if comp else emp.monthly_ctc) or 0))
        is_exit = emp.lifecycle_state == LifecycleState.EXITED
        uname = None
        if emp.user:
            uname = getattr(emp.user, "full_name", None) or getattr(emp.user, "email", None)

        row = {
            "employee_id": emp.id, "employee_code": emp.employee_id, "employee_name": uname,
            "department_name": emp.department.name if emp.department else None,
            "lifecycle_state": emp.lifecycle_state.value, "monthly_ctc": monthly_ctc,
            "lop_days": month_lop_days(db, emp.id, year, month), "paid_days": None,
            "working_days": None, "est_gross": None, "est_net": None,
            "eligible": blocker is None, "reason": blocker,
            "reason_label": ELIGIBILITY_REASONS.get(blocker) if blocker else None,
            "final_settlement": is_exit and blocker is None,
            "net_floored": False, "tds_deferred": Decimal("0"),
        }
        if blocker is None:
            built = build_payslip_for_employee(db, emp, year, month, cfg)
            if built:
                row["lop_days"] = built["lop_days"]
                row["paid_days"] = built["paid_days"]
                row["working_days"] = built["working_days"]
                row["est_gross"] = built["gross_earnings"]
                row["est_net"] = built["net_pay"]
                # Net was floored to ≥ 0 (deductions exceeded gross) — surface as a
                # warning so HR sees it instead of a silently-floored payout.
                row["net_floored"] = bool(built.get("net_floored"))
                row["tds_deferred"] = built.get("tds_deferred") or Decimal("0")
                eligible += 1
                est_monthly += monthly_ctc
                est_net += built["net_pay"]
                est_gross += built["gross_earnings"]
                est_employer += built["employer_contributions"]
                if is_exit:
                    final_settlement += 1
            else:
                # build refused after all (race / data change) — treat as blocked
                row["eligible"] = False
                row["reason"] = "no_compensation"
                row["reason_label"] = ELIGIBILITY_REASONS["no_compensation"]
                row["final_settlement"] = False
        rows.append(row)
    # eligible first, then by code; blocked sink to the bottom of the roster
    rows.sort(key=lambda r: (not r["eligible"], r["employee_code"] or ""))
    eligible = sum(1 for r in rows if r["eligible"])
    floored_count = sum(1 for r in rows if r.get("net_floored"))
    return {
        "period_month": month, "period_year": year, "department_id": department_id,
        "total_candidates": len(emps), "eligible_count": eligible,
        "blocked_count": len(emps) - eligible, "final_settlement_count": final_settlement,
        "floored_count": floored_count,
        "estimated_monthly_ctc": est_monthly.quantize(Q2),
        "estimated_gross": est_gross.quantize(Q2), "estimated_net": est_net.quantize(Q2),
        "estimated_employer_cost": est_employer.quantize(Q2), "rows": rows,
    }


def _approved_encashment(db: Session, employee_id) -> Decimal:
    total = (
        db.query(sa_func.coalesce(sa_func.sum(LeaveEncashment.amount), 0))
        .filter(LeaveEncashment.employee_id == employee_id,
                LeaveEncashment.is_deleted == False,  # noqa: E712
                LeaveEncashment.status == EncashmentStatus.APPROVED,
                LeaveEncashment.paid_at.is_(None))
        .scalar()
    )
    return Decimal(str(total or 0))


def _pending_adjustments(db: Session, employee_id, year: int, month: int) -> list:
    """Approved, unpaid adjustments for this employee that target this period
    (or are unscoped). Returned as engine-ready dicts."""
    rows = (
        db.query(PayrollAdjustment)
        .filter(
            PayrollAdjustment.employee_id == employee_id,
            PayrollAdjustment.is_deleted == False,  # noqa: E712
            PayrollAdjustment.status == AdjustmentStatus.APPROVED,
            PayrollAdjustment.paid_at.is_(None),
            sa_func.coalesce(PayrollAdjustment.period_year, year) == year,
            sa_func.coalesce(PayrollAdjustment.period_month, month) == month,
        )
        .all()
    )
    return [{
        "code": r.adjustment_type.value, "adjustment_type": r.adjustment_type.value,
        "title": r.title, "amount": Decimal(str(r.amount or 0)),
        "is_deduction": r.is_deduction, "is_taxable": r.is_taxable,
        "note": (r.sub_type or r.adjustment_type.value),
    } for r in rows]


# Statuses that prove the employee actually WORKED that day (earns night allowance
# / holiday premium). ABSENT / LEAVE / WEEK_OFF / HOLIDAY / LWP do NOT. HALF_DAY = half.
_WORKED_STATUSES = (
    AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.ON_DUTY,
    AttendanceStatus.WFH, AttendanceStatus.REMOTE, AttendanceStatus.HALF_DAY,
)

# OT must never pay on a day the employee did NOT actually work. Paying OT on top
# of a paid LEAVE day is double-pay; ABSENT / LWP / rested HOLIDAY / WEEK_OFF are
# likewise non-worked. A holiday or week-off that was actually worked carries a
# worked status (PRESENT/LATE) — daily_rollup only stamps HOLIDAY/WEEK_OFF when
# there was no clock-in — so those legitimately keep their OT.
_OT_NONWORK_STATUSES = (
    AttendanceStatus.ABSENT, AttendanceStatus.LEAVE, AttendanceStatus.LWP,
    AttendanceStatus.WEEK_OFF, AttendanceStatus.HOLIDAY,
)


def _night_allowance(db: Session, employee_id, year: int, month: int):
    """Per-night allowance the employee earned this period.

    Paid per night ACTUALLY WORKED on a NIGHT-type shift that has an active night
    policy with a positive allowance (set in the Night Shifts console at
    /admin/hr/shifts/night). Counted from the daily Attendance rows — which carry
    the shift used that day — so it reflects attendance truth and is idempotent on
    re-generate (re-derived, never stored as a pending row → no double-pay).
    HALF_DAY earns half a night's allowance. Returns (total Decimal, nights Decimal).
    """
    start, end = month_bounds(year, month)
    # {shift_id: per-night allowance} for NIGHT shifts that carry a live policy.
    pol_map = dict(
        db.query(NightShiftPolicy.shift_id, NightShiftPolicy.allowance_amount)
        .join(Shift, Shift.id == NightShiftPolicy.shift_id)
        .filter(NightShiftPolicy.is_deleted == False,  # noqa: E712
                NightShiftPolicy.allowance_amount > 0,
                Shift.shift_type == ShiftType.NIGHT,
                Shift.is_deleted == False)  # noqa: E712
        .all()
    )
    if not pol_map:
        return Decimal("0"), Decimal("0")
    rows = (
        db.query(Attendance.shift_id, Attendance.status, sa_func.count(Attendance.id))
        .filter(Attendance.employee_id == employee_id,
                Attendance.is_deleted == False,  # noqa: E712
                Attendance.date >= start, Attendance.date <= end,
                Attendance.shift_id.in_(list(pol_map.keys())),
                Attendance.status.in_(_WORKED_STATUSES))
        .group_by(Attendance.shift_id, Attendance.status)
        .all()
    )
    total = Decimal("0")
    nights = Decimal("0")
    for shift_id, status, cnt in rows:
        weight = Decimal("0.5") if status == AttendanceStatus.HALF_DAY else Decimal("1")
        n = Decimal(str(cnt)) * weight
        nights += n
        total += n * Decimal(str(pol_map.get(shift_id) or 0))
    return total.quantize(Q2), nights


def _holiday_premium(db: Session, employee: Employee, year: int, month: int, day_salary_base) -> tuple:
    """Premium pay for working holidays this period (HolidayShiftAssignment).

    Corporate rule: working a holiday pays DOUBLE the day's salary. A holiday is
    already paid once inside the monthly salary, so we add only the PREMIUM above
    normal pay per holiday actually worked:
        premium = (pay_multiplier - 1) × daily_salary
        daily_salary = day_salary_base / days_in_month   (a full day's pay)
    DOUBLE_PAY @ 2.0 → +1 day's salary ⇒ that day totals 2× = double pay. HALF_DAY
    earns half. COMP_OFF is a comp-off LEAVE credit, not cash → excluded here.
    Attendance-gated (only paid if the employee actually worked the holiday) and
    re-derived every run (no stored flag) → idempotent, no double-pay.
    Returns (total Decimal, holidays_worked Decimal).
    """
    start, end = month_bounds(year, month)
    rows = (
        db.query(HolidayShiftAssignment, Holiday.date)
        .join(Holiday, Holiday.id == HolidayShiftAssignment.holiday_id)
        .filter(HolidayShiftAssignment.employee_id == employee.id,
                HolidayShiftAssignment.is_deleted == False,  # noqa: E712
                HolidayShiftAssignment.compensation != HolidayCompType.COMP_OFF,
                Holiday.is_deleted == False,  # noqa: E712
                # Only APPLIED holidays are live. A draft holiday never short-circuits
                # the daily rollup, so the day is normal work — never pay a premium for it.
                Holiday.is_active == True,  # noqa: E712
                Holiday.date >= start, Holiday.date <= end)
        .all()
    )
    if not rows:
        return Decimal("0"), Decimal("0")
    days = Decimal(str(days_in_month(year, month)))
    base = Decimal(str(day_salary_base or 0))
    if base <= 0 or days <= 0:
        return Decimal("0"), Decimal("0")
    daily = base / days
    # Attendance proof: did they actually work the holiday? (HOLIDAY status = rested)
    want_dates = {d for _, d in rows}
    att = dict(
        db.query(Attendance.date, Attendance.status)
        .filter(Attendance.employee_id == employee.id, Attendance.is_deleted == False,  # noqa: E712
                Attendance.date.in_(list(want_dates))).all()
    )
    total = Decimal("0")
    worked = Decimal("0")
    for a, hdate in rows:
        st = att.get(hdate)
        if st not in _WORKED_STATUSES:
            continue
        premium = Decimal(str(a.pay_multiplier or 0)) - Decimal("1")
        if premium <= 0:  # multiplier ≤ 1 (e.g. plain allowance / comp-off) → no cash premium
            continue
        weight = Decimal("0.5") if st == AttendanceStatus.HALF_DAY else Decimal("1")
        worked += weight
        total += weight * daily * premium
    return total.quantize(Q2), worked


def _overtime_pay(db: Session, employee: Employee, year: int, month: int,
                  slip_result: Dict, working_days: Decimal, fallback_base) -> tuple:
    """Payable overtime for the period from APPROVED, not-yet-processed requests.

    Per OvertimeRequest dated in the period:
      payable_hours × hourly_rate × multiplier
      • hourly_rate = full monthly Basic(+DA) / (days_in_month × OT_HOURS_PER_DAY),
        falling back to monthly gross/CTC when the structure has no BASIC line.
      • multiplier = the NIGHT-shift differential (NightShiftPolicy.overtime_rate)
        when that date's attendance shift is a NIGHT shift with a policy; otherwise
        the highest-priority active OvertimeRule for the ot_type (with its hour cap).

    Re-derived every run from APPROVED+PENDING requests, so re-generating a batch
    can't double-pay; release marks them PROCESSED (see post_overtime_processed).
    Returns (total Decimal, hours Decimal).
    """
    start, end = month_bounds(year, month)
    reqs = (
        db.query(OvertimeRequest)
        .filter(OvertimeRequest.employee_id == employee.id,
                OvertimeRequest.is_deleted == False,  # noqa: E712
                OvertimeRequest.status == OtStatus.APPROVED,
                OvertimeRequest.payroll_status == OtPayrollStatus.PENDING,
                OvertimeRequest.date >= start, OvertimeRequest.date <= end)
        .all()
    )
    if not reqs:
        return Decimal("0"), Decimal("0")

    # Hourly base — full (un-prorated) monthly Basic(+DA) from the computed slip.
    by_code = {l["component_code"]: l for l in slip_result.get("lines", [])}
    base = Decimal("0")
    if "BASIC" in by_code:
        base = Decimal(str(by_code["BASIC"]["full_amount"] or 0))
        for da_code in ("DA", "DEARNESS_ALLOWANCE", "DEARNESS"):
            if da_code in by_code:
                base += Decimal(str(by_code[da_code]["full_amount"] or 0))
                break
    if base <= 0:
        base = Decimal(str(fallback_base or 0))
    days = Decimal(str(working_days or 0))
    if base <= 0 or days <= 0:
        return Decimal("0"), Decimal("0")
    hourly = base / (days * OT_HOURS_PER_DAY)

    # Highest-priority active OT rule per type (mirrors /overtime-rules/resolve).
    rule_by_type = {}
    for r in (db.query(OvertimeRule)
              .filter(OvertimeRule.is_deleted == False, OvertimeRule.is_active == True)  # noqa: E712
              .order_by(OvertimeRule.priority.desc(), OvertimeRule.created_at.desc()).all()):
        rule_by_type.setdefault(r.ot_type, r)

    # Night differential: {date: shift_id} this period × {night shift_id: ot_rate}.
    # Also capture {date: status} so OT can be skipped on non-worked days.
    _att_rows = (
        db.query(Attendance.date, Attendance.shift_id, Attendance.status)
        .filter(Attendance.employee_id == employee.id, Attendance.is_deleted == False,  # noqa: E712
                Attendance.date >= start, Attendance.date <= end).all()
    )
    att = {d: sid for d, sid, _st in _att_rows}
    att_status = {d: _st for d, _sid, _st in _att_rows}
    night_rate = dict(
        db.query(NightShiftPolicy.shift_id, NightShiftPolicy.overtime_rate)
        .join(Shift, Shift.id == NightShiftPolicy.shift_id)
        .filter(NightShiftPolicy.is_deleted == False,  # noqa: E712
                NightShiftPolicy.overtime_rate > 1,
                Shift.shift_type == ShiftType.NIGHT, Shift.is_deleted == False)  # noqa: E712
        .all()
    )

    total = Decimal("0")
    hours_paid = Decimal("0")
    for req in reqs:
        hrs = Decimal(str(req.ot_hours or 0))
        if hrs <= 0:
            continue
        # Skip OT on a day the employee didn't actually work (paid LEAVE, ABSENT,
        # LWP, or a rested holiday/week-off) — paying it would double-pay the day.
        # To earn OT on a booked-off day the leave must first be cancelled, which
        # flips the day back to a worked status (then this pays it on a later run).
        if att_status.get(req.date) in _OT_NONWORK_STATUSES:
            continue
        sid = att.get(req.date)
        nrate = night_rate.get(sid) if sid else None
        if nrate:  # OT worked on a night shift → night differential
            mult, cap = Decimal(str(nrate)), None
        else:
            rule = rule_by_type.get(req.ot_type)
            mult = Decimal(str(rule.multiplier)) if rule else Decimal("1")
            cap = Decimal(str(rule.max_ot_hours)) if (rule and rule.max_ot_hours is not None) else None
        payable = min(hrs, cap) if cap is not None else hrs
        hours_paid += payable
        total += payable * hourly * mult
    return total.quantize(Q2), hours_paid


def post_overtime_processed(db: Session, batch: PayrollBatch, actor_id) -> None:
    """On release, mark this batch's APPROVED+PENDING in-period OT as PROCESSED so
    it is never picked up by a later run. Mirrors post_adjustments_paid."""
    emp_ids = [r[0] for r in db.query(Payslip.employee_id).filter(Payslip.batch_id == batch.id).all()]
    if not emp_ids:
        return
    start, end = month_bounds(batch.period_year, batch.period_month)
    rows = db.query(OvertimeRequest).filter(
        OvertimeRequest.employee_id.in_(emp_ids),
        OvertimeRequest.is_deleted == False,  # noqa: E712
        OvertimeRequest.status == OtStatus.APPROVED,
        OvertimeRequest.payroll_status == OtPayrollStatus.PENDING,
        OvertimeRequest.date >= start, OvertimeRequest.date <= end,
    ).all()
    # Mirror the _overtime_pay gate: only OT that was actually PAYABLE (a worked
    # day) is marked PROCESSED. OT skipped because the day was LEAVE/ABSENT/etc.
    # stays PENDING, so it can still pay later if that day is converted to a
    # worked day (e.g. the leave for it is cancelled) — never silently consumed.
    att_status = {
        (e, d): st for e, d, st in db.query(
            Attendance.employee_id, Attendance.date, Attendance.status
        ).filter(
            Attendance.employee_id.in_(emp_ids), Attendance.is_deleted == False,  # noqa: E712
            Attendance.date >= start, Attendance.date <= end,
        ).all()
    }
    for r in rows:
        if att_status.get((r.employee_id, r.date)) in _OT_NONWORK_STATUSES:
            continue
        r.payroll_status = OtPayrollStatus.PROCESSED


def post_adjustments_paid(db: Session, batch: PayrollBatch, actor_id) -> None:
    """On release, mark this batch's matching approved adjustments as PAID."""
    emp_ids = [r[0] for r in db.query(Payslip.employee_id).filter(Payslip.batch_id == batch.id).all()]
    if not emp_ids:
        return
    rows = db.query(PayrollAdjustment).filter(
        PayrollAdjustment.employee_id.in_(emp_ids),
        PayrollAdjustment.is_deleted == False,  # noqa: E712
        PayrollAdjustment.status == AdjustmentStatus.APPROVED,
        PayrollAdjustment.paid_at.is_(None),
        sa_func.coalesce(PayrollAdjustment.period_year, batch.period_year) == batch.period_year,
        sa_func.coalesce(PayrollAdjustment.period_month, batch.period_month) == batch.period_month,
    ).all()
    now = datetime.now(timezone.utc)
    paid_adj_ids = []
    for r in rows:
        r.status = AdjustmentStatus.PAID
        r.paid_at = now
        r.batch_id = batch.id
        r.payroll_ref = batch.batch_no
        paid_adj_ids.append(r.id)

    # Keep reimbursement claims in lockstep: any SETTLED claim whose payroll
    # adjustment was just paid flips to PAID with the batch's payroll ref.
    if paid_adj_ids:
        try:
            from app.models.hr.claim import Claim
            from app.models.hr.reimbursement_type import ClaimStatus
            claims = db.query(Claim).filter(
                Claim.payroll_adjustment_id.in_(paid_adj_ids),
                Claim.status == ClaimStatus.SETTLED,
                Claim.is_deleted == False,  # noqa: E712
            ).all()
            for c in claims:
                c.status = ClaimStatus.PAID
                c.paid_at = now
                c.payroll_ref = batch.batch_no
        except Exception:
            # Reimbursement module may not be present in older deployments.
            pass


def _serialize_cfg(cfg: Dict) -> Dict:
    out = {}
    for k, v in cfg.items():
        if isinstance(v, Decimal):
            out[k] = str(v)
        else:
            out[k] = v
    return out


# ─────────────────────────────── compute one payslip dict ───────────────────────────────

def build_payslip_for_employee(db: Session, employee: Employee, year: int, month: int,
                               cfg: Dict) -> Optional[Dict]:
    comp = resolve_compensation(db, employee, year, month)
    monthly_ctc = None
    annual_ctc = None
    regime = "NEW"
    declarations = None
    monthly_gross_hint = None
    if comp:
        monthly_ctc = comp.monthly_ctc
        annual_ctc = comp.annual_ctc
        regime = (comp.tax_regime.value if comp.tax_regime else (employee.tax_regime.value if employee.tax_regime else "NEW"))
        declarations = comp.tds_declarations
        monthly_gross_hint = comp.monthly_gross
    else:
        monthly_ctc = employee.monthly_ctc
        annual_ctc = employee.annual_ctc
        regime = employee.tax_regime.value if employee.tax_regime else "NEW"
    if not monthly_ctc or Decimal(str(monthly_ctc)) <= 0:
        return None  # can't pay an employee with no compensation
    if not annual_ctc:
        annual_ctc = Decimal(str(monthly_ctc)) * 12

    structure_id = _structure_id_for(db, employee, comp)
    if not structure_id:
        return None
    components = resolve_structure(db, structure_id)
    if not components:
        return None

    working = Decimal(str(days_in_month(year, month)))   # proration denominator = full month
    lop = month_lop_days(db, employee.id, year, month)
    start, end = month_bounds(year, month)
    # ── Payable window ───────────────────────────────────────────────────────
    # An employee is only paid for days that are actually ACCOUNTABLE within the
    # period. `lop` above already captures absences on finalized (elapsed) days;
    # every day OUTSIDE the accountable window is unpaid and added to LOP:
    #   • before the joining date (mid-period joiner),
    #   • after the last working day (mid-period leaver / final settlement),
    #   • NOT YET ELAPSED — a still-running month must not pay for days that
    #     haven't happened. Those days carry no attendance, so paying them is how
    #     a no-show was being paid for the rest of the month. They become payable
    #     as they elapse (and are finalized), so a month-end run pays in full.
    win_start = max(start, employee.joining_date) if employee.joining_date else start
    win_end = end
    today = date.today()
    if today <= end:                                     # in-progress / future month
        win_end = min(win_end, today - timedelta(days=1))   # only fully-elapsed days
    lwd = employee.last_working_date or employee.exit_date
    if employee.lifecycle_state == LifecycleState.EXITED and lwd:
        win_end = min(win_end, lwd)
    if win_start > start:
        lop = lop + Decimal(str((win_start - start).days))     # pre-joining days
    if win_end < end:
        lop = lop + Decimal(str((end - win_end).days))         # post-LWD / not-yet-elapsed days
    if lop > working:
        lop = working
    encash = _approved_encashment(db, employee.id)
    adjustments = _pending_adjustments(db, employee.id, year, month)

    # Night-shift allowance — per night actually worked on a NIGHT shift that
    # carries an active policy (rate set in /admin/hr/shifts/night). Injected as a
    # taxable earning line; it is RE-DERIVED from attendance every generate (never
    # a stored pending row), so re-running a batch can't double-pay it.
    night_total, night_nights = _night_allowance(db, employee.id, year, month)
    if night_total > 0:
        adjustments = list(adjustments) + [{
            "code": "NIGHT_ALLOWANCE", "adjustment_type": "NIGHT_ALLOWANCE",
            "title": "Night Shift Allowance", "amount": night_total,
            "is_deduction": False, "is_taxable": True,
            "note": f"{night_nights} night(s) worked × per-shift allowance",
        }]

    # Holiday pay — premium for working a holiday (HolidayShiftAssignment). Double-pay
    # rule: the day is already paid in monthly salary, so we add (multiplier-1)× a
    # day's salary (2.0× ⇒ +1 day ⇒ double pay). Re-derived each run → no double-pay.
    hol_total, hol_worked = _holiday_premium(db, employee, year, month, monthly_gross_hint or monthly_ctc)
    if hol_total > 0:
        adjustments = list(adjustments) + [{
            "code": "HOLIDAY_PAY", "adjustment_type": "HOLIDAY_PAY",
            "title": "Holiday Pay (premium)", "amount": hol_total,
            "is_deduction": False, "is_taxable": True,
            "note": f"{hol_worked} holiday(s) worked × premium over normal day pay",
        }]

    # Apply this employee's structure PF policy (cap at ceiling vs full Basic) for
    # this computation. cfg is shared across the batch, so set it per employee.
    cfg["PF_RESTRICT_TO_CEILING"] = pf_restrict_for(db, structure_id)
    # One-time TAXABLE earnings this period (bonus/incentive/commission/arrear +
    # night/holiday — all carry is_taxable) feed the TDS annual base ONCE, plus the
    # taxable one-offs already paid earlier this FY, so a bonus is actually withheld.
    this_month_taxable_extra = sum(
        (Decimal(str(a.get("amount") or 0)) for a in adjustments
         if a.get("is_taxable", True) and not a.get("is_deduction")),
        Decimal("0"),
    )
    extra_annual_taxable = this_month_taxable_extra + taxable_oneoff_paid_ytd(db, employee.id, year, month)
    result = compute_payslip(
        components=components, monthly_ctc=Decimal(str(monthly_ctc)),
        annual_ctc=Decimal(str(annual_ctc)), monthly_gross_hint=monthly_gross_hint,
        regime=regime, declarations=declarations, working_days=working, lop_days=lop,
        cfg=cfg, encashment_amount=encash, remaining_months=remaining_months_in_fy(month),
        adjustments=adjustments, tds_paid_ytd=tds_paid_ytd(db, employee.id, year, month),
        extra_annual_taxable=extra_annual_taxable,
    )
    # Overtime — APPROVED, not-yet-processed OT dated in this period. Appended
    # after the slip is computed so the hourly rate can use the actual computed
    # Basic(+DA); ×OT-rule multiplier, or the night-shift differential when the OT
    # was worked on a night shift. Idempotent: re-derived each run, marked
    # PROCESSED only on release (post_overtime_processed) → no double-pay.
    ot_total, ot_hours = _overtime_pay(db, employee, year, month, result, working,
                                       monthly_gross_hint or monthly_ctc)
    if ot_total > 0:
        result["lines"].append({
            "component_id": None, "component_code": "OVERTIME",
            "component_name": "Overtime", "component_type": ComponentType.EARNING,
            "statutory_kind": None, "sequence": 47,
            "full_amount": ot_total, "amount": ot_total,
            "is_taxable": True, "is_employer_cost": False,
            "calc_note": f"{ot_hours} OT hr(s) this period",
        })
        result["lines"].sort(key=lambda x: (x["sequence"], x["component_code"]))
        result["gross_earnings"] = (Decimal(str(result["gross_earnings"])) + ot_total).quantize(Q2)
        result["net_pay"] = (Decimal(str(result["net_pay"])) + ot_total).quantize(Q2)
        result["ctc_value"] = (Decimal(str(result["ctc_value"])) + ot_total).quantize(Q2)

    result["_comp"] = comp
    result["_regime"] = regime
    result["_encash"] = encash
    return result


# ─────────────────────────────── batch generation ───────────────────────────────

GENERATE_BLOCKED = {PayrollBatchStatus.APPROVED, PayrollBatchStatus.RELEASED,
                    PayrollBatchStatus.LOCKED, PayrollBatchStatus.CANCELLED}


def generate_batch(db: Session, batch: PayrollBatch, actor_id) -> PayrollBatch:
    """(Re)compute every payslip in the batch. Idempotent — only non-released
    payslips are deleted and re-inserted."""
    if batch.status in GENERATE_BLOCKED:
        raise ValueError(f"Cannot generate a batch in status {batch.status.value}")

    # Finalize attendance for the period first (gap-fill no-clock-in days as LOP, in
    # its own committed step) so generation can't pay for days with no attendance and
    # no approved leave — mirrors what the eligibility preview showed.
    finalize_period_for_payroll(db, batch.period_year, batch.period_month, batch.department_id)

    fy = fy_for_period(batch.period_year, batch.period_month)
    cfg = load_config(db, fy, None)
    batch.config_snapshot = _serialize_cfg(cfg)

    # Drop existing non-released payslips (DB FK ON DELETE CASCADE removes lines).
    keep_released = (
        db.query(Payslip.id).filter(Payslip.batch_id == batch.id,
                                    Payslip.status == PayslipStatus.RELEASED).count()
    )
    db.query(Payslip).filter(
        Payslip.batch_id == batch.id, Payslip.status != PayslipStatus.RELEASED
    ).delete(synchronize_session=False)
    db.flush()

    # Shared candidate set — currently-payable staff plus exited employees owed a
    # final settlement (see candidate_employees). This is the same set the run
    # wizard previews, so what the admin saw is exactly what gets minted.
    employees = candidate_employees(db, batch.period_year, batch.period_month, batch.department_id)

    total_gross = total_ded = total_net = total_empr = Decimal("0")
    count = 0
    skipped: List[Dict] = []
    for emp in employees:
        # skip if a released payslip already exists for this employee
        if db.query(Payslip.id).filter(Payslip.batch_id == batch.id, Payslip.employee_id == emp.id,
                                       Payslip.status == PayslipStatus.RELEASED).first():
            continue
        built = build_payslip_for_employee(db, emp, batch.period_year, batch.period_month, cfg)
        if not built:
            # Record WHY this candidate was excluded instead of dropping silently.
            skipped.append({
                "employee_id": str(emp.id), "employee_code": emp.employee_id,
                "reason": payslip_blocker(db, emp, batch.period_year, batch.period_month) or "no_compensation",
            })
            continue
        comp = built.get("_comp")
        slip = Payslip(
            batch_id=batch.id, employee_id=emp.id,
            compensation_id=comp.id if comp else None,
            payslip_no=next_payslip_no(db, batch.period_year, batch.period_month, emp.employee_id),
            period_month=batch.period_month, period_year=batch.period_year,
            status=PayslipStatus.GENERATED,
            working_days=built["working_days"], lop_days=built["lop_days"], paid_days=built["paid_days"],
            tax_regime=emp.tax_regime,
            gross_earnings=built["gross_earnings"], total_deductions=built["total_deductions"],
            net_pay=built["net_pay"], employer_contributions=built["employer_contributions"],
            ctc_value=built["ctc_value"], encashment_amount=built["encashment_amount"],
            bank_name=emp.bank_name, account_number=emp.account_number, ifsc=emp.ifsc,
            pf_number=emp.pf_number, esic_number=emp.esic_number, uan=emp.uan, pan=emp.pan,
        )
        db.add(slip)
        db.flush()
        for ln in built["lines"]:
            db.add(PayslipLine(payslip_id=slip.id, **ln))
        total_gross += built["gross_earnings"]
        total_ded += built["total_deductions"]
        total_net += built["net_pay"]
        total_empr += built["employer_contributions"]
        count += 1

    batch.total_employees = count + keep_released
    batch.total_gross = total_gross.quantize(Q2)
    batch.total_deductions = total_ded.quantize(Q2)
    batch.total_net = total_net.quantize(Q2)
    batch.total_employer_cost = total_empr.quantize(Q2)
    prev = batch.status.value
    batch.status = PayrollBatchStatus.GENERATED
    batch.generated_at = datetime.now(timezone.utc)
    batch.generated_by_id = actor_id
    note = f"Generated {count} payslips"
    if skipped:
        note += f"; skipped {len(skipped)} (no compensation/structure)"
    write_audit(db, entity_type="BATCH", entity_id=batch.id,
                action=PayrollAuditAction.GENERATE, batch_id=batch.id, actor_id=actor_id,
                from_status=prev, to_status=batch.status.value,
                note=note, payload={"skipped": skipped} if skipped else None)
    return batch


def recalc_employee(db: Session, slip: Payslip, actor_id) -> Payslip:
    batch = slip.batch
    if batch.status in (PayrollBatchStatus.APPROVED, PayrollBatchStatus.RELEASED, PayrollBatchStatus.LOCKED):
        raise ValueError("Batch is locked for edits")
    emp = slip.employee
    fy = fy_for_period(batch.period_year, batch.period_month)
    cfg = load_config(db, fy, None)
    built = build_payslip_for_employee(db, emp, batch.period_year, batch.period_month, cfg)
    if not built:
        raise ValueError("Employee has no payable compensation")
    db.query(PayslipLine).filter(PayslipLine.payslip_id == slip.id).delete(synchronize_session=False)
    slip.working_days = built["working_days"]
    slip.lop_days = built["lop_days"]
    slip.paid_days = built["paid_days"]
    slip.gross_earnings = built["gross_earnings"]
    slip.total_deductions = built["total_deductions"]
    slip.net_pay = built["net_pay"]
    slip.employer_contributions = built["employer_contributions"]
    slip.ctc_value = built["ctc_value"]
    slip.encashment_amount = built["encashment_amount"]
    db.flush()
    for ln in built["lines"]:
        db.add(PayslipLine(payslip_id=slip.id, **ln))
    write_audit(db, entity_type="PAYSLIP", entity_id=slip.id,
                action=PayrollAuditAction.REGENERATE, batch_id=batch.id, actor_id=actor_id,
                note="Recalculated single payslip")
    return slip


# ─────────────────────────────── transitions ───────────────────────────────

# legal next statuses per current status
TRANSITIONS = {
    PayrollBatchStatus.DRAFT: {PayrollBatchStatus.GENERATED, PayrollBatchStatus.CANCELLED},
    PayrollBatchStatus.GENERATED: {PayrollBatchStatus.VERIFIED, PayrollBatchStatus.DRAFT, PayrollBatchStatus.CANCELLED},
    PayrollBatchStatus.VERIFIED: {PayrollBatchStatus.APPROVED, PayrollBatchStatus.GENERATED, PayrollBatchStatus.CANCELLED},
    PayrollBatchStatus.APPROVED: {PayrollBatchStatus.RELEASED, PayrollBatchStatus.GENERATED},
    PayrollBatchStatus.RELEASED: {PayrollBatchStatus.LOCKED},
    PayrollBatchStatus.LOCKED: set(),
    PayrollBatchStatus.CANCELLED: set(),
}


def can_transition(current: PayrollBatchStatus, target: PayrollBatchStatus) -> bool:
    return target in TRANSITIONS.get(current, set())
