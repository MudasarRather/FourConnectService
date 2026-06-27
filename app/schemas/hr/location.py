from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

from app.models.hr.location import WorkLocationType


class WorkLocationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    type: WorkLocationType = WorkLocationType.HQ
    code: Optional[str] = Field(None, max_length=20)
    timezone: Optional[str] = Field(None, max_length=40)
    weekly_off_pattern: Optional[dict] = None


class WorkLocationCreate(WorkLocationBase):
    model_config = ConfigDict(extra="ignore", use_enum_values=True)


class WorkLocationUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=True)
    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    type: Optional[WorkLocationType] = None
    code: Optional[str] = None
    timezone: Optional[str] = None
    weekly_off_pattern: Optional[dict] = None


class WorkLocationResponse(WorkLocationBase):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)
    id: UUID
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
