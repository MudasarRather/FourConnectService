"""HR Asset schemas."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.hr.asset import (
    AssetType, AssetCondition, AssetStatus, AllocationStatus,
)


class AssetCreate(BaseModel):
    asset_code: str
    asset_type: AssetType
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    purchase_date: Optional[date] = None
    purchase_cost: Optional[Decimal] = None
    condition: AssetCondition = AssetCondition.NEW
    status: AssetStatus = AssetStatus.AVAILABLE
    location_id: Optional[UUID] = None
    notes: Optional[str] = None


class AssetUpdate(BaseModel):
    asset_code: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    purchase_date: Optional[date] = None
    purchase_cost: Optional[Decimal] = None
    condition: Optional[AssetCondition] = None
    status: Optional[AssetStatus] = None
    location_id: Optional[UUID] = None
    notes: Optional[str] = None


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    asset_code: str
    asset_type: AssetType
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    purchase_date: Optional[date] = None
    purchase_cost: Optional[Decimal] = None
    condition: AssetCondition
    status: AssetStatus
    assigned_employee_id: Optional[UUID] = None
    assigned_employee_name: Optional[str] = None
    location_id: Optional[UUID] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AssetListResponse(BaseModel):
    items: List[AssetResponse]
    total: int
    page: int
    limit: int
    total_pages: int


class AssetAllocationCreate(BaseModel):
    asset_id: UUID
    employee_id: UUID
    process_id: Optional[UUID] = None
    expected_return_date: Optional[date] = None
    condition_on_issue: Optional[AssetCondition] = None
    notes: Optional[str] = None


class AssetAllocationReturnBody(BaseModel):
    returned_date: date
    condition_on_return: Optional[AssetCondition] = None
    status: AllocationStatus = AllocationStatus.RETURNED
    notes: Optional[str] = None


class AssetAllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    asset_id: UUID
    asset_code: Optional[str] = None
    asset_type: Optional[AssetType] = None
    employee_id: UUID
    employee_name: Optional[str] = None
    process_id: Optional[UUID] = None
    allocated_date: date
    expected_return_date: Optional[date] = None
    returned_date: Optional[date] = None
    condition_on_issue: Optional[AssetCondition] = None
    condition_on_return: Optional[AssetCondition] = None
    status: AllocationStatus
    acknowledged_by_employee: bool
    acknowledged_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime
