"""HR Training & Development — Trainers database."""
from __future__ import annotations

from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.trainer import Trainer, TrainerType
from app.models.hr.training import TrainingProgram
from app.schemas.hr.trainer import TrainerCreate, TrainerUpdate, TrainerResponse
from app.models.hr.training_audit_log import TrainingAuditAction
from app.utils.hr.training.audit import write_training_audit
from app.utils.hr.training.service import user_name
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/training", tags=["HR — Training Trainers"])


def _program_count(db: Session, t: Trainer) -> int:
    if not t.user_id:
        return 0
    return int((
        db.query(func.count(TrainingProgram.id))
        .filter(TrainingProgram.trainer_user_id == t.user_id, TrainingProgram.is_deleted == False)  # noqa: E712
        .scalar()
    ) or 0)


def _resp(db: Session, t: Trainer) -> TrainerResponse:
    pc = _program_count(db, t)
    return TrainerResponse(
        id=t.id, name=t.name, trainer_type=t.trainer_type, user_id=t.user_id,
        user_name=user_name(db, t.user_id), email=t.email, phone=t.phone,
        organization=t.organization, specialization=t.specialization,
        hourly_rate=t.hourly_rate, currency=t.currency, rating_avg=t.rating_avg,
        rating_count=t.rating_count, program_count=int(pc or 0), is_active=t.is_active,
        created_at=t.created_at,
    )


@router.get("/trainers", response_model=List[TrainerResponse])
def list_trainers(
    trainer_type: Optional[TrainerType] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(Trainer).filter(Trainer.is_deleted == False)  # noqa: E712
    if trainer_type:
        q = q.filter(Trainer.trainer_type == trainer_type)
    if search:
        q = q.filter(Trainer.name.ilike(f"%{search}%"))
    rows = q.order_by(Trainer.name.asc()).all()
    return [_resp(db, t) for t in rows]


@router.post("/trainers", response_model=TrainerResponse, status_code=http_status.HTTP_201_CREATED)
def create_trainer(
    payload: TrainerCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    t = Trainer(**payload.model_dump(), created_by_id=admin.id)
    db.add(t)
    db.flush()
    write_training_audit(db, entity_type="TRAINER", entity_id=t.id,
                         action=TrainingAuditAction.CREATE, actor_id=admin.id, note=t.name)
    db.commit()
    db.refresh(t)
    return _resp(db, t)


@router.patch("/trainers/{trainer_id}", response_model=TrainerResponse)
def update_trainer(
    trainer_id: UUID,
    payload: TrainerUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    t = db.query(Trainer).filter(Trainer.id == trainer_id, Trainer.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Trainer not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    write_training_audit(db, entity_type="TRAINER", entity_id=t.id,
                         action=TrainingAuditAction.UPDATE, actor_id=admin.id)
    db.commit()
    db.refresh(t)
    return _resp(db, t)


@router.delete("/trainers/{trainer_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_trainer(
    trainer_id: UUID,
    reason: Optional[str] = Query(None, max_length=60),
    note: Optional[str] = Query(None, max_length=240),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    t = db.query(Trainer).filter(Trainer.id == trainer_id, Trainer.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Trainer not found")
    linked = _program_count(db, t)
    if linked:
        raise HTTPException(
            409,
            f"Cannot remove a trainer leading {linked} active program(s); "
            "reassign those programs to another trainer first.",
        )
    t.is_deleted = True
    audit_note = f"Removed trainer “{t.name}”"
    if reason:
        audit_note += f" · {reason}"
    write_training_audit(db, entity_type="TRAINER", entity_id=t.id,
                         action=TrainingAuditAction.DELETE, actor_id=admin.id,
                         note=audit_note[:300], payload={"reason": reason, "note": note, "name": t.name})
    db.commit()
