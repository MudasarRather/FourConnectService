"""HR Training & Development — Assessment schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Any, Dict
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.hr.assessment import AssessmentType


class AssessmentCreate(BaseModel):
    program_id: UUID
    title: str
    assessment_type: AssessmentType = AssessmentType.QUIZ
    pass_score: Decimal = Decimal("60")
    max_score: Decimal = Decimal("100")
    max_attempts: Optional[int] = None
    duration_minutes: Optional[int] = None
    questions: Optional[List[Dict[str, Any]]] = None


class AssessmentUpdate(BaseModel):
    title: Optional[str] = None
    assessment_type: Optional[AssessmentType] = None
    pass_score: Optional[Decimal] = None
    max_score: Optional[Decimal] = None
    max_attempts: Optional[int] = None
    duration_minutes: Optional[int] = None
    questions: Optional[List[Dict[str, Any]]] = None
    is_active: Optional[bool] = None


class AssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    program_id: UUID
    program_name: Optional[str] = None
    title: str
    assessment_type: AssessmentType
    pass_score: Decimal
    max_score: Decimal
    max_attempts: Optional[int] = None
    duration_minutes: Optional[int] = None
    is_active: bool
    result_count: Optional[int] = None
    pass_count: Optional[int] = None
    created_at: datetime


class AssessmentResultCreate(BaseModel):
    assessment_id: UUID
    employee_id: UUID
    assignment_id: Optional[UUID] = None
    score: Decimal
    attempt_number: Optional[int] = None
    answers: Optional[Dict[str, Any]] = None


class AssessmentResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    assessment_id: UUID
    assessment_title: Optional[str] = None
    employee_id: UUID
    employee_name: Optional[str] = None
    assignment_id: Optional[UUID] = None
    attempt_number: int
    score: Optional[Decimal] = None
    passed: Optional[bool] = None
    submitted_at: Optional[datetime] = None
    created_at: datetime
