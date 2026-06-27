"""HR Settings — Numbering Series CRUD + a 'sync counter to current max' action."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.numbering_series import NumberingSeries, NUMBERING_MODULES
from app.models.hr.employee import Employee
from app.models.hr.recruitment import JobRequisition, JobPosition, Candidate, Application, Interview, Offer
from app.schemas.hr.numbering_series import (
    NumberingSeriesCreate, NumberingSeriesUpdate, NumberingSeriesResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.settings_audit import log_settings_change

router = APIRouter(prefix="/hr/settings/numbering", tags=["HR — Numbering Series"])

# module → (model, code column) for the "sync to current max" helper.
_SYNC_MAP = {
    "EMPLOYEE": (Employee, "employee_id"),
    "RECRUITMENT_REQUISITION": (JobRequisition, "requisition_number"),
    "RECRUITMENT_POSITION": (JobPosition, "job_code"),
    "RECRUITMENT_CANDIDATE": (Candidate, "candidate_code"),
    "RECRUITMENT_APPLICATION": (Application, "application_code"),
    "RECRUITMENT_INTERVIEW": (Interview, "interview_code"),
    "RECRUITMENT_OFFER": (Offer, "offer_code"),
}


def _current_max(db: Session, module: str) -> int:
    pair = _SYNC_MAP.get(module)
    if not pair:
        return 0
    model, col = pair
    last = db.query(model).order_by(desc(getattr(model, col))).first()
    if not last:
        return 0
    raw = getattr(last, col) or ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else 0


@router.get("/catalog")
def catalog(admin: User = Depends(get_current_superuser)):
    return {"modules": NUMBERING_MODULES}


@router.get("/", response_model=list[NumberingSeriesResponse])
def list_series(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return (db.query(NumberingSeries)
            .filter(NumberingSeries.is_deleted == False)  # noqa: E712
            .order_by(NumberingSeries.module).all())


@router.post("/", response_model=NumberingSeriesResponse, status_code=201)
def create_series(payload: NumberingSeriesCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    if db.query(NumberingSeries).filter(NumberingSeries.module == payload.module, NumberingSeries.is_deleted == False).first():  # noqa: E712
        raise HTTPException(409, "A series for this module already exists")
    row = NumberingSeries(**payload.model_dump())
    db.add(row)
    db.flush()
    log_settings_change(db, "NUMBERING", row.id, "CREATE", admin.id, after={"module": row.module, "prefix": row.prefix}, note=row.module)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{series_id}", response_model=NumberingSeriesResponse)
def update_series(series_id: UUID, payload: NumberingSeriesUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    row = db.query(NumberingSeries).filter(NumberingSeries.id == series_id).first()
    if not row:
        raise HTTPException(404, "Series not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    log_settings_change(db, "NUMBERING", row.id, "UPDATE", admin.id, note=row.module)
    db.commit()
    db.refresh(row)
    return row


@router.post("/{series_id}/sync", response_model=NumberingSeriesResponse)
def sync_counter(series_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Set the counter to the highest number already in use for the module, so a
    newly-configured series continues the existing run instead of colliding."""
    row = db.query(NumberingSeries).filter(NumberingSeries.id == series_id).first()
    if not row:
        raise HTTPException(404, "Series not found")
    row.current_number = _current_max(db, row.module)
    log_settings_change(db, "NUMBERING", row.id, "UPDATE", admin.id, note=f"sync→{row.current_number}")
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{series_id}", status_code=204)
def delete_series(
    series_id: UUID,
    reason: str | None = Query(None, max_length=400),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Decommission a series → reverts the module to the built-in auto-ID.

    Soft-delete only (``is_deleted``), so a super admin can recover it. The
    counter value is *not* preserved on the fallback path — if a series is
    re-configured later it should be re-synced to the current max to avoid
    collisions. An optional ``reason`` is sealed into the settings audit ledger.
    """
    row = db.query(NumberingSeries).filter(NumberingSeries.id == series_id).first()
    if not row:
        raise HTTPException(404, "Series not found")
    row.is_deleted = True
    note = f"{row.module} · {reason.strip()}" if reason and reason.strip() else row.module
    log_settings_change(db, "NUMBERING", row.id, "DELETE", admin.id, note=note)
    db.commit()
