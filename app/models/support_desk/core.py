"""Support Desk — core master records: Organization, Customer (contact),
Contract, SLA package, and Ticket category.

All classes are prefixed ``Sd`` and tables ``support_*`` to guarantee no
collision with existing HR/project models. Plain String status columns;
UUID PKs; soft-delete via ``is_deleted``; ``server_default=func.now()``.
New tables — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Text, Boolean, DateTime, Integer, Numeric, ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class SdOrganization(Base):
    __tablename__ = "support_organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False, index=True)
    code = Column(String(40), nullable=True, unique=True, index=True)
    industry = Column(String(80), nullable=True)
    website = Column(String(200), nullable=True)
    email = Column(String(200), nullable=True)
    phone = Column(String(40), nullable=True)
    address = Column(Text, nullable=True)
    country = Column(String(80), nullable=True)
    state = Column(String(80), nullable=True)
    city = Column(String(80), nullable=True)
    zip_code = Column(String(20), nullable=True)
    # Business info
    gst_number = Column(String(40), nullable=True)
    pan_number = Column(String(20), nullable=True)
    registration_number = Column(String(60), nullable=True)
    # Support info
    support_plan = Column(String(60), nullable=True)
    sla_package_id = Column(UUID(as_uuid=True), ForeignKey("support_sla_packages.id"), nullable=True)
    dedicated_manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    support_hours = Column(String(80), nullable=True)  # e.g. "24x7", "9-6 IST Mon-Fri"
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<SdOrganization {self.code or self.name}>"


class SdCustomer(Base):
    """A contact/user within a client organization."""
    __tablename__ = "support_customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("support_organizations.id"), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    designation = Column(String(120), nullable=True)
    department = Column(String(120), nullable=True)
    email = Column(String(200), nullable=True, index=True)
    phone = Column(String(40), nullable=True)
    mobile = Column(String(40), nullable=True)
    # Portal login (deferred auth — fields present so the data model is complete)
    username = Column(String(120), nullable=True, unique=True, index=True)
    # Granular portal permissions, e.g. ["create_tickets","view_tickets","approve_changes"]
    permissions = Column(JSONB, nullable=False, default=list)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (Index("ix_support_customers_org_active", "organization_id", "is_deleted"),)

    def __repr__(self):
        return f"<SdCustomer {self.name}>"


class SdSlaPackage(Base):
    """Priority → response/resolution matrix + escalation ladder.

    ``matrix`` shape: {"critical": {"response_mins": 15, "resolution_mins": 240}, ...}
    ``escalation_levels`` shape: [{"level": 1, "after_mins": 30, "notify": "manager"}, ...]
    """
    __tablename__ = "support_sla_packages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    matrix = Column(JSONB, nullable=False, default=dict)
    escalation_levels = Column(JSONB, nullable=False, default=list)
    is_default = Column(Boolean, nullable=False, default=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<SdSlaPackage {self.name}>"


class SdContract(Base):
    __tablename__ = "support_contracts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    contract_number = Column(String(60), nullable=True, unique=True, index=True)
    name = Column(String(160), nullable=False)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("support_organizations.id"), nullable=False, index=True)
    contract_type = Column(String(60), nullable=True)  # AMC / Support / Project
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    # Support details
    support_package = Column(String(80), nullable=True)
    hours_included = Column(Numeric(10, 2), nullable=True)
    dedicated_resources = Column(Integer, nullable=True)
    sla_package_id = Column(UUID(as_uuid=True), ForeignKey("support_sla_packages.id"), nullable=True)
    # Billing
    contract_value = Column(Numeric(14, 2), nullable=True)
    currency = Column(String(8), nullable=False, default="INR")
    renewal_date = Column(DateTime(timezone=True), nullable=True)
    billing_cycle = Column(String(40), nullable=True)  # Monthly / Quarterly / Annual
    status = Column(String(30), nullable=False, default="active", index=True)  # active/expired/terminated
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<SdContract {self.contract_number or self.name}>"


class SdCategory(Base):
    """Ticket category (self-parented for sub-categories)."""
    __tablename__ = "support_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("support_categories.id"), nullable=True)
    icon = Column(String(40), nullable=True)
    color = Column(String(20), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    # Which request TYPES this (top-level) category applies to — drives the
    # request_type → category → subcategory cascade in the create form. Empty = all types.
    # Subcategories inherit their parent's request types.
    request_types = Column(JSONB, nullable=False, default=list)
    # Optional default routing
    default_team = Column(String(80), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<SdCategory {self.name}>"
