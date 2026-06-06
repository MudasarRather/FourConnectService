"""HR Payroll — Employee Compensation (effective-dated history).

One row per CTC revision; rows are NEVER updated in place once ACTIVE. The
"current" compensation for a period is the latest ACTIVE row whose
[effective_from, effective_to] window covers that period. ``revision_reason`` /
``revision_ref`` are the Phase-B Salary-Revisions / Arrears hook.

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Enum, Numeric, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.employee import TaxRegime  # reuse existing PG enum hr_tax_regime


class CompensationStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class EmployeeCompensation(Base):
    __tablename__ = "hr_employee_compensations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    structure_id = Column(UUID(as_uuid=True), ForeignKey("hr_salary_structures.id", ondelete="SET NULL"), nullable=True, index=True)

    effective_from = Column(Date, nullable=False, index=True)
    effective_to = Column(Date, nullable=True)

    annual_ctc = Column(Numeric(14, 2), nullable=False, default=0)
    monthly_ctc = Column(Numeric(12, 2), nullable=False, default=0)
    monthly_gross = Column(Numeric(12, 2), nullable=True)
    basic_amount = Column(Numeric(12, 2), nullable=True)
    breakdown = Column(JSONB, nullable=True)  # {component_code: amount} snapshot from preview

    # create_type=False: hr_tax_regime enum is owned by the Employee model.
    tax_regime = Column(Enum(TaxRegime, name="hr_tax_regime", create_type=False), nullable=True)
    # Phase B: investment / exemption declarations for TDS. Read defensively.
    tds_declarations = Column(JSONB, nullable=True)

    revision_reason = Column(String(200), nullable=True)
    revision_ref = Column(String(80), nullable=True)

    status = Column(Enum(CompensationStatus, name="hr_compensation_status"), nullable=False, default=CompensationStatus.DRAFT, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    employee = relationship("Employee", foreign_keys=[employee_id], back_populates="compensations")
    structure = relationship("SalaryStructure", foreign_keys=[structure_id])

    __table_args__ = (
        Index("ix_hr_comp_emp_eff", "employee_id", "effective_from"),
        Index("ix_hr_comp_emp_status", "employee_id", "status"),
    )

    def __repr__(self):
        return f"<EmployeeCompensation emp={self.employee_id} from={self.effective_from}>"
