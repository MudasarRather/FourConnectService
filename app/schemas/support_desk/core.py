"""Support Desk — core master schemas (Organization, Customer, Contract, SLA, Category)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────── Organization ───────────────────────────
class OrganizationBase(BaseModel):
    name: str
    code: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    registration_number: Optional[str] = None
    support_plan: Optional[str] = None
    sla_package_id: Optional[UUID] = None
    dedicated_manager_id: Optional[UUID] = None
    support_hours: Optional[str] = None
    is_active: bool = True


class OrganizationCreate(OrganizationBase):
    pass


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    registration_number: Optional[str] = None
    support_plan: Optional[str] = None
    sla_package_id: Optional[UUID] = None
    dedicated_manager_id: Optional[UUID] = None
    support_hours: Optional[str] = None
    is_active: Optional[bool] = None


class OrganizationResponse(OrganizationBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime
    updated_at: datetime
    # enriched (router-attached)
    customer_count: Optional[int] = None
    open_ticket_count: Optional[int] = None


# ─────────────────────────── Customer ───────────────────────────
class CustomerBase(BaseModel):
    organization_id: UUID
    name: str
    designation: Optional[str] = None
    department: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    username: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)
    is_active: bool = True


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    organization_id: Optional[UUID] = None
    name: Optional[str] = None
    designation: Optional[str] = None
    department: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    username: Optional[str] = None
    permissions: Optional[List[str]] = None
    is_active: Optional[bool] = None


class CustomerResponse(CustomerBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime
    updated_at: datetime
    organization_name: Optional[str] = None


# ─────────────────────────── SLA Package ───────────────────────────
class SlaPackageBase(BaseModel):
    name: str
    description: Optional[str] = None
    matrix: Dict[str, Any] = Field(default_factory=dict)
    # Coverage calendar — when the SLA clock runs. {} / mode "24x7" = wall-clock (legacy);
    # mode "business_hours" = {tz, days[1..7], start "HH:MM", end "HH:MM", holidays[],
    # priority_overrides{}}. Validated in the router via sla.validate_coverage.
    coverage: Dict[str, Any] = Field(default_factory=dict)
    escalation_levels: List[Dict[str, Any]] = Field(default_factory=list)
    is_default: bool = False
    is_active: bool = True


class SlaPackageCreate(SlaPackageBase):
    pass


class SlaPackageUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    matrix: Optional[Dict[str, Any]] = None
    coverage: Optional[Dict[str, Any]] = None
    escalation_levels: Optional[List[Dict[str, Any]]] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


class SlaPackageResponse(SlaPackageBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime
    updated_at: datetime


# ─────────────────────────── Contract ───────────────────────────
class ContractBase(BaseModel):
    contract_number: Optional[str] = None
    name: str
    organization_id: UUID
    contract_type: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    support_package: Optional[str] = None
    hours_included: Optional[float] = None
    dedicated_resources: Optional[int] = None
    sla_package_id: Optional[UUID] = None
    contract_value: Optional[float] = None
    currency: str = "INR"
    renewal_date: Optional[datetime] = None
    billing_cycle: Optional[str] = None
    status: str = "active"


class ContractCreate(ContractBase):
    pass


class ContractUpdate(BaseModel):
    contract_number: Optional[str] = None
    name: Optional[str] = None
    organization_id: Optional[UUID] = None
    contract_type: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    support_package: Optional[str] = None
    hours_included: Optional[float] = None
    dedicated_resources: Optional[int] = None
    sla_package_id: Optional[UUID] = None
    contract_value: Optional[float] = None
    currency: Optional[str] = None
    renewal_date: Optional[datetime] = None
    billing_cycle: Optional[str] = None
    status: Optional[str] = None


class ContractResponse(ContractBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime
    updated_at: datetime
    organization_name: Optional[str] = None


# ─────────────────────────── Category ───────────────────────────
class CategoryBase(BaseModel):
    name: str
    parent_id: Optional[UUID] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    sort_order: int = 0
    request_types: List[str] = Field(default_factory=list)   # request types this category applies to (top-level)
    default_team: Optional[str] = None
    is_active: bool = True


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[UUID] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None
    request_types: Optional[List[str]] = None
    default_team: Optional[str] = None
    is_active: Optional[bool] = None


class CategoryResponse(CategoryBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime
    updated_at: datetime
