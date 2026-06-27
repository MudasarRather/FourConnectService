"""Schemas for the HR Performance Management module (review instances).

Request/input schemas only — list/detail responses are serialized to plain
dicts in the router (joined employee/reviewer names aren't ORM columns), the
same pattern the training dashboard uses.
"""
from datetime import date
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PerfReviewCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    employee_id: UUID
    template_id: UUID
    cycle: Optional[str] = Field(None, max_length=20)
    period_label: Optional[str] = Field(None, max_length=60)
    due_date: Optional[date] = None
    reviewer_id: Optional[UUID] = None   # override; else snapshot reporting_manager
    merit_policy_id: Optional[UUID] = None       # else org default at launch
    hike_effective_from: Optional[date] = None   # date the approved hike takes effect


class PerfReviewBulkCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    template_id: UUID
    employee_ids: List[UUID] = Field(default_factory=list)
    cycle: Optional[str] = Field(None, max_length=20)
    period_label: Optional[str] = Field(None, max_length=60)
    due_date: Optional[date] = None
    merit_policy_id: Optional[UUID] = None
    hike_effective_from: Optional[date] = None


class PerfSectionScoreIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str
    rating: Optional[float] = Field(None, ge=0, le=100)
    comment: Optional[str] = None


class PerfSelfSubmit(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sections: List[PerfSectionScoreIn] = Field(default_factory=list)
    comments: Optional[str] = None
    submit: bool = True                  # False → save draft without advancing


class PerfManagerSubmit(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sections: List[PerfSectionScoreIn] = Field(default_factory=list)
    comments: Optional[str] = None
    submit: bool = True                  # False → save draft, stay in MANAGER_ASSESSMENT


class PerfTeamLaunch(BaseModel):
    """A manager opens a review for one of their own direct reports."""
    model_config = ConfigDict(extra="ignore")
    employee_id: UUID
    template_id: UUID
    cycle: Optional[str] = Field(None, max_length=20)
    period_label: Optional[str] = Field(None, max_length=60)
    due_date: Optional[date] = None


class PerfReflectionSubmit(BaseModel):
    """Employee self-service — an OPTIONAL, NON-SCORING reflection note. The
    employee never submits ratings; the manager owns the official score."""
    model_config = ConfigDict(extra="ignore")
    comments: Optional[str] = None


class PerfRecommendIn(BaseModel):
    """Manager (or HR) recommends a hike % — clamped to the resolved band range."""
    model_config = ConfigDict(extra="ignore")
    hike_pct: float = Field(..., ge=0, le=100)
    note: Optional[str] = None


class PerfApproveHikeIn(BaseModel):
    """HR approves & applies the hike to payroll as an effective-dated revision."""
    model_config = ConfigDict(extra="ignore")
    approved_hike_pct: Optional[float] = Field(None, ge=0, le=100)  # else use recommended
    effective_from: Optional[date] = None                          # else review.hike_effective_from
    note: Optional[str] = None


class PerfAck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    comments: Optional[str] = None


class PerfTransition(BaseModel):
    model_config = ConfigDict(extra="ignore")
    to: str
    note: Optional[str] = None


class PerfReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    period_label: Optional[str] = None
    cycle: Optional[str] = None
    due_date: Optional[date] = None
    reviewer_id: Optional[UUID] = None
