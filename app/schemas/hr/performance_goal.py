"""Schemas for HR Performance — Goals & OKRs (inputs only; responses are dicts)."""
from datetime import date
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GoalCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    employee_id: UUID
    parent_id: Optional[UUID] = None
    goal_type: str = Field("OBJECTIVE", max_length=20)
    title: str = Field(..., max_length=200)
    description: Optional[str] = None
    category: Optional[str] = Field(None, max_length=40)
    cycle: Optional[str] = Field(None, max_length=20)
    period_label: Optional[str] = Field(None, max_length=60)
    weight: Optional[float] = Field(0, ge=0, le=100)
    metric_type: Optional[str] = Field("PERCENT", max_length=20)
    start_value: Optional[float] = 0
    target_value: Optional[float] = None
    current_value: Optional[float] = 0
    unit: Optional[str] = Field(None, max_length=20)
    status: Optional[str] = Field(None, max_length=20)
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    review_id: Optional[UUID] = None


class GoalKeyResultIn(BaseModel):
    """A nested key result when creating an objective in one shot."""
    model_config = ConfigDict(extra="ignore")
    title: str = Field(..., max_length=200)
    metric_type: Optional[str] = Field("PERCENT", max_length=20)
    start_value: Optional[float] = 0
    target_value: Optional[float] = None
    current_value: Optional[float] = 0
    unit: Optional[str] = Field(None, max_length=20)
    weight: Optional[float] = Field(0, ge=0, le=100)


class ObjectiveCreate(BaseModel):
    """Create an objective + its key results in a single call."""
    model_config = ConfigDict(extra="ignore")
    employee_id: UUID
    title: str = Field(..., max_length=200)
    description: Optional[str] = None
    category: Optional[str] = Field(None, max_length=40)
    cycle: Optional[str] = Field(None, max_length=20)
    period_label: Optional[str] = Field(None, max_length=60)
    weight: Optional[float] = Field(0, ge=0, le=100)
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    review_id: Optional[UUID] = None
    key_results: List[GoalKeyResultIn] = Field(default_factory=list)


class GoalUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    weight: Optional[float] = Field(None, ge=0, le=100)
    metric_type: Optional[str] = None
    start_value: Optional[float] = None
    target_value: Optional[float] = None
    current_value: Optional[float] = None
    unit: Optional[str] = None
    progress: Optional[float] = Field(None, ge=0, le=100)
    status: Optional[str] = None
    start_date: Optional[date] = None
    due_date: Optional[date] = None


class GoalCheckIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    current_value: Optional[float] = None
    progress: Optional[float] = Field(None, ge=0, le=100)
    status: Optional[str] = None
    note: Optional[str] = None
