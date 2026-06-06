"""HR Payroll — Statutory config + dashboard stats + global audit log."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee, LifecycleState
from app.models.hr.salary_structure import SalaryStructure
from app.models.hr.salary_component import SalaryComponent
from app.models.hr.employee_compensation import EmployeeCompensation, CompensationStatus
from app.models.hr.payroll_batch import PayrollBatch, PayrollBatchStatus
from app.models.hr.payroll_config import StatutoryConfig, PayrollAuditLog, PayrollAuditAction
from app.schemas.hr.payroll import (
    StatutoryConfigCreate, StatutoryConfigUpdate, StatutoryConfigResponse, StatutoryConfigListResponse,
    PayrollDashboardStats, PayrollAuditListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.payroll import fy_for
from app.utils.hr.payroll.service import write_audit

router = APIRouter(prefix="/hr/payroll", tags=["HR — Payroll Config"])

_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@router.get("/dashboard", response_model=PayrollDashboardStats)
def dashboard(db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    today = date.today()
    fy = fy_for(today)
    structures = db.query(SalaryStructure.id).filter(SalaryStructure.is_deleted == False).count()  # noqa: E712
    components = db.query(SalaryComponent.id).filter(SalaryComponent.is_deleted == False).count()  # noqa: E712
    active_comps = db.query(EmployeeCompensation.id).filter(
        EmployeeCompensation.status == CompensationStatus.ACTIVE,
        EmployeeCompensation.is_deleted == False).count()  # noqa: E712
    on_payroll = db.query(Employee.id).filter(
        Employee.is_deleted == False,  # noqa: E712
        Employee.lifecycle_state.in_([LifecycleState.ACTIVE, LifecycleState.ON_PROBATION, LifecycleState.ON_NOTICE]),
    ).count()

    # current-period figures (latest non-cancelled batch for this month)
    cur = db.query(PayrollBatch).filter(
        PayrollBatch.period_year == today.year, PayrollBatch.period_month == today.month,
        PayrollBatch.is_deleted == False, PayrollBatch.status != PayrollBatchStatus.CANCELLED,  # noqa: E712
    ).order_by(PayrollBatch.created_at.desc()).first()
    cur_gross = cur.total_gross if cur else Decimal(0)
    cur_net = cur.total_net if cur else Decimal(0)
    cur_ded = cur.total_deductions if cur else Decimal(0)
    cur_empr = cur.total_employer_cost if cur else Decimal(0)
    cur_head = cur.total_employees if cur else 0

    pending = db.query(PayrollBatch.id).filter(
        PayrollBatch.status == PayrollBatchStatus.VERIFIED, PayrollBatch.is_deleted == False).count()  # noqa: E712

    by_status = {}
    for st, cnt in db.query(PayrollBatch.status, sa_func.count(PayrollBatch.id)).filter(
            PayrollBatch.is_deleted == False).group_by(PayrollBatch.status).all():  # noqa: E712
        by_status[st.value] = cnt

    trend_rows = db.query(PayrollBatch).filter(
        PayrollBatch.is_deleted == False,  # noqa: E712
        PayrollBatch.status.in_([PayrollBatchStatus.RELEASED, PayrollBatchStatus.LOCKED,
                                 PayrollBatchStatus.APPROVED, PayrollBatchStatus.VERIFIED, PayrollBatchStatus.GENERATED]),
    ).order_by(PayrollBatch.period_year.desc(), PayrollBatch.period_month.desc()).limit(12).all()
    cost_trend = [{
        "label": f"{_MONTHS[b.period_month]} {str(b.period_year)[-2:]}",
        "gross": str(b.total_gross), "net": str(b.total_net), "deductions": str(b.total_deductions),
    } for b in reversed(trend_rows)]

    return PayrollDashboardStats(
        fiscal_year=fy, period_label=f"{_MONTHS[today.month]} {today.year}",
        structures_count=structures, components_count=components, active_compensations=active_comps,
        employees_on_payroll=on_payroll, current_gross=cur_gross, current_net=cur_net,
        current_deductions=cur_ded, current_employer_cost=cur_empr, current_headcount=cur_head,
        pending_approvals=pending, batches_by_status=by_status, cost_trend=cost_trend,
    )


@router.get("/config/statutory", response_model=StatutoryConfigListResponse)
def list_statutory(fiscal_year: Optional[str] = None, state_code: Optional[str] = None,
                   db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    q = db.query(StatutoryConfig)
    if fiscal_year:
        q = q.filter(StatutoryConfig.fiscal_year == fiscal_year)
    if state_code:
        q = q.filter(StatutoryConfig.state_code == state_code)
    rows = q.order_by(StatutoryConfig.fiscal_year.desc(), StatutoryConfig.key).all()
    return {"items": rows, "total": len(rows)}


@router.post("/config/statutory", response_model=StatutoryConfigResponse, status_code=201)
def create_statutory(payload: StatutoryConfigCreate, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_superuser)):
    dup = db.query(StatutoryConfig.id).filter(
        StatutoryConfig.fiscal_year == payload.fiscal_year,
        StatutoryConfig.state_code == payload.state_code, StatutoryConfig.key == payload.key).first()
    if dup:
        raise HTTPException(409, "A config row for this fiscal_year/state/key already exists")
    row = StatutoryConfig(**payload.model_dump(), created_by_id=current_user.id)
    db.add(row)
    db.flush()
    write_audit(db, entity_type="CONFIG", entity_id=row.id, action=PayrollAuditAction.CONFIG_CHANGE,
                actor_id=current_user.id, note=f"Created {payload.key}")
    db.commit()
    db.refresh(row)
    return row


@router.patch("/config/statutory/{config_id}", response_model=StatutoryConfigResponse)
def update_statutory(config_id: UUID, payload: StatutoryConfigUpdate, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_superuser)):
    row = db.query(StatutoryConfig).filter(StatutoryConfig.id == config_id).first()
    if not row:
        raise HTTPException(404, "Config row not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    row.last_updated_by_id = current_user.id
    write_audit(db, entity_type="CONFIG", entity_id=row.id, action=PayrollAuditAction.CONFIG_CHANGE,
                actor_id=current_user.id, note=f"Updated {row.key}")
    db.commit()
    db.refresh(row)
    return row


@router.get("/audit", response_model=PayrollAuditListResponse)
def global_audit(entity_type: Optional[str] = None, skip: int = 0, limit: int = Query(50, ge=1, le=200),
                 db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser)):
    q = db.query(PayrollAuditLog)
    if entity_type:
        q = q.filter(PayrollAuditLog.entity_type == entity_type)
    total = q.count()
    rows = q.order_by(PayrollAuditLog.created_at.desc()).offset(skip).limit(limit).all()
    return {"items": [{
        "id": r.id, "entity_type": r.entity_type, "entity_id": r.entity_id, "action": r.action,
        "batch_id": r.batch_id, "actor_id": r.actor_id, "from_status": r.from_status,
        "to_status": r.to_status, "note": r.note, "created_at": r.created_at, "actor_name": None,
    } for r in rows], "total": total}
