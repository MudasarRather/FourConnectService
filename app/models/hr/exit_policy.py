"""HR Exit Management — Exit Policy master.

Per-grade (or wildcard ``grade_id=NULL`` = default) separation config: notice
days, buyout rules, the configurable approval chain, the clearance checklist
template, interview questions and gratuity eligibility. Mirrors ``TravelPolicy``.

New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Numeric, Integer, Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.employee import EmployeeCategory
from sqlalchemy import Enum


class ExitPolicy(Base):
    __tablename__ = "hr_exit_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    policy_name = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)

    # Scope: null grade = default for any grade without a specific policy.
    grade_id = Column(UUID(as_uuid=True), ForeignKey("hr_grades.id", ondelete="SET NULL"), nullable=True, index=True)
    employee_category = Column(Enum(EmployeeCategory, name="hr_employee_category", create_type=False), nullable=True)

    notice_period_days = Column(Integer, nullable=False, default=30)
    probation_notice_days = Column(Integer, nullable=False, default=7)
    buyout_allowed = Column(Boolean, nullable=False, default=True)
    buyout_basis = Column(String(20), nullable=False, default="BASIC")   # BASIC | GROSS

    # Configurable chains / templates (lists of dicts).
    approval_levels = Column(JSONB, nullable=False, default=list)      # [{level, role, label}]
    clearance_template = Column(JSONB, nullable=False, default=list)   # [{department, item_key, title, description, is_mandatory, sort_order}]
    interview_questions = Column(JSONB, nullable=False, default=list)  # [{key, question, type}]

    gratuity_enabled = Column(Boolean, nullable=False, default=True)
    gratuity_min_years = Column(Numeric(4, 2), nullable=False, default=5)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    def __repr__(self):
        return f"<ExitPolicy {self.policy_name}>"
