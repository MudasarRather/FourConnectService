"""HR Payroll — Employee Compensation (effective-dated CTC history)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.salary_structure import SalaryStructure
from app.models.hr.employee_compensation import EmployeeCompensation, CompensationStatus
from app.models.hr.payroll_config import PayrollAuditAction
from app.schemas.hr.payroll import (
    CompensationCreate, CompensationUpdate, CompensationResponse, CompensationListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.payroll import resolve_structure, compute_payslip, load_config, fy_for
from app.utils.hr.payroll.service import write_audit, create_compensation_revision
from app.utils.hr.lifecycle_guard import guard_employable

router = APIRouter(prefix="/hr/payroll/compensation", tags=["HR — Payroll Compensation"])


def _structure_for(db, employee, structure_id):
    sid = structure_id or employee.salary_structure_id
    if not sid:
        default = db.query(SalaryStructure).filter(
            SalaryStructure.is_default == True, SalaryStructure.is_deleted == False).first()  # noqa: E712
        sid = default.id if default else None
    return sid


def _compute_breakdown(db, employee, structure_id, monthly_ctc, annual_ctc, regime):
    """Run the engine for a full month to capture gross/basic/breakdown snapshot."""
    components = resolve_structure(db, structure_id) if structure_id else []
    if not components:
        return None, None, None
    cfg = load_config(db, fy_for(date.today()), None)
    result = compute_payslip(
        components=components, monthly_ctc=Decimal(str(monthly_ctc)),
        annual_ctc=Decimal(str(annual_ctc)), monthly_gross_hint=None,
        regime=regime, declarations=None, working_days=Decimal("30"), lop_days=Decimal("0"), cfg=cfg,
    )
    breakdown = {l["component_code"]: str(l["amount"]) for l in result["lines"]}
    basic = next((l["amount"] for l in result["lines"] if l["component_code"] == "BASIC"), None)
    return result["gross_earnings"], basic, breakdown


def _enrich(c: EmployeeCompensation, db: Session) -> dict:
    sname = None
    if c.structure_id:
        s = db.query(SalaryStructure.name).filter(SalaryStructure.id == c.structure_id).first()
        sname = s[0] if s else None
    return {**{k: getattr(c, k) for k in (
        "id", "employee_id", "structure_id", "effective_from", "effective_to",
        "annual_ctc", "monthly_ctc", "monthly_gross", "basic_amount", "breakdown",
        "tax_regime", "revision_reason", "revision_ref", "status", "created_at")},
        "structure_name": sname}


@router.get("/revisions", response_model=CompensationListResponse)
def list_revisions(limit: int = 100, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_superuser)):
    """Org-wide recent compensation revisions (increments / promotions / changes)."""
    rows = db.query(EmployeeCompensation).filter(
        EmployeeCompensation.is_deleted == False,  # noqa: E712
    ).order_by(EmployeeCompensation.created_at.desc()).limit(min(limit, 300)).all()
    out = []
    for c in rows:
        d = _enrich(c, db)
        emp = db.query(Employee).options().filter(Employee.id == c.employee_id).first()
        d["employee_name"] = (emp.user.full_name if emp and emp.user and getattr(emp.user, "full_name", None)
                              else (emp.employee_id if emp else None))
        out.append(d)
    return {"items": out, "total": len(out)}


@router.get("/employee/{employee_id}", response_model=CompensationListResponse)
def list_history(employee_id: UUID, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_superuser)):
    rows = db.query(EmployeeCompensation).filter(
        EmployeeCompensation.employee_id == employee_id,
        EmployeeCompensation.is_deleted == False,  # noqa: E712
    ).order_by(EmployeeCompensation.effective_from.desc()).all()
    return {"items": [_enrich(c, db) for c in rows], "total": len(rows)}


@router.get("/employee/{employee_id}/current", response_model=Optional[CompensationResponse])
def get_current(employee_id: UUID, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_superuser)):
    """The employee's authoritative current compensation — the single ACTIVE
    revision. This is the same row whose CTC/structure is mirrored onto the
    Employee record and shown as the profile's CTC, so the profile's
    salary-structure breakdown must come from it too.

    We deliberately do NOT gate on ``effective_from <= today``: an ACTIVE raise
    that was activated with a future effective date is still the authoritative
    "current" CTC (it's already mirrored to the Employee), so excluding it left
    the profile with no compensation and it fell back to the generic indicative
    estimate — making the breakdown disagree with both the assigned structure
    and the CTC shown right above it. Per-period payroll math uses the separate,
    window-based resolve_compensation(); this endpoint is purely the "current
    salary on file" view. Falls back to the most recent non-draft row when no
    ACTIVE row exists (e.g. an exited employee with only superseded history) so
    we still render the exact structure breakdown rather than a generic guess.
    """
    base = db.query(EmployeeCompensation).filter(
        EmployeeCompensation.employee_id == employee_id,
        EmployeeCompensation.is_deleted == False,  # noqa: E712
    )
    c = (base.filter(EmployeeCompensation.status == CompensationStatus.ACTIVE)
         .order_by(EmployeeCompensation.effective_from.desc()).first())
    if not c:
        c = (base.filter(EmployeeCompensation.status != CompensationStatus.DRAFT)
             .order_by(EmployeeCompensation.effective_from.desc()).first())
    return _enrich(c, db) if c else None


@router.post("/employee/{employee_id}", response_model=CompensationResponse, status_code=201)
def create_revision(employee_id: UUID, payload: CompensationCreate, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_superuser)):
    emp = db.query(Employee).filter(Employee.id == employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    # Compensation is frozen once an employee is leaving / gone — no new revisions
    # for ON_NOTICE / EXITED / SUSPENDED / ARCHIVED. Their existing comp drives the
    # final payroll + F&F; to revise pay you must cancel notice (→ ACTIVE) first.
    guard_employable(emp, "revise compensation for this employee")
    comp = create_compensation_revision(
        db, emp,
        annual_ctc=payload.annual_ctc, effective_from=payload.effective_from,
        structure_id=payload.structure_id, monthly_ctc=payload.monthly_ctc,
        tax_regime=payload.tax_regime, revision_reason=payload.revision_reason,
        revision_ref=payload.revision_ref, tds_declarations=payload.tds_declarations,
        activate=payload.activate, actor_id=current_user.id,
    )
    db.commit()
    db.refresh(comp)
    return _enrich(comp, db)


@router.patch("/{comp_id}", response_model=CompensationResponse)
def update_compensation(comp_id: UUID, payload: CompensationUpdate, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_superuser)):
    c = db.query(EmployeeCompensation).filter(EmployeeCompensation.id == comp_id,
                                              EmployeeCompensation.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Compensation not found")
    if c.status != CompensationStatus.DRAFT:
        raise HTTPException(409, "Only DRAFT compensation can be edited; create a new revision instead")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    if not c.monthly_ctc and c.annual_ctc:
        c.monthly_ctc = c.annual_ctc / 12
    c.last_updated_by_id = current_user.id
    write_audit(db, entity_type="COMPENSATION", entity_id=c.id, action=PayrollAuditAction.UPDATE,
                actor_id=current_user.id)
    db.commit()
    db.refresh(c)
    return _enrich(c, db)


@router.post("/{comp_id}/activate", response_model=CompensationResponse)
def activate_compensation(comp_id: UUID, db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_superuser)):
    c = db.query(EmployeeCompensation).filter(EmployeeCompensation.id == comp_id,
                                              EmployeeCompensation.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Compensation not found")
    if c.status not in (CompensationStatus.DRAFT,):
        raise HTTPException(409, "Only DRAFT compensation can be activated")
    # Don't activate a (possibly pre-existing) draft for someone now leaving / gone.
    emp_guard = db.query(Employee).filter(Employee.id == c.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    guard_employable(emp_guard, "activate a compensation revision for this employee")
    prior = db.query(EmployeeCompensation).filter(
        EmployeeCompensation.employee_id == c.employee_id, EmployeeCompensation.id != c.id,
        EmployeeCompensation.is_deleted == False,  # noqa: E712
        EmployeeCompensation.status == CompensationStatus.ACTIVE,
    ).all()
    for p in prior:
        p.status = CompensationStatus.SUPERSEDED
        if not p.effective_to or p.effective_to >= c.effective_from:
            p.effective_to = c.effective_from - timedelta(days=1)
    c.status = CompensationStatus.ACTIVE
    # Mirror the activated CTC onto the Employee record (HR profile source fields).
    emp = db.query(Employee).filter(Employee.id == c.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if emp:
        emp.annual_ctc = c.annual_ctc
        emp.monthly_ctc = c.monthly_ctc
        if c.tax_regime:
            emp.tax_regime = c.tax_regime
        if c.structure_id:
            emp.salary_structure_id = c.structure_id
    write_audit(db, entity_type="COMPENSATION", entity_id=c.id, action=PayrollAuditAction.UPDATE,
                actor_id=current_user.id, to_status="ACTIVE")
    db.commit()
    db.refresh(c)
    return _enrich(c, db)


@router.delete("/{comp_id}", status_code=204)
def delete_compensation(comp_id: UUID, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_superuser)):
    c = db.query(EmployeeCompensation).filter(EmployeeCompensation.id == comp_id,
                                              EmployeeCompensation.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Compensation not found")
    if c.status == CompensationStatus.ACTIVE:
        raise HTTPException(409, "Active compensation cannot be deleted; supersede it with a new revision")
    c.is_deleted = True
    write_audit(db, entity_type="COMPENSATION", entity_id=c.id, action=PayrollAuditAction.DELETE,
                actor_id=current_user.id)
    db.commit()
    return
