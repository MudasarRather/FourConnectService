"""Pydantic schemas for Workforce Planning — demand entries + forecast."""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkforceDemandCreate(BaseModel):
    shift_id: UUID
    department_id: Optional[UUID] = None
    required_headcount: int = 1
    skill: Optional[str] = None
    valid_from: date
    valid_to: Optional[date] = None
    notes: Optional[str] = None


class WorkforceDemandUpdate(BaseModel):
    shift_id: Optional[UUID] = None
    department_id: Optional[UUID] = None
    required_headcount: Optional[int] = None
    skill: Optional[str] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class WorkforceDemandResponse(BaseModel):
    id: UUID
    shift_id: UUID
    shift_code: Optional[str] = None
    shift_name: Optional[str] = None
    department_id: Optional[UUID] = None
    department_name: Optional[str] = None
    required_headcount: int = 1
    skill: Optional[str] = None
    valid_from: date
    valid_to: Optional[date] = None
    notes: Optional[str] = None
    is_active: bool = True
    created_at: datetime


# ── Forecast ──

class ForecastCell(BaseModel):
    demand_id: UUID
    label: Optional[str] = None
    shift_id: UUID
    shift_code: Optional[str] = None
    shift_name: Optional[str] = None
    required: int = 0
    assigned: int = 0
    shortfall: int = 0
    ratio: float = 1.0   # assigned / required (1.0 = fully covered)


class ForecastDay(BaseModel):
    date: date
    weekday: int
    required: int = 0
    assigned: int = 0
    shortfall: int = 0
    # An active, non-RESTRICTED holiday rests the workforce this day — assigned
    # capacity counts only employees with a HolidayShiftAssignment override.
    # Surfaced so the UI can attribute a holiday-driven gap to the holiday
    # rather than reading it as a staffing failure.
    is_holiday: bool = False
    holiday_name: Optional[str] = None
    cells: List[ForecastCell] = Field(default_factory=list)


class WorkforceForecastSummary(BaseModel):
    horizon_days: int = 0
    demand_entries: int = 0
    total_required: int = 0
    total_assigned: int = 0
    total_shortfall: int = 0
    shortfall_days: int = 0
    coverage_pct: float = 100.0
    worst_shift: Optional[str] = None
    worst_shift_shortfall: int = 0


class WorkforceForecastResponse(BaseModel):
    from_date: date
    to_date: date
    summary: WorkforceForecastSummary
    days: List[ForecastDay] = Field(default_factory=list)
