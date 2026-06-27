"""Schemas for HR Settings — Appraisal Templates."""
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AppraisalSectionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(..., min_length=1, max_length=120)
    weight: Decimal = Field(0, ge=0, le=100)
    section_type: str = Field("COMPETENCY", max_length=20)
    criteria_json: Optional[Any] = None
    sort_order: Optional[int] = 0


class AppraisalSectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    weight: Decimal
    section_type: str
    criteria_json: Optional[Any] = None
    sort_order: int


class AppraisalTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(..., min_length=1, max_length=120)
    code: str = Field(..., min_length=1, max_length=30)
    description: Optional[str] = None
    cycle: str = Field("ANNUAL", max_length=20)
    rating_scale: Optional[Any] = None
    applies_to_json: Optional[Any] = None
    is_active: Optional[bool] = True
    sections: List[AppraisalSectionIn] = Field(default_factory=list)


class AppraisalTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    cycle: Optional[str] = None
    rating_scale: Optional[Any] = None
    applies_to_json: Optional[Any] = None
    is_active: Optional[bool] = None
    sections: Optional[List[AppraisalSectionIn]] = None   # if present → replace all


class AppraisalTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: str
    description: Optional[str] = None
    cycle: str
    rating_scale: Optional[Any] = None
    applies_to_json: Optional[Any] = None
    is_active: bool
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    sections: List[AppraisalSectionOut] = Field(default_factory=list)
