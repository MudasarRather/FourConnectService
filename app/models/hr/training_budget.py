"""HR Training & Development — Training budget allocation + cost tracking.

A budget allocates L&D spend for a (fiscal-year, period, department) bucket;
budget items are the individual cost lines. ``spent_amount`` / ``committed_amount``
are recomputed from the items on every write.

New tables — auto-created on startup via ``Base.metadata.create_all``.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Integer, Numeric, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class BudgetPeriodType(str, enum.Enum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"


class BudgetCostType(str, enum.Enum):
    TRAINER_FEE = "TRAINER_FEE"
    MATERIAL = "MATERIAL"
    VENUE = "VENUE"
    TRAVEL = "TRAVEL"
    CERT_FEE = "CERT_FEE"
    OTHER = "OTHER"


class TrainingBudget(Base):
    __tablename__ = "hr_training_budgets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False)
    period_type = Column(Enum(BudgetPeriodType, name="hr_budget_period_type"), nullable=False, default=BudgetPeriodType.ANNUAL)
    fiscal_year = Column(Integer, nullable=False, index=True)
    period_index = Column(Integer, nullable=True)  # quarter (1-4) or month (1-12)
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id"), nullable=True, index=True)  # null = org-wide
    allocated_amount = Column(Numeric(14, 2), nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="INR")
    spent_amount = Column(Numeric(14, 2), nullable=False, default=0)
    committed_amount = Column(Numeric(14, 2), nullable=False, default=0)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("fiscal_year", "period_type", "period_index", "department_id", name="uq_hr_training_budget"),
    )


class TrainingBudgetItem(Base):
    __tablename__ = "hr_training_budget_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    budget_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_budgets.id", ondelete="CASCADE"), nullable=False, index=True)
    program_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_programs.id", ondelete="SET NULL"), nullable=True)
    assignment_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_assignments.id", ondelete="SET NULL"), nullable=True)
    request_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_requests.id", ondelete="SET NULL"), nullable=True)
    trainer_id = Column(UUID(as_uuid=True), ForeignKey("hr_trainers.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(200), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False, default=0)
    cost_type = Column(Enum(BudgetCostType, name="hr_budget_cost_type"), nullable=False, default=BudgetCostType.OTHER)
    is_committed = Column(Boolean, default=False, nullable=False)  # committed (approved-but-unspent) vs spent
    incurred_date = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
