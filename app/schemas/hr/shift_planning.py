"""Pydantic schemas for the Shifts & Rosters ("Control Tower") module:
dashboard aggregation, calendar, rotations, weekly rosters, coverage rules.
All request/response shapes the frontend consumes live here.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────── Dashboard ───────────────────────────

class ShiftKpis(BaseModel):
    active_shifts: int = 0
    employees_assigned: int = 0
    night_shift_employees: int = 0
    upcoming_rotations: int = 0
    holiday_shift_staff: int = 0
    overtime_hours: float = 0.0
    shift_conflicts: int = 0
    unassigned_employees: int = 0


class ShiftDistributionItem(BaseModel):
    shift_id: UUID
    code: str
    name: str
    shift_type: str
    count: int = 0


class DeptAllocationItem(BaseModel):
    department_id: Optional[UUID] = None
    department_name: str
    count: int = 0
    night_count: int = 0


class TrendPoint(BaseModel):
    label: str
    value: float = 0.0


class CoverageSnapshot(BaseModel):
    label: str
    required: int = 0
    assigned: int = 0


class ShiftDashboardResponse(BaseModel):
    kpis: ShiftKpis
    shift_distribution: List[ShiftDistributionItem] = Field(default_factory=list)
    dept_allocation: List[DeptAllocationItem] = Field(default_factory=list)
    overtime_trend: List[TrendPoint] = Field(default_factory=list)
    night_utilization: List[TrendPoint] = Field(default_factory=list)
    weekly_coverage: List[CoverageSnapshot] = Field(default_factory=list)
    generated_at: datetime


# ─────────────────────────── Calendar ───────────────────────────

class CalendarAssignment(BaseModel):
    employee_id: UUID
    employee_name: Optional[str] = None
    shift_id: UUID
    shift_code: Optional[str] = None
    shift_name: Optional[str] = None
    shift_type: Optional[str] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None


class CalendarDay(BaseModel):
    date: date
    weekday: int
    is_holiday: bool = False
    holiday_name: Optional[str] = None
    assignments: List[CalendarAssignment] = Field(default_factory=list)
    count: int = 0


class ShiftCalendarResponse(BaseModel):
    from_date: date
    to_date: date
    days: List[CalendarDay] = Field(default_factory=list)


# ─────────────────────────── Rotation ───────────────────────────

class RotationStepInput(BaseModel):
    sequence: int = 0
    shift_id: Optional[UUID] = None  # None = OFF block
    label: Optional[str] = None


class RotationStepResponse(BaseModel):
    id: UUID
    sequence: int
    shift_id: Optional[UUID] = None
    shift_code: Optional[str] = None
    shift_name: Optional[str] = None
    label: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class RotationMemberResponse(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    phase_offset: int = 0
    model_config = ConfigDict(from_attributes=True)


class ShiftRotationCreate(BaseModel):
    name: str
    code: Optional[str] = None
    cycle: str = "WEEKLY"
    frequency_days: Optional[int] = None
    description: Optional[str] = None
    department_ids: List[str] = Field(default_factory=list)
    anchor_date: Optional[date] = None
    steps: List[RotationStepInput] = Field(default_factory=list)
    member_employee_ids: List[UUID] = Field(default_factory=list)


class ShiftRotationUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    cycle: Optional[str] = None
    frequency_days: Optional[int] = None
    description: Optional[str] = None
    department_ids: Optional[List[str]] = None
    anchor_date: Optional[date] = None
    is_active: Optional[bool] = None
    steps: Optional[List[RotationStepInput]] = None
    member_employee_ids: Optional[List[UUID]] = None


class ShiftRotationResponse(BaseModel):
    id: UUID
    name: str
    code: Optional[str] = None
    cycle: str
    frequency_days: int
    description: Optional[str] = None
    department_ids: List[str] = Field(default_factory=list)
    anchor_date: Optional[date] = None
    current_step_index: int = 0
    last_advanced_on: Optional[date] = None
    is_active: bool = True
    created_at: datetime
    steps: List[RotationStepResponse] = Field(default_factory=list)
    members: List[RotationMemberResponse] = Field(default_factory=list)
    member_count: int = 0
    current_step_label: Optional[str] = None


class RotationAdvanceResult(BaseModel):
    rotation_id: UUID
    advanced_to_step: int
    assignments_written: int
    window_from: date
    window_to: date


# ─────────────────────────── Roster ───────────────────────────

class RosterEntryInput(BaseModel):
    employee_id: UUID
    day: date
    shift_id: Optional[UUID] = None  # None = OFF
    duty_hours: Optional[float] = None


class RosterEntryResponse(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    day: date
    shift_id: Optional[UUID] = None
    shift_code: Optional[str] = None
    shift_name: Optional[str] = None
    duty_hours: Optional[float] = None
    model_config = ConfigDict(from_attributes=True)


class ShiftRosterCreate(BaseModel):
    name: Optional[str] = None
    week_start: date
    week_end: Optional[date] = None
    department_id: Optional[UUID] = None
    notes: Optional[str] = None


class ShiftRosterUpdate(BaseModel):
    name: Optional[str] = None
    week_start: Optional[date] = None
    week_end: Optional[date] = None
    department_id: Optional[UUID] = None
    notes: Optional[str] = None


class ShiftRosterResponse(BaseModel):
    id: UUID
    name: Optional[str] = None
    week_start: date
    week_end: date
    department_id: Optional[UUID] = None
    department_name: Optional[str] = None
    status: str = "DRAFT"
    notes: Optional[str] = None
    published_at: Optional[datetime] = None
    entry_count: int = 0
    created_at: datetime
    entries: List[RosterEntryResponse] = Field(default_factory=list)


class RosterBulkEntriesBody(BaseModel):
    entries: List[RosterEntryInput] = Field(default_factory=list)


class RosterPublishResult(BaseModel):
    roster_id: UUID
    assignments_written: int
    skipped: int


# ─────────────────────────── Coverage ───────────────────────────

class CoverageRuleCreate(BaseModel):
    shift_id: UUID
    department_id: Optional[UUID] = None
    min_staff: int = 1
    label: Optional[str] = None
    critical: bool = False


class CoverageRuleUpdate(BaseModel):
    shift_id: Optional[UUID] = None
    department_id: Optional[UUID] = None
    min_staff: Optional[int] = None
    label: Optional[str] = None
    critical: Optional[bool] = None
    is_active: Optional[bool] = None


class CoverageRuleResponse(BaseModel):
    id: UUID
    shift_id: UUID
    shift_code: Optional[str] = None
    shift_name: Optional[str] = None
    department_id: Optional[UUID] = None
    department_name: Optional[str] = None
    min_staff: int = 1
    label: Optional[str] = None
    critical: bool = False
    is_active: bool = True
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class CoverageAlert(BaseModel):
    rule_id: UUID
    shift_id: UUID
    shift_code: Optional[str] = None
    shift_name: Optional[str] = None
    department_id: Optional[UUID] = None
    department_name: Optional[str] = None
    min_staff: int = 0
    assigned: int = 0
    shortfall: int = 0
    critical: bool = False
    status: str = "OK"  # OK | WARN | CRITICAL


class CoverageAlertsResponse(BaseModel):
    on_date: date
    alerts: List[CoverageAlert] = Field(default_factory=list)
    total_shortfall: int = 0
    critical_count: int = 0
