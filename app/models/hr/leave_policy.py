"""HR Leave Policy — per-leave-type rules.

One row per `LeaveType`. Drives quota, accrual rate, carry-forward cap,
encashment eligibility, attachment requirements and notice/advance windows.
Phase 1 stores a single global policy per type; Phase 2 will add
department-scoped overrides via a composite unique constraint.

Defaults are seeded by `add_leave_module_tables.py`. Admin edits via
`PATCH /hr/leaves/policies/{leave_type}`.
"""
import uuid

from sqlalchemy import (
    Column, Boolean, DateTime, ForeignKey,
    Enum, Integer, Numeric, UniqueConstraint, String,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.leave_type import LeaveType


class LeavePolicy(Base):
    """Per-leave-type rules. Single global row per type in Phase 1."""
    __tablename__ = "hr_leave_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    leave_type = Column(Enum(LeaveType, name="hr_leave_type"), nullable=False, index=True)

    # Quota & accrual
    annual_quota = Column(Numeric(5, 2), nullable=False, default=0)
    monthly_accrual = Column(Numeric(5, 2), nullable=False, default=0)  # auto-credit per month
    max_carry_forward = Column(Numeric(5, 2), nullable=False, default=0)  # cap carried into next FY

    # Behavioural flags
    encashment_allowed = Column(Boolean, nullable=False, default=False)
    requires_attachment = Column(Boolean, nullable=False, default=False)
    # When True, holidays / week-offs that fall inside a multi-day leave are
    # COUNTED against balance. Paid leave types (CASUAL/EARNED) set True;
    # LWP-style "weekends shouldn't cost the employee" set False.
    count_holidays_weekoffs = Column(Boolean, nullable=False, default=True)

    # Booking constraints (nullable = no constraint)
    max_consecutive_days = Column(Integer, nullable=True)
    requires_notice_days = Column(Integer, nullable=False, default=0)   # min lead time
    advance_book_days = Column(Integer, nullable=True)                  # max future booking horizon

    # Display helpers
    label = Column(String(60), nullable=True)            # human label override (e.g. "Casual Leave")
    description = Column(String(400), nullable=True)
    color_hex = Column(String(9), nullable=True)         # admin UI accent

    # Phase 4 — Configurable approval chain. NULL means legacy default
    # ``[MANAGER, HR]`` two-tier behavior. When set, each element is
    # ``{"approver_type": "MANAGER"|"HR"|"USER", "approver_user_id": <uuid|null>, "label": <str>}``.
    # The chain is snapshotted onto each LeaveRequest at submit time.
    approval_chain = Column(JSONB, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("leave_type", name="uq_hr_leave_policy_type"),
    )
