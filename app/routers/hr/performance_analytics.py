"""HR Performance — Analytics & Insights (admin / superuser).

Cross-module rollups for the Insights tab: score distribution / bell curve,
department comparison, cycle trend, goal health, feedback participation, PIP load,
plus top-performer / needs-attention lists. Read-only aggregation — no writes.

Distinct prefix /hr/performance-analytics.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_superuser
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.performance_review import PerformanceReview, PerformanceReviewStatus
from app.models.hr.performance_goal import PerformanceGoal, GoalType, GoalStatus
from app.models.hr.performance_feedback import PerfFeedbackResponse, PerfFeedbackRequest, FeedbackResponseStatus
from app.models.hr.performance_pip import PerformancePip, PipStatus

router = APIRouter(prefix="/hr/performance-analytics", tags=["HR — Performance Analytics"])

S = PerformanceReviewStatus
DONE = [S.COMPLETED.value, S.ACKNOWLEDGED.value]


@router.get("/overview")
def overview(
    cycle: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    rbase = db.query(PerformanceReview).filter(PerformanceReview.is_deleted == False)  # noqa: E712
    if cycle:
        rbase = rbase.filter(PerformanceReview.cycle == cycle)

    total = rbase.count()
    by_status = {st: cnt for st, cnt in rbase.with_entities(
        PerformanceReview.status, func.count(PerformanceReview.id)).group_by(PerformanceReview.status).all()}
    completed = by_status.get(S.COMPLETED.value, 0) + by_status.get(S.ACKNOWLEDGED.value, 0)
    avg_overall = rbase.filter(PerformanceReview.overall_score.isnot(None)).with_entities(
        func.avg(PerformanceReview.overall_score)).scalar()

    # distribution (1..5 floor) over scored reviews
    dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for (sc,) in rbase.filter(PerformanceReview.overall_score.isnot(None)).with_entities(PerformanceReview.overall_score).all():
        b = max(1, min(5, int(float(sc))))
        dist[b] += 1

    # trend by cycle
    trend = []
    for cyc, cnt, avg_c in (
        db.query(PerformanceReview.cycle, func.count(PerformanceReview.id), func.avg(PerformanceReview.overall_score))
        .filter(PerformanceReview.is_deleted == False)  # noqa: E712
        .group_by(PerformanceReview.cycle).all()
    ):
        trend.append({"cycle": cyc, "count": cnt, "avg": round(float(avg_c), 2) if avg_c is not None else None})

    # by department (avg score over completed reviews)
    by_dept = []
    rows = (
        db.query(Department.name, func.count(PerformanceReview.id), func.avg(PerformanceReview.overall_score))
        .join(Employee, Employee.id == PerformanceReview.employee_id)
        .join(Department, Department.id == Employee.department_id)
        .filter(
            PerformanceReview.is_deleted == False,  # noqa: E712
            PerformanceReview.overall_score.isnot(None),
        )
    )
    if cycle:
        rows = rows.filter(PerformanceReview.cycle == cycle)
    for dept_name, cnt, avg_d in rows.group_by(Department.name).all():
        by_dept.append({"department": dept_name or "—", "count": cnt, "avg": round(float(avg_d), 2) if avg_d is not None else None})
    by_dept.sort(key=lambda x: (x["avg"] is not None, x["avg"]), reverse=True)

    # top performers + needs attention (latest completed, scored)
    scored_q = rbase.filter(PerformanceReview.overall_score.isnot(None), PerformanceReview.status.in_(DONE))
    top_rows = scored_q.order_by(PerformanceReview.overall_score.desc()).limit(5).all()
    low_rows = scored_q.order_by(PerformanceReview.overall_score.asc()).limit(5).all()

    def _mini(r):
        emp = r.employee
        nm = None
        if emp and getattr(emp, "user", None):
            nm = getattr(emp.user, "full_name", None)
        return {
            "id": str(r.id), "employee_id": str(r.employee_id),
            "employee_name": nm or getattr(emp, "employee_id", "—"),
            "overall_score": float(r.overall_score) if r.overall_score is not None else None,
            "rating_max": r.rating_max, "cycle": r.cycle, "period_label": r.period_label,
        }

    # goals health
    gbase = db.query(PerformanceGoal).filter(PerformanceGoal.is_deleted == False)  # noqa: E712
    if cycle:
        gbase = gbase.filter(PerformanceGoal.cycle == cycle)
    goal_by_status = {st: cnt for st, cnt in gbase.with_entities(
        PerformanceGoal.status, func.count(PerformanceGoal.id)).group_by(PerformanceGoal.status).all()}
    goal_avg = gbase.filter(PerformanceGoal.goal_type != GoalType.KEY_RESULT.value).with_entities(
        func.avg(PerformanceGoal.progress)).scalar()

    # feedback participation
    fb_invited = db.query(func.count(PerfFeedbackResponse.id)).join(
        PerfFeedbackRequest, PerfFeedbackResponse.request_id == PerfFeedbackRequest.id).filter(
        PerfFeedbackRequest.is_deleted == False).scalar() or 0  # noqa: E712
    fb_submitted = db.query(func.count(PerfFeedbackResponse.id)).join(
        PerfFeedbackRequest, PerfFeedbackResponse.request_id == PerfFeedbackRequest.id).filter(
        PerfFeedbackRequest.is_deleted == False,  # noqa: E712
        PerfFeedbackResponse.status == FeedbackResponseStatus.SUBMITTED.value).scalar() or 0

    # PIP load
    pip_active = db.query(func.count(PerformancePip.id)).filter(
        PerformancePip.is_deleted == False,  # noqa: E712
        PerformancePip.status.in_([PipStatus.ACTIVE.value, PipStatus.EXTENDED.value])).scalar() or 0

    return {
        "reviews": {
            "total": total,
            "completed": completed,
            "completion_rate": round(completed / total * 100, 1) if total else 0,
            "avg_overall": round(float(avg_overall), 2) if avg_overall is not None else None,
            "by_status": by_status,
            "distribution": [{"band": k, "count": v} for k, v in dist.items()],
            "trend": trend,
            "by_department": by_dept,
        },
        "top_performers": [_mini(r) for r in top_rows],
        "needs_attention": [_mini(r) for r in low_rows],
        "goals": {
            "by_status": goal_by_status,
            "avg_progress": round(float(goal_avg), 1) if goal_avg is not None else 0,
            "at_risk": goal_by_status.get(GoalStatus.AT_RISK.value, 0) + goal_by_status.get(GoalStatus.OFF_TRACK.value, 0),
            "achieved": goal_by_status.get(GoalStatus.ACHIEVED.value, 0),
        },
        "feedback": {
            "invited": int(fb_invited),
            "submitted": int(fb_submitted),
            "response_rate": round(int(fb_submitted) / int(fb_invited) * 100, 1) if fb_invited else 0,
        },
        "pip": {"active": int(pip_active)},
    }
