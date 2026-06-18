"""HR Training & Development — Trainer schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.hr.trainer import TrainerType


class TrainerCreate(BaseModel):
    name: str
    trainer_type: TrainerType = TrainerType.INTERNAL
    user_id: Optional[UUID] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    organization: Optional[str] = None
    specialization: Optional[str] = None
    hourly_rate: Optional[Decimal] = None
    currency: str = "INR"
    is_active: bool = True


class TrainerUpdate(BaseModel):
    name: Optional[str] = None
    trainer_type: Optional[TrainerType] = None
    user_id: Optional[UUID] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    organization: Optional[str] = None
    specialization: Optional[str] = None
    hourly_rate: Optional[Decimal] = None
    is_active: Optional[bool] = None


class TrainerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    trainer_type: TrainerType
    user_id: Optional[UUID] = None
    user_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    organization: Optional[str] = None
    specialization: Optional[str] = None
    hourly_rate: Optional[Decimal] = None
    currency: str
    rating_avg: Optional[Decimal] = None
    rating_count: int
    program_count: Optional[int] = None
    is_active: bool
    created_at: datetime
