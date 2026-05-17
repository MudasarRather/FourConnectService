from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.location import WorkLocation
from app.schemas.hr.location import (
    WorkLocationCreate, WorkLocationUpdate, WorkLocationResponse,
)
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/locations", tags=["HR — Work Locations"])


def _track_actor(db: Session, actor_id):
    db.info["audit_actor_id"] = str(actor_id)


@router.get("/", response_model=List[WorkLocationResponse])
def list_locations(
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(WorkLocation)
    if not include_deleted:
        q = q.filter(WorkLocation.is_deleted == False)  # noqa: E712
    return q.order_by(WorkLocation.name).all()


@router.post("/", response_model=WorkLocationResponse, status_code=status.HTTP_201_CREATED)
def create_location(
    payload: WorkLocationCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    if db.query(WorkLocation).filter(WorkLocation.name == payload.name).first():
        raise HTTPException(400, "Work location with this name already exists")
    loc = WorkLocation(**payload.model_dump(exclude_unset=True))
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return loc


@router.get("/{location_id}", response_model=WorkLocationResponse)
def get_location(
    location_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    loc = db.query(WorkLocation).filter(WorkLocation.id == location_id).first()
    if not loc:
        raise HTTPException(404, "Work location not found")
    return loc


@router.patch("/{location_id}", response_model=WorkLocationResponse)
def update_location(
    location_id: UUID,
    payload: WorkLocationUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    loc = db.query(WorkLocation).filter(WorkLocation.id == location_id).first()
    if not loc:
        raise HTTPException(404, "Work location not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(loc, k, v)
    db.commit()
    db.refresh(loc)
    return loc


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location(
    location_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    loc = db.query(WorkLocation).filter(WorkLocation.id == location_id).first()
    if not loc:
        raise HTTPException(404, "Work location not found")
    loc.is_deleted = True
    db.commit()
    return None
