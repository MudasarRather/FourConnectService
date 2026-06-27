"""HR Performance Management — self-service + manager endpoints (/hr/me/performance).

Employee POV: see my reviews, fill my self-assessment, acknowledge the final.
Manager POV: a "team" queue of reviews where I'm the reviewer, and the
manager-assessment write. Both authenticate with get_current_user (NOT superuser)
so regular employees & line managers can act without admin rights.
"""
from __future__ import annotations

from datetime import datetime, timezone, date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_user
from app.models.hr.employee import Employee
from app.models.hr.appraisal_template import AppraisalTemplate
from app.models.hr.merit_policy import MeritPolicy
from app.models.hr.performance_review import PerformanceReview, PerformanceReviewStatus, HikeStatus
from app.models.hr.performance_goal import PerformanceGoal, GoalType
from app.models.hr.performance_pip import PerformancePip, PipStatus
from app.models.hr.performance_feedback import (
    PerfFeedbackRequest, PerfFeedbackResponse, FeedbackResponseStatus,
)
from app.schemas.hr.performance_review import (
    PerfReflectionSubmit, PerfManagerSubmit, PerfAck, PerfRecommendIn, PerfTeamLaunch,
)
from app.schemas.hr.performance_goal import GoalCheckIn
from app.schemas.hr.performance_pip import PipUpdate, PipCheckIn, PipTransition, PipAck
from app.schemas.hr.performance_feedback import FeedbackResponseSubmit
from app.utils.hr.performance_service import (
    serialize, apply_scores, recompute, latest_review_summary,
    serialize_goal, serialize_feedback_request, serialize_feedback_response, feedback_rollup,
    goal_progress_from_value, derive_goal_status, recompute_objective, user_name,
    merit_band_context, snapshot_sections, emp_name, _emp_facets, serialize_pip,
)
from app.utils.hr.performance_autofill import suggest_ratings
from app.utils.hr.lifecycle_guard import SEPARATED, guard_employable

router = APIRouter(prefix="/hr/me/performance", tags=["HR — Performance (Self)"])

S = PerformanceReviewStatus


def _now():
    return datetime.now(timezone.utc)


def _my_employee(db: Session, user: User, required: bool = True):
    emp = db.query(Employee).filter(
        Employee.user_id == user.id, Employee.is_deleted == False,  # noqa: E712
    ).first()
    if not emp and required:
        raise HTTPException(404, "Your account is not linked to an employee profile. Contact HR.")
    return emp


def _summary(db: Session, emp_id) -> dict:
    rows = db.query(PerformanceReview).filter(
        PerformanceReview.employee_id == emp_id, PerformanceReview.is_deleted == False,  # noqa: E712
    ).all()
    total = len(rows)
    completed = [r for r in rows if r.status in (S.COMPLETED.value, S.ACKNOWLEDGED.value)]
    scored = [float(r.overall_score) for r in completed if r.overall_score is not None]
    action_needed = sum(1 for r in rows if r.status == S.SELF_ASSESSMENT.value)
    to_ack = sum(1 for r in rows if r.status == S.COMPLETED.value)
    latest = latest_review_summary(db, emp_id)
    return {
        "total": total,
        "completed": len(completed),
        "open": sum(1 for r in rows if r.status in (S.SELF_ASSESSMENT.value, S.MANAGER_ASSESSMENT.value, S.DRAFT.value)),
        "action_needed": action_needed,
        "to_acknowledge": to_ack,
        "average": round(sum(scored) / len(scored), 2) if scored else None,
        "latest": latest,
    }


# ─────────────────────────── Employee POV ───────────────────────────
@router.get("/")
def my_reviews(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    emp = _my_employee(db, current_user, required=False)
    if not emp:
        return {"items": [], "total": 0, "unlinked": True, "summary": None}
    rows = db.query(PerformanceReview).filter(
        PerformanceReview.employee_id == emp.id, PerformanceReview.is_deleted == False,  # noqa: E712
    ).order_by(PerformanceReview.updated_at.desc()).all()
    maps = {"desig": {}, "dept": {}}
    return {
        "items": [serialize(db, r, maps) for r in rows],
        "total": len(rows),
        "unlinked": False,
        "summary": _summary(db, emp.id),
    }


@router.get("/summary")
def my_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    emp = _my_employee(db, current_user, required=False)
    if not emp:
        return {"unlinked": True}
    return {"unlinked": False, **_summary(db, emp.id)}


def _direct_report_ids(db: Session, user: User) -> set:
    """Active employees who currently report to this user (excludes separated)."""
    rows = db.query(Employee.id).filter(
        Employee.reporting_manager_id == user.id,
        Employee.is_deleted == False,  # noqa: E712
        or_(Employee.lifecycle_state.is_(None), Employee.lifecycle_state.notin_(SEPARATED)),
    ).all()
    return {r[0] for r in rows}


def _manager_review(db: Session, review_id: UUID, user: User, claim: bool = False) -> PerformanceReview:
    """Load a review the manager may act on: they're the snapshot reviewer OR the
    subject currently reports to them (closes the stale-reviewer loophole where a
    review launched before the manager was assigned never showed up). When ``claim``
    and the manager isn't yet the reviewer, stamp them as the reviewer."""
    r = db.query(PerformanceReview).filter(
        PerformanceReview.id == review_id, PerformanceReview.is_deleted == False,  # noqa: E712
    ).first()
    if not r:
        raise HTTPException(404, "Review not found")
    is_reviewer = r.reviewer_id == user.id
    reports_to_me = False
    if not is_reviewer:
        emp = db.query(Employee).filter(Employee.id == r.employee_id).first()
        reports_to_me = bool(emp and emp.reporting_manager_id == user.id)
    if not (is_reviewer or reports_to_me):
        raise HTTPException(403, "You are not the reviewer for this review.")
    if claim and not is_reviewer:
        r.reviewer_id = user.id
    return r


@router.get("/team")
def my_team_reviews(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The manager's FULL active direct-report roster — each report with their
    current review (if any). Reports without a review still appear (so a report HR
    never opened a cycle for is visible, not silently missing). Also folds in any
    review where I'm the snapshot reviewer even if the report has since moved."""
    maps = {"desig": {}, "dept": {}}
    report_ids = _direct_report_ids(db, current_user)

    # every non-deleted review where I'm the reviewer OR the subject reports to me
    rev_q = db.query(PerformanceReview).filter(
        PerformanceReview.is_deleted == False,  # noqa: E712
        or_(PerformanceReview.reviewer_id == current_user.id,
            PerformanceReview.employee_id.in_(report_ids) if report_ids else False),
    ).order_by(PerformanceReview.created_at.desc())
    all_revs = rev_q.all()

    # pick the most relevant review per employee (latest non-cancelled, else latest)
    by_emp: dict = {}
    for r in all_revs:
        cur = by_emp.get(r.employee_id)
        if cur is None:
            by_emp[r.employee_id] = r
        else:
            cur_cancelled = cur.status == S.CANCELLED.value
            r_cancelled = r.status == S.CANCELLED.value
            if cur_cancelled and not r_cancelled:
                by_emp[r.employee_id] = r  # prefer a live review over a cancelled one

    # roster = active direct reports ∪ employees I'm reviewing (e.g. moved teams)
    roster_ids = set(report_ids) | set(by_emp.keys())
    items = []
    for eid in roster_ids:
        emp = db.query(Employee).filter(Employee.id == eid, Employee.is_deleted == False).first()  # noqa: E712
        if not emp:
            continue
        rev = by_emp.get(eid)
        item = {
            "employee_id": str(emp.id),
            "lifecycle_state": getattr(emp, "lifecycle_state", None),
            "is_direct_report": emp.id in report_ids,
            "has_review": rev is not None,
            "review": serialize(db, rev, maps) if rev else None,
            "latest": latest_review_summary(db, emp.id),
        }
        item.update(_emp_facets(db, emp, maps))
        items.append(item)

    # newest-activity first; no-review reports sink to the bottom
    items.sort(key=lambda x: (x["review"] is not None, x["review"]["updated_at"] if x["review"] else ""), reverse=True)

    OPEN = (S.DRAFT.value, S.SELF_ASSESSMENT.value, S.MANAGER_ASSESSMENT.value)
    DONE = (S.COMPLETED.value, S.ACKNOWLEDGED.value)
    to_score = sum(1 for it in items if it["review"] and it["review"]["status"] in OPEN)
    to_recommend = sum(1 for it in items if it["review"] and it["review"]["status"] in DONE
                       and (it["review"].get("hike_status") or "NONE") == "NONE")
    decided = sum(1 for it in items if it["review"] and (it["review"].get("hike_status") or "NONE") in
                  ("RECOMMENDED", "APPROVED", "APPLIED"))
    no_review = sum(1 for it in items if not it["review"])
    scored = [it["review"]["overall_score"] for it in items if it["review"] and it["review"]["overall_score"] is not None]
    return {
        "items": items,
        "total": len(items),
        "pending": to_score + to_recommend,
        "counts": {"to_score": to_score, "to_recommend": to_recommend, "decided": decided,
                   "no_review": no_review, "total": len(items)},
        "avg_score": round(sum(scored) / len(scored), 2) if scored else None,
        "manager_name": user_name(current_user),
    }


@router.get("/team/templates")
def team_templates(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Active appraisal templates (with sections) a manager can open a review from."""
    rows = (db.query(AppraisalTemplate)
            .filter(AppraisalTemplate.is_deleted == False, AppraisalTemplate.is_active == True)  # noqa: E712
            .order_by(AppraisalTemplate.name).all())
    out = []
    for t in rows:
        if not (t.sections or []):
            continue
        scale = t.rating_scale if isinstance(t.rating_scale, dict) else {}
        out.append({
            "id": str(t.id), "code": t.code, "name": t.name, "cycle": t.cycle,
            "rating_max": int(scale.get("max", 5) or 5), "section_count": len(t.sections or []),
        })
    return {"items": out, "total": len(out)}


@router.post("/team/launch", status_code=201)
def launch_team_review(payload: PerfTeamLaunch, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """A manager opens a review for one of their OWN direct reports — closes the gap
    where HR launched a cycle for some reports but not others. Reviewer = me,
    status = MANAGER_ASSESSMENT (manager owns the score; the employee may still add
    an optional reflection). Binds the org default merit policy."""
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    if emp.reporting_manager_id != current_user.id:
        raise HTTPException(403, "You can only open reviews for your own direct reports.")
    guard_employable(emp, "open a performance review")
    template = db.query(AppraisalTemplate).filter(
        AppraisalTemplate.id == payload.template_id, AppraisalTemplate.is_deleted == False,  # noqa: E712
    ).first()
    if not template or not (template.sections or []):
        raise HTTPException(404, "Appraisal template not found (or has no sections).")
    cycle = payload.cycle or template.cycle or "ANNUAL"
    dupe = db.query(PerformanceReview).filter(
        PerformanceReview.employee_id == emp.id, PerformanceReview.cycle == cycle,
        PerformanceReview.period_label == payload.period_label, PerformanceReview.is_deleted == False,  # noqa: E712
        PerformanceReview.status.in_([S.DRAFT.value, S.SELF_ASSESSMENT.value, S.MANAGER_ASSESSMENT.value,
                                      S.COMPLETED.value, S.ACKNOWLEDGED.value]),
    ).first()
    if dupe:
        raise HTTPException(409, "A review already exists for this report, cycle and period.")
    policy = db.query(MeritPolicy).filter(
        MeritPolicy.is_default == True, MeritPolicy.is_active == True, MeritPolicy.is_deleted == False,  # noqa: E712
    ).first()
    scale = template.rating_scale if isinstance(template.rating_scale, dict) else {}
    r = PerformanceReview(
        employee_id=emp.id, reviewer_id=current_user.id,
        template_id=template.id, template_code=template.code, template_name=template.name,
        cycle=cycle, period_label=payload.period_label,
        rating_max=int(scale.get("max", 5) or 5),
        rating_labels=scale.get("labels") if isinstance(scale.get("labels"), list) else None,
        sections_json=snapshot_sections(template),
        status=S.MANAGER_ASSESSMENT.value,
        due_date=datetime.combine(payload.due_date, datetime.min.time()) if payload.due_date else None,
        merit_policy_id=policy.id if policy else None,
        created_by_id=current_user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return serialize(db, r)


# ─────────────────────────── Improvement Plans (PIP) ───────────────────────────
# Employee POV: see + acknowledge my own plan. Manager POV: run my reports' plans.
# HR keeps full control (cancel / reopen / delete) in the admin module.
_PIP_VISIBLE = (PipStatus.ACTIVE.value, PipStatus.EXTENDED.value,
                PipStatus.SUCCESSFUL.value, PipStatus.UNSUCCESSFUL.value, PipStatus.CANCELLED.value)
_PIP_OPEN = (PipStatus.ACTIVE.value, PipStatus.EXTENDED.value)
_PIP_MGR_ALLOWED = {
    PipStatus.DRAFT.value: {PipStatus.ACTIVE.value},
    PipStatus.ACTIVE.value: {PipStatus.EXTENDED.value, PipStatus.SUCCESSFUL.value, PipStatus.UNSUCCESSFUL.value},
    PipStatus.EXTENDED.value: {PipStatus.SUCCESSFUL.value, PipStatus.UNSUCCESSFUL.value, PipStatus.ACTIVE.value},
}


@router.get("/pips")
def my_pips(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The employee's own improvement plans — DRAFTs stay hidden until a manager/HR activates."""
    emp = _my_employee(db, current_user, required=False)
    if not emp:
        return {"items": [], "total": 0, "unlinked": True, "active": 0, "to_acknowledge": 0}
    rows = db.query(PerformancePip).filter(
        PerformancePip.employee_id == emp.id, PerformancePip.is_deleted == False,  # noqa: E712
        PerformancePip.status.in_(_PIP_VISIBLE),
    ).order_by(PerformancePip.created_at.desc()).all()
    maps = {"desig": {}, "dept": {}}
    items = [serialize_pip(db, p, maps) for p in rows]
    active = sum(1 for p in rows if p.status in _PIP_OPEN)
    to_ack = sum(1 for p in rows if p.status in _PIP_OPEN and not p.employee_ack_at)
    return {"items": items, "total": len(items), "unlinked": False, "active": active, "to_acknowledge": to_ack}


@router.post("/pips/{pip_id:uuid}/acknowledge")
def acknowledge_my_pip(pip_id: UUID, payload: PipAck, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Employee acknowledges receipt of their active plan (records a timestamp + a
    timeline entry the manager can see). Idempotent."""
    emp = _my_employee(db, current_user)
    p = db.query(PerformancePip).filter(
        PerformancePip.id == pip_id, PerformancePip.is_deleted == False,  # noqa: E712
    ).first()
    if not p:
        raise HTTPException(404, "Plan not found")
    if p.employee_id != emp.id:
        raise HTTPException(403, "Not your plan")
    if p.status not in _PIP_OPEN:
        raise HTTPException(409, "Only an active plan can be acknowledged.")
    if not p.employee_ack_at:
        p.employee_ack_at = _now()
        note = (payload.note or "").strip()
        entry = {"at": _now().isoformat(),
                 "note": "Acknowledged the plan" + (f" — {note}" if note else ""),
                 "rating": None, "by": emp_name(emp) or user_name(current_user) or "Employee", "kind": "ack"}
        p.check_ins_json = (p.check_ins_json or []) + [entry]
        db.commit()
        db.refresh(p)
    return serialize_pip(db, p)


def _team_pip(db: Session, pip_id: UUID, user: User) -> PerformancePip:
    p = db.query(PerformancePip).filter(
        PerformancePip.id == pip_id, PerformancePip.is_deleted == False,  # noqa: E712
    ).first()
    if not p:
        raise HTTPException(404, "Plan not found")
    report_ids = _direct_report_ids(db, user)
    if not (p.manager_id == user.id or p.employee_id in report_ids):
        raise HTTPException(403, "You can only manage plans for your own direct reports.")
    return p


@router.get("/team-pips")
def my_team_pips(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Improvement plans for the manager's direct reports (incl. plans where they're the
    stamped manager even if the report has since moved)."""
    report_ids = _direct_report_ids(db, current_user)
    rows = db.query(PerformancePip).filter(
        PerformancePip.is_deleted == False,  # noqa: E712
        or_(PerformancePip.manager_id == current_user.id,
            PerformancePip.employee_id.in_(report_ids) if report_ids else False),
    ).order_by(PerformancePip.created_at.desc()).all()
    maps = {"desig": {}, "dept": {}}
    items = [serialize_pip(db, p, maps) for p in rows]
    now = _now()
    drafts = sum(1 for p in rows if p.status == PipStatus.DRAFT.value)
    active = sum(1 for p in rows if p.status in _PIP_OPEN)
    overdue = sum(1 for p in rows if p.status in _PIP_OPEN and p.end_date and p.end_date < now)
    successful = sum(1 for p in rows if p.status == PipStatus.SUCCESSFUL.value)
    return {"items": items, "total": len(items), "manager_name": user_name(current_user),
            "counts": {"drafts": drafts, "active": active, "overdue": overdue,
                       "successful": successful, "total": len(items)}}


@router.patch("/team-pips/{pip_id:uuid}")
def update_team_pip(pip_id: UUID, payload: PipUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    p = _team_pip(db, pip_id, current_user)
    data = payload.model_dump(exclude_unset=True)
    for k in ("title", "reason", "expectations", "support", "outcome"):
        if k in data:
            setattr(p, k, data[k])
    if "start_date" in data:
        p.start_date = datetime.combine(data["start_date"], datetime.min.time()) if data["start_date"] else None
    if "end_date" in data:
        p.end_date = datetime.combine(data["end_date"], datetime.min.time()) if data["end_date"] else None
    if "objectives" in data and data["objectives"] is not None:
        p.objectives_json = [
            {"title": o.get("title"), "measure": o.get("measure"), "target": o.get("target"), "status": o.get("status") or "OPEN"}
            for o in data["objectives"]
        ]
    db.commit()
    db.refresh(p)
    return serialize_pip(db, p)


@router.post("/team-pips/{pip_id:uuid}/check-in")
def team_pip_check_in(pip_id: UUID, payload: PipCheckIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    p = _team_pip(db, pip_id, current_user)
    entry = {"at": _now().isoformat(), "note": payload.note, "rating": payload.rating, "by": user_name(current_user) or "Manager"}
    p.check_ins_json = (p.check_ins_json or []) + [entry]
    db.commit()
    db.refresh(p)
    return serialize_pip(db, p)


@router.post("/team-pips/{pip_id:uuid}/transition")
def team_pip_transition(pip_id: UUID, payload: PipTransition, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    p = _team_pip(db, pip_id, current_user)
    target = payload.to
    if target not in {s.value for s in PipStatus}:
        raise HTTPException(400, "Unknown status")
    if target not in _PIP_MGR_ALLOWED.get(p.status, set()):
        raise HTTPException(409, f"As the manager you can't move {p.status} → {target}. Ask HR for that change.")
    p.status = target
    if payload.outcome is not None:
        p.outcome = payload.outcome
    if target in (PipStatus.SUCCESSFUL.value, PipStatus.UNSUCCESSFUL.value) and not p.end_date:
        p.end_date = _now()
    db.commit()
    db.refresh(p)
    return serialize_pip(db, p)


@router.get("/{review_id:uuid}")
def my_review(review_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    r = db.query(PerformanceReview).filter(
        PerformanceReview.id == review_id, PerformanceReview.is_deleted == False,  # noqa: E712
    ).first()
    if not r:
        raise HTTPException(404, "Review not found")
    emp = _my_employee(db, current_user, required=False)
    is_subject = emp and r.employee_id == emp.id
    is_reviewer = r.reviewer_id == current_user.id
    if not (is_subject or is_reviewer):
        raise HTTPException(403, "Not your review")
    out = serialize(db, r)
    out["is_subject"] = bool(is_subject)
    out["is_reviewer"] = bool(is_reviewer)
    return out


@router.patch("/{review_id:uuid}/self")
def submit_self(review_id: UUID, payload: PerfReflectionSubmit, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Employee posts an OPTIONAL, NON-SCORING self-reflection note.

    Employees never rate themselves and never advance the workflow — the official
    score is the reporting manager's (or HR's). This only records `self_comments`
    so the manager has the employee's context while scoring. The review stays in
    whatever stage it's in; the manager picks it up from there.
    """
    emp = _my_employee(db, current_user)
    r = db.query(PerformanceReview).filter(
        PerformanceReview.id == review_id, PerformanceReview.is_deleted == False,  # noqa: E712
    ).first()
    if not r:
        raise HTTPException(404, "Review not found")
    if r.employee_id != emp.id:
        raise HTTPException(403, "Not your review")
    if r.status in (S.COMPLETED.value, S.ACKNOWLEDGED.value, S.CANCELLED.value):
        raise HTTPException(409, "This review is closed — you can no longer edit your reflection.")
    if payload.comments is not None:
        r.self_comments = payload.comments
    if r.self_submitted_at is None:
        r.self_submitted_at = _now()
    db.commit()
    db.refresh(r)
    return serialize(db, r)


@router.post("/{review_id:uuid}/acknowledge")
def acknowledge(review_id: UUID, payload: PerfAck, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    emp = _my_employee(db, current_user)
    r = db.query(PerformanceReview).filter(
        PerformanceReview.id == review_id, PerformanceReview.is_deleted == False,  # noqa: E712
    ).first()
    if not r:
        raise HTTPException(404, "Review not found")
    if r.employee_id != emp.id:
        raise HTTPException(403, "Not your review")
    if r.status != S.COMPLETED.value:
        raise HTTPException(409, "Only a completed review can be acknowledged.")
    r.status = S.ACKNOWLEDGED.value
    r.employee_ack = True
    r.acknowledged_at = _now()
    if payload.comments is not None:
        r.ack_comments = payload.comments
    db.commit()
    db.refresh(r)
    return serialize(db, r)


# ─────────────────────────── Manager POV ───────────────────────────
@router.patch("/team/{review_id:uuid}/manager")
def submit_manager(review_id: UUID, payload: PerfManagerSubmit, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    r = _manager_review(db, review_id, current_user, claim=True)
    if r.status not in (S.MANAGER_ASSESSMENT.value, S.SELF_ASSESSMENT.value, S.DRAFT.value):
        raise HTTPException(409, "This review is not awaiting your assessment.")
    merged = apply_scores(r.sections_json, payload.sections, "manager", r.rating_max)
    if payload.submit:
        # Governance gate (defense-in-depth; mirrors the frontend): a manager who rates a
        # section ABOVE the system-suggested baseline (= rounded suggested_rating) must
        # justify it in that section's note. Validated on the MERGED result before any
        # mutation, so a rejection leaves the review untouched.
        baselines = {
            sug["key"]: round(float(sug["suggested_rating"]))
            for sug in suggest_ratings(db, r)
            if sug.get("key") is not None and sug.get("suggested_rating") is not None
        }
        unjustified = [
            (s.get("title") or s.get("key") or "section")
            for s in (merged or [])
            if (base := baselines.get(s.get("key"))) is not None
            and s.get("manager_rating") is not None
            and float(s["manager_rating"]) > base
            and not str(s.get("manager_comment") or "").strip()
        ]
        if unjustified:
            raise HTTPException(
                422,
                "A reason is required for raising the score above the suggested baseline in: "
                + ", ".join(unjustified) + ".",
            )
    r.sections_json = merged
    if payload.comments is not None:
        r.manager_comments = payload.comments
    recompute(r)
    if payload.submit:
        r.status = S.COMPLETED.value
        r.manager_submitted_at = _now()
        r.completed_at = _now()
    db.commit()
    db.refresh(r)
    return serialize(db, r)


@router.get("/team/{review_id:uuid}/suggestions")
def team_suggestions(review_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Auto-fill suggestions (overridable) + the resolved merit band for a manager
    scoring their report. Returns ONLY the hike% band — never salary figures
    (compensation is superuser-gated; managers recommend %, HR applies ₹)."""
    r = _manager_review(db, review_id, current_user)
    ctx = merit_band_context(db, r)
    b = ctx["band"]
    merit = {
        "policy_name": ctx["policy_name"], "score": ctx["score"], "rating_max": ctx["rating_max"],
        "source": ctx["source"], "band": b,
        "hike_min_pct": float(b["hike_min_pct"]) if b else None,
        "hike_max_pct": float(b["hike_max_pct"]) if b else None,
    }
    return {"suggestions": suggest_ratings(db, r), "merit": merit}


@router.patch("/team/{review_id:uuid}/recommend")
def recommend_hike(review_id: UUID, payload: PerfRecommendIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Manager recommends a hike %, hard-clamped to the band the (calibrated, else
    manager) score lands in. Sets the review to RECOMMENDED for HR approval."""
    r = _manager_review(db, review_id, current_user, claim=True)
    if r.status != S.COMPLETED.value:
        raise HTTPException(409, "Complete the review (submit your assessment) before recommending a hike.")
    if r.hike_status == HikeStatus.APPLIED.value:
        raise HTTPException(409, "A hike has already been applied for this review.")
    ctx = merit_band_context(db, r)
    band = ctx["band"]
    if band is None:
        raise HTTPException(409, "No score to resolve a merit band. Score the review first.")
    lo = float(band.get("hike_min_pct") or 0)
    hi = float(band.get("hike_max_pct") or 0)
    if not (lo <= payload.hike_pct <= hi):
        raise HTTPException(409, f"Hike {payload.hike_pct}% is outside the '{band.get('label')}' band range {lo}–{hi}%.")
    r.recommended_hike_pct = payload.hike_pct
    r.recommendation_note = payload.note
    r.recommended_by_id = current_user.id
    r.recommended_at = _now()
    r.final_rating_band = band.get("label")
    r.hike_status = HikeStatus.RECOMMENDED.value
    # Below-expectations bands auto-spawn a draft PIP for the manager to run.
    if band.get("auto_pip"):
        exists = db.query(PerformancePip).filter(
            PerformancePip.review_id == r.id, PerformancePip.is_deleted == False,  # noqa: E712
        ).first()
        if not exists:
            db.add(PerformancePip(
                employee_id=r.employee_id, review_id=r.id, manager_id=current_user.id,
                title=f"PIP — {r.period_label or r.cycle}",
                reason=f"Performance rated '{band.get('label')}' in {r.period_label or r.cycle}.",
                status=PipStatus.DRAFT.value, created_by_id=current_user.id,
            ))
    db.commit()
    db.refresh(r)
    return serialize(db, r)


@router.get("/team/{employee_id:uuid}/feedback")
def team_feedback(employee_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """A reporting manager's view of the 360° feedback collected ABOUT a direct report —
    including that employee's own SELF-assessment.

    Disclosure policy (corporate-standard): the **SELF** response (what the employee said
    about themselves) and the **MANAGER** response (the manager's own input) are always
    shown in full; PEER / DIRECT_REPORT / SKIP_LEVEL / EXTERNAL responses respect the
    request's anonymity flag so individual raters stay unidentifiable. The aggregated
    rollup (per-competency averages, response rate, by-relationship counts) is always
    shown. This closes the gap where a manager had no way to see their report's feedback.
    """
    emp = db.query(Employee).filter(
        Employee.id == employee_id, Employee.is_deleted == False,  # noqa: E712
    ).first()
    if not emp:
        raise HTTPException(404, "Employee not found")
    if not (current_user.is_superuser or emp.reporting_manager_id == current_user.id):
        raise HTTPException(403, "This employee does not report to you.")

    reqs = db.query(PerfFeedbackRequest).filter(
        PerfFeedbackRequest.employee_id == employee_id,
        PerfFeedbackRequest.is_deleted == False,  # noqa: E712
    ).order_by(PerfFeedbackRequest.created_at.desc()).all()

    maps = {"desig": {}, "dept": {}}
    NAMED_RELS = {"SELF", "MANAGER"}
    items = []
    for req in reqs:
        anon = bool(req.anonymous)
        responses = []
        self_response = None
        for r in (req.responses or []):
            if r.status != FeedbackResponseStatus.SUBMITTED.value:
                continue
            rel = (r.relationship_type or "").upper()
            reveal = (rel in NAMED_RELS) or (not anon)
            ser = serialize_feedback_response(r, anonymize=not reveal)
            responses.append(ser)
            if rel == "SELF":
                self_response = ser
        items.append({
            "id": str(req.id),
            "title": req.title,
            "cycle": req.cycle,
            "period_label": req.period_label,
            "status": req.status,
            "rating_max": float(req.rating_max) if req.rating_max is not None else 5,
            "competencies": req.competencies_json or [],
            "anonymous": anon,
            "due_date": req.due_date,
            "created_at": req.created_at,
            "rollup": feedback_rollup(req),
            "self_response": self_response,
            "responses": responses,
        })
    facets = _emp_facets(db, emp, maps)
    return {
        "employee_id": str(emp.id),
        "employee_name": facets.get("employee_name"),
        "designation_name": facets.get("designation_name"),
        "department_name": facets.get("department_name"),
        "items": items,
        "total": len(items),
    }


@router.get("/team/feedback-overview")
def team_feedback_overview(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """One-shot 360° feedback summary for ALL direct reports — powers the manager's
    Team Feedback board. Per report: latest request's rollup, whether the report
    submitted their self-assessment, and the manager's own pending give-duty (if any).
    Keeps the board to a single round-trip instead of N per-report calls."""
    report_ids = _direct_report_ids(db, current_user)
    if not report_ids:
        return {"items": [], "total": 0}
    maps = {"desig": {}, "dept": {}}
    items = []
    for emp_id in report_ids:
        emp = db.query(Employee).filter(Employee.id == emp_id).first()
        if not emp:
            continue
        req = db.query(PerfFeedbackRequest).filter(
            PerfFeedbackRequest.employee_id == emp_id,
            PerfFeedbackRequest.is_deleted == False,  # noqa: E712
        ).order_by(PerfFeedbackRequest.created_at.desc()).first()
        facets = _emp_facets(db, emp, maps)
        row = {
            "employee_id": str(emp_id),
            "employee_name": facets.get("employee_name"),
            "designation_name": facets.get("designation_name"),
            "department_name": facets.get("department_name"),
            "has_feedback": bool(req),
            "request_id": None, "period_label": None, "status": None,
            "rating_max": 5, "rollup": None, "self_submitted": False,
            "my_response_id": None, "my_status": None,
        }
        if req:
            self_sub = any(
                (r.relationship_type or "").upper() == "SELF" and r.status == FeedbackResponseStatus.SUBMITTED.value
                for r in (req.responses or [])
            )
            mine = next((r for r in (req.responses or []) if r.reviewer_user_id == current_user.id), None)
            row.update({
                "request_id": str(req.id),
                "period_label": req.period_label or req.cycle,
                "status": req.status,
                "rating_max": float(req.rating_max) if req.rating_max is not None else 5,
                "rollup": feedback_rollup(req),
                "self_submitted": self_sub,
                "my_response_id": str(mine.id) if mine else None,
                "my_status": mine.status if mine else None,
            })
        items.append(row)
    return {"items": items, "total": len(items)}


# ─────────────────────────── My Goals / OKRs ───────────────────────────
@router.get("/goals")
def my_goals(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    emp = _my_employee(db, current_user, required=False)
    if not emp:
        return {"items": [], "total": 0, "unlinked": True}
    rows = db.query(PerformanceGoal).filter(
        PerformanceGoal.employee_id == emp.id,
        PerformanceGoal.is_deleted == False,  # noqa: E712
        PerformanceGoal.parent_id.is_(None),
    ).order_by(PerformanceGoal.created_at.desc()).all()
    maps = {"desig": {}, "dept": {}}
    return {"items": [serialize_goal(db, g, maps, with_children=True) for g in rows], "total": len(rows), "unlinked": False}


@router.post("/goals/{goal_id:uuid}/check-in")
def my_goal_check_in(goal_id: UUID, payload: GoalCheckIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    emp = _my_employee(db, current_user)
    g = db.query(PerformanceGoal).filter(
        PerformanceGoal.id == goal_id, PerformanceGoal.is_deleted == False,  # noqa: E712
    ).first()
    if not g:
        raise HTTPException(404, "Goal not found")
    if g.employee_id != emp.id:
        raise HTTPException(403, "Not your goal")
    if g.goal_type == GoalType.OBJECTIVE.value:
        raise HTTPException(409, "Check in on a key result — the objective rolls up automatically.")
    if payload.current_value is not None:
        g.current_value = payload.current_value
    g.progress = goal_progress_from_value(g.start_value, g.current_value, g.target_value, g.metric_type)
    if payload.progress is not None and (g.metric_type or "").upper() in ("MILESTONE", "BOOLEAN"):
        g.progress = payload.progress
    if payload.status:
        g.status = payload.status
    else:
        g.status = derive_goal_status(g.progress, g.due_date, g.status)
    entry = {
        "at": _now().isoformat(), "progress": float(g.progress or 0),
        "current_value": float(g.current_value) if g.current_value is not None else None,
        "note": payload.note or "", "status": g.status, "by": user_name(current_user) or "Employee",
    }
    g.check_ins_json = (g.check_ins_json or []) + [entry]
    db.flush()
    if g.parent_id:
        parent = db.query(PerformanceGoal).filter(PerformanceGoal.id == g.parent_id).first()
        if parent:
            kids = db.query(PerformanceGoal).filter(
                PerformanceGoal.parent_id == parent.id, PerformanceGoal.is_deleted == False,  # noqa: E712
            ).all()
            recompute_objective(parent, kids)
    db.commit()
    db.refresh(g)
    return serialize_goal(db, g)


# ─────────────────────────── My 360° feedback (as a rater) ───────────────────────────
@router.get("/feedback")
def my_feedback_requests(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Feedback responses assigned to me to fill (I'm a nominated rater)."""
    responses = db.query(PerfFeedbackResponse).filter(
        PerfFeedbackResponse.reviewer_user_id == current_user.id,
    ).all()
    out = []
    maps = {"desig": {}, "dept": {}}
    for resp in responses:
        req = resp.request
        if not req or req.is_deleted:
            continue
        req_dict = serialize_feedback_request(db, req, maps, include_responses=False)
        out.append({
            "response_id": str(resp.id),
            "relationship_type": resp.relationship_type,
            "status": resp.status,
            "subject_name": req_dict.get("employee_name"),
            "subject_designation": req_dict.get("designation_name"),
            "title": req.title,
            "prompt": req.prompt,
            "competencies": req.competencies_json or [],
            "rating_max": float(req.rating_max) if req.rating_max is not None else 5,
            "due_date": req.due_date,
            "request_status": req.status,
            "anonymous": bool(req.anonymous),
            "ratings": resp.ratings_json or [],
            "strengths": resp.strengths,
            "improvements": resp.improvements,
            "comments": resp.comments,
        })
    pending = sum(1 for r in out if r["status"] == FeedbackResponseStatus.PENDING.value)
    return {"items": out, "total": len(out), "pending": pending}


@router.post("/feedback/{response_id:uuid}/submit")
def submit_feedback(response_id: UUID, payload: FeedbackResponseSubmit, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    resp = db.query(PerfFeedbackResponse).filter(PerfFeedbackResponse.id == response_id).first()
    if not resp:
        raise HTTPException(404, "Feedback assignment not found")
    if resp.reviewer_user_id != current_user.id:
        raise HTTPException(403, "Not your feedback to give")
    if resp.request and resp.request.status != "OPEN":
        raise HTTPException(409, "This feedback request is closed.")
    if payload.decline:
        resp.status = FeedbackResponseStatus.DECLINED.value
        db.commit()
        db.refresh(resp)
        return serialize_feedback_response(resp, anonymize=False)
    ratings = [{"key": r.key, "label": r.label, "rating": r.rating} for r in payload.ratings]
    resp.ratings_json = ratings
    vals = [float(r["rating"]) for r in ratings if r.get("rating") is not None]
    resp.overall_rating = round(sum(vals) / len(vals), 2) if vals else None
    if payload.strengths is not None:
        resp.strengths = payload.strengths
    if payload.improvements is not None:
        resp.improvements = payload.improvements
    if payload.comments is not None:
        resp.comments = payload.comments
    if payload.submit:
        resp.status = FeedbackResponseStatus.SUBMITTED.value
        resp.submitted_at = _now()
    db.commit()
    db.refresh(resp)
    return serialize_feedback_response(resp, anonymize=False)
