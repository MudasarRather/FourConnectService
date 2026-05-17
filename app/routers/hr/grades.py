from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.grade import Grade
from app.schemas.hr.grade import GradeCreate, GradeUpdate, GradeResponse
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/grades", tags=["HR — Grades"])


def _track_actor(db: Session, actor_id):
    db.info["audit_actor_id"] = str(actor_id)


@router.get("/", response_model=List[GradeResponse])
def list_grades(
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(Grade)
    if not include_deleted:
        q = q.filter(Grade.is_deleted == False)  # noqa: E712
    return q.order_by(Grade.level.nulls_last(), Grade.code).all()


@router.post("/", response_model=GradeResponse, status_code=status.HTTP_201_CREATED)
def create_grade(
    payload: GradeCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    if db.query(Grade).filter(Grade.code == payload.code).first():
        raise HTTPException(400, "Grade code already exists")
    g = Grade(**payload.model_dump(exclude_unset=True))
    db.add(g)
    db.commit()
    db.refresh(g)
    return g


@router.get("/{grade_id}", response_model=GradeResponse)
def get_grade(
    grade_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    g = db.query(Grade).filter(Grade.id == grade_id).first()
    if not g:
        raise HTTPException(404, "Grade not found")
    return g


@router.patch("/{grade_id}", response_model=GradeResponse)
def update_grade(
    grade_id: UUID,
    payload: GradeUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    g = db.query(Grade).filter(Grade.id == grade_id).first()
    if not g:
        raise HTTPException(404, "Grade not found")
    update = payload.model_dump(exclude_unset=True)
    if "code" in update and update["code"] != g.code:
        if db.query(Grade).filter(Grade.code == update["code"], Grade.id != grade_id).first():
            raise HTTPException(400, "Grade code already exists")
    for k, v in update.items():
        setattr(g, k, v)
    db.commit()
    db.refresh(g)
    return g


@router.delete("/{grade_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_grade(
    grade_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _track_actor(db, admin.id)
    g = db.query(Grade).filter(Grade.id == grade_id).first()
    if not g:
        raise HTTPException(404, "Grade not found")
    g.is_deleted = True
    db.commit()
    return None
