"""Schemas for HR Settings — Numbering Series."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NumberingSeriesBase(BaseModel):
    prefix: str = Field("", max_length=20)
    suffix: Optional[str] = Field(None, max_length=20)
    separator: str = Field("", max_length=4)
    padding: int = Field(4, ge=0, le=10)
    include_year: bool = False
    include_month: bool = False
    financial_year_reset: bool = False
    is_active: bool = True


class NumberingSeriesCreate(NumberingSeriesBase):
    model_config = ConfigDict(extra="ignore")
    module: str = Field(..., min_length=1, max_length=40)
    current_number: int = Field(0, ge=0)


class NumberingSeriesUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prefix: Optional[str] = None
    suffix: Optional[str] = None
    separator: Optional[str] = None
    padding: Optional[int] = Field(None, ge=0, le=10)
    include_year: Optional[bool] = None
    include_month: Optional[bool] = None
    financial_year_reset: Optional[bool] = None
    is_active: Optional[bool] = None
    current_number: Optional[int] = Field(None, ge=0)


class NumberingSeriesResponse(NumberingSeriesBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    module: str
    current_number: int
    last_reset_fy: Optional[str] = None
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
