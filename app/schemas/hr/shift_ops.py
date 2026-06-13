"""Pydantic schemas for Shifts & Rosters Phase 2 ops:
overtime rules, shift swaps, holiday shifts, night-shift policies.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ───────────── Overtime Rules ─────────────

class OvertimeRuleCreate(BaseModel):
    name: str
    ot_type: str = "WEEKDAY"
    threshold_hours: float = 8.0
    multiplier: float = 1.5
    max_ot_hours: Optional[float] = None
    approval_required: bool = True
    department_ids: List[str] = Field(default_factory=list)
    priority: int = 0
    description: Optional[str] = None


class OvertimeRuleUpdate(BaseModel):
    name: Optional[str] = None
    ot_type: Optional[str] = None
    threshold_hours: Optional[float] = None
    multiplier: Optional[float] = None
    max_ot_hours: Optional[float] = None
    approval_required: Optional[bool] = None
    department_ids: Optional[List[str]] = None
    priority: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class OvertimeRuleResponse(BaseModel):
    id: UUID
    name: str
    ot_type: str
    threshold_hours: float
    multiplier: float
    max_ot_hours: Optional[float] = None
    approval_required: bool = True
    department_ids: List[str] = Field(default_factory=list)
    priority: int = 0
    description: Optional[str] = None
    is_active: bool = True
    created_at: datetime


class OtResolveResult(BaseModel):
    matched: bool = False
    rule_id: Optional[UUID] = None
    rule_name: Optional[str] = None
    ot_type: str
    multiplier: float = 1.0
    requested_hours: float = 0.0
    payable_hours: float = 0.0
    capped: bool = False
    approval_required: bool = True


# ───────────── Shift Swaps ─────────────

class ShiftSwapCreate(BaseModel):
    requester_employee_id: UUID
    counterparty_employee_id: UUID
    swap_date: date
    requester_shift_id: Optional[UUID] = None
    counterparty_shift_id: Optional[UUID] = None
    reason: Optional[str] = None


class SwapDecisionBody(BaseModel):
    notes: Optional[str] = None


class ShiftSwapResponse(BaseModel):
    id: UUID
    requester_employee_id: UUID
    requester_name: Optional[str] = None
    counterparty_employee_id: UUID
    counterparty_name: Optional[str] = None
    swap_date: date
    requester_shift_id: Optional[UUID] = None
    requester_shift_code: Optional[str] = None
    counterparty_shift_id: Optional[UUID] = None
    counterparty_shift_code: Optional[str] = None
    reason: Optional[str] = None
    status: str
    peer_accepted: bool = False
    decision_notes: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime


# ───────────── Holiday Shifts ─────────────

class HolidayShiftCreate(BaseModel):
    holiday_id: UUID
    employee_id: UUID
    shift_id: Optional[UUID] = None
    compensation: str = "DOUBLE_PAY"
    pay_multiplier: float = 2.0
    notes: Optional[str] = None


class HolidayShiftBulkBody(BaseModel):
    holiday_id: UUID
    employee_ids: List[UUID] = Field(default_factory=list)
    shift_id: Optional[UUID] = None
    compensation: str = "DOUBLE_PAY"
    pay_multiplier: float = 2.0


class HolidayShiftUpdate(BaseModel):
    shift_id: Optional[UUID] = None
    compensation: Optional[str] = None
    pay_multiplier: Optional[float] = None
    notes: Optional[str] = None


class HolidayShiftResponse(BaseModel):
    id: UUID
    holiday_id: UUID
    holiday_name: Optional[str] = None
    holiday_date: Optional[date] = None
    employee_id: UUID
    employee_name: Optional[str] = None
    shift_id: Optional[UUID] = None
    shift_code: Optional[str] = None
    shift_name: Optional[str] = None
    compensation: str
    pay_multiplier: float = 2.0
    notes: Optional[str] = None
    created_at: datetime


# ───────────── Night Shift Policies ─────────────

class NightPolicyUpsert(BaseModel):
    shift_id: UUID
    allowance_amount: float = 0.0
    overtime_rate: float = 1.5
    transport_required: bool = False
    meal_eligible: bool = False
    safety_compliance: bool = True
    notes: Optional[str] = None


class NightPolicyResponse(BaseModel):
    id: UUID
    shift_id: UUID
    shift_code: Optional[str] = None
    shift_name: Optional[str] = None
    allowance_amount: float = 0.0
    overtime_rate: float = 1.5
    transport_required: bool = False
    meal_eligible: bool = False
    safety_compliance: bool = True
    notes: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class NightRosterEmployee(BaseModel):
    employee_id: UUID
    employee_name: Optional[str] = None
    shift_id: UUID
    shift_code: Optional[str] = None
    shift_name: Optional[str] = None
    allowance_amount: float = 0.0
    transport_required: bool = False
    meal_eligible: bool = False


class NightRosterResponse(BaseModel):
    on_date: date
    count: int = 0
    staff: List[NightRosterEmployee] = Field(default_factory=list)
