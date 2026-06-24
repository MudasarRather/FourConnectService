"""HR Training — programs + per-employee assignments."""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.onboarding import OnboardingProcess
from app.models.hr.training import (
    TrainingProgram, TrainingAssignment, TrainingType, TrainingAssignmentStatus,
)
from app.schemas.hr.training import (
    TrainingProgramCreate, TrainingProgramUpdate, TrainingProgramResponse,
    TrainingAssignmentCreate, TrainingAssignmentUpdate, TrainingAssignmentResponse,
)
from app.models.hr.training_audit_log import TrainingAuditAction
from app.utils.hr.training.audit import write_training_audit
from app.utils.hr.lifecycle_guard import guard_employable
from app.utils.dependencies import get_current_superuser


router = APIRouter(prefix="/hr/training", tags=["HR — Training"])


def _user_name(db: Session, uid: Optional[UUID]) -> Optional[str]:
    if not uid:
        return None
    r = db.query(User.full_name).filter(User.id == uid).first()
    return r[0] if r else None


def _emp_name(db: Session, eid: Optional[UUID]) -> Optional[str]:
    if not eid:
        return None
    r = (
        db.query(User.full_name)
        .join(Employee, Employee.user_id == User.id)
        .filter(Employee.id == eid)
        .first()
    )
    return r[0] if r else None


def _program_response(db: Session, p: TrainingProgram, enrollment_count: int = 0) -> TrainingProgramResponse:
    """Single source of truth for the outbound program shape."""
    return TrainingProgramResponse(
        id=p.id, name=p.name, code=p.code, training_type=p.training_type,
        description=p.description, duration_hours=p.duration_hours,
        trainer_user_id=p.trainer_user_id, trainer_name=_user_name(db, p.trainer_user_id),
        certification_required=p.certification_required,
        is_mandatory_for_new_joiners=p.is_mandatory_for_new_joiners,
        materials_url=p.materials_url, delivery_mode=p.delivery_mode,
        is_compliance=bool(p.is_compliance), is_active=p.is_active,
        created_at=p.created_at, enrollment_count=enrollment_count,
    )


def _enrollment_count(db: Session, program_id: UUID) -> int:
    return (
        db.query(func.count(TrainingAssignment.id))
        .filter(TrainingAssignment.program_id == program_id)
        .scalar()
    ) or 0


# In-flight states. Only these block archiving a program — COMPLETED / FAILED /
# WAIVED are terminal *history* that must be preserved, so they never block.
_ACTIVE_ASSIGNMENT_STATUSES = (
    TrainingAssignmentStatus.NOT_STARTED,
    TrainingAssignmentStatus.IN_PROGRESS,
)


def _active_enrollment_count(db: Session, program_id: UUID) -> int:
    return (
        db.query(func.count(TrainingAssignment.id))
        .filter(
            TrainingAssignment.program_id == program_id,
            TrainingAssignment.status.in_(_ACTIVE_ASSIGNMENT_STATUSES),
        )
        .scalar()
    ) or 0


@router.get("/programs", response_model=List[TrainingProgramResponse])
def list_programs(
    training_type: Optional[TrainingType] = None,
    mandatory_only: bool = False,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(TrainingProgram).filter(TrainingProgram.is_deleted == False)  # noqa: E712
    if training_type:
        q = q.filter(TrainingProgram.training_type == training_type)
    if mandatory_only:
        q = q.filter(TrainingProgram.is_mandatory_for_new_joiners == True)  # noqa: E712
    rows = q.order_by(TrainingProgram.created_at.desc()).all()
    # One grouped count query for all programs — avoids an N+1 per card.
    counts = dict(
        db.query(TrainingAssignment.program_id, func.count(TrainingAssignment.id))
        .group_by(TrainingAssignment.program_id)
        .all()
    )
    return [_program_response(db, p, counts.get(p.id, 0)) for p in rows]


@router.post("/programs", response_model=TrainingProgramResponse, status_code=http_status.HTTP_201_CREATED)
def create_program(
    payload: TrainingProgramCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if db.query(TrainingProgram).filter(TrainingProgram.name == payload.name).first():
        raise HTTPException(400, "Program name already exists")
    p = TrainingProgram(**payload.model_dump(), created_by_id=admin.id)
    db.add(p)
    db.flush()
    write_training_audit(
        db, entity_type="PROGRAM", entity_id=p.id, action=TrainingAuditAction.CREATE,
        actor_id=admin.id, note=f"Created program “{p.name}”",
        payload={"training_type": str(p.training_type), "code": p.code},
    )
    db.commit()
    db.refresh(p)
    return _program_response(db, p, 0)


@router.patch("/programs/{program_id}", response_model=TrainingProgramResponse)
def update_program(
    program_id: UUID,
    payload: TrainingProgramUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    p = db.query(TrainingProgram).filter(TrainingProgram.id == program_id, TrainingProgram.is_deleted == False).first()  # noqa: E712
    if not p:
        raise HTTPException(404, "Program not found")
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(p, k, v)
    write_training_audit(
        db, entity_type="PROGRAM", entity_id=p.id, action=TrainingAuditAction.UPDATE,
        actor_id=_admin.id, note=f"Updated program “{p.name}”",
        payload={"fields": sorted(changes.keys())},
    )
    db.commit()
    db.refresh(p)
    return _program_response(db, p, _enrollment_count(db, p.id))


@router.delete("/programs/{program_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_program(
    program_id: UUID,
    reason: Optional[str] = Query(None, max_length=60),
    note: Optional[str] = Query(None, max_length=240),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    p = db.query(TrainingProgram).filter(
        TrainingProgram.id == program_id, TrainingProgram.is_deleted == False,  # noqa: E712
    ).first()
    if not p:
        raise HTTPException(404, "Not found")
    active = _active_enrollment_count(db, program_id)
    if active:
        raise HTTPException(
            409,
            f"Cannot archive — {active} learner(s) still in progress. "
            "Complete, waive, or un-enrol them first. Finished records are kept.",
        )
    p.is_deleted = True
    audit_note = f"Archived program “{p.name}”"
    if reason:
        audit_note += f" · {reason}"
    write_training_audit(
        db, entity_type="PROGRAM", entity_id=p.id, action=TrainingAuditAction.DELETE,
        actor_id=_admin.id, note=audit_note[:300],
        payload={"reason": reason, "note": note, "name": p.name},
    )
    db.commit()


# ───────────────────────────── Assignments ─────────────────────────────

@router.get("/assignments", response_model=List[TrainingAssignmentResponse])
def list_assignments(
    employee_id: Optional[UUID] = None,
    process_id: Optional[UUID] = None,
    program_id: Optional[UUID] = None,
    assignment_status: Optional[TrainingAssignmentStatus] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(TrainingAssignment, TrainingProgram).join(
        TrainingProgram, TrainingProgram.id == TrainingAssignment.program_id
    )
    if employee_id:
        q = q.filter(TrainingAssignment.employee_id == employee_id)
    if process_id:
        q = q.filter(TrainingAssignment.process_id == process_id)
    if program_id:
        q = q.filter(TrainingAssignment.program_id == program_id)
    if assignment_status:
        q = q.filter(TrainingAssignment.status == assignment_status)
    rows = q.order_by(TrainingAssignment.created_at.desc()).limit(500).all()
    out: List[TrainingAssignmentResponse] = []
    for a, p in rows:
        out.append(TrainingAssignmentResponse(
            id=a.id, program_id=a.program_id,
            program_name=p.name if p else None,
            program_type=p.training_type if p else None,
            employee_id=a.employee_id, employee_name=_emp_name(db, a.employee_id),
            process_id=a.process_id, assigned_date=a.assigned_date,
            due_date=a.due_date, completion_date=a.completion_date,
            status=a.status, score=a.score, certification_url=a.certification_url, notes=a.notes,
        ))
    return out


@router.post("/assignments", response_model=TrainingAssignmentResponse, status_code=http_status.HTTP_201_CREATED)
def create_assignment(
    payload: TrainingAssignmentCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    if not db.query(TrainingProgram).filter(TrainingProgram.id == payload.program_id).first():
        raise HTTPException(404, "Program not found")
    emp = db.query(Employee).filter(Employee.id == payload.employee_id).first()
    guard_employable(emp, "assign training to this employee")
    a = TrainingAssignment(**payload.model_dump())
    db.add(a)
    db.commit()
    db.refresh(a)
    p = db.query(TrainingProgram).filter(TrainingProgram.id == a.program_id).first()
    return TrainingAssignmentResponse(
        id=a.id, program_id=a.program_id,
        program_name=p.name if p else None,
        program_type=p.training_type if p else None,
        employee_id=a.employee_id, employee_name=_emp_name(db, a.employee_id),
        process_id=a.process_id, assigned_date=a.assigned_date,
        due_date=a.due_date, completion_date=a.completion_date,
        status=a.status, score=a.score, certification_url=a.certification_url, notes=a.notes,
    )


@router.patch("/assignments/{assignment_id}", response_model=TrainingAssignmentResponse)
def update_assignment(
    assignment_id: UUID,
    payload: TrainingAssignmentUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    a = db.query(TrainingAssignment).filter(TrainingAssignment.id == assignment_id).first()
    if not a:
        raise HTTPException(404, "Assignment not found")
    prev_status = a.status
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    if payload.status == TrainingAssignmentStatus.COMPLETED and prev_status != TrainingAssignmentStatus.COMPLETED:
        a.completion_date = a.completion_date or date.today()
        from app.routers.hr.onboarding import _recalculate_progress
        proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == a.process_id).first()
        if proc:
            _recalculate_progress(db, proc)
    db.commit()
    db.refresh(a)
    p = db.query(TrainingProgram).filter(TrainingProgram.id == a.program_id).first()
    return TrainingAssignmentResponse(
        id=a.id, program_id=a.program_id,
        program_name=p.name if p else None,
        program_type=p.training_type if p else None,
        employee_id=a.employee_id, employee_name=_emp_name(db, a.employee_id),
        process_id=a.process_id, assigned_date=a.assigned_date,
        due_date=a.due_date, completion_date=a.completion_date,
        status=a.status, score=a.score, certification_url=a.certification_url, notes=a.notes,
    )


@router.delete("/assignments/{assignment_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_assignment(
    assignment_id: UUID,
    reason: Optional[str] = Query(None, max_length=60),
    note: Optional[str] = Query(None, max_length=240),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    a = db.query(TrainingAssignment).filter(TrainingAssignment.id == assignment_id).first()
    if not a:
        raise HTTPException(404, "Not found")
    p = db.query(TrainingProgram).filter(TrainingProgram.id == a.program_id).first()
    emp = _emp_name(db, a.employee_id)
    audit_note = f"Un-enrolled {emp or 'employee'} from “{p.name if p else 'program'}”"
    if reason:
        audit_note += f" · {reason}"
    write_training_audit(
        db, entity_type="ASSIGNMENT", entity_id=a.id, action=TrainingAuditAction.DELETE,
        actor_id=_admin.id, from_status=str(a.status), note=audit_note[:300],
        payload={"reason": reason, "note": note, "program": p.name if p else None, "employee": emp},
    )
    db.delete(a)
    db.commit()
