"""HR Asset Management — lifecycle models (Phase 5 "Asset Hangar").

Extends the core ``Asset`` / ``AssetAllocation`` spine (``app/models/hr/asset.py``)
into a full enterprise lifecycle: category & vendor masters, transfers, maintenance,
damage tickets, an immutable history/event log, and (Phase 2) physical audits +
disposal.

Conventions match the rest of the HR package: classic ``Column(...)`` style,
``UUID`` PKs, PG named enums, ``JSONB`` for flexible blobs, ``is_deleted`` soft
delete, ``server_default=func.now()`` timestamps, ``created_by_id -> users.id``.

NOTE — reused enums: ``AssetType``/``AssetCondition``/``AssetStatus`` already create
their PG type in ``asset.py``. Any column here that reuses one MUST pass
``create_type=False`` or ``create_all`` errors with "type already exists" (the same
gotcha the payroll module hit). New enums below own their own type names.
"""
import enum
import uuid
from datetime import date

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Integer, Text, Index, Numeric,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.asset import AssetType, AssetCondition, AssetStatus


# ───────────────────────────── Enums ─────────────────────────────

class AssetTransferType(str, enum.Enum):
    EMPLOYEE_TO_EMPLOYEE = "EMPLOYEE_TO_EMPLOYEE"
    EMPLOYEE_TO_STORE = "EMPLOYEE_TO_STORE"      # return-to-store (offboarding, request-return)
    STORE_TO_EMPLOYEE = "STORE_TO_EMPLOYEE"
    LOCATION = "LOCATION"
    DEPARTMENT = "DEPARTMENT"


class AssetTransferStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class AssetMaintenanceType(str, enum.Enum):
    REPAIR = "REPAIR"
    PREVENTIVE = "PREVENTIVE"
    INSPECTION = "INSPECTION"
    UPGRADE = "UPGRADE"
    CALIBRATION = "CALIBRATION"


class AssetMaintenanceStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AssetDamageSeverity(str, enum.Enum):
    MINOR = "MINOR"
    MODERATE = "MODERATE"
    MAJOR = "MAJOR"
    TOTAL_LOSS = "TOTAL_LOSS"


class AssetDamageStatus(str, enum.Enum):
    REPORTED = "REPORTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    IN_REPAIR = "IN_REPAIR"
    RESOLVED = "RESOLVED"
    WRITE_OFF = "WRITE_OFF"
    REJECTED = "REJECTED"


class AssetEventType(str, enum.Enum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    PROCUREMENT_RECORDED = "PROCUREMENT_RECORDED"
    ALLOCATED = "ALLOCATED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RETURN_REQUESTED = "RETURN_REQUESTED"
    RETURN_REQUEST_CANCELLED = "RETURN_REQUEST_CANCELLED"
    RETURNED = "RETURNED"
    MARKED_LOST = "MARKED_LOST"
    MARKED_DAMAGED = "MARKED_DAMAGED"
    TRANSFER_REQUESTED = "TRANSFER_REQUESTED"
    TRANSFER_APPROVED = "TRANSFER_APPROVED"
    TRANSFER_COMPLETED = "TRANSFER_COMPLETED"
    TRANSFER_REJECTED = "TRANSFER_REJECTED"
    MAINTENANCE_SCHEDULED = "MAINTENANCE_SCHEDULED"
    MAINTENANCE_STARTED = "MAINTENANCE_STARTED"
    MAINTENANCE_COMPLETED = "MAINTENANCE_COMPLETED"
    DAMAGE_REPORTED = "DAMAGE_REPORTED"
    DAMAGE_RESOLVED = "DAMAGE_RESOLVED"
    AUDIT_SCANNED = "AUDIT_SCANNED"
    DISPOSAL_REQUESTED = "DISPOSAL_REQUESTED"
    DISPOSAL_APPROVED = "DISPOSAL_APPROVED"
    DISPOSAL_COMPLETED = "DISPOSAL_COMPLETED"
    RETIRED = "RETIRED"
    DELETED = "DELETED"
    STATUS_CHANGED = "STATUS_CHANGED"


class AssetAuditStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AssetAuditResult(str, enum.Enum):
    PENDING = "PENDING"
    FOUND = "FOUND"
    MISSING = "MISSING"
    MISMATCH = "MISMATCH"
    DAMAGED = "DAMAGED"


class AssetDisposalMethod(str, enum.Enum):
    SOLD = "SOLD"
    SCRAPPED = "SCRAPPED"
    DONATED = "DONATED"
    RECYCLED = "RECYCLED"
    LOST = "LOST"
    WRITE_OFF = "WRITE_OFF"
    RETURNED_TO_VENDOR = "RETURNED_TO_VENDOR"


class AssetDisposalStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


# ───────────────────────────── Masters ─────────────────────────────

class AssetCategory(Base):
    """Editable taxonomy master (distinct from the hard-coded ``AssetType`` enum,
    which stays for code branching). Categories drive depreciation + grouping."""
    __tablename__ = "hr_asset_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), unique=True, nullable=False)
    code = Column(String(30), unique=True, nullable=False, index=True)
    parent_category_id = Column(UUID(as_uuid=True), ForeignKey("hr_asset_categories.id"), nullable=True)
    default_asset_type = Column(Enum(AssetType, name="hr_asset_type", create_type=False), nullable=True)
    depreciation_method = Column(String(30), nullable=True)   # STRAIGHT_LINE / NONE / ...
    useful_life_months = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    parent = relationship("AssetCategory", remote_side=[id])


class Vendor(Base):
    """Asset supplier / vendor master."""
    __tablename__ = "hr_asset_vendors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False, index=True)
    code = Column(String(40), unique=True, nullable=True, index=True)
    contact_person = Column(String(120), nullable=True)
    email = Column(String(160), nullable=True)
    phone = Column(String(40), nullable=True)
    address = Column(Text, nullable=True)
    gstin = Column(String(20), nullable=True)
    website = Column(String(200), nullable=True)
    rating = Column(Integer, nullable=True)  # 1..5
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


# ───────────────────────────── Transfers ─────────────────────────────

class AssetTransfer(Base):
    """Movement of an asset between employees / locations / departments / store."""
    __tablename__ = "hr_asset_transfers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("hr_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    transfer_type = Column(Enum(AssetTransferType, name="hr_asset_transfer_type"), nullable=False)
    status = Column(Enum(AssetTransferStatus, name="hr_asset_transfer_status"), nullable=False, default=AssetTransferStatus.REQUESTED, index=True)

    from_employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=True)
    to_employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=True)
    from_location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id"), nullable=True)
    to_location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id"), nullable=True)
    from_department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id"), nullable=True)
    to_department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id"), nullable=True)

    reason = Column(Text, nullable=True)
    effective_date = Column(Date, nullable=True)
    old_allocation_id = Column(UUID(as_uuid=True), ForeignKey("hr_asset_allocations.id"), nullable=True)
    new_allocation_id = Column(UUID(as_uuid=True), ForeignKey("hr_asset_allocations.id"), nullable=True)

    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    notes = Column(Text, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    asset = relationship("Asset", foreign_keys=[asset_id])
    from_employee = relationship("Employee", foreign_keys=[from_employee_id])
    to_employee = relationship("Employee", foreign_keys=[to_employee_id])


# ───────────────────────────── Maintenance ─────────────────────────────

class AssetMaintenance(Base):
    """Repair / preventive-maintenance lifecycle for an asset."""
    __tablename__ = "hr_asset_maintenance"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("hr_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    maintenance_type = Column(Enum(AssetMaintenanceType, name="hr_asset_maintenance_type"), nullable=False, default=AssetMaintenanceType.REPAIR)
    status = Column(Enum(AssetMaintenanceStatus, name="hr_asset_maintenance_status"), nullable=False, default=AssetMaintenanceStatus.SCHEDULED, index=True)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("hr_asset_vendors.id"), nullable=True)
    damage_id = Column(UUID(as_uuid=True), ForeignKey("hr_asset_damages.id"), nullable=True)

    reported_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assigned_to_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reported_date = Column(Date, nullable=True)
    scheduled_date = Column(Date, nullable=True)
    started_date = Column(Date, nullable=True)
    completed_date = Column(Date, nullable=True)

    cost = Column(Numeric(12, 2), nullable=True)
    description = Column(Text, nullable=True)
    resolution_notes = Column(Text, nullable=True)
    condition_before = Column(Enum(AssetCondition, name="hr_asset_condition", create_type=False), nullable=True)
    condition_after = Column(Enum(AssetCondition, name="hr_asset_condition", create_type=False), nullable=True)
    prior_status = Column(String(40), nullable=True)  # AssetStatus snapshot to restore on completion
    attachments = Column(JSONB, default=list, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    asset = relationship("Asset", foreign_keys=[asset_id])
    vendor = relationship("Vendor", foreign_keys=[vendor_id])


# ───────────────────────────── Damage ─────────────────────────────

class AssetDamage(Base):
    """Damage / loss incident ticket. Raised by admin OR self-reported by the
    holding employee (with photos)."""
    __tablename__ = "hr_asset_damages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("hr_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    allocation_id = Column(UUID(as_uuid=True), ForeignKey("hr_asset_allocations.id"), nullable=True)
    severity = Column(Enum(AssetDamageSeverity, name="hr_asset_damage_severity"), nullable=False, default=AssetDamageSeverity.MINOR)
    status = Column(Enum(AssetDamageStatus, name="hr_asset_damage_status"), nullable=False, default=AssetDamageStatus.REPORTED, index=True)

    reported_by_employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=True)
    reported_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    title = Column(String(200), nullable=True)
    description = Column(Text, nullable=False)
    attachments = Column(JSONB, default=list, nullable=False)  # photo paths
    reported_date = Column(Date, nullable=False, default=date.today, server_default=func.current_date())
    resolved_date = Column(Date, nullable=True)
    resolution_notes = Column(Text, nullable=True)
    liable_employee = Column(Boolean, default=False, nullable=False)
    recovery_amount = Column(Numeric(12, 2), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    asset = relationship("Asset", foreign_keys=[asset_id])


# ───────────────────────────── History (immutable) ─────────────────────────────

class AssetHistory(Base):
    """Append-only lifecycle event log — the source of truth for the asset timeline
    UI. No updated_at / is_deleted: rows are never mutated or removed."""
    __tablename__ = "hr_asset_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("hr_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(Enum(AssetEventType, name="hr_asset_event_type"), nullable=False, index=True)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    actor_employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=True)
    from_status = Column(String(40), nullable=True)
    to_status = Column(String(40), nullable=True)
    related_entity_type = Column(String(40), nullable=True)   # allocation / transfer / maintenance / damage / ...
    related_entity_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    payload = Column(JSONB, default=dict, nullable=False)
    note = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        Index("ix_hr_asset_history_asset_created", "asset_id", "created_at"),
    )


# ───────────────────────────── Audits (Phase 2) ─────────────────────────────

class AssetAudit(Base):
    """Physical reconciliation campaign over a scope (location/department/category)."""
    __tablename__ = "hr_asset_audits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False)
    status = Column(Enum(AssetAuditStatus, name="hr_asset_audit_status"), nullable=False, default=AssetAuditStatus.DRAFT, index=True)
    scope_location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id"), nullable=True)
    scope_department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id"), nullable=True)
    scope_category_id = Column(UUID(as_uuid=True), ForeignKey("hr_asset_categories.id"), nullable=True)
    scheduled_date = Column(Date, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    conducted_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    total_expected = Column(Integer, default=0, nullable=False)
    total_found = Column(Integer, default=0, nullable=False)
    total_missing = Column(Integer, default=0, nullable=False)
    total_mismatched = Column(Integer, default=0, nullable=False)
    notes = Column(Text, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    items = relationship("AssetAuditItem", back_populates="audit", cascade="all, delete-orphan")


class AssetAuditItem(Base):
    """A single expected asset snapshot within an audit + its scan result."""
    __tablename__ = "hr_asset_audit_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    audit_id = Column(UUID(as_uuid=True), ForeignKey("hr_asset_audits.id", ondelete="CASCADE"), nullable=False, index=True)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("hr_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    expected_status = Column(Enum(AssetStatus, name="hr_asset_status", create_type=False), nullable=True)
    expected_employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=True)
    expected_location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id"), nullable=True)
    result = Column(Enum(AssetAuditResult, name="hr_asset_audit_result"), nullable=False, default=AssetAuditResult.PENDING)
    found_employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=True)
    found_location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id"), nullable=True)
    found_condition = Column(Enum(AssetCondition, name="hr_asset_condition", create_type=False), nullable=True)
    scanned_at = Column(DateTime(timezone=True), nullable=True)
    scanned_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    remarks = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    audit = relationship("AssetAudit", back_populates="items")
    asset = relationship("Asset", foreign_keys=[asset_id])


# ───────────────────────────── Disposal (Phase 2) ─────────────────────────────

class AssetDisposal(Base):
    """End-of-life disposal request → approval → completion."""
    __tablename__ = "hr_asset_disposals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("hr_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    disposal_method = Column(Enum(AssetDisposalMethod, name="hr_asset_disposal_method"), nullable=False, default=AssetDisposalMethod.SCRAPPED)
    status = Column(Enum(AssetDisposalStatus, name="hr_asset_disposal_status"), nullable=False, default=AssetDisposalStatus.REQUESTED, index=True)
    reason = Column(Text, nullable=False)
    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    request_date = Column(Date, nullable=False, default=date.today, server_default=func.current_date())
    approved_date = Column(Date, nullable=True)
    disposed_date = Column(Date, nullable=True)
    sale_value = Column(Numeric(12, 2), nullable=True)
    book_value = Column(Numeric(12, 2), nullable=True)
    buyer = Column(String(160), nullable=True)
    attachments = Column(JSONB, default=list, nullable=False)
    notes = Column(Text, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    asset = relationship("Asset", foreign_keys=[asset_id])
