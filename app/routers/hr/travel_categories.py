"""HR Travel — Travel Category master CRUD (admin)."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.travel_category import TravelCategory
from app.models.hr.travel_request import TravelRequest
from app.models.hr.travel_type import TravelAuditAction
from app.schemas.hr.travel import (
    TravelCategoryCreate, TravelCategoryUpdate, TravelCategoryResponse, TravelCategoryListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.travel import write_travel_audit

router = APIRouter(prefix="/hr/travel-categories", tags=["HR — Travel Categories"])


def _to_resp(c: TravelCategory, count: Optional[int] = None) -> dict:
    return {
        "id": c.id, "code": c.code, "name": c.name, "description": c.description,
        "icon": c.icon, "color_hex": c.color_hex, "field_schema": c.field_schema or [],
        "default_travel_type": c.default_travel_type, "requires_attachment": c.requires_attachment,
        "sort_order": c.sort_order, "is_active": c.is_active, "created_at": c.created_at,
        "request_count": count,
    }


@router.get("/", response_model=TravelCategoryListResponse)
def list_categories(
    include_inactive: bool = False, q: Optional[str] = None,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_superuser),
):
    query = db.query(TravelCategory).filter(TravelCategory.is_deleted == False)  # noqa: E712
    if not include_inactive:
        query = query.filter(TravelCategory.is_active == True)  # noqa: E712
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(sa_func.lower(TravelCategory.name).like(like.lower()))
    rows = query.order_by(TravelCategory.sort_order.asc().nullslast(), TravelCategory.name.asc()).all()
    counts = dict(
        db.query(TravelRequest.category_id, sa_func.count(TravelRequest.id))
        .filter(TravelRequest.is_deleted == False)  # noqa: E712
        .group_by(TravelRequest.category_id).all()
    )
    return TravelCategoryListResponse(
        items=[_to_resp(c, int(counts.get(c.id, 0))) for c in rows], total=len(rows))


@router.post("/", response_model=TravelCategoryResponse, status_code=201)
def create_category(payload: TravelCategoryCreate, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_superuser)):
    if db.query(TravelCategory.id).filter(TravelCategory.code == payload.code).first():
        raise HTTPException(409, f"A category with code {payload.code} already exists")
    cat = TravelCategory(
        code=payload.code, name=payload.name, description=payload.description,
        icon=payload.icon, color_hex=payload.color_hex,
        field_schema=[f.model_dump() for f in payload.field_schema],
        default_travel_type=payload.default_travel_type,
        requires_attachment=payload.requires_attachment, sort_order=payload.sort_order,
        is_active=payload.is_active, created_by_id=current_user.id,
    )
    db.add(cat)
    db.flush()
    write_travel_audit(db, entity_type="CATEGORY", entity_id=cat.id,
                       action=TravelAuditAction.CATEGORY_CREATE, actor_id=current_user.id,
                       note=f"Category {cat.code}")
    db.commit()
    db.refresh(cat)
    return _to_resp(cat, 0)


@router.patch("/{category_id}", response_model=TravelCategoryResponse)
def update_category(category_id: UUID, payload: TravelCategoryUpdate, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_superuser)):
    cat = db.query(TravelCategory).filter(
        TravelCategory.id == category_id, TravelCategory.is_deleted == False).first()  # noqa: E712
    if not cat:
        raise HTTPException(404, "Category not found")
    data = payload.model_dump(exclude_unset=True)
    if "field_schema" in data and data["field_schema"] is not None:
        data["field_schema"] = [f.model_dump() if hasattr(f, "model_dump") else dict(f) for f in payload.field_schema]
    for k, v in data.items():
        setattr(cat, k, v)
    cat.updated_by_id = current_user.id
    write_travel_audit(db, entity_type="CATEGORY", entity_id=cat.id,
                       action=TravelAuditAction.CATEGORY_UPDATE, actor_id=current_user.id)
    db.commit()
    db.refresh(cat)
    return _to_resp(cat)


@router.delete("/{category_id}")
def delete_category(
    category_id: UUID,
    reason: Optional[str] = Query(None, max_length=400),
    deactivate: bool = Query(False, description="Deactivate (hide) instead of permanently deleting"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    cat = db.query(TravelCategory).filter(
        TravelCategory.id == category_id, TravelCategory.is_deleted == False).first()  # noqa: E712
    if not cat:
        raise HTTPException(404, "Category not found")
    reason_clean = (reason or "").strip()
    note_tail = f" — {reason_clean}" if reason_clean else ""
    in_use = db.query(TravelRequest.id).filter(
        TravelRequest.category_id == category_id, TravelRequest.is_deleted == False).first()  # noqa: E712

    # Don't orphan requests — a used category (or an explicit deactivate request)
    # is hidden, never hard-deleted. Either way the reason lands in the audit log.
    if in_use or deactivate:
        cat.is_active = False
        cat.updated_by_id = current_user.id
        prefix = "Deactivated (in use)" if in_use else "Deactivated"
        write_travel_audit(db, entity_type="CATEGORY", entity_id=cat.id,
                           action=TravelAuditAction.CATEGORY_UPDATE, actor_id=current_user.id,
                           note=f"{prefix}{note_tail}")
        db.commit()
        return {"success": True, "deactivated": True, "reason": reason_clean or None}

    cat.is_deleted = True
    cat.is_active = False
    cat.updated_by_id = current_user.id
    write_travel_audit(db, entity_type="CATEGORY", entity_id=cat.id,
                       action=TravelAuditAction.CATEGORY_DELETE, actor_id=current_user.id,
                       note=f"Deleted{note_tail}")
    db.commit()
    return {"success": True, "deactivated": False, "reason": reason_clean or None}
