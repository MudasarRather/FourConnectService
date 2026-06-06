"""HR Payroll — Salary Structure ↔ Component link.

Join row that places a ``SalaryComponent`` into a ``SalaryStructure`` with an
optional per-structure override of its calc inputs (so the same HRA component
can be 40% of basic in one structure and 50% in another) plus a structure-local
``sequence``.

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Numeric, Integer,
    Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.salary_component import CalcType


class SalaryStructureComponent(Base):
    __tablename__ = "hr_salary_structure_components"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    structure_id = Column(UUID(as_uuid=True), ForeignKey("hr_salary_structures.id", ondelete="CASCADE"), nullable=False, index=True)
    component_id = Column(UUID(as_uuid=True), ForeignKey("hr_salary_components.id", ondelete="CASCADE"), nullable=False, index=True)

    sequence = Column(Integer, nullable=False, default=100)  # overrides component.sequence within this structure

    # Per-structure overrides (null = inherit the component default).
    # create_type=False: the hr_salary_calc_type enum is owned by SalaryComponent.
    override_calc_type = Column(Enum(CalcType, name="hr_salary_calc_type", create_type=False), nullable=True)
    override_formula = Column(String(400), nullable=True)
    override_percent_value = Column(Numeric(7, 4), nullable=True)
    override_percent_of_code = Column(String(40), nullable=True)
    override_flat_amount = Column(Numeric(12, 2), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    structure = relationship("SalaryStructure", back_populates="components")
    component = relationship("SalaryComponent")

    __table_args__ = (
        UniqueConstraint("structure_id", "component_id", name="uq_hr_struct_component"),
        Index("ix_hr_struct_comp_seq", "structure_id", "sequence"),
    )
