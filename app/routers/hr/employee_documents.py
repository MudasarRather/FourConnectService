"""HR Employee Documents — unified document lifecycle hub.

Endpoints under /api/hr/employee-documents/*:
  dashboard · list/CRUD · upload (versioned, reuses DriveDocument) · verify /
  reject / request-resubmit / bulk · archive / restore / soft-delete · signed
  expiring download URLs · verification queue · expiry buckets · onboarding sync
  · templates (table ready; UI in Pass 2).

Auth: every endpoint requires `get_current_superuser`, EXCEPT the signed
download endpoint, whose short-lived token *is* the capability.
File bytes live in `drive_documents` (reused for versioning + counters).
"""
from __future__ import annotations

import math
import os
import uuid as _uuid
from datetime import datetime, date, timedelta
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from jose import jwt, JWTError
from sqlalchemy import func, or_, desc
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.models.drive_document import DriveDocument
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.onboarding import OnboardingDocument, DocumentSlotStatus
from app.models.hr.employee_document import (
    EmployeeDocument, EmployeeDocumentEvent, EmployeeDocumentTemplate,
    DocumentCategory, DocVerificationStatus, DocSource, CONFIDENTIAL_CATEGORIES,
)
from app.schemas.hr.employee_documents import (
    EmployeeDocumentCreate, EmployeeDocumentUpdate, EmployeeDocumentResponse,
    EmployeeDocumentDetailResponse, EmployeeDocumentListResponse,
    VerifyBody, RejectBody, ResubmitBody, DeleteBody, BulkVerifyBody, DownloadTokenResponse,
    EdocDashboardResponse, ChartPoint,
    TemplateCreate, TemplateUpdate, TemplateResponse, TemplateListResponse,
)
from app.utils.dependencies import get_current_superuser, get_current_user
from app.utils.hr.employee_document_sync import sync_onboarding_documents, sync_all_onboarding_documents

# Self-service additions
from app.models.hr.document_request import (
    DocumentRequest, DocumentRequestType, DocumentRequestStatus,
)
from app.schemas.hr.employee_documents import (
    MyDocumentsSummaryResponse, MyDocCategoryBreakdown,
    DocumentRequestCreate, DocumentRequestResponse,
    DocumentRequestListResponse, DocumentRequestDecision,
)

router = APIRouter(prefix="/hr/employee-documents", tags=["HR — Employee Documents"])

# ──────────────────────────────────────────────────────────────────────────────
# Storage + constants
# ──────────────────────────────────────────────────────────────────────────────

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
STORAGE_DIR = os.path.join(SERVICE_ROOT, "storage")
EDOC_DIR = os.path.join(STORAGE_DIR, "employee-documents")
os.makedirs(EDOC_DIR, exist_ok=True)

ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".docx", ".xlsx"}
MAX_SIZE = 10 * 1024 * 1024  # 10 MB
DOWNLOAD_TOKEN_TTL = 300     # seconds (5 min)
EXPIRY_SOON_DAYS = 90
EXPIRY_THRESHOLDS = [90, 60, 30, 7]

# Minimal mandatory matrix used for "missing documents" analytics.
MANDATORY_DOC_TYPES = [
    (DocumentCategory.KYC, "AADHAAR"),
    (DocumentCategory.KYC, "PAN"),
]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _paginate(query, page: int, limit: int):
    total = query.count()
    items = query.offset((page - 1) * limit).limit(limit).all()
    return items, total, max(1, math.ceil(total / max(1, limit)))


def _mask_number(num: Optional[str]) -> Optional[str]:
    if not num:
        return num
    s = str(num)
    if len(s) <= 4:
        return "•" * len(s)
    return "•" * (len(s) - 4) + s[-4:]


def _resolve_path(file_url: Optional[str]) -> Optional[str]:
    if not file_url:
        return None
    return os.path.join(SERVICE_ROOT, file_url.lstrip("/").replace("/", os.sep))


def _log_event(db: Session, doc: EmployeeDocument, action: str,
               actor: Optional[User] = None, note: Optional[str] = None,
               metadata: Optional[dict] = None) -> None:
    db.add(EmployeeDocumentEvent(
        employee_document_id=doc.id,
        action=action,
        actor_id=actor.id if actor else None,
        actor_name=(getattr(actor, "full_name", None) or getattr(actor, "email", None)) if actor else None,
        note=note,
        event_metadata=metadata or {},
    ))


def _doc_to_response(d: EmployeeDocument, *, reveal: bool = False) -> dict:
    dd: Optional[DriveDocument] = d.drive_document
    emp: Optional[Employee] = d.employee
    emp_user = getattr(emp, "user", None) if emp else None
    dept = getattr(emp, "department", None) if emp else None

    confidential = bool(d.is_confidential)
    masked = confidential and not reveal
    number = _mask_number(d.document_number) if masked else d.document_number

    days_to_expiry = None
    if d.expiry_date:
        days_to_expiry = (d.expiry_date - date.today()).days

    return {
        "id": d.id,
        "employee_id": d.employee_id,
        "category": d.category,
        "doc_type": d.doc_type,
        "title": d.title,
        "document_number": number,
        "document_number_masked": masked,
        "issued_by": d.issued_by,
        "issue_date": d.issue_date,
        "expiry_date": d.expiry_date,
        "verification_status": d.verification_status,
        "verified_by_user_id": d.verified_by_user_id,
        "verified_by_name": (getattr(d.verified_by, "full_name", None)
                             or getattr(d.verified_by, "email", None)) if d.verified_by else None,
        "verified_at": d.verified_at,
        "rejection_reason": d.rejection_reason,
        "attributes": d.attributes or {},
        "source": d.source,
        "onboarding_document_id": d.onboarding_document_id,
        "is_confidential": confidential,
        "is_archived": bool(d.is_archived),
        "drive_document_id": d.drive_document_id,
        "file_name": dd.file_name if dd else None,
        "file_type": dd.file_type if dd else None,
        "file_size": dd.file_size if dd else None,
        "has_file": dd is not None,
        "employee_name": getattr(emp_user, "full_name", None) if emp_user else None,
        "employee_code": getattr(emp, "employee_id", None) if emp else None,
        "department_name": getattr(dept, "name", None) if dept else None,
        "days_to_expiry": days_to_expiry,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }


def _base_query(db: Session):
    return db.query(EmployeeDocument).options(
        joinedload(EmployeeDocument.drive_document),
        joinedload(EmployeeDocument.verified_by),
        joinedload(EmployeeDocument.employee).joinedload(Employee.user),
        joinedload(EmployeeDocument.employee).joinedload(Employee.department),
    )


def _get_doc(db: Session, doc_id: UUID) -> EmployeeDocument:
    d = _base_query(db).filter(
        EmployeeDocument.id == doc_id,
        EmployeeDocument.is_deleted == False,  # noqa: E712
    ).first()
    if not d:
        raise HTTPException(404, "Document not found")
    return d


def _reflect_to_onboarding(db: Session, doc: EmployeeDocument) -> None:
    """Keep the originating onboarding slot in sync (one source of truth)."""
    if not doc.onboarding_document_id:
        return
    slot = db.query(OnboardingDocument).filter(
        OnboardingDocument.id == doc.onboarding_document_id
    ).first()
    if not slot:
        return
    mapping = {
        DocVerificationStatus.VERIFIED: DocumentSlotStatus.VERIFIED,
        DocVerificationStatus.REJECTED: DocumentSlotStatus.REJECTED,
        DocVerificationStatus.PENDING: DocumentSlotStatus.UPLOADED if slot.drive_document_id else DocumentSlotStatus.PENDING,
        DocVerificationStatus.RESUBMIT_REQUIRED: DocumentSlotStatus.REJECTED,
        DocVerificationStatus.EXPIRED: DocumentSlotStatus.EXPIRED,
    }
    slot.status = mapping.get(doc.verification_status, slot.status)
    slot.verified_by_user_id = doc.verified_by_user_id
    slot.verified_at = doc.verified_at
    slot.rejection_reason = doc.rejection_reason
    try:
        from app.routers.hr.onboarding import _recalculate_progress
        from app.models.hr.onboarding import OnboardingProcess
        proc = db.query(OnboardingProcess).filter(OnboardingProcess.id == slot.process_id).first()
        if proc:
            _recalculate_progress(db, proc)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_model=EdocDashboardResponse)
def dashboard(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    # Self-heal: surface every employee's onboarding-uploaded documents into the
    # unified store so they appear across the hub (idempotent, only creates gaps).
    try:
        if sync_all_onboarding_documents(db):
            db.commit()
    except Exception:
        db.rollback()

    today = date.today()
    soon = today + timedelta(days=EXPIRY_SOON_DAYS)
    month_start = today.replace(day=1)

    live = (EmployeeDocument.is_deleted == False, EmployeeDocument.is_archived == False)  # noqa: E712

    total_documents = db.query(func.count(EmployeeDocument.id)).filter(*live).scalar() or 0
    pending_verification = db.query(func.count(EmployeeDocument.id)).filter(
        *live,
        EmployeeDocument.verification_status.in_([
            DocVerificationStatus.PENDING, DocVerificationStatus.RESUBMIT_REQUIRED,
        ]),
        EmployeeDocument.drive_document_id.isnot(None),
    ).scalar() or 0
    expiring_soon = db.query(func.count(EmployeeDocument.id)).filter(
        *live,
        EmployeeDocument.expiry_date.isnot(None),
        EmployeeDocument.expiry_date >= today,
        EmployeeDocument.expiry_date <= soon,
        EmployeeDocument.verification_status != DocVerificationStatus.EXPIRED,
    ).scalar() or 0
    uploaded_this_month = db.query(func.count(EmployeeDocument.id)).filter(
        *live, EmployeeDocument.created_at >= datetime(month_start.year, month_start.month, 1),
    ).scalar() or 0
    compliance_pending = db.query(func.count(EmployeeDocument.id)).filter(
        *live,
        EmployeeDocument.category == DocumentCategory.COMPLIANCE,
        EmployeeDocument.verification_status.in_([
            DocVerificationStatus.PENDING, DocVerificationStatus.RESUBMIT_REQUIRED,
        ]),
    ).scalar() or 0
    contract_expiry = db.query(func.count(EmployeeDocument.id)).filter(
        *live,
        EmployeeDocument.category == DocumentCategory.CONTRACT,
        EmployeeDocument.expiry_date.isnot(None),
        EmployeeDocument.expiry_date >= today,
        EmployeeDocument.expiry_date <= soon,
    ).scalar() or 0
    archived_documents = db.query(func.count(EmployeeDocument.id)).filter(
        EmployeeDocument.is_deleted == False, EmployeeDocument.is_archived == True,  # noqa: E712
    ).scalar() or 0

    # Category distribution
    cat_rows = db.query(EmployeeDocument.category, func.count(EmployeeDocument.id)).filter(*live).group_by(
        EmployeeDocument.category
    ).all()
    cat_map = {c.value: n for c, n in cat_rows}
    category_distribution = [
        ChartPoint(label=c.value.replace("_", " ").title(), key=c.value, value=cat_map.get(c.value, 0))
        for c in DocumentCategory
    ]

    # Verification status distribution
    vs_rows = db.query(EmployeeDocument.verification_status, func.count(EmployeeDocument.id)).filter(
        *live
    ).group_by(EmployeeDocument.verification_status).all()
    vs_map = {s.value: n for s, n in vs_rows}
    verification_status = [
        ChartPoint(label=s.value.replace("_", " ").title(), key=s.value, value=vs_map.get(s.value, 0))
        for s in DocVerificationStatus
    ]

    # Expiry timeline buckets
    def _bucket(lo_days, hi_days):
        lo = today + timedelta(days=lo_days)
        hi = today + timedelta(days=hi_days)
        return db.query(func.count(EmployeeDocument.id)).filter(
            *live, EmployeeDocument.expiry_date.isnot(None),
            EmployeeDocument.expiry_date >= lo, EmployeeDocument.expiry_date <= hi,
        ).scalar() or 0

    expired_count = db.query(func.count(EmployeeDocument.id)).filter(
        *live, EmployeeDocument.expiry_date.isnot(None), EmployeeDocument.expiry_date < today,
    ).scalar() or 0
    expiry_timeline = [
        ChartPoint(label="Expired", key="expired", value=expired_count),
        ChartPoint(label="0–30 days", key="0_30", value=_bucket(0, 30)),
        ChartPoint(label="31–60 days", key="31_60", value=_bucket(31, 60)),
        ChartPoint(label="61–90 days", key="61_90", value=_bucket(61, 90)),
    ]

    # Missing documents (active employees × mandatory types not present)
    active_emps = db.query(Employee.id, Employee.department_id).filter(
        Employee.is_deleted == False,  # noqa: E712
        Employee.lifecycle_state.notin_(["EXITED", "ARCHIVED"]),
    ).all()
    present = db.query(EmployeeDocument.employee_id, EmployeeDocument.category, EmployeeDocument.doc_type).filter(
        EmployeeDocument.is_deleted == False,  # noqa: E712
    ).all()
    present_set = {(e, c, t) for e, c, t in present}
    dept_names = {d.id: d.name for d in db.query(Department).all()}

    missing_total = 0
    dept_missing_map: dict = {}
    for emp_id, dept_id in active_emps:
        for cat, dtype in MANDATORY_DOC_TYPES:
            if (emp_id, cat, dtype) not in present_set:
                missing_total += 1
                label = dept_names.get(dept_id, "Unassigned")
                dept_missing_map[label] = dept_missing_map.get(label, 0) + 1

    department_missing = [
        ChartPoint(label=k, value=v) for k, v in sorted(dept_missing_map.items(), key=lambda x: -x[1])
    ]

    return EdocDashboardResponse(
        total_documents=total_documents,
        pending_verification=pending_verification,
        expiring_soon=expiring_soon,
        missing_documents=missing_total,
        uploaded_this_month=uploaded_this_month,
        compliance_pending=compliance_pending,
        contract_expiry=contract_expiry,
        archived_documents=archived_documents,
        category_distribution=category_distribution,
        expiry_timeline=expiry_timeline,
        department_missing=department_missing,
        verification_status=verification_status,
    )


# ──────────────────────────────────────────────────────────────────────────────
# List / fetch
# ──────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=EmployeeDocumentListResponse)
@router.get("/", response_model=EmployeeDocumentListResponse)
def list_documents(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    employee_id: Optional[UUID] = None,
    category: Optional[DocumentCategory] = None,
    doc_type: Optional[str] = None,
    verification_status: Optional[DocVerificationStatus] = Query(None, alias="status"),
    expiring_within: Optional[int] = None,
    q: Optional[str] = None,
    archived: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    query = _base_query(db).filter(
        EmployeeDocument.is_deleted == False,  # noqa: E712
        EmployeeDocument.is_archived == archived,
    )
    if employee_id:
        query = query.filter(EmployeeDocument.employee_id == employee_id)
    if category:
        query = query.filter(EmployeeDocument.category == category)
    if doc_type:
        query = query.filter(EmployeeDocument.doc_type == doc_type)
    if verification_status:
        query = query.filter(EmployeeDocument.verification_status == verification_status)
    if expiring_within:
        hi = date.today() + timedelta(days=expiring_within)
        query = query.filter(
            EmployeeDocument.expiry_date.isnot(None),
            EmployeeDocument.expiry_date >= date.today(),
            EmployeeDocument.expiry_date <= hi,
        )
    if q:
        s = f"%{q.lower()}%"
        query = query.filter(or_(
            func.lower(EmployeeDocument.title).like(s),
            func.lower(EmployeeDocument.doc_type).like(s),
        ))
    query = query.order_by(desc(EmployeeDocument.created_at))
    items, total, pages = _paginate(query, page, limit)
    return {
        "items": [_doc_to_response(d) for d in items],
        "total": total, "page": page, "limit": limit, "total_pages": pages,
    }


@router.get("/queue", response_model=EmployeeDocumentListResponse)
def verification_queue(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    query = _base_query(db).filter(
        EmployeeDocument.is_deleted == False,  # noqa: E712
        EmployeeDocument.is_archived == False,  # noqa: E712
        EmployeeDocument.drive_document_id.isnot(None),
        EmployeeDocument.verification_status.in_([
            DocVerificationStatus.PENDING, DocVerificationStatus.RESUBMIT_REQUIRED,
        ]),
    ).order_by(EmployeeDocument.created_at.asc())
    items, total, pages = _paginate(query, page, limit)
    return {
        "items": [_doc_to_response(d) for d in items],
        "total": total, "page": page, "limit": limit, "total_pages": pages,
    }


@router.get("/expiring", response_model=EmployeeDocumentListResponse)
def expiring_documents(
    within: int = Query(90, ge=1, le=365),
    include_expired: bool = True,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    hi = date.today() + timedelta(days=within)
    query = _base_query(db).filter(
        EmployeeDocument.is_deleted == False,  # noqa: E712
        EmployeeDocument.is_archived == False,  # noqa: E712
        EmployeeDocument.expiry_date.isnot(None),
    )
    if include_expired:
        query = query.filter(EmployeeDocument.expiry_date <= hi)
    else:
        query = query.filter(
            EmployeeDocument.expiry_date >= date.today(),
            EmployeeDocument.expiry_date <= hi,
        )
    query = query.order_by(EmployeeDocument.expiry_date.asc())
    items, total, pages = _paginate(query, page, limit)
    return {
        "items": [_doc_to_response(d) for d in items],
        "total": total, "page": page, "limit": limit, "total_pages": pages,
    }


@router.get("/by-employee/{employee_id}", response_model=EmployeeDocumentListResponse)
def by_employee(
    employee_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    # Lazy-surface onboarding documents into the unified store first.
    created = sync_onboarding_documents(db, employee_id)
    if created:
        db.commit()
    query = _base_query(db).filter(
        EmployeeDocument.employee_id == employee_id,
        EmployeeDocument.is_deleted == False,  # noqa: E712
    ).order_by(EmployeeDocument.category, desc(EmployeeDocument.created_at))
    rows = query.all()
    return {
        "items": [_doc_to_response(d) for d in rows],
        "total": len(rows), "page": 1, "limit": len(rows) or 1, "total_pages": 1,
    }


@router.post("/sync-onboarding/{employee_id}", response_model=EmployeeDocumentListResponse)
def manual_sync(
    employee_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    sync_onboarding_documents(db, employee_id)
    db.commit()
    return by_employee(employee_id, db, admin)


# Path is `/file/download` (two segments) rather than `/download` so it can
# never collide with the parametric `/{doc_id}` route — FastAPI's `{doc_id}`
# only matches single-segment paths, so this is registration-order-proof.
# The previous single-segment `/download` got swallowed by `/{doc_id}` and
# returned "Not authenticated" because that route requires admin auth.
@router.get("/file/download")
def download_with_token(token: str, inline: bool = False, db: Session = Depends(get_db)):
    """Stream the file behind a signed token.

    `inline=true` serves the file with `Content-Disposition: inline` so the
    browser renders it directly (e.g. iframe preview for PDFs). Default
    `inline=false` triggers a normal download via `attachment`.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(401, "Invalid or expired download link")
    if payload.get("scope") != "edoc_download":
        raise HTTPException(401, "Invalid token scope")
    doc_id = payload.get("doc_id")
    d = db.query(EmployeeDocument).options(joinedload(EmployeeDocument.drive_document)).filter(
        EmployeeDocument.id == doc_id, EmployeeDocument.is_deleted == False,  # noqa: E712
    ).first()
    if not d or not d.drive_document:
        raise HTTPException(404, "Document not found")
    path = _resolve_path(d.drive_document.file_url)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "File missing from storage")
    # Track the download (count both inline previews and explicit downloads —
    # they're both end-user accesses).
    d.drive_document.download_count = (d.drive_document.download_count or 0) + 1
    uid = payload.get("uid")
    actor = db.query(User).filter(User.id == uid).first() if uid else None
    _log_event(db, d, "VIEWED" if inline else "DOWNLOADED", actor)
    db.commit()

    file_name = d.drive_document.file_name or os.path.basename(path)
    media_type = d.drive_document.mime_type or "application/octet-stream"

    if inline:
        # Build the response WITHOUT FastAPI's auto-attachment header so the
        # browser renders the file in-place (e.g. PDF preview in iframe).
        # `filename=None` keeps FileResponse from forcing `attachment`.
        response = FileResponse(path, media_type=media_type)
        # Quote the filename per RFC 6266 — fall back to ascii-only if needed.
        try:
            safe = file_name.encode("ascii").decode("ascii")
            response.headers["Content-Disposition"] = f'inline; filename="{safe}"'
        except UnicodeEncodeError:
            response.headers["Content-Disposition"] = (
                f"inline; filename*=UTF-8''{file_name}"
            )
        return response

    return FileResponse(path, filename=file_name, media_type=media_type)


# ══════════════════════════════════════════════════════════════════════════════
# Self-service (employee-facing) /me/* cluster
# ──────────────────────────────────────────────────────────────────────────────
# MUST be declared BEFORE the parametric `/{doc_id}` route so FastAPI matches
# the literal `/me` segments first (UUID type coercion on /{doc_id} would
# otherwise 422 on the literal "me"). Pattern matches the /file/download
# workaround above.
# ══════════════════════════════════════════════════════════════════════════════

# Mandatory document types per category — drives "missing required" warnings.
MANDATORY_TYPES_BY_CATEGORY = {
    DocumentCategory.KYC:        ["AADHAAR", "PAN"],
    DocumentCategory.COMPLIANCE: [],   # informational; admin populates as needed
    DocumentCategory.ID_PROOF:   [],
}


def _resolve_self_employee(db: Session, user: User) -> Employee:
    """Resolve the calling user's own employee record.

    Pattern lifted from attendance.py:_resolve_self_employee — kept local to
    avoid coupling routers. Raises 404 (not 403) to avoid leaking whether an
    employee record exists for unrelated users.
    """
    emp = db.query(Employee).filter(
        Employee.user_id == user.id,
        Employee.is_deleted == False,  # noqa: E712
    ).first()
    if not emp:
        raise HTTPException(404, "No employee record linked to your account")
    return emp


def _get_my_doc(db: Session, doc_id: UUID, emp: Employee) -> EmployeeDocument:
    """Fetch a document AND verify ownership in one call. 404 on any miss."""
    d = _base_query(db).filter(
        EmployeeDocument.id == doc_id,
        EmployeeDocument.is_deleted == False,  # noqa: E712
        EmployeeDocument.employee_id == emp.id,
    ).first()
    if not d:
        # 404, not 403 — don't leak the existence of other employees' docs.
        raise HTTPException(404, "Document not found")
    return d


# ─── My documents — list / summary / detail ─────────────────────────────────

@router.get("/me", response_model=EmployeeDocumentListResponse)
def my_documents(
    category: Optional[DocumentCategory] = None,
    verification_status: Optional[DocVerificationStatus] = Query(None, alias="status"),
    archived: bool = False,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Paginated list of the calling employee's own documents.

    Lazy-syncs onboarding documents so any KYC uploaded during onboarding is
    surfaced into the unified store (same pattern as /by-employee).
    """
    emp = _resolve_self_employee(db, user)
    if sync_onboarding_documents(db, emp.id):
        db.commit()
    query = _base_query(db).filter(
        EmployeeDocument.employee_id == emp.id,
        EmployeeDocument.is_deleted == False,  # noqa: E712
        EmployeeDocument.is_archived == archived,
    )
    if category:
        query = query.filter(EmployeeDocument.category == category)
    if verification_status:
        query = query.filter(EmployeeDocument.verification_status == verification_status)
    query = query.order_by(EmployeeDocument.category, desc(EmployeeDocument.created_at))
    items, total, pages = _paginate(query, page, limit)
    # Own documents are NEVER masked — it's the employee's own data.
    return {
        "items": [_doc_to_response(d, reveal=True) for d in items],
        "total": total, "page": page, "limit": limit, "total_pages": pages,
    }


@router.get("/me/summary", response_model=MyDocumentsSummaryResponse)
def my_documents_summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """KPI snapshot for the self-service hero — counts per category × status."""
    emp = _resolve_self_employee(db, user)
    if sync_onboarding_documents(db, emp.id):
        db.commit()

    today = date.today()
    soon = today + timedelta(days=EXPIRY_SOON_DAYS)
    live_q = db.query(EmployeeDocument).filter(
        EmployeeDocument.employee_id == emp.id,
        EmployeeDocument.is_deleted == False,  # noqa: E712
        EmployeeDocument.is_archived == False,  # noqa: E712
    )
    docs = live_q.all()

    total = len(docs)
    pending = sum(1 for d in docs if d.verification_status in (
        DocVerificationStatus.PENDING, DocVerificationStatus.RESUBMIT_REQUIRED))
    verified = sum(1 for d in docs if d.verification_status == DocVerificationStatus.VERIFIED)
    rejected = sum(1 for d in docs if d.verification_status == DocVerificationStatus.REJECTED)
    expiring_soon = sum(1 for d in docs
        if d.expiry_date and d.expiry_date >= today and d.expiry_date <= soon
        and d.verification_status != DocVerificationStatus.EXPIRED)
    expired = sum(1 for d in docs
        if d.expiry_date and d.expiry_date < today)

    # Per-category breakdown — emits a row for EVERY category so the rail
    # always renders all stops, even empty ones.
    by_category: List[MyDocCategoryBreakdown] = []
    for cat in DocumentCategory:
        cat_docs = [d for d in docs if d.category == cat]
        types_present = {d.doc_type for d in cat_docs}
        required = MANDATORY_TYPES_BY_CATEGORY.get(cat, [])
        missing = [t for t in required if t not in types_present]
        by_category.append(MyDocCategoryBreakdown(
            category=cat,
            total=len(cat_docs),
            pending=sum(1 for d in cat_docs if d.verification_status in (
                DocVerificationStatus.PENDING, DocVerificationStatus.RESUBMIT_REQUIRED)),
            verified=sum(1 for d in cat_docs if d.verification_status == DocVerificationStatus.VERIFIED),
            rejected=sum(1 for d in cat_docs if d.verification_status == DocVerificationStatus.REJECTED),
            resubmit_required=sum(1 for d in cat_docs if d.verification_status == DocVerificationStatus.RESUBMIT_REQUIRED),
            expired=sum(1 for d in cat_docs if d.expiry_date and d.expiry_date < today),
            expiring_soon=sum(1 for d in cat_docs
                if d.expiry_date and d.expiry_date >= today and d.expiry_date <= soon),
            is_mandatory=bool(required),
            missing_required_types=missing,
        ))

    emp_user = getattr(emp, "user", None)
    dept = getattr(emp, "department", None)
    return MyDocumentsSummaryResponse(
        employee_id=emp.id,
        employee_name=getattr(emp_user, "full_name", None) if emp_user else None,
        employee_code=emp.employee_id,
        department_name=getattr(dept, "name", None) if dept else None,
        total_documents=total,
        pending_count=pending,
        verified_count=verified,
        rejected_count=rejected,
        expiring_soon_count=expiring_soon,
        expired_count=expired,
        by_category=by_category,
    )


# ─── My documents — actions: upload, resubmit, download ─────────────────────

@router.post("/me/upload", response_model=EmployeeDocumentResponse, status_code=201)
async def my_upload(
    file: UploadFile = File(...),
    category: DocumentCategory = Form(...),
    doc_type: str = Form(...),
    title: str = Form(...),
    document_number: Optional[str] = Form(None),
    issued_by: Optional[str] = Form(None),
    issue_date: Optional[date] = Form(None),
    expiry_date: Optional[date] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Employee self-upload — create + upload in one shot.

    The created document is PENDING by default. HR sees it in the verification
    queue. For Aadhaar in KYC, only the last 4 digits are persisted.
    """
    emp = _resolve_self_employee(db, user)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Invalid file type {ext}. Allowed: {', '.join(sorted(ALLOWED_EXT))}")
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "File too large. Max 10MB.")

    # Aadhaar: persist only last 4 digits, per the project convention.
    number = document_number
    if category == DocumentCategory.KYC and (doc_type or "").upper() == "AADHAAR" and number:
        digits = "".join(ch for ch in number if ch.isdigit())
        number = digits[-4:] if digits else None

    confidential = category in CONFIDENTIAL_CATEGORIES

    # 1) Create the EmployeeDocument row
    d = EmployeeDocument(
        employee_id=emp.id,
        category=category,
        doc_type=doc_type.strip().upper(),
        title=title.strip(),
        document_number=number,
        issued_by=issued_by,
        issue_date=issue_date,
        expiry_date=expiry_date,
        attributes={},
        is_confidential=confidential,
        source=DocSource.DIRECT_UPLOAD,
        verification_status=DocVerificationStatus.PENDING,
        created_by_id=user.id,
        last_updated_by_id=user.id,
    )
    db.add(d)
    db.flush()

    # 2) Persist the file + link DriveDocument (mirrors admin upload_file)
    cat_dir = category.value.lower()
    dest_dir = os.path.join(EDOC_DIR, str(emp.id), cat_dir)
    os.makedirs(dest_dir, exist_ok=True)
    unique = f"{_uuid.uuid4()}{ext}"
    with open(os.path.join(dest_dir, unique), "wb") as f:
        f.write(content)
    file_url = f"/storage/employee-documents/{emp.id}/{cat_dir}/{unique}"

    drive_doc = DriveDocument(
        title=d.title,
        file_name=file.filename or unique,
        file_url=file_url,
        file_type=ext.lstrip("."),
        file_size=len(content),
        mime_type=file.content_type,
        category="HR",
        status="Under Review",
        is_confidential=confidential,
        uploaded_by=user.id,
        employee_id=emp.id,
        version_number=1,
        parent_document_id=None,
    )
    db.add(drive_doc)
    db.flush()
    d.drive_document_id = drive_doc.id
    _log_event(db, d, "UPLOADED", user, note=file.filename,
               metadata={"source": "SELF_UPLOAD", "version": 1})
    db.commit()
    return _doc_to_response(_get_my_doc(db, d.id, emp), reveal=True)


@router.post("/me/{doc_id}/resubmit", response_model=EmployeeDocumentResponse)
async def my_resubmit(
    doc_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-upload a new version of a rejected / resubmit-required document.

    Allowed only when the doc's current status is REJECTED or
    RESUBMIT_REQUIRED. Versions chain via DriveDocument.parent_document_id.
    """
    emp = _resolve_self_employee(db, user)
    d = _get_my_doc(db, doc_id, emp)
    if d.verification_status not in (
        DocVerificationStatus.REJECTED, DocVerificationStatus.RESUBMIT_REQUIRED,
    ):
        raise HTTPException(409, "This document is not eligible for resubmission")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Invalid file type {ext}. Allowed: {', '.join(sorted(ALLOWED_EXT))}")
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "File too large. Max 10MB.")

    cat_dir = d.category.value.lower()
    dest_dir = os.path.join(EDOC_DIR, str(emp.id), cat_dir)
    os.makedirs(dest_dir, exist_ok=True)
    unique = f"{_uuid.uuid4()}{ext}"
    with open(os.path.join(dest_dir, unique), "wb") as f:
        f.write(content)
    file_url = f"/storage/employee-documents/{emp.id}/{cat_dir}/{unique}"

    prev = d.drive_document
    drive_doc = DriveDocument(
        title=d.title,
        file_name=file.filename or unique,
        file_url=file_url,
        file_type=ext.lstrip("."),
        file_size=len(content),
        mime_type=file.content_type,
        category="HR",
        status="Under Review",
        is_confidential=bool(d.is_confidential),
        uploaded_by=user.id,
        employee_id=emp.id,
        version_number=(getattr(prev, "version_number", 0) or 0) + 1 if prev else 1,
        parent_document_id=prev.id if prev else None,
    )
    db.add(drive_doc)
    db.flush()

    d.drive_document_id = drive_doc.id
    d.verification_status = DocVerificationStatus.PENDING
    d.rejection_reason = None
    d.last_updated_by_id = user.id
    _log_event(db, d, "RESUBMITTED", user, note=file.filename,
               metadata={"source": "SELF_RESUBMIT", "version": drive_doc.version_number})
    _reflect_to_onboarding(db, d)
    db.commit()
    return _doc_to_response(_get_my_doc(db, d.id, emp), reveal=True)


@router.post("/me/{doc_id}/download-token", response_model=DownloadTokenResponse)
def my_download_token(
    doc_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Issue a 5-minute signed URL for the employee's own document."""
    emp = _resolve_self_employee(db, user)
    d = _get_my_doc(db, doc_id, emp)
    if not d.drive_document_id:
        raise HTTPException(404, "No file attached to this document")
    settings = get_settings()
    payload = {
        "scope": "edoc_download",
        "doc_id": str(doc_id),
        "uid": str(user.id),
        "exp": datetime.utcnow() + timedelta(seconds=DOWNLOAD_TOKEN_TTL),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return DownloadTokenResponse(
        token=token, expires_in=DOWNLOAD_TOKEN_TTL,
        url=f"/api/hr/employee-documents/file/download?token={token}",
    )


# ─── My document requests — list / create / cancel ──────────────────────────

def _request_to_response(r: DocumentRequest) -> dict:
    emp = r.employee
    emp_user = getattr(emp, "user", None) if emp else None
    dept = getattr(emp, "department", None) if emp else None
    decided_by = r.decided_by
    assigned_to = r.assigned_to
    return {
        "id": r.id,
        "employee_id": r.employee_id,
        "employee_name": getattr(emp_user, "full_name", None) if emp_user else None,
        "employee_code": getattr(emp, "employee_id", None) if emp else None,
        "department_name": getattr(dept, "name", None) if dept else None,
        "request_type": r.request_type,
        "custom_title": r.custom_title,
        "reason": r.reason,
        "notes": r.notes,
        "purpose": r.purpose,
        "status": r.status,
        "assigned_to_user_id": r.assigned_to_user_id,
        "assigned_to_name": (getattr(assigned_to, "full_name", None)
                              or getattr(assigned_to, "email", None)) if assigned_to else None,
        "fulfilled_doc_id": r.fulfilled_doc_id,
        "decided_by_user_id": r.decided_by_user_id,
        "decided_by_name": (getattr(decided_by, "full_name", None)
                             or getattr(decided_by, "email", None)) if decided_by else None,
        "decided_at": r.decided_at,
        "decision_notes": r.decision_notes,
        "cancelled_at": r.cancelled_at,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def _request_query(db: Session):
    return db.query(DocumentRequest).options(
        joinedload(DocumentRequest.employee).joinedload(Employee.user),
        joinedload(DocumentRequest.employee).joinedload(Employee.department),
        joinedload(DocumentRequest.decided_by),
        joinedload(DocumentRequest.assigned_to),
    )


@router.get("/me/requests", response_model=DocumentRequestListResponse)
def my_requests(
    status: Optional[DocumentRequestStatus] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    query = _request_query(db).filter(
        DocumentRequest.employee_id == emp.id,
        DocumentRequest.is_deleted == False,  # noqa: E712
    )
    if status:
        query = query.filter(DocumentRequest.status == status)
    query = query.order_by(desc(DocumentRequest.created_at))
    items, total, pages = _paginate(query, page, limit)
    return {
        "items": [_request_to_response(r) for r in items],
        "total": total, "page": page, "limit": limit, "total_pages": pages,
    }


@router.post("/me/requests", response_model=DocumentRequestResponse, status_code=201)
def my_request_create(
    payload: DocumentRequestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Submit a new document request to HR."""
    emp = _resolve_self_employee(db, user)
    # Custom type requires a custom_title (otherwise it's a noise row in admin queue).
    if payload.request_type == DocumentRequestType.CUSTOM and not (payload.custom_title or "").strip():
        raise HTTPException(400, "A custom title is required for CUSTOM requests")
    r = DocumentRequest(
        employee_id=emp.id,
        request_type=payload.request_type,
        custom_title=payload.custom_title,
        reason=payload.reason.strip(),
        notes=(payload.notes or None),
        purpose=payload.purpose,
        status=DocumentRequestStatus.PENDING,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _request_to_response(_request_query(db).filter(DocumentRequest.id == r.id).first())


@router.delete("/me/requests/{req_id}", status_code=204)
def my_request_cancel(
    req_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cancel a pending request. Only PENDING requests are cancellable."""
    emp = _resolve_self_employee(db, user)
    r = db.query(DocumentRequest).filter(
        DocumentRequest.id == req_id,
        DocumentRequest.employee_id == emp.id,
        DocumentRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not r:
        raise HTTPException(404, "Request not found")
    if r.status != DocumentRequestStatus.PENDING:
        raise HTTPException(409, "Only pending requests can be cancelled")
    r.status = DocumentRequestStatus.CANCELLED
    r.cancelled_at = datetime.utcnow()
    db.commit()


# ─── Admin queue for document requests ──────────────────────────────────────

@router.get("/admin/requests", response_model=DocumentRequestListResponse)
def admin_list_requests(
    status: Optional[DocumentRequestStatus] = None,
    request_type: Optional[DocumentRequestType] = None,
    employee_id: Optional[UUID] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    query = _request_query(db).filter(DocumentRequest.is_deleted == False)  # noqa: E712
    if status:
        query = query.filter(DocumentRequest.status == status)
    if request_type:
        query = query.filter(DocumentRequest.request_type == request_type)
    if employee_id:
        query = query.filter(DocumentRequest.employee_id == employee_id)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(or_(
            func.lower(DocumentRequest.reason).like(like),
            func.lower(DocumentRequest.purpose).like(like),
            func.lower(DocumentRequest.custom_title).like(like),
        ))
    query = query.order_by(desc(DocumentRequest.created_at))
    items, total, pages = _paginate(query, page, limit)
    return {
        "items": [_request_to_response(r) for r in items],
        "total": total, "page": page, "limit": limit, "total_pages": pages,
    }


@router.patch("/admin/requests/{req_id}", response_model=DocumentRequestResponse)
def admin_decide_request(
    req_id: UUID,
    payload: DocumentRequestDecision,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    r = _request_query(db).filter(
        DocumentRequest.id == req_id,
        DocumentRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not r:
        raise HTTPException(404, "Request not found")

    # Status transitions: PENDING -> IN_PROGRESS -> FULFILLED/REJECTED
    # IN_PROGRESS -> FULFILLED/REJECTED. Cancelled is terminal.
    if r.status == DocumentRequestStatus.CANCELLED:
        raise HTTPException(409, "Cancelled requests cannot be modified")
    if r.status == DocumentRequestStatus.FULFILLED:
        raise HTTPException(409, "Fulfilled requests cannot be re-decided")

    r.status = payload.status
    if payload.decision_notes is not None:
        r.decision_notes = payload.decision_notes
    if payload.fulfilled_doc_id is not None:
        # Validate the doc belongs to the same employee
        doc = db.query(EmployeeDocument).filter(
            EmployeeDocument.id == payload.fulfilled_doc_id,
            EmployeeDocument.employee_id == r.employee_id,
            EmployeeDocument.is_deleted == False,  # noqa: E712
        ).first()
        if not doc:
            raise HTTPException(400, "fulfilled_doc_id does not point to a document of this employee")
        r.fulfilled_doc_id = payload.fulfilled_doc_id
    if payload.assigned_to_user_id is not None:
        r.assigned_to_user_id = payload.assigned_to_user_id
    if payload.status in (DocumentRequestStatus.FULFILLED, DocumentRequestStatus.REJECTED):
        r.decided_by_user_id = admin.id
        r.decided_at = datetime.utcnow()
    db.commit()
    return _request_to_response(_request_query(db).filter(DocumentRequest.id == r.id).first())


# ══════════════════════════════════════════════════════════════════════════════
# End of /me/* and /admin/requests cluster
# ══════════════════════════════════════════════════════════════════════════════


@router.get("/{doc_id}", response_model=EmployeeDocumentDetailResponse)
def get_document(
    doc_id: UUID,
    reveal: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    d = _get_doc(db, doc_id)
    data = _doc_to_response(d, reveal=reveal)
    if reveal and d.is_confidential:
        _log_event(db, d, "REVEALED", admin, note="Document number revealed")
        db.commit()
    data["events"] = [
        {"id": e.id, "action": e.action, "actor_id": e.actor_id,
         "actor_name": e.actor_name, "note": e.note, "created_at": e.created_at}
        for e in d.events
    ]
    return data


# ──────────────────────────────────────────────────────────────────────────────
# Create / update
# ──────────────────────────────────────────────────────────────────────────────

@router.post("", response_model=EmployeeDocumentResponse, status_code=201)
@router.post("/", response_model=EmployeeDocumentResponse, status_code=201)
def create_document(
    payload: EmployeeDocumentCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    confidential = payload.is_confidential
    if confidential is None:
        confidential = payload.category in CONFIDENTIAL_CATEGORIES
    # Never persist a full Aadhaar number — keep last 4 only.
    number = payload.document_number
    if payload.category == DocumentCategory.KYC and (payload.doc_type or "").upper() == "AADHAAR" and number:
        digits = "".join(ch for ch in number if ch.isdigit())
        number = digits[-4:] if digits else None
    d = EmployeeDocument(
        employee_id=payload.employee_id,
        category=payload.category,
        doc_type=payload.doc_type,
        title=payload.title,
        document_number=number,
        issued_by=payload.issued_by,
        issue_date=payload.issue_date,
        expiry_date=payload.expiry_date,
        attributes=payload.attributes or {},
        is_confidential=confidential,
        source=DocSource.DIRECT_UPLOAD,
        created_by_id=admin.id,
        last_updated_by_id=admin.id,
    )
    db.add(d)
    db.flush()
    _log_event(db, d, "CREATED", admin)
    db.commit()
    return _doc_to_response(_get_doc(db, d.id))


@router.patch("/{doc_id}", response_model=EmployeeDocumentResponse)
def update_document(
    doc_id: UUID,
    payload: EmployeeDocumentUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    d = _get_doc(db, doc_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    d.last_updated_by_id = admin.id
    _log_event(db, d, "UPDATED", admin)
    db.commit()
    return _doc_to_response(_get_doc(db, d.id))


# ──────────────────────────────────────────────────────────────────────────────
# File upload (versioned, reuses DriveDocument)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/{doc_id}/upload", response_model=EmployeeDocumentResponse)
async def upload_file(
    doc_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    d = _get_doc(db, doc_id)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Invalid file type {ext}. Allowed: {', '.join(sorted(ALLOWED_EXT))}")
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "File too large. Max 10MB.")

    cat_dir = d.category.value.lower()
    dest_dir = os.path.join(EDOC_DIR, str(d.employee_id), cat_dir)
    os.makedirs(dest_dir, exist_ok=True)
    unique = f"{_uuid.uuid4()}{ext}"
    with open(os.path.join(dest_dir, unique), "wb") as f:
        f.write(content)
    file_url = f"/storage/employee-documents/{d.employee_id}/{cat_dir}/{unique}"

    # Version chain: link the previous DriveDocument as parent.
    prev = d.drive_document
    drive_doc = DriveDocument(
        title=d.title,
        file_name=file.filename or unique,
        file_url=file_url,
        file_type=ext.lstrip("."),
        file_size=len(content),
        mime_type=file.content_type,
        category="HR",
        status="Under Review",
        is_confidential=bool(d.is_confidential),
        uploaded_by=admin.id,
        employee_id=d.employee_id,
        version_number=(getattr(prev, "version_number", 0) or 0) + 1 if prev else 1,
        parent_document_id=prev.id if prev else None,
    )
    db.add(drive_doc)
    db.flush()

    d.drive_document_id = drive_doc.id
    d.verification_status = DocVerificationStatus.PENDING
    d.rejection_reason = None
    d.last_updated_by_id = admin.id
    _log_event(db, d, "UPLOADED", admin, note=file.filename,
               metadata={"version": drive_doc.version_number})
    _reflect_to_onboarding(db, d)
    db.commit()
    return _doc_to_response(_get_doc(db, d.id))


# ──────────────────────────────────────────────────────────────────────────────
# Verify / reject / resubmit (+ bulk)
# ──────────────────────────────────────────────────────────────────────────────

def _do_verify(db: Session, d: EmployeeDocument, admin: User, expiry_date=None, note=None):
    if d.drive_document_id is None:
        raise HTTPException(409, "Cannot verify a document with no file attached")
    if d.verification_status == DocVerificationStatus.VERIFIED:
        return  # idempotent
    d.verification_status = DocVerificationStatus.VERIFIED
    d.verified_by_user_id = admin.id
    d.verified_at = datetime.utcnow()
    d.rejection_reason = None
    d.last_updated_by_id = admin.id
    if expiry_date:
        d.expiry_date = expiry_date
    if d.drive_document:
        d.drive_document.status = "Approved"
    # Flush the row UPDATE BEFORE logging the audit event. The session runs with
    # autoflush=False, and a prior incident showed the audit event INSERT persisting
    # while the doc's verification_status UPDATE was silently dropped. Explicitly
    # flushing here forces the UPDATE statement to be issued in the same transaction
    # so a SQL failure surfaces immediately instead of being half-committed.
    db.flush()
    _log_event(db, d, "VERIFIED", admin, note=note)
    _reflect_to_onboarding(db, d)


def _do_reject(db: Session, d: EmployeeDocument, admin: User, reason: str):
    d.verification_status = DocVerificationStatus.REJECTED
    d.verified_by_user_id = admin.id
    d.verified_at = datetime.utcnow()
    d.rejection_reason = reason
    d.last_updated_by_id = admin.id
    db.flush()  # same rationale as _do_verify — surface UPDATE before logging audit
    _log_event(db, d, "REJECTED", admin, note=reason)
    _reflect_to_onboarding(db, d)


def _do_resubmit(db: Session, d: EmployeeDocument, admin: User, reason: Optional[str]):
    d.verification_status = DocVerificationStatus.RESUBMIT_REQUIRED
    d.rejection_reason = reason
    d.last_updated_by_id = admin.id
    db.flush()  # same rationale as _do_verify
    _log_event(db, d, "RESUBMIT_REQUESTED", admin, note=reason)
    _reflect_to_onboarding(db, d)


@router.post("/{doc_id}/verify", response_model=EmployeeDocumentResponse)
def verify_document(
    doc_id: UUID, payload: VerifyBody,
    db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    d = _get_doc(db, doc_id)
    _do_verify(db, d, admin, expiry_date=payload.expiry_date, note=payload.note)
    db.commit()
    return _doc_to_response(_get_doc(db, d.id))


@router.post("/{doc_id}/reject", response_model=EmployeeDocumentResponse)
def reject_document(
    doc_id: UUID, payload: RejectBody,
    db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    d = _get_doc(db, doc_id)
    _do_reject(db, d, admin, payload.reason)
    db.commit()
    return _doc_to_response(_get_doc(db, d.id))


@router.post("/{doc_id}/request-resubmit", response_model=EmployeeDocumentResponse)
def request_resubmit(
    doc_id: UUID, payload: ResubmitBody,
    db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    d = _get_doc(db, doc_id)
    _do_resubmit(db, d, admin, payload.reason)
    db.commit()
    return _doc_to_response(_get_doc(db, d.id))


@router.post("/verify-bulk")
def verify_bulk(
    payload: BulkVerifyBody,
    db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    if payload.action in ("reject", "resubmit") and not payload.reason:
        raise HTTPException(400, "A reason is required to reject or request resubmission")
    processed, skipped = 0, 0
    for doc_id in payload.ids:
        d = _base_query(db).filter(
            EmployeeDocument.id == doc_id, EmployeeDocument.is_deleted == False,  # noqa: E712
        ).first()
        if not d:
            skipped += 1
            continue
        try:
            if payload.action == "verify":
                _do_verify(db, d, admin)
            elif payload.action == "reject":
                _do_reject(db, d, admin, payload.reason)
            else:
                _do_resubmit(db, d, admin, payload.reason)
            processed += 1
        except HTTPException:
            skipped += 1
    db.commit()
    return {"processed": processed, "skipped": skipped}


# ──────────────────────────────────────────────────────────────────────────────
# Archive / restore / delete
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/{doc_id}/archive", response_model=EmployeeDocumentResponse)
def archive_document(
    doc_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    d = _get_doc(db, doc_id)
    d.is_archived = True
    d.archived_at = datetime.utcnow()
    d.archived_by_id = admin.id
    _log_event(db, d, "ARCHIVED", admin)
    db.commit()
    return _doc_to_response(_get_doc(db, d.id))


@router.post("/{doc_id}/restore", response_model=EmployeeDocumentResponse)
def restore_document(
    doc_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    d = _get_doc(db, doc_id)
    d.is_archived = False
    d.archived_at = None
    d.archived_by_id = None
    _log_event(db, d, "RESTORED", admin)
    db.commit()
    return _doc_to_response(_get_doc(db, d.id))


@router.delete("/{doc_id}", status_code=204)
def delete_document(
    doc_id: UUID,
    payload: Optional[DeleteBody] = Body(None),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    d = _get_doc(db, doc_id)
    d.is_deleted = True
    d.deleted_at = datetime.utcnow()
    d.deleted_by_id = admin.id
    note = (payload.reason.strip() if payload and payload.reason else None)
    metadata = {}
    if payload and payload.reason_category:
        metadata["reason_category"] = payload.reason_category
    _log_event(db, d, "DELETED", admin, note=note, metadata=metadata or None)
    db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Signed downloads (token-based; the token is the capability)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/{doc_id}/download-token", response_model=DownloadTokenResponse)
def issue_download_token(
    doc_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    d = _get_doc(db, doc_id)
    if not d.drive_document_id:
        raise HTTPException(404, "No file attached to this document")
    settings = get_settings()
    payload = {
        "scope": "edoc_download",
        "doc_id": str(doc_id),
        "uid": str(admin.id),
        "exp": datetime.utcnow() + timedelta(seconds=DOWNLOAD_TOKEN_TTL),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return DownloadTokenResponse(
        token=token, expires_in=DOWNLOAD_TOKEN_TTL,
        url=f"/api/hr/employee-documents/file/download?token={token}",
    )


# The `/download` GET endpoint was moved up — registered before `/{doc_id}` so
# the literal path wins. See the block above the get_document() handler.


# ──────────────────────────────────────────────────────────────────────────────
# Templates (table ready; full UI in Pass 2)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/templates/list", response_model=TemplateListResponse)
def list_templates(
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    query = db.query(EmployeeDocumentTemplate).filter(
        EmployeeDocumentTemplate.is_deleted == False,  # noqa: E712
    ).order_by(EmployeeDocumentTemplate.created_at.desc())
    items, total, pages = _paginate(query, page, limit)
    return {"items": items, "total": total, "page": page, "limit": limit, "total_pages": pages}


@router.post("/templates", response_model=TemplateResponse, status_code=201)
def create_template(
    payload: TemplateCreate,
    db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    t = EmployeeDocumentTemplate(
        **payload.model_dump(exclude_unset=True), created_by_id=admin.id,
    )
    if t.placeholders is None:
        t.placeholders = []
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.patch("/templates/{tid}", response_model=TemplateResponse)
def update_template(
    tid: UUID, payload: TemplateUpdate,
    db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    t = db.query(EmployeeDocumentTemplate).filter(EmployeeDocumentTemplate.id == tid).first()
    if not t:
        raise HTTPException(404, "Template not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return t


@router.delete("/templates/{tid}", status_code=204)
def delete_template(
    tid: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    t = db.query(EmployeeDocumentTemplate).filter(EmployeeDocumentTemplate.id == tid).first()
    if not t:
        raise HTTPException(404, "Template not found")
    t.is_deleted = True
    db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Expiry cron — invoked from tasks_cron.py (not an HTTP route)
# ──────────────────────────────────────────────────────────────────────────────

def check_document_expiry_alerts(db: Session) -> dict:
    """Mark passed-expiry documents EXPIRED and emit 90/60/30/7-day reminders.

    Idempotent — reminders are deduped via `expiry_reminders_sent`. Designed to
    run daily from tasks_cron.py.
    """
    from app.models.notification import Notification

    today = date.today()
    marked_expired = 0
    reminders = 0

    # 1) Flip newly-expired documents.
    expired = db.query(EmployeeDocument).filter(
        EmployeeDocument.is_deleted == False,  # noqa: E712
        EmployeeDocument.expiry_date.isnot(None),
        EmployeeDocument.expiry_date < today,
        EmployeeDocument.verification_status != DocVerificationStatus.EXPIRED,
    ).all()
    for d in expired:
        d.verification_status = DocVerificationStatus.EXPIRED
        _log_event(db, d, "EXPIRED", None, note=f"Expired on {d.expiry_date}")
        marked_expired += 1

    # 2) Threshold reminders for upcoming expiries.
    horizon = today + timedelta(days=max(EXPIRY_THRESHOLDS))
    upcoming = db.query(EmployeeDocument).options(
        joinedload(EmployeeDocument.employee), joinedload(EmployeeDocument.drive_document),
    ).filter(
        EmployeeDocument.is_deleted == False,  # noqa: E712
        EmployeeDocument.expiry_date.isnot(None),
        EmployeeDocument.expiry_date >= today,
        EmployeeDocument.expiry_date <= horizon,
    ).all()
    for d in upcoming:
        days_left = (d.expiry_date - today).days
        sent = list(d.expiry_reminders_sent or [])
        for thr in EXPIRY_THRESHOLDS:
            if days_left <= thr and thr not in sent:
                emp = d.employee
                recipient = getattr(emp, "hr_manager_id", None) or getattr(emp, "created_by_id", None)
                if recipient:
                    db.add(Notification(
                        user_id=recipient,
                        type="DOCUMENT_EXPIRY",
                        title=f"Document expiring in {days_left} day(s)",
                        message=f"{d.title} ({d.category.value}) expires on {d.expiry_date}.",
                        action_url=f"/admin/hr/employee-documents/expiry",
                    ))
                    reminders += 1
                sent.append(thr)
        if sorted(sent) != sorted(d.expiry_reminders_sent or []):
            d.expiry_reminders_sent = sorted(set(sent))

    db.commit()
    return {"marked_expired": marked_expired, "reminders_sent": reminders}
