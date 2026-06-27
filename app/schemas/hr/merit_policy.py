"""Schemas for HR Settings — Merit & Increment Policy."""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MeritBandIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: Optional[str] = Field(None, max_length=40)
    label: str = Field(..., min_length=1, max_length=60)
    frac_min: Decimal = Field(..., ge=0, le=1)
    frac_max: Decimal = Field(..., ge=0, le=1.01)
    hike_min_pct: Decimal = Field(0, ge=0, le=100)
    hike_max_pct: Decimal = Field(0, ge=0, le=100)
    auto_pip: bool = False

    @model_validator(mode="after")
    def _check(self):
        if self.frac_max < self.frac_min:
            raise ValueError("Band frac_max must be ≥ frac_min")
        if self.hike_max_pct < self.hike_min_pct:
            raise ValueError("Band hike_max_pct must be ≥ hike_min_pct")
        return self


class MeritBandOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: Optional[str] = None
    label: str
    frac_min: Decimal
    frac_max: Decimal
    hike_min_pct: Decimal
    hike_max_pct: Decimal
    auto_pip: bool = False


class MeritPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    merit_budget_pct: Optional[Decimal] = Field(None, ge=0, le=100)
    bands: List[MeritBandIn] = Field(default_factory=list)
    is_active: Optional[bool] = True
    is_default: Optional[bool] = False


class MeritPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    description: Optional[str] = None
    merit_budget_pct: Optional[Decimal] = None
    bands: Optional[List[MeritBandIn]] = None   # if present → replace all
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class MeritPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: Optional[str] = None
    merit_budget_pct: Optional[Decimal] = None
    bands: Optional[List[MeritBandOut]] = None
    is_active: bool
    is_default: bool
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
