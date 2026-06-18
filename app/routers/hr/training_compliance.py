"""HR Training & Development — Compliance training config + status + reassign."""
from __future__ import annotations

from datetime import date
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.database import get_db
from app.models.user import User
from app.models.hr.training import TrainingProgram, TrainingAssignment, TrainingAssignmentStatus
from app.models.hr.compliance_training import ComplianceTraining, FREQUENCY_MONTHS
from app.schemas.hr.compliance_training import (
    ComplianceTrainingCreate, ComplianceTrainingUpdate, ComplianceTrainingResponse,
    ComplianceStatusRow, ComplianceReassignResult,
)
from app.models.hr.training_audit_log import TrainingAuditAction
from app.utils.hr.training.audit import write_training_audit
from app.utils.hr.training.flow import add_months
from app.utils.hr.training.service import emp_display, resolve_eligible_employee_ids
from app.utils.hr.training.compliance_engine import run_compliance_reassign
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/training", tags=["HR — Training Compliance"])


def _program(db: Session, pid):
    return db.query(TrainingProgram).filter(TrainingProgram.id == pid).first()


def _config_rollup(db: Session, cfg: ComplianceTraining) -> dict:
    """Lightweight eligible/compliant/overdue counts for one config."""
    emp_ids = resolve_eligible_employee_ids(db, cfg.applies_to)
    eligible = len(emp_ids)
    if not eligible:
        return {"eligible": 0, "compliant": 0, "overdue": 0, "rate": 0.0}
    today = date.today()
    months = FREQUENCY_MONTHS.get(cfg.frequency)
    compliant = 0
    for emp_id in emp_ids:
        last = db.query(TrainingAssignment.completion_date).filter(
            TrainingAssignment.employee_id == emp_id,
            TrainingAssignment.program_id == cfg.program_id,
            TrainingAssignment.status == TrainingAssignmentStatus.COMPLETED,
        ).order_by(TrainingAssignment.completion_date.desc()).first()
        lc = last[0] if last else None
        if lc is None:
            continue
        if months is None:           # ONE_TIME — completed once = compliant
            compliant += 1
        elif add_months(lc, months) >= today:
            compliant += 1
    overdue = eligible - compliant
    rate = round(compliant / eligible * 100, 1) if eligible else 0.0
    return {"eligible": eligible, "compliant": compliant, "overdue": overdue, "rate": rate}


def _resp(db: Session, cfg: ComplianceTraining, *, with_rollup: bool = True) -> ComplianceTrainingResponse:
    prog = _program(db, cfg.program_id)
    roll = _config_rollup(db, cfg) if with_rollup else {}
    return ComplianceTrainingResponse(
        id=cfg.id, program_id=cfg.program_id,
        program_name=prog.name if prog else None,
        program_type=prog.training_type.value if prog else None,
        frequency=cfg.frequency, validity_months=cfg.validity_months,
        grace_period_days=cfg.grace_period_days, applies_to=cfg.applies_to,
        auto_reassign=cfg.auto_reassign, due_days_after_assign=cfg.due_days_after_assign,
        is_active=cfg.is_active,
        eligible_count=roll.get("eligible"), compliant_count=roll.get("compliant"),
        overdue_count=roll.get("overdue"), completion_rate=roll.get("rate"),
        created_at=cfg.created_at,
    )


@router.get("/compliance", response_model=List[ComplianceTrainingResponse])
def list_compliance(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = db.query(ComplianceTraining).filter(ComplianceTraining.is_deleted == False).all()  # noqa: E712
    return [_resp(db, c) for c in rows]


@router.post("/compliance", response_model=ComplianceTrainingResponse, status_code=http_status.HTTP_201_CREATED)
def create_compliance(
    payload: ComplianceTrainingCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    prog = _program(db, payload.program_id)
    if not prog or prog.is_deleted:
        raise HTTPException(404, "Program not found")
    if db.query(ComplianceTraining.id).filter(
        ComplianceTraining.program_id == payload.program_id,
        ComplianceTraining.is_deleted == False,  # noqa: E712
    ).first():
        raise HTTPException(400, "This program already has a compliance configuration")
    cfg = ComplianceTraining(**payload.model_dump(), created_by_id=admin.id)
    db.add(cfg)
    prog.is_compliance = True
    db.flush()
    write_training_audit(db, entity_type="COMPLIANCE", entity_id=cfg.id,
                         action=TrainingAuditAction.CREATE, actor_id=admin.id, note=prog.name)
    db.commit()
    db.refresh(cfg)
    return _resp(db, cfg)


@router.patch("/compliance/{config_id}", response_model=ComplianceTrainingResponse)
def update_compliance(
    config_id: UUID,
    payload: ComplianceTrainingUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    cfg = db.query(ComplianceTraining).filter(ComplianceTraining.id == config_id, ComplianceTraining.is_deleted == False).first()  # noqa: E712
    if not cfg:
        raise HTTPException(404, "Compliance config not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(cfg, k, v)
    if "applies_to" in data:
        flag_modified(cfg, "applies_to")  # JSONB columns are not mutable-wrapped
    write_training_audit(db, entity_type="COMPLIANCE", entity_id=cfg.id,
                         action=TrainingAuditAction.UPDATE, actor_id=admin.id)
    db.commit()
    db.refresh(cfg)
    return _resp(db, cfg)


@router.delete("/compliance/{config_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_compliance(
    config_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    cfg = db.query(ComplianceTraining).filter(ComplianceTraining.id == config_id, ComplianceTraining.is_deleted == False).first()  # noqa: E712
    if not cfg:
        raise HTTPException(404, "Compliance config not found")
    cfg.is_deleted = True
    prog = _program(db, cfg.program_id)
    if prog:
        prog.is_compliance = False
    write_training_audit(db, entity_type="COMPLIANCE", entity_id=cfg.id,
                         action=TrainingAuditAction.DELETE, actor_id=admin.id)
    db.commit()


@router.get("/compliance/status", response_model=List[ComplianceStatusRow])
def compliance_status(
    config_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    today = date.today()
    configs = db.query(ComplianceTraining).filter(ComplianceTraining.is_deleted == False)  # noqa: E712
    if config_id:
        configs = configs.filter(ComplianceTraining.id == config_id)
    out: List[ComplianceStatusRow] = []
    for cfg in configs.all():
        prog = _program(db, cfg.program_id)
        months = FREQUENCY_MONTHS.get(cfg.frequency)
        for emp_id in resolve_eligible_employee_ids(db, cfg.applies_to):
            last = db.query(TrainingAssignment.completion_date).filter(
                TrainingAssignment.employee_id == emp_id,
                TrainingAssignment.program_id == cfg.program_id,
                TrainingAssignment.status == TrainingAssignmentStatus.COMPLETED,
            ).order_by(TrainingAssignment.completion_date.desc()).first()
            lc = last[0] if last else None
            valid_until = add_months(lc, months) if (lc and months) else None
            if lc is None:
                state = "NEVER"
            elif months is None:
                state = "COMPLIANT"
            elif valid_until and valid_until >= today:
                state = "COMPLIANT" if (valid_until - today).days > 30 else "DUE"
            else:
                state = "OVERDUE"
            disp = emp_display(db, emp_id)
            out.append(ComplianceStatusRow(
                employee_id=emp_id, employee_name=disp.get("name"),
                employee_code=disp.get("code"), department_name=disp.get("dept"),
                program_id=cfg.program_id, program_name=prog.name if prog else None,
                last_completed=lc, valid_until=valid_until, state=state,
            ))
    return out


@router.post("/compliance/{config_id}/run-reassign", response_model=ComplianceReassignResult)
def run_reassign(
    config_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    cfg = db.query(ComplianceTraining).filter(ComplianceTraining.id == config_id, ComplianceTraining.is_deleted == False).first()  # noqa: E712
    if not cfg:
        raise HTTPException(404, "Compliance config not found")
    # Manual trigger: force the sweep even for Manual rules (daemon stays opt-in).
    result = run_compliance_reassign(db, only_config_id=config_id, actor_id=admin.id, force=True)
    return ComplianceReassignResult(**result)
