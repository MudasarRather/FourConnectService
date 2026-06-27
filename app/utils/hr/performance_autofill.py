"""Performance review auto-fill — suggest manager ratings from real signals.

Best-effort, per-section SUGGESTIONS only. They pre-fill the manager's scoresheet
so the form is never blank, but the manager owns every value and can override
freely. Nothing here ever submits or finalizes a review.

Signal by section_type:
    GOAL / KRA   → average goal/OKR achievement % for the employee
    ATTENDANCE   → attendance reliability over the last ~6 months
    FEEDBACK     → 360° feedback overall rollup (rescaled to the review's scale)
    COMPETENCY / BEHAVIORAL → anchor to the prior completed review's rating
    (anything else) → no suggestion (left for the manager)

Each signal is wrapped so missing data yields None rather than an error.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.hr.performance_review import PerformanceReview
from app.models.hr.performance_goal import PerformanceGoal
from app.models.hr.attendance import Attendance, AttendanceStatus

# attendance status → credit toward the reliability ratio (working days only)
_ATT_CREDIT = {
    AttendanceStatus.PRESENT.value: 1.0,
    AttendanceStatus.WFH.value: 1.0,
    AttendanceStatus.REMOTE.value: 1.0,
    AttendanceStatus.ON_DUTY.value: 1.0,
    AttendanceStatus.LATE.value: 0.8,
    AttendanceStatus.HALF_DAY.value: 0.5,
    AttendanceStatus.ABSENT.value: 0.0,
    AttendanceStatus.LWP.value: 0.0,
}
# excluded from the working-day denominator (not worked / neutral)
_ATT_NEUTRAL = {
    AttendanceStatus.HOLIDAY.value, AttendanceStatus.WEEK_OFF.value, AttendanceStatus.LEAVE.value,
}


def _clamp(v, rating_max):
    return round(max(0.0, min(float(rating_max), float(v))), 1)


def _goals_signal(db: Session, employee_id, cycle, rating_max) -> Optional[dict]:
    try:
        q = db.query(PerformanceGoal).filter(
            PerformanceGoal.employee_id == employee_id,
            PerformanceGoal.is_deleted == False,  # noqa: E712
            PerformanceGoal.parent_id.is_(None),
        )
        rows = q.all()
        # Prefer goals in the same cycle if any exist, else fall back to all.
        same_cycle = [g for g in rows if (g.cycle or "") == (cycle or "")]
        use = same_cycle or rows
        progresses = [float(g.progress or 0) for g in use]
        if not progresses:
            return None
        avg = sum(progresses) / len(progresses)
        return {
            "rating": _clamp(avg / 100.0 * rating_max, rating_max),
            "basis": f"{len(use)} goal(s) avg {round(avg)}% complete",
        }
    except Exception:
        return None


def _attendance_signal(db: Session, employee_id, rating_max, days: int = 180) -> Optional[dict]:
    try:
        since = date.today() - timedelta(days=days)
        rows = db.query(Attendance.status).filter(
            Attendance.employee_id == employee_id,
            Attendance.is_deleted == False,  # noqa: E712
            Attendance.date >= since,
        ).all()
        working = [s for (s,) in rows if _status_val(s) not in _ATT_NEUTRAL]
        if not working:
            return None
        credit = sum(_ATT_CREDIT.get(_status_val(s), 0.0) for s in working)
        frac = credit / len(working)
        present = sum(1 for s in working if _ATT_CREDIT.get(_status_val(s), 0.0) >= 1.0)
        return {
            "rating": _clamp(frac * rating_max, rating_max),
            "basis": f"{present}/{len(working)} working days present (last 6 months)",
        }
    except Exception:
        return None


def _status_val(s):
    return s.value if hasattr(s, "value") else s


def _feedback_signal(db: Session, employee_id, review, rating_max) -> Optional[dict]:
    try:
        from app.models.hr.performance_feedback import PerfFeedbackRequest
        from app.utils.hr.performance_service import feedback_rollup
        reqs = db.query(PerfFeedbackRequest).filter(
            PerfFeedbackRequest.employee_id == employee_id,
            PerfFeedbackRequest.is_deleted == False,  # noqa: E712
        ).all()
        # Prefer a request linked to this review, else the most recent with data.
        linked = [r for r in reqs if review and r.review_id == review.id]
        best = None
        for r in (linked or reqs):
            roll = feedback_rollup(r)
            if roll.get("overall_avg") is not None:
                src_max = float(r.rating_max or rating_max) or rating_max
                rescaled = roll["overall_avg"] / src_max * rating_max
                best = {"rating": _clamp(rescaled, rating_max),
                        "basis": f"360° avg {roll['overall_avg']} from {roll['submitted']} rater(s)"}
                if linked:
                    break
        return best
    except Exception:
        return None


def _prior_section_signal(db: Session, employee_id, section_type, this_review_id, rating_max) -> Optional[dict]:
    try:
        prior = (db.query(PerformanceReview)
                 .filter(PerformanceReview.employee_id == employee_id,
                         PerformanceReview.id != this_review_id,
                         PerformanceReview.is_deleted == False,  # noqa: E712
                         PerformanceReview.status.in_(["COMPLETED", "ACKNOWLEDGED"]))
                 .order_by(PerformanceReview.completed_at.desc().nullslast(),
                           PerformanceReview.updated_at.desc())
                 .first())
        if not prior:
            return None
        for s in (prior.sections_json or []):
            if (s.get("section_type") or "").upper() == (section_type or "").upper() and s.get("manager_rating") is not None:
                return {"rating": _clamp(s["manager_rating"], rating_max),
                        "basis": f"anchored to last cycle ({prior.period_label or prior.cycle})"}
        return None
    except Exception:
        return None


def suggest_ratings(db: Session, review: PerformanceReview) -> list:
    """Per-section suggestion list: [{key, title, section_type, suggested_rating, basis}].

    `suggested_rating` is None where no signal applies (the manager fills it in).
    """
    rating_max = review.rating_max or 5
    emp_id = review.employee_id
    cycle = review.cycle

    # compute shared signals lazily / once
    _cache = {}

    def goals():
        if "goals" not in _cache:
            _cache["goals"] = _goals_signal(db, emp_id, cycle, rating_max)
        return _cache["goals"]

    def attendance():
        if "att" not in _cache:
            _cache["att"] = _attendance_signal(db, emp_id, rating_max)
        return _cache["att"]

    def feedback():
        if "fb" not in _cache:
            _cache["fb"] = _feedback_signal(db, emp_id, review, rating_max)
        return _cache["fb"]

    out = []
    for s in (review.sections_json or []):
        st = (s.get("section_type") or "").upper()
        sig = None
        if st in ("GOAL", "KRA"):
            sig = goals()
        elif st == "ATTENDANCE":
            sig = attendance()
        elif st == "FEEDBACK":
            sig = feedback()
        elif st in ("COMPETENCY", "BEHAVIORAL"):
            sig = _prior_section_signal(db, emp_id, st, review.id, rating_max)
        out.append({
            "key": s.get("key"),
            "title": s.get("title"),
            "section_type": s.get("section_type"),
            "suggested_rating": sig["rating"] if sig else None,
            "basis": sig["basis"] if sig else None,
        })
    return out
