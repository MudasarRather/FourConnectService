"""HR Onboarding models — Phase 2 joining workflow.

Drives the journey from "Offer Accepted" to "Employee Active":
  OnboardingProcess (1:1 with Employee) orchestrates
    ├── OnboardingChecklistItem  (6-category checklist, per process)
    ├── OnboardingDocument       (per-doc slot with verification + drive link)
    ├── JoiningApproval          (optional pre-docs gate)
    ├── OnboardingTask           (free-form task engine with SLA + escalation)
    ├── EmployeeIdentity         (1:1 — email/biometric/RFID/badge)
    └── WelcomeKit               (per-employee kit issuance from template)

Conventions match the rest of the HR phase 1 spine:
  - UUID PKs, DateTime(timezone=True) with server_default=func.now()
  - is_deleted soft-delete flag on top-level entities
  - Status enums use lowercase-friendly StrEnum so payloads are stable
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Integer, Index, Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────

class OnboardingStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class OnboardingStage(str, enum.Enum):
    """Seven waypoints rendered along the Joining Journey hero path."""
    PRE_JOIN = "PRE_JOIN"
    APPROVAL = "APPROVAL"
    DOCS = "DOCS"
    IDENTITY = "IDENTITY"
    ASSETS = "ASSETS"
    TRAINING = "TRAINING"
    ACTIVE = "ACTIVE"


class ChecklistCategory(str, enum.Enum):
    HR = "HR"
    IT = "IT"
    ADMIN = "ADMIN"
    FINANCE = "FINANCE"
    SECURITY = "SECURITY"
    DEPARTMENT = "DEPARTMENT"


class ChecklistItemStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    WAIVED = "WAIVED"


class DocumentSlotStatus(str, enum.Enum):
    PENDING = "PENDING"
    UPLOADED = "UPLOADED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ApprovalRole(str, enum.Enum):
    HR_HEAD = "HR_HEAD"
    DEPT_HEAD = "DEPT_HEAD"
    FINANCE = "FINANCE"
    IT = "IT"
    SECURITY = "SECURITY"
    OTHER = "OTHER"


class ApprovalDecision(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WAIVED = "WAIVED"


class TaskStatus(str, enum.Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class TaskPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class IdentityStatus(str, enum.Enum):
    PENDING = "PENDING"
    ISSUED = "ISSUED"
    REVOKED = "REVOKED"


class WelcomeKitStatus(str, enum.Enum):
    PENDING = "PENDING"
    PACKED = "PACKED"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"


# ──────────────────────────────────────────────────────────────────────────────
# Onboarding Process
# ──────────────────────────────────────────────────────────────────────────────

class OnboardingProcess(Base):
    """One row per Employee — the umbrella record for their joining journey."""
    __tablename__ = "hr_onboarding_processes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hr_employees.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    offer_id = Column(UUID(as_uuid=True), ForeignKey("hr_rec_offers.id", ondelete="SET NULL"), nullable=True, index=True)

    status = Column(
        Enum(OnboardingStatus, name="hr_onboarding_status"),
        nullable=False, default=OnboardingStatus.IN_PROGRESS, index=True,
    )
    current_stage = Column(
        Enum(OnboardingStage, name="hr_onboarding_stage"),
        nullable=False, default=OnboardingStage.PRE_JOIN, index=True,
    )
    progress_pct = Column(Integer, nullable=False, default=0)

    target_joining_date = Column(Date, nullable=True, index=True)
    actual_joining_date = Column(Date, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    on_hold_reason = Column(Text, nullable=True)

    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Relationships
    employee = relationship("Employee", foreign_keys=[employee_id])
    checklist_items = relationship(
        "OnboardingChecklistItem", back_populates="process",
        cascade="all, delete-orphan", order_by="OnboardingChecklistItem.sort_order",
    )
    documents = relationship(
        "OnboardingDocument", back_populates="process",
        cascade="all, delete-orphan",
    )
    approvals = relationship(
        "JoiningApproval", back_populates="process",
        cascade="all, delete-orphan", order_by="JoiningApproval.sort_order",
    )
    tasks = relationship(
        "OnboardingTask", back_populates="process",
        cascade="all, delete-orphan", order_by="OnboardingTask.created_at",
    )

    __table_args__ = (
        Index("ix_hr_onb_status_stage", "status", "current_stage"),
    )

    def __repr__(self):
        return f"<OnboardingProcess employee={self.employee_id} status={self.status}>"


# ──────────────────────────────────────────────────────────────────────────────
# Checklist
# ──────────────────────────────────────────────────────────────────────────────

class OnboardingChecklistTemplate(Base):
    """Admin-defined master items copied into every new OnboardingProcess."""
    __tablename__ = "hr_onboarding_checklist_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    category = Column(Enum(ChecklistCategory, name="hr_onb_checklist_category"), nullable=False, index=True)
    task_name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    default_assignee_role = Column(String(60), nullable=True)
    default_due_offset_days = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True, nullable=False)
    is_mandatory = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class OnboardingChecklistItem(Base):
    """Per-process checklist row — copied from template at process bootstrap."""
    __tablename__ = "hr_onboarding_checklist_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    process_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hr_onboarding_processes.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    template_id = Column(UUID(as_uuid=True), ForeignKey("hr_onboarding_checklist_templates.id"), nullable=True)

    category = Column(Enum(ChecklistCategory, name="hr_onb_checklist_category"), nullable=False, index=True)
    task_name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    assigned_to_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    due_date = Column(Date, nullable=True)
    status = Column(
        Enum(ChecklistItemStatus, name="hr_onb_checklist_item_status"),
        nullable=False, default=ChecklistItemStatus.PENDING, index=True,
    )
    is_mandatory = Column(Boolean, default=True, nullable=False)
    completed_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    remarks = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    process = relationship("OnboardingProcess", back_populates="checklist_items")
    assigned_to = relationship("User", foreign_keys=[assigned_to_user_id])
    completed_by = relationship("User", foreign_keys=[completed_by_user_id])


# ──────────────────────────────────────────────────────────────────────────────
# Documents
# ──────────────────────────────────────────────────────────────────────────────

class OnboardingDocument(Base):
    """Per-process slot for each required doc; links to DriveDocument when uploaded."""
    __tablename__ = "hr_onboarding_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    process_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hr_onboarding_processes.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    doc_type_key = Column(String(60), nullable=False, index=True)  # aadhaar, pan, resume, ...
    doc_type_label = Column(String(160), nullable=False)
    is_mandatory = Column(Boolean, default=True, nullable=False)

    drive_document_id = Column(UUID(as_uuid=True), ForeignKey("drive_documents.id"), nullable=True, index=True)
    status = Column(
        Enum(DocumentSlotStatus, name="hr_onb_doc_status"),
        nullable=False, default=DocumentSlotStatus.PENDING, index=True,
    )
    expiry_date = Column(Date, nullable=True)
    ocr_data = Column(JSONB, nullable=True)
    verified_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    process = relationship("OnboardingProcess", back_populates="documents")
    verified_by = relationship("User", foreign_keys=[verified_by_user_id])


# ──────────────────────────────────────────────────────────────────────────────
# Joining Approvals
# ──────────────────────────────────────────────────────────────────────────────

class JoiningApproval(Base):
    """Optional pre-docs gate. Multiple approver roles can be configured per process."""
    __tablename__ = "hr_joining_approvals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    process_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hr_onboarding_processes.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    approver_role = Column(Enum(ApprovalRole, name="hr_joining_approver_role"), nullable=False)
    approver_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    status = Column(
        Enum(ApprovalDecision, name="hr_joining_approval_status"),
        nullable=False, default=ApprovalDecision.PENDING, index=True,
    )
    sort_order = Column(Integer, nullable=False, default=0)
    decision_at = Column(DateTime(timezone=True), nullable=True)
    decision_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    process = relationship("OnboardingProcess", back_populates="approvals")
    approver = relationship("User", foreign_keys=[approver_user_id])


# ──────────────────────────────────────────────────────────────────────────────
# Free-form Task Engine
# ──────────────────────────────────────────────────────────────────────────────

class OnboardingTask(Base):
    """Free-form task beyond the structured checklist — kanban-able, SLA-tracked."""
    __tablename__ = "hr_onboarding_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    process_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hr_onboarding_processes.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(Enum(ChecklistCategory, name="hr_onb_checklist_category"), nullable=True, index=True)
    assigned_to_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    due_date = Column(Date, nullable=True)
    status = Column(
        Enum(TaskStatus, name="hr_onb_task_status"),
        nullable=False, default=TaskStatus.TODO, index=True,
    )
    priority = Column(
        Enum(TaskPriority, name="hr_onb_task_priority"),
        nullable=False, default=TaskPriority.MEDIUM, index=True,
    )
    depends_on_task_id = Column(UUID(as_uuid=True), ForeignKey("hr_onboarding_tasks.id"), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    completed_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    sla_hours = Column(Integer, nullable=True)
    escalation_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    process = relationship("OnboardingProcess", back_populates="tasks")
    assigned_to = relationship("User", foreign_keys=[assigned_to_user_id])
    completed_by = relationship("User", foreign_keys=[completed_by_user_id])
    escalation = relationship("User", foreign_keys=[escalation_user_id])


# ──────────────────────────────────────────────────────────────────────────────
# Employee Identity (1:1)
# ──────────────────────────────────────────────────────────────────────────────

class EmployeeIdentity(Base):
    """Official identity provisioning state for an Employee."""
    __tablename__ = "hr_employee_identities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hr_employees.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True,
    )
    official_email = Column(String(200), nullable=True, unique=True, index=True)
    biometric_id = Column(String(60), nullable=True, unique=True)
    rfid_card_number = Column(String(60), nullable=True, unique=True)
    access_card_number = Column(String(60), nullable=True)
    username = Column(String(120), nullable=True)
    photo_url = Column(String(600), nullable=True)
    qr_payload = Column(String(600), nullable=True)
    status = Column(
        Enum(IdentityStatus, name="hr_employee_identity_status"),
        nullable=False, default=IdentityStatus.PENDING, index=True,
    )
    issued_at = Column(DateTime(timezone=True), nullable=True)
    issued_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


# ──────────────────────────────────────────────────────────────────────────────
# Welcome Kit
# ──────────────────────────────────────────────────────────────────────────────

class WelcomeKitTemplate(Base):
    """A kit definition (default items packaged for new joiners)."""
    __tablename__ = "hr_welcome_kit_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    default_items = Column(JSONB, default=list, nullable=False)   # [{item_name, qty, included}]
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class WelcomeKit(Base):
    """Per-employee kit issuance from a template."""
    __tablename__ = "hr_welcome_kits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    process_id = Column(UUID(as_uuid=True), ForeignKey("hr_onboarding_processes.id", ondelete="SET NULL"), nullable=True, index=True)
    template_id = Column(UUID(as_uuid=True), ForeignKey("hr_welcome_kit_templates.id"), nullable=True)

    status = Column(
        Enum(WelcomeKitStatus, name="hr_welcome_kit_status"),
        nullable=False, default=WelcomeKitStatus.PENDING, index=True,
    )
    items = Column(JSONB, default=list, nullable=False)   # [{item_name, qty, packed: bool, delivered: bool}]
    tracking_number = Column(String(120), nullable=True)
    packed_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    packed_at = Column(DateTime(timezone=True), nullable=True)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
