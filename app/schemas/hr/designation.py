from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class DesignationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    code: str = Field(..., min_length=1, max_length=30)
    grade_id: Optional[UUID] = None
    department_id: Optional[UUID] = None
    level: Optional[int] = None


class DesignationCreate(DesignationBase):
    model_config = ConfigDict(extra="ignore")


class DesignationUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    code: Optional[str] = None
    grade_id: Optional[UUID] = None
    department_id: Optional[UUID] = None
    level: Optional[int] = None


class DesignationResponse(DesignationBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
