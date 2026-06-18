"""HR Training & Development — Training request schemas."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Any, Dict
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.hr.training_request import TrainingRequestStatus


class TrainingRequestCreate(BaseModel):
    program_id: Optional[UUID] = None
    title: str
    description: Optional[str] = None
    justification: Optional[str] = None
    external_provider: Optional[str] = None
    estimated_cost: Optional[Decimal] = None
    currency: str = "INR"
    preferred_start_date: Optional[date] = None
    # Admin may create on behalf of an employee; self-service infers it.
    employee_id: Optional[UUID] = None


class TrainingRequestUpdate(BaseModel):
    program_id: Optional[UUID] = None
    title: Optional[str] = None
    description: Optional[str] = None
    justification: Optional[str] = None
    external_provider: Optional[str] = None
    estimated_cost: Optional[Decimal] = None
    preferred_start_date: Optional[date] = None


class TrainingRequestDecideInput(BaseModel):
    decision: str  # APPROVE | REJECT | RETURN
    notes: Optional[str] = None


class TrainingRequestFulfillInput(BaseModel):
    due_date: Optional[date] = None
    notes: Optional[str] = None
    # Lets HR attach a program when fulfilling a request that was raised against
    # an external provider (no linked program). Ignored when the request already
    # has a program_id.
    program_id: Optional[UUID] = None


class TrainingRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    request_number: str
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    designation_name: Optional[str] = None
    program_id: Optional[UUID] = None
    program_name: Optional[str] = None
    title: str
    description: Optional[str] = None
    justification: Optional[str] = None
    external_provider: Optional[str] = None
    estimated_cost: Optional[Decimal] = None
    currency: str
    preferred_start_date: Optional[date] = None
    status: TrainingRequestStatus
    approval_steps: Optional[List[Dict[str, Any]]] = None
    current_step: int
    approved_at: Optional[datetime] = None
    approver_notes: Optional[str] = None
    reject_reason: Optional[str] = None
    return_reason: Optional[str] = None
    resulting_assignment_id: Optional[UUID] = None
    submitted_at: Optional[datetime] = None
    created_at: datetime
    can_edit: Optional[bool] = None
    can_withdraw: Optional[bool] = None
