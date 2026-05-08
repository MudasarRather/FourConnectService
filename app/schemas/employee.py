from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from uuid import UUID

class EmployeeBase(BaseModel):
    employee_code: str
    phone: str

class EmployeeCreate(EmployeeBase):
    pass

class EmployeeResponse(EmployeeBase):
    id: UUID
    is_registered: bool
    created_at: datetime

    class Config:
        orm_mode = True
