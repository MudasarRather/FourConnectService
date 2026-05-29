"""HR Geo-Fences — SKELETON for Phase 2.X."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.geo_fence import GeoFence
from app.schemas.hr.attendance import (
    GeoFenceCreate, GeoFenceResponse, GeoFenceListResponse,
)
from app.utils.dependencies import get_current_superuser, get_current_user

router = APIRouter(prefix="/hr/geo-fences", tags=["HR — Geo Fences"])


@router.get("/active", response_model=GeoFenceListResponse)
def list_active_fences_for_user(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """User-accessible read-only list of currently active geo-fences.

    The self-service attendance page uses this to compute whether the
    user is inside an authorised punching zone before letting them clock in.
    Only the minimal fields needed to draw a fence on the client are returned
    (the schema is identical to the admin list — there are no secrets here).
    """
    rows = (
        db.query(GeoFence)
        .filter(GeoFence.is_deleted == False)  # noqa: E712
        .filter(GeoFence.is_active == True)  # noqa: E712
        .order_by(GeoFence.created_at.desc())
        .limit(200)
        .all()
    )
    return GeoFenceListResponse(items=[_to_response(r) for r in rows])


def _to_response(g: GeoFence) -> GeoFenceResponse:
    return GeoFenceResponse(
        id=g.id, name=g.name, location_id=g.location_id,
        center_lat=float(g.center_lat), center_lng=float(g.center_lng),
        radius_meters=int(g.radius_meters), is_active=bool(g.is_active),
        created_at=g.created_at,
    )


@router.get("/", response_model=GeoFenceListResponse)
def list_fences(
    location_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(GeoFence).filter(GeoFence.is_deleted == False)  # noqa: E712
    if location_id:
        q = q.filter(GeoFence.location_id == location_id)
    rows = q.order_by(GeoFence.created_at.desc()).limit(500).all()
    return GeoFenceListResponse(items=[_to_response(r) for r in rows])


@router.post("/", response_model=GeoFenceResponse, status_code=http_status.HTTP_201_CREATED)
def create_fence(
    payload: GeoFenceCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    g = GeoFence(**payload.model_dump(), created_by_id=admin.id)
    db.add(g)
    db.commit()
    db.refresh(g)
    return _to_response(g)


@router.delete("/{fence_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_fence(
    fence_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    g = db.query(GeoFence).filter(GeoFence.id == fence_id).first()
    if not g:
        raise HTTPException(404, "Fence not found")
    g.is_deleted = True
    db.commit()
