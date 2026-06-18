"""HR Training & Development — Certifications.

- ``Certification``        : the org's certification catalog (e.g. "AWS Solutions
                             Architect"), optionally tied to a skill.
- ``EmployeeCertification``: one row per certification an employee holds, with
                             issue / expiry dates and a lifecycle status driven by
                             the certification-expiry monitor.

New tables — auto-created on startup via ``Base.metadata.create_all``.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Integer, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class CertificationStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    EXPIRING_SOON = "EXPIRING_SOON"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    PENDING_RENEWAL = "PENDING_RENEWAL"


class Certification(Base):
    __tablename__ = "hr_certifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(200), nullable=False, unique=True, index=True)
    code = Column(String(40), nullable=True, unique=True)
    issuing_authority = Column(String(200), nullable=True)
    category = Column(String(80), nullable=True)
    validity_months = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    skill_id = Column(UUID(as_uuid=True), ForeignKey("hr_skills.id"), nullable=True, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class EmployeeCertification(Base):
    __tablename__ = "hr_employee_certifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    certification_id = Column(UUID(as_uuid=True), ForeignKey("hr_certifications.id"), nullable=True, index=True)
    # Snapshot / free-text so ad-hoc certs (not in the catalog) are allowed.
    name = Column(String(200), nullable=False)
    issuing_authority = Column(String(200), nullable=True)
    certificate_number = Column(String(120), nullable=True)
    issue_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True, index=True)
    status = Column(
        Enum(CertificationStatus, name="hr_certification_status"),
        nullable=False, default=CertificationStatus.ACTIVE, index=True,
    )
    certificate_url = Column(String(600), nullable=True)
    drive_document_id = Column(UUID(as_uuid=True), nullable=True)  # loose ref to DriveDocument.id
    # Set when a cert is auto-minted from a completed certification-required training.
    source_assignment_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_assignments.id", ondelete="SET NULL"), nullable=True)
    # Which program renews this cert (drives auto re-assignment by the expiry monitor).
    renewal_training_program_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_programs.id", ondelete="SET NULL"), nullable=True)
    # Idempotency marker for expiry notifications — last window (90/60/30) already notified.
    last_notified_window = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_hr_emp_cert_expiry_status", "expiry_date", "status"),
    )
