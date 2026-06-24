"""HR Payroll — Adjustments (bonus / incentive / variable pay / arrear / deduction)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.payroll_adjustment import PayrollAdjustment, AdjustmentType, AdjustmentStatus
from app.models.hr.payroll_config import PayrollAuditAction
from app.schemas.hr.payroll import (
    AdjustmentCreate, AdjustmentUpdate, AdjustmentResponse, AdjustmentListResponse,
    AdjustmentActionBody,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.payroll.service import write_audit
from app.utils.hr.lifecycle_guard import guard_settleable

router = APIRouter(prefix="/hr/payroll/adjustments", tags=["HR — Payroll Adjustments"])


def _enrich(a: PayrollAdjustment) -> dict:
    emp = a.employee
    name = None
    if emp and emp.user:
        name = getattr(emp.user, "full_name", None) or getattr(emp.user, "email", None)
    out = {k: getattr(a, k) for k in (
        "id", "employee_id", "adjustment_type", "sub_type", "title", "amount", "is_taxable",
        "is_deduction", "period_month", "period_year", "from_date", "to_date", "reason",
        "status", "payroll_ref", "paid_at", "created_at")}
    out["employee_name"] = name
    out["employee_code"] = emp.employee_id if emp else None
    return out


def _get(db, aid) -> PayrollAdjustment:
    a = db.query(PayrollAdjustment).options(joinedload(PayrollAdjustment.employee)).filter(
        PayrollAdjustment.id == aid, PayrollAdjustment.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Adjustment not found")
    return a


@router.get("/", response_model=AdjustmentListResponse)
def list_adjustments(adjustment_type: Optional[AdjustmentType] = None, status: Optional[AdjustmentStatus] = None,
                     employee_id: Optional[UUID] = None, year: Optional[int] = None, month: Optional[int] = None,
                     skip: int = 0, limit: int = Query(50, ge=1, le=200),
                     db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    q = db.query(PayrollAdjustment).options(joinedload(PayrollAdjustment.employee)).filter(
        PayrollAdjustment.is_deleted == False)  # noqa: E712
    if adjustment_type:
        q = q.filter(PayrollAdjustment.adjustment_type == adjustment_type)
    if status:
        q = q.filter(PayrollAdjustment.status == status)
    if employee_id:
        q = q.filter(PayrollAdjustment.employee_id == employee_id)
    if year:
        q = q.filter(PayrollAdjustment.period_year == year)
    if month:
        q = q.filter(PayrollAdjustment.period_month == month)
    total = q.count()
    rows = q.order_by(PayrollAdjustment.created_at.desc()).offset(skip).limit(limit).all()
    return {"items": [_enrich(a) for a in rows], "total": total}


@router.post("/", response_model=AdjustmentResponse, status_code=201)
def create_adjustment(payload: AdjustmentCreate, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_superuser)):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    guard_settleable(emp, "create a payroll adjustment for this employee")
    a = PayrollAdjustment(**payload.model_dump(), status=AdjustmentStatus.DRAFT, created_by_id=current_user.id)
    db.add(a)
    db.flush()
    write_audit(db, entity_type="ADJUSTMENT", entity_id=a.id, action=PayrollAuditAction.CREATE,
                actor_id=current_user.id, note=f"{a.adjustment_type.value}: {a.title}")
    db.commit()
    db.refresh(a)
    return _enrich(a)


@router.patch("/{adj_id}", response_model=AdjustmentResponse)
def update_adjustment(adj_id: UUID, payload: AdjustmentUpdate, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_superuser)):
    a = _get(db, adj_id)
    if a.status in (AdjustmentStatus.PAID,):
        raise HTTPException(409, "Paid adjustments cannot be edited")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    write_audit(db, entity_type="ADJUSTMENT", entity_id=a.id, action=PayrollAuditAction.UPDATE, actor_id=current_user.id)
    db.commit()
    db.refresh(a)
    return _enrich(a)


@router.post("/{adj_id}/approve", response_model=AdjustmentResponse)
def approve_adjustment(adj_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    a = _get(db, adj_id)
    if a.status != AdjustmentStatus.DRAFT:
        raise HTTPException(409, f"Cannot approve an adjustment in status {a.status.value}")
    a.status = AdjustmentStatus.APPROVED
    a.approved_by_id = current_user.id
    a.approved_at = datetime.now(timezone.utc)
    write_audit(db, entity_type="ADJUSTMENT", entity_id=a.id, action=PayrollAuditAction.APPROVE,
                actor_id=current_user.id, to_status="APPROVED")
    db.commit()
    db.refresh(a)
    return _enrich(a)


def _action_note(body: Optional[AdjustmentActionBody]) -> Optional[str]:
    """Fold an action body's reason + note into a single audit-log note string."""
    if not body:
        return None
    parts = [p for p in (body.reason, body.note) if p and p.strip()]
    return " — ".join(parts) or None


@router.post("/{adj_id}/cancel", response_model=AdjustmentResponse)
def cancel_adjustment(adj_id: UUID, body: Optional[AdjustmentActionBody] = None,
                      db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    a = _get(db, adj_id)
    if a.status == AdjustmentStatus.PAID:
        raise HTTPException(409, "Paid adjustments cannot be cancelled")
    if a.status == AdjustmentStatus.CANCELLED:
        raise HTTPException(409, "Adjustment is already cancelled")
    a.status = AdjustmentStatus.CANCELLED
    write_audit(db, entity_type="ADJUSTMENT", entity_id=a.id, action=PayrollAuditAction.CANCEL,
                actor_id=current_user.id, to_status="CANCELLED", note=_action_note(body))
    db.commit()
    db.refresh(a)
    return _enrich(a)


@router.delete("/{adj_id}", status_code=204)
def delete_adjustment(adj_id: UUID, body: Optional[AdjustmentActionBody] = None,
                      db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    a = _get(db, adj_id)
    if a.status == AdjustmentStatus.PAID:
        raise HTTPException(409, "Paid adjustments cannot be deleted")
    a.is_deleted = True
    write_audit(db, entity_type="ADJUSTMENT", entity_id=a.id, action=PayrollAuditAction.DELETE,
                actor_id=current_user.id, note=_action_note(body))
    db.commit()
    return
