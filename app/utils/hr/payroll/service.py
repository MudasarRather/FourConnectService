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
from app.models.hr.attendance import Attendance
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
        }
        if blocker is None:
            built = build_payslip_for_employee(db, emp, year, month, cfg)
            if built:
                row["lop_days"] = built["lop_days"]
                row["paid_days"] = built["paid_days"]
                row["working_days"] = built["working_days"]
                row["est_gross"] = built["gross_earnings"]
                row["est_net"] = built["net_pay"]
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
    return {
        "period_month": month, "period_year": year, "department_id": department_id,
        "total_candidates": len(emps), "eligible_count": eligible,
        "blocked_count": len(emps) - eligible, "final_settlement_count": final_settlement,
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
    for r in rows:
        r.status = AdjustmentStatus.PAID
        r.paid_at = now
        r.batch_id = batch.id
        r.payroll_ref = batch.batch_no


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

    working = Decimal(str(days_in_month(year, month)))
    lop = month_lop_days(db, employee.id, year, month)
    start, end = month_bounds(year, month)
    # Mid-period JOINER: days before the joining date are unpaid (added to LOP so
    # earnings prorate down). Joining on the 1st ⇒ no effect.
    if employee.joining_date and start < employee.joining_date <= end:
        lop = lop + Decimal(str((employee.joining_date - start).days))
    # Mid-period LEAVER (final settlement): every calendar day after the last
    # working day is unpaid. The 2-pass gross derivation in compute_payslip still
    # runs on the full month, so the proration stays clean.
    if employee.lifecycle_state == LifecycleState.EXITED:
        lwd = employee.last_working_date or employee.exit_date
        if lwd and start <= lwd <= end:
            lop = lop + Decimal(str((end - lwd).days))
    encash = _approved_encashment(db, employee.id)
    adjustments = _pending_adjustments(db, employee.id, year, month)

    # Apply this employee's structure PF policy (cap at ceiling vs full Basic) for
    # this computation. cfg is shared across the batch, so set it per employee.
    cfg["PF_RESTRICT_TO_CEILING"] = pf_restrict_for(db, structure_id)
    result = compute_payslip(
        components=components, monthly_ctc=Decimal(str(monthly_ctc)),
        annual_ctc=Decimal(str(annual_ctc)), monthly_gross_hint=monthly_gross_hint,
        regime=regime, declarations=declarations, working_days=working, lop_days=lop,
        cfg=cfg, encashment_amount=encash, remaining_months=remaining_months_in_fy(month),
        adjustments=adjustments,
    )
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
