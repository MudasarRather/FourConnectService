"""HR Induction schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.hr.induction import (
    InductionType, AttendanceStatus,
)


class InductionSessionCreate(BaseModel):
    name: str
    session_type: InductionType
    scheduled_at: datetime
    duration_minutes: int = 60
    location: Optional[str] = None
    meeting_url: Optional[str] = None
    host_user_id: Optional[UUID] = None
    capacity: Optional[int] = None
    agenda: Optional[str] = None
    materials_url: Optional[str] = None


class InductionSessionUpdate(BaseModel):
    name: Optional[str] = None
    session_type: Optional[InductionType] = None
    scheduled_at: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    location: Optional[str] = None
    meeting_url: Optional[str] = None
    host_user_id: Optional[UUID] = None
    capacity: Optional[int] = None
    agenda: Optional[str] = None
    materials_url: Optional[str] = None
    is_active: Optional[bool] = None


class InductionSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    session_type: InductionType
    scheduled_at: datetime
    duration_minutes: Optional[int] = None
    location: Optional[str] = None
    meeting_url: Optional[str] = None
    host_user_id: Optional[UUID] = None
    host_name: Optional[str] = None
    capacity: Optional[int] = None
    agenda: Optional[str] = None
    materials_url: Optional[str] = None
    is_active: bool
    attendee_count: Optional[int] = None
    confirmed_count: Optional[int] = None


class InductionAttendanceCreate(BaseModel):
    session_id: UUID
    employee_id: UUID
    process_id: Optional[UUID] = None


class InductionAttendanceUpdate(BaseModel):
    status: Optional[AttendanceStatus] = None
    rating: Optional[Decimal] = None
    feedback: Optional[str] = None


class InductionAttendanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    session_id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    process_id: Optional[UUID] = None
    status: AttendanceStatus
    rating: Optional[Decimal] = None
    feedback: Optional[str] = None
    rsvp_at: Optional[datetime] = None
    attended_at: Optional[datetime] = None


class InductionBulkInviteBody(BaseModel):
    employee_ids: List[UUID]
