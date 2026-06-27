"""HR Settings — configurable master tables for workforce taxonomy.

These tables make three previously-hardcoded enums editable from HR Settings
WITHOUT breaking existing data: ``Employee.employment_type`` /
``Employee.employee_category`` and the exit-module reason fields keep storing the
enum STRING value (e.g. ``"FULL_TIME"``). These master tables are the editable
SOURCE OF TRUTH for the dropdowns — their ``code`` mirrors the enum string for
the seeded ``is_system`` rows, so existing rows resolve cleanly.

Compatibility rules (enforced in the router):
  * ``is_system`` rows: code is immutable and the row can't be deleted
    (deactivate instead) — they back live enum values.
  * Genuinely-new codes that need to be persisted on ``Employee`` require an
    ``ALTER TYPE … ADD VALUE`` migration first (gated as "advanced" in the UI);
    until then new master rows are usable as labels/scoping only.

New tables — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class EmploymentTypeMaster(Base):
    __tablename__ = "hr_employment_type_master"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    code = Column(String(40), nullable=False, unique=True, index=True)   # == EmploymentType enum value
    label = Column(String(80), nullable=False)
    description = Column(String(300), nullable=True)
    is_system = Column(Boolean, nullable=False, default=False)           # backs a live enum value
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<EmploymentTypeMaster {self.code}>"


class EmployeeCategoryMaster(Base):
    __tablename__ = "hr_employee_category_master"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    code = Column(String(40), nullable=False, unique=True, index=True)   # == EmployeeCategory enum value
    label = Column(String(80), nullable=False)
    description = Column(String(300), nullable=True)
    is_system = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<EmployeeCategoryMaster {self.code}>"


class SeparationReasonMaster(Base):
    __tablename__ = "hr_separation_reason_master"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    code = Column(String(50), nullable=False, index=True)               # mirrors Resignation/ExitReason enum value
    label = Column(String(120), nullable=False)
    # which exit vocabulary this belongs to: RESIGNATION_TYPE | EXIT_REASON
    category = Column(String(30), nullable=False, default="EXIT_REASON", index=True)
    is_voluntary = Column(Boolean, nullable=True)                       # only meaningful for RESIGNATION_TYPE
    description = Column(String(300), nullable=True)
    is_system = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("code", "category", name="uq_hr_separation_reason_code_cat"),
        Index("ix_hr_separation_reason_cat", "category", "is_active"),
    )

    def __repr__(self):
        return f"<SeparationReasonMaster {self.category}:{self.code}>"
