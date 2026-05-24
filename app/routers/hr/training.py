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
    return [
        TrainingProgramResponse(
            id=p.id, name=p.name, code=p.code, training_type=p.training_type,
            description=p.description, duration_hours=p.duration_hours,
            trainer_user_id=p.trainer_user_id, trainer_name=_user_name(db, p.trainer_user_id),
            certification_required=p.certification_required,
            is_mandatory_for_new_joiners=p.is_mandatory_for_new_joiners,
            materials_url=p.materials_url, is_active=p.is_active, created_at=p.created_at,
        ) for p in rows
    ]


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
    db.commit()
    db.refresh(p)
    return TrainingProgramResponse(
        id=p.id, name=p.name, code=p.code, training_type=p.training_type,
        description=p.description, duration_hours=p.duration_hours,
        trainer_user_id=p.trainer_user_id, trainer_name=_user_name(db, p.trainer_user_id),
        certification_required=p.certification_required,
        is_mandatory_for_new_joiners=p.is_mandatory_for_new_joiners,
        materials_url=p.materials_url, is_active=p.is_active, created_at=p.created_at,
    )


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
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return TrainingProgramResponse(
        id=p.id, name=p.name, code=p.code, training_type=p.training_type,
        description=p.description, duration_hours=p.duration_hours,
        trainer_user_id=p.trainer_user_id, trainer_name=_user_name(db, p.trainer_user_id),
        certification_required=p.certification_required,
        is_mandatory_for_new_joiners=p.is_mandatory_for_new_joiners,
        materials_url=p.materials_url, is_active=p.is_active, created_at=p.created_at,
    )


@router.delete("/programs/{program_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_program(
    program_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    p = db.query(TrainingProgram).filter(TrainingProgram.id == program_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    p.is_deleted = True
    db.commit()


# ───────────────────────────── Assignments ─────────────────────────────

@router.get("/assignments", response_model=List[TrainingAssignmentResponse])
def list_assignments(
    employee_id: Optional[UUID] = None,
    process_id: Optional[UUID] = None,
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
    if not db.query(Employee).filter(Employee.id == payload.employee_id).first():
        raise HTTPException(404, "Employee not found")
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
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    a = db.query(TrainingAssignment).filter(TrainingAssignment.id == assignment_id).first()
    if not a:
        raise HTTPException(404, "Not found")
    db.delete(a)
    db.commit()
