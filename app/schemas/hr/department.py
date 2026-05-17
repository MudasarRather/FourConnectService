from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class DepartmentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    code: str = Field(..., min_length=1, max_length=20)
    parent_department_id: Optional[UUID] = None
    head_employee_id: Optional[UUID] = None


class DepartmentCreate(DepartmentBase):
    model_config = ConfigDict(extra="ignore")


class DepartmentUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    code: Optional[str] = None
    parent_department_id: Optional[UUID] = None
    head_employee_id: Optional[UUID] = None


class DepartmentResponse(DepartmentBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
