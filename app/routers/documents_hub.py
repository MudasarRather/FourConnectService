"""Documents Hub — aggregated overview powering the /user/documents +
/admin/documents dashboard ("Document Atlas").

Single endpoint that returns counts, recent items per kind, mixed recent,
storage breakdown, and activity. The frontend hits this on mount only.

Scoping mirrors each underlying router's rule:
  - Superuser sees all.
  - Regular user sees only docs they created (sla/handover/dpr) plus the
    visibility rules from drive's _apply_access_filter.
  - Archive count uses the same terminal-status-and-age criterion as
    /api/archive/stats.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from app.database import get_db
from app.utils.dependencies import get_current_active_user
from app.models.user import User
from app.models.sla import SlaAgreement
from app.models.handover import Handover
from app.models.dpr import DprDocument
from app.models.drive_document import DriveDocument, DriveActivity
from app.routers.archive import _scoped_sla, _scoped_handover, _scoped_dpr
from app.routers.drive import _apply_access_filter

router = APIRouter(prefix="/documents", tags=["Documents Hub"])


def _iso(dt):
    return dt.isoformat() if dt else None


def _best_dt(row):
    """Prefer updated_at, fall back to created_at — consistent ordering."""
    return getattr(row, "updated_at", None) or getattr(row, "created_at", None)


@router.get("/overview")
def documents_overview(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    is_admin = bool(getattr(current_user, "is_superuser", False))

    # ─── Per-kind base queries ───
    sla_q = db.query(SlaAgreement)
    h_q   = db.query(Handover)
    d_q   = db.query(DprDocument)
    if not is_admin:
        sla_q = sla_q.filter(SlaAgreement.created_by_id == current_user.id)
        h_q   = h_q.filter(Handover.created_by_id == current_user.id)
        d_q   = d_q.filter(DprDocument.created_by_id == current_user.id)

    drive_q = _apply_access_filter(
        db.query(DriveDocument).filter(DriveDocument.is_deleted == False),
        current_user,
    )

    # ─── Counts ───
    totals = {
        "sla":      sla_q.count(),
        "handover": h_q.count(),
        "dpr":      d_q.count(),
        "drive":    drive_q.count(),
        "archive": (
            _scoped_sla(db, current_user).count()
            + _scoped_handover(db, current_user).count()
            + _scoped_dpr(db, current_user).count()
        ),
    }

    # ─── Recent rows per kind ───
    sla_recent   = sla_q.order_by(desc(SlaAgreement.updated_at)).limit(5).all()
    h_recent     = h_q.order_by(desc(Handover.updated_at)).limit(5).all()
    d_recent     = d_q.order_by(desc(DprDocument.updated_at)).limit(5).all()
    drive_recent = drive_q.order_by(desc(DriveDocument.updated_at)).limit(5).all()

    def shape_sla(r):
        return {
            "id": str(r.id), "kind": "sla",
            "title": r.title or r.client_organization_name or "Untitled SLA",
            "subtitle": r.client_organization_name,
            "status": r.status,
            "updated_at": _iso(_best_dt(r)),
        }

    def shape_handover(r):
        return {
            "id": str(r.id), "kind": "handover",
            "title": r.project_name or "Untitled Handover",
            "subtitle": r.client_organization,
            "status": r.status,
            "updated_at": _iso(_best_dt(r)),
        }

    def shape_dpr(r):
        return {
            "id": str(r.id), "kind": "dpr",
            "title": r.title,
            "subtitle": r.dpr_code,
            "status": r.status,
            "updated_at": _iso(_best_dt(r)),
        }

    def shape_drive(r):
        return {
            "id": str(r.id), "kind": "drive",
            "title": r.title,
            "subtitle": r.file_type,
            "status": r.status,
            "updated_at": _iso(_best_dt(r)),
            "file_size": r.file_size,
            "file_type": r.file_type,
        }

    recent_by_kind = {
        "sla":      [shape_sla(r) for r in sla_recent[:3]],
        "handover": [shape_handover(r) for r in h_recent[:3]],
        "dpr":      [shape_dpr(r) for r in d_recent[:3]],
        "drive":    [shape_drive(r) for r in drive_recent[:3]],
    }

    # Mixed cross-vault feed — top 15 across all kinds
    mixed = (
        [shape_sla(r) for r in sla_recent]
        + [shape_handover(r) for r in h_recent]
        + [shape_dpr(r) for r in d_recent]
        + [shape_drive(r) for r in drive_recent]
    )
    mixed.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    recent_mixed = mixed[:15]

    # ─── Storage (drive only) ───
    drive_size = drive_q.with_entities(func.sum(DriveDocument.file_size)).scalar() or 0
    type_rows = drive_q.with_entities(
        DriveDocument.file_type,
        func.count(DriveDocument.id),
        func.sum(DriveDocument.file_size),
    ).group_by(DriveDocument.file_type).all()
    storage = {
        "used_bytes": int(drive_size),
        "file_count": totals["drive"],
        "type_breakdown": [
            {"type": t[0] or "other", "count": int(t[1] or 0), "bytes": int(t[2] or 0)}
            for t in type_rows
        ],
    }

    # ─── Activity stream — DriveActivity is the only persistent activity table.
    # Synthesize SLA/Handover/DPR "activity" from updated_at as well, marked synthetic.
    activity = []
    visible_doc_ids = drive_q.with_entities(DriveDocument.id).subquery()
    for a in (
        db.query(DriveActivity)
        .filter(DriveActivity.document_id.in_(db.query(visible_doc_ids)))
        .order_by(desc(DriveActivity.created_at))
        .limit(8)
        .all()
    ):
        activity.append({
            "id": str(a.id),
            "kind": "drive_activity",
            "actor_name": a.user_name,
            "action": a.action,
            "details": a.details,
            "doc_id": str(a.document_id) if a.document_id else None,
            "doc_kind": "drive",
            "created_at": _iso(a.created_at),
        })
    # Synthetic events from non-drive doc updates (gives mixed feed for users with no drive activity)
    for r in sla_recent[:3]:
        activity.append({
            "id": f"sla-{r.id}",
            "kind": "doc_update",
            "actor_name": None,
            "action": (r.status or "updated").lower(),
            "details": r.title or r.client_organization_name,
            "doc_id": str(r.id), "doc_kind": "sla",
            "created_at": _iso(_best_dt(r)),
        })
    for r in h_recent[:3]:
        activity.append({
            "id": f"handover-{r.id}",
            "kind": "doc_update",
            "actor_name": None,
            "action": (r.status or "updated").lower(),
            "details": r.project_name,
            "doc_id": str(r.id), "doc_kind": "handover",
            "created_at": _iso(_best_dt(r)),
        })
    for r in d_recent[:3]:
        activity.append({
            "id": f"dpr-{r.id}",
            "kind": "doc_update",
            "actor_name": None,
            "action": (r.status or "updated").lower(),
            "details": r.title,
            "doc_id": str(r.id), "doc_kind": "dpr",
            "created_at": _iso(_best_dt(r)),
        })
    activity.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    activity = activity[:12]

    return {
        "totals": totals,
        "recent_by_kind": recent_by_kind,
        "recent_mixed": recent_mixed,
        "storage": storage,
        "activity": activity,
    }
