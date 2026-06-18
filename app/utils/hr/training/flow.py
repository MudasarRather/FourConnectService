"""HR Training & Development — enrollment (assignment) state machine + the single
completion side-effect, plus the skill-gap recompute.

``complete_assignment`` is the ONE place an assignment becomes COMPLETED: it stamps
the completion date, auto-mints a certification when the program requires one, sets
the completion validity window, recalculates onboarding progress when the assignment
is part of an onboarding process, and writes the audit row. It is idempotent — a
no-op when the assignment is already COMPLETED (preserving the guard in
``training.py``'s ``update_assignment``).
"""
from __future__ import annotations

import calendar
from datetime import date
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.hr.training import (
    TrainingProgram, TrainingAssignment, TrainingAssignmentStatus,
)
from app.models.hr.certification import EmployeeCertification, CertificationStatus
from app.models.hr.compliance_training import ComplianceTraining
from app.models.hr.training_audit_log import TrainingAuditAction
from app.utils.hr.training.audit import write_training_audit


_S = TrainingAssignmentStatus
_VALID_TRANSITIONS = {
    _S.NOT_STARTED: {_S.IN_PROGRESS, _S.COMPLETED, _S.FAILED, _S.WAIVED},
    _S.IN_PROGRESS: {_S.IN_PROGRESS, _S.COMPLETED, _S.FAILED, _S.WAIVED},
    _S.COMPLETED: set(),   # terminal — re-completing is a no-op, never a transition
    _S.FAILED: {_S.NOT_STARTED, _S.IN_PROGRESS},   # admin may re-open a failed attempt
    _S.WAIVED: set(),
}


def assert_assignment_transition(current: TrainingAssignmentStatus,
                                 next_: TrainingAssignmentStatus) -> None:
    if current == next_:
        return
    if next_ not in _VALID_TRANSITIONS.get(current, set()):
        raise HTTPException(409, f"Cannot transition training from {current.value} to {next_.value}")


def add_months(d: date, months: int) -> date:
    """date + N whole months, clamping the day to the target month's length."""
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def complete_assignment(
    db: Session, assignment: TrainingAssignment, *, actor_id=None,
    score=None, certification_url: Optional[str] = None,
) -> bool:
    """Drive an assignment to COMPLETED with all side-effects. Idempotent.

    Returns True when a completion was actually performed, False if it was already
    completed (no-op). The caller commits.
    """
    if assignment.status == _S.COMPLETED:
        return False  # idempotent no-op — do not re-mint certs or re-fire recalc

    prev = assignment.status
    assignment.status = _S.COMPLETED
    assignment.completion_date = assignment.completion_date or date.today()
    if score is not None:
        assignment.score = score
    if certification_url:
        assignment.certification_url = certification_url

    program = db.query(TrainingProgram).filter(TrainingProgram.id == assignment.program_id).first()

    # Completion validity window (compliance config drives it when present).
    comp = db.query(ComplianceTraining).filter(
        ComplianceTraining.program_id == assignment.program_id,
        ComplianceTraining.is_deleted == False,  # noqa: E712
    ).first()
    validity_months = comp.validity_months if comp else None
    if validity_months:
        try:
            assignment.valid_until = add_months(assignment.completion_date, int(validity_months))
        except Exception:
            pass

    # Auto-mint a certification when the program requires one (idempotent on
    # source_assignment_id so re-runs can't duplicate).
    if program and program.certification_required:
        existing = db.query(EmployeeCertification).filter(
            EmployeeCertification.source_assignment_id == assignment.id,
        ).first()
        if not existing:
            expiry = None
            if validity_months:
                try:
                    expiry = add_months(assignment.completion_date, int(validity_months))
                except Exception:
                    expiry = None
            db.add(EmployeeCertification(
                employee_id=assignment.employee_id,
                name=program.name,
                issue_date=assignment.completion_date,
                expiry_date=expiry,
                status=CertificationStatus.ACTIVE,
                certificate_url=certification_url or assignment.certification_url,
                source_assignment_id=assignment.id,
                renewal_training_program_id=program.id,
                created_by_id=actor_id,
            ))

    write_training_audit(
        db, entity_type="ASSIGNMENT", entity_id=assignment.id,
        action=TrainingAuditAction.COMPLETE, actor_id=actor_id,
        from_status=prev.value if prev else None, to_status=_S.COMPLETED.value,
    )

    # Keep onboarding progress in sync (unchanged behavior from training.py).
    if assignment.process_id:
        try:
            from app.routers.hr.onboarding import _recalculate_progress
            from app.models.hr.onboarding import OnboardingProcess
            proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == assignment.process_id).first()
            if proc:
                _recalculate_progress(db, proc)
        except Exception:
            import traceback
            traceback.print_exc()

    return True


def recompute_skill_gap(emp_skill) -> None:
    """gap = max(required - current, 0); None when required is unknown."""
    req = emp_skill.required_level
    cur = emp_skill.current_level
    if req is None:
        emp_skill.gap = None
    else:
        emp_skill.gap = max(int(req) - int(cur or 0), 0)
