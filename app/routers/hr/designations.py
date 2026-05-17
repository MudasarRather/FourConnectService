from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.designation import Designation
from app.schemas.hr.designation import (
    DesignationCreate, DesignationUpdate, DesignationResponse,
)
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/designations", tags=["HR — Designations"])


def _track_actor(db: Session, actor_id):
    db.info["audit_actor_id"] = str(actor_id)


@router.get("/", response_model=List[DesignationResponse])
def list_designations(
    department_id: Optional[UUID] = Query(None),
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(Designation)
    if not include_deleted:
        q = q.filter(Designation.is_deleted == False)  # noqa: E712
    if department_id:
        q = q.filter(Designation.department_id == department_id)
    return q.order_by(Designation.name).all()


@router.post("/", response_model=DesignationResponse, status_code=status.HTTP_201_CREATED)
def create_designation(
    payload: DesignationCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    if db.query(Designation).filter(Designation.code == payload.code).first():
        raise HTTPException(400, "Designation code already exists")
    d = Designation(**payload.model_dump(exclude_unset=True))
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


@router.get("/{designation_id}", response_model=DesignationResponse)
def get_designation(
    designation_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    d = db.query(Designation).filter(Designation.id == designation_id).first()
    if not d:
        raise HTTPException(404, "Designation not found")
    return d


@router.patch("/{designation_id}", response_model=DesignationResponse)
def update_designation(
    designation_id: UUID,
    payload: DesignationUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    d = db.query(Designation).filter(Designation.id == designation_id).first()
    if not d:
        raise HTTPException(404, "Designation not found")
    update = payload.model_dump(exclude_unset=True)
    if "code" in update and update["code"] != d.code:
        if db.query(Designation).filter(Designation.code == update["code"], Designation.id != designation_id).first():
            raise HTTPException(400, "Designation code already exists")
    for k, v in update.items():
        setattr(d, k, v)
    db.commit()
    db.refresh(d)
    return d


@router.delete("/{designation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_designation(
    designation_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    d = db.query(Designation).filter(Designation.id == designation_id).first()
    if not d:
        raise HTTPException(404, "Designation not found")
    d.is_deleted = True
    db.commit()
    return None
