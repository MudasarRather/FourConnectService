"""HR Asset Management — vendor master (`/hr/asset-vendors`)."""
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
from app.models.hr.asset_lifecycle import Vendor
from app.schemas.hr.asset_lifecycle import (
    VendorCreate, VendorUpdate, VendorResponse, VendorListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.assets.responses import to_vendor_response

router = APIRouter(prefix="/hr/asset-vendors", tags=["HR — Asset Vendors"])


def _asset_count(db: Session, vendor_id: UUID) -> int:
    return db.query(func.count(Asset.id)).filter(
        Asset.vendor_id == vendor_id, Asset.is_deleted == False,  # noqa: E712
    ).scalar() or 0


@router.get("/", response_model=VendorListResponse)
def list_vendors(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(Vendor).filter(Vendor.is_deleted == False)  # noqa: E712
    if is_active is not None:
        q = q.filter(Vendor.is_active == is_active)
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(or_(
            func.lower(Vendor.name).like(like),
            func.lower(Vendor.code).like(like),
            func.lower(Vendor.contact_person).like(like),
        ))
    total = q.count()
    rows = q.order_by(Vendor.name.asc()).offset((page - 1) * limit).limit(limit).all()
    return VendorListResponse(
        items=[to_vendor_response(v, _asset_count(db, v.id)) for v in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.post("/", response_model=VendorResponse, status_code=http_status.HTTP_201_CREATED)
def create_vendor(
    payload: VendorCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if payload.code and db.query(Vendor).filter(Vendor.code == payload.code, Vendor.is_deleted == False).first():  # noqa: E712
        raise HTTPException(400, "Vendor code already exists")
    v = Vendor(**payload.model_dump(), created_by_id=admin.id)
    db.add(v)
    db.commit()
    db.refresh(v)
    return to_vendor_response(v, 0)


@router.get("/{vendor_id}", response_model=VendorResponse)
def get_vendor(
    vendor_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    v = db.query(Vendor).filter(Vendor.id == vendor_id, Vendor.is_deleted == False).first()  # noqa: E712
    if not v:
        raise HTTPException(404, "Vendor not found")
    return to_vendor_response(v, _asset_count(db, v.id))


@router.patch("/{vendor_id}", response_model=VendorResponse)
def update_vendor(
    vendor_id: UUID,
    payload: VendorUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    v = db.query(Vendor).filter(Vendor.id == vendor_id, Vendor.is_deleted == False).first()  # noqa: E712
    if not v:
        raise HTTPException(404, "Vendor not found")
    data = payload.model_dump(exclude_unset=True)
    if data.get("code") and data["code"] != v.code:
        if db.query(Vendor).filter(Vendor.code == data["code"], Vendor.is_deleted == False, Vendor.id != vendor_id).first():  # noqa: E712
            raise HTTPException(400, "Vendor code already exists")
    for k, val in data.items():
        setattr(v, k, val)
    db.commit()
    db.refresh(v)
    return to_vendor_response(v, _asset_count(db, v.id))


@router.delete("/{vendor_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_vendor(
    vendor_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    v = db.query(Vendor).filter(Vendor.id == vendor_id, Vendor.is_deleted == False).first()  # noqa: E712
    if not v:
        raise HTTPException(404, "Vendor not found")
    if _asset_count(db, vendor_id) > 0:
        raise HTTPException(409, "Vendor is referenced by assets; reassign them first.")
    v.is_deleted = True
    db.commit()
