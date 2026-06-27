"""HR Asset Management — lifecycle schemas (categories, vendors, transfers,
maintenance, damage, history, dashboard stats, audits, disposal)."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Dict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.hr.asset import AssetType, AssetCondition, AssetStatus
from app.models.hr.asset_lifecycle import (
    AssetTransferType, AssetTransferStatus,
    AssetMaintenanceType, AssetMaintenanceStatus,
    AssetDamageSeverity, AssetDamageStatus,
    AssetEventType, AssetAuditStatus, AssetAuditResult,
    AssetDisposalMethod, AssetDisposalStatus,
)


# ───────────────────────────── Categories ─────────────────────────────

class AssetCategoryCreate(BaseModel):
    name: str
    code: str
    parent_category_id: Optional[UUID] = None
    default_asset_type: Optional[str] = None
    allowed_asset_types: Optional[List[str]] = None
    depreciation_method: Optional[str] = None
    useful_life_months: Optional[int] = None
    description: Optional[str] = None
    is_active: bool = True


class AssetCategoryUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    parent_category_id: Optional[UUID] = None
    default_asset_type: Optional[str] = None
    allowed_asset_types: Optional[List[str]] = None
    depreciation_method: Optional[str] = None
    useful_life_months: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class AssetCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: str
    parent_category_id: Optional[UUID] = None
    default_asset_type: Optional[str] = None
    allowed_asset_types: Optional[List[str]] = None
    depreciation_method: Optional[str] = None
    useful_life_months: Optional[int] = None
    description: Optional[str] = None
    is_active: bool
    asset_count: Optional[int] = None
    created_at: datetime


class AssetCategoryListResponse(BaseModel):
    items: List[AssetCategoryResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ─────────────────────── Asset Types (manageable catalog) ───────────────────────

class AssetTypeDefCreate(BaseModel):
    code: str
    label: str
    icon: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True


class AssetTypeDefUpdate(BaseModel):
    code: Optional[str] = None
    label: Optional[str] = None
    icon: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class AssetTypeDefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    label: str
    icon: Optional[str] = None
    sort_order: int
    is_system: bool
    is_active: bool
    asset_count: Optional[int] = None
    created_at: datetime


class AssetTypeDefListResponse(BaseModel):
    items: List[AssetTypeDefResponse]
    total: int


# ───────────────────────────── Vendors ─────────────────────────────

class VendorCreate(BaseModel):
    name: str
    code: Optional[str] = None
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None
    website: Optional[str] = None
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    notes: Optional[str] = None
    is_active: bool = True


class VendorUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None
    website: Optional[str] = None
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class VendorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: Optional[str] = None
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None
    website: Optional[str] = None
    rating: Optional[int] = None
    notes: Optional[str] = None
    is_active: bool
    asset_count: Optional[int] = None
    created_at: datetime


class VendorListResponse(BaseModel):
    items: List[VendorResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ───────────────────────────── Transfers ─────────────────────────────

class AssetTransferCreate(BaseModel):
    asset_id: UUID
    transfer_type: AssetTransferType
    to_employee_id: Optional[UUID] = None
    to_location_id: Optional[UUID] = None
    to_department_id: Optional[UUID] = None
    reason: Optional[str] = None
    effective_date: Optional[date] = None
    notes: Optional[str] = None


class AssetTransferDecisionBody(BaseModel):
    notes: Optional[str] = None


class AssetTransferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    asset_id: UUID
    asset_code: Optional[str] = None
    transfer_type: AssetTransferType
    status: AssetTransferStatus
    from_employee_id: Optional[UUID] = None
    from_employee_name: Optional[str] = None
    to_employee_id: Optional[UUID] = None
    to_employee_name: Optional[str] = None
    from_location_id: Optional[UUID] = None
    to_location_id: Optional[UUID] = None
    from_department_id: Optional[UUID] = None
    to_department_id: Optional[UUID] = None
    reason: Optional[str] = None
    effective_date: Optional[date] = None
    notes: Optional[str] = None
    created_at: datetime


class AssetTransferListResponse(BaseModel):
    items: List[AssetTransferResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ───────────────────────────── Maintenance ─────────────────────────────

class AssetMaintenanceCreate(BaseModel):
    asset_id: UUID
    maintenance_type: AssetMaintenanceType = AssetMaintenanceType.REPAIR
    vendor_id: Optional[UUID] = None
    damage_id: Optional[UUID] = None
    scheduled_date: Optional[date] = None
    cost: Optional[Decimal] = None
    description: Optional[str] = None
    attachments: List[str] = Field(default_factory=list)


class AssetMaintenanceUpdate(BaseModel):
    maintenance_type: Optional[AssetMaintenanceType] = None
    vendor_id: Optional[UUID] = None
    scheduled_date: Optional[date] = None
    cost: Optional[Decimal] = None
    description: Optional[str] = None
    attachments: Optional[List[str]] = None


class AssetMaintenanceCompleteBody(BaseModel):
    completed_date: Optional[date] = None
    cost: Optional[Decimal] = None
    condition_after: Optional[AssetCondition] = None
    resolution_notes: Optional[str] = None


class AssetMaintenanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    asset_id: UUID
    asset_code: Optional[str] = None
    maintenance_type: AssetMaintenanceType
    status: AssetMaintenanceStatus
    vendor_id: Optional[UUID] = None
    vendor_name: Optional[str] = None
    damage_id: Optional[UUID] = None
    reported_date: Optional[date] = None
    scheduled_date: Optional[date] = None
    started_date: Optional[date] = None
    completed_date: Optional[date] = None
    cost: Optional[Decimal] = None
    description: Optional[str] = None
    resolution_notes: Optional[str] = None
    condition_before: Optional[AssetCondition] = None
    condition_after: Optional[AssetCondition] = None
    attachments: List[str] = Field(default_factory=list)
    created_at: datetime


class AssetMaintenanceListResponse(BaseModel):
    items: List[AssetMaintenanceResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ───────────────────────────── Damage ─────────────────────────────

class AssetDamageCreate(BaseModel):
    """Admin-raised damage ticket."""
    asset_id: UUID
    allocation_id: Optional[UUID] = None
    severity: AssetDamageSeverity = AssetDamageSeverity.MINOR
    title: Optional[str] = None
    description: str
    attachments: List[str] = Field(default_factory=list)
    liable_employee: bool = False
    recovery_amount: Optional[Decimal] = None


class DamageSelfReport(BaseModel):
    """Employee self-report (asset resolved from the allocation in the path)."""
    severity: AssetDamageSeverity = AssetDamageSeverity.MINOR
    title: Optional[str] = None
    description: str
    attachments: List[str] = Field(default_factory=list)


class AssetDamageStatusUpdate(BaseModel):
    status: AssetDamageStatus
    notes: Optional[str] = None
    liable_employee: Optional[bool] = None
    recovery_amount: Optional[Decimal] = None


class AssetDamageResolveBody(BaseModel):
    resolution_notes: Optional[str] = None
    resolved_date: Optional[date] = None
    write_off: bool = False


class AssetDamageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    asset_id: UUID
    asset_code: Optional[str] = None
    allocation_id: Optional[UUID] = None
    severity: AssetDamageSeverity
    status: AssetDamageStatus
    reported_by_employee_id: Optional[UUID] = None
    reported_by_name: Optional[str] = None
    title: Optional[str] = None
    description: str
    attachments: List[str] = Field(default_factory=list)
    reported_date: date
    resolved_date: Optional[date] = None
    resolution_notes: Optional[str] = None
    liable_employee: bool
    recovery_amount: Optional[Decimal] = None
    created_at: datetime


class AssetDamageListResponse(BaseModel):
    items: List[AssetDamageResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ───────────────────────────── History ─────────────────────────────

class AssetHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    asset_id: UUID
    asset_code: Optional[str] = None
    asset_type: Optional[str] = None
    event_type: AssetEventType
    actor_user_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    actor_employee_id: Optional[UUID] = None
    actor_employee_name: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    related_entity_type: Optional[str] = None
    related_entity_id: Optional[UUID] = None
    payload: dict = Field(default_factory=dict)
    note: Optional[str] = None
    created_at: datetime


class AssetHistoryListResponse(BaseModel):
    items: List[AssetHistoryResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ───────────────────────────── Dashboard stats ─────────────────────────────

class AssetDashboardStats(BaseModel):
    total: int
    available: int
    allocated: int
    reserved: int
    maintenance: int
    retired: int
    total_value: float
    unacknowledged: int
    overdue_returns: int
    open_damages: int
    warranty_expiring_30d: int
    by_type: Dict[str, int]
    by_condition: Dict[str, int]
    by_status: Dict[str, int]


# ───────────────────────────── Self-service ─────────────────────────────

class MyAssetSummary(BaseModel):
    held: int
    pending_ack: int
    needs_return: int


class MyAssetsResponse(BaseModel):
    items: list
    unlinked: bool = False
    summary: Optional[MyAssetSummary] = None


# ───────────────────────────── Audits (Phase 2) ─────────────────────────────

class AssetAuditCreate(BaseModel):
    name: str
    scope_location_id: Optional[UUID] = None
    scope_department_id: Optional[UUID] = None
    scope_category_id: Optional[UUID] = None
    scheduled_date: Optional[date] = None
    notes: Optional[str] = None


class AssetAuditScanBody(BaseModel):
    result: AssetAuditResult
    found_employee_id: Optional[UUID] = None
    found_location_id: Optional[UUID] = None
    found_condition: Optional[AssetCondition] = None
    remarks: Optional[str] = None


class AssetAuditItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    audit_id: UUID
    asset_id: UUID
    asset_code: Optional[str] = None
    expected_status: Optional[AssetStatus] = None
    result: AssetAuditResult
    found_condition: Optional[AssetCondition] = None
    scanned_at: Optional[datetime] = None
    remarks: Optional[str] = None


class AssetAuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    status: AssetAuditStatus
    scope_location_id: Optional[UUID] = None
    scope_department_id: Optional[UUID] = None
    scope_category_id: Optional[UUID] = None
    scheduled_date: Optional[date] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_expected: int
    total_found: int
    total_missing: int
    total_mismatched: int
    notes: Optional[str] = None
    created_at: datetime


class AssetAuditListResponse(BaseModel):
    items: List[AssetAuditResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ───────────────────────────── Disposal (Phase 2) ─────────────────────────────

class AssetDisposalCreate(BaseModel):
    asset_id: UUID
    disposal_method: AssetDisposalMethod = AssetDisposalMethod.SCRAPPED
    reason: str
    sale_value: Optional[Decimal] = None
    buyer: Optional[str] = None
    attachments: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class AssetDisposalDecisionBody(BaseModel):
    notes: Optional[str] = None
    disposed_date: Optional[date] = None
    sale_value: Optional[Decimal] = None


class AssetDisposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    asset_id: UUID
    asset_code: Optional[str] = None
    disposal_method: AssetDisposalMethod
    status: AssetDisposalStatus
    reason: str
    request_date: date
    approved_date: Optional[date] = None
    disposed_date: Optional[date] = None
    sale_value: Optional[Decimal] = None
    book_value: Optional[Decimal] = None
    buyer: Optional[str] = None
    attachments: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    created_at: datetime


class AssetDisposalListResponse(BaseModel):
    items: List[AssetDisposalResponse]
    total: int
    page: int
    limit: int
    total_pages: int
