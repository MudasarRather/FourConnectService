"""HR Training & Development — Training material repository (Drive-backed)."""
from __future__ import annotations

from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.training import TrainingProgram
from app.models.hr.training_material import TrainingMaterial
from app.schemas.hr.training_material import (
    TrainingMaterialCreate, TrainingMaterialUpdate, TrainingMaterialResponse,
)
from app.models.hr.training_audit_log import TrainingAuditAction
from app.utils.hr.training.audit import write_training_audit
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/training", tags=["HR — Training Materials"])


def _program_name(db: Session, pid) -> Optional[str]:
    if not pid:
        return None
    r = db.query(TrainingProgram.name).filter(TrainingProgram.id == pid).first()
    return r[0] if r else None


def _resp(db: Session, m: TrainingMaterial) -> TrainingMaterialResponse:
    return TrainingMaterialResponse(
        id=m.id, program_id=m.program_id, program_name=_program_name(db, m.program_id),
        title=m.title, material_type=m.material_type, drive_document_id=m.drive_document_id,
        external_url=m.external_url, file_url=m.file_url, description=m.description,
        sort_order=m.sort_order, is_active=m.is_active, created_at=m.created_at,
    )


def _validate_drive(db: Session, drive_document_id) -> None:
    if not drive_document_id:
        return
    try:
        from app.models.drive_document import DriveDocument
        exists = db.query(DriveDocument.id).filter(DriveDocument.id == drive_document_id).first()
        if not exists:
            raise HTTPException(404, "Linked drive document not found")
    except HTTPException:
        raise
    except Exception:
        # Drive model unavailable — skip validation rather than block.
        return


@router.get("/materials", response_model=List[TrainingMaterialResponse])
def list_materials(
    program_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(TrainingMaterial).filter(TrainingMaterial.is_deleted == False)  # noqa: E712
    if program_id:
        q = q.filter(TrainingMaterial.program_id == program_id)
    rows = q.order_by(TrainingMaterial.sort_order.asc().nullslast(), TrainingMaterial.created_at.desc()).all()
    return [_resp(db, m) for m in rows]


@router.post("/materials", response_model=TrainingMaterialResponse, status_code=http_status.HTTP_201_CREATED)
def create_material(
    payload: TrainingMaterialCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    _validate_drive(db, payload.drive_document_id)
    m = TrainingMaterial(**payload.model_dump(), created_by_id=admin.id)
    db.add(m)
    db.flush()
    write_training_audit(db, entity_type="MATERIAL", entity_id=m.id,
                         action=TrainingAuditAction.CREATE, actor_id=admin.id, note=m.title)
    db.commit()
    db.refresh(m)
    return _resp(db, m)


@router.patch("/materials/{material_id}", response_model=TrainingMaterialResponse)
def update_material(
    material_id: UUID,
    payload: TrainingMaterialUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    m = db.query(TrainingMaterial).filter(TrainingMaterial.id == material_id, TrainingMaterial.is_deleted == False).first()  # noqa: E712
    if not m:
        raise HTTPException(404, "Material not found")
    data = payload.model_dump(exclude_unset=True)
    if "drive_document_id" in data:
        _validate_drive(db, data["drive_document_id"])
    for k, v in data.items():
        setattr(m, k, v)
    write_training_audit(db, entity_type="MATERIAL", entity_id=m.id,
                         action=TrainingAuditAction.UPDATE, actor_id=admin.id)
    db.commit()
    db.refresh(m)
    return _resp(db, m)


@router.delete("/materials/{material_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_material(
    material_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    m = db.query(TrainingMaterial).filter(TrainingMaterial.id == material_id, TrainingMaterial.is_deleted == False).first()  # noqa: E712
    if not m:
        raise HTTPException(404, "Material not found")
    m.is_deleted = True
    write_training_audit(db, entity_type="MATERIAL", entity_id=m.id,
                         action=TrainingAuditAction.DELETE, actor_id=admin.id)
    db.commit()
