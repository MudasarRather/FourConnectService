"""HR Employee Documents — enterprise document lifecycle hub.

A single unified table (`hr_employee_documents`) holds every employee document
across categories (KYC, contracts, certificates, salary slips, experience
letters, ID proofs, education, compliance). The actual file lives in the shared
`drive_documents` table (reused for versioning chain, download/view counts,
soft delete), referenced via `drive_document_id`.

Documents uploaded during onboarding (`OnboardingDocument`) surface here too —
`source=ONBOARDING` rows back-link via `onboarding_document_id` and reuse the
same `DriveDocument`, so there is one source of truth per employee.

Conventions match the rest of the HR spine:
  - UUID PKs, DateTime(timezone=True) with server_default=func.now()
  - Enum columns use an explicit `name=` so the DB type is stable
  - is_deleted soft-delete + is_archived (restorable archive) on the top entity
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date, Enum, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────

class DocumentCategory(str, enum.Enum):
    KYC = "KYC"
    CONTRACT = "CONTRACT"
    CERTIFICATE = "CERTIFICATE"
    SALARY_SLIP = "SALARY_SLIP"
    EXPERIENCE_LETTER = "EXPERIENCE_LETTER"
    ID_PROOF = "ID_PROOF"
    EDUCATION = "EDUCATION"
    COMPLIANCE = "COMPLIANCE"
    OTHER = "OTHER"


class DocVerificationStatus(str, enum.Enum):
    PENDING = "PENDING"          # awaiting review (file may or may not be attached)
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    RESUBMIT_REQUIRED = "RESUBMIT_REQUIRED"
    EXPIRED = "EXPIRED"          # set by the expiry cron once expiry_date passes


class DocSource(str, enum.Enum):
    ONBOARDING = "ONBOARDING"        # surfaced from an OnboardingDocument slot
    DIRECT_UPLOAD = "DIRECT_UPLOAD"  # uploaded directly in this module
    GENERATED = "GENERATED"          # produced from a template (experience letters, etc.)
    IMPORTED = "IMPORTED"


class DocTemplateType(str, enum.Enum):
    OFFER_LETTER = "OFFER_LETTER"
    NDA = "NDA"
    EXPERIENCE_LETTER = "EXPERIENCE_LETTER"
    RELIEVING_LETTER = "RELIEVING_LETTER"
    SALARY_CERTIFICATE = "SALARY_CERTIFICATE"
    APPOINTMENT_LETTER = "APPOINTMENT_LETTER"
    CONFIRMATION_LETTER = "CONFIRMATION_LETTER"


# Confidential-by-default categories (sensitive PII / statutory).
CONFIDENTIAL_CATEGORIES = {DocumentCategory.KYC, DocumentCategory.COMPLIANCE, DocumentCategory.SALARY_SLIP}


# ──────────────────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────────────────

class EmployeeDocument(Base):
    """One employee document of any category. File lives in DriveDocument."""
    __tablename__ = "hr_employee_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=False, index=True)

    category = Column(Enum(DocumentCategory, name="hr_doc_category"), nullable=False, index=True)
    doc_type = Column(String(80), nullable=False)   # AADHAAR, PAN, EMPLOYMENT_CONTRACT, DEGREE, ...
    title = Column(String(200), nullable=False)

    drive_document_id = Column(UUID(as_uuid=True), ForeignKey("drive_documents.id"), nullable=True, index=True)

    # Common descriptive fields (category-specific extras go in `attributes`).
    document_number = Column(String(120), nullable=True)   # masked on output for confidential categories
    issued_by = Column(String(200), nullable=True)
    issue_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True, index=True)   # drives the expiry engine

    # Verification lifecycle.
    verification_status = Column(
        Enum(DocVerificationStatus, name="hr_doc_verify_status"),
        nullable=False, default=DocVerificationStatus.PENDING, index=True,
    )
    verified_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)

    # Category-specific bag: contract_status, institution, percentage, payroll_month,
    # gross/net/deductions/tax, letter_type, signature_status, rfid/biometric, etc.
    attributes = Column(JSONB, nullable=False, default=dict)

    source = Column(Enum(DocSource, name="hr_doc_source"), nullable=False, default=DocSource.DIRECT_UPLOAD)
    onboarding_document_id = Column(UUID(as_uuid=True), ForeignKey("hr_onboarding_documents.id"), nullable=True, index=True)

    is_confidential = Column(Boolean, nullable=False, default=False)

    # Expiry reminder dedupe — list of day-thresholds already alerted, e.g. [90, 60].
    expiry_reminders_sent = Column(JSONB, nullable=False, default=list)

    # Archive (restorable) + soft delete (trash).
    is_archived = Column(Boolean, nullable=False, default=False, index=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Audit.
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Relationships.
    employee = relationship("Employee", foreign_keys=[employee_id])
    drive_document = relationship("DriveDocument", foreign_keys=[drive_document_id])
    verified_by = relationship("User", foreign_keys=[verified_by_user_id])
    events = relationship(
        "EmployeeDocumentEvent",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="EmployeeDocumentEvent.created_at.desc()",
    )

    __table_args__ = (
        Index("ix_hr_emp_doc_emp_category", "employee_id", "category"),
        Index("ix_hr_emp_doc_status_active", "verification_status", "is_deleted"),
        Index("ix_hr_emp_doc_expiry_active", "expiry_date", "is_deleted"),
    )

    def __repr__(self):
        return f"<EmployeeDocument {self.category}/{self.doc_type} emp={self.employee_id}>"


class EmployeeDocumentEvent(Base):
    """Append-only audit trail for an employee document (drawer timeline)."""
    __tablename__ = "hr_employee_document_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_document_id = Column(
        UUID(as_uuid=True), ForeignKey("hr_employee_documents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # UPLOADED, VERIFIED, REJECTED, RESUBMIT_REQUESTED, DOWNLOADED, VIEWED,
    # RENEWED, ARCHIVED, RESTORED, EXPIRED, DELETED, REVEALED, CREATED, UPDATED
    action = Column(String(40), nullable=False, index=True)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    actor_name = Column(String(200), nullable=True)
    note = Column(Text, nullable=True)
    event_metadata = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    document = relationship("EmployeeDocument", back_populates="events")

    def __repr__(self):
        return f"<EmployeeDocumentEvent {self.action} on {self.employee_document_id}>"


class EmployeeDocumentTemplate(Base):
    """Reusable document template with {{placeholder}} substitution (UI in Pass 2)."""
    __tablename__ = "hr_employee_document_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False)
    template_type = Column(Enum(DocTemplateType, name="hr_doc_template_type"), nullable=False, index=True)
    description = Column(Text, nullable=True)
    body = Column(Text, nullable=True)               # HTML/markdown with {{placeholders}}
    placeholders = Column(JSONB, nullable=False, default=list)  # ["employee_name", "joining_date", ...]
    is_active = Column(Boolean, nullable=False, default=True, index=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    def __repr__(self):
        return f"<EmployeeDocumentTemplate {self.name} ({self.template_type})>"
