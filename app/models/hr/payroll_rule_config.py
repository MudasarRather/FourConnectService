"""HR Settings — Payroll Rules (calculation policy, distinct from statutory rates).

Holds the configurable calculation knobs the payroll engine reads: pay cycle,
processing day, working-days basis, LOP formula, overtime multiplier, leave-
encashment basis, notice-recovery basis, default tax regime. Scoped by fiscal
year (null = global default). Resolved values are exposed to the engine under
``cfg["RULES"]`` (see statutory.load_config); the engine adopts them
incrementally, so a missing row simply yields the built-in default.

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Numeric, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class PayrollRuleConfig(Base):
    __tablename__ = "hr_payroll_rule_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    fiscal_year = Column(String(7), nullable=True, index=True)   # null = global default
    key = Column(String(50), nullable=False)
    value_num = Column(Numeric(14, 4), nullable=True)
    value_str = Column(String(60), nullable=True)
    value_json = Column(JSONB, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("fiscal_year", "key", name="uq_hr_payroll_rule"),
    )

    def __repr__(self):
        return f"<PayrollRuleConfig {self.fiscal_year or 'global'}/{self.key}>"
