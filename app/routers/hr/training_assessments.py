"""HR Training & Development — Assessments + results (grading engine).

Recording a passing result drives the linked enrollment to COMPLETED through the
single ``complete_assignment`` path (cert minting + onboarding recalc included).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.training import TrainingProgram, TrainingAssignment, TrainingAssignmentStatus
from app.models.hr.assessment import Assessment, AssessmentResult
from app.schemas.hr.assessment import (
    AssessmentCreate, AssessmentUpdate, AssessmentResponse,
    AssessmentResultCreate, AssessmentResultResponse,
)
from app.models.hr.training_audit_log import TrainingAuditAction
from app.utils.hr.training.audit import write_training_audit
from app.utils.hr.training.flow import complete_assignment
from app.utils.hr.training.service import emp_display
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/training", tags=["HR — Training Assessments"])


def _program_name(db: Session, pid) -> Optional[str]:
    r = db.query(TrainingProgram.name).filter(TrainingProgram.id == pid).first()
    return r[0] if r else None


def _assessment_resp(db: Session, a: Assessment) -> AssessmentResponse:
    rc = db.query(func.count(AssessmentResult.id)).filter(AssessmentResult.assessment_id == a.id).scalar() or 0
    pc = db.query(func.count(AssessmentResult.id)).filter(
        AssessmentResult.assessment_id == a.id, AssessmentResult.passed == True,  # noqa: E712
    ).scalar() or 0
    return AssessmentResponse(
        id=a.id, program_id=a.program_id, program_name=_program_name(db, a.program_id),
        title=a.title, assessment_type=a.assessment_type, pass_score=a.pass_score,
        max_score=a.max_score, max_attempts=a.max_attempts, duration_minutes=a.duration_minutes,
        is_active=a.is_active, result_count=int(rc), pass_count=int(pc), created_at=a.created_at,
    )


@router.get("/assessments", response_model=List[AssessmentResponse])
def list_assessments(
    program_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(Assessment).filter(Assessment.is_deleted == False)  # noqa: E712
    if program_id:
        q = q.filter(Assessment.program_id == program_id)
    rows = q.order_by(Assessment.created_at.desc()).all()
    return [_assessment_resp(db, a) for a in rows]


@router.post("/assessments", response_model=AssessmentResponse, status_code=http_status.HTTP_201_CREATED)
def create_assessment(
    payload: AssessmentCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if not db.query(TrainingProgram.id).filter(TrainingProgram.id == payload.program_id, TrainingProgram.is_deleted == False).first():  # noqa: E712
        raise HTTPException(404, "Program not found")
    a = Assessment(**payload.model_dump(), created_by_id=admin.id)
    db.add(a)
    db.flush()
    write_training_audit(db, entity_type="ASSESSMENT", entity_id=a.id,
                         action=TrainingAuditAction.CREATE, actor_id=admin.id, note=a.title)
    db.commit()
    db.refresh(a)
    return _assessment_resp(db, a)


@router.patch("/assessments/{assessment_id}", response_model=AssessmentResponse)
def update_assessment(
    assessment_id: UUID,
    payload: AssessmentUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    a = db.query(Assessment).filter(Assessment.id == assessment_id, Assessment.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Assessment not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    write_training_audit(db, entity_type="ASSESSMENT", entity_id=a.id,
                         action=TrainingAuditAction.UPDATE, actor_id=admin.id)
    db.commit()
    db.refresh(a)
    return _assessment_resp(db, a)


@router.delete("/assessments/{assessment_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_assessment(
    assessment_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    a = db.query(Assessment).filter(Assessment.id == assessment_id, Assessment.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Assessment not found")
    a.is_deleted = True
    write_training_audit(db, entity_type="ASSESSMENT", entity_id=a.id,
                         action=TrainingAuditAction.DELETE, actor_id=admin.id)
    db.commit()


# ─────────────────────────── results ───────────────────────────

def _result_resp(db: Session, r: AssessmentResult) -> AssessmentResultResponse:
    at = db.query(Assessment.title).filter(Assessment.id == r.assessment_id).first()
    disp = emp_display(db, r.employee_id)
    return AssessmentResultResponse(
        id=r.id, assessment_id=r.assessment_id, assessment_title=at[0] if at else None,
        employee_id=r.employee_id, employee_name=disp.get("name"), assignment_id=r.assignment_id,
        attempt_number=r.attempt_number, score=r.score, passed=r.passed,
        submitted_at=r.submitted_at, created_at=r.created_at,
    )


@router.get("/assessment-results", response_model=List[AssessmentResultResponse])
def list_results(
    assessment_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
    assignment_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AssessmentResult)
    if assessment_id:
        q = q.filter(AssessmentResult.assessment_id == assessment_id)
    if employee_id:
        q = q.filter(AssessmentResult.employee_id == employee_id)
    if assignment_id:
        q = q.filter(AssessmentResult.assignment_id == assignment_id)
    rows = q.order_by(AssessmentResult.created_at.desc()).limit(500).all()
    return [_result_resp(db, r) for r in rows]


@router.post("/assessment-results", response_model=AssessmentResultResponse, status_code=http_status.HTTP_201_CREATED)
def record_result(
    payload: AssessmentResultCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    assessment = db.query(Assessment).filter(Assessment.id == payload.assessment_id, Assessment.is_deleted == False).first()  # noqa: E712
    if not assessment:
        raise HTTPException(404, "Assessment not found")
    if not db.query(Employee.id).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first():  # noqa: E712
        raise HTTPException(404, "Employee not found")

    # attempt number: next after the employee's latest attempt
    prior = db.query(func.max(AssessmentResult.attempt_number)).filter(
        AssessmentResult.assessment_id == payload.assessment_id,
        AssessmentResult.employee_id == payload.employee_id,
    ).scalar() or 0
    attempt = payload.attempt_number or (prior + 1)
    if assessment.max_attempts is not None and attempt > assessment.max_attempts:
        raise HTTPException(409, f"Maximum attempts ({assessment.max_attempts}) exceeded")

    passed = Decimal(str(payload.score)) >= Decimal(str(assessment.pass_score))
    r = AssessmentResult(
        assessment_id=payload.assessment_id, employee_id=payload.employee_id,
        assignment_id=payload.assignment_id, attempt_number=attempt,
        score=payload.score, passed=passed, answers=payload.answers,
        submitted_at=datetime.now(timezone.utc), graded_by_id=admin.id,
    )
    db.add(r)
    db.flush()
    write_training_audit(db, entity_type="ASSESSMENT", entity_id=assessment.id,
                         action=TrainingAuditAction.UPDATE, actor_id=admin.id,
                         note=f"Result recorded: {payload.score} ({'pass' if passed else 'fail'})")

    # Drive the linked enrollment through the single completion path on a pass;
    # on a fail with attempts exhausted, mark it FAILED.
    if payload.assignment_id:
        a = db.query(TrainingAssignment).filter(TrainingAssignment.id == payload.assignment_id).first()
        if a:
            if passed:
                complete_assignment(db, a, actor_id=admin.id, score=payload.score)
            elif assessment.max_attempts is not None and attempt >= assessment.max_attempts:
                if a.status not in (TrainingAssignmentStatus.COMPLETED,):
                    prev = a.status
                    a.status = TrainingAssignmentStatus.FAILED
                    a.score = payload.score
                    write_training_audit(db, entity_type="ASSIGNMENT", entity_id=a.id,
                                         action=TrainingAuditAction.FAIL, actor_id=admin.id,
                                         from_status=prev.value if prev else None,
                                         to_status=TrainingAssignmentStatus.FAILED.value)
    db.commit()
    db.refresh(r)
    return _result_resp(db, r)
