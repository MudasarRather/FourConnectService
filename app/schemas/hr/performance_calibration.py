"""Schemas for HR Performance — calibration / 9-box (inputs only)."""
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CalibrationUpsert(BaseModel):
    """Place / update one employee on the 9-box grid for a cycle.

    Either pass review_id (snapshot its score) or employee_id + cycle directly.
    potential_band is the manager/HR judgement; performance_band is normally
    derived from the score but can be overridden by the committee.
    """
    model_config = ConfigDict(extra="ignore")
    employee_id: UUID
    review_id: Optional[UUID] = None
    cycle: Optional[str] = Field(None, max_length=20)
    period_label: Optional[str] = Field(None, max_length=60)
    potential_band: int = Field(2, ge=1, le=3)
    performance_band: Optional[int] = Field(None, ge=1, le=3)
    calibrated_score: Optional[float] = None
    note: Optional[str] = None
    status: Optional[str] = Field(None, max_length=16)


class CalibrationMove(BaseModel):
    """Drag a chip to a new 9-box cell."""
    model_config = ConfigDict(extra="ignore")
    performance_band: int = Field(..., ge=1, le=3)
    potential_band: int = Field(..., ge=1, le=3)
    note: Optional[str] = None


class SeedCalibrationBody(BaseModel):
    """Bootstrap calibration rows from all completed reviews in a cycle/period."""
    model_config = ConfigDict(extra="ignore")
    cycle: Optional[str] = None
    period_label: Optional[str] = None
