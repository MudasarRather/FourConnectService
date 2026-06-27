"""Pydantic schemas for the HR Settings configurable master tables."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Employment Type / Employee Category (shared shape) ───────────────────────
class SimpleMasterCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str = Field(..., min_length=1, max_length=40)
    label: str = Field(..., min_length=1, max_length=80)
    description: Optional[str] = None
    sort_order: Optional[int] = 0
    is_active: Optional[bool] = True


class SimpleMasterUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class SimpleMasterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    label: str
    description: Optional[str] = None
    is_system: bool
    sort_order: int
    is_active: bool
    is_deleted: bool
    created_at: datetime
    updated_at: datetime


# ── Separation Reason (adds category + is_voluntary) ─────────────────────────
class SeparationReasonCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str = Field(..., min_length=1, max_length=50)
    label: str = Field(..., min_length=1, max_length=120)
    category: str = Field("EXIT_REASON", max_length=30)
    is_voluntary: Optional[bool] = None
    description: Optional[str] = None
    sort_order: Optional[int] = 0
    is_active: Optional[bool] = True


class SeparationReasonUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: Optional[str] = None
    label: Optional[str] = None
    category: Optional[str] = None
    is_voluntary: Optional[bool] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class SeparationReasonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    label: str
    category: str
    is_voluntary: Optional[bool] = None
    description: Optional[str] = None
    is_system: bool
    sort_order: int
    is_active: bool
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
