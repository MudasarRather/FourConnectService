"""HR Training & Development — Training material schemas."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.hr.training_material import MaterialType


class TrainingMaterialCreate(BaseModel):
    program_id: Optional[UUID] = None
    title: str
    material_type: MaterialType = MaterialType.DOCUMENT
    drive_document_id: Optional[UUID] = None
    external_url: Optional[str] = None
    file_url: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None


class TrainingMaterialUpdate(BaseModel):
    program_id: Optional[UUID] = None
    title: Optional[str] = None
    material_type: Optional[MaterialType] = None
    drive_document_id: Optional[UUID] = None
    external_url: Optional[str] = None
    file_url: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class TrainingMaterialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    program_id: Optional[UUID] = None
    program_name: Optional[str] = None
    title: str
    material_type: MaterialType
    drive_document_id: Optional[UUID] = None
    external_url: Optional[str] = None
    file_url: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: bool
    created_at: datetime
