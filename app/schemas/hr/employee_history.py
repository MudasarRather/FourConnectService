from datetime import date, datetime
from typing import Optional, Any, Dict
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EmployeeHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: UUID
    employee_id: UUID
    change_type: str
    from_value_json: Optional[Dict[str, Any]] = None
    to_value_json: Optional[Dict[str, Any]] = None
    effective_date: Optional[date] = None
    reason: Optional[str] = None
    actioned_by_id: Optional[UUID] = None
    created_at: datetime

    # Joined display
    actioned_by_name: Optional[str] = None
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
