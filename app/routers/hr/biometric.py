"""HR Biometric Devices — SKELETON for Phase 2.X.

`POST /sync` returns `{status: "not_implemented"}` until vendor adapters land.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.biometric_device import BiometricDevice
from app.schemas.hr.attendance import (
    BiometricDeviceCreate, BiometricDeviceResponse, BiometricDeviceListResponse,
)
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/biometric", tags=["HR — Biometric"])


def _to_response(d: BiometricDevice) -> BiometricDeviceResponse:
    return BiometricDeviceResponse(
        id=d.id, device_id=d.device_id, name=d.name, device_type=d.device_type,
        location_id=d.location_id, ip_address=d.ip_address,
        last_sync_at=d.last_sync_at, last_sync_status=d.last_sync_status,
        last_sync_message=d.last_sync_message, is_active=bool(d.is_active),
        created_at=d.created_at,
    )


@router.get("/", response_model=BiometricDeviceListResponse)
def list_devices(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(BiometricDevice)
        .filter(BiometricDevice.is_deleted == False)  # noqa: E712
        .order_by(BiometricDevice.created_at.desc())
        .all()
    )
    return BiometricDeviceListResponse(items=[_to_response(r) for r in rows])


@router.post("/", response_model=BiometricDeviceResponse, status_code=http_status.HTTP_201_CREATED)
def create_device(
    payload: BiometricDeviceCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if db.query(BiometricDevice).filter(BiometricDevice.device_id == payload.device_id).first():
        raise HTTPException(400, "Device ID already exists")
    d = BiometricDevice(**payload.model_dump(), created_by_id=admin.id)
    db.add(d)
    db.commit()
    db.refresh(d)
    return _to_response(d)


@router.delete("/{device_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_device(
    device_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    d = db.query(BiometricDevice).filter(BiometricDevice.id == device_id).first()
    if not d:
        raise HTTPException(404, "Device not found")
    d.is_deleted = True
    db.commit()


@router.post("/sync")
def sync_devices(
    device_id: Optional[UUID] = None,
    _admin: User = Depends(get_current_superuser),
):
    # TODO: vendor adapters per BiometricDeviceType.
    return {"status": "not_implemented", "message": "Vendor sync adapters land in Phase 2.X."}
