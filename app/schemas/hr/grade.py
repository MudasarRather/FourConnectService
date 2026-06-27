from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class GradeBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    code: str = Field(..., min_length=1, max_length=20)
    band: Optional[str] = None
    level: Optional[int] = None
    default_pay_level: Optional[str] = Field(None, max_length=20)
    min_ctc: Optional[Decimal] = None
    max_ctc: Optional[Decimal] = None
    eligibility: Optional[dict] = None


class GradeCreate(GradeBase):
    model_config = ConfigDict(extra="ignore")


class GradeUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    code: Optional[str] = None
    band: Optional[str] = None
    level: Optional[int] = None
    default_pay_level: Optional[str] = Field(None, max_length=20)
    min_ctc: Optional[Decimal] = None
    max_ctc: Optional[Decimal] = None
    eligibility: Optional[dict] = None


class GradeResponse(GradeBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
