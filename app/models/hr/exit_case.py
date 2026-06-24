"""HR Exit Management — Exit Case (the separation hub record).

One ``ExitCase`` per separation. ``status`` is the rich workflow overlay; the
authoritative lifecycle (ON_NOTICE / EXITED / ARCHIVED) stays on the Employee row
and is mutated ONLY via the existing ``/hr/employees/{id}/lifecycle/*`` handlers
(see ``app/routers/hr/exit_management.py:_sync_employee_lifecycle``).

A partial-unique index (``uq_hr_exit_open_case``, added by the migration script)
guarantees one OPEN case per employee. New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date, Enum, Numeric,
    Integer, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.exit_type import (
    ResignationType, ExitReasonCategory, ExitCaseStatus,
)
from app.models.hr.employee import EmployeeCategory


class ExitCase(Base):
    __tablename__ = "hr_exit_cases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    case_number = Column(String(20), nullable=False, unique=True, index=True)   # EX-{YY}-{NNNNNN}
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"),
                         nullable=False, index=True)

    # ─── Separation classification ───
    resignation_type = Column(Enum(ResignationType, name="hr_exit_resignation_type"), nullable=False)
    reason_category = Column(Enum(ExitReasonCategory, name="hr_exit_reason_category"), nullable=True)
    reason_detail = Column(Text, nullable=True)

    status = Column(Enum(ExitCaseStatus, name="hr_exit_case_status"), nullable=False,
                    default=ExitCaseStatus.DRAFT, index=True)
    initiated_by = Column(String(20), nullable=False, default="HR")  # EMPLOYEE | HR | MANAGER

    # ─── Dates ───
    resignation_date = Column(Date, nullable=True)
    requested_last_working_date = Column(Date, nullable=True)   # employee's ask
    notice_period_days = Column(Integer, nullable=True)         # resolved from policy at accept
    notice_period_start_date = Column(Date, nullable=True)      # mirrors Employee field once ON_NOTICE
    last_working_date = Column(Date, nullable=True)             # authoritative LWD (pro-rata + mirror)
    exit_date = Column(Date, nullable=True)                     # set when lifecycle_exit fires
    notice_waived = Column(Boolean, nullable=False, default=False)
    notice_buyout_days = Column(Integer, nullable=True)         # recovery basis

    # ─── Manager review ───
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)  # snapshot at submit
    manager_decision = Column(String(20), nullable=True)        # APPROVED | REJECTED
    manager_decided_at = Column(DateTime(timezone=True), nullable=True)
    manager_notes = Column(Text, nullable=True)

    # ─── HR acceptance / rejection ───
    accepted_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    cancel_reason = Column(Text, nullable=True)
    withdraw_reason = Column(Text, nullable=True)
    eligible_for_rehire = Column(Boolean, nullable=True)

    # ─── Scope snapshots (denormalised for fast list/report queries) ───
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id", ondelete="SET NULL"), nullable=True, index=True)
    designation_id = Column(UUID(as_uuid=True), ForeignKey("hr_designations.id", ondelete="SET NULL"), nullable=True)
    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id", ondelete="SET NULL"), nullable=True)
    employee_category = Column(Enum(EmployeeCategory, name="hr_employee_category", create_type=False), nullable=True)
    joining_date_snapshot = Column(Date, nullable=True)

    policy_id = Column(UUID(as_uuid=True), ForeignKey("hr_exit_policies.id", ondelete="SET NULL"), nullable=True)

    # ─── Denormalised progress mirrors ───
    clearance_progress_pct = Column(Integer, nullable=False, default=0)
    settlement_net_amount = Column(Numeric(14, 2), nullable=True)

    # ─── Former-employee document portal ───
    # Unguessable token (secrets.token_urlsafe) minted at acceptance. Powers the
    # PUBLIC, no-auth document portal so a leaver whose ERP login was revoked during
    # clearance can still download their relieving/experience letters. personal_email
    # is where HR pushes the portal link (mailto until SMTP infra lands).
    public_token = Column(String(64), nullable=True, unique=True, index=True)
    personal_email = Column(String(255), nullable=True)
    # Security window: the portal link is only live for a few days after the
    # documents are ISSUED (set/refreshed at issue + on rotate). NULL before any
    # letter is issued (link works but exposes nothing); past this the portal 410s.
    public_token_expires_at = Column(DateTime(timezone=True), nullable=True)

    # ─── Audit ───
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # ─── Relationships ───
    employee = relationship("Employee", foreign_keys=[employee_id])
    department = relationship("Department", foreign_keys=[department_id])
    policy = relationship("ExitPolicy", foreign_keys=[policy_id])
    clearance_items = relationship(
        "ExitClearanceItem", back_populates="exit_case",
        cascade="all, delete-orphan", order_by="ExitClearanceItem.sort_order.asc()",
    )
    interview = relationship(
        "ExitInterview", back_populates="exit_case",
        uselist=False, cascade="all, delete-orphan",
    )
    settlement = relationship(
        "ExitSettlement", back_populates="exit_case",
        uselist=False, cascade="all, delete-orphan",
    )
    documents = relationship(
        "ExitDocument", back_populates="exit_case",
        cascade="all, delete-orphan", order_by="ExitDocument.created_at.asc()",
    )

    __table_args__ = (
        Index("ix_hr_exit_emp_status", "employee_id", "status"),
        Index("ix_hr_exit_status_active", "status", "is_deleted"),
    )

    def __repr__(self):
        return f"<ExitCase {self.case_number} {self.status}>"
