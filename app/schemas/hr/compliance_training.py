"""HR Training & Development — Compliance training schemas."""
from datetime import date, datetime
from typing import Optional, List, Any, Dict
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.hr.compliance_training import ComplianceFrequency


class ComplianceTrainingCreate(BaseModel):
    program_id: UUID
    frequency: ComplianceFrequency = ComplianceFrequency.ANNUAL
    validity_months: Optional[int] = None
    grace_period_days: int = 0
    applies_to: Optional[Dict[str, Any]] = None
    auto_reassign: bool = True
    due_days_after_assign: int = 30


class ComplianceTrainingUpdate(BaseModel):
    frequency: Optional[ComplianceFrequency] = None
    validity_months: Optional[int] = None
    grace_period_days: Optional[int] = None
    applies_to: Optional[Dict[str, Any]] = None
    auto_reassign: Optional[bool] = None
    due_days_after_assign: Optional[int] = None
    is_active: Optional[bool] = None


class ComplianceTrainingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    program_id: UUID
    program_name: Optional[str] = None
    program_type: Optional[str] = None
    frequency: ComplianceFrequency
    validity_months: Optional[int] = None
    grace_period_days: int
    applies_to: Optional[Dict[str, Any]] = None
    auto_reassign: bool
    due_days_after_assign: int
    is_active: bool
    # rollup
    eligible_count: Optional[int] = None
    compliant_count: Optional[int] = None
    overdue_count: Optional[int] = None
    completion_rate: Optional[float] = None
    created_at: datetime


class ComplianceStatusRow(BaseModel):
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    program_id: UUID
    program_name: Optional[str] = None
    last_completed: Optional[date] = None
    valid_until: Optional[date] = None
    state: str  # COMPLIANT | DUE | OVERDUE | NEVER


class ComplianceReassignResult(BaseModel):
    created: int
    skipped: int
    eligible: int
    assignment_ids: List[UUID] = []
