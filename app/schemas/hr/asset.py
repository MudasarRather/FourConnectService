"""HR Asset schemas."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.hr.asset import (
    AssetType, AssetCondition, AssetStatus, AllocationStatus,
)


class _AssetExtras(BaseModel):
    """Lifecycle/procurement fields shared by Create + Update (all optional)."""
    category_id: Optional[UUID] = None
    vendor_id: Optional[UUID] = None
    department_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    purchase_order_no: Optional[str] = None
    invoice_no: Optional[str] = None
    warranty_start: Optional[date] = None
    warranty_end: Optional[date] = None
    depreciation_method: Optional[str] = None
    salvage_value: Optional[Decimal] = None
    current_book_value: Optional[Decimal] = None
    building: Optional[str] = None
    floor: Optional[str] = None
    room: Optional[str] = None
    tag: Optional[str] = None
    photo_path: Optional[str] = None
    invoice_path: Optional[str] = None
    warranty_doc_path: Optional[str] = None


class AssetCreate(_AssetExtras):
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


class AssetUpdate(_AssetExtras):
    asset_code: Optional[str] = None
    asset_type: Optional[AssetType] = None
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
    # Lifecycle / procurement extensions
    category_id: Optional[UUID] = None
    category_name: Optional[str] = None
    vendor_id: Optional[UUID] = None
    vendor_name: Optional[str] = None
    department_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    purchase_order_no: Optional[str] = None
    invoice_no: Optional[str] = None
    warranty_start: Optional[date] = None
    warranty_end: Optional[date] = None
    depreciation_method: Optional[str] = None
    salvage_value: Optional[Decimal] = None
    current_book_value: Optional[Decimal] = None
    building: Optional[str] = None
    floor: Optional[str] = None
    room: Optional[str] = None
    tag: Optional[str] = None
    photo_path: Optional[str] = None
    invoice_path: Optional[str] = None
    warranty_doc_path: Optional[str] = None
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


class ReturnRequestBody(BaseModel):
    """Employee self-service return request — a note for HR, no authoritative state."""
    note: Optional[str] = Field(default=None, max_length=500)


class AssetAllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    asset_id: UUID
    asset_code: Optional[str] = None
    asset_type: Optional[AssetType] = None
    # Asset descriptors (additive — power the holding-card detail view; non-financial).
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    warranty_end: Optional[date] = None
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
    # Self-service return request (employee-flagged; HR completes from Returns tab).
    return_requested: bool = False
    return_requested_at: Optional[datetime] = None
    return_request_note: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
