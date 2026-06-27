"""Support Desk — ITIL schemas (Change Requests, Problems, Customer Assets)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ─────────── Change Request ───────────
class ChangeRequestCreate(BaseModel):
    title: str
    description: Optional[str] = None
    reason: Optional[str] = None
    impact: Optional[str] = None
    risk_level: str = "low"
    implementation_date: Optional[datetime] = None
    rollback_plan: Optional[str] = None
    testing_plan: Optional[str] = None
    organization_id: Optional[UUID] = None


class ChangeRequestUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    reason: Optional[str] = None
    impact: Optional[str] = None
    risk_level: Optional[str] = None
    implementation_date: Optional[datetime] = None
    rollback_plan: Optional[str] = None
    testing_plan: Optional[str] = None
    organization_id: Optional[UUID] = None
    status: Optional[str] = None


class ChangeRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    change_number: Optional[str] = None
    title: str
    description: Optional[str] = None
    reason: Optional[str] = None
    impact: Optional[str] = None
    risk_level: str
    implementation_date: Optional[datetime] = None
    rollback_plan: Optional[str] = None
    testing_plan: Optional[str] = None
    approver_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    organization_id: Optional[UUID] = None
    status: str
    created_by_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    organization_name: Optional[str] = None


# ─────────── Problem ───────────
class ProblemCreate(BaseModel):
    title: str
    description: Optional[str] = None
    severity: str = "medium"
    impact: Optional[str] = None
    organization_id: Optional[UUID] = None
    linked_ticket_ids: List[UUID] = Field(default_factory=list)
    linked_change_ids: List[UUID] = Field(default_factory=list)
    linked_asset_ids: List[UUID] = Field(default_factory=list)
    root_cause: Optional[str] = None
    resolution_plan: Optional[str] = None
    preventive_measures: Optional[str] = None
    lessons_learned: Optional[str] = None


class ProblemUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[str] = None
    impact: Optional[str] = None
    status: Optional[str] = None
    organization_id: Optional[UUID] = None
    linked_ticket_ids: Optional[List[UUID]] = None
    linked_change_ids: Optional[List[UUID]] = None
    linked_asset_ids: Optional[List[UUID]] = None
    root_cause: Optional[str] = None
    resolution_plan: Optional[str] = None
    preventive_measures: Optional[str] = None
    lessons_learned: Optional[str] = None


class ProblemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    problem_number: Optional[str] = None
    title: str
    description: Optional[str] = None
    severity: str
    impact: Optional[str] = None
    status: str
    organization_id: Optional[UUID] = None
    linked_ticket_ids: List[UUID] = Field(default_factory=list)
    linked_change_ids: List[UUID] = Field(default_factory=list)
    linked_asset_ids: List[UUID] = Field(default_factory=list)
    root_cause: Optional[str] = None
    resolution_plan: Optional[str] = None
    preventive_measures: Optional[str] = None
    lessons_learned: Optional[str] = None
    created_by_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


# ─────────── Customer Asset ───────────
class CustomerAssetCreate(BaseModel):
    organization_id: Optional[UUID] = None
    name: str
    asset_type: Optional[str] = None
    serial_number: Optional[str] = None
    model: Optional[str] = None
    vendor: Optional[str] = None
    warranty_start: Optional[datetime] = None
    warranty_end: Optional[datetime] = None
    amc: Optional[str] = None
    vendor_contact: Optional[str] = None
    notes: Optional[str] = None


class CustomerAssetUpdate(BaseModel):
    organization_id: Optional[UUID] = None
    name: Optional[str] = None
    asset_type: Optional[str] = None
    serial_number: Optional[str] = None
    model: Optional[str] = None
    vendor: Optional[str] = None
    warranty_start: Optional[datetime] = None
    warranty_end: Optional[datetime] = None
    amc: Optional[str] = None
    vendor_contact: Optional[str] = None
    notes: Optional[str] = None


class CustomerAssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: Optional[UUID] = None
    name: str
    asset_type: Optional[str] = None
    serial_number: Optional[str] = None
    model: Optional[str] = None
    vendor: Optional[str] = None
    warranty_start: Optional[datetime] = None
    warranty_end: Optional[datetime] = None
    amc: Optional[str] = None
    vendor_contact: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    organization_name: Optional[str] = None
