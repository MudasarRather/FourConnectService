"""HR Asset Management — asset-type catalog (`/hr/asset-types`).

The user-manageable list of asset *types* (the physical-form tag stored on
``Asset.asset_type``). Built-ins are seeded with ``is_system=True`` (code locked +
delete-protected → deactivate only); admins can add their own. Distinct from
``AssetCategory`` (the depreciation/grouping taxonomy).
"""
from __future__ import annotations

import re
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.asset import Asset
from app.models.hr.asset_lifecycle import AssetTypeDef
from app.schemas.hr.asset_lifecycle import (
    AssetTypeDefCreate, AssetTypeDefUpdate, AssetTypeDefResponse, AssetTypeDefListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.settings_audit import log_settings_change

router = APIRouter(prefix="/hr/asset-types", tags=["HR — Asset Types"])


def _normalize_code(raw: str) -> str:
    """LAPTOP-style code: upper snake, alnum + underscore only."""
    code = re.sub(r"[^A-Za-z0-9]+", "_", (raw or "").strip()).strip("_").upper()
    return code[:40]


def _asset_count(db: Session, code: str) -> int:
    return db.query(func.count(Asset.id)).filter(
        Asset.asset_type == code, Asset.is_deleted == False,  # noqa: E712
    ).scalar() or 0


def _to_response(db: Session, t: AssetTypeDef) -> AssetTypeDefResponse:
    r = AssetTypeDefResponse.model_validate(t)
    r.asset_count = _asset_count(db, t.code)
    return r


@router.get("/", response_model=AssetTypeDefListResponse)
def list_types(
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AssetTypeDef).filter(AssetTypeDef.is_deleted == False)  # noqa: E712
    if is_active is not None:
        q = q.filter(AssetTypeDef.is_active == is_active)
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(or_(
            func.lower(AssetTypeDef.label).like(like),
            func.lower(AssetTypeDef.code).like(like),
        ))
    rows = q.order_by(
        AssetTypeDef.sort_order.asc(), AssetTypeDef.is_system.desc(), AssetTypeDef.label.asc(),
    ).all()
    return AssetTypeDefListResponse(items=[_to_response(db, t) for t in rows], total=len(rows))


@router.post("/", response_model=AssetTypeDefResponse, status_code=http_status.HTTP_201_CREATED)
def create_type(
    payload: AssetTypeDefCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    code = _normalize_code(payload.code)
    if not code:
        raise HTTPException(400, "A valid code is required")
    if db.query(AssetTypeDef).filter(AssetTypeDef.code == code, AssetTypeDef.is_deleted == False).first():  # noqa: E712
        raise HTTPException(400, "An asset type with this code already exists")
    t = AssetTypeDef(
        code=code, label=(payload.label or code.title()).strip(),
        icon=payload.icon, sort_order=payload.sort_order or 0,
        is_active=payload.is_active, is_system=False, created_by_id=admin.id,
    )
    db.add(t)
    db.flush()
    log_settings_change(db, "ASSET_TYPE", t.id, "CREATE", admin.id, after={"code": t.code}, note=t.label)
    db.commit()
    db.refresh(t)
    return _to_response(db, t)


@router.patch("/{type_id}", response_model=AssetTypeDefResponse)
def update_type(
    type_id: UUID,
    payload: AssetTypeDefUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    t = db.query(AssetTypeDef).filter(AssetTypeDef.id == type_id, AssetTypeDef.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Asset type not found")
    data = payload.model_dump(exclude_unset=True)
    # Built-in codes are locked (assets already store that string); label/icon/active stay editable.
    if "code" in data and data["code"] is not None:
        new_code = _normalize_code(data["code"])
        if t.is_system and new_code != t.code:
            raise HTTPException(400, "Built-in type codes are locked")
        if new_code != t.code:
            if db.query(AssetTypeDef).filter(AssetTypeDef.code == new_code, AssetTypeDef.is_deleted == False, AssetTypeDef.id != type_id).first():  # noqa: E712
                raise HTTPException(400, "An asset type with this code already exists")
            # Re-point existing assets so the rename is non-destructive.
            db.query(Asset).filter(Asset.asset_type == t.code).update({Asset.asset_type: new_code})
            t.code = new_code
        data.pop("code", None)
    for k, v in data.items():
        setattr(t, k, v)
    log_settings_change(db, "ASSET_TYPE", t.id, "UPDATE", admin.id, note=t.label)
    db.commit()
    db.refresh(t)
    return _to_response(db, t)


@router.delete("/{type_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_type(
    type_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    t = db.query(AssetTypeDef).filter(AssetTypeDef.id == type_id, AssetTypeDef.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Asset type not found")
    if t.is_system:
        raise HTTPException(403, "Built-in types can't be deleted — deactivate it instead.")
    if _asset_count(db, t.code) > 0:
        raise HTTPException(409, "Type is in use by assets; re-type them or deactivate instead.")
    t.is_deleted = True
    log_settings_change(db, "ASSET_TYPE", t.id, "DELETE", admin.id, note=t.label)
    db.commit()
