"""HR Travel — DA rate matrix CRUD (admin). Grade × City-Category, effective-dated."""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.grade import Grade
from app.models.hr.travel_da import TravelDaRate
from app.models.hr.travel_type import CityCategory, TravelAuditAction
from app.schemas.hr.travel import DaRateCreate, DaRateUpdate, DaRateResponse, DaRateListResponse
from app.utils.dependencies import get_current_superuser
from app.utils.hr.travel import write_travel_audit

router = APIRouter(prefix="/hr/travel-da-rates", tags=["HR — Travel DA Rates"])


def _to_resp(db: Session, r: TravelDaRate) -> dict:
    grade_name = None
    if r.grade_id:
        g = db.query(Grade.name).filter(Grade.id == r.grade_id).first()
        grade_name = g[0] if g else None
    return {
        "id": r.id, "grade_id": r.grade_id, "grade_name": grade_name,
        "city_category": r.city_category, "daily_rate": r.daily_rate, "currency": r.currency,
        "effective_date": r.effective_date, "notes": r.notes, "is_active": r.is_active,
        "created_at": r.created_at,
    }


@router.get("/", response_model=DaRateListResponse)
def list_rates(grade_id: Optional[UUID] = None, city_category: Optional[CityCategory] = None,
               include_inactive: bool = False, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_superuser)):
    query = db.query(TravelDaRate).filter(TravelDaRate.is_deleted == False)  # noqa: E712
    if grade_id:
        query = query.filter(TravelDaRate.grade_id == grade_id)
    if city_category:
        query = query.filter(TravelDaRate.city_category == city_category)
    if not include_inactive:
        query = query.filter(TravelDaRate.is_active == True)  # noqa: E712
    rows = query.order_by(
        TravelDaRate.grade_id.asc().nullsfirst(), TravelDaRate.city_category.asc(),
        TravelDaRate.effective_date.desc(),
    ).all()
    return DaRateListResponse(items=[_to_resp(db, r) for r in rows], total=len(rows))


@router.post("/", response_model=DaRateResponse, status_code=201)
def create_rate(payload: DaRateCreate, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_superuser)):
    r = TravelDaRate(
        grade_id=payload.grade_id, city_category=payload.city_category,
        daily_rate=payload.daily_rate, currency=payload.currency,
        effective_date=payload.effective_date or date.today(), notes=payload.notes,
        is_active=payload.is_active, created_by_id=current_user.id,
    )
    db.add(r)
    db.flush()
    write_travel_audit(db, entity_type="DA_RATE", entity_id=r.id,
                       action=TravelAuditAction.DA_RATE_CREATE, actor_id=current_user.id,
                       note=f"{r.city_category.value} @ {r.daily_rate}")
    db.commit()
    db.refresh(r)
    return _to_resp(db, r)


@router.patch("/{rate_id}", response_model=DaRateResponse)
def update_rate(rate_id: UUID, payload: DaRateUpdate, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_superuser)):
    r = db.query(TravelDaRate).filter(
        TravelDaRate.id == rate_id, TravelDaRate.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "DA rate not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    write_travel_audit(db, entity_type="DA_RATE", entity_id=r.id,
                       action=TravelAuditAction.DA_RATE_UPDATE, actor_id=current_user.id)
    db.commit()
    db.refresh(r)
    return _to_resp(db, r)


@router.delete("/{rate_id}")
def delete_rate(rate_id: UUID, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_superuser)):
    r = db.query(TravelDaRate).filter(
        TravelDaRate.id == rate_id, TravelDaRate.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "DA rate not found")
    r.is_deleted = True
    write_travel_audit(db, entity_type="DA_RATE", entity_id=r.id,
                       action=TravelAuditAction.DA_RATE_DELETE, actor_id=current_user.id)
    db.commit()
    return {"success": True}
