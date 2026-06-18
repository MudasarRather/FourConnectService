"""HR Training & Development — compliance auto re-assignment engine.

For each active compliance-training config, resolve the eligible employee set and
ensure each has a current (non-expired) completion. When an employee's last
completion is due (or they've never completed it) AND they have no OPEN assignment
already, a fresh assignment is created.

Idempotency is the whole game: the decision to create is keyed on "no open
assignment exists", never on wall-clock — so the sweep is safe to run repeatedly
(and from both the daemon and a manual trigger).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.hr.training import TrainingAssignment, TrainingAssignmentStatus
from app.models.hr.compliance_training import (
    ComplianceTraining, ComplianceFrequency, FREQUENCY_MONTHS,
)
from app.models.hr.training_audit_log import TrainingAuditAction
from app.utils.hr.training.audit import write_training_audit
from app.utils.hr.training.flow import add_months
from app.utils.hr.training.service import resolve_eligible_employee_ids


_OPEN = (TrainingAssignmentStatus.NOT_STARTED, TrainingAssignmentStatus.IN_PROGRESS)


def _is_due(last_completion: Optional[date], freq: ComplianceFrequency, today: date) -> bool:
    if last_completion is None:
        return True  # never completed → due now
    months = FREQUENCY_MONTHS.get(freq)
    if months is None:          # ONE_TIME and already completed → not due
        return False
    next_due = add_months(last_completion, months)
    return next_due <= today


def run_compliance_reassign(
    db: Session, *, only_config_id=None, actor_id=None, force=False,
) -> dict:
    """Sweep eligible cohorts and create due/never enrollments.

    ``force=True`` (used by the manual "Run sweep" trigger) drops the
    ``auto_reassign`` filter so a Manual rule can still be swept on demand;
    the daemon leaves ``force=False`` so automatic sweeps stay opt-in.
    Paused (``is_active == False``) rules are never swept, even with force.
    """
    today = date.today()
    configs_q = db.query(ComplianceTraining).filter(
        ComplianceTraining.is_deleted == False,  # noqa: E712
        ComplianceTraining.is_active == True,     # noqa: E712
    )
    if not force:
        configs_q = configs_q.filter(ComplianceTraining.auto_reassign == True)  # noqa: E712
    if only_config_id is not None:
        configs_q = configs_q.filter(ComplianceTraining.id == only_config_id)
    configs = configs_q.all()

    created = 0
    skipped = 0
    eligible_total = 0
    new_ids = []

    for cfg in configs:
        emp_ids = resolve_eligible_employee_ids(db, cfg.applies_to)
        eligible_total += len(emp_ids)
        for emp_id in emp_ids:
            # already has an open assignment for this program → skip (idempotent)
            open_exists = db.query(TrainingAssignment.id).filter(
                TrainingAssignment.employee_id == emp_id,
                TrainingAssignment.program_id == cfg.program_id,
                TrainingAssignment.status.in_(_OPEN),
            ).first()
            if open_exists:
                skipped += 1
                continue
            # latest completion
            last = db.query(TrainingAssignment.completion_date).filter(
                TrainingAssignment.employee_id == emp_id,
                TrainingAssignment.program_id == cfg.program_id,
                TrainingAssignment.status == TrainingAssignmentStatus.COMPLETED,
            ).order_by(TrainingAssignment.completion_date.desc()).first()
            last_completion = last[0] if last else None
            if not _is_due(last_completion, cfg.frequency, today):
                skipped += 1
                continue
            a = TrainingAssignment(
                program_id=cfg.program_id,
                employee_id=emp_id,
                assigned_date=today,
                due_date=today + timedelta(days=int(cfg.due_days_after_assign or 30)),
                status=TrainingAssignmentStatus.NOT_STARTED,
                enrollment_source="COMPLIANCE",
            )
            db.add(a)
            db.flush()
            new_ids.append(a.id)
            write_training_audit(
                db, entity_type="ASSIGNMENT", entity_id=a.id,
                action=TrainingAuditAction.REASSIGN, actor_id=actor_id,
                to_status=TrainingAssignmentStatus.NOT_STARTED.value,
                note=f"Compliance auto-reassign ({cfg.frequency.value})",
            )
            created += 1

    db.commit()
    return {
        "created": created, "skipped": skipped, "eligible": eligible_total,
        "assignment_ids": new_ids,
    }
