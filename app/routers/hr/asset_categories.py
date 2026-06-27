"""HR Asset Management — category master (`/hr/asset-categories`)."""
from __future__ import annotations

from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.asset import Asset
from app.models.hr.asset_lifecycle import AssetCategory
from app.schemas.hr.asset_lifecycle import (
    AssetCategoryCreate, AssetCategoryUpdate, AssetCategoryResponse, AssetCategoryListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.assets.responses import to_category_response
from app.utils.hr.settings_audit import log_settings_change

router = APIRouter(prefix="/hr/asset-categories", tags=["HR — Asset Categories"])


def _asset_count(db: Session, category_id: UUID) -> int:
    return db.query(func.count(Asset.id)).filter(
        Asset.category_id == category_id, Asset.is_deleted == False,  # noqa: E712
    ).scalar() or 0


@router.get("/", response_model=AssetCategoryListResponse)
def list_categories(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AssetCategory).filter(AssetCategory.is_deleted == False)  # noqa: E712
    if is_active is not None:
        q = q.filter(AssetCategory.is_active == is_active)
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(or_(
            func.lower(AssetCategory.name).like(like),
            func.lower(AssetCategory.code).like(like),
        ))
    total = q.count()
    rows = q.order_by(AssetCategory.name.asc()).offset((page - 1) * limit).limit(limit).all()
    return AssetCategoryListResponse(
        items=[to_category_response(c, _asset_count(db, c.id)) for c in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.post("/", response_model=AssetCategoryResponse, status_code=http_status.HTTP_201_CREATED)
def create_category(
    payload: AssetCategoryCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if db.query(AssetCategory).filter(AssetCategory.code == payload.code, AssetCategory.is_deleted == False).first():  # noqa: E712
        raise HTTPException(400, "Category code already exists")
    c = AssetCategory(**payload.model_dump(), created_by_id=admin.id)
    db.add(c)
    db.flush()
    log_settings_change(db, "ASSET_CATEGORY", c.id, "CREATE", admin.id, after={"code": c.code}, note=c.name)
    db.commit()
    db.refresh(c)
    return to_category_response(c, 0)


@router.get("/{category_id}", response_model=AssetCategoryResponse)
def get_category(
    category_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    c = db.query(AssetCategory).filter(AssetCategory.id == category_id, AssetCategory.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Category not found")
    return to_category_response(c, _asset_count(db, c.id))


@router.patch("/{category_id}", response_model=AssetCategoryResponse)
def update_category(
    category_id: UUID,
    payload: AssetCategoryUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    c = db.query(AssetCategory).filter(AssetCategory.id == category_id, AssetCategory.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Category not found")
    data = payload.model_dump(exclude_unset=True)
    if "code" in data and data["code"] != c.code:
        if db.query(AssetCategory).filter(AssetCategory.code == data["code"], AssetCategory.is_deleted == False, AssetCategory.id != category_id).first():  # noqa: E712
            raise HTTPException(400, "Category code already exists")
    for k, v in data.items():
        setattr(c, k, v)
    log_settings_change(db, "ASSET_CATEGORY", c.id, "UPDATE", admin.id, note=c.name)
    db.commit()
    db.refresh(c)
    return to_category_response(c, _asset_count(db, c.id))


@router.delete("/{category_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    c = db.query(AssetCategory).filter(AssetCategory.id == category_id, AssetCategory.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Category not found")
    if _asset_count(db, category_id) > 0:
        raise HTTPException(409, "Category is referenced by assets; reassign them first.")
    c.is_deleted = True
    log_settings_change(db, "ASSET_CATEGORY", c.id, "DELETE", admin.id, note=c.name)
    db.commit()
