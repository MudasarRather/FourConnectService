"""HR Travel — Travel Policy master.

Per-grade (or global) travel entitlement + routing config: flight class, hotel
category, DA eligibility, advance limit, and the configurable N-stage approval
chain snapshotted onto a request at submit time. Mirrors ``ClaimPolicy``.

New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Numeric, Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class TravelPolicy(Base):
    __tablename__ = "hr_travel_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    policy_name = Column(String(120), nullable=False)
    description = Column(String(400), nullable=True)

    # Scope: which grade this entitlement applies to (null = applies to all grades).
    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id", ondelete="SET NULL"), nullable=True, index=True)
    # DOMESTIC | INTERNATIONAL | PROJECT | TRAINING | EMERGENCY | ALL
    travel_scope = Column(String(40), nullable=False, default="ALL")

    # Entitlements
    flight_eligibility = Column(String(40), nullable=True)   # ECONOMY | PREMIUM_ECONOMY | BUSINESS | FIRST | NONE
    train_class = Column(String(40), nullable=True)          # AC1 | AC2 | AC3 | SLEEPER | NONE
    hotel_category = Column(String(40), nullable=True)       # e.g. "3 Star" | "5 Star"
    da_eligible = Column(Boolean, nullable=False, default=True)
    advance_limit = Column(Numeric(12, 2), nullable=True)    # max advance allowed under this policy

    # Configurable approval chain (list of ApprovalStageConfig dicts). Snapshotted
    # onto a request at submit. Null → the module's default chain.
    approval_chain = Column(JSONB, nullable=True)

    # Eligibility scoping (dept / designation / grade ids) — informational filter.
    eligibility = Column(JSONB, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    def __repr__(self):
        return f"<TravelPolicy {self.policy_name}>"
