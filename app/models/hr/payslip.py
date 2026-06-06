"""HR Payroll — Payslip + PayslipLine.

A Payslip is one employee's computed statement within a PayrollBatch. Statutory
IDs + bank details are snapshotted at generation so a re-print is faithful even
if the Employee record changes later. Line items live in PayslipLine (one row
per component).

New tables — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Numeric, Integer,
    Text, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.employee import TaxRegime
from app.models.hr.salary_component import ComponentType, StatutoryKind


class PayslipStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    GENERATED = "GENERATED"
    APPROVED = "APPROVED"
    RELEASED = "RELEASED"
    HELD = "HELD"
    CANCELLED = "CANCELLED"


class Payslip(Base):
    __tablename__ = "hr_payslips"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    batch_id = Column(UUID(as_uuid=True), ForeignKey("hr_payroll_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    compensation_id = Column(UUID(as_uuid=True), ForeignKey("hr_employee_compensations.id", ondelete="SET NULL"), nullable=True)

    payslip_no = Column(String(40), nullable=False, unique=True, index=True)  # PS-{YY}{MM}-{empcode}
    period_month = Column(Integer, nullable=False)
    period_year = Column(Integer, nullable=False)
    status = Column(Enum(PayslipStatus, name="hr_payslip_status"), nullable=False, default=PayslipStatus.DRAFT, index=True)

    working_days = Column(Numeric(4, 1), nullable=False, default=0)
    lop_days = Column(Numeric(4, 1), nullable=False, default=0)
    paid_days = Column(Numeric(4, 1), nullable=False, default=0)

    tax_regime = Column(Enum(TaxRegime, name="hr_tax_regime", create_type=False), nullable=True)

    gross_earnings = Column(Numeric(14, 2), nullable=False, default=0)
    total_deductions = Column(Numeric(14, 2), nullable=False, default=0)
    net_pay = Column(Numeric(14, 2), nullable=False, default=0)
    employer_contributions = Column(Numeric(14, 2), nullable=False, default=0)
    ctc_value = Column(Numeric(14, 2), nullable=False, default=0)

    encashment_amount = Column(Numeric(12, 2), nullable=False, default=0)
    encashment_ref = Column(String(80), nullable=True)

    # Snapshots (faithful re-print)
    bank_name = Column(String(120), nullable=True)
    account_number = Column(String(40), nullable=True)
    ifsc = Column(String(20), nullable=True)
    pf_number = Column(String(30), nullable=True)
    esic_number = Column(String(30), nullable=True)
    uan = Column(String(20), nullable=True)
    pan = Column(String(10), nullable=True)

    remarks = Column(Text, nullable=True)
    pdf_generated_at = Column(DateTime(timezone=True), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    batch = relationship("PayrollBatch", back_populates="payslips")
    employee = relationship("Employee", foreign_keys=[employee_id])
    lines = relationship(
        "PayslipLine",
        back_populates="payslip",
        cascade="all, delete-orphan",
        order_by="PayslipLine.sequence",
    )

    __table_args__ = (
        UniqueConstraint("batch_id", "employee_id", name="uq_hr_payslip_batch_emp"),
        Index("ix_hr_payslip_emp_period", "employee_id", "period_year", "period_month"),
        Index("ix_hr_payslip_status", "status", "is_deleted"),
    )

    def __repr__(self):
        return f"<Payslip {self.payslip_no}>"


class PayslipLine(Base):
    __tablename__ = "hr_payslip_lines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    payslip_id = Column(UUID(as_uuid=True), ForeignKey("hr_payslips.id", ondelete="CASCADE"), nullable=False, index=True)
    component_id = Column(UUID(as_uuid=True), ForeignKey("hr_salary_components.id", ondelete="SET NULL"), nullable=True)

    component_code = Column(String(40), nullable=False)   # snapshot
    component_name = Column(String(120), nullable=False)  # snapshot
    # create_type=False: these enums are owned by the SalaryComponent model.
    component_type = Column(Enum(ComponentType, name="hr_salary_component_type", create_type=False), nullable=False)
    statutory_kind = Column(Enum(StatutoryKind, name="hr_statutory_kind", create_type=False), nullable=True)
    sequence = Column(Integer, nullable=False, default=100)

    full_amount = Column(Numeric(12, 2), nullable=False, default=0)  # pre-proration
    amount = Column(Numeric(12, 2), nullable=False, default=0)       # post-proration (what hits the slip)
    is_taxable = Column(Boolean, nullable=False, default=True)
    is_employer_cost = Column(Boolean, nullable=False, default=False)
    calc_note = Column(String(200), nullable=True)  # trace, e.g. "40% of BASIC; prorated 28/30"

    payslip = relationship("Payslip", back_populates="lines")

    __table_args__ = (
        Index("ix_hr_payslip_line_slip", "payslip_id", "sequence"),
    )
