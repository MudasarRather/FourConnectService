"""HR Training schemas."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.hr.training import (
    TrainingType, TrainingAssignmentStatus,
)


class TrainingProgramCreate(BaseModel):
    name: str
    code: Optional[str] = None
    training_type: TrainingType
    description: Optional[str] = None
    duration_hours: Optional[Decimal] = None
    trainer_user_id: Optional[UUID] = None
    certification_required: bool = False
    is_mandatory_for_new_joiners: bool = False
    materials_url: Optional[str] = None


class TrainingProgramUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    training_type: Optional[TrainingType] = None
    description: Optional[str] = None
    duration_hours: Optional[Decimal] = None
    trainer_user_id: Optional[UUID] = None
    certification_required: Optional[bool] = None
    is_mandatory_for_new_joiners: Optional[bool] = None
    materials_url: Optional[str] = None
    is_active: Optional[bool] = None


class TrainingProgramResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: Optional[str] = None
    training_type: TrainingType
    description: Optional[str] = None
    duration_hours: Optional[Decimal] = None
    trainer_user_id: Optional[UUID] = None
    trainer_name: Optional[str] = None
    certification_required: bool
    is_mandatory_for_new_joiners: bool
    materials_url: Optional[str] = None
    is_active: bool
    created_at: datetime


class TrainingAssignmentCreate(BaseModel):
    program_id: UUID
    employee_id: UUID
    process_id: Optional[UUID] = None
    due_date: Optional[date] = None
    notes: Optional[str] = None


class TrainingAssignmentUpdate(BaseModel):
    status: Optional[TrainingAssignmentStatus] = None
    due_date: Optional[date] = None
    completion_date: Optional[date] = None
    score: Optional[Decimal] = None
    certification_url: Optional[str] = None
    notes: Optional[str] = None


class TrainingAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    program_id: UUID
    program_name: Optional[str] = None
    program_type: Optional[TrainingType] = None
    employee_id: UUID
    employee_name: Optional[str] = None
    process_id: Optional[UUID] = None
    assigned_date: date
    due_date: Optional[date] = None
    completion_date: Optional[date] = None
    status: TrainingAssignmentStatus
    score: Optional[Decimal] = None
    certification_url: Optional[str] = None
    notes: Optional[str] = None
