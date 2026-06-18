"""HR Training & Development — Dashboard stats (single read-only aggregation)."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.training import (
    TrainingProgram, TrainingAssignment, TrainingAssignmentStatus,
)
from app.models.hr.certification import EmployeeCertification, CertificationStatus
from app.models.hr.skill import Skill, EmployeeSkill
from app.models.hr.training_request import TrainingRequest, TrainingRequestStatus
from app.models.hr.training_feedback import TrainingFeedback
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/training", tags=["HR — Training Dashboard"])

_OPEN = (TrainingAssignmentStatus.NOT_STARTED, TrainingAssignmentStatus.IN_PROGRESS)
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@router.get("/stats")
def training_stats(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    today = date.today()

    total_programs = db.query(func.count(TrainingProgram.id)).filter(
        TrainingProgram.is_deleted == False, TrainingProgram.is_active == True,  # noqa: E712
    ).scalar() or 0

    total_assignments = db.query(func.count(TrainingAssignment.id)).scalar() or 0
    completed_count = db.query(func.count(TrainingAssignment.id)).filter(
        TrainingAssignment.status == TrainingAssignmentStatus.COMPLETED,
    ).scalar() or 0
    active_assignments = db.query(func.count(TrainingAssignment.id)).filter(
        TrainingAssignment.status.in_(_OPEN),
    ).scalar() or 0
    overdue_count = db.query(func.count(TrainingAssignment.id)).filter(
        TrainingAssignment.status.in_(_OPEN),
        TrainingAssignment.due_date.isnot(None),
        TrainingAssignment.due_date < today,
    ).scalar() or 0
    upcoming = db.query(func.count(TrainingAssignment.id)).filter(
        TrainingAssignment.status.in_(_OPEN),
        TrainingAssignment.due_date.isnot(None),
        TrainingAssignment.due_date >= today,
        TrainingAssignment.due_date <= today + timedelta(days=30),
    ).scalar() or 0
    employees_trained = db.query(func.count(func.distinct(TrainingAssignment.employee_id))).filter(
        TrainingAssignment.status == TrainingAssignmentStatus.COMPLETED,
    ).scalar() or 0
    completion_rate = round(completed_count / total_assignments * 100, 1) if total_assignments else 0.0

    # Certifications
    def _cert_count(*filters):
        q = db.query(func.count(EmployeeCertification.id)).filter(
            EmployeeCertification.is_deleted == False, *filters,  # noqa: E712
        )
        return q.scalar() or 0

    certs_active = _cert_count(EmployeeCertification.status.in_(
        [CertificationStatus.ACTIVE, CertificationStatus.EXPIRING_SOON, CertificationStatus.PENDING_RENEWAL]
    ))
    certs_expired = _cert_count(EmployeeCertification.status == CertificationStatus.EXPIRED)

    def _expiring_within(days):
        return _cert_count(
            EmployeeCertification.expiry_date.isnot(None),
            EmployeeCertification.expiry_date >= today,
            EmployeeCertification.expiry_date <= today + timedelta(days=days),
            EmployeeCertification.status != CertificationStatus.REVOKED,
        )

    certs_expiring_30 = _expiring_within(30)
    certs_expiring_60 = _expiring_within(60)
    certs_expiring_90 = _expiring_within(90)

    # Skill gap
    skill_gap_count = db.query(func.count(EmployeeSkill.id)).filter(EmployeeSkill.gap > 0).scalar() or 0

    # Compliance: completion among compliance-flagged programs
    comp_total = db.query(func.count(TrainingAssignment.id)).join(
        TrainingProgram, TrainingProgram.id == TrainingAssignment.program_id,
    ).filter(TrainingProgram.is_compliance == True).scalar() or 0  # noqa: E712
    comp_done = db.query(func.count(TrainingAssignment.id)).join(
        TrainingProgram, TrainingProgram.id == TrainingAssignment.program_id,
    ).filter(
        TrainingProgram.is_compliance == True,  # noqa: E712
        TrainingAssignment.status == TrainingAssignmentStatus.COMPLETED,
    ).scalar() or 0
    compliance_rate = round(comp_done / comp_total * 100, 1) if comp_total else 0.0

    pending_requests = db.query(func.count(TrainingRequest.id)).filter(
        TrainingRequest.is_deleted == False,  # noqa: E712
        TrainingRequest.status == TrainingRequestStatus.PENDING_APPROVAL,
    ).scalar() or 0

    avg_feedback = db.query(func.avg(TrainingFeedback.rating)).scalar()
    avg_feedback_rating = round(float(avg_feedback), 2) if avg_feedback is not None else None

    # by_type (assignment counts grouped by program type)
    by_type_rows = (
        db.query(TrainingProgram.training_type, func.count(TrainingAssignment.id))
        .join(TrainingAssignment, TrainingAssignment.program_id == TrainingProgram.id)
        .group_by(TrainingProgram.training_type)
        .all()
    )
    by_type = [{"type": t.value if t else "OTHER", "count": int(c or 0)} for t, c in by_type_rows]

    # by_status distribution
    by_status_rows = (
        db.query(TrainingAssignment.status, func.count(TrainingAssignment.id))
        .group_by(TrainingAssignment.status)
        .all()
    )
    by_status = [{"status": s.value if s else "NOT_STARTED", "count": int(c or 0)} for s, c in by_status_rows]

    # cert status distribution
    cert_status_rows = (
        db.query(EmployeeCertification.status, func.count(EmployeeCertification.id))
        .filter(EmployeeCertification.is_deleted == False)  # noqa: E712
        .group_by(EmployeeCertification.status)
        .all()
    )
    cert_status = [{"status": s.value if s else "ACTIVE", "count": int(c or 0)} for s, c in cert_status_rows]

    # monthly completions + training hours (last 6 months, bucketed in Python)
    window_start = (today.replace(day=1) - timedelta(days=31 * 5)).replace(day=1)
    comp_rows = (
        db.query(TrainingAssignment.completion_date, TrainingProgram.duration_hours)
        .join(TrainingProgram, TrainingProgram.id == TrainingAssignment.program_id)
        .filter(
            TrainingAssignment.status == TrainingAssignmentStatus.COMPLETED,
            TrainingAssignment.completion_date.isnot(None),
            TrainingAssignment.completion_date >= window_start,
        )
        .all()
    )
    buckets = {}
    cur = window_start
    for _ in range(6):
        key = (cur.year, cur.month)
        buckets[key] = {"count": 0, "hours": 0.0}
        # advance one month
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    for cd, hours in comp_rows:
        key = (cd.year, cd.month)
        if key in buckets:
            buckets[key]["count"] += 1
            buckets[key]["hours"] += float(hours or 0)
    monthly = [
        {"month": f"{_MONTHS[k[1] - 1]} {str(k[0])[2:]}", "completions": v["count"], "hours": round(v["hours"], 1)}
        for k, v in buckets.items()
    ]

    # top skill gaps
    top_gap_rows = (
        db.query(Skill.id, Skill.name, func.avg(EmployeeSkill.gap), func.count(EmployeeSkill.id))
        .join(EmployeeSkill, EmployeeSkill.skill_id == Skill.id)
        .filter(Skill.is_deleted == False)  # noqa: E712
        .group_by(Skill.id, Skill.name)
        .order_by(func.avg(EmployeeSkill.gap).desc().nullslast())
        .limit(6)
        .all()
    )
    top_skill_gaps = [
        {"skill_id": str(sid), "skill": sname, "avg_gap": round(float(g), 2) if g is not None else 0.0,
         "employees": int(c or 0)}
        for sid, sname, g, c in top_gap_rows
    ]

    return {
        "total_programs": int(total_programs),
        "active_assignments": int(active_assignments),
        "completed_count": int(completed_count),
        "completion_rate": completion_rate,
        "overdue_count": int(overdue_count),
        "employees_trained": int(employees_trained),
        "upcoming_trainings": int(upcoming),
        "certs_active": int(certs_active),
        "certs_expired": int(certs_expired),
        "certs_expiring_30": int(certs_expiring_30),
        "certs_expiring_60": int(certs_expiring_60),
        "certs_expiring_90": int(certs_expiring_90),
        "skill_gap_count": int(skill_gap_count),
        "compliance_rate": compliance_rate,
        "pending_requests": int(pending_requests),
        "avg_feedback_rating": avg_feedback_rating,
        "by_type": by_type,
        "by_status": by_status,
        "cert_status": cert_status,
        "monthly": monthly,
        "top_skill_gaps": top_skill_gaps,
    }
