"""Schemas for HR Performance — PIP (inputs only; responses are dicts)."""
from datetime import date
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PipObjectiveIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(..., max_length=200)
    measure: Optional[str] = None
    target: Optional[str] = None
    status: Optional[str] = Field("OPEN", max_length=12)


class PipCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    employee_id: UUID
    review_id: Optional[UUID] = None
    manager_id: Optional[UUID] = None
    title: str = Field(..., max_length=200)
    reason: Optional[str] = None
    expectations: Optional[str] = None
    support: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    objectives: List[PipObjectiveIn] = Field(default_factory=list)
    activate: bool = False          # True → open as ACTIVE rather than DRAFT


class PipUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: Optional[str] = None
    reason: Optional[str] = None
    expectations: Optional[str] = None
    support: Optional[str] = None
    manager_id: Optional[UUID] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    objectives: Optional[List[PipObjectiveIn]] = None
    outcome: Optional[str] = None


class PipCheckIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    note: str
    rating: Optional[str] = Field(None, max_length=24)   # e.g. Improving / On-track / No-change


class PipTransition(BaseModel):
    model_config = ConfigDict(extra="ignore")
    to: str
    outcome: Optional[str] = None


class PipAck(BaseModel):
    """Employee self-service acknowledgement of an active plan."""
    model_config = ConfigDict(extra="ignore")
    note: Optional[str] = None
