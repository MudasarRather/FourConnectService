"""HR Recruitment router — Phase 4 hiring.

Endpoints under /api/hr/recruitment/* for the complete corporate flow:
  Requisitions → Positions → Candidates → Applications → Interviews → Offers
Plus dashboard, pipeline, and analytics summaries.

Auth: every endpoint requires `get_current_superuser` (admin panel only).
"""
import math
from datetime import datetime, timedelta, date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import and_, or_, func, desc
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.recruitment import (
    JobRequisition, JobPosition, Candidate, Application,
    InterviewPanel, Interview, InterviewFeedback, Offer,
    RequisitionStatus, PositionStatus, CandidateStatus,
    ApplicationStage, InterviewStatus, OfferStatus,
)
from app.models.hr.department import Department
from app.models.hr.designation import Designation
from app.models.hr.location import WorkLocation
from app.schemas.hr.recruitment import (
    RequisitionCreate, RequisitionUpdate, RequisitionResponse,
    RequisitionListResponse, RequisitionApproval,
    PositionCreate, PositionUpdate, PositionResponse, PositionListResponse,
    PositionCloseRequest,
    CandidateCreate, CandidateUpdate, CandidateResponse, CandidateListResponse,
    ApplicationCreate, ApplicationUpdate, ApplicationResponse,
    ApplicationListResponse, ApplicationStageChange,
    InterviewPanelCreate, InterviewPanelUpdate, InterviewPanelResponse,
    InterviewCreate, InterviewUpdate, InterviewResponse, InterviewListResponse,
    InterviewFeedbackCreate, InterviewFeedbackResponse,
    OfferCreate, OfferUpdate, OfferResponse, OfferListResponse,
    OfferResponseAction, OnboardingPrefillResponse,
    RecruitmentDashboardStats, RecruitmentDashboardData,
    FunnelStage, DepartmentHiring, MonthlyTrendItem,
    PipelineCard, PipelineStage,
)
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/recruitment", tags=["HR — Recruitment"])


# ──────────────────────────────────────────────────────────────────────────────
# Sequence helpers (auto-codes)
# ──────────────────────────────────────────────────────────────────────────────

def _next_code(db: Session, prefix: str, model, code_col: str, width: int = 4) -> str:
    """Generate the next code like REQ0001 / POS0001 / CAN0001 ...
    Falls back to counting rows if no row exists yet. Race-tolerant for low
    write volume — the unique constraint at the DB will reject duplicates.
    """
    last = db.query(model).order_by(desc(getattr(model, code_col))).first()
    n = 1
    if last:
        raw = getattr(last, code_col) or ""
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if digits:
            n = int(digits) + 1
    return f"{prefix}{n:0{width}d}"


def _paginate(query, page: int, limit: int):
    total = query.count()
    items = query.offset((page - 1) * limit).limit(limit).all()
    return items, total, max(1, math.ceil(total / max(1, limit)))


def _user_label(u) -> Optional[str]:
    if not u:
        return None
    return getattr(u, "full_name", None) or getattr(u, "email", None)


# ──────────────────────────────────────────────────────────────────────────────
# REQUISITIONS
# ──────────────────────────────────────────────────────────────────────────────

def _requisition_to_response(req: JobRequisition) -> dict:
    data = {c.name: getattr(req, c.name) for c in req.__table__.columns}
    data["department_name"] = req.department.name if req.department else None
    data["designation_name"] = req.designation.name if req.designation else None
    data["location_name"] = req.location.name if req.location else None
    data["hiring_manager_name"] = _user_label(req.hiring_manager)
    data["requested_by_name"] = _user_label(req.requested_by)
    return data


@router.get("/requisitions", response_model=RequisitionListResponse)
def list_requisitions(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    status_filter: Optional[RequisitionStatus] = Query(None, alias="status"),
    department_id: Optional[UUID] = None,
    priority: Optional[str] = None,
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(JobRequisition).options(
        joinedload(JobRequisition.department),
        joinedload(JobRequisition.designation),
        joinedload(JobRequisition.location),
        joinedload(JobRequisition.hiring_manager),
        joinedload(JobRequisition.requested_by),
    )
    if not include_deleted:
        q = q.filter(JobRequisition.is_deleted == False)  # noqa: E712
    if search:
        s = f"%{search.lower()}%"
        q = q.filter(or_(
            func.lower(JobRequisition.job_title).like(s),
            func.lower(JobRequisition.requisition_number).like(s),
        ))
    if status_filter:
        q = q.filter(JobRequisition.status == status_filter)
    if department_id:
        q = q.filter(JobRequisition.department_id == department_id)
    if priority:
        q = q.filter(JobRequisition.priority == priority)
    q = q.order_by(desc(JobRequisition.created_at))

    items, total, pages = _paginate(q, page, limit)
    return {
        "items": [_requisition_to_response(i) for i in items],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": pages,
    }


@router.post("/requisitions", response_model=RequisitionResponse, status_code=201)
def create_requisition(
    payload: RequisitionCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    data = payload.model_dump(exclude_unset=True)
    req = JobRequisition(
        **data,
        requisition_number=_next_code(db, "REQ", JobRequisition, "requisition_number"),
        requested_by_id=admin.id,
        status=RequisitionStatus.DRAFT,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return _requisition_to_response(req)


@router.get("/requisitions/{rid}", response_model=RequisitionResponse)
def get_requisition(
    rid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    req = db.query(JobRequisition).options(
        joinedload(JobRequisition.department),
        joinedload(JobRequisition.designation),
        joinedload(JobRequisition.location),
        joinedload(JobRequisition.hiring_manager),
        joinedload(JobRequisition.requested_by),
    ).filter(JobRequisition.id == rid).first()
    if not req:
        raise HTTPException(404, "Requisition not found")
    return _requisition_to_response(req)


@router.patch("/requisitions/{rid}", response_model=RequisitionResponse)
def update_requisition(
    rid: UUID,
    payload: RequisitionUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    req = db.query(JobRequisition).filter(JobRequisition.id == rid).first()
    if not req:
        raise HTTPException(404, "Requisition not found")
    if req.status in (RequisitionStatus.APPROVED, RequisitionStatus.CONVERTED, RequisitionStatus.ARCHIVED):
        raise HTTPException(409, f"Cannot edit requisition in status {req.status}")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(req, k, v)
    db.commit()
    db.refresh(req)
    return _requisition_to_response(req)


@router.post("/requisitions/{rid}/submit", response_model=RequisitionResponse)
def submit_requisition(
    rid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    req = db.query(JobRequisition).filter(JobRequisition.id == rid).first()
    if not req:
        raise HTTPException(404, "Requisition not found")
    if req.status != RequisitionStatus.DRAFT:
        raise HTTPException(409, f"Only DRAFT requisitions can be submitted (current: {req.status})")
    req.status = RequisitionStatus.PENDING_APPROVAL
    db.commit()
    db.refresh(req)
    return _requisition_to_response(req)


@router.post("/requisitions/{rid}/decision", response_model=RequisitionResponse)
def decide_requisition(
    rid: UUID,
    payload: RequisitionApproval,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    req = db.query(JobRequisition).filter(JobRequisition.id == rid).first()
    if not req:
        raise HTTPException(404, "Requisition not found")
    if req.status != RequisitionStatus.PENDING_APPROVAL:
        raise HTTPException(409, "Only pending requisitions can be approved/rejected")
    if payload.approve:
        req.status = RequisitionStatus.APPROVED
        req.approved_by_id = admin.id
        req.approved_at = datetime.utcnow()
        req.rejected_reason = None
    else:
        req.status = RequisitionStatus.REJECTED
        req.rejected_reason = payload.reason
    db.commit()
    db.refresh(req)
    return _requisition_to_response(req)


@router.post("/requisitions/{rid}/convert", response_model=PositionResponse, status_code=201)
def convert_requisition(
    rid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    req = db.query(JobRequisition).filter(JobRequisition.id == rid).first()
    if not req:
        raise HTTPException(404, "Requisition not found")
    if req.status != RequisitionStatus.APPROVED:
        raise HTTPException(409, "Only APPROVED requisitions can be converted to an Open Position")

    pos = JobPosition(
        job_code=_next_code(db, "POS", JobPosition, "job_code"),
        requisition_id=req.id,
        job_title=req.job_title,
        department_id=req.department_id,
        designation_id=req.designation_id,
        grade_id=req.grade_id,
        location_id=req.location_id,
        hiring_manager_id=req.hiring_manager_id,
        openings_count=req.number_of_openings,
        experience_min_years=req.experience_min_years,
        experience_max_years=req.experience_max_years,
        salary_min=req.budgeted_salary_min,
        salary_max=req.budgeted_salary_max,
        currency=req.currency,
        employment_type=req.employment_type,
        skills_required=req.required_skills or [],
        qualification=req.qualification,
        job_description=req.job_description,
        status=PositionStatus.DRAFT,
        created_by_id=admin.id,
    )
    db.add(pos)
    req.status = RequisitionStatus.CONVERTED
    db.commit()
    db.refresh(pos)
    return _position_to_response(pos)


@router.delete("/requisitions/{rid}", status_code=204)
def archive_requisition(
    rid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    req = db.query(JobRequisition).filter(JobRequisition.id == rid).first()
    if not req:
        raise HTTPException(404, "Requisition not found")
    req.is_deleted = True
    req.status = RequisitionStatus.ARCHIVED
    db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# POSITIONS
# ──────────────────────────────────────────────────────────────────────────────

def _position_to_response(pos: JobPosition) -> dict:
    data = {c.name: getattr(pos, c.name) for c in pos.__table__.columns}
    data["department_name"] = pos.department.name if pos.department else None
    data["designation_name"] = pos.designation.name if pos.designation else None
    data["location_name"] = pos.location.name if pos.location else None
    data["recruiter_name"] = _user_label(pos.recruiter)
    data["hiring_manager_name"] = _user_label(pos.hiring_manager)
    # Count is computed via subquery or relationship — keep cheap here.
    try:
        data["applications_count"] = len([a for a in pos.applications if not a.is_deleted])
    except Exception:
        data["applications_count"] = 0
    return data


def _apply_position_fill(pos: JobPosition, joined: int) -> bool:
    """Sync a position's filled_count to its JOINED-application count and
    auto-close it once every opening is filled. Returns True if anything
    changed so the caller knows whether to commit.

    Only OPEN/ON_HOLD positions are auto-closed — a DRAFT or already-CLOSED
    (incl. manually closed) position is left untouched, so a manual close can
    never be silently reopened. Idempotent: safe to call on every load.
    """
    changed = False
    if pos.filled_count != joined:
        pos.filled_count = joined
        changed = True
    openings = pos.openings_count or 0
    if openings > 0 and joined >= openings and pos.status in (
        PositionStatus.OPEN, PositionStatus.ON_HOLD,
    ):
        pos.status = PositionStatus.CLOSED
        changed = True
    return changed


@router.get("/positions", response_model=PositionListResponse)
def list_positions(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    status_filter: Optional[PositionStatus] = Query(None, alias="status"),
    department_id: Optional[UUID] = None,
    work_mode: Optional[str] = None,
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(JobPosition).options(
        joinedload(JobPosition.department),
        joinedload(JobPosition.designation),
        joinedload(JobPosition.location),
        joinedload(JobPosition.recruiter),
        joinedload(JobPosition.hiring_manager),
        joinedload(JobPosition.applications),
    )
    if not include_deleted:
        q = q.filter(JobPosition.is_deleted == False)  # noqa: E712
    if search:
        s = f"%{search.lower()}%"
        q = q.filter(or_(
            func.lower(JobPosition.job_title).like(s),
            func.lower(JobPosition.job_code).like(s),
        ))
    if status_filter:
        q = q.filter(JobPosition.status == status_filter)
    if department_id:
        q = q.filter(JobPosition.department_id == department_id)
    if work_mode:
        q = q.filter(JobPosition.work_mode == work_mode)
    q = q.order_by(desc(JobPosition.created_at))

    items, total, pages = _paginate(q, page, limit)

    # Self-heal: reconcile filled_count and auto-close any fully-filled position
    # whose status wasn't transitioned at join time (e.g. positions filled
    # before auto-close existed). Uses the already-joinedloaded applications
    # relationship, so no extra queries; commits only when something changed.
    dirty = False
    for pos in items:
        joined = sum(
            1 for a in (pos.applications or [])
            if not a.is_deleted and a.current_stage == ApplicationStage.JOINED
        )
        if _apply_position_fill(pos, joined):
            dirty = True
    if dirty:
        db.commit()

    return {
        "items": [_position_to_response(i) for i in items],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": pages,
    }


@router.post("/positions", response_model=PositionResponse, status_code=201)
def create_position(
    payload: PositionCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    pos = JobPosition(
        **payload.model_dump(exclude_unset=True),
        job_code=_next_code(db, "POS", JobPosition, "job_code"),
        created_by_id=admin.id,
        status=PositionStatus.DRAFT,
    )
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return _position_to_response(pos)


@router.get("/positions/{pid}", response_model=PositionResponse)
def get_position(
    pid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    pos = db.query(JobPosition).options(
        joinedload(JobPosition.department),
        joinedload(JobPosition.designation),
        joinedload(JobPosition.location),
        joinedload(JobPosition.recruiter),
        joinedload(JobPosition.hiring_manager),
        joinedload(JobPosition.applications),
    ).filter(JobPosition.id == pid).first()
    if not pos:
        raise HTTPException(404, "Position not found")
    return _position_to_response(pos)


@router.patch("/positions/{pid}", response_model=PositionResponse)
def update_position(
    pid: UUID,
    payload: PositionUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    pos = db.query(JobPosition).filter(JobPosition.id == pid).first()
    if not pos:
        raise HTTPException(404, "Position not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(pos, k, v)
    db.commit()
    db.refresh(pos)
    return _position_to_response(pos)


@router.post("/positions/{pid}/publish", response_model=PositionResponse)
def publish_position(
    pid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    pos = db.query(JobPosition).filter(JobPosition.id == pid).first()
    if not pos:
        raise HTTPException(404, "Position not found")
    pos.status = PositionStatus.OPEN
    if not pos.publish_date:
        pos.publish_date = date.today()
    db.commit()
    db.refresh(pos)
    return _position_to_response(pos)


@router.post("/positions/{pid}/hold", response_model=PositionResponse)
def hold_position(
    pid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    pos = db.query(JobPosition).filter(JobPosition.id == pid).first()
    if not pos:
        raise HTTPException(404, "Position not found")
    pos.status = PositionStatus.ON_HOLD
    db.commit()
    db.refresh(pos)
    return _position_to_response(pos)


@router.post("/positions/{pid}/close", response_model=PositionResponse)
def close_position(
    pid: UUID,
    payload: PositionCloseRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    pos = db.query(JobPosition).filter(JobPosition.id == pid).first()
    if not pos:
        raise HTTPException(404, "Position not found")
    # State-machine guard: only a live (OPEN / ON_HOLD) position can be closed.
    # DRAFT positions are archived/published instead; an already-CLOSED or
    # ARCHIVED position is a no-op error so a stale UI can't double-close.
    if pos.status not in (PositionStatus.OPEN, PositionStatus.ON_HOLD):
        raise HTTPException(
            409, f"Only OPEN or ON_HOLD positions can be closed (current status: {pos.status.value})"
        )
    pos.status = PositionStatus.CLOSED
    pos.close_reason = payload.reason.value
    pos.close_note = (payload.note or "").strip() or None
    pos.closed_at = datetime.utcnow()
    pos.closed_by_id = admin.id
    db.commit()
    db.refresh(pos)
    return _position_to_response(pos)


@router.delete("/positions/{pid}", status_code=204)
def archive_position(
    pid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    pos = db.query(JobPosition).filter(JobPosition.id == pid).first()
    if not pos:
        raise HTTPException(404, "Position not found")
    pos.is_deleted = True
    pos.status = PositionStatus.ARCHIVED
    db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# CANDIDATES
# ──────────────────────────────────────────────────────────────────────────────

def _candidate_to_response(c: Candidate) -> dict:
    data = {col.name: getattr(c, col.name) for col in c.__table__.columns}
    try:
        data["applications_count"] = len([a for a in c.applications if not a.is_deleted])
    except Exception:
        data["applications_count"] = 0
    return data


@router.get("/candidates", response_model=CandidateListResponse)
def list_candidates(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    status_filter: Optional[CandidateStatus] = Query(None, alias="status"),
    in_talent_pool: Optional[bool] = None,
    source: Optional[str] = None,
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(Candidate).options(joinedload(Candidate.applications))
    if not include_deleted:
        q = q.filter(Candidate.is_deleted == False)  # noqa: E712
    if search:
        s = f"%{search.lower()}%"
        q = q.filter(or_(
            func.lower(Candidate.full_name).like(s),
            func.lower(Candidate.email).like(s),
            func.lower(Candidate.candidate_code).like(s),
            func.lower(Candidate.mobile).like(s),
        ))
    if status_filter:
        q = q.filter(Candidate.status == status_filter)
    if in_talent_pool is not None:
        q = q.filter(Candidate.is_in_talent_pool == in_talent_pool)
    if source:
        q = q.filter(Candidate.source == source)
    q = q.order_by(desc(Candidate.created_at))

    items, total, pages = _paginate(q, page, limit)
    return {
        "items": [_candidate_to_response(i) for i in items],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": pages,
    }


@router.post("/candidates", response_model=CandidateResponse, status_code=201)
def create_candidate(
    payload: CandidateCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    cand = Candidate(
        **payload.model_dump(exclude_unset=True),
        candidate_code=_next_code(db, "CAN", Candidate, "candidate_code"),
        created_by_id=admin.id,
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)
    return _candidate_to_response(cand)


@router.get("/candidates/{cid}", response_model=CandidateResponse)
def get_candidate(
    cid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    cand = db.query(Candidate).options(joinedload(Candidate.applications)).filter(Candidate.id == cid).first()
    if not cand:
        raise HTTPException(404, "Candidate not found")
    return _candidate_to_response(cand)


@router.patch("/candidates/{cid}", response_model=CandidateResponse)
def update_candidate(
    cid: UUID,
    payload: CandidateUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    cand = db.query(Candidate).filter(Candidate.id == cid).first()
    if not cand:
        raise HTTPException(404, "Candidate not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(cand, k, v)
    db.commit()
    db.refresh(cand)
    return _candidate_to_response(cand)


@router.delete("/candidates/{cid}", status_code=204)
def archive_candidate(
    cid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    cand = db.query(Candidate).filter(Candidate.id == cid).first()
    if not cand:
        raise HTTPException(404, "Candidate not found")
    cand.is_deleted = True
    cand.status = CandidateStatus.ARCHIVED
    db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# APPLICATIONS
# ──────────────────────────────────────────────────────────────────────────────

def _application_to_response(a: Application) -> dict:
    data = {col.name: getattr(a, col.name) for col in a.__table__.columns}
    if a.candidate:
        data["candidate_name"] = a.candidate.full_name
        data["candidate_email"] = a.candidate.email
    if a.position:
        data["position_title"] = a.position.job_title
        data["position_code"] = a.position.job_code
        if a.position.department:
            data["department_name"] = a.position.department.name
    data["recruiter_name"] = _user_label(a.recruiter)
    return data


@router.get("/applications", response_model=ApplicationListResponse)
def list_applications(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    stage: Optional[ApplicationStage] = None,
    position_id: Optional[UUID] = None,
    candidate_id: Optional[UUID] = None,
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(Application).options(
        joinedload(Application.candidate),
        joinedload(Application.position).joinedload(JobPosition.department),
        joinedload(Application.recruiter),
    )
    if not include_deleted:
        q = q.filter(Application.is_deleted == False)  # noqa: E712
    if stage:
        q = q.filter(Application.current_stage == stage)
    if position_id:
        q = q.filter(Application.position_id == position_id)
    if candidate_id:
        q = q.filter(Application.candidate_id == candidate_id)
    if search:
        s = f"%{search.lower()}%"
        q = q.join(Candidate, Application.candidate_id == Candidate.id, isouter=True)\
             .join(JobPosition, Application.position_id == JobPosition.id, isouter=True)\
             .filter(or_(
                 func.lower(Application.application_code).like(s),
                 func.lower(Candidate.full_name).like(s),
                 func.lower(JobPosition.job_title).like(s),
             ))
    q = q.order_by(desc(Application.applied_date))

    items, total, pages = _paginate(q, page, limit)
    return {
        "items": [_application_to_response(i) for i in items],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": pages,
    }


@router.post("/applications", response_model=ApplicationResponse, status_code=201)
def create_application(
    payload: ApplicationCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    cand = db.query(Candidate).filter(Candidate.id == payload.candidate_id, Candidate.is_deleted == False).first()  # noqa: E712
    if not cand:
        raise HTTPException(404, "Candidate not found")
    pos = db.query(JobPosition).filter(JobPosition.id == payload.position_id, JobPosition.is_deleted == False).first()  # noqa: E712
    if not pos:
        raise HTTPException(404, "Position not found")
    existing = db.query(Application).filter(
        Application.candidate_id == payload.candidate_id,
        Application.position_id == payload.position_id,
        Application.is_deleted == False,  # noqa: E712
    ).first()
    if existing:
        raise HTTPException(409, "Candidate has already applied for this position")

    app_obj = Application(
        **payload.model_dump(exclude_unset=True),
        application_code=_next_code(db, "APP", Application, "application_code"),
        current_stage=ApplicationStage.APPLIED,
    )
    db.add(app_obj)
    db.commit()
    db.refresh(app_obj)
    return _application_to_response(app_obj)


@router.get("/applications/{aid}", response_model=ApplicationResponse)
def get_application(
    aid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    a = db.query(Application).options(
        joinedload(Application.candidate),
        joinedload(Application.position).joinedload(JobPosition.department),
        joinedload(Application.recruiter),
    ).filter(Application.id == aid).first()
    if not a:
        raise HTTPException(404, "Application not found")
    return _application_to_response(a)


@router.patch("/applications/{aid}", response_model=ApplicationResponse)
def update_application(
    aid: UUID,
    payload: ApplicationUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    a = db.query(Application).filter(Application.id == aid).first()
    if not a:
        raise HTTPException(404, "Application not found")
    data = payload.model_dump(exclude_unset=True)
    if "current_stage" in data and data["current_stage"] != a.current_stage:
        a.stage_changed_at = datetime.utcnow()
    for k, v in data.items():
        setattr(a, k, v)
    db.commit()
    db.refresh(a)
    return _application_to_response(a)


@router.post("/applications/{aid}/stage", response_model=ApplicationResponse)
def change_application_stage(
    aid: UUID,
    payload: ApplicationStageChange,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    a = db.query(Application).options(joinedload(Application.candidate)).filter(Application.id == aid).first()
    if not a:
        raise HTTPException(404, "Application not found")
    if a.current_stage == payload.stage:
        return _application_to_response(a)
    a.current_stage = payload.stage
    a.stage_changed_at = datetime.utcnow()
    if payload.notes:
        a.notes = (a.notes or "") + f"\n[{datetime.utcnow().isoformat()}] {payload.notes}"
    if payload.stage == ApplicationStage.REJECTED:
        a.rejection_reason = payload.rejection_reason
        if a.candidate:
            a.candidate.status = CandidateStatus.REJECTED
    elif payload.stage == ApplicationStage.SHORTLISTED:
        if a.candidate:
            a.candidate.status = CandidateStatus.SHORTLISTED
    elif payload.stage == ApplicationStage.INTERVIEW:
        if a.candidate:
            a.candidate.status = CandidateStatus.INTERVIEW
    elif payload.stage == ApplicationStage.SELECTED:
        if a.candidate:
            a.candidate.status = CandidateStatus.SELECTED
    elif payload.stage == ApplicationStage.OFFER:
        if a.candidate:
            a.candidate.status = CandidateStatus.OFFERED
    elif payload.stage == ApplicationStage.JOINED:
        if a.candidate:
            a.candidate.status = CandidateStatus.JOINED
    db.commit()
    db.refresh(a)
    return _application_to_response(a)


@router.delete("/applications/{aid}", status_code=204)
def archive_application(
    aid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    a = db.query(Application).filter(Application.id == aid).first()
    if not a:
        raise HTTPException(404, "Application not found")
    a.is_deleted = True
    db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# INTERVIEW PANELS
# ──────────────────────────────────────────────────────────────────────────────

def _panel_to_response(p: InterviewPanel) -> dict:
    data = {c.name: getattr(p, c.name) for c in p.__table__.columns}
    data["department_name"] = p.department.name if p.department else None
    data["member_count"] = len(p.members or [])
    return data


@router.get("/panels", response_model=List[InterviewPanelResponse])
def list_panels(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(InterviewPanel).options(joinedload(InterviewPanel.department)).filter(InterviewPanel.is_deleted == False)  # noqa: E712
    if not include_inactive:
        q = q.filter(InterviewPanel.is_active == True)  # noqa: E712
    return [_panel_to_response(p) for p in q.order_by(InterviewPanel.name).all()]


@router.post("/panels", response_model=InterviewPanelResponse, status_code=201)
def create_panel(
    payload: InterviewPanelCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    data = payload.model_dump(exclude_unset=True)
    members = data.pop("members", None)
    if members is not None:
        data["members"] = [m.model_dump() if hasattr(m, "model_dump") else m for m in members]
    p = InterviewPanel(**data)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _panel_to_response(p)


@router.patch("/panels/{pid}", response_model=InterviewPanelResponse)
def update_panel(
    pid: UUID,
    payload: InterviewPanelUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    p = db.query(InterviewPanel).filter(InterviewPanel.id == pid).first()
    if not p:
        raise HTTPException(404, "Panel not found")
    data = payload.model_dump(exclude_unset=True)
    if "members" in data and data["members"] is not None:
        data["members"] = [m.model_dump() if hasattr(m, "model_dump") else m for m in data["members"]]
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return _panel_to_response(p)


@router.delete("/panels/{pid}", status_code=204)
def delete_panel(
    pid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    p = db.query(InterviewPanel).filter(InterviewPanel.id == pid).first()
    if not p:
        raise HTTPException(404, "Panel not found")
    p.is_deleted = True
    db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# INTERVIEWS
# ──────────────────────────────────────────────────────────────────────────────

def _interview_to_response(iv: Interview) -> dict:
    data = {c.name: getattr(iv, c.name) for c in iv.__table__.columns}
    if iv.application:
        if iv.application.candidate:
            data["candidate_name"] = iv.application.candidate.full_name
        if iv.application.position:
            data["position_title"] = iv.application.position.job_title
    data["panel_name"] = iv.panel.name if iv.panel else None
    try:
        data["feedback_count"] = len(iv.feedback_entries or [])
    except Exception:
        data["feedback_count"] = 0
    return data


@router.get("/interviews", response_model=InterviewListResponse)
def list_interviews(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status_filter: Optional[InterviewStatus] = Query(None, alias="status"),
    upcoming_only: bool = False,
    application_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(Interview).options(
        joinedload(Interview.application).joinedload(Application.candidate),
        joinedload(Interview.application).joinedload(Application.position),
        joinedload(Interview.panel),
        joinedload(Interview.feedback_entries),
    ).filter(Interview.is_deleted == False)  # noqa: E712
    if status_filter:
        q = q.filter(Interview.status == status_filter)
    if upcoming_only:
        q = q.filter(Interview.scheduled_at >= datetime.utcnow(), Interview.status == InterviewStatus.SCHEDULED)
    if application_id:
        q = q.filter(Interview.application_id == application_id)
    q = q.order_by(Interview.scheduled_at.asc())

    items, total, pages = _paginate(q, page, limit)
    return {
        "items": [_interview_to_response(i) for i in items],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": pages,
    }


@router.post("/interviews", response_model=InterviewResponse, status_code=201)
def create_interview(
    payload: InterviewCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    app_obj = db.query(Application).filter(Application.id == payload.application_id).first()
    if not app_obj:
        raise HTTPException(404, "Application not found")

    data = payload.model_dump(exclude_unset=True)
    interviewers = data.pop("interviewers", None)
    if interviewers is not None:
        data["interviewers"] = [i.model_dump() if hasattr(i, "model_dump") else i for i in interviewers]

    iv = Interview(
        **data,
        interview_code=_next_code(db, "INT", Interview, "interview_code"),
        status=InterviewStatus.SCHEDULED,
        created_by_id=admin.id,
    )
    db.add(iv)

    if app_obj.current_stage in (ApplicationStage.APPLIED, ApplicationStage.SCREENING, ApplicationStage.SHORTLISTED):
        app_obj.current_stage = ApplicationStage.INTERVIEW
        app_obj.stage_changed_at = datetime.utcnow()
        if app_obj.candidate:
            app_obj.candidate.status = CandidateStatus.INTERVIEW

    db.commit()
    db.refresh(iv)
    return _interview_to_response(iv)


@router.get("/interviews/{iid}", response_model=InterviewResponse)
def get_interview(
    iid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    iv = db.query(Interview).options(
        joinedload(Interview.application).joinedload(Application.candidate),
        joinedload(Interview.application).joinedload(Application.position),
        joinedload(Interview.panel),
        joinedload(Interview.feedback_entries),
    ).filter(Interview.id == iid).first()
    if not iv:
        raise HTTPException(404, "Interview not found")
    return _interview_to_response(iv)


@router.patch("/interviews/{iid}", response_model=InterviewResponse)
def update_interview(
    iid: UUID,
    payload: InterviewUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    iv = db.query(Interview).filter(Interview.id == iid).first()
    if not iv:
        raise HTTPException(404, "Interview not found")
    data = payload.model_dump(exclude_unset=True)
    if "interviewers" in data and data["interviewers"] is not None:
        data["interviewers"] = [i.model_dump() if hasattr(i, "model_dump") else i for i in data["interviewers"]]
    for k, v in data.items():
        setattr(iv, k, v)
    db.commit()
    db.refresh(iv)
    return _interview_to_response(iv)


@router.delete("/interviews/{iid}", status_code=204)
def delete_interview(
    iid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    iv = db.query(Interview).filter(Interview.id == iid).first()
    if not iv:
        raise HTTPException(404, "Interview not found")
    iv.is_deleted = True
    iv.status = InterviewStatus.CANCELLED
    db.commit()


@router.post("/interviews/{iid}/feedback", response_model=InterviewFeedbackResponse, status_code=201)
def submit_feedback(
    iid: UUID,
    payload: InterviewFeedbackCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    iv = db.query(Interview).filter(Interview.id == iid).first()
    if not iv:
        raise HTTPException(404, "Interview not found")
    existing = db.query(InterviewFeedback).filter(
        InterviewFeedback.interview_id == iid,
        InterviewFeedback.interviewer_id == admin.id,
    ).first()
    if existing:
        for k, v in payload.model_dump(exclude_unset=True).items():
            setattr(existing, k, v)
        existing.submitted_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        fb = existing
    else:
        fb = InterviewFeedback(
            **payload.model_dump(exclude_unset=True),
            interview_id=iid,
            interviewer_id=admin.id,
        )
        db.add(fb)
        db.commit()
        db.refresh(fb)

    if iv.status == InterviewStatus.SCHEDULED:
        iv.status = InterviewStatus.COMPLETED
        db.commit()

    # ── Auto-progress the linked application based on the feedback recommendation.
    #    STRONG_HIRE / HIRE   → SELECTED  (ready for offer)
    #    STRONG_NO_HIRE / NO_HIRE → REJECTED
    #    HOLD                 → leave stage unchanged
    rec = (payload.recommendation or getattr(fb, "recommendation", None) or "").upper()
    if rec and iv.application_id:
        app = db.query(Application).filter(Application.id == iv.application_id).first()
        if app:
            new_stage = None
            if rec in ("STRONG_HIRE", "HIRE"):
                # Only advance forward — don't downgrade an app that was already SELECTED/OFFER/JOINED
                if app.current_stage in (
                    ApplicationStage.APPLIED,
                    ApplicationStage.SCREENING,
                    ApplicationStage.SHORTLISTED,
                    ApplicationStage.INTERVIEW,
                ):
                    new_stage = ApplicationStage.SELECTED
            elif rec in ("STRONG_NO_HIRE", "NO_HIRE"):
                # Don't override a terminal state
                if app.current_stage not in (
                    ApplicationStage.JOINED,
                    ApplicationStage.REJECTED,
                    ApplicationStage.WITHDRAWN,
                ):
                    new_stage = ApplicationStage.REJECTED
            if new_stage is not None and app.current_stage != new_stage:
                app.current_stage = new_stage
                db.commit()

    out = {c.name: getattr(fb, c.name) for c in fb.__table__.columns}
    out["interviewer_name"] = _user_label(admin)
    return out


@router.get("/interviews/{iid}/feedback", response_model=List[InterviewFeedbackResponse])
def list_feedback(
    iid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    entries = db.query(InterviewFeedback).options(
        joinedload(InterviewFeedback.interviewer)
    ).filter(InterviewFeedback.interview_id == iid).all()
    out = []
    for fb in entries:
        item = {c.name: getattr(fb, c.name) for c in fb.__table__.columns}
        item["interviewer_name"] = _user_label(fb.interviewer)
        out.append(item)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# OFFERS
# ──────────────────────────────────────────────────────────────────────────────

def _offer_to_response(o: Offer) -> dict:
    data = {c.name: getattr(o, c.name) for c in o.__table__.columns}
    if o.candidate:
        data["candidate_name"] = o.candidate.full_name
    if o.position:
        data["position_title"] = o.position.job_title
    return data


@router.get("/offers", response_model=OfferListResponse)
def list_offers(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status_filter: Optional[OfferStatus] = Query(None, alias="status"),
    include_deleted: bool = False,
    # When true, only return offers whose employee_id is NULL — used by the
    # Add Employee wizard to hide offers that have already been onboarded.
    unlinked_only: bool = Query(False),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(Offer).options(
        joinedload(Offer.candidate),
        joinedload(Offer.position),
    )
    if not include_deleted:
        q = q.filter(Offer.is_deleted == False)  # noqa: E712
    if status_filter:
        q = q.filter(Offer.status == status_filter)
    if unlinked_only:
        q = q.filter(Offer.employee_id.is_(None))
    q = q.order_by(desc(Offer.created_at))

    items, total, pages = _paginate(q, page, limit)
    return {
        "items": [_offer_to_response(i) for i in items],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": pages,
    }


@router.post("/offers", response_model=OfferResponse, status_code=201)
def create_offer(
    payload: OfferCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    app_obj = db.query(Application).options(
        joinedload(Application.candidate),
        joinedload(Application.position),
    ).filter(Application.id == payload.application_id).first()
    if not app_obj:
        raise HTTPException(404, "Application not found")

    o = Offer(
        **payload.model_dump(exclude_unset=True),
        offer_code=_next_code(db, "OFR", Offer, "offer_code"),
        candidate_id=app_obj.candidate_id,
        position_id=app_obj.position_id,
        status=OfferStatus.DRAFT,
        created_by_id=admin.id,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return _offer_to_response(o)


@router.get("/offers/{oid}", response_model=OfferResponse)
def get_offer(
    oid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    o = db.query(Offer).options(
        joinedload(Offer.candidate),
        joinedload(Offer.position),
    ).filter(Offer.id == oid).first()
    if not o:
        raise HTTPException(404, "Offer not found")
    return _offer_to_response(o)


@router.get("/offers/{oid}/onboarding-prefill", response_model=OnboardingPrefillResponse)
def get_offer_onboarding_prefill(
    oid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Return a flat payload combining offer + candidate + position fields
    so the Add Employee wizard can prefill in a single network call.

    Errors:
      - 404 if the offer does not exist
      - 409 if the offer has not been accepted yet
      - 409 if the offer has already been linked to an employee (the caller
        gets the existing employee_id in the detail so it can deep-link)
    """
    o = (
        db.query(Offer)
        .options(joinedload(Offer.candidate), joinedload(Offer.position))
        .filter(Offer.id == oid)
        .first()
    )
    if not o:
        raise HTTPException(404, "Offer not found")
    if o.status != OfferStatus.ACCEPTED:
        raise HTTPException(409, f"Offer must be accepted to onboard (current status: {o.status})")
    if o.employee_id is not None:
        raise HTTPException(
            409,
            {
                "message": "Offer has already been onboarded",
                "employee_id": str(o.employee_id),
            },
        )

    c = o.candidate
    p = o.position
    return {
        "offer_id": o.id,
        "offer_code": o.offer_code,
        "candidate": {
            "id": c.id,
            "candidate_code": getattr(c, "candidate_code", None),
            "full_name": c.full_name,
            "email": c.email,
            "mobile": c.mobile,
            "gender": c.gender,
            "dob": c.dob,
            "current_city": c.current_city,
            "current_state": c.current_state,
            "current_country": c.current_country,
            "notice_period_days": c.notice_period_days,
            "highest_qualification": c.highest_qualification,
            "current_designation": c.current_designation,
        },
        "position": {
            "id": p.id,
            "job_title": p.job_title,
            "job_code": p.job_code,
            "department_id": p.department_id,
            "designation_id": p.designation_id,
            "grade_id": p.grade_id,
            "location_id": p.location_id,
            "employment_type": p.employment_type.value if p.employment_type else None,
            "work_mode": p.work_mode.value if p.work_mode else None,
        },
        "offer": {
            "joining_date": o.joining_date,
            "designation_text": o.designation,
            "offered_salary": o.offered_salary,
            "bonus": o.bonus,
            "currency": o.currency,
            "reporting_manager_id": o.reporting_manager_id,
            "offer_valid_till": o.offer_valid_till,
            # Offer-level overrides if any (else position values still apply)
            "department_id": o.department_id,
            "grade_id": o.grade_id,
            "location_id": o.location_id,
        },
    }


@router.patch("/offers/{oid}", response_model=OfferResponse)
def update_offer(
    oid: UUID,
    payload: OfferUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    o = db.query(Offer).filter(Offer.id == oid).first()
    if not o:
        raise HTTPException(404, "Offer not found")
    if o.status in (OfferStatus.ACCEPTED, OfferStatus.REJECTED):
        raise HTTPException(409, f"Cannot edit offer in status {o.status}")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(o, k, v)
    db.commit()
    db.refresh(o)
    return _offer_to_response(o)


@router.post("/offers/{oid}/approve", response_model=OfferResponse)
def approve_offer(
    oid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    o = db.query(Offer).filter(Offer.id == oid).first()
    if not o:
        raise HTTPException(404, "Offer not found")
    if o.status not in (OfferStatus.DRAFT, OfferStatus.PENDING_APPROVAL):
        raise HTTPException(409, "Only DRAFT/PENDING_APPROVAL offers can be approved")
    o.status = OfferStatus.APPROVED
    o.approved_by_id = admin.id
    o.approved_at = datetime.utcnow()
    db.commit()
    db.refresh(o)
    return _offer_to_response(o)


@router.post("/offers/{oid}/release", response_model=OfferResponse)
def release_offer(
    oid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    o = db.query(Offer).options(joinedload(Offer.application)).filter(Offer.id == oid).first()
    if not o:
        raise HTTPException(404, "Offer not found")
    if o.status != OfferStatus.APPROVED:
        raise HTTPException(409, "Only APPROVED offers can be released")
    o.status = OfferStatus.RELEASED
    o.released_at = datetime.utcnow()
    if o.application:
        o.application.current_stage = ApplicationStage.OFFER
        o.application.stage_changed_at = datetime.utcnow()
        if o.application.candidate:
            o.application.candidate.status = CandidateStatus.OFFERED
    db.commit()
    db.refresh(o)
    return _offer_to_response(o)


@router.post("/offers/{oid}/respond", response_model=OfferResponse)
def respond_offer(
    oid: UUID,
    payload: OfferResponseAction,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    o = db.query(Offer).options(joinedload(Offer.application)).filter(Offer.id == oid).first()
    if not o:
        raise HTTPException(404, "Offer not found")
    if o.status != OfferStatus.RELEASED:
        raise HTTPException(409, "Only RELEASED offers can be accepted/rejected")
    o.status = OfferStatus.ACCEPTED if payload.accept else OfferStatus.REJECTED
    o.candidate_response_at = datetime.utcnow()
    o.candidate_response_note = payload.note
    if payload.accept and o.application and o.application.candidate:
        o.application.candidate.status = CandidateStatus.JOINED
        o.application.current_stage = ApplicationStage.JOINED
        o.application.stage_changed_at = datetime.utcnow()
        # The candidate has joined this position — auto-close it if every
        # opening is now filled. Flush first so the count below sees the
        # JOINED stage we just set (session is autoflush=False).
        pid = o.application.position_id or o.position_id
        if pid:
            db.flush()
            joined = db.query(func.count(Application.id)).filter(
                Application.position_id == pid,
                Application.current_stage == ApplicationStage.JOINED,
                Application.is_deleted == False,  # noqa: E712
            ).scalar() or 0
            pos = db.query(JobPosition).filter(JobPosition.id == pid).first()
            if pos:
                _apply_position_fill(pos, joined)
    db.commit()
    db.refresh(o)
    return _offer_to_response(o)


@router.delete("/offers/{oid}", status_code=204)
def archive_offer(
    oid: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    o = db.query(Offer).filter(Offer.id == oid).first()
    if not o:
        raise HTTPException(404, "Offer not found")
    o.is_deleted = True
    db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_model=RecruitmentDashboardData)
def dashboard(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    today = date.today()
    month_start = today.replace(day=1)

    open_positions = db.query(func.count(JobPosition.id)).filter(
        JobPosition.is_deleted == False,  # noqa: E712
        JobPosition.status == PositionStatus.OPEN,
    ).scalar() or 0

    applications_received = db.query(func.count(Application.id)).filter(
        Application.is_deleted == False,  # noqa: E712
    ).scalar() or 0

    candidates_in_pipeline = db.query(func.count(Candidate.id)).filter(
        Candidate.is_deleted == False,  # noqa: E712
        Candidate.status.in_([
            CandidateStatus.NEW, CandidateStatus.SCREENING, CandidateStatus.SHORTLISTED,
            CandidateStatus.INTERVIEW, CandidateStatus.SELECTED, CandidateStatus.OFFERED,
        ]),
    ).scalar() or 0

    pending_interviews = db.query(func.count(Interview.id)).filter(
        Interview.is_deleted == False,  # noqa: E712
        Interview.status == InterviewStatus.SCHEDULED,
        Interview.scheduled_at >= datetime.utcnow(),
    ).scalar() or 0

    offers_pending = db.query(func.count(Offer.id)).filter(
        Offer.is_deleted == False,  # noqa: E712
        Offer.status == OfferStatus.RELEASED,
    ).scalar() or 0

    hires_this_month = db.query(func.count(Application.id)).filter(
        Application.is_deleted == False,  # noqa: E712
        Application.current_stage == ApplicationStage.JOINED,
        Application.stage_changed_at >= month_start,
    ).scalar() or 0

    rejected_candidates = db.query(func.count(Candidate.id)).filter(
        Candidate.is_deleted == False,  # noqa: E712
        Candidate.status == CandidateStatus.REJECTED,
    ).scalar() or 0

    total_released = db.query(func.count(Offer.id)).filter(
        Offer.is_deleted == False,  # noqa: E712
        Offer.status.in_([OfferStatus.RELEASED, OfferStatus.ACCEPTED, OfferStatus.REJECTED]),
    ).scalar() or 0
    total_accepted = db.query(func.count(Offer.id)).filter(
        Offer.is_deleted == False,  # noqa: E712
        Offer.status == OfferStatus.ACCEPTED,
    ).scalar() or 0
    acceptance_rate = round((total_accepted / total_released * 100), 1) if total_released else 0.0

    # Time-to-hire: avg days between Application.applied_date and stage_changed_at for JOINED
    joined_apps = db.query(Application.applied_date, Application.stage_changed_at).filter(
        Application.is_deleted == False,  # noqa: E712
        Application.current_stage == ApplicationStage.JOINED,
    ).all()
    if joined_apps:
        days = [(b - a).days for a, b in joined_apps if a and b]
        avg_tth = round(sum(days) / max(1, len(days)), 1) if days else 0.0
    else:
        avg_tth = 0.0

    stats = RecruitmentDashboardStats(
        open_positions=open_positions,
        applications_received=applications_received,
        candidates_in_pipeline=candidates_in_pipeline,
        pending_interviews=pending_interviews,
        offers_pending=offers_pending,
        hires_this_month=hires_this_month,
        rejected_candidates=rejected_candidates,
        avg_time_to_hire_days=avg_tth,
        offer_acceptance_rate=acceptance_rate,
    )

    # Funnel
    funnel_stages = [
        ApplicationStage.APPLIED, ApplicationStage.SCREENING, ApplicationStage.SHORTLISTED,
        ApplicationStage.INTERVIEW, ApplicationStage.SELECTED, ApplicationStage.OFFER,
        ApplicationStage.JOINED,
    ]
    funnel = []
    for st in funnel_stages:
        c = db.query(func.count(Application.id)).filter(
            Application.is_deleted == False,  # noqa: E712
            Application.current_stage == st,
        ).scalar() or 0
        funnel.append(FunnelStage(stage=st.value, count=c))

    # Department hiring
    rows = db.query(
        Department.name,
        func.count(JobPosition.id).filter(JobPosition.status == PositionStatus.OPEN).label("open_positions"),
    ).outerjoin(JobPosition, JobPosition.department_id == Department.id).filter(
        Department.is_deleted == False,  # noqa: E712
    ).group_by(Department.name).order_by(Department.name).all()

    dept_hiring = []
    for name, op in rows:
        app_count = db.query(func.count(Application.id)).join(
            JobPosition, Application.position_id == JobPosition.id
        ).join(Department, JobPosition.department_id == Department.id).filter(
            Department.name == name,
            Application.is_deleted == False,  # noqa: E712
        ).scalar() or 0
        hire_count = db.query(func.count(Application.id)).join(
            JobPosition, Application.position_id == JobPosition.id
        ).join(Department, JobPosition.department_id == Department.id).filter(
            Department.name == name,
            Application.is_deleted == False,  # noqa: E712
            Application.current_stage == ApplicationStage.JOINED,
        ).scalar() or 0
        dept_hiring.append(DepartmentHiring(
            department=name, open_positions=int(op or 0),
            applications=app_count, hires=hire_count,
        ))

    # Monthly trend (last 6 months)
    monthly = []
    for i in range(5, -1, -1):
        m_first = (today.replace(day=1) - timedelta(days=30 * i)).replace(day=1)
        # End: first of next month
        if m_first.month == 12:
            m_next = m_first.replace(year=m_first.year + 1, month=1)
        else:
            m_next = m_first.replace(month=m_first.month + 1)
        apps = db.query(func.count(Application.id)).filter(
            Application.is_deleted == False,  # noqa: E712
            Application.applied_date >= m_first,
            Application.applied_date < m_next,
        ).scalar() or 0
        hires = db.query(func.count(Application.id)).filter(
            Application.is_deleted == False,  # noqa: E712
            Application.current_stage == ApplicationStage.JOINED,
            Application.stage_changed_at >= m_first,
            Application.stage_changed_at < m_next,
        ).scalar() or 0
        monthly.append(MonthlyTrendItem(
            month=m_first.strftime("%Y-%m"),
            applications=apps,
            hires=hires,
        ))

    # Candidate status distribution
    csd = []
    for st in CandidateStatus:
        c = db.query(func.count(Candidate.id)).filter(
            Candidate.is_deleted == False,  # noqa: E712
            Candidate.status == st,
        ).scalar() or 0
        if c:
            csd.append(FunnelStage(stage=st.value, count=c))

    # Sources distribution — count candidates by source channel
    sources = (
        db.query(Candidate.source, func.count(Candidate.id))
          .filter(Candidate.is_deleted == False)  # noqa: E712
          .group_by(Candidate.source)
          .all()
    )
    sources_distribution = [
        FunnelStage(stage=str(src.value if hasattr(src, "value") else src), count=cnt)
        for src, cnt in sources if cnt
    ]

    return RecruitmentDashboardData(
        stats=stats,
        funnel=funnel,
        department_hiring=dept_hiring,
        monthly_trend=monthly,
        candidate_status_distribution=csd,
        sources_distribution=sources_distribution,
        recent_activities=[],
    )


# ──────────────────────────────────────────────────────────────────────────────
# PIPELINE (Kanban)
# ──────────────────────────────────────────────────────────────────────────────

def _initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


@router.get("/pipeline", response_model=List[PipelineStage])
def pipeline(
    position_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(Application).options(
        joinedload(Application.candidate),
        joinedload(Application.position),
    ).filter(Application.is_deleted == False)  # noqa: E712
    if position_id:
        q = q.filter(Application.position_id == position_id)

    apps = q.order_by(desc(Application.applied_date)).all()

    stages = [
        (ApplicationStage.APPLIED, "Applied"),
        (ApplicationStage.SCREENING, "Screening"),
        (ApplicationStage.SHORTLISTED, "Shortlisted"),
        (ApplicationStage.INTERVIEW, "Interview"),
        (ApplicationStage.SELECTED, "Selected"),
        (ApplicationStage.OFFER, "Offer"),
        (ApplicationStage.JOINED, "Joined"),
        (ApplicationStage.REJECTED, "Rejected"),
    ]
    by_stage = {st: [] for st, _ in stages}

    for a in apps:
        cand_name = a.candidate.full_name if a.candidate else "Unknown"
        card = PipelineCard(
            application_id=a.id,
            candidate_id=a.candidate_id,
            candidate_name=cand_name,
            candidate_email=a.candidate.email if a.candidate else None,
            position_title=a.position.job_title if a.position else "—",
            position_code=a.position.job_code if a.position else "—",
            applied_date=a.applied_date,
            rating=a.rating,
            stage=a.current_stage,
            avatar_initials=_initials(cand_name),
        )
        if a.current_stage in by_stage:
            by_stage[a.current_stage].append(card)

    return [
        PipelineStage(
            stage=st,
            label=label,
            count=len(by_stage[st]),
            cards=by_stage[st],
        )
        for st, label in stages
    ]
