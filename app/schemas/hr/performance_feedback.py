"""Schemas for HR Performance — 360° feedback (inputs only; responses are dicts)."""
from datetime import date
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CompetencyIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str
    label: str


class NomineeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reviewer_user_id: Optional[UUID] = None
    reviewer_employee_id: Optional[UUID] = None
    reviewer_name: Optional[str] = Field(None, max_length=160)
    relationship_type: str = Field("PEER", max_length=20)


class FeedbackRequestCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    employee_id: UUID
    review_id: Optional[UUID] = None
    cycle: Optional[str] = Field(None, max_length=20)
    period_label: Optional[str] = Field(None, max_length=60)
    title: Optional[str] = Field(None, max_length=160)
    prompt: Optional[str] = None
    competencies: List[CompetencyIn] = Field(default_factory=list)
    rating_max: Optional[float] = Field(5, ge=1, le=10)
    anonymous: bool = True
    due_date: Optional[date] = None
    # Rater composition — deterministic, no "if empty" surprise. Manager (downward
    # feedback) is the backbone of 360°; self-assessment powers the self-vs-others
    # perception gap. Both default on but the admin controls them explicitly.
    include_manager: bool = True
    include_self: bool = True
    nominees: List[NomineeIn] = Field(default_factory=list)   # peers / reports / skip / external only


class FeedbackRequestUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: Optional[str] = None
    prompt: Optional[str] = None
    due_date: Optional[date] = None
    anonymous: Optional[bool] = None


class NominateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    nominees: List[NomineeIn] = Field(default_factory=list)


class RatingIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str
    label: Optional[str] = None
    rating: Optional[float] = Field(None, ge=0, le=10)


class FeedbackResponseSubmit(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ratings: List[RatingIn] = Field(default_factory=list)
    strengths: Optional[str] = None
    improvements: Optional[str] = None
    comments: Optional[str] = None
    submit: bool = True             # False → save draft, stay PENDING
    decline: bool = False           # True → mark DECLINED
