"""Archive Documents — unified read-only endpoint that aggregates SLAs, Handovers,
and DPRs that are >=1 year old AND in a terminal status.

Single source of truth for the Cinematic Vault page on the frontend.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, func, desc
from app.database import get_db
from app.utils.dependencies import get_current_active_user
from app.models.user import User
from app.models.sla import SlaAgreement
from app.models.handover import Handover
from app.models.dpr import DprDocument
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone


router = APIRouter(prefix="/archive", tags=["Archive Documents"])


# Terminal statuses per type that qualify a document as "archivable" once it's
# also old enough. Drafts/Approval-pending/Rejected docs never appear in archive.
TERMINAL_STATUSES = {
    "sla":      ("Active", "Expired"),
    "handover": ("Approved", "Completed"),
    "dpr":      ("Approved",),
}


def _one_year_ago_naive() -> datetime:
    """SLA and DPR `created_at` are naive (datetime.utcnow). Compare with naive UTC."""
    return datetime.utcnow() - timedelta(days=365)


def _one_year_ago_aware() -> datetime:
    """Handover `created_at` is timezone-aware. Compare with aware UTC."""
    return datetime.now(timezone.utc) - timedelta(days=365)


def _years_old(created_at: datetime) -> int:
    """Calendar-ish 'years old' for the UI age pill. Handles aware/naive."""
    if created_at is None:
        return 0
    now = datetime.now(timezone.utc) if created_at.tzinfo else datetime.utcnow()
    return max(0, (now - created_at).days // 365)


def _scoped_sla(db: Session, current_user: User):
    q = db.query(SlaAgreement).filter(
        SlaAgreement.status.in_(TERMINAL_STATUSES["sla"]),
        SlaAgreement.created_at <= _one_year_ago_naive(),
    )
    if not current_user.is_superuser:
        q = q.filter(SlaAgreement.created_by_id == current_user.id)
    return q


def _scoped_handover(db: Session, current_user: User):
    q = db.query(Handover).filter(
        Handover.status.in_(TERMINAL_STATUSES["handover"]),
        Handover.created_at <= _one_year_ago_aware(),
    )
    if not current_user.is_superuser:
        q = q.filter(Handover.created_by_id == current_user.id)
    return q


def _scoped_dpr(db: Session, current_user: User):
    q = db.query(DprDocument).filter(
        DprDocument.status.in_(TERMINAL_STATUSES["dpr"]),
        DprDocument.created_at <= _one_year_ago_naive(),
    )
    if not current_user.is_superuser:
        q = q.filter(DprDocument.created_by_id == current_user.id)
    return q


def _shape_sla(row: SlaAgreement) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": "sla",
        "title": row.title or row.client_organization_name or "Untitled SLA",
        "client": row.client_organization_name,
        "code": row.contract_reference,
        "project_id": str(row.project_id) if row.project_id else None,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "years_old": _years_old(row.created_at),
        "created_by_id": str(row.created_by_id) if row.created_by_id else None,
        "agreement_value": row.agreement_value,
        "currency": row.currency,
    }


def _shape_handover(row: Handover) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": "handover",
        "title": row.project_name or "Untitled Handover",
        "client": row.client_organization,
        "code": row.project_code,
        "project_id": str(row.project_id) if row.project_id else None,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "years_old": _years_old(row.created_at),
        "created_by_id": str(row.created_by_id) if row.created_by_id else None,
        "completion_date": row.completion_date.isoformat() if row.completion_date else None,
        "total_project_value": row.total_project_value,
        "currency": row.currency,
    }


def _shape_dpr(row: DprDocument) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": "dpr",
        "title": row.title,
        "client": None,
        "code": row.dpr_code,
        "project_id": str(row.project_id) if row.project_id else None,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "years_old": _years_old(row.created_at),
        "created_by_id": str(row.created_by_id) if row.created_by_id else None,
    }


def _matches_search(item: Dict[str, Any], q: str) -> bool:
    needle = q.lower()
    for key in ("title", "client", "code", "status"):
        v = item.get(key)
        if v and needle in str(v).lower():
            return True
    return False


def _year_of(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        return int(iso[:4])
    except (ValueError, TypeError):
        return None


@router.get("/stats")
def archive_stats(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Counts that drive the hero metrics + year filter dropdown."""
    sla_rows = _scoped_sla(db, current_user).all()
    hnd_rows = _scoped_handover(db, current_user).all()
    dpr_rows = _scoped_dpr(db, current_user).all()

    items = [
        *(_shape_sla(r) for r in sla_rows),
        *(_shape_handover(r) for r in hnd_rows),
        *(_shape_dpr(r) for r in dpr_rows),
    ]

    by_year_map: Dict[int, int] = {}
    oldest_year: Optional[int] = None
    for it in items:
        y = _year_of(it.get("created_at"))
        if y is None:
            continue
        by_year_map[y] = by_year_map.get(y, 0) + 1
        if oldest_year is None or y < oldest_year:
            oldest_year = y

    return {
        "total": len(items),
        "sla": len(sla_rows),
        "handover": len(hnd_rows),
        "dpr": len(dpr_rows),
        "oldest_year": oldest_year,
        "by_year": [{"year": y, "count": by_year_map[y]} for y in sorted(by_year_map.keys(), reverse=True)],
    }


@router.get("/documents")
def list_archived_documents(
    doc_type: Optional[str] = Query(None, description="sla | handover | dpr"),
    status: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Aggregated archive list. Filters happen in three places:
      - Status/age (always) — SQL via _scoped_*().
      - Status text override (status query) — applied per-row if user picked a specific terminal value.
      - Year + search — Python-side over the merged list (modest dataset).
    """
    items: List[Dict[str, Any]] = []

    if doc_type in (None, "sla"):
        items.extend(_shape_sla(r) for r in _scoped_sla(db, current_user).all())
    if doc_type in (None, "handover"):
        items.extend(_shape_handover(r) for r in _scoped_handover(db, current_user).all())
    if doc_type in (None, "dpr"):
        items.extend(_shape_dpr(r) for r in _scoped_dpr(db, current_user).all())

    if status:
        items = [it for it in items if (it.get("status") or "").lower() == status.lower()]
    if year:
        items = [it for it in items if _year_of(it.get("created_at")) == year]
    if search:
        items = [it for it in items if _matches_search(it, search.strip())]

    items.sort(key=lambda it: it.get("created_at") or "", reverse=True)

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = items[start:end]

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }
