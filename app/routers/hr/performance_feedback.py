"""HR Performance — 360° / multi-rater feedback (admin / superuser).

Open a feedback request for an employee, nominate raters (self/manager/peers/
reports), then collect + roll up their responses. Responses are anonymous by
default — the rollup surfaces averages + themes, not who said what. A superuser
may pass ?reveal=true to de-anonymise for calibration.

Distinct prefix /hr/performance-feedback.
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
from app.models.hr.performance_feedback import (
    PerfFeedbackRequest, PerfFeedbackResponse,
    FeedbackRequestStatus, FeedbackResponseStatus, FeedbackRelationship,
)
from app.schemas.hr.performance_feedback import (
    FeedbackRequestCreate, FeedbackRequestUpdate, NominateBody, NomineeIn,
)
from app.utils.hr.performance_service import serialize_feedback_request, emp_name, user_name

router = APIRouter(prefix="/hr/performance-feedback", tags=["HR — Performance Feedback"])

RS = FeedbackRequestStatus


def _now():
    return datetime.now(timezone.utc)


def _to_dt(d: Optional[date]):
    return datetime.combine(d, datetime.min.time()) if d else None


def _load(db: Session, req_id: UUID) -> PerfFeedbackRequest:
    r = db.query(PerfFeedbackRequest).filter(
        PerfFeedbackRequest.id == req_id, PerfFeedbackRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not r:
        raise HTTPException(404, "Feedback request not found")
    return r


def _resolve_name(db: Session, nom: NomineeIn) -> str:
    if nom.reviewer_name:
        return nom.reviewer_name
    if nom.reviewer_user_id:
        u = db.get(User, nom.reviewer_user_id)
        if u:
            return user_name(u)
    if nom.reviewer_employee_id:
        e = db.get(Employee, nom.reviewer_employee_id)
        if e:
            return emp_name(e)
    return "Rater"


def _add_nominee(db: Session, req: PerfFeedbackRequest, nom: NomineeIn):
    # de-dupe by user / employee within the request
    for r in req.responses:
        if nom.reviewer_user_id and r.reviewer_user_id == nom.reviewer_user_id:
            return
        if nom.reviewer_employee_id and r.reviewer_employee_id == nom.reviewer_employee_id:
            return
    resp = PerfFeedbackResponse(
        request_id=req.id,
        reviewer_user_id=nom.reviewer_user_id,
        reviewer_employee_id=nom.reviewer_employee_id,
        reviewer_name=_resolve_name(db, nom),
        relationship_type=nom.relationship_type or FeedbackRelationship.PEER.value,
        status=FeedbackResponseStatus.PENDING.value,
        ratings_json=[],
    )
    db.add(resp)


@router.get("/stats")
def feedback_stats(
    cycle: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    base = db.query(PerfFeedbackRequest).filter(PerfFeedbackRequest.is_deleted == False)  # noqa: E712
    if cycle:
        base = base.filter(PerfFeedbackRequest.cycle == cycle)
    total = base.count()
    open_n = base.filter(PerfFeedbackRequest.status == RS.OPEN.value).count()
    closed_n = base.filter(PerfFeedbackRequest.status == RS.CLOSED.value).count()
    invited = db.query(func.count(PerfFeedbackResponse.id)).join(
        PerfFeedbackRequest, PerfFeedbackResponse.request_id == PerfFeedbackRequest.id,
    ).filter(PerfFeedbackRequest.is_deleted == False).scalar() or 0  # noqa: E712
    submitted = db.query(func.count(PerfFeedbackResponse.id)).join(
        PerfFeedbackRequest, PerfFeedbackResponse.request_id == PerfFeedbackRequest.id,
    ).filter(
        PerfFeedbackRequest.is_deleted == False,  # noqa: E712
        PerfFeedbackResponse.status == FeedbackResponseStatus.SUBMITTED.value,
    ).scalar() or 0
    return {
        "total": total, "open": open_n, "closed": closed_n,
        "invited": int(invited), "submitted": int(submitted),
        "response_rate": round(int(submitted) / int(invited) * 100, 1) if invited else 0,
    }


@router.get("/")
def list_requests(
    page: int = 1,
    limit: int = Query(30, ge=1, le=100),
    employee_id: Optional[UUID] = None,
    cycle: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(PerfFeedbackRequest).filter(PerfFeedbackRequest.is_deleted == False)  # noqa: E712
    if employee_id:
        q = q.filter(PerfFeedbackRequest.employee_id == employee_id)
    if cycle:
        q = q.filter(PerfFeedbackRequest.cycle == cycle)
    if status:
        q = q.filter(PerfFeedbackRequest.status == status)
    total = q.count()
    rows = q.order_by(PerfFeedbackRequest.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    maps = {"desig": {}, "dept": {}}
    return {"items": [serialize_feedback_request(db, r, maps) for r in rows], "total": total, "page": page, "limit": limit}


@router.post("/", status_code=201)
def create_request(payload: FeedbackRequestCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    comps = [{"key": c.key, "label": c.label} for c in payload.competencies]
    req = PerfFeedbackRequest(
        employee_id=emp.id, review_id=payload.review_id, cycle=payload.cycle or "ANNUAL",
        period_label=payload.period_label, title=payload.title or "360° feedback",
        prompt=payload.prompt, competencies_json=comps, rating_max=payload.rating_max or 5,
        anonymous=payload.anonymous, status=RS.OPEN.value, due_date=_to_dt(payload.due_date),
        created_by_id=admin.id,
    )
    db.add(req)
    db.flush()

    # Deterministic rater composition (corporate 360°): the reporting manager is
    # always seeded unless explicitly excluded — it is NEVER dropped just because
    # peers were nominated (the previous "if empty" logic silently lost it). The
    # self-assessment is opt-in/out. Peers/reports/skip/external come from nominees.
    # _add_nominee de-dupes by user/employee, so overlaps collapse safely.
    seeded: list[NomineeIn] = []
    if payload.include_manager and getattr(emp, "reporting_manager_id", None):
        seeded.append(NomineeIn(reviewer_user_id=emp.reporting_manager_id, relationship_type=FeedbackRelationship.MANAGER.value))
    if payload.include_self and getattr(emp, "user_id", None):
        seeded.append(NomineeIn(reviewer_user_id=emp.user_id, relationship_type=FeedbackRelationship.SELF.value))
    seeded.extend(payload.nominees)
    for nom in seeded:
        _add_nominee(db, req, nom)
    db.commit()
    db.refresh(req)
    return serialize_feedback_request(db, req, include_responses=True, force_reveal=False)


@router.get("/{request_id}")
def get_request(request_id: UUID, reveal: bool = False, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    req = _load(db, request_id)
    return serialize_feedback_request(db, req, include_responses=True, force_reveal=reveal)


@router.patch("/{request_id}")
def update_request(request_id: UUID, payload: FeedbackRequestUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    req = _load(db, request_id)
    data = payload.model_dump(exclude_unset=True)
    if "title" in data:
        req.title = data["title"]
    if "prompt" in data:
        req.prompt = data["prompt"]
    if "anonymous" in data and data["anonymous"] is not None:
        req.anonymous = data["anonymous"]
    if "due_date" in data:
        req.due_date = _to_dt(data["due_date"])
    db.commit()
    db.refresh(req)
    return serialize_feedback_request(db, req, include_responses=True)


@router.post("/{request_id}/nominate")
def nominate(request_id: UUID, payload: NominateBody, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    req = _load(db, request_id)
    if req.status != RS.OPEN.value:
        raise HTTPException(409, "Feedback request is closed.")
    for nom in payload.nominees:
        _add_nominee(db, req, nom)
    db.commit()
    db.refresh(req)
    return serialize_feedback_request(db, req, include_responses=True)


@router.post("/{request_id}/close")
def close_request(request_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    req = _load(db, request_id)
    req.status = RS.CLOSED.value
    db.commit()
    db.refresh(req)
    return serialize_feedback_request(db, req, include_responses=True)


@router.post("/{request_id}/reopen")
def reopen_request(request_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    req = _load(db, request_id)
    req.status = RS.OPEN.value
    db.commit()
    db.refresh(req)
    return serialize_feedback_request(db, req, include_responses=True)


@router.delete("/responses/{response_id}", status_code=204)
def remove_nominee(response_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    resp = db.query(PerfFeedbackResponse).filter(PerfFeedbackResponse.id == response_id).first()
    if not resp:
        raise HTTPException(404, "Response not found")
    if resp.status == FeedbackResponseStatus.SUBMITTED.value:
        raise HTTPException(409, "Cannot remove a rater who has already submitted.")
    db.delete(resp)
    db.commit()


@router.delete("/{request_id}", status_code=204)
def delete_request(request_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    req = _load(db, request_id)
    req.is_deleted = True
    db.commit()
