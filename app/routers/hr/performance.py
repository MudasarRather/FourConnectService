"""HR Performance Management — admin endpoints (superuser).

Review instances scored against appraisal templates. The settings page authors
the rubric; THIS module runs the reviews. Workflow:
    SELF_ASSESSMENT → MANAGER_ASSESSMENT → COMPLETED → ACKNOWLEDGED  (+ CANCELLED)

New module; reads/writes the auto-created ``hr_performance_reviews`` table.
"""
from __future__ import annotations

from datetime import datetime, date, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_superuser
from app.models.hr.employee import Employee
from app.models.hr.appraisal_template import AppraisalTemplate
from app.models.hr.performance_review import PerformanceReview, PerformanceReviewStatus, HikeStatus
from app.models.hr.performance_goal import PerformanceGoal
from app.models.hr.performance_feedback import PerfFeedbackRequest
from app.models.hr.performance_calibration import PerformanceCalibration
from app.models.hr.performance_pip import PerformancePip, PipStatus
from app.models.hr.merit_policy import MeritPolicy
from app.models.hr.employee_compensation import EmployeeCompensation, CompensationStatus
from app.schemas.hr.performance_review import (
    PerfReviewCreate, PerfReviewBulkCreate, PerfReviewUpdate, PerfManagerSubmit, PerfSelfSubmit,
    PerfTransition, PerfRecommendIn, PerfApproveHikeIn,
)
from app.utils.hr.performance_service import (
    serialize, snapshot_sections, apply_scores, recompute, latest_review_summary,
    serialize_goal, serialize_feedback_request, serialize_calibration, serialize_pip, emp_name,
    merit_band_context,
)
from app.utils.hr.performance_autofill import suggest_ratings
from app.utils.hr.lifecycle_guard import guard_employable
from app.utils.hr.payroll.service import create_compensation_revision

router = APIRouter(prefix="/hr/performance", tags=["HR — Performance"])

S = PerformanceReviewStatus
OPEN_VALUES = [S.DRAFT.value, S.SELF_ASSESSMENT.value, S.MANAGER_ASSESSMENT.value]

# admin-allowed status transitions
ALLOWED = {
    S.DRAFT.value: {S.SELF_ASSESSMENT.value, S.CANCELLED.value},
    S.SELF_ASSESSMENT.value: {S.MANAGER_ASSESSMENT.value, S.CANCELLED.value},
    S.MANAGER_ASSESSMENT.value: {S.SELF_ASSESSMENT.value, S.COMPLETED.value, S.CANCELLED.value},
    S.COMPLETED.value: {S.MANAGER_ASSESSMENT.value, S.ACKNOWLEDGED.value},
    S.ACKNOWLEDGED.value: {S.COMPLETED.value},
    S.CANCELLED.value: {S.SELF_ASSESSMENT.value, S.MANAGER_ASSESSMENT.value},
}


def _now():
    return datetime.now(timezone.utc)


def _to_dt(d: Optional[date]):
    return datetime.combine(d, datetime.min.time()) if d else None


def _stamp_status(r: PerformanceReview, new: str):
    """Apply a status + the matching timeline stamp/score rollup."""
    r.status = new
    if new == S.MANAGER_ASSESSMENT.value and r.self_submitted_at is None:
        r.self_submitted_at = _now()
    if new == S.COMPLETED.value:
        recompute(r)
        if r.manager_submitted_at is None:
            r.manager_submitted_at = _now()
        r.completed_at = _now()
    if new == S.ACKNOWLEDGED.value:
        r.employee_ack = True
        r.acknowledged_at = _now()


def _load(db: Session, review_id: UUID) -> PerformanceReview:
    r = db.query(PerformanceReview).filter(
        PerformanceReview.id == review_id, PerformanceReview.is_deleted == False,  # noqa: E712
    ).first()
    if not r:
        raise HTTPException(404, "Review not found")
    return r


# ─────────────────────────── Dashboard stats ───────────────────────────
@router.get("/stats")
def performance_stats(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    base = db.query(PerformanceReview).filter(PerformanceReview.is_deleted == False)  # noqa: E712
    total = base.count()

    by_status = {}
    for status_val, cnt in (
        db.query(PerformanceReview.status, func.count(PerformanceReview.id))
        .filter(PerformanceReview.is_deleted == False)  # noqa: E712
        .group_by(PerformanceReview.status).all()
    ):
        by_status[status_val] = cnt

    completed = by_status.get(S.COMPLETED.value, 0) + by_status.get(S.ACKNOWLEDGED.value, 0)
    in_self = by_status.get(S.SELF_ASSESSMENT.value, 0)
    in_manager = by_status.get(S.MANAGER_ASSESSMENT.value, 0)
    acknowledged = by_status.get(S.ACKNOWLEDGED.value, 0)

    avg_overall = (
        db.query(func.avg(PerformanceReview.overall_score))
        .filter(PerformanceReview.is_deleted == False, PerformanceReview.overall_score.isnot(None))  # noqa: E712
        .scalar()
    )

    # score bands (floor of overall, clamped 1..5) over scored reviews
    bands = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for (score,) in (
        db.query(PerformanceReview.overall_score)
        .filter(PerformanceReview.is_deleted == False, PerformanceReview.overall_score.isnot(None))  # noqa: E712
        .all()
    ):
        b = max(1, min(5, int(float(score)))) if score is not None else None
        if b:
            bands[b] += 1
    score_bands = [{"band": k, "count": v} for k, v in bands.items()]

    # by cycle
    by_cycle = []
    for cyc, cnt, avg_c in (
        db.query(PerformanceReview.cycle, func.count(PerformanceReview.id), func.avg(PerformanceReview.overall_score))
        .filter(PerformanceReview.is_deleted == False)  # noqa: E712
        .group_by(PerformanceReview.cycle).all()
    ):
        by_cycle.append({"cycle": cyc, "count": cnt, "avg": round(float(avg_c), 2) if avg_c is not None else None})

    overdue = (
        db.query(func.count(PerformanceReview.id))
        .filter(
            PerformanceReview.is_deleted == False,  # noqa: E712
            PerformanceReview.due_date.isnot(None),
            PerformanceReview.due_date < _now(),
            PerformanceReview.status.in_(OPEN_VALUES),
        ).scalar()
    ) or 0

    # recent
    maps = {"desig": {}, "dept": {}}
    recent_rows = (
        db.query(PerformanceReview)
        .filter(PerformanceReview.is_deleted == False)  # noqa: E712
        .order_by(PerformanceReview.updated_at.desc()).limit(8).all()
    )
    recent = [serialize(db, r, maps) for r in recent_rows]

    return {
        "total": total,
        "by_status": by_status,
        "completed": completed,
        "in_self": in_self,
        "in_manager": in_manager,
        "acknowledged": acknowledged,
        "open": in_self + in_manager + by_status.get(S.DRAFT.value, 0),
        "avg_overall": round(float(avg_overall), 2) if avg_overall is not None else None,
        "completion_rate": round(completed / total * 100, 1) if total else 0,
        "overdue": overdue,
        "score_bands": score_bands,
        "by_cycle": by_cycle,
        "recent": recent,
    }


@router.get("/cycles")
def list_cycles(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Distinct cycle/period groupings with progress, for the Cycles tab."""
    GK = (PerformanceReview.cycle, PerformanceReview.period_label, PerformanceReview.template_name)
    totals = (
        db.query(*GK, func.count(PerformanceReview.id), func.avg(PerformanceReview.overall_score), func.min(PerformanceReview.created_at))
        .filter(PerformanceReview.is_deleted == False)  # noqa: E712
        .group_by(*GK).all()
    )
    done_rows = (
        db.query(*GK, func.count(PerformanceReview.id))
        .filter(
            PerformanceReview.is_deleted == False,  # noqa: E712
            PerformanceReview.status.in_([S.COMPLETED.value, S.ACKNOWLEDGED.value]),
        ).group_by(*GK).all()
    )
    done_map = {(c, p, t): n for c, p, t, n in done_rows}
    out = []
    for cyc, period, tmpl, cnt, avg_c, started in totals:
        done = int(done_map.get((cyc, period, tmpl), 0))
        out.append({
            "cycle": cyc,
            "period_label": period,
            "template_name": tmpl,
            "total": cnt,
            "completed": done,
            "progress": round(done / cnt * 100, 1) if cnt else 0,
            "avg": round(float(avg_c), 2) if avg_c is not None else None,
            "started_at": started,
        })
    out.sort(key=lambda x: (x["started_at"] is not None, x["started_at"]), reverse=True)
    return {"items": out, "total": len(out)}


@router.get("/employees/{employee_id}/latest-review")
def employee_latest_review(employee_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Latest finalized review — surfaced as evidence on the promote screen."""
    return latest_review_summary(db, employee_id) or {}


@router.get("/employees/{employee_id}/snapshot")
def employee_snapshot(employee_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Everything the profile Appraisal Console needs for one employee in a single
    call: reviews + score history, goals/OKRs, 360 feedback, calibration, PIPs."""
    emp = db.query(Employee).filter(Employee.id == employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    maps = {"desig": {}, "dept": {}}

    reviews = (
        db.query(PerformanceReview)
        .filter(PerformanceReview.employee_id == employee_id, PerformanceReview.is_deleted == False)  # noqa: E712
        .order_by(PerformanceReview.created_at.desc()).all()
    )
    review_dicts = [serialize(db, r, maps) for r in reviews]

    # score history — completed reviews, oldest→newest, for a sparkline/trend
    history = sorted(
        [r for r in review_dicts if r["overall_score"] is not None and r["status"] in ("COMPLETED", "ACKNOWLEDGED")],
        key=lambda r: (r["completed_at"] or r["created_at"]),
    )
    score_history = [
        {"period": r["period_label"] or r["cycle"], "cycle": r["cycle"], "score": r["overall_score"], "rating_max": r["rating_max"]}
        for r in history
    ]

    goals = (
        db.query(PerformanceGoal)
        .filter(PerformanceGoal.employee_id == employee_id, PerformanceGoal.is_deleted == False,  # noqa: E712
                PerformanceGoal.parent_id.is_(None))
        .order_by(PerformanceGoal.created_at.desc()).all()
    )
    feedback = (
        db.query(PerfFeedbackRequest)
        .filter(PerfFeedbackRequest.employee_id == employee_id, PerfFeedbackRequest.is_deleted == False)  # noqa: E712
        .order_by(PerfFeedbackRequest.created_at.desc()).all()
    )
    calibration = (
        db.query(PerformanceCalibration)
        .filter(PerformanceCalibration.employee_id == employee_id, PerformanceCalibration.is_deleted == False)  # noqa: E712
        .order_by(PerformanceCalibration.created_at.desc()).all()
    )
    pips = (
        db.query(PerformancePip)
        .filter(PerformancePip.employee_id == employee_id, PerformancePip.is_deleted == False)  # noqa: E712
        .order_by(PerformancePip.created_at.desc()).all()
    )

    scored = [r["overall_score"] for r in review_dicts if r["overall_score"] is not None]
    return {
        "employee": {
            "id": str(emp.id),
            "employee_id": emp.employee_id,
            "name": emp_name(emp),
        },
        "latest_review": latest_review_summary(db, employee_id) or None,
        "reviews": review_dicts,
        "score_history": score_history,
        "goals": [serialize_goal(db, g, maps, with_children=True) for g in goals],
        "feedback": [serialize_feedback_request(db, f, maps) for f in feedback],
        "calibration": [serialize_calibration(db, c, maps) for c in calibration],
        "pips": [serialize_pip(db, p, maps) for p in pips],
        "stats": {
            "reviews": len(reviews),
            "avg_score": round(sum(scored) / len(scored), 2) if scored else None,
            "open_goals": sum(1 for g in goals if g.status not in ("ACHIEVED", "MISSED", "CANCELLED")),
            "active_pips": sum(1 for p in pips if p.status in (PipStatus.ACTIVE.value, PipStatus.EXTENDED.value)),
        },
    }


# Literal path — MUST be declared before GET /{review_id} so "merit-budget"
# isn't swallowed by the UUID path converter.
@router.get("/merit-budget")
def merit_budget(cycle: Optional[str] = None, period_label: Optional[str] = None,
                 db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Recommended/approved/applied hike spend across a cycle vs the policy budget pool."""
    q = db.query(PerformanceReview).filter(PerformanceReview.is_deleted == False)  # noqa: E712
    if cycle:
        q = q.filter(PerformanceReview.cycle == cycle)
    if period_label:
        q = q.filter(PerformanceReview.period_label == period_label)
    rows = q.all()

    total_ctc = 0.0
    recommended_amt = approved_amt = applied_amt = 0.0
    recommended_n = approved_n = applied_n = 0
    policy = None
    for r in rows:
        emp = r.employee
        # Base CTC for budgeting: the PRE-hike figure for applied rows (so an
        # already-raised current CTC doesn't inflate the spend), else the live CTC.
        base = float(r.prev_annual_ctc) if r.prev_annual_ctc is not None else (_current_annual_ctc(db, emp) if emp else None)
        if base:
            total_ctc += base
        if policy is None and r.merit_policy_id:
            policy = db.query(MeritPolicy).filter(MeritPolicy.id == r.merit_policy_id, MeritPolicy.is_deleted == False).first()  # noqa: E712
        if r.hike_status == HikeStatus.RECOMMENDED.value and r.recommended_hike_pct and base:
            recommended_amt += base * float(r.recommended_hike_pct) / 100.0
            recommended_n += 1
        if r.hike_status in (HikeStatus.APPLIED.value, HikeStatus.APPROVED.value):
            # Exact delta when both snapshots exist, else pct × base.
            if r.prev_annual_ctc is not None and r.new_annual_ctc is not None:
                amt = float(r.new_annual_ctc) - float(r.prev_annual_ctc)
            elif base and (r.approved_hike_pct or r.recommended_hike_pct):
                amt = base * float(r.approved_hike_pct or r.recommended_hike_pct) / 100.0
            else:
                amt = 0.0
            approved_amt += amt
            approved_n += 1
            if r.hike_status == HikeStatus.APPLIED.value:
                applied_amt += amt
                applied_n += 1
    if policy is None:
        policy = db.query(MeritPolicy).filter(MeritPolicy.is_default == True, MeritPolicy.is_active == True, MeritPolicy.is_deleted == False).first()  # noqa: E712
    budget_pct = float(policy.merit_budget_pct) if (policy and policy.merit_budget_pct is not None) else None
    budget_amt = round(total_ctc * budget_pct / 100.0, 2) if budget_pct is not None else None
    committed = recommended_amt + approved_amt
    return {
        "cycle": cycle, "period_label": period_label,
        "policy_id": str(policy.id) if policy else None,
        "policy_name": policy.name if policy else None,
        "review_count": len(rows),
        "total_annual_ctc": round(total_ctc, 2),
        "budget_pct": budget_pct,
        "budget_amount": budget_amt,
        "recommended_amount": round(recommended_amt, 2), "recommended_count": recommended_n,
        "approved_amount": round(approved_amt, 2), "approved_count": approved_n,
        "applied_amount": round(applied_amt, 2), "applied_count": applied_n,
        "committed_amount": round(committed, 2),
        "over_budget": bool(budget_amt is not None and committed > budget_amt),
        "remaining": round(budget_amt - committed, 2) if budget_amt is not None else None,
    }


# ─────────────────────────── List + create ───────────────────────────
@router.get("/")
def list_reviews(
    page: int = 1,
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    cycle: Optional[str] = None,
    employee_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(PerformanceReview).filter(PerformanceReview.is_deleted == False)  # noqa: E712
    if status:
        q = q.filter(PerformanceReview.status == status)
    if cycle:
        q = q.filter(PerformanceReview.cycle == cycle)
    if employee_id:
        q = q.filter(PerformanceReview.employee_id == employee_id)
    total = q.count()
    rows = (
        q.order_by(PerformanceReview.updated_at.desc())
        .offset((page - 1) * limit).limit(limit).all()
    )
    maps = {"desig": {}, "dept": {}}
    return {"items": [serialize(db, r, maps) for r in rows], "total": total, "page": page, "limit": limit}


def _default_merit_policy_id(db: Session):
    p = db.query(MeritPolicy).filter(
        MeritPolicy.is_default == True, MeritPolicy.is_active == True, MeritPolicy.is_deleted == False,  # noqa: E712
    ).first()
    return p.id if p else None


def _create_one(db: Session, template: AppraisalTemplate, emp: Employee, *, cycle, period_label, due_date,
                reviewer_id, actor_id, merit_policy_id=None, hike_effective_from=None) -> PerformanceReview:
    scale = template.rating_scale if isinstance(template.rating_scale, dict) else {}
    r = PerformanceReview(
        employee_id=emp.id,
        reviewer_id=reviewer_id if reviewer_id else getattr(emp, "reporting_manager_id", None),
        template_id=template.id,
        template_code=template.code,
        template_name=template.name,
        cycle=cycle or template.cycle or "ANNUAL",
        period_label=period_label,
        rating_max=int(scale.get("max", 5) or 5),
        rating_labels=scale.get("labels") if isinstance(scale.get("labels"), list) else None,
        sections_json=snapshot_sections(template),
        # Manager-owned model: launch straight into the manager's court. The
        # employee's self-input is now an OPTIONAL non-scoring reflection, so we
        # don't gate the cycle behind a "self-assessment" stage.
        status=S.MANAGER_ASSESSMENT.value,
        due_date=_to_dt(due_date),
        merit_policy_id=merit_policy_id,
        hike_effective_from=hike_effective_from,
        created_by_id=actor_id,
    )
    db.add(r)
    return r


@router.post("/", status_code=201)
def create_review(payload: PerfReviewCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    template = db.query(AppraisalTemplate).filter(
        AppraisalTemplate.id == payload.template_id, AppraisalTemplate.is_deleted == False,  # noqa: E712
    ).first()
    if not template:
        raise HTTPException(404, "Appraisal template not found")
    # guard duplicate open review for same employee+cycle+period
    dupe = db.query(PerformanceReview).filter(
        PerformanceReview.employee_id == emp.id,
        PerformanceReview.cycle == (payload.cycle or template.cycle),
        PerformanceReview.period_label == payload.period_label,
        PerformanceReview.is_deleted == False,  # noqa: E712
        PerformanceReview.status.in_(OPEN_VALUES + [S.COMPLETED.value, S.ACKNOWLEDGED.value]),
    ).first()
    if dupe:
        raise HTTPException(409, "An open review already exists for this employee, cycle and period.")
    policy_id = payload.merit_policy_id or _default_merit_policy_id(db)
    r = _create_one(db, template, emp, cycle=payload.cycle, period_label=payload.period_label,
                    due_date=payload.due_date, reviewer_id=payload.reviewer_id, actor_id=admin.id,
                    merit_policy_id=policy_id, hike_effective_from=payload.hike_effective_from)
    db.commit()
    db.refresh(r)
    return serialize(db, r)


@router.post("/bulk", status_code=201)
def bulk_create(payload: PerfReviewBulkCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    template = db.query(AppraisalTemplate).filter(
        AppraisalTemplate.id == payload.template_id, AppraisalTemplate.is_deleted == False,  # noqa: E712
    ).first()
    if not template:
        raise HTTPException(404, "Appraisal template not found")
    cycle = payload.cycle or template.cycle
    policy_id = payload.merit_policy_id or _default_merit_policy_id(db)
    created, skipped = 0, 0
    for eid in payload.employee_ids:
        emp = db.query(Employee).filter(Employee.id == eid, Employee.is_deleted == False).first()  # noqa: E712
        if not emp:
            skipped += 1
            continue
        dupe = db.query(PerformanceReview).filter(
            PerformanceReview.employee_id == emp.id,
            PerformanceReview.cycle == cycle,
            PerformanceReview.period_label == payload.period_label,
            PerformanceReview.is_deleted == False,  # noqa: E712
            PerformanceReview.status.in_(OPEN_VALUES + [S.COMPLETED.value, S.ACKNOWLEDGED.value]),
        ).first()
        if dupe:
            skipped += 1
            continue
        _create_one(db, template, emp, cycle=payload.cycle, period_label=payload.period_label,
                    due_date=payload.due_date, reviewer_id=None, actor_id=admin.id,
                    merit_policy_id=policy_id, hike_effective_from=payload.hike_effective_from)
        created += 1
    db.commit()
    return {"created": created, "skipped": skipped}


# ─────────────────────────── Detail + mutate ───────────────────────────
@router.get("/{review_id}")
def get_review(review_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return serialize(db, _load(db, review_id))


@router.patch("/{review_id}")
def update_review(review_id: UUID, payload: PerfReviewUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    r = _load(db, review_id)
    if payload.period_label is not None:
        r.period_label = payload.period_label
    if payload.cycle is not None:
        r.cycle = payload.cycle
    if payload.due_date is not None:
        r.due_date = _to_dt(payload.due_date)
    if payload.reviewer_id is not None:
        r.reviewer_id = payload.reviewer_id
    db.commit()
    db.refresh(r)
    return serialize(db, r)


@router.post("/{review_id}/self")
def admin_self_scores(review_id: UUID, payload: PerfSelfSubmit, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """HR records the SELF-assessment on the employee's behalf (e.g. digitising a
    paper form, or for staff without system access). Closes the loophole where an
    appraisal stalls forever because the employee never self-rated.
    submit=True advances DRAFT/SELF_ASSESSMENT → MANAGER_ASSESSMENT."""
    r = _load(db, review_id)
    if r.status in (S.COMPLETED.value, S.ACKNOWLEDGED.value, S.CANCELLED.value):
        raise HTTPException(409, f"Self-assessment is locked on a {r.status} review.")
    r.sections_json = apply_scores(r.sections_json, payload.sections, "self", r.rating_max)
    if payload.comments is not None:
        r.self_comments = payload.comments
    recompute(r)
    if payload.submit and r.status in (S.DRAFT.value, S.SELF_ASSESSMENT.value):
        _stamp_status(r, S.MANAGER_ASSESSMENT.value)
    db.commit()
    db.refresh(r)
    return serialize(db, r)


@router.post("/{review_id}/manager")
def admin_manager_scores(review_id: UUID, payload: PerfManagerSubmit, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """HR records manager-side ratings (acting for the manager). submit=True completes."""
    r = _load(db, review_id)
    if r.status in (S.CANCELLED.value, S.ACKNOWLEDGED.value):
        raise HTTPException(409, f"Cannot score a {r.status} review.")
    # Manager scoring presumes a self-assessment exists; if HR is scoring directly
    # from SELF_ASSESSMENT, advance the stage so the timeline stays coherent.
    if r.status in (S.DRAFT.value, S.SELF_ASSESSMENT.value):
        _stamp_status(r, S.MANAGER_ASSESSMENT.value)
    r.sections_json = apply_scores(r.sections_json, payload.sections, "manager", r.rating_max)
    if payload.comments is not None:
        r.manager_comments = payload.comments
    recompute(r)
    if payload.submit:
        _stamp_status(r, S.COMPLETED.value)
    db.commit()
    db.refresh(r)
    return serialize(db, r)


@router.post("/{review_id}/transition")
def transition(review_id: UUID, payload: PerfTransition, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    r = _load(db, review_id)
    target = payload.to
    valid = {s.value for s in S}
    if target not in valid:
        raise HTTPException(400, "Unknown status")
    if target not in ALLOWED.get(r.status, set()):
        raise HTTPException(409, f"Cannot move {r.status} → {target}")
    _stamp_status(r, target)
    # Persist a transition reason where it belongs so it's never a fake field.
    note = (payload.note or "").strip()
    if note:
        if target == S.ACKNOWLEDGED.value:
            r.ack_comments = note
        elif target == S.CANCELLED.value:
            r.manager_comments = (f"{r.manager_comments}\n" if r.manager_comments else "") + f"[Cancelled] {note}"
    db.commit()
    db.refresh(r)
    return serialize(db, r)


# ─────────────────────────── Merit / hike pipeline ───────────────────────────
def _current_annual_ctc(db: Session, emp) -> Optional[float]:
    """Latest ACTIVE compensation CTC, falling back to the Employee mirror."""
    c = (db.query(EmployeeCompensation)
         .filter(EmployeeCompensation.employee_id == emp.id,
                 EmployeeCompensation.is_deleted == False,  # noqa: E712
                 EmployeeCompensation.status == CompensationStatus.ACTIVE)
         .order_by(EmployeeCompensation.effective_from.desc()).first())
    if c and c.annual_ctc is not None:
        return float(c.annual_ctc)
    return float(emp.annual_ctc) if getattr(emp, "annual_ctc", None) is not None else None


def _band_block(db: Session, r: PerformanceReview) -> dict:
    """merit band context as a serializable block for the UI."""
    ctx = merit_band_context(db, r)
    b = ctx["band"]
    return {
        "policy_id": ctx["policy_id"], "policy_name": ctx["policy_name"],
        "score": ctx["score"], "rating_max": ctx["rating_max"], "source": ctx["source"],
        "band": b,
        "hike_min_pct": float(b["hike_min_pct"]) if b else None,
        "hike_max_pct": float(b["hike_max_pct"]) if b else None,
    }


@router.get("/{review_id}/suggestions")
def review_suggestions(review_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Auto-fill suggestions + the resolved merit band, for the profile console."""
    r = _load(db, review_id)
    return {"suggestions": suggest_ratings(db, r), "merit": _band_block(db, r)}


@router.get("/{review_id}/merit")
def review_merit(review_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """The merit band context for a review (band + allowed hike range)."""
    return _band_block(db, _load(db, review_id))


@router.post("/{review_id}/recommend")
def admin_recommend_hike(review_id: UUID, payload: PerfRecommendIn, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """HR records a hike recommendation (no-manager employees, or HR override).
    Clamped to the resolved band exactly like the manager path."""
    r = _load(db, review_id)
    if r.status != S.COMPLETED.value:
        raise HTTPException(409, "Complete the review before recommending a hike.")
    if r.hike_status == HikeStatus.APPLIED.value:
        raise HTTPException(409, "A hike has already been applied for this review.")
    ctx = merit_band_context(db, r)
    band = ctx["band"]
    if band is None:
        raise HTTPException(409, "No score to resolve a merit band. Score the review first.")
    lo, hi = float(band.get("hike_min_pct") or 0), float(band.get("hike_max_pct") or 0)
    if not (lo <= payload.hike_pct <= hi):
        raise HTTPException(409, f"Hike {payload.hike_pct}% is outside the '{band.get('label')}' band range {lo}–{hi}%.")
    r.recommended_hike_pct = payload.hike_pct
    r.recommendation_note = payload.note
    r.recommended_by_id = admin.id
    r.recommended_at = _now()
    r.final_rating_band = band.get("label")
    r.hike_status = HikeStatus.RECOMMENDED.value
    if band.get("auto_pip"):
        exists = db.query(PerformancePip).filter(
            PerformancePip.review_id == r.id, PerformancePip.is_deleted == False,  # noqa: E712
        ).first()
        if not exists:
            db.add(PerformancePip(
                employee_id=r.employee_id, review_id=r.id, manager_id=r.reviewer_id,
                title=f"PIP — {r.period_label or r.cycle}",
                reason=f"Performance rated '{band.get('label')}' in {r.period_label or r.cycle}.",
                status=PipStatus.DRAFT.value, created_by_id=admin.id,
            ))
    db.commit()
    db.refresh(r)
    return serialize(db, r)


@router.post("/{review_id}/approve-hike")
def approve_hike(review_id: UUID, payload: PerfApproveHikeIn, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """HR approves the hike and applies it to payroll as an effective-dated
    compensation revision. Idempotent — refuses to apply twice."""
    r = _load(db, review_id)
    if r.status not in (S.COMPLETED.value, S.ACKNOWLEDGED.value):
        raise HTTPException(409, "Only a completed review can have a hike approved.")
    if r.hike_status == HikeStatus.APPLIED.value or r.comp_revision_id:
        raise HTTPException(409, "A hike has already been applied for this review.")

    emp = db.query(Employee).filter(Employee.id == r.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    guard_employable(emp, "apply an appraisal hike")  # blocks EXITED/ARCHIVED/INACTIVE

    # idempotency guard on the revision_ref natural key
    ref = f"APPRAISAL_{r.id}"
    if db.query(EmployeeCompensation).filter(
        EmployeeCompensation.revision_ref == ref, EmployeeCompensation.is_deleted == False,  # noqa: E712
    ).first():
        raise HTTPException(409, "A compensation revision for this appraisal already exists.")

    ctx = merit_band_context(db, r)
    band = ctx["band"]
    pct = payload.approved_hike_pct if payload.approved_hike_pct is not None else (
        float(r.recommended_hike_pct) if r.recommended_hike_pct is not None else None)
    if pct is None:
        raise HTTPException(409, "No hike % to approve — recommend one first or pass approved_hike_pct.")
    if band is not None:
        lo, hi = float(band.get("hike_min_pct") or 0), float(band.get("hike_max_pct") or 0)
        if not (lo <= pct <= hi):
            raise HTTPException(409, f"Approved hike {pct}% is outside the '{band.get('label')}' band range {lo}–{hi}%.")

    current = _current_annual_ctc(db, emp)
    if current is None:
        raise HTTPException(409, "Employee has no current CTC on record — set compensation before applying a hike.")
    new_ctc = round(current * (1 + pct / 100.0), 2)
    eff = payload.effective_from or r.hike_effective_from or date.today()

    comp = create_compensation_revision(
        db, emp, annual_ctc=new_ctc, effective_from=eff,
        revision_reason=f"Appraisal {r.cycle} {r.period_label or ''} — {pct}% merit hike".strip(),
        revision_ref=ref, activate=True, actor_id=admin.id,
    )
    db.flush()
    r.approved_hike_pct = pct
    r.approved_by_id = admin.id
    r.approved_at = _now()
    r.hike_status = HikeStatus.APPLIED.value
    r.comp_revision_id = comp.id
    r.prev_annual_ctc = current
    r.new_annual_ctc = new_ctc
    if band is not None and not r.final_rating_band:
        r.final_rating_band = band.get("label")
    if payload.note:
        r.recommendation_note = (f"{r.recommendation_note}\n" if r.recommendation_note else "") + f"[HR] {payload.note}"
    db.commit()
    db.refresh(r)
    out = serialize(db, r)
    out["compensation"] = {"id": str(comp.id), "annual_ctc": float(comp.annual_ctc),
                           "monthly_ctc": float(comp.monthly_ctc) if comp.monthly_ctc is not None else None,
                           "effective_from": comp.effective_from}
    return out


@router.post("/{review_id}/reject-hike")
def reject_hike(review_id: UUID, payload: PerfTransition, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """HR declines the hike (e.g. budget). The review stays completed; no pay change."""
    r = _load(db, review_id)
    if r.hike_status == HikeStatus.APPLIED.value:
        raise HTTPException(409, "Cannot reject a hike that is already applied.")
    r.hike_status = HikeStatus.REJECTED.value
    note = (payload.note or "").strip()
    if note:
        r.recommendation_note = (f"{r.recommendation_note}\n" if r.recommendation_note else "") + f"[Rejected] {note}"
    db.commit()
    db.refresh(r)
    return serialize(db, r)


@router.delete("/{review_id}", status_code=204)
def delete_review(review_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    r = _load(db, review_id)
    r.is_deleted = True
    db.commit()
