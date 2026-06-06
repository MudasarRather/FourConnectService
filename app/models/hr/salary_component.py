"""HR Payroll — Salary Component catalog.

The master list of earning / deduction / statutory heads. A component's ``code``
is the stable token referenced by formulas (e.g. ``BASIC``, ``HRA``). Most heads
need no expression — they resolve via ``calc_type`` (FLAT / PERCENT_OF /
STATUTORY / BALANCE / ATTENDANCE_PRORATED). Only ``calc_type == FORMULA`` uses
the restricted AST evaluator in ``app.utils.hr.payroll.formula``.

``is_system`` protects the seeded core heads (BASIC, HRA, PF, ESI, PT, TDS) from
deletion / structural edits.

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Numeric, Integer,
    Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class ComponentType(str, enum.Enum):
    EARNING = "EARNING"
    DEDUCTION = "DEDUCTION"
    STATUTORY_DEDUCTION = "STATUTORY_DEDUCTION"
    EMPLOYER_CONTRIBUTION = "EMPLOYER_CONTRIBUTION"
    REIMBURSEMENT = "REIMBURSEMENT"


class CalcType(str, enum.Enum):
    FLAT = "FLAT"                              # fixed flat_amount
    PERCENT_OF = "PERCENT_OF"                  # percent_value * resolve(percent_of_code)
    FORMULA = "FORMULA"                        # restricted AST expression
    STATUTORY = "STATUTORY"                    # delegate to statutory engine by statutory_kind
    BALANCE = "BALANCE"                        # gross − sum(prior earnings) (e.g. Special Allowance)
    ATTENDANCE_PRORATED = "ATTENDANCE_PRORATED"  # flat_amount * paid_ratio


class StatutoryKind(str, enum.Enum):
    PF_EMPLOYEE = "PF_EMPLOYEE"
    PF_EMPLOYER = "PF_EMPLOYER"
    ESI_EMPLOYEE = "ESI_EMPLOYEE"
    ESI_EMPLOYER = "ESI_EMPLOYER"
    PROFESSIONAL_TAX = "PROFESSIONAL_TAX"
    TDS = "TDS"
    LWF_EMPLOYEE = "LWF_EMPLOYEE"
    LWF_EMPLOYER = "LWF_EMPLOYER"


class SalaryComponent(Base):
    __tablename__ = "hr_salary_components"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    code = Column(String(40), nullable=False, unique=True, index=True)   # stable formula token, e.g. "BASIC"
    name = Column(String(120), nullable=False)

    component_type = Column(Enum(ComponentType, name="hr_salary_component_type"), nullable=False, index=True)
    calc_type = Column(Enum(CalcType, name="hr_salary_calc_type"), nullable=False, default=CalcType.FLAT)
    statutory_kind = Column(Enum(StatutoryKind, name="hr_statutory_kind"), nullable=True)

    # Calc inputs (only the ones relevant to calc_type are used)
    formula = Column(String(400), nullable=True)            # FORMULA
    percent_value = Column(Numeric(7, 4), nullable=True)    # PERCENT_OF (e.g. 0.4000 = 40%)
    percent_of_code = Column(String(40), nullable=True)     # PERCENT_OF base token (BASIC/GROSS/CTC…)
    flat_amount = Column(Numeric(12, 2), nullable=True)     # FLAT / ATTENDANCE_PRORATED

    sequence = Column(Integer, nullable=False, default=100)  # resolution / display order

    # Behaviour flags
    is_taxable = Column(Boolean, nullable=False, default=True)
    is_part_of_gross = Column(Boolean, nullable=False, default=True)
    affects_pf_wage = Column(Boolean, nullable=False, default=False)
    affects_esi_wage = Column(Boolean, nullable=False, default=False)
    prorate_on_lop = Column(Boolean, nullable=False, default=True)
    is_employer_cost = Column(Boolean, nullable=False, default=False)  # adds to CTC, not to net pay

    is_system = Column(Boolean, nullable=False, default=False)  # protect seeded core heads
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("code", name="uq_hr_salary_component_code"),
        Index("ix_hr_salary_comp_type_active", "component_type", "is_active"),
    )

    def __repr__(self):
        return f"<SalaryComponent {self.code} ({self.component_type})>"
